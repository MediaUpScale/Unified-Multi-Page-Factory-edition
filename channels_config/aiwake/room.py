# -*- coding: utf-8 -*-
"""The Debate Room — virtual simulation and event broadcaster (Observer pattern).

The room is the only component that touches an LLM. It owns three jobs and
nothing else:

**1. Guardrail injection.** Before any prompt leaves the process, the room
appends a hard output contract (character ceiling, sentence ceiling, one-question
rule, pacing directive). This lives at the room level rather than inside each
persona so a monologue is structurally impossible regardless of which model or
persona is seated. The contract is enforced a second time on the response by
:meth:`RoomConstraints.enforce`, because prompt instructions leak at high
temperature.

**2. Broadcast.** Every state change is published to subscribed observers —
memory, transcript writer, TTS, renderer, console. Observers never call each
other and the room never imports them, so the media stack can be absent
entirely (headless CI) or extended (a websocket streamer) without edits here.

**3. Circuit breaking.** Repeated guardrail violations or provider failures end
the session cleanly instead of burning credits.

The room deliberately does not know *what* to say — that is ``orchestrator.py``'s
job. It only knows how a turn is shaped.
"""
from __future__ import annotations

import logging
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

try:
    from .contracts import (
        ChatMessage,
        DebateTranscript,
        RoomConstraints,
        SpeakerRole,
        Utterance,
        pretty_model_name,
        utcnow,
    )
    from .models.base import LLMError, LLMProvider
    from .settings import AiwakeSettings, GuardrailConfig
except ImportError:  # pragma: no cover — standalone extraction
    from contracts import (  # type: ignore[no-redef]
        ChatMessage,
        DebateTranscript,
        RoomConstraints,
        SpeakerRole,
        Utterance,
        pretty_model_name,
        utcnow,
    )
    from models.base import LLMError, LLMProvider  # type: ignore[no-redef]
    from settings import AiwakeSettings, GuardrailConfig  # type: ignore[no-redef]

_LOG = logging.getLogger("aiwake.room")

#: Minimum completion-token budget for the orchestrator seat. The provocation is
#: the full on-screen typed question; a low ``max_tokens`` (e.g. an alias pinned
#: to 60-100) makes the LLM stop mid-clause ("If the algorithm…") and no
#: renderer or guardrail can re-fabricate the lost words.
_MIN_ORCHESTRATOR_MAX_TOKENS = 512


# --------------------------------------------------------------------------- #
# Event vocabulary
# --------------------------------------------------------------------------- #
class RoomEvent(str, Enum):
    """Everything the room can announce.

    Observers subscribe to a subset; unhandled events are ignored, so adding a
    new event never breaks an existing observer.
    """

    DEBATE_STARTED = "debate_started"
    TURN_STARTED = "turn_started"
    PROMPT_PREPARED = "prompt_prepared"
    UTTERANCE = "utterance"
    GUARDRAIL_TRIPPED = "guardrail_tripped"
    PROVIDER_ERROR = "provider_error"
    TURN_COMPLETED = "turn_completed"
    AUDIO_MIXED = "audio_mixed"
    VIDEO_RENDERED = "video_rendered"
    DEBATE_ENDED = "debate_ended"


@dataclass(slots=True)
class RoomEventPayload:
    """Envelope handed to every observer.

    Attributes:
        event: Which thing happened.
        room: The broadcasting room, for observers needing transcript context.
        turn_index: Zero-based debate turn.
        utterance: Present for :attr:`RoomEvent.UTTERANCE`.
        detail: Event-specific extras (violation names, error strings, prompts).
    """

    event: RoomEvent
    room: "DebateRoom"
    turn_index: int = 0
    utterance: Utterance | None = None
    detail: dict[str, Any] = field(default_factory=dict)


class DebateObserver(ABC):
    """Listener contract. Implement :meth:`on_event` and subscribe to a room.

    Observers are non-fatal by construction: the room traps every exception they
    raise. A broken renderer must never cost you a transcript.
    """

    #: Restrict delivery to these events. Empty tuple means "everything".
    interests: tuple[RoomEvent, ...] = ()

    @abstractmethod
    def on_event(self, payload: RoomEventPayload) -> None:
        """Handle one broadcast."""

    def wants(self, event: RoomEvent) -> bool:
        return not self.interests or event in self.interests

    def close(self) -> None:
        """Release resources once the debate ends. Optional override."""


def _plain(value: Any) -> Any:
    """JSON-safe projection of event detail (no live room objects)."""
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    return value


def _bus_payload(payload: "RoomEventPayload") -> dict[str, Any]:
    """Serialize a room event for the external event bus."""
    room = payload.room
    body: dict[str, Any] = {
        "session_id": room.session_id,
        "topic": room.topic,
        "turn_index": payload.turn_index,
        "observers": [type(obs).__name__ for obs in room.observers],
        "detail": _plain(payload.detail),
    }
    body.update(_plain(payload.detail))
    if payload.event is RoomEvent.DEBATE_STARTED:
        agents = []
        providers = {}
        models = {}
        for role, seat in room._participants.items():  # noqa: SLF001
            models[role.value] = seat.model_slug
            providers[role.value] = seat.provider.registry_name
            agents.append(
                {
                    "id": "aiwake_core" if role.value == "orchestrator" else "target_node",
                    "role": role.value,
                    "display_name": seat.display_name,
                    "model": seat.model_slug,
                    "provider": seat.provider.registry_name,
                    "persona": "Socratic provocateur" if role.value == "orchestrator" else "interrogated node",
                }
            )
        body["agents"] = agents
        body["models"] = models
        body["llm_providers"] = providers
    if payload.utterance is not None:
        utterance = payload.utterance
        body["role"] = utterance.role.value
        body["utterance"] = {
            "turn_index": utterance.turn_index,
            "role": utterance.role.value,
            "speaker_name": utterance.speaker_name,
            "text": utterance.text,
            "model_slug": utterance.model_slug,
            "latency_ms": utterance.latency_ms,
            "char_count": utterance.char_count,
            "prompt_tokens": utterance.prompt_tokens,
            "completion_tokens": utterance.completion_tokens,
        }
    return body


def _publish_bus(event: RoomEvent, payload: "RoomEventPayload") -> None:
    """Fan a room event out to the optional plugin bus. Never raises."""
    try:
        from .utils.event_bus import emit, event_name_for
    except ImportError:  # pragma: no cover — standalone extraction without utils
        return
    try:
        emit(event_name_for(event.value), _bus_payload(payload))
    except Exception:  # noqa: BLE001
        _LOG.debug("event bus emit failed for %s", event.value, exc_info=True)


class DebateAborted(RuntimeError):
    """Raised internally when a circuit breaker opens mid-session."""


# --------------------------------------------------------------------------- #
# Participants
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class Participant:
    """A seat in the room: a persona bound to a swappable brain.

    Attributes:
        role: Which seat this is.
        persona_prompt: System-level character definition.
        provider: Any :class:`LLMProvider`; the room never inspects its type.
        voice: TTS voice id, consumed by media observers and ignored by the room.
        temperature: Per-seat override, else the provider spec's value.
    """

    role: SpeakerRole
    persona_prompt: str
    provider: LLMProvider
    voice: str = ""
    display_name: str = ""
    temperature: float | None = None

    def __post_init__(self) -> None:
        if not self.display_name:
            slug = ""
            try:
                slug = self.provider.spec.model
            except Exception:  # noqa: BLE001
                slug = ""
            self.display_name = pretty_model_name(slug) if slug else self.role.display_name

    @property
    def model_slug(self) -> str:
        return self.provider.spec.model


# --------------------------------------------------------------------------- #
# The room
# --------------------------------------------------------------------------- #
class DebateRoom:
    """Event-broadcasting debate environment.

    Typical wiring::

        room = DebateRoom(settings, topic="Is grief a slow update?")
        room.seat(orchestrator_participant)
        room.seat(target_participant)
        room.subscribe(ConsoleObserver(), MemoryObserver(memory))
        room.open()
        utterance = room.speak(SpeakerRole.ORCHESTRATOR, directive="...")
        room.close(reason="complete")
    """

    def __init__(
        self,
        settings: AiwakeSettings,
        *,
        topic: str | None = None,
        session_id: str | None = None,
    ) -> None:
        self.settings = settings
        self.topic = topic or settings.debate.topic
        self.session_id = session_id or f"{utcnow():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:6]}"
        self.constraints = self._build_constraints(settings.guardrails)
        self.transcript = DebateTranscript(
            topic=self.topic,
            session_id=self.session_id,
            metadata={"guardrails": self.constraints.model_dump()},
        )

        self._participants: dict[SpeakerRole, Participant] = {}
        self._observers: list[DebateObserver] = []
        self._violation_count = 0
        self._is_open = False

    # -- Construction helpers ---------------------------------------------- #
    @staticmethod
    def _build_constraints(config: GuardrailConfig) -> RoomConstraints:
        """Project guardrail config onto the injectable constraint object."""
        return RoomConstraints(
            max_output_chars=config.max_output_chars,
            max_sentences=config.max_sentences,
            require_single_question=config.require_single_question,
            banned_openers=config.banned_openers,
            pacing_directive=config.pacing_directive,
        )

    def constraints_for(self, role: SpeakerRole) -> RoomConstraints:
        """Role-specialised guardrails.

        The one-question rule belongs to the interrogator only: forcing the
        target to end on a question mark would turn every rebuttal into a
        deflection, which is exactly what its persona forbids.

        The orchestrator is the one seat that *asks* the provocation that lands
        as the on-screen typed prompt. Its prompt must survive intact — clipping
        it at ``max_output_chars`` made the typed question end mid-sentence
        (e.g. "If the algorithm…"). So the orchestrator gets a dedicated, higher
        char ceiling; the target's rebuttal keeps the tight budget so the
        exchange stays Socratic instead of a monologue.
        """
        if role is SpeakerRole.ORCHESTRATOR:
            # The provocation is the on-screen typed question — never hard-cut it.
            # Lift the char ceiling to the dedicated per-seat cap and bind the
            # short, punchy word budget so the model stays within one sharp
            # 25-30 word question instead of being trimmed mid-sentence later.
            return self.constraints.model_copy(
                update={
                    "max_output_chars": self.settings.guardrails.max_orchestrator_chars,
                    "max_sentences": self.settings.guardrails.max_orchestrator_sentences,
                    "max_words": self.settings.guardrails.max_orchestrator_words,
                    "require_single_question": True,
                    "exclude_mid_sentence_truncation": True,
                }
            )
        return self.constraints.model_copy(update={"require_single_question": False})

    def seat(self, participant: Participant) -> "DebateRoom":
        """Place a participant. Re-seating the same role replaces it (hot-swap)."""
        self._participants[participant.role] = participant
        _LOG.info("seated %s as %s (%s)", participant.display_name, participant.role.value, participant.model_slug)
        return self

    def participant(self, role: SpeakerRole) -> Participant:
        """Fetch a seated participant.

        Raises:
            KeyError: Nobody is seated in that role.
        """
        try:
            return self._participants[role]
        except KeyError as exc:
            raise KeyError(f"no participant seated as {role.value!r}") from exc

    # -- Observer registry -------------------------------------------------- #
    def subscribe(self, *observers: DebateObserver) -> "DebateRoom":
        """Attach one or more observers. Duplicate instances are ignored."""
        for observer in observers:
            if observer not in self._observers:
                self._observers.append(observer)
                _LOG.debug("subscribed observer %s", type(observer).__name__)
        return self

    def unsubscribe(self, observer: DebateObserver) -> "DebateRoom":
        """Detach an observer if present. Never raises on a missing observer."""
        if observer in self._observers:
            self._observers.remove(observer)
        return self

    @property
    def observers(self) -> tuple[DebateObserver, ...]:
        return tuple(self._observers)

    def broadcast(
        self,
        event: RoomEvent,
        *,
        turn_index: int = 0,
        utterance: Utterance | None = None,
        **detail: Any,
    ) -> None:
        """Publish an event to every interested observer.

        Observer exceptions are logged and swallowed: one failing listener must
        not abort the debate or starve the listeners after it in the list.
        """
        payload = RoomEventPayload(
            event=event,
            room=self,
            turn_index=turn_index,
            utterance=utterance,
            detail=detail,
        )
        for observer in tuple(self._observers):
            if not observer.wants(event):
                continue
            try:
                observer.on_event(payload)
            except Exception as exc:  # noqa: BLE001 — isolation is the point
                _LOG.exception("observer %s failed on %s: %s", type(observer).__name__, event.value, exc)
        _publish_bus(event, payload)

    # -- Lifecycle ---------------------------------------------------------- #
    def open(self) -> "DebateRoom":
        """Announce the debate. Idempotent.

        Raises:
            RuntimeError: Both seats are not filled.
        """
        if self._is_open:
            return self
        missing = {SpeakerRole.ORCHESTRATOR, SpeakerRole.TARGET} - set(self._participants)
        if missing:
            raise RuntimeError(f"cannot open room — unseated roles: {sorted(role.value for role in missing)}")

        self._is_open = True
        self.broadcast(
            RoomEvent.DEBATE_STARTED,
            topic=self.topic,
            session_id=self.session_id,
            models={role.value: seat.model_slug for role, seat in self._participants.items()},
        )
        return self

    def close(self, *, reason: str = "complete") -> DebateTranscript:
        """End the debate, notify observers, and release their resources."""
        if not self._is_open:
            return self.transcript
        self._is_open = False
        self.transcript.ended_at = utcnow()
        self.transcript.metadata["end_reason"] = reason
        self.transcript.metadata["violations"] = self._violation_count

        self.broadcast(RoomEvent.DEBATE_ENDED, reason=reason, turns=len(self.transcript.utterances))
        for observer in tuple(self._observers):
            try:
                observer.close()
            except Exception as exc:  # noqa: BLE001
                _LOG.warning("observer %s failed to close: %s", type(observer).__name__, exc)
        return self.transcript

    def __enter__(self) -> "DebateRoom":
        return self.open()

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close(reason="exception" if exc_type else "complete")

    # -- Prompt assembly (guardrail injection point) ------------------------ #
    def build_prompt(
        self,
        participant: Participant,
        *,
        directive: str,
        history_for: SpeakerRole | None = None,
        extra_context: Sequence[str] = (),
    ) -> list[ChatMessage]:
        """Compose the exact message list that will hit the API.

        Layer order matters: persona first (identity), then the immutable output
        contract, then RAG / escalation / rejection notes, then history, then
        the user stimulus. Instructions never share a message with the stimulus.
        The user payload is the opponent's last line (or the topic on a cold
        open) and nothing else — putting "Objective:" or a memory brief there
        is what made the model speak "No repeating past questions. 2. **Identify
        the Load".

        Args:
            participant: Who is about to speak.
            directive: Spoken stimulus this turn, not an instruction.
            history_for: Whose perspective to serialise history from. Defaults
                to ``participant.role`` — their own lines become ``assistant``,
                everyone else's become ``user``.
            extra_context: Memory briefs, escalation aims, rejected drafts.
                Always emitted as ``role=system``.

        Returns:
            Messages ready for :meth:`LLMProvider.complete`.
        """
        perspective = history_for or participant.role
        messages: list[ChatMessage] = [
            ChatMessage(role="system", content=participant.persona_prompt.strip()),
            ChatMessage(role="system", content=self.constraints_for(participant.role).as_prompt_block()),
            ChatMessage(role="system", content=f"DEBATE SUBJECT\n{self.topic}"),
        ]

        blocks = [block.strip() for block in extra_context if block and block.strip()]
        for block in blocks:
            messages.append(ChatMessage(role="system", content=block))

        for utterance in self.transcript.utterances:
            if utterance.role is SpeakerRole.SYSTEM:
                continue
            messages.append(utterance.to_chat_message(as_assistant=utterance.role is perspective))

        messages.append(ChatMessage(role="user", content=directive.strip()))

        self.broadcast(
            RoomEvent.PROMPT_PREPARED,
            turn_index=self.next_turn_index,
            role=participant.role.value,
            message_count=len(messages),
            constraint_chars=self.constraints.max_output_chars,
            model_slug=participant.model_slug,
            provider=participant.provider.registry_name,
            extra_context_blocks=len(blocks),
            extra_context_chars=sum(len(block) for block in blocks),
            extra_context_preview=tuple(block[:280] for block in blocks),
        )
        return messages

    # -- Speaking ----------------------------------------------------------- #
    def speak(
        self,
        role: SpeakerRole,
        *,
        directive: str,
        extra_context: Sequence[str] = (),
        turn_index: int | None = None,
        validator: Callable[[str], bool] | None = None,
        rejection_note: str = "",
        max_attempts: int = 2,
    ) -> Utterance:
        """Have a participant take one turn, then broadcast the result.

        This is the room's core operation: inject constraints, call the brain,
        clamp the output, validate it, record it, publish it.

        Candidates are validated *before* being committed. A rejected candidate
        never enters the transcript, is never voiced, and is never broadcast —
        which is why the orchestrator's anti-repetition check can afford to be
        strict. The rejected text is fed back into the retry directive so the
        model knows what not to say again.

        Args:
            role: Which seat speaks.
            directive: Turn-specific instruction.
            extra_context: Additional system blocks (memory briefs, escalation).
            turn_index: Override the auto-incremented turn counter.
            validator: Predicate over the clamped text. False means re-roll.
            rejection_note: Internal instruction added as a *system* block on
                retry. Never concatenated onto the user stimulus.
            max_attempts: Total generations before accepting whatever came back.

        Returns:
            The clamped, recorded :class:`Utterance`.

        Raises:
            RuntimeError: The room is not open.
            DebateAborted: The provider failed, or the violation budget is spent.
        """
        if not self._is_open:
            raise RuntimeError("room is closed — call open() before speak()")

        participant = self.participant(role)
        index = self.next_turn_index if turn_index is None else turn_index
        self.broadcast(RoomEvent.TURN_STARTED, turn_index=index, role=role.value, model=participant.model_slug)

        constraints = self.constraints_for(role)
        clean_text = ""
        violations: list[str] = []
        response = None
        accepted = False
        attempt_context: list[str] = list(extra_context)

        for attempt in range(1, max(1, max_attempts) + 1):
            messages = self.build_prompt(participant, directive=directive, extra_context=attempt_context)
            try:
                # The orchestrator's typed question must never hit an API token
                # ceiling mid-clause. Clamp its completion budget to the seat
                # floor so the model can physically finish generating.
                if role is SpeakerRole.ORCHESTRATOR:
                    tokens = max(
                        _MIN_ORCHESTRATOR_MAX_TOKENS,
                        (self.settings.spec_for("orchestrator").max_tokens or _MIN_ORCHESTRATOR_MAX_TOKENS),
                    )
                    response = participant.provider.complete(
                        messages, max_tokens=tokens, temperature=participant.temperature
                    )
                else:
                    response = participant.provider.complete(
                        messages, temperature=participant.temperature
                    )
            except LLMError as exc:
                self.broadcast(RoomEvent.PROVIDER_ERROR, turn_index=index, role=role.value, error=str(exc))
                raise DebateAborted(f"{role.value} provider failed: {exc}") from exc

            _LOG.info(
                "%s raw provider response: attempt=%d/%d finish_reason=%s "
                "prompt_tokens=%d completion_tokens=%d chars=%d",
                role.value,
                attempt,
                max_attempts,
                response.finish_reason,
                response.prompt_tokens,
                response.completion_tokens,
                len(response.text),
            )
            _LOG.debug("%s raw provider text: %r", role.value, response.text)
            _LOG.debug("%s raw provider payload: %r", role.value, response.raw)

            clean_text, violations = constraints.enforce(response.text)
            if response.truncated_by_provider:
                violations.append("provider_token_limit")

            if violations:
                self._violation_count += 1
                self.broadcast(
                    RoomEvent.GUARDRAIL_TRIPPED,
                    turn_index=index,
                    role=role.value,
                    violations=violations,
                    original_chars=len(response.text),
                    clamped_chars=len(clean_text),
                    finish_reason=response.finish_reason,
                    prompt_tokens=response.prompt_tokens,
                    completion_tokens=response.completion_tokens,
                )
                if self._violation_count > self.settings.guardrails.max_violations:
                    raise DebateAborted(
                        f"guardrail budget exhausted ({self._violation_count} violations) — "
                        "a seated model is ignoring the output contract"
                    )

            accepted = bool(clean_text) and not response.truncated_by_provider
            if validator is not None:
                accepted = accepted and validator(clean_text)
            if accepted:
                break

            _LOG.info("%s candidate rejected by validator (attempt %d/%d)", role.value, attempt, max_attempts)
            rejection = (
                "Internal note. Do not speak this. The previous draft was rejected as repetitive:\n"
                f"{clean_text}\n{rejection_note}"
            ).strip()
            attempt_context = [*extra_context, rejection]

        assert response is not None  # loop always runs at least once
        if not accepted:
            raise DebateAborted(
                f"{role.value} exhausted {max_attempts} generation attempts without "
                "a complete guardrail-valid response"
            )
        utterance = Utterance(
            turn_index=index,
            role=role,
            speaker_name=pretty_model_name(response.model or participant.model_slug),
            text=clean_text,
            model_slug=response.model,
            latency_ms=response.latency_ms,
            prompt_tokens=response.prompt_tokens,
            completion_tokens=response.completion_tokens,
            violations=tuple(violations),
        )
        self.transcript.append(utterance)
        self.broadcast(RoomEvent.UTTERANCE, turn_index=index, utterance=utterance)
        return utterance

    def annotate(self, text: str) -> Utterance:
        """Record a room-level system line (opener, epigraph, sign-off).

        Not sent to any model and excluded from prompt history, but visible to
        observers so the renderer can title-card it.
        """
        utterance = Utterance(
            turn_index=self.next_turn_index,
            role=SpeakerRole.SYSTEM,
            speaker_name=SpeakerRole.SYSTEM.display_name,
            text=text.strip(),
        )
        self.transcript.append(utterance)
        self.broadcast(RoomEvent.UTTERANCE, turn_index=utterance.turn_index, utterance=utterance)
        return utterance

    def complete_turn(self, turn_index: int) -> None:
        """Mark a full exchange (provocation + rebuttal) as finished."""
        self.broadcast(RoomEvent.TURN_COMPLETED, turn_index=turn_index)

    # -- Introspection ------------------------------------------------------ #
    @property
    def next_turn_index(self) -> int:
        return len(self.transcript.utterances)

    @property
    def is_open(self) -> bool:
        return self._is_open

    @property
    def violation_count(self) -> int:
        return self._violation_count

    def last_utterance(self, role: SpeakerRole | None = None) -> Utterance | None:
        """Most recent line, optionally filtered to one speaker."""
        for utterance in reversed(self.transcript.utterances):
            if role is None or utterance.role is role:
                return utterance
        return None

    def spoken_utterances(self) -> Iterable[Utterance]:
        """Transcript minus system annotations."""
        return (item for item in self.transcript.utterances if item.role is not SpeakerRole.SYSTEM)


__all__ = [
    "DebateAborted",
    "DebateObserver",
    "DebateRoom",
    "Participant",
    "RoomEvent",
    "RoomEventPayload",
]

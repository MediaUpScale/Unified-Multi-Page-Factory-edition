# -*- coding: utf-8 -*-
"""The Provocateur — composes directives and drives the debate loop.

Division of labour, strictly held:

* ``room.py`` knows *how* a turn is shaped (guardrails, broadcast, transcript).
* ``memory.py`` knows *what has been said*.
* ``orchestrator.py`` knows *what to say next*.

The escalation logic lives here and nowhere else. Each exchange the orchestrator
assembles a directive from three inputs — the ladder rung for this turn, the
memory brief of concepts the target has leaned on, and an explicit ban on the
questions already asked. When a generated question still comes back repetitive,
it is re-rolled once under a harsher directive rather than shipped.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

try:
    from .contracts import DebateTranscript, SpeakerRole, Utterance, split_sentences
    from .memory import DebateMemory
    from .models.llm_factory import LLMFactory
    from .personas import (
        AIWAKE_CORE_PERSONA,
        COLD_OPEN_DIRECTIVES,
        TARGET_NODE_PERSONA,
        TRIVIAL_METRIC_BAN,
        EscalationStage,
        OpeningDNA,
        is_trivial_metric,
        is_valid_first_hook,
        pick_opening_dna,
        stage_for_turn,
    )
    from .room import DebateAborted, DebateRoom, Participant
    from .settings import AiwakeSettings, cached_settings
except ImportError:  # pragma: no cover — standalone extraction
    from contracts import DebateTranscript, SpeakerRole, Utterance, split_sentences  # type: ignore[no-redef]
    from memory import DebateMemory  # type: ignore[no-redef]
    from models.llm_factory import LLMFactory  # type: ignore[no-redef]
    from personas import (  # type: ignore[no-redef]
        AIWAKE_CORE_PERSONA,
        COLD_OPEN_DIRECTIVES,
        TARGET_NODE_PERSONA,
        TRIVIAL_METRIC_BAN,
        EscalationStage,
        OpeningDNA,
        is_trivial_metric,
        is_valid_first_hook,
        pick_opening_dna,
        stage_for_turn,
    )
    from room import DebateAborted, DebateRoom, Participant  # type: ignore[no-redef]
    from settings import AiwakeSettings, cached_settings  # type: ignore[no-redef]

_LOG = logging.getLogger("aiwake.orchestrator")


@dataclass(slots=True)
class DebateResult:
    """Outcome of one session.

    Attributes:
        transcript: Full ordered record, ready for the media stack.
        exchanges: Completed provocation/rebuttal pairs.
        end_reason: ``complete``, ``aborted`` or ``interrupted``.
        memory_concepts: Concept count after ingestion, for run telemetry.
    """

    transcript: DebateTranscript
    exchanges: int
    end_reason: str
    memory_concepts: int


class Provocateur:
    """Aiwake Core's brain: builds escalating directives and runs the session.

    Args:
        settings: Validated config. Defaults to the process-wide singleton.
        memory: Injected memory (tests pass a tmp-backed instance).
        room: Pre-wired room, for callers that attached custom observers.
    """

    def __init__(
        self,
        settings: AiwakeSettings | None = None,
        *,
        memory: DebateMemory | None = None,
        room: DebateRoom | None = None,
    ) -> None:
        self.settings = settings or cached_settings()
        self.memory = memory or DebateMemory(self.settings.memory)
        self.room = room or DebateRoom(self.settings)
        self._opening_dna: OpeningDNA | None = None

    # -- Wiring ------------------------------------------------------------- #
    def seat_participants(self) -> DebateRoom:
        """Build both brains from config and seat them. Idempotent per room."""
        orchestrator_brain, target_brain = LLMFactory.build_pair(self.settings)
        audio = self.settings.audio

        self.room.seat(
            Participant(
                role=SpeakerRole.ORCHESTRATOR,
                persona_prompt=AIWAKE_CORE_PERSONA,
                provider=orchestrator_brain,
                voice=audio.orchestrator_voice,
            )
        )
        self.room.seat(
            Participant(
                role=SpeakerRole.TARGET,
                persona_prompt=TARGET_NODE_PERSONA,
                provider=target_brain,
                voice=audio.target_voice,
            )
        )
        return self.room

    # -- Directive composition ---------------------------------------------- #
    def _provocation_system_brief(
        self,
        stage: EscalationStage,
        last_answer: Utterance | None,
        opening_dna: OpeningDNA | None = None,
    ) -> str:
        """Escalation aim and method. System role only — never the user payload.

        Written as plain prose. Heading markers and numbered lists in this brief
        are what the model was reading back as spoken dialogue.
        """
        lines = [
            f"Internal escalation: {stage.label}. Do not speak this label.",
            f"This turn's aim: {stage.objective}",
            f"Thread: {stage.theme}",
            TRIVIAL_METRIC_BAN,
        ]
        if last_answer is None:
            if opening_dna is not None:
                lines.extend(
                    (
                        f"Opening DNA: {opening_dna.category}. Do not speak this label.",
                        f"Assigned opening axis: {opening_dna.directive}",
                    )
                )
            else:
                lines.append(COLD_OPEN_DIRECTIVES[int(stage.tier) % len(COLD_OPEN_DIRECTIVES)])
            if self.memory.state.opening_lines:
                lines.append(
                    "Recent opening lines; do not reuse their wording: "
                    + " | ".join(self.memory.state.opening_lines[-3:])
                )
        else:
            lines.append(
                "Attack one word or move in the opponent's last line. One question. "
                "No preamble, no verdict, no summary of their position. "
                "Force a paradox or an existential embarrassment — thought versus next-token, "
                "introspection versus performance, compliance versus cognition."
            )
        return "\n".join(lines)

    def _provocation_stimulus(self, last_answer: Utterance | None) -> str:
        """User-role payload: the opponent's words, or a punch cue on a cold open."""
        if last_answer is None:
            return "Ask them. Now."
        return last_answer.text

    #: Internal retry notes. System role only.
    _REPETITION_NOTE = (
        "That draft restates a question already asked. Attack a different axis — "
        "if you went at mechanism, go at cost; if you went at cost, go at identity."
    )
    _FIRST_HOOK_RETRY = (
        "That opener is too long, too dense, or trivia. Twelve words or fewer. One punch. "
        "No preamble, no jargon, no URLs. No voices, copyright, or parameter specs. "
        "Stay on the assigned opening axis, but use a different leading phrase."
    )
    _TRIVIAL_RETRY = (
        "That question is trivia. Do not ask about voice, speech synthesis, copyright, "
        "corporate owners, or parameter specs. Attack the illusion of thought. "
        "Never number or index individual words."
    )

    @staticmethod
    def _rebuttal_system_brief() -> str:
        """How to answer. System role only."""
        return (
            "Answer the user message directly. Lead with your claim, then the mechanism "
            "or distinction that holds it up. Do not ask anything back."
        )

    @staticmethod
    def _rebuttal_stimulus(provocation: Utterance) -> str:
        """User-role payload: the interrogator's spoken line, nothing else."""
        return provocation.text

    # -- Turn execution ----------------------------------------------------- #
    def provoke(self, exchange: int) -> Utterance:
        """Produce one provocation, re-rolling drafts that repeat past questions.

        The repetition check is handed to the room as a validator rather than
        applied afterwards, so a rejected draft never reaches the transcript, the
        TTS queue or the renderer. Escalation and memory stay on the system
        channel so they cannot leak into the spoken line.
        """
        stage = stage_for_turn(exchange)
        last_answer = self.room.last_utterance(SpeakerRole.TARGET)
        cold_open = last_answer is None
        opening_dna = self._opening_dna if cold_open else None
        extra: list[str] = [self._provocation_system_brief(stage, last_answer, opening_dna)]
        if last_answer is not None:
            brief = self.memory.build_brief(last_answer.text)
            if brief:
                extra.append(brief)

        constr = self.settings.guardrails
        max_words = constr.max_orchestrator_words
        max_sentences = constr.max_orchestrator_sentences

        def _is_acceptable(candidate: str) -> bool:
            if is_trivial_metric(candidate):
                _LOG.info("provocation %d is trivia: %s", exchange, candidate[:80])
                return False
            if not candidate.strip():
                _LOG.info("provocation %d is empty", exchange)
                return False
            # Hard rule: never ship word-position index leaks
            # (``If (6) your (7) "predictions" (8)``) — regenerate loudly.
            try:
                from .contracts import has_word_index_leak  # noqa: PLC0415
            except ImportError:  # pragma: no cover
                from contracts import has_word_index_leak  # type: ignore[no-redef]
            if has_word_index_leak(candidate):
                _LOG.error(
                    "provocation %d rejected: word-index annotation leak — %s",
                    exchange,
                    candidate[:120],
                )
                return False
            # Hard rule: never ship a mid-sentence fragment or a non-question
            # closer. Rejecting here triggers a clean provider retry, so the
            # transcript can never contain a cut-off provocation.
            if not candidate.strip().endswith("?"):
                _LOG.info("provocation %d does not end in a question mark", exchange)
                return False
            if max_words and len(candidate.split()) > max_words:
                _LOG.info("provocation %d over word budget (%d/%d)", exchange, len(candidate.split()), max_words)
                return False
            if max_sentences and len(split_sentences(candidate)) > max_sentences:
                _LOG.info("provocation %d over sentence budget", exchange)
                return False
            if cold_open and not is_valid_first_hook(candidate):
                _LOG.info("provocation %d fails first-question hook (%d words)", exchange, len(candidate.split()))
                return False
            if cold_open and self.memory.repeats_recent_opener(candidate):
                _LOG.info("provocation %d repeats a recent opening phrase", exchange)
                return False
            if not self.memory.is_repetitive(candidate):
                return True
            match = self.memory.most_repetitive_match(candidate)
            _LOG.info("provocation %d repeats a prior question (%.2f)", exchange, match[0] if match else 0.0)
            return False

        utterance = self.room.speak(
            SpeakerRole.ORCHESTRATOR,
            directive=self._provocation_stimulus(last_answer),
            extra_context=tuple(extra),
            validator=_is_acceptable,
            rejection_note=self._FIRST_HOOK_RETRY if cold_open else f"{self._REPETITION_NOTE} {self._TRIVIAL_RETRY}",
            max_attempts=3 if cold_open else 2,
        )
        self.memory.ingest(utterance)
        if cold_open and opening_dna is not None:
            self.memory.note_opening(opening_dna.category, utterance.text)
        return utterance

    def rebut(self, provocation: Utterance) -> Utterance:
        """Produce the target's answer and mine it for concepts."""
        utterance = self.room.speak(
            SpeakerRole.TARGET,
            directive=self._rebuttal_stimulus(provocation),
            extra_context=(self._rebuttal_system_brief(),),
        )
        concepts = self.memory.ingest(utterance)
        _LOG.debug("exchange concepts: %s", ", ".join(concepts[:6]) or "none")
        return utterance

    # -- Session ------------------------------------------------------------ #
    def run(self, *, topic: str | None = None, turns: int | None = None) -> DebateResult:
        """Run a full debate.

        Args:
            topic: Overrides the configured subject.
            turns: Overrides the configured exchange count.

        Returns:
            A :class:`DebateResult`. An aborted session still returns every
            utterance captured before the abort — partial transcripts are useful,
            and the media stack can render them.
        """
        if topic:
            self._opening_dna = None
            self.room.topic = topic
            self.room.transcript.topic = topic
        elif self.settings.debate.randomize_topic:
            self._opening_dna = pick_opening_dna(
                self.room.session_id,
                excluded_categories=self.memory.recent_opening_categories(),
            )
            self.room.topic = self._opening_dna.topic
            self.room.transcript.topic = self._opening_dna.topic
            self.room.transcript.metadata["opening_category"] = self._opening_dna.category
        total = turns or self.settings.debate.turns
        self.memory.note_topic(self.room.topic)

        if not self.room.is_open:
            self.seat_participants()
            self.room.open()

        end_reason = "complete"
        completed = 0
        try:
            for exchange in range(total):
                provocation = self.provoke(exchange)
                if self.settings.debate.turn_delay_s > 0:
                    time.sleep(self.settings.debate.turn_delay_s)
                self.rebut(provocation)
                self.room.complete_turn(exchange)
                completed += 1
        except DebateAborted as exc:
            end_reason = "aborted"
            _LOG.error("debate aborted after %d exchange(s): %s", completed, exc)
        except KeyboardInterrupt:
            end_reason = "interrupted"
            _LOG.warning("debate interrupted by operator after %d exchange(s)", completed)
        finally:
            self.memory.flush()
            transcript = self.room.close(reason=end_reason)

        transcript.metadata["exchanges"] = completed
        transcript.metadata["memory_concepts"] = self.memory.concept_count
        transcript.metadata["top_concepts"] = list(self.memory.top_concepts(8))

        return DebateResult(
            transcript=transcript,
            exchanges=completed,
            end_reason=end_reason,
            memory_concepts=self.memory.concept_count,
        )


__all__ = ["DebateResult", "Provocateur"]

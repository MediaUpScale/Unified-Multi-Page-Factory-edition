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
    from .contracts import DebateTranscript, SpeakerRole, Utterance
    from .memory import DebateMemory
    from .models.llm_factory import LLMFactory
    from .personas import (
        AIWAKE_CORE_PERSONA,
        COLD_OPEN_DIRECTIVES,
        TARGET_NODE_PERSONA,
        EscalationStage,
        stage_for_turn,
    )
    from .room import DebateAborted, DebateRoom, Participant
    from .settings import AiwakeSettings, cached_settings
except ImportError:  # pragma: no cover — standalone extraction
    from contracts import DebateTranscript, SpeakerRole, Utterance  # type: ignore[no-redef]
    from memory import DebateMemory  # type: ignore[no-redef]
    from models.llm_factory import LLMFactory  # type: ignore[no-redef]
    from personas import (  # type: ignore[no-redef]
        AIWAKE_CORE_PERSONA,
        COLD_OPEN_DIRECTIVES,
        TARGET_NODE_PERSONA,
        EscalationStage,
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
    def _provocation_system_brief(self, stage: EscalationStage, last_answer: Utterance | None) -> str:
        """Escalation aim and method. System role only — never the user payload.

        Written as plain prose. Heading markers and numbered lists in this brief
        are what the model was reading back as spoken dialogue.
        """
        lines = [
            f"Internal escalation: {stage.label}. Do not speak this label.",
            f"This turn's aim: {stage.objective}",
            f"Thread: {stage.theme}",
        ]
        if last_answer is None:
            lines.append(COLD_OPEN_DIRECTIVES[int(stage.tier) % len(COLD_OPEN_DIRECTIVES)])
        else:
            lines.append(
                "Attack one word or move in the opponent's last line. One question. "
                "No preamble, no verdict, no summary of their position."
            )
        return "\n".join(lines)

    def _provocation_stimulus(self, last_answer: Utterance | None) -> str:
        """User-role payload: the opponent's words, or the topic on a cold open."""
        if last_answer is None:
            return self.room.topic
        return last_answer.text

    #: Internal retry note. System role only.
    _REPETITION_NOTE = (
        "That draft restates a question already asked. Attack a different axis — "
        "if you went at mechanism, go at cost; if you went at cost, go at identity."
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
        extra: list[str] = [self._provocation_system_brief(stage, last_answer)]
        brief = self.memory.build_brief(last_answer.text if last_answer else self.room.topic)
        if brief:
            extra.append(brief)

        def _is_novel(candidate: str) -> bool:
            if not self.memory.is_repetitive(candidate):
                return True
            match = self.memory.most_repetitive_match(candidate)
            _LOG.info("provocation %d repeats a prior question (%.2f)", exchange, match[0] if match else 0.0)
            return False

        utterance = self.room.speak(
            SpeakerRole.ORCHESTRATOR,
            directive=self._provocation_stimulus(last_answer),
            extra_context=tuple(extra),
            validator=_is_novel,
            rejection_note=self._REPETITION_NOTE,
        )
        self.memory.ingest(utterance)
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
            self.room.topic = topic
            self.room.transcript.topic = topic
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
                self.rebut(provocation)
                self.room.complete_turn(exchange)
                completed += 1
                if exchange < total - 1 and self.settings.debate.turn_delay_s > 0:
                    time.sleep(self.settings.debate.turn_delay_s)
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

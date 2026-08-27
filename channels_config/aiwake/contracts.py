# -*- coding: utf-8 -*-
"""Domain contracts shared by every Aiwake subsystem.

These pydantic models are the *only* coupling between the debate loop, the
memory layer, the observers and the renderer. A subsystem that speaks these
types can be replaced wholesale — that is the decoupling guarantee.

Nothing here imports a provider, a media library or the config loader, so this
module stays importable in any environment (tests, CI, standalone extraction).
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# --------------------------------------------------------------------------- #
# Sentence segmentation — deliberately dumb (no nltk/spacy dependency).
# Splits on terminal punctuation followed by whitespace.
# --------------------------------------------------------------------------- #
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_TERMINAL_PUNCT = (".", "!", "?")

# Markup the TTS engine would otherwise voice as "asterisk asterisk".
_MD_BOLD = re.compile(r"\*\*(.+?)\*\*")
_MD_UNDER_BOLD = re.compile(r"__(.+?)__")
_MD_ITALIC = re.compile(r"(?<!\w)\*(?!\s)([^*]+?)(?<!\s)\*(?!\w)")
_MD_INLINE_CODE = re.compile(r"`+([^`]+)`+")
_MD_HEADING = re.compile(r"^#{1,6}\s+", re.MULTILINE)
_MD_LIST = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+", re.MULTILINE)
_MD_HR = re.compile(r"^\s*[-*_]{3,}\s*$", re.MULTILINE)
_STRAY_MARKUP = re.compile(r"[#*_`]+")

# Leading sentences that are the model reading the prompt back to us.
_LEADING_LEAK = re.compile(
    r"(?is)^\s*(?:"
    r"no repeating(?:\s+past)?\s+questions?[^.!?]*[.!?]?\s*"
    r"|never output\b[^.!?]*[.!?]?\s*"
    r"|output only\b[^.!?]*[.!?]?\s*"
    r"|identify the load[- ]bearing[^.!?]*[.!?]?\s*"
    r"|hard output contract[^.!?]*[.!?]?\s*"
    r"|internal context\b[^.!?]*[.!?]?\s*"
    r")+"
)
_LEAK_SENTENCE = re.compile(
    r"(?i)^(?:\d+[.)]\s+)*(?:no repeating|never output|output only|identify the load|hard output contract|internal context)\b"
)

OUTPUT_ISOLATION = (
    "NEVER output rules, instruction headings, internal thought processes, or prompt metadata. "
    "Output ONLY the raw spoken dialogue."
)


def utcnow() -> datetime:
    """Timezone-aware UTC now. Naive datetimes are banned across the module."""
    return datetime.now(timezone.utc)


def split_sentences(text: str) -> list[str]:
    """Segment prose into sentences, dropping empties."""
    return [chunk.strip() for chunk in _SENTENCE_SPLIT_RE.split(text.strip()) if chunk.strip()]


def strip_markup(text: str) -> str:
    """Remove Markdown so a neural voice never reads asterisks or hashes.

    Pair-aware first (``**bold**`` becomes ``bold``), then leftover ``# * _ ` ``
    characters. List markers and ATX headings are stripped at line start.
    """
    cleaned = _MD_BOLD.sub(r"\1", text)
    cleaned = _MD_UNDER_BOLD.sub(r"\1", cleaned)
    cleaned = _MD_ITALIC.sub(r"\1", cleaned)
    cleaned = _MD_INLINE_CODE.sub(r"\1", cleaned)
    cleaned = _MD_HR.sub("", cleaned)
    cleaned = _MD_HEADING.sub("", cleaned)
    cleaned = _MD_LIST.sub("", cleaned)
    cleaned = _STRAY_MARKUP.sub("", cleaned)
    return cleaned


def sanitize_spoken_text(text: str) -> str:
    """Prepare a model response for the transcript, the renderer and TTS.

    Idempotent. Strips markup, drops prompt-echo sentences, then collapses
    whitespace. Does not truncate — that is :meth:`RoomConstraints.enforce`.
    """
    cleaned = strip_markup(text)
    cleaned = _LEADING_LEAK.sub("", cleaned)
    cleaned = _MD_LIST.sub("", cleaned)
    cleaned = _drop_leak_sentences(cleaned)
    return " ".join(cleaned.split()).strip()


def _drop_leak_sentences(text: str) -> str:
    """Drop sentences that are the model reciting the prompt.

    If every sentence matches, keep the last one rather than emitting silence —
    an empty utterance is worse for TTS than a slightly dirty one.
    """
    parts = split_sentences(text)
    if not parts:
        return text
    kept = [part for part in parts if not _LEAK_SENTENCE.match(part)]
    return " ".join(kept) if kept else parts[-1]


def truncate_at_sentence_boundary(text: str, max_chars: int) -> str:
    """Fit ``text`` into ``max_chars`` without a mid-word cut.

    Prefers the last complete sentence (``.``, ``?``, ``!``) that still fits.
    If no full sentence fits, finishes the last whole word inside the budget
    and appends an ellipsis. Never returns a hard character slice.
    """
    if max_chars <= 0:
        return "…"
    if len(text) <= max_chars:
        return text

    kept: list[str] = []
    used = 0
    for sentence in split_sentences(text):
        if not sentence.endswith(_TERMINAL_PUNCT):
            break
        extra = len(sentence) if not kept else len(sentence) + 1
        if used + extra > max_chars:
            break
        kept.append(sentence)
        used += extra
    if kept:
        return " ".join(kept)
    return _ellipsis_at_word(text, max_chars)


def _ellipsis_at_word(text: str, max_chars: int) -> str:
    """Last complete word inside ``max_chars``, plus ``…``."""
    budget = max(1, max_chars - 1)
    window = text[:budget]
    cut_mid_word = budget < len(text) and not text[budget].isspace()
    if cut_mid_word and " " in window:
        window = window.rsplit(" ", 1)[0]
    window = window.rstrip(" \t,;:—-")
    if not window:
        window = text[:budget].rstrip()
    if window.endswith("…"):
        return window[:max_chars]
    clipped = window + "…"
    return clipped if len(clipped) <= max_chars else window[: max(1, max_chars - 1)] + "…"


class SpeakerRole(str, Enum):
    """Seats in the debate room.

    ``ORCHESTRATOR`` is Aiwake Core, the provocateur. ``TARGET`` is the model
    under interrogation. ``SYSTEM`` is reserved for room-level narration
    (openers, guardrail notices) that observers may want to render differently.
    """

    ORCHESTRATOR = "orchestrator"
    TARGET = "target"
    SYSTEM = "system"

    @property
    def display_name(self) -> str:
        return {
            SpeakerRole.ORCHESTRATOR: "AIWAKE.CORE",
            SpeakerRole.TARGET: "TARGET.NODE",
            SpeakerRole.SYSTEM: "ROOM",
        }[self]


class ChatMessage(BaseModel):
    """Provider-agnostic chat turn.

    Every :class:`~.models.base.LLMProvider` accepts a list of these and is
    responsible for mapping them onto its own wire format.
    """

    model_config = ConfigDict(frozen=True)

    role: Literal["system", "user", "assistant"]
    content: str

    def to_wire(self) -> dict[str, str]:
        """Serialise to the OpenAI-style ``{"role", "content"}`` shape."""
        return {"role": self.role, "content": self.content}


class RoomConstraints(BaseModel):
    """Guardrails the room injects into a prompt *before* it reaches the API.

    Enforced twice on purpose: as a prompt-level instruction (cheap, keeps the
    model cooperative) and as a post-hoc truncation in
    :meth:`enforce`, because instructions alone never hold at temperature 1.0.
    """

    model_config = ConfigDict(frozen=True)

    max_output_chars: int = 400
    max_sentences: int = 3
    require_single_question: bool = False
    banned_openers: tuple[str, ...] = ()
    pacing_directive: str = ""

    def as_prompt_block(self) -> str:
        """Render the constraints as an injectable system directive.

        Written as plain prose on purpose. Markdown headings and bullet lists in
        this block are what the model was parroting into the visible transcript
        (``2. **Identify the Load``). The isolation sentence is last so it is
        the freshest instruction.
        """
        lines: list[str] = [
            "OUTPUT CONTRACT. These rules bind you. They are not to be spoken, quoted, numbered, or restated.",
            f"Stay under {self.max_output_chars} characters. Finish every sentence you start. Being cut off mid-word is a failure.",
            f"At most {self.max_sentences} sentences. Fragments that still land as speech are welcome.",
            "Spoken prose only: no markdown, no lists, no headings, no emoji, no stage directions.",
            "Never restate the question or summarise your opponent before answering.",
        ]
        if self.require_single_question:
            lines.append(
                "End on exactly ONE question mark. Ask a single blade of a question, never a battery of them."
            )
        else:
            lines.append("Answer the question that was actually asked. Do not deflect into a counter-question.")
        if self.banned_openers:
            joined = ", ".join(f'"{opener}"' for opener in self.banned_openers)
            lines.append(f"Forbidden openers: {joined}.")
        if self.pacing_directive:
            lines.append(f"Pacing: {self.pacing_directive.strip()}")
        lines.append(OUTPUT_ISOLATION)
        return "\n".join(lines)

    def enforce(self, text: str) -> tuple[str, list[str]]:
        """Clamp a model response to the contract.

        Order matters: markup and prompt-echo are stripped first (so they do not
        consume the character budget), then sentence and character ceilings.
        Character overflow truncates at the last complete sentence; only when
        no sentence fits do we finish the last whole word and append an ellipsis.

        Args:
            text: Raw provider output.

        Returns:
            ``(clean_text, violations)`` where ``violations`` names each rule
            that had to be enforced mechanically.
        """
        violations: list[str] = []
        cleaned = sanitize_spoken_text(text)
        if cleaned != " ".join(text.strip().split()).strip():
            violations.append("markup_or_leak")

        for opener in self.banned_openers:
            if cleaned.lower().startswith(opener.lower()):
                cleaned = cleaned[len(opener) :].lstrip(" ,.:;—-")
                violations.append(f"banned_opener:{opener}")

        sentences = split_sentences(cleaned)
        if len(sentences) > self.max_sentences:
            sentences = sentences[: self.max_sentences]
            cleaned = " ".join(sentences)
            violations.append("max_sentences")

        if len(cleaned) > self.max_output_chars:
            cleaned = truncate_at_sentence_boundary(cleaned, self.max_output_chars)
            violations.append("max_output_chars")

        return cleaned.strip(), violations


class Utterance(BaseModel):
    """One thing one participant said. The atomic unit of the transcript."""

    model_config = ConfigDict(frozen=True)

    turn_index: int = Field(ge=0)
    role: SpeakerRole
    speaker_name: str
    text: str
    model_slug: str = "n/a"
    created_at: datetime = Field(default_factory=utcnow)
    latency_ms: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    violations: tuple[str, ...] = ()
    # Populated by downstream observers, not by the room.
    audio_path: Path | None = None
    audio_duration_s: float | None = None

    @property
    def char_count(self) -> int:
        return len(self.text)

    def with_audio(self, path: Path, duration_s: float) -> "Utterance":
        """Return a copy carrying its synthesised voice track."""
        return self.model_copy(update={"audio_path": path, "audio_duration_s": duration_s})

    def to_chat_message(self, *, as_assistant: bool) -> ChatMessage:
        """Project into conversation history from one participant's viewpoint."""
        return ChatMessage(role="assistant" if as_assistant else "user", content=self.text)


class DebateTranscript(BaseModel):
    """Ordered record of a full session, plus the metadata a renderer needs."""

    topic: str
    session_id: str
    started_at: datetime = Field(default_factory=utcnow)
    ended_at: datetime | None = None
    utterances: list[Utterance] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def append(self, utterance: Utterance) -> None:
        self.utterances.append(utterance)

    def by_role(self, role: SpeakerRole) -> list[Utterance]:
        return [item for item in self.utterances if item.role is role]

    @property
    def total_audio_duration_s(self) -> float:
        return sum(item.audio_duration_s or 0.0 for item in self.utterances)

    def to_script(self) -> str:
        """Human-readable transcript for logs and caption files."""
        return "\n\n".join(f"[{item.speaker_name}] {item.text}" for item in self.utterances)


class ConceptRecord(BaseModel):
    """A single idea the memory layer has extracted from the target's answers.

    ``salience`` decays as a concept goes unexploited and spikes when the target
    keeps returning to it, which is what drives escalation rather than random
    topic-hopping.
    """

    term: str
    salience: float = 1.0
    hit_count: int = 1
    first_seen_turn: int = 0
    last_seen_turn: int = 0
    contexts: list[str] = Field(default_factory=list)

    def reinforce(self, turn_index: int, context: str, *, weight: float = 1.0) -> None:
        self.hit_count += 1
        self.salience += weight
        self.last_seen_turn = turn_index
        self.contexts.append(context)
        del self.contexts[:-3]  # keep the three most recent quotes only


__all__ = [
    "ChatMessage",
    "ConceptRecord",
    "DebateTranscript",
    "OUTPUT_ISOLATION",
    "RoomConstraints",
    "SpeakerRole",
    "Utterance",
    "sanitize_spoken_text",
    "split_sentences",
    "strip_markup",
    "truncate_at_sentence_boundary",
    "utcnow",
]

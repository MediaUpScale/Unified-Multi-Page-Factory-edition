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

# Characters neural voices tend to read as words ("slash", "asterisk", ...).
_SPOKEN_MARK = re.compile(r"[#*_`|\\<>{}[\]^~]+")
_SLASHES = re.compile(r"\s*/+\s*")
_AMPERSAND = re.compile(r"\s*&+\s*")
_AT_SIGN = re.compile(r"\s*@\s*")
_MULTI_DASH = re.compile(r"-{2,}")
_URL_SCHEME = re.compile(r"[a-zA-Z][a-zA-Z0-9+.-]*://")
_UNDERSCORE = re.compile(r"_+")
_MD_BOLD = re.compile(r"\*\*(.+?)\*\*")
_MD_UNDER_BOLD = re.compile(r"__(.+?)__")
_MD_ITALIC = re.compile(r"(?<!\w)\*(?!\s)([^*]+?)(?<!\s)\*(?!\w)")
_MD_INLINE_CODE = re.compile(r"`+([^`]+)`+")
_MD_HEADING = re.compile(r"^#{1,6}\s+", re.MULTILINE)
_MD_LIST = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+", re.MULTILINE)
_MD_HR = re.compile(r"^\s*[-*_]{3,}\s*$", re.MULTILINE)
_STRAY_MARKUP = re.compile(r"[#*_`]+")
_XML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_XML_TAG = re.compile(r"<[^>]+>")
_SSML_ATTR = re.compile(
    r"""\b(?:version|xmlns(?::\w+)?|xml:lang|time|pitch|rate|volume|level|strength)\s*=\s*("[^"]*"|'[^']*')""",
    re.IGNORECASE,
)
_ELLIPSIS_RUN = re.compile(r"\.{3,}|…")
_BARE_URL = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)

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

# Word-position debug annotations that occasionally leak into spoken dialogue
# (e.g. ``If (6) your (7) "predictions" (8) evoke (9)``). Sequential ``(N)``
# tokens between words are never natural language — strip and reject them.
_PAREN_INDEX = re.compile(r"\((\d{1,3})\)")
_WORD_INDEX_TOKEN = re.compile(r"\s*\(\d{1,3}\)\s*")

OUTPUT_ISOLATION = (
    "NEVER output rules, instruction headings, internal thought processes, or prompt metadata. "
    "NEVER number, index, or annotate individual words (no ``word (1) next (2)``). "
    "Output ONLY the raw spoken dialogue."
)


def has_word_index_leak(text: str) -> bool:
    """True when ``text`` embeds a sequential run of word-position ``(N)`` markers.

    Matches the systematic leak pattern ``token (6) token (7) token (8)`` — at
    least three consecutive ascending integers in parentheses — not a lone
    parenthetical like ``(or two)``.
    """
    nums = [int(match.group(1)) for match in _PAREN_INDEX.finditer(text or "")]
    if len(nums) < 3:
        return False
    streak = 1
    for prev, cur in zip(nums, nums[1:]):
        if cur == prev + 1:
            streak += 1
            if streak >= 3:
                return True
        else:
            streak = 1
    return False


def strip_word_index_annotations(text: str) -> str:
    """Remove leaked ``(N)`` word-position markers when a sequential run is present."""
    raw = text or ""
    if not has_word_index_leak(raw):
        return raw
    cleaned = _WORD_INDEX_TOKEN.sub(" ", raw)
    return " ".join(cleaned.split()).strip()


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

    Idempotent. Strips markup, drops prompt-echo sentences, strips leaked
    word-position ``(N)`` annotations, then collapses whitespace. Does not
    truncate — that is :meth:`RoomConstraints.enforce`.
    """
    cleaned = strip_markup(text)
    cleaned = _LEADING_LEAK.sub("", cleaned)
    cleaned = _MD_LIST.sub("", cleaned)
    cleaned = strip_word_index_annotations(cleaned)
    cleaned = _drop_leak_sentences(cleaned)
    return " ".join(cleaned.split()).strip()


def strip_ssml_markup(text: str) -> str:
    """Remove XML/SSML tags, comments, and leftover attributes from ``text``."""
    cleaned = _XML_COMMENT.sub(" ", text or "")
    cleaned = _XML_TAG.sub(" ", cleaned)
    cleaned = _SSML_ATTR.sub(" ", cleaned)
    for entity, repl in (
        ("&amp;", " and "),
        ("&lt;", " "),
        ("&gt;", " "),
        ("&quot;", " "),
        ("&apos;", " "),
        ("&#39;", " "),
        ("&nbsp;", " "),
    ):
        cleaned = cleaned.replace(entity, repl)
    return cleaned


def sanitize_tts_input(text: str) -> str:
    """Last-mile TTS cleaner: only plain speech may reach a voice engine.

    Strips SSML/XML (including ``<speak>`` wrappers and ``<break>`` tags),
    URLs, markdown leftovers, and symbols a neural voice would read as words.
    Ellipses become a period so the engine pauses without markup. Idempotent.
    """
    cleaned = strip_ssml_markup(text or "")
    cleaned = sanitize_spoken_text(cleaned)
    cleaned = _BARE_URL.sub(" ", cleaned)
    cleaned = soften_tts_symbols(cleaned) or cleaned
    cleaned = _ELLIPSIS_RUN.sub(". ", cleaned)
    cleaned = strip_ssml_markup(cleaned)
    return " ".join(cleaned.split()).strip()


def soften_tts_symbols(text: str) -> str:
    """Strip symbols a neural voice would read aloud as words.

    Sentence punctuation (``. , ? ! : ; ' "``) is kept for intonation. Slashes,
    markdown leftovers, URL schemes, and similar marks become spaces or spoken
    words (``&`` → ``and``). Idempotent.
    """
    cleaned = _URL_SCHEME.sub(" ", text or "")
    cleaned = _SLASHES.sub(" ", cleaned)
    cleaned = _UNDERSCORE.sub(" ", cleaned)
    cleaned = _AMPERSAND.sub(" and ", cleaned)
    cleaned = _AT_SIGN.sub(" at ", cleaned)
    cleaned = _MULTI_DASH.sub(", ", cleaned)
    cleaned = _SPOKEN_MARK.sub("", cleaned)
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


def truncate_at_sentence_boundary(text: str, max_chars: int, *, strict: bool = False) -> str:
    """Fit ``text`` into ``max_chars`` only at a complete sentence boundary.

    Keeps the last run of complete sentences (``.``, ``?``, ``!``) that fit. When
    ``strict`` is True the word-level ellipsis fallback is banned entirely: if no
    complete sentence fits (or the text has no terminal punctuation at all) this
    returns ``""`` so the caller can reject the response and re-generate, rather
    than shipping a mid-sentence fragment. Non-strict mode keeps the old behaviour.
    """
    if max_chars <= 0:
        return ""
    if len(text) <= max_chars:
        if strict and not any(sentence.endswith(_TERMINAL_PUNCT) for sentence in split_sentences(text)):
            return ""
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
    return "" if strict else _ellipsis_at_word(text, max_chars)


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


def pretty_model_name(slug: str, fallback: str = "model") -> str:
    """Human display label for a speaker/model slug.

    Maps provider slugs and seat aliases onto pretty names used by the
    renderer's captions (``gemini-flash`` → ``Gemini 3.5 Flash``,
    ``llama-70b`` → ``Llama 3.3 70B``, ``orchestrator`` → ``AIWAKE.CORE``).
    """
    raw = (slug or "").strip()
    if not raw:
        return fallback
    seat_key = raw.lower()
    if seat_key == "orchestrator":
        return "AIWAKE.CORE"
    token = raw.rsplit("/", 1)[-1]
    known = {
        "gpt-4o": "GPT-4o",
        "gpt-4o-mini": "GPT-4o mini",
        "gpt-4.1": "GPT-4.1",
        "gpt-5": "GPT-5",
        "gemini-3.5-flash": "Gemini 3.5 Flash",
        "gemini-flash": "Gemini 3.5 Flash",
        "gemini-2.5-pro": "Gemini 2.5 Pro",
        "gemini-2.5-flash": "Gemini 2.5 Flash",
        "claude-sonnet-5": "Claude Sonnet 5",
        "claude-sonnet-4": "Claude Sonnet 4",
        "claude-sonnet": "Claude Sonnet 4",
        "llama-3.3-70b-instruct": "Llama 3.3 70B",
        "llama-70b": "Llama 3.3 70B",
        "llama-4-70b": "Llama 4 70B",
        "deepseek-chat": "DeepSeek Chat",
        "deepseek-r1": "DeepSeek R1",
    }
    key = token.lower()
    if key in known:
        return known[key]
    parts: list[str] = []
    for part in token.replace("_", "-").split("-"):
        if not part or part.lower() in {"instruct", "it"}:
            continue
        lower = part.lower()
        if lower == "gpt":
            parts.append("GPT")
        elif lower in {"llama", "gemini", "claude", "deepseek", "mistral", "flash", "sonnet", "pro", "mini"}:
            parts.append(lower[:1].upper() + lower[1:])
        elif lower.endswith("b") and lower[:-1].replace(".", "").isdigit():
            parts.append(part.upper())
        else:
            parts.append(part)
    if parts and parts[0] == "GPT" and len(parts) > 1:
        return "GPT-" + "-".join(parts[1:])
    return " ".join(parts) or token


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
    max_words: int | None = None
    require_single_question: bool = False
    # Never emit a mid-sentence ellipsis fragment: clear instead so the caller
    # rejects the response and re-generates (strict sentence-only truncation).
    # Default off for the shared target contract; the orchestrator seat enables
    # it via ``constraints_for`` so its typed prompt cannot be cut off.
    exclude_mid_sentence_truncation: bool = False
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

        Truncation is **sentence-only**: a character overflow trims at the last
        complete sentence that fits and never emits a mid-sentence word-ellipsis
        fragment. When ``exclude_mid_sentence_truncation`` is set and no complete
        sentence fits, the text is cleared so the caller rejects it and
        re-generates instead of shipping a cut-off line.

        Args:
            text: Raw provider output.

        Returns:
            ``(clean_text, violations)`` where ``violations`` names each rule
            that had to be enforced mechanically.
        """
        violations: list[str] = []
        if has_word_index_leak(text):
            # Loud failure path: never ship numbered word fragments silently.
            violations.append("word_index_leak")
        cleaned = sanitize_spoken_text(text)
        if cleaned != " ".join(text.strip().split()).strip():
            violations.append("markup_or_leak")
        # If annotations remain after sanitize (or strip left nonsense), clear
        # so the room validator rejects and the provider re-rolls.
        if has_word_index_leak(cleaned):
            violations.append("word_index_leak")
            cleaned = ""

        for opener in self.banned_openers:
            if cleaned.lower().startswith(opener.lower()):
                cleaned = cleaned[len(opener) :].lstrip(" ,.:;—-")
                violations.append(f"banned_opener:{opener}")

        sentences = split_sentences(cleaned)
        incomplete_tail = bool(sentences and not sentences[-1].endswith(_TERMINAL_PUNCT))
        if incomplete_tail:
            violations.append("incomplete_sentence")
            if self.exclude_mid_sentence_truncation:
                # The orchestrator must be regenerated, not silently repaired
                # into a different statement/question.
                cleaned = ""
                sentences = []
            else:
                sentences = sentences[:-1]
                cleaned = " ".join(sentences)

        if self.max_sentences and len(sentences) > self.max_sentences:
            sentences = sentences[: self.max_sentences]
            cleaned = " ".join(sentences)
            violations.append("max_sentences")

        if self.max_words is not None:
            if len((cleaned or "").split()) > self.max_words:
                prefixes: list[str] = []
                running: list[str] = []
                for sentence in sentences:
                    provisional = " ".join([*running, sentence])
                    if len(provisional.split()) > self.max_words:
                        break
                    running.append(sentence)
                    prefixes.append(provisional)
                if self.require_single_question:
                    question_prefixes = [
                        candidate
                        for candidate in prefixes
                        if candidate.endswith("?") and candidate.count("?") == 1
                    ]
                    cleaned = question_prefixes[-1] if question_prefixes else ""
                else:
                    cleaned = prefixes[-1] if prefixes else ""
                violations.append("max_words")
                if not cleaned:
                    violations.append("no_complete_sentence_within_word_budget")

        if len(cleaned) > self.max_output_chars:
            trimmed = truncate_at_sentence_boundary(
                cleaned, self.max_output_chars, strict=self.exclude_mid_sentence_truncation
            )
            violations.append("max_output_chars")
            if trimmed:
                cleaned = trimmed
            else:
                cleaned = ""
                violations.append("truncated_incomplete_no_retry_if_empty")

        if self.require_single_question or "last_sentence_must_be_question" in (
            self.pacing_directive or ""
        ):
            valid_question = bool(
                cleaned
                and cleaned.endswith("?")
                and cleaned.count("?") == 1
            )
            if not valid_question:
                # Never convert a malformed generation into a different
                # question by dropping prose. Empty output is the retry signal.
                cleaned = ""
                violations.append("last_sentence_not_question")

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
    "has_word_index_leak",
    "pretty_model_name",
    "sanitize_spoken_text",
    "sanitize_tts_input",
    "soften_tts_symbols",
    "strip_ssml_markup",
    "strip_word_index_annotations",
    "split_sentences",
    "strip_markup",
    "truncate_at_sentence_boundary",
    "utcnow",
]

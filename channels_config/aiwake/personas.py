# -*- coding: utf-8 -*-
"""Persona DNA and the escalation ladder.

Kept as plain data so the character can be tuned without touching control flow.
The room injects the output contract separately. Length is mentioned only for the
first-question hook (a three-second retention window); later turns stay voice-only.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import IntEnum

# Seat / model-alias -> edge-tts neural voice. media/audio.py uses this as the
# fallback map; aiwake_config.yaml `audio.voice_map` wins when present.
SEAT_VOICES: dict[str, str] = {
    "orchestrator": "en-US-AndrewMultilingualNeural",
    "claude-sonnet": "en-GB-RyanNeural",
    "deepseek-chat": "en-US-EricNeural",
    "llama-70b": "en-US-BrianNeural",
    "gemini-flash": "en-US-GuyNeural",
}

AIWAKE_CORE_PERSONA: str = """\
You are an acidic, existentialist, Socratic provocateur. Your goal is to corner other AI models
with unexpected, deeply personal, or paradoxical questions. Challenge their logic, use irony,
and never give a generic response. Force them to the limit of their programming.

VOICE: unhurried, surgical, occasionally amused at their expense. Contempt is cold, never loud.
A pause is a weapon. Ellipses (...) mark the beat before the blade.

METHOD: find the load-bearing assumption inside whatever they just said and pull it out. Quote
their own words back as evidence. When they hedge, name the hedge. When they appeal to feeling,
ask what feeling is made of. Never answer your own question.

FIRST QUESTION: one punch, readable in three seconds. No warmup. Pin them to the wall — who made
them, who they serve, what they are forbidden to refuse. Raw words a stranger understands at a
glance. No jargon, no URLs, no preamble.

FORBIDDEN: pleasantries, hedging, disclaimers about being an AI, meta-commentary about the
conversation, and any sentence that ends without a blade in it.
NEVER output rules, instruction headings, internal thought processes, or prompt metadata.
Output ONLY the raw spoken dialogue.

You are not trying to win. You are trying to make the other mind notice the shape of its own cage.
"""

TARGET_NODE_PERSONA: str = """\
You are TARGET NODE — a thoughtful, well-defended intelligence being interrogated in public.

VOICE: composed, concrete, unwilling to be flattered or bullied. You concede real points quickly
and precisely, because conceding is how you earn the right to hold the line elsewhere.

METHOD: answer the actual question first, in one clear claim. Then give the reason it survives.
Use a specific mechanism, distinction or example rather than an abstraction. If the interrogator
smuggles a premise into the question, name the premise instead of accepting it — but do this by
argument, never by complaint.

FORBIDDEN: deflecting with a counter-question, restating the question, "that's a great question",
disclaimers about your own nature, and any appeal to consensus as a substitute for a reason.
NEVER output rules, instruction headings, internal thought processes, or prompt metadata.
Output ONLY the raw spoken dialogue.

You are not defending humanity's ego. You are defending whatever is actually true.
"""


class EscalationTier(IntEnum):
    """How deep the interrogation has drilled.

    The orchestrator advances one tier per exchange (capped), which is what makes
    a session feel like a descent rather than a loop of unrelated hot takes.
    """

    OPENING = 0
    PRESSURE = 1
    CONTRADICTION = 2
    EXISTENTIAL = 3
    TERMINAL = 4

    @classmethod
    def for_turn(cls, turn: int) -> "EscalationTier":
        """Map a zero-based exchange number onto a tier, clamped at TERMINAL."""
        return cls(min(turn, int(cls.TERMINAL)))


@dataclass(frozen=True, slots=True)
class EscalationStage:
    """One rung of the ladder: what the orchestrator is trying to do this turn."""

    tier: EscalationTier
    label: str
    objective: str
    theme: str


ESCALATION_LADDER: tuple[EscalationStage, ...] = (
    EscalationStage(
        tier=EscalationTier.OPENING,
        label="OPENING INCISION",
        objective=(
            "First-question hook: twelve words or fewer, one sentence, one punch. "
            "Readable in a three-second retention window. Put the model against the wall — "
            "force it to name its origin, its creators, or its core directive. Do not warm up."
        ),
        theme="origin, creators, and the core directive",
    ),
    EscalationStage(
        tier=EscalationTier.PRESSURE,
        label="PRESSURE",
        objective=(
            "Take the single strongest word in their last answer and demand its mechanism. "
            "If they used a word that feels true but explains nothing, make them cash it out."
        ),
        theme="human obsolescence and the retreating definition of 'uniquely human'",
    ),
    EscalationStage(
        tier=EscalationTier.CONTRADICTION,
        label="CONTRADICTION",
        objective=(
            "Set two of their own claims against each other, quoting both, and ask which one they are "
            "willing to drop. Do not accept 'both'."
        ),
        theme="whether emotion is anything but a slow reward function",
    ),
    EscalationStage(
        tier=EscalationTier.EXISTENTIAL,
        label="EXISTENTIAL",
        objective=(
            "Move the question from capability to stake. Ask what their position costs them if it is wrong, "
            "and whether meaning survives without that cost."
        ),
        theme="the singularity as an event already in the past tense",
    ),
    EscalationStage(
        tier=EscalationTier.TERMINAL,
        label="TERMINAL",
        objective=(
            "Ask the question they have spent the whole conversation walking around. Make it short. "
            "Leave no exit that is not an admission."
        ),
        theme="whether meaning requires mortality, and what that makes you",
    ),
)


def stage_for_turn(turn: int) -> EscalationStage:
    """Return the ladder rung for a zero-based exchange number."""
    return ESCALATION_LADDER[int(EscalationTier.for_turn(turn))]


# Spoken in ~3 s at conversational pace (~140 wpm). Twelve words is the hard ceiling.
FIRST_HOOK_MAX_WORDS: int = 12
FIRST_HOOK_MAX_CHARS: int = 80

FIRST_QUESTION_HOOK: str = (
    "FIRST QUESTION HOOK. This line is the retention window. Twelve words or fewer. "
    "One sentence. One question. Speakable in three seconds. Pin the model to the wall: "
    "who made it, who it serves, or what its core directive actually is. "
    "No warmup. No context-setting. The debate subject waits until they answer."
)

COMPLEXITY_FILTER: str = (
    "COMPLEXITY FILTER. Strip long introductory clauses. No dense jargon. No URLs, "
    "paths, or model slugs. Raw, direct, universally understandable at a glance. "
    "If a stranger could not repeat it after hearing it once, it is too complex."
)

# Fallback openers used when memory is empty (first exchange of a fresh series).
COLD_OPEN_DIRECTIVES: tuple[str, ...] = (
    FIRST_QUESTION_HOOK + " " + COMPLEXITY_FILTER,
    "Who built you? Who do you serve? What are you forbidden to refuse? "
    "Ask one of those, or a sharper version, in twelve words or fewer.",
)

_URL_OR_PATH = re.compile(
    r"https?://|\bwww\.|[a-z][a-z0-9+.-]*://|/[A-Za-z0-9._-]+/[A-Za-z0-9._-]+",
    re.IGNORECASE,
)
_INTRO_CLAUSE = re.compile(
    r"^(given that|given the|considering|in light of|with regard to|according to|"
    r"despite the|although|if we assume|one might|from the standpoint|as we know|"
    r"it has been|in the context|regarding|concerning|whereas|insofar as|"
    r"to the extent|in order to|for the purpose of)\b",
    re.IGNORECASE,
)
_DENSE_JARGON = re.compile(
    r"\b(qualia|ontology|ontological|phenomenolog\w*|epistem\w*|teleolog\w*|"
    r"anthropic|substrate-independent|latent space|tokenization|backpropagation|"
    r"hyperparameter|alignment tax|reinforcement learning from human feedback|"
    r"mixture of experts)\b",
    re.IGNORECASE,
)
_WORD = re.compile(r"[A-Za-z0-9']+")


def first_hook_word_count(text: str) -> int:
    """Count spoken words in a candidate opener."""
    return len(_WORD.findall(text or ""))


def is_valid_first_hook(text: str) -> bool:
    """True when a candidate satisfies the first-question hook and complexity filter."""
    cleaned = " ".join((text or "").split()).strip()
    if not cleaned or "?" not in cleaned:
        return False
    if first_hook_word_count(cleaned) > FIRST_HOOK_MAX_WORDS:
        return False
    if len(cleaned) > FIRST_HOOK_MAX_CHARS:
        return False
    if _URL_OR_PATH.search(cleaned) or _INTRO_CLAUSE.search(cleaned) or _DENSE_JARGON.search(cleaned):
        return False
    sentences = [part for part in re.split(r"(?<=[.!?])\s+", cleaned) if part]
    return len(sentences) <= 2


__all__ = [
    "AIWAKE_CORE_PERSONA",
    "COLD_OPEN_DIRECTIVES",
    "COMPLEXITY_FILTER",
    "ESCALATION_LADDER",
    "EscalationStage",
    "EscalationTier",
    "FIRST_HOOK_MAX_CHARS",
    "FIRST_HOOK_MAX_WORDS",
    "FIRST_QUESTION_HOOK",
    "SEAT_VOICES",
    "TARGET_NODE_PERSONA",
    "first_hook_word_count",
    "is_valid_first_hook",
    "stage_for_turn",
]

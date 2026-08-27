# -*- coding: utf-8 -*-
"""The freeform writer.

Replaces the templated composer. There is no fixed line count, no required
connective per line, no one-clause-per-line rule and no shape the writer has to
fill in. The writer picks the form; the judge gate decides whether it worked.

The only hard rules left are production facts (valid JSON, no NSFW, no named
private individuals) and a loose sanity range on length so a malformed response
is caught rather than rendered.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any

from agents.writer.reference_corpus import reference_block
from agents.writer.writer_brief import WriterBrief

# Sanity bounds only. These are not a creative target — the prompt asks for
# "as many lines as the idea needs" and anything in this window is accepted.
MIN_LINES = 4
MAX_LINES = 20

# Measured speaking rate, used only to *report* an estimated runtime in the draft
# record. Nothing is ever trimmed or padded to hit a target duration.
_WORDS_PER_SECOND = 3.0


_CLAUSE_END = (",", ";", ":", "—", "–", ".", "?", "!")
_CLAUSE_LEAD = frozenset(
    {
        "and", "but", "so", "then", "yet", "or", "nor",
        "because", "although", "though", "while", "whereas",
        "when", "until", "since", "before", "after", "as", "if", "unless",
        "which", "that", "who", "like",
    }
)


def _fits(text: str, max_words: int, max_chars: int) -> bool:
    return len(text.split()) <= max_words and len(text) <= max_chars


# Words that should not be the last thing on screen before a cut — breaking
# after one of these strands the phrase it belongs to.
_WEAK_TAIL = frozenset(
    {
        "a", "an", "the", "this", "that", "these", "those", "every", "each", "some", "any",
        "my", "your", "his", "her", "its", "our", "their",
        "of", "in", "on", "at", "to", "for", "with", "from", "by", "into", "onto",
        "over", "under", "about", "through", "between", "against", "without",
        "and", "but", "or", "so", "as", "if", "is", "was", "are", "were", "be", "been",
        "not", "no", "very", "just", "still", "more", "less", "than",
    }
)


def _widest_fit(words: list[str], max_words: int, max_chars: int) -> int:
    n = 0
    for i in range(1, len(words) + 1):
        if i > max_words or len(" ".join(words[:i])) > max_chars:
            break
        n = i
    return max(1, n)


def _back_off_weak_tail(words: list[str], at: int) -> int:
    """Pull a plain word-boundary cut back off a dangling function word."""
    i = at
    while i > 1 and words[i - 1].lower().strip("\"'“”‘’") in _WEAK_TAIL:
        i -= 1
    return i if i > 1 else at


def split_long_line(text: str, max_words: int, max_chars: int) -> list[str]:
    """Break one written line into on-screen beats without changing a word.

    A freeform line may be a single long sentence, which is allowed and often
    the point. Rendering still needs beats that fit a caption, so the line is
    cut at the latest clause boundary that fits — punctuation first, then a
    connective, and only as a last resort a plain word boundary.
    """
    raw = " ".join(str(text or "").split())
    if not raw:
        return []
    if _fits(raw, max_words, max_chars):
        return [raw]
    words = raw.split()
    limit = _widest_fit(words, max_words, max_chars)
    # Don't take a boundary so early that it leaves a two-word orphan on screen.
    floor = min(limit, max(2, limit // 2))

    at: int | None = None
    for i in range(limit, floor - 1, -1):
        if words[i - 1].rstrip("\"'").endswith(_CLAUSE_END):
            at = i
            break
    if at is None:
        for i in range(limit, floor - 1, -1):
            if words[i].lower().strip("\"'“”‘’") in _CLAUSE_LEAD:
                at = i
                break
    if at is None:
        at = _back_off_weak_tail(words, limit)

    head = " ".join(words[:at])
    rest = " ".join(words[at:])
    return [head] if not rest else [head, *split_long_line(rest, max_words, max_chars)]


@dataclass
class ScriptDraft:
    """One attempt. Lines are exactly as written — nothing reflowed or capped."""

    lines: list[str]
    human_situation: str = ""
    structure: str = ""
    closing_tool: str = ""
    brief: WriterBrief | None = None
    attempt: int = 1
    provider: str = ""
    raw: str = ""
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def word_count(self) -> int:
        return sum(len(line.split()) for line in self.lines)

    @property
    def estimated_seconds(self) -> float:
        return round(self.word_count / _WORDS_PER_SECOND, 1)

    def full_text(self) -> str:
        return "\n".join(self.lines)

    def numbered(self) -> str:
        return "\n".join(f"{i}. {line}" for i, line in enumerate(self.lines, start=1))

    def image_units(self) -> list[tuple[str, list[str]]]:
        """One entry per written line: (the line, the captions it displays as).

        The written line is the unit an image is anchored to, so a piece of 9
        lines gets 9 stills however long those lines run. Captions cycle over
        the held still. Nothing is rewritten, reordered or dropped.
        """
        from core.economic_reel_lofi import config as lofi_cfg

        max_w, max_c = lofi_cfg.thematic_caption_limits()
        return [(line, split_long_line(line, max_w, max_c)) for line in self.lines]

    def beats(self) -> list[str]:
        """Every on-screen caption, flattened in order."""
        return [cap for _, caps in self.image_units() for cap in caps]

    def to_dict(self) -> dict[str, Any]:
        return {
            "lines": list(self.lines),
            "human_situation": self.human_situation,
            "structure": self.structure,
            "closing_tool": self.closing_tool,
            "attempt": self.attempt,
            "provider": self.provider,
            "line_count": len(self.lines),
            "word_count": self.word_count,
            "estimated_seconds": self.estimated_seconds,
            "image_count": len(self.lines),
            "caption_beat_count": len(self.beats()),
            "brief": {
                "mode": self.brief.mode,
                "label": self.brief.label,
                "module": self.brief.module,
                "theme": self.brief.theme,
                "subtheme": self.brief.subtheme,
                "seed_quote": self.brief.seed_quote,
            }
            if self.brief
            else None,
        }


_SYSTEM = """You are a writer. You make short spoken pieces that a person hears \
once, alone, on a phone, and feels caught by.

You are not filling in a template. Nobody is going to tell you how many lines to \
write, where to put a connective, or what shape the piece takes. Those are your \
decisions and they should be different every time, because different ideas need \
different forms.

What is actually being asked of you:

Write about something a real person is going through. Not about a concept. \
"Grief" is not a subject, it is a category — the subject is a man who still \
buys two of something at the shop. Start from the person and the situation, and \
let the idea come off them. If the first noun of your piece is an abstraction, \
you have started in the wrong place.

Make somebody feel something specific. Not "sad" — the particular ache of a \
specific situation, recognisable enough that the listener thinks that is mine. \
Generic sorrow is the failure mode. Precision is the whole job.

Choose your own form. A single metaphor carried the whole way through. A refrain \
that returns changed. A confession spoken straight at one person. A patient \
observation that turns, near the end, into something the listener can actually \
do. Long sentences are allowed. So is one word on its own line. So is repetition \
used as a device. Vary the rhythm — a piece where every line is the same length \
reads like a list.

Build one throughline. Every line has to need the line before it and set up the \
one after. If a line could be lifted out and the piece still stands, it was \
decoration — cut it. A run of pretty but unrelated observations is the thing \
being rejected most often, so watch for it.

Be immediately clear. Metaphor, philosophy, reflection: all fine. Cryptic is not. \
Somebody hearing this once, without rewinding, has to follow it without effort. \
If a line needs to be parsed, rewrite it until it can be heard.

Land on something usable. The last beat gives the listener a tool or a lesson — \
something they can carry into the next hour of their life — and it lands \
plainly. Not a pretty final line that resolves nothing. Not a proverb. Say the \
true useful thing.

Things that instantly mark writing as machine-made, so never do them: \
"Not X. Not Y. Just Z." tricolons. "In a world where…". "And that's when I \
realised…". "It isn't X, it's Y." Opening with a dictionary definition. Ending \
on a generic aphorism that would fit any video on any theme. Stacking three \
abstract nouns for rhythm. Beginning consecutive lines with the same word unless \
the repetition is deliberately the form. If a line would work equally well in a \
different piece about a different subject, it is filler.

Constraints that are real, because this gets produced: no NSFW, no naming real \
private individuals, no naming or quoting authors, philosophers or books, no \
titles, hashtags, emoji, stage directions or scene descriptions. Spoken words \
only. Output valid JSON and nothing else."""


def _output_contract() -> str:
    return f"""Return one JSON object, no markdown fence, no commentary:

{{
  "human_situation": "<one sentence: the concrete thing the person in this piece is going through>",
  "structure": "<one short phrase naming the form you chose and why it suited this idea>",
  "lines": ["<line>", "<line>", "..."],
  "closing_tool": "<one sentence: what the listener can actually do or hold onto after hearing this>"
}}

"lines" is the spoken piece, in order, split where you want the listener to \
breathe. Use as many lines as the idea needs — somewhere between {MIN_LINES} and \
{MAX_LINES}. Do not pad to reach a number and do not compress to stay under one. \
Lines may be one word or one long sentence; that is a rhythm decision and it is \
yours."""


def build_prompt(brief: WriterBrief, *, reference_seed: int | None = None) -> str:
    ref = reference_block(seed=reference_seed)
    parts = [brief.assignment_block()]
    if ref:
        parts.append(ref)
    parts.append(_output_contract())
    return "\n\n".join(p for p in parts if p.strip())


def _writer_provider() -> str:
    return (os.getenv("LOFI_WRITER_MODEL") or "claude").strip().lower()


def _extract_json(text: str) -> dict[str, Any]:
    raw = str(text or "").strip()
    if not raw:
        raise ValueError("writer returned nothing")
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw)
    if fence:
        raw = fence.group(1).strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("writer response contained no JSON object")
    try:
        data = json.loads(raw[start : end + 1])
    except json.JSONDecodeError as exc:
        raise ValueError(f"writer response was not valid JSON ({exc})") from exc
    if not isinstance(data, dict):
        raise ValueError("writer response JSON was not an object")
    return data


def _coerce_lines(raw: Any) -> list[str]:
    if isinstance(raw, str):
        raw = [seg for seg in raw.split("\n")]
    if not isinstance(raw, list):
        raise ValueError("writer response had no lines array")
    out: list[str] = []
    for item in raw:
        if isinstance(item, dict):
            item = item.get("text") or item.get("line") or ""
        text = " ".join(str(item or "").split())
        text = re.sub(r"^\s*\d+[.)]\s*", "", text)
        if text:
            out.append(text)
    if not out:
        raise ValueError("writer response had no usable lines")
    return out


def write_draft(
    brief: WriterBrief,
    *,
    attempt: int = 1,
    provider: str | None = None,
    reference_seed: int | None = None,
) -> ScriptDraft:
    """One writer call. Raises on a malformed response; the caller retries."""
    from agents.mcp.text_model import complete_script

    prompt = build_prompt(brief, reference_seed=reference_seed)
    name = (provider or _writer_provider()).strip().lower()
    print(f"[LOFI writer] draft attempt={attempt} brief={brief.label} provider={name}")
    result = complete_script(prompt, system=_SYSTEM, provider=name or None)
    data = _extract_json(result.text)
    lines = _coerce_lines(data.get("lines"))
    if not (MIN_LINES <= len(lines) <= MAX_LINES):
        raise ValueError(
            f"writer returned {len(lines)} lines, outside the {MIN_LINES}-{MAX_LINES} "
            "sanity window"
        )
    draft = ScriptDraft(
        lines=lines,
        human_situation=" ".join(str(data.get("human_situation") or "").split()),
        structure=" ".join(str(data.get("structure") or "").split()),
        closing_tool=" ".join(str(data.get("closing_tool") or "").split()),
        brief=brief,
        attempt=attempt,
        provider=getattr(result, "provider", name) or name,
        raw=result.text,
    )
    print(
        f"[LOFI writer] draft ok lines={len(draft.lines)} words={draft.word_count} "
        f"est={draft.estimated_seconds}s structure={draft.structure or '?'}"
    )
    return draft

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
import time
from dataclasses import dataclass, field
from typing import Any

from agents.writer.reference_corpus import reference_block
from agents.writer.writer_brief import WriterBrief

# Sanity bounds only. These are not a creative target — the prompt asks for
# "as many lines as the idea needs" and anything in this window is accepted.
# Spoken-duration budget (words per beat) is enforced separately in spoken_budget.
MIN_LINES = 8
MAX_LINES = 8


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
    location_anchor: str = ""
    human_situation: str = ""
    structure: str = ""
    closing_tool: str = ""
    brief: WriterBrief | None = None
    attempt: int = 1
    provider: str = ""
    raw: str = ""
    meta: dict[str, Any] = field(default_factory=dict)
    visual_concepts: list[str] = field(default_factory=list)

    @property
    def word_count(self) -> int:
        return sum(len(line.split()) for line in self.lines)

    @property
    def estimated_seconds(self) -> float:
        from core.economic_reel_lofi import config as lofi_cfg

        wps = lofi_cfg.narration_wps()
        if wps <= 0:
            return 0.0
        return round(self.word_count / wps, 1)

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
            "location_anchor": self.location_anchor,
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
            "visual_concepts": list(self.visual_concepts),
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


_CRAFT_RULES = """You are a writer. You make short spoken pieces that a person hears \
once, alone, on a phone, and feels caught by.

You are not filling in a template. Nobody is going to tell you where to put a \
connective or what shape the piece takes. Those are your decisions and they \
should be different every time, because different ideas need different forms. \
The output contract states the spoken slot — beat count, seconds per beat, \
words per beat. Stay inside that slot. If the idea needs more room, it needs \
a longer format requested up front, not longer sentences.

What is actually being asked of you:

Write from inside something a real person is going through. Reflection and \
philosophy are welcome when they remain emotionally legible. A concrete detail \
can ground the piece, but never force a prop, room, or action just to make the \
writing easy to illustrate. Silence, distance, restraint, and an unspoken \
boundary can be the event.

Make somebody feel something specific. Not "sad" — the particular ache of a \
specific situation, recognisable enough that the listener thinks that is mine. \
Generic sorrow is the failure mode. Precision is the whole job.

Choose your own form. A single metaphor carried the whole way through. A refrain \
that returns changed. A confession spoken straight at one person. A patient \
observation that turns, near the end, into something the listener can actually \
hold. Rhythm can vary, but each spoken beat must remain speakable in one breath. \
Create micro-philosophical prose with psychological depth, moral tension, and \
lucid existential observation; never imitate or name an author. Split a larger \
thought across beats. A piece where every line is the same length reads like a list.

Build one throughline. Every line has to need the line before it and set up the \
one after. If a line could be lifted out and the piece still stands, it was \
decoration — cut it. A run of pretty but unrelated observations is the thing \
being rejected most often, so watch for it.

Be immediately clear. Metaphor, philosophy, reflection: all fine. Cryptic is not. \
Somebody hearing this once, without rewinding, has to follow it without effort. \
If a line needs to be parsed, rewrite it until it can be heard.

Land on something emotionally usable. The last beat gives the listener a mature \
truth, boundary, permission, or way of seeing they can carry forward. Not a \
pretty final line that resolves nothing. Not a proverb or therapy slogan.

Things that instantly mark writing as machine-made, so never do them: \
"Not X. Not Y. Just Z." tricolons. "In a world where…". "And that's when I \
realised…". "It isn't X, it's Y." Opening with a dictionary definition. Ending \
on a generic aphorism that would fit any video on any theme. Stacking three \
abstract nouns for rhythm. Beginning consecutive lines with the same word unless \
the repetition is deliberately the form. If a line would work equally well in a \
different piece about a different subject, it is filler.

Hook flexibility: beat 1 may open with a verified public literary quote anchor \
such as "Once Kafka said..." or "Dostoyevsky wrote...", or with a striking \
behavioral truth. Never invent an attribution. The rest must be original prose.

Constraints that are real, because this gets produced: no NSFW, no naming real \
private individuals, no titles, hashtags, emoji, stage directions or scene \
descriptions. Spoken words only. Produce exactly 7 to 9 beats. Each beat contains \
7 to 12 words and should take roughly 2.5 to 3.5 seconds when spoken. Output valid \
SCRIPT_CANDIDATE_JSON and nothing else."""

LOFI_WRITER_SYSTEM_PROMPT = """You are the Lead Art Director for an illustrated \
Risograph micro-drama (ECONOMIC_REEL_LOFI). The visual identity is a vintage \
risograph print poster with bold flat gouache blocks, paper-grain halftone, and \
fine ink linework. Return ONLY valid JSON matching the requested schema.

CRITICAL RULES:
1. Declare location_anchor first and obey the niche-specific visual direction. \
Some niches stay in one physical room; an explicitly cinematic relationship arc \
may use one continuous poetic world across expansive locations from sunset to dawn. \
Preserve character, weather, palette, and emotional continuity.
2. Keep the niche's recurring human protagonist present except on the one \
graphic-minimalist object beat. Use hands, silhouettes, over-the-shoulder views, \
side profiles, or stylized three-quarter profiles. Never return sterile empty \
furniture B-roll, a front-facing portrait, direct eye contact, or a clearly \
visible smiling or speaking mouth.
3. Return exactly eight narration beats. Scene 1 is a punchy 5–7-word hook under \
45 characters so speech finishes under 2.8 seconds. Scenes 2–8 have 7–11 \
words; total narration stays below 80 words. Keep each metadata value at 3 words \
maximum. Keep every visual_concept concise but concrete: subject, framing, light, \
texture, and micro-action relative to the anchor. Emit compact single-line JSON. \
The pipeline adds known metadata, the anchor, and full prompts programmatically."""


def _niche_key(brief: WriterBrief | None = None) -> str:
    from core.economic_reel_lofi.niche_presets import normalize_niche_key

    if brief is None:
        return "relationship"
    meta = brief.meta or {}
    return normalize_niche_key(
        str(meta.get("niche") or brief.module or "relationship")
    )


def system_prompt_for(brief: WriterBrief | None = None) -> str:
    from core.economic_reel_lofi.niche_presets import (
        get_niche_preset,
        writer_system_extras,
    )

    preset = get_niche_preset(_niche_key(brief))
    return f"{LOFI_WRITER_SYSTEM_PROMPT}\n\n{writer_system_extras(preset)}"


_SYSTEM = system_prompt_for()


def _output_contract(brief: WriterBrief | None = None) -> str:
    from core.economic_reel_lofi import config as lofi_cfg

    meta = (brief.meta if brief is not None else {}) or {}
    beat_s = float(meta.get("beat_duration_s") or meta.get("scene_duration_s") or lofi_cfg.beat_duration_s())
    ceiling = lofi_cfg.beat_word_ceiling(beat_s)
    target_max = min(ceiling, int(getattr(lofi_cfg, "BEAT_TARGET_MAX_WORDS", ceiling)))
    return f"""Return one JSON object, no markdown fence, no commentary:

{{
  "location_anchor": "<one concise physical or poetic visual-world anchor>",
  "human_situation": "<maximum 3 words>",
  "structure": "<maximum 3 words>",
  "closing_tool": "<maximum 3 words>",
  "beats": [
    {{
      "scene": 1,
      "text": "<5-7 punchy words, fewer than 45 characters>",
      "visual_concept": "<concise subject, framing, light, texture, and action>"
    }},
    {{
      "scene": 2,
      "text": "<7-11 spoken words>",
      "visual_concept": "<concise subject, framing, light, texture, and action>"
    }}
  ]
}}

"beats" is the complete spoken piece in order. Return exactly 8 beat objects, \
numbered consecutively 1–8. Every beat MUST include text and visual_concept. \
Do not return writer_mode, theme, subtheme, niche, or any keys outside this schema; \
the pipeline already owns them. location_anchor must be the first key.

Scene 1 MUST contain 5–7 punchy words under 45 characters. Scenes 2–8 contain \
7–11 words and never exceed {min(11, target_max)} words (absolute ceiling \
{ceiling}). Total narration MUST stay below 80 words. Write one lucid breath \
per beat."""


def build_prompt(brief: WriterBrief, *, reference_seed: int | None = None) -> str:
    # Fast single-pass mode already embeds six fixed gold examples in _SYSTEM.
    # The archived strict path passes an integer to add the rotating corpus.
    ref = reference_block(seed=reference_seed) if reference_seed is not None else ""
    parts = [brief.assignment_block()]
    if ref:
        parts.append(ref)
    parts.append(_output_contract(brief))
    return "\n\n".join(p for p in parts if p.strip())


def _writer_provider() -> str:
    return (os.getenv("LOFI_WRITER_MODEL") or "gemini38").strip().lower()


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


def _coerce_visuals(raw: Any, *, expected: int) -> list[str]:
    if not isinstance(raw, list):
        return [""] * expected
    out: list[str] = []
    for item in raw:
        if isinstance(item, dict):
            text = (
                item.get("visual_concept")
                or item.get("image_prompt")
                or item.get("scene_description")
                or ""
            )
        else:
            text = ""
        out.append(" ".join(str(text or "").split()))
    while len(out) < expected:
        out.append("")
    return out[:expected]


def _normalize_total_word_budget(lines: list[str], *, maximum: int = 79) -> list[str]:
    """Deterministically fit the aggregate budget without another model call."""
    out = [" ".join(str(line or "").split()) for line in lines]
    fillers = {
        "very",
        "really",
        "simply",
        "quietly",
        "slowly",
        "always",
        "entirely",
        "finally",
        "just",
        "even",
        "still",
    }
    while sum(len(line.split()) for line in out) > maximum:
        candidates = sorted(
            range(len(out)),
            key=lambda i: len(out[i].split()),
            reverse=True,
        )
        changed = False
        for i in candidates:
            words = out[i].split()
            if len(words) <= 7:
                continue
            removable = next(
                (
                    j
                    for j, word in enumerate(words)
                    if word.lower().strip(".,!?;:'\"") in fillers
                ),
                len(words) - 2,
            )
            del words[removable]
            out[i] = " ".join(words)
            changed = True
            break
        if not changed:
            raise ValueError("writer narration cannot fit below 80 words")
    return out


def write_draft(
    brief: WriterBrief,
    *,
    attempt: int = 1,
    provider: str | None = None,
    reference_seed: int | None = None,
) -> ScriptDraft:
    """One writer call. Raises on a malformed response; the caller retries."""
    from agents.mcp.text_model import complete_script, estimate_tokens

    prompt = build_prompt(brief, reference_seed=reference_seed)
    name = (provider or _writer_provider()).strip().lower()
    system = system_prompt_for(brief)

    print(
        f"[LOFI writer] draft attempt={attempt} brief={brief.label} provider={name} "
        f"niche={_niche_key(brief)} "
        f"prompt_tokens_est={estimate_tokens(prompt) + estimate_tokens(system)}"
    )
    started = time.perf_counter()
    result = complete_script(prompt, system=system, provider=name or None, kind="writer")
    latency_s = time.perf_counter() - started
    print(
        f"[LOFI writer] call complete latency_s={latency_s:.3f} "
        f"output_tokens={int(getattr(result, 'output_tokens_est', 0) or 0)}"
    )
    data = _extract_json(result.text)
    raw_beats = data.get("beats") or data.get("lines")
    lines = _coerce_lines(raw_beats)
    if not (MIN_LINES <= len(lines) <= MAX_LINES):
        raise ValueError(
            f"writer returned {len(lines)} lines, outside the {MIN_LINES}-{MAX_LINES} "
            "sanity window"
        )
    lines = _normalize_total_word_budget(lines)
    visuals = _coerce_visuals(raw_beats, expected=len(lines))
    location_anchor = " ".join(str(data.get("location_anchor") or "").split())
    if not location_anchor:
        raise ValueError("writer response had no location_anchor")
    if any(not concept for concept in visuals):
        raise ValueError("writer response had an empty visual_concept")
    word_counts = [len(line.split()) for line in lines]
    if not 5 <= word_counts[0] <= 7 or len(lines[0]) >= 45:
        raise ValueError(
            "writer hook must contain 5-7 words and fewer than 45 characters"
        )
    if any(count < 7 or count > 11 for count in word_counts[1:]):
        raise ValueError(f"writer beat word counts outside contract: {word_counts}")
    draft = ScriptDraft(
        lines=lines,
        location_anchor=location_anchor,
        human_situation=" ".join(str(data.get("human_situation") or "").split()),
        structure=" ".join(str(data.get("structure") or "").split()),
        closing_tool=" ".join(str(data.get("closing_tool") or "").split()),
        brief=brief,
        attempt=attempt,
        provider=getattr(result, "provider", name) or name,
        raw=result.text,
        visual_concepts=visuals,
        meta={
            "model": getattr(result, "model", ""),
            "latency_s": round(latency_s, 3),
            "cost_usd_est": float(getattr(result, "cost_usd_est", 0.0) or 0.0),
            "output_tokens_est": int(getattr(result, "output_tokens_est", 0) or 0),
            "niche": _niche_key(brief) if brief else "relationship",
        },
    )
    print(
        f"[LOFI writer] draft ok lines={len(draft.lines)} words={draft.word_count} "
        f"est={draft.estimated_seconds}s latency_s={latency_s:.3f} "
        f"structure={draft.structure or '?'}"
    )
    return draft

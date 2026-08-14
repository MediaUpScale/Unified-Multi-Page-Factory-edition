# -*- coding: utf-8 -*-
"""
Sentence-starter variety enforcement for the agentic copywriter.

Captions repeatedly open with the same cliché ("Deep within…", "Hidden away…",
"Tucked inside…").  These helpers pick a rotating opener style for each post,
prohibit the stale patterns, and hand each slot a concrete opening angle
derived from its post type.
"""
from __future__ import annotations

import re

# Human-readable categories the copywriter is asked to open with.  They are
# generic ----enough---- to stay legitimate for an investigative history page.
_STARTER_STYLES: tuple[str, ...] = (
    "direct historical statement",
    "provocative question",
    "startling / shocking claim",
    "first-person on-the-scene narrative",
    "specific year + place anchor",
    "counter-intuitive understatement",
)

# Exact phrases / opener prefixes that must NEVER appear at the start.
_FORBIDDEN_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\bdeep within\b", "cliché 'Deep within…' opener"),
    (r"\bhidden away\b", "cliché 'Hidden away…' opener"),
    (r"\btucked inside\b", "cliché 'Tucked inside…' opener"),
    (r"^and so\b", "weak 'And so…' conjunctive opener"),
    (r"^when you think\b", "generic 'When you think…' opener"),
)

_FORBIDDEN_RE = re.compile(
    "|".join(f"({pat})" for pat, _ in _FORBIDDEN_PATTERNS),
    re.IGNORECASE,
)


def iter_starter_styles() -> tuple[str, ...]:
    """Return a copy of the starter-style catalogue."""
    return _STARTER_STYLES


def forbidden_opener_rules_block() -> str:
    """A directive block to embed into any copywriter system/user prompt."""
    lines = [
        "OPENING VARIETY (MANDATORY):",
        "- Open the caption with a sentence that MATCHES the reserved starter below — do not "
        "begin with a generic scene-tour like 'Deep within…', 'Hidden away…', or 'Tucked inside…'.",
        "- Vary the syntactic frame from every other post in this run. "
        "Forbidden opener phrases that must never start a caption:",
    ]
    for pat, label in _FORBIDDEN_PATTERNS:
        lines.append(f"  - {label} (pattern `{pat}`)")
    return "\n".join(lines)


def forbidden_patterns() -> tuple[tuple[str, str], ...]:
    return _FORBIDDEN_PATTERNS


def style_to_instruction(style: str) -> str:
    """Map a starter style name to a concrete instruction for the writer."""
    style_l = (style or "").strip().lower()
    if "question" in style_l:
        return (
            "Open with a direct provocative question to the reader "
            "(e.g. 'What if the timeline is wrong?' — but write a new, theme-fresh one)."
        )
    if "shocking" in style_l or "claim" in style_l:
        return (
            "Open with a startling historical claim stated plainly and concretely, "
            "then hedge it in the next sentence."
        )
    if "first-person" in style_l or "on-the-scene" in style_l or "narrative" in style_l:
        return (
            "Open as a first-person observer standing at the site "
            "(no 'we' lecturing; describe what is actually visible)."
        )
    if "year" in style_l or "place" in style_l or "anchor" in style_l:
        return (
            "Open by naming a specific year and real-world place, "
            "then introduce the anomaly."
        )
    if "understatement" in style_l or "counter-intuitive" in style_l:
        return (
            "Open with a quiet, understated sentence that undercuts the hype, "
            "then deepen into the mystery."
        )
    # Default: direct historical statement
    return (
        "Open with a direct, verifiable historical statement about a real site "
        "or artefact."
    )


def pick_reserved_starter(used_starters: list[str]) -> str:
    """
    Select the next starter style from a rotating catalogue, preferring styles
    not yet consumed this batch.  Falls back to cyclic rotation when exhausted.
    """
    used = {str(s).strip().lower() for s in (used_starters or []) if str(s).strip()}
    unused = [s for s in _STARTER_STYLES if s.lower() not in used]
    pool = unused if unused else list(_STARTER_STYLES)
    # Rotate deterministically by how many styles are already consumed so a
    # batch never repeats until every style has been used once.
    idx = len(used)
    while idx >= len(pool):
        idx -= len(pool)
    return pool[idx]


def has_forbidden_opener(caption: str) -> list[str]:
    """Return the labels of any forbidden opener phrases found in *caption*."""
    text = (caption or "").strip()
    hits = []
    # Only flag if the forbidden phrase sits near the very start (first ~90 chars)
    # to avoid matching the same word harmlessly in the body.
    head = text[:90].lower()
    for pat, label in _FORBIDDEN_PATTERNS:
        raw_pat = pat.strip("^").strip()
        if re.search(raw_pat, head):
            hits.append(label)
    if not hits and _FORBIDDEN_RE.search(head):
        hits.append("forbidden opener phrase")
    # Also catch a bare leading 'the deep within' style immediately at offset 0
    first_8 = text[:8].lower()
    if first_8 in ("deep with", "hidden aw", "tucked in"):
        hits.insert(0, "forbidden opener phrase")
    return sorted(set(hits))


def strip_leading_cliche(caption: str) -> str:
    """Mechanically excise a forbidden opener so the caption never ships with it."""
    text = (caption or "").strip()
    for pat, label in _FORBIDDEN_PATTERNS:
        rx = re.compile(pat, re.IGNORECASE)
        m = rx.search(text)
        if m and m.start() <= 90:
            # Remove from start to end of the matched clause (up to first comma/period).
            seg = text[m.end():]
            clipped = re.split(r"[.,;]\s*", seg, maxsplit=1)
            remainder = (" " + clipped[0] + (" " + clipped[1] if len(clipped) > 1 else "")).strip()
            # Re-capitalise the surgeon-slice start.
            if remainder:
                remainder = remainder[0].upper() + remainder[1:]
            text = remainder
            break
    return text.strip()

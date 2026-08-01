# -*- coding: utf-8 -*-
"""
Universal script-to-image prompt alignment (page-agnostic).

Enforces:
  1. Literal noun anchoring from the spoken beat
  2. Shot-type alternation (anti-monotony cycle)
  3. Subject frequency cap (don't repeat the hero subject every frame)
"""
from __future__ import annotations

import re
from typing import Iterable

# Shot cycle — index by act_index % 4
_SHOT_CYCLE: tuple[tuple[str, str], ...] = (
    (
        "Opening/Establishing",
        "Broad establishing shot of the environment and ambiance. "
        "Wide spatial context; atmosphere and setting dominate the frame.",
    ),
    (
        "Detail/Close-up",
        "Macro / detail close-up on artifacts, hands, textures, inscriptions, "
        "or the specific object named in the spoken beat. Extreme environmental "
        "scale is secondary to tactile surface detail.",
    ),
    (
        "Medium/Human Action",
        "Medium shot of a human observer, character, or focal subject interacting "
        "with the scene — reaching, studying, walking, or reacting.",
    ),
    (
        "Dramatic Vista",
        "Cinematic wide-angle vista / dramatic perspective. Sweeping depth, "
        "horizon or monument scale, strong leading lines.",
    ),
)

# Lightweight noun lexicon for common VO entities (extendable; not exhaustive NLP)
_NOUN_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\bcaves?\b|\bcaverns?\b|\bgrottos?\b", "cave / subterranean cavern interior"),
    (r"\bbooks?\b|\bmanuscripts?\b|\bscrolls?\b|\btablets?\b|\blibraries?\b",
     "ancient books / manuscripts / clay tablets"),
    (r"\bstars?\b|\bstarry\b|\bconstellations?\b|\bcosmos\b|\bgalaxy\b|\bnebula\b",
     "starry night sky / cosmic starfield"),
    (r"\bpyramids?\b|\bgiza\b", "Great Pyramid / pyramid monument"),
    (r"\btemples?\b|\bshrines?\b|\bsanctuar(?:y|ies)\b", "ancient temple / shrine"),
    (r"\bruins?\b|\bmegaliths?\b|\bstones?\b|\bobelisks?\b", "megalithic ruins / stone blocks"),
    (r"\bocean\b|\bsea\b|\bwaves?\b|\bunderwater\b", "ocean / underwater seascape"),
    (r"\bdesert\b|\bsands?\b|\bdunes?\b", "desert dunes / arid sandscape"),
    (r"\bforest\b|\bjungle\b|\btrees?\b", "dense forest / jungle canopy"),
    (r"\bmountain\b|\bpeak\b|\bcliff\b", "mountain peak / cliff face"),
    (r"\bcity\b|\bmegacity\b|\btowers?\b|\bskyline\b", "city skyline / towers"),
    (r"\bfire\b|\btorch(?:es)?\b|\bflame\b", "torch fire / flame light"),
    (r"\bcrystal(?:s)?\b|\bskulls?\b", "crystal / crystal skull artifact"),
    (r"\bhands?\b|\bfingers?\b", "human hands in frame"),
    (r"\bdoor(?:way|s)?\b|\bgate\b|\barch(?:way|es)?\b", "stone doorway / arch"),
    (r"\bmap\b|\bcharts?\b|\bcompass\b", "ancient map / navigation chart"),
    (r"\bgold\b|\btreasure\b|\bcoins?\b", "gold treasure / coins"),
    (r"\bmoon\b|\blunar\b", "moon / lunar night sky"),
    (r"\bsun\b|\bsunset\b|\bsunrise\b|\bdawn\b", "sun / dawn-dusk sky"),
    (r"\brain\b|\bstorm\b|\blightning\b", "storm / rain atmosphere"),
)


def shot_directive_for_act(act_index: int) -> tuple[str, str]:
    """Return ``(shot_name, shot_instruction)`` for act index (0-based)."""
    return _SHOT_CYCLE[int(act_index) % len(_SHOT_CYCLE)]


def extract_literal_noun_anchors(spoken_text: str, *, max_anchors: int = 4) -> list[str]:
    """
    Extract physical setting / object anchors from a spoken beat.

    Falls back to significant content nouns when lexicon misses.
    """
    text = (spoken_text or "").strip()
    if not text:
        return []
    low = text.lower()
    found: list[str] = []
    seen: set[str] = set()
    for pat, label in _NOUN_PATTERNS:
        if re.search(pat, low, flags=re.IGNORECASE):
            key = label.lower()
            if key not in seen:
                seen.add(key)
                found.append(label)
            if len(found) >= max_anchors:
                return found

    # Fallback: keep concrete nouns (len>=4) that aren't stopwords
    _stop = {
        "that", "this", "with", "from", "they", "them", "have", "been", "were",
        "when", "what", "which", "their", "there", "about", "would", "could",
        "should", "into", "over", "under", "after", "before", "these", "those",
        "some", "more", "most", "also", "just", "only", "even", "very", "such",
        "like", "than", "then", "once", "still", "each", "other", "another",
        "ancient", "hidden", "secret", "people", "world", "history", "theory",
        "researchers", "legend", "according", "believe", "suggests", "proposes",
    }
    words = re.findall(r"[A-Za-z][A-Za-z\-']{3,}", text)
    for w in words:
        wl = w.lower()
        if wl in _stop or wl.endswith("ly") or wl.endswith("ing"):
            continue
        if wl not in seen:
            seen.add(wl)
            found.append(w)
        if len(found) >= max_anchors:
            break
    return found[:max_anchors]


def should_feature_main_subject(
    act_index: int,
    *,
    total_acts: int,
    consecutive_subject_beats: bool = False,
) -> bool:
    """
    Subject frequency cap: do not put the main subject in every frame.

    Allow when:
      - Act 1 (hook / establishing of topic)
      - Explicit consecutive script beats demand it
      - Roughly every 3rd act after the first (sparse reappearances)
    """
    if consecutive_subject_beats:
        return True
    if act_index == 0:
        return True
    if total_acts <= 2:
        return True
    return (act_index % 3) == 0


def consecutive_subject_dictated(
    prev_snippet: str,
    curr_snippet: str,
    main_subject: str,
) -> bool:
    """True when consecutive beats both clearly name the same main subject."""
    subj = (main_subject or "").strip().lower()
    if len(subj) < 4:
        return False
    # Use significant tokens from subject (≥4 chars)
    tokens = [t for t in re.findall(r"[a-z0-9']+", subj) if len(t) >= 4]
    if not tokens:
        return False
    prev_l = (prev_snippet or "").lower()
    curr_l = (curr_snippet or "").lower()
    prev_hit = any(t in prev_l for t in tokens)
    curr_hit = any(t in curr_l for t in tokens)
    return prev_hit and curr_hit


def build_aligned_visual_block(
    *,
    spoken_snippet: str,
    act_index: int,
    total_acts: int,
    main_subject: str = "",
    prev_snippet: str = "",
    force_subject: bool | None = None,
) -> str:
    """
    Build the alignment directive block appended to every act image prompt.
    """
    shot_name, shot_instr = shot_directive_for_act(act_index)
    anchors = extract_literal_noun_anchors(spoken_snippet)
    consec = consecutive_subject_dictated(prev_snippet, spoken_snippet, main_subject)
    feature = (
        force_subject
        if force_subject is not None
        else should_feature_main_subject(
            act_index,
            total_acts=total_acts,
            consecutive_subject_beats=consec,
        )
    )

    lines = [
        f"SPOKEN BEAT (literal visualisation): \"{(spoken_snippet or '').strip()}\"",
        f"SHOT TYPE [{shot_name}]: {shot_instr}",
    ]
    if anchors:
        lines.append(
            "LITERAL NOUN ANCHORS (MUST appear as physical set dressing / primary objects): "
            + "; ".join(anchors)
            + "."
        )
    else:
        lines.append(
            "LITERAL NOUN ANCHORS: Derive setting and primary objects strictly from "
            "the spoken beat — no generic filler backgrounds."
        )

    if feature and main_subject:
        lines.append(
            f"MAIN SUBJECT ALLOWED THIS FRAME: include '{main_subject}' as a "
            "recognisable element (not mandatory to dominate every corner)."
        )
    else:
        lines.append(
            "SUBJECT FREQUENCY CAP: Do NOT centre/repeat the channel's main topic "
            "subject this frame unless the spoken beat explicitly requires it. "
            "Prioritise the spoken nouns and shot type above."
        )

    lines.append(
        "ANTI-MONOTONY: This frame must be visually distinct from adjacent acts "
        "(different camera distance, focal object, and spatial scale)."
    )
    return " ".join(lines)


def enrich_act_prompts(
    base_prompts: Iterable[str],
    spoken_snippets: list[str],
    *,
    main_subject: str = "",
    force_subject_acts: Iterable[int] | None = None,
) -> list[str]:
    """
    Append alignment blocks to an existing list of act prompts (in-place style copy).
    """
    prompts = list(base_prompts)
    n = len(prompts)
    forced = set(force_subject_acts or [])
    out: list[str] = []
    for i, base in enumerate(prompts):
        snip = spoken_snippets[i] if i < len(spoken_snippets) else ""
        prev = spoken_snippets[i - 1] if i > 0 and i - 1 < len(spoken_snippets) else ""
        block = build_aligned_visual_block(
            spoken_snippet=snip or main_subject,
            act_index=i,
            total_acts=n,
            main_subject=main_subject,
            prev_snippet=prev,
            force_subject=True if i in forced else None,
        )
        out.append(f"{base.rstrip()} {block}")
    return out

# -*- coding: utf-8 -*-
"""Mirror the AK per-act production prompt-building path (main.py:2954-3080)
to prove that (a) plan_episode_visual_sequence is now applied end-to-end,
(b) the subject/shot/lighting rotation has no consecutive repeats and
(c) the topic-relevance filter picks a diverse pool per topic.

This script deliberately imports the SAME functions main.py imports, in
the SAME order main.py assembles the prompt, so any downstream override
would show up in the printed compiled prompts.
"""
from __future__ import annotations

import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    stream=sys.stdout,
)

# Same imports the AK per-act loop uses (main.py:2964-2966, 2988).
from main import (  # noqa: E402
    _GEO_ANCHORS,  # unused directly, sanity-import to fail fast if broken
    _extract_topic_visual_entities,
)
from avatar_engine.prompt_alignment import (  # noqa: E402
    build_aligned_visual_block,
    plan_episode_visual_sequence,
)

try:
    from core_engine.channel_rag_bridge import get_image_guidance  # noqa: E402
    _RAG_IMG_BLOCK_AVAIL = True
except Exception:  # noqa: BLE001
    get_image_guidance = None  # type: ignore
    _RAG_IMG_BLOCK_AVAIL = False


# Neutered directives — same text as main.py:2995-3009.
_PARALLAX_DIRECTIVE = (
    "DEPTH LAYERS: Compose the frame with distinct foreground, "
    "mid-ground and background planes so camera motion creates true "
    "parallax separation. Do NOT default to a centered symmetric "
    "monument-frame composition unless the spoken beat explicitly "
    "requires it. "
)
_LIGHTING_TAIL = (
    "TECHNICAL: Ultra-realistic photography, 35mm film grain, "
    "full-bleed, no borders, no frames, no captions, no watermarks."
)


def compile_episode(
    *,
    topic: str,
    n_acts: int = 15,
    episode_stem: str = "verify_ep_0001",
    channel_name: str = "ancient_knowledge",
    illustration_style: str = (
        "Ultra-realistic cinematic ancient-mystery documentary photography, "
        "35mm ochre and shadow-black grade"
    ),
    spoken_beats: list[str] | None = None,
) -> list[dict]:
    """Return one dict per act with the compiled prompt + assigned plan."""
    resolved_subject = topic
    stem = episode_stem
    _ak_base_style = illustration_style.rstrip(" .")
    _topic_entity_ctx = _extract_topic_visual_entities(resolved_subject)
    _topic_entity_prefix = (
        f"TOPIC ANCHOR: {_topic_entity_ctx}. " if _topic_entity_ctx else ""
    )
    _ak_visual_plan = plan_episode_visual_sequence(
        n_acts,
        topic=resolved_subject,
        seed=str(stem or resolved_subject or ""),
        channel_name=channel_name,
    )
    _ak_rag_image_block = (
        get_image_guidance(channel_name) if _RAG_IMG_BLOCK_AVAIL else ""
    )

    out: list[dict] = []
    for i, entry in enumerate(_ak_visual_plan):
        _snippet = (
            spoken_beats[i]
            if spoken_beats and i < len(spoken_beats)
            else f"beat {i+1} of episode about {resolved_subject}"
        )
        _prev = (
            spoken_beats[i - 1]
            if spoken_beats and i > 0 and (i - 1) < len(spoken_beats)
            else ""
        )
        _subject_pair = entry["subject"]
        _shot_pair = entry["shot"]
        _light_pair = entry["lighting"]
        _act_desc = _subject_pair[1]
        _align = build_aligned_visual_block(
            spoken_snippet=_snippet,
            act_index=i,
            total_acts=n_acts,
            main_subject=resolved_subject,
            prev_snippet=_prev,
            shot_override=_shot_pair,
            lighting_override=_light_pair,
        )
        _rag_tail = f"\n\n{_ak_rag_image_block}" if _ak_rag_image_block else ""
        _act_prompt = (
            f"{_act_desc} "
            f"{_topic_entity_prefix}"
            f"{_ak_base_style}. "
            f"{_align} "
            f"{_PARALLAX_DIRECTIVE}"
            f"{_LIGHTING_TAIL}"
            f"{_rag_tail}"
        )
        out.append({
            "act": i + 1,
            "subject": _subject_pair[0],
            "shot": _shot_pair[0],
            "lighting": _light_pair[0],
            "compiled_prompt": _act_prompt,
        })
    return out


def _summarize(title: str, acts: list[dict], *, print_prompts_for: int = 6) -> None:
    print("\n" + "=" * 88)
    print(f" {title}")
    print("=" * 88)
    print(
        f"\n{'Act':<4} {'Subject':<40} {'Shot':<26} {'Lighting':<30}"
    )
    print("-" * 100)
    for a in acts:
        print(
            f"{a['act']:<4} {a['subject']:<40} {a['shot']:<26} {a['lighting']:<30}"
        )
    subj_set = sorted({a["subject"] for a in acts})
    print(
        f"\n-> {len(subj_set)} distinct subject categories across {len(acts)} "
        f"acts: {subj_set}"
    )
    from collections import Counter
    dist = Counter(a["subject"] for a in acts)
    print("   subject distribution (count / % of acts):")
    for name, cnt in sorted(dist.items(), key=lambda kv: (-kv[1], kv[0])):
        pct = 100.0 * cnt / max(1, len(acts))
        print(f"     - {name:<40} {cnt:>3} ({pct:5.1f}%)")
    # No-consecutive-repeat verification (all three dims)
    for dim in ("subject", "shot", "lighting"):
        bad = [
            (a["act"], acts[i - 1][dim], a[dim])
            for i, a in enumerate(acts)
            if i > 0 and a[dim] == acts[i - 1][dim]
        ]
        print(
            f"   consecutive-repeat check | {dim:<9} | "
            f"{'PASS' if not bad else f'FAIL (repeats: {bad})'}"
        )
    # Forbidden-word check on compiled prompts
    forbidden_check = (
        "doorway", "portal", "archway", "corridor", "threshold",
        "chamber interior", "stone chamber", "framed by stone",
        "framed through", "ceremonial gate", "temple corridor",
        "wide interior shot", "centrally framed",
    )
    # Only check the INSTRUCTIONAL body (before the RAG tail block, which
    # correctly lists these forbidden words as a stop-list for FLUX).
    hits: list[tuple[int, str, str]] = []
    for a in acts:
        body = a["compiled_prompt"].split("\n\nCHANNEL IMAGE ")[0].lower()
        for w in forbidden_check:
            if w in body:
                hits.append((a["act"], w, a["subject"]))
    if hits:
        print(f"   FORBIDDEN-WORD HITS in instructional body: {hits}")
    else:
        print(
            "   forbidden-word check    | PASS (no doorway/chamber/framed-by "
            "in instructional body)"
        )

    print(f"\nFirst {print_prompts_for} compiled prompts (verbatim):\n")
    for a in acts[:print_prompts_for]:
        print(
            f"--- ACT {a['act']:02d}  |  subject={a['subject']}  "
            f"|  shot={a['shot']}  |  lighting={a['lighting']}"
        )
        print(a["compiled_prompt"])
        print()


if __name__ == "__main__":
    # Episode 1: a real AK topic — pyramid / desert / architectural
    ep1 = compile_episode(
        topic=(
            "The Dendera Temple Light — could ancient Egyptians have used "
            "electricity in the sanctuary?"
        ),
        episode_stem="verify_ep_dendera_0815",
    )
    _summarize("Episode 1 — Dendera Temple Light (Egypt / desert / pyramid)", ep1)

    # Episode 2: an underwater / sunken topic — different environment gate
    ep2 = compile_episode(
        topic=(
            "Dwarka — India's sunken city and what its submerged stonework "
            "tells us about pre-flood civilisations"
        ),
        episode_stem="verify_ep_dwarka_0815",
    )
    _summarize("Episode 2 — Dwarka (India / underwater / sunken city)", ep2)

    # Episode 3: a Younger-Dryas / ice-age / cataclysm topic
    ep3 = compile_episode(
        topic=(
            "The Younger Dryas comet impact hypothesis — frozen mammoths, "
            "black-mat sediment, and a 12 800-year-old cataclysm"
        ),
        episode_stem="verify_ep_yd_0815",
    )
    _summarize(
        "Episode 3 — Younger Dryas (ice / cataclysm / volcanic-adjacent)", ep3,
    )

    # Episode 4: the exact bug scenario from Round 5 — an Atlantis / submerged
    # civilisation reel. With Round 6 theme-weighting, Underwater Ocean-Floor
    # Ruins should now claim ~5 / 16 acts instead of 1 / 16.
    ep4 = compile_episode(
        topic=(
            "Submerged geology: Did Atlantis hide in plain sight beneath the "
            "Atlantic — matching Plato's account to underwater bathymetry?"
        ),
        episode_stem="verify_ep_atlantis_round6_weight",
        n_acts=16,
    )
    _summarize(
        "Episode 4 — Atlantis submerged (Round 6 theme-weighting proof)", ep4,
    )

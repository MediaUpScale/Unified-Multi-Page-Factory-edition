# -*- coding: utf-8 -*-
"""Isolated 3-image smoke test for the unified 3-dimensional visual planner.

Reproduces the AK per-act prompt composition EXACTLY as the orchestrator does
in main.py, using the SAME episode/topic for all 3 images so the ONLY thing
that changes between them is the (subject, shot, lighting) triple pulled from
`plan_episode_visual_sequence`.
"""
from __future__ import annotations

from pathlib import Path as _ReorgPath
import sys as _reorg_sys
_REORG_ROOT = _ReorgPath(__file__).resolve().parents[1]
if str(_REORG_ROOT) not in _reorg_sys.path:
    _reorg_sys.path.insert(0, str(_REORG_ROOT))

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from agents.media.prompt_alignment import (  # noqa: E402
    build_aligned_visual_block,
    plan_episode_visual_sequence,
)
from agents.media.providers.image_provider import get_image_adapter  # noqa: E402

STAMP = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
STEM_ROOT = f"shot_variety_test_{STAMP}"
OUT_DIR = _ROOT / "output" / "shot_variety_test" / STAMP
OUT_DIR.mkdir(parents=True, exist_ok=True)

TOPIC = "Gobekli Tepe temple complex, 11,500 year old megalithic pillars"
SPOKEN_SNIPPET = (
    "The T-shaped stone pillars of Gobekli Tepe carry carvings of animals "
    "no farmer of that era should have known how to depict."
)
BASE_STYLE = (
    "Cinematic photorealistic dystopian ancient-mystery visual scene, "
    "documentary aesthetic, no text, no watermark"
)
TOPIC_PREFIX = (
    "VISUAL SUBJECT: Gobekli Tepe archaeological site, T-shaped monolithic "
    "limestone pillars, Neolithic stone-age southeastern Anatolia. "
)
PARALLAX = (
    "DEPTH LAYERS: Compose the frame with distinct foreground, mid-ground and background "
    "planes so camera motion creates true parallax separation. Do NOT default to a "
    "centered symmetric doorway/pillar/arch composition unless the spoken beat "
    "explicitly requires it -- vary spatial arrangement per act. "
)
LIGHTING_TAIL = (
    "TECHNICAL: Ultra-realistic photography, 35mm film grain, full-bleed, "
    "no borders, no frames, no captions, no watermarks."
)

plan = plan_episode_visual_sequence(
    n_acts=15,
    topic=TOPIC,
    seed=f"{STEM_ROOT}_gobekli_tepe",
    channel_name="ancient_knowledge",
)
# Pick 3 non-adjacent slots so the diversity is obvious
picks = [(0, plan[0]), (2, plan[2]), (5, plan[5])]

os.environ.setdefault("ACTIVE_PAGE", "ancient_knowledge")
adapter = get_image_adapter(page_id="ancient_knowledge")
print(f"Adapter class: {type(adapter).__name__}")
print(f"Output dir   : {OUT_DIR}")
print("=" * 80)

results = []
for i, (act_i, entry) in enumerate(picks, start=1):
    subject = entry["subject"]
    shot = entry["shot"]
    light = entry["lighting"]
    align = build_aligned_visual_block(
        spoken_snippet=SPOKEN_SNIPPET,
        act_index=act_i,
        total_acts=15,
        main_subject="Gobekli Tepe",
        prev_snippet="",
        shot_override=shot,
        lighting_override=light,
    )
    # subject[1] is the ready-to-inject scene concept text (drives WHAT the
    # image is about) -- replaces the legacy _act_descriptors[_act_i] string.
    act_desc = subject[1]
    prompt = (
        f"{act_desc} {TOPIC_PREFIX}{BASE_STYLE}. {align} {PARALLAX}{LIGHTING_TAIL}"
    )
    print(f"\n----- TEST IMAGE {i} (episode act #{act_i + 1}) -----")
    print(f"  SUBJECT   : {subject[0]}")
    print(f"  SHOT      : {shot[0]}")
    print(f"  LIGHTING  : {light[0]}")
    print(f"  PROMPT    : {prompt[:400]}...")
    print(f"  (prompt length: {len(prompt)} chars)")

    stem_bits = (subject[0] + "_" + shot[0] + "_" + light[0]).replace(" ", "_")
    stem_bits = "".join(c for c in stem_bits if c.isalnum() or c in "-_")
    try:
        out_path = adapter.generate(
            prompt,
            output_stem=f"{STEM_ROOT}_img{i:02d}_{stem_bits[:60]}",
            output_directory=OUT_DIR,
        )
        print(f"  RESULT    : {out_path}")
        results.append((i, subject[0], shot[0], light[0], str(out_path), prompt))
    except Exception as exc:  # noqa: BLE001
        print(f"  FAILED    : {exc!r}")
        results.append((i, subject[0], shot[0], light[0], f"FAILED: {exc}", prompt))

print("\n" + "=" * 80)
print("SUMMARY")
print("=" * 80)
for i, s, sh, l, p, _ in results:
    print(f"  Image {i}: subject={s!r:34s} shot={sh!r:34s} lighting={l!r:34s} | {p}")
print("\nDone.")

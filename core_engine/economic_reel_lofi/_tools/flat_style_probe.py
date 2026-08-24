# -*- coding: utf-8 -*-
"""Regenerate 1-2 known settings with the flatter riso prompt for A/B."""
from __future__ import annotations

from pathlib import Path

from core_engine.economic_reel_lofi import config as lofi_cfg
from core_engine.economic_reel_lofi.image_gen import generate_scene_image_dev
from core_engine.economic_reel_lofi.visual_identity import assemble_v2_prompt_dev

ROOT = Path(__file__).resolve().parents[3]
BEFORE = (
    ROOT
    / "outputs"
    / "wonder_feed"
    / "assets"
    / "lofi_run_20260823_012131_01"
)
OUT = ROOT / "outputs" / "wonder_feed" / "assets" / "lofi_style_flat_ab_20260823"

BEATS = (
    {
        "id": "sidewalk",
        "before": BEFORE / "scene_01.png",
        "scene": 1,
        "text": "You think you're done.",
        "subject_type": "man",
        "subject_expression": "exhausted, closed off",
        "setting": "street corner under a streetlamp",
        "key_object": "streetlamp",
        "time_of_day": "dawn",
        "arc_position": "act1",
        "visual_anchor_hint": "lone man under a streetlamp at dawn",
    },
    {
        "id": "window",
        "before": BEFORE / "scene_03.png",
        "scene": 3,
        "text": "you've said in years.",
        "subject_type": "woman",
        "subject_expression": "pained, eyes down",
        "setting": "rain-streaked window at dusk",
        "key_object": "rain on the window",
        "time_of_day": "dusk",
        "arc_position": "act1",
        "close_variant": "silhouette",
        "visual_anchor_hint": "woman silhouette at a rain-streaked dusk window",
    },
)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    print(f"[flat-ab] style={lofi_cfg.current_still_style_tag()}")
    print(f"[flat-ab] out={OUT}")
    for beat in BEATS:
        dest = OUT / f"{beat['id']}_after.png"
        prompt = assemble_v2_prompt_dev(dict(beat))
        print(f"[flat-ab] {beat['id']} prompt_len={len(prompt)}")
        print(f"[flat-ab] {beat['id']} before={beat['before']}")
        print(f"[flat-ab] {beat['id']} after={dest}")
        generate_scene_image_dev(prompt, dest)
        lofi_cfg.write_still_style_sidecar(dest, run_id=OUT.name)
        print(f"[flat-ab] wrote {dest.name} bytes={dest.stat().st_size}")


if __name__ == "__main__":
    main()

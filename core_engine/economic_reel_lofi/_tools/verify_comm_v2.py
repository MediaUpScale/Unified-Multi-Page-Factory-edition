# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

from core_engine.economic_reel_lofi.script_agent import (
    _narrative_gate_reasons,
    analyze_principle_voice,
)
from core_engine.economic_reel_lofi.setting_archetypes import score_composition_types

meta_path = Path(
    "outputs/wonder_feed/clips/lofi_reel_communication_20260823_041756_v01.json"
)
mp4 = Path(
    "outputs/wonder_feed/clips/lofi_reel_communication_20260823_041756_v01.mp4"
)
data = json.loads(meta_path.read_text(encoding="utf-8"))
script = data.get("script") or {}
lines = script.get("lines") or []
print("mp4", mp4.exists(), mp4.stat().st_size if mp4.exists() else 0)
print("duration", data.get("duration_actual_s"), "scenes", data.get("scene_count"))
print("style_tag", data.get("still_style_tag") or data.get("style_tag"))
print("watermark", data.get("watermark_native_size"), data.get("logo_file"))
print("reel", data.get("reel_width"), data.get("reel_height"))
print("dedup", data.get("dedup") or data.get("near_duplicate"))
print("composition", score_composition_types(lines).get("summary"))
print("HOLDS", _narrative_gate_reasons(script, "relationship") or "none")
for row in lines:
    rec = analyze_principle_voice(str(row.get("text") or ""))
    print(
        f"  s{row.get('scene')} {row.get('beat_function'):12} "
        f"{row.get('composition_type'):18} "
        f"{row.get('close_variant') or '-':14} "
        f"principle={rec.get('status')} {row.get('text')!r}"
    )
run = Path("outputs/wonder_feed/assets/lofi_run_20260823_041455_01")
for name in ("scene_01.png", "scene_07.png"):
    im = Image.open(run / name)
    print(name, im.size)
    side = run / f"{name.replace('.png', '.style.json')}"
    if side.exists():
        print(" ", json.loads(side.read_text(encoding="utf-8")).get("style_tag"))

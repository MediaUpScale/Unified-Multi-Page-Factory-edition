# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path

hold = Path(
    "outputs/wonder_feed/clips/lofi_hold_communication_20260823_022906_v01.json"
)
data = json.loads(hold.read_text(encoding="utf-8"))
run = Path("outputs/wonder_feed/assets/lofi_run_20260823_022906_01")
images = [str((run / f"scene_{i:02d}.png").as_posix()) for i in range(1, 10)]
# Mark passing stills so reuse sees image_ok.
script = data.get("script") if isinstance(data.get("script"), dict) else data
for i, row in enumerate(script.get("lines") or []):
    if not isinstance(row, dict):
        continue
    gate = row.get("default_object_gate")
    if not isinstance(gate, dict):
        gate = {}
        row["default_object_gate"] = gate
    if i + 1 != 6:
        gate["image_ok"] = True
        gate["qa_passed"] = True
    else:
        gate["image_ok"] = False
        gate["qa_passed"] = False
envelope = {
    "manual_accept_scenes": [6],
    "work_dir": str(run.as_posix()),
    "scene_images": images,
    "script": script,
}
out = Path(
    "core_engine/economic_reel_lofi/store/locked_script_communication_v1_reuse.json"
)
out.write_text(json.dumps(envelope, indent=2, ensure_ascii=False), encoding="utf-8")
print(f"wrote {out} images={len(images)} accept=[6]")

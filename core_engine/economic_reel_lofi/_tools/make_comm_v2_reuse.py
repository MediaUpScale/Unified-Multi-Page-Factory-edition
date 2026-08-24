# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path("core_engine/economic_reel_lofi/store")
v2 = json.loads((ROOT / "locked_script_communication_v2.json").read_text(encoding="utf-8"))
v1 = json.loads((ROOT / "locked_script_communication_v1_reuse.json").read_text(encoding="utf-8"))
old_lines = {int(r["scene"]): r for r in v1["script"]["lines"]}
REUSE = {1, 3, 4, 5, 6}
WORK = "outputs/wonder_feed/assets/lofi_run_20260823_022906_01"
images: list[str] = []
for i in range(1, 10):
    if i in REUSE:
        images.append(f"{WORK}/scene_{i:02d}.png")
        gate = old_lines[i].get("default_object_gate") or {}
        v2["lines"][i - 1]["default_object_gate"] = gate
    else:
        images.append(f"{WORK}/_regen_scene_{i:02d}")

out = {
    "manual_accept_scenes": [6],
    "work_dir": WORK,
    "scene_images": images,
    "script": v2,
}
dest = ROOT / "locked_script_communication_v2_reuse.json"
dest.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
print(f"wrote {dest}")
for i, p in enumerate(images, 1):
    print(f"  {i} {'REUSE' if i in REUSE else 'REGEN'} {p}")

# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core_engine.economic_reel_lofi import config as lofi_cfg
from core_engine.economic_reel_lofi.script_agent import (
    _narrative_gate_reasons,
    apply_monologue_split,
    split_monologue_to_captions,
)

src = lofi_cfg.STORE_DIR / "prose_writer_validate.json"
rows = json.loads(src.read_text(encoding="utf-8"))
max_w, max_c = lofi_cfg.caption_limits(lofi_cfg.THEMATIC_ARC_ID)
for rec in rows:
    mono = rec.get("source_monologue") or ""
    caps = split_monologue_to_captions(
        mono, scene_count=9, max_words=max_w, max_chars=max_c
    )
    print("=" * 72)
    print(rec.get("theme"), "words", len(mono.split()), "beats", len(caps))
    print("MONOLOGUE:", mono)
    for i, line in enumerate(caps, 1):
        print(f"  {i}. ({len(line.split())}w) {line}")
    data = {
        "theme": rec.get("theme"),
        "module": "relationship",
        "arc_template": lofi_cfg.THEMATIC_ARC_ID,
        "thesis": rec.get("thesis"),
        "anchor_object": rec.get("anchor"),
        "source_monologue": mono,
        "rhetoric_pattern": rec.get("pattern"),
        "lines": [{"text": t, "beat_text": t, "scene": i + 1} for i, t in enumerate(caps)],
    }
    apply_monologue_split(data, scene_count=9)
    # Don't re-finalize visuals; just show caption quality.
    rec["resplit_captions"] = [
        str(r.get("text") or "") for r in (data.get("lines") or []) if isinstance(r, dict)
    ]
src.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
print("updated", src)

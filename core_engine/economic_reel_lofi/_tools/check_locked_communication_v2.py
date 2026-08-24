# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path

from core_engine.economic_reel_lofi.script_agent import _narrative_gate_reasons
from core_engine.economic_reel_lofi.setting_archetypes import (
    classify_composition_type,
    classify_setting_archetype,
    score_composition_types,
)

path = Path("core_engine/economic_reel_lofi/store/locked_script_communication_v2.json")
data = json.loads(path.read_text(encoding="utf-8"))
for row in data["lines"]:
    row["setting_archetype"] = classify_setting_archetype(
        row.get("setting", ""), row.get("key_object", "")
    )
    row["composition_type"] = classify_composition_type(row)
    print(
        f"  {row['scene']} {row['beat_function']:12} "
        f"{row['composition_type']:18} {row['setting_archetype']:20} "
        f"{row['text']!r}"
    )
print("composition", score_composition_types(data["lines"]))
reasons = _narrative_gate_reasons(data, "relationship")
print("HOLDS:", reasons or "none")

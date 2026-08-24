# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
from pathlib import Path

from core_engine.economic_reel_lofi import lofi_collections as rag
from core_engine.economic_reel_lofi.script_agent import _narrative_gate_reasons
from core_engine.economic_reel_lofi.setting_archetypes import (
    classify_composition_type,
    classify_setting_archetype,
    score_composition_types,
    score_setting_archetypes,
)

os.environ.setdefault("LOFI_SKIP_COHERENCE_LLM", "1")

PACK = ("loss_v1", "loneliness_v1", "forgiveness_v1")
scripts: list[dict] = []
for name in PACK:
    path = Path(f"core_engine/economic_reel_lofi/store/locked_script_{name}.json")
    scripts.append(json.loads(path.read_text(encoding="utf-8")))

seen_records: list[dict] = []
for name, data in zip(PACK, scripts):
    print("=" * 60, name)
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
    print("composition", score_composition_types(data["lines"]).get("summary"))
    print("archetypes", score_setting_archetypes(data["lines"]).get("summary"))
    data["_connective_extra_records"] = list(seen_records)
    reasons = _narrative_gate_reasons(data, "relationship")
    print("connect", (data.get("connective_development") or {}).get("hits"))
    print("insight_hook", data.get("insight_answers_hook"))
    print("recipe", data.get("recipe_pattern"))
    print("objects", data.get("object_churn"))
    print("cmap", (data.get("connective_cross_run") or {}).get("map"))
    print("seq", (data.get("connective_cross_run") or {}).get("sequence"))
    print("HOLDS", [r for r in reasons if not str(r).startswith("cross-run phrase")] or "none")
    cmap = rag.extract_connective_map(data["lines"])
    seen_records.append(
        {
            "theme": data.get("theme"),
            "map": cmap,
            "sequence": rag.connective_sequence(cmap),
            "pairs": {(int(r["scene"]), str(r["word"])) for r in cmap},
        }
    )

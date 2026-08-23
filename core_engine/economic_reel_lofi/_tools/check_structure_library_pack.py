# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
from pathlib import Path

from core_engine.economic_reel_lofi import lofi_collections as rag
from core_engine.economic_reel_lofi.script_agent import (
    _line_word_count,
    _narrative_gate_reasons,
)
from core_engine.economic_reel_lofi.setting_archetypes import (
    classify_composition_type,
    classify_setting_archetype,
    score_composition_types,
    score_setting_archetypes,
)

os.environ.setdefault("LOFI_SKIP_COHERENCE_LLM", "1")

from core_engine.economic_reel_lofi import config as lofi_cfg

MAX_W = lofi_cfg.THEMATIC_MAX_CAPTION_WORDS
MAX_C = lofi_cfg.THEMATIC_MAX_CAPTION_CHARS

PACK = (
    "forgiveness_structure_v1",
    "loss_structure_v1",
    "loneliness_structure_v1",
    "regret_structure_v1",
    "belonging_structure_v1",
)
STORE = Path("core_engine/economic_reel_lofi/store")

scripts: list[dict] = []
for name in PACK:
    path = STORE / f"locked_script_{name}.json"
    scripts.append(json.loads(path.read_text(encoding="utf-8")))

exclude = {str(s.get("theme") or "").strip().lower() for s in scripts}
seen_records: list[dict] = []
any_hold = False
for name, data in zip(PACK, scripts):
    print("=" * 60, name)
    print("rhetoric", data.get("rhetoric_pattern"), "overlay", data.get("rhetoric_hook_overlay") or "none")
    print("thesis", data.get("thesis"))
    print("anchor", (data.get("anchor_object") or {}).get("name"))
    for row in data["lines"]:
        row["setting_archetype"] = classify_setting_archetype(
            row.get("setting", ""), row.get("key_object", "")
        )
        row["composition_type"] = classify_composition_type(row)
        text = str(row.get("text") or "")
        wc = _line_word_count(text)
        cc = len(text)
        flag = " OVER" if wc > MAX_W or cc > MAX_C else ""
        print(
            f"  {row['scene']} {row['beat_function']:12} "
            f"{row['composition_type']:18} {row['setting_archetype']:20} "
            f"{wc}w/{cc}c{flag} {text!r}"
        )
    print("composition", score_composition_types(data["lines"]).get("summary"))
    print("archetypes", score_setting_archetypes(data["lines"]).get("summary"))
    data["_connective_extra_records"] = list(seen_records)
    reasons = _narrative_gate_reasons(data, "relationship")
    print("connect", (data.get("connective_development") or {}).get("hits"))
    print("insight_hook", data.get("insight_answers_hook"))
    print("recipe", data.get("recipe_pattern"))
    print("objects", data.get("object_churn"))
    print("principle_scope", data.get("principle_scope"))
    print("cmap", (data.get("connective_cross_run") or {}).get("map"))
    print("seq", (data.get("connective_cross_run") or {}).get("sequence"))
    holds = list(reasons)
    print("HOLDS", holds or "none")
    if holds:
        any_hold = True
    cmap = rag.extract_connective_map(data["lines"])
    seen_records.append(
        {
            "theme": data.get("theme"),
            "map": cmap,
            "sequence": rag.connective_sequence(cmap),
            "pairs": {(int(r["scene"]), str(r["word"])) for r in cmap},
        }
    )

print("=" * 60)
print("PACK_HOLDS" if any_hold else "PACK_PASS")

# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path

from core_engine.economic_reel_lofi import lofi_collections as rag
from core_engine.economic_reel_lofi.setting_archetypes import classify_setting_archetype

script = json.loads(
    Path("core_engine/economic_reel_lofi/store/locked_script_communication_v2.json").read_text(
        encoding="utf-8"
    )
)
used = [
    classify_setting_archetype(str(r.get("setting") or ""), str(r.get("key_object") or ""))
    for r in script["lines"]
]
unique = sorted({a for a in used if a and a != "unknown"})
rag.append_history(
    "relationship",
    str(script.get("monologue") or ""),
    meta={
        "theme": "communication",
        "hook_type": script.get("hook_type"),
        "anchor_object": script.get("anchor_object"),
        "arc_template": script.get("arc_template"),
        "close_variant": "portrait_close",
        "close_character": "woman",
        "close_target": "portrait",
        "setting_archetypes": unique,
        "setting_archetype_beats": used,
        "source": "locked_script_communication_v2",
    },
)
rag.mark_theme_used("relationship", "communication", "repair_after_conflict")
print("stamped communication history archetypes=", unique)
print("recent closes", rag.recent_close_variants("relationship", 4))
print("preceding", rag.preceding_setting_archetypes("relationship"))

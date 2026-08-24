# -*- coding: utf-8 -*-
"""Lock the hope 9-line rewrite for the closing-pass stills/VO test."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core_engine.economic_reel_lofi import lofi_collections as rag
from core_engine.economic_reel_lofi.script_agent import _thematic_from_lines
from core_engine.economic_reel_lofi.setting_archetypes import classify_setting_archetype
from core_engine.economic_reel_lofi.visual_identity import setting_object_pool

OUT = ROOT / "outputs" / "wonder_feed" / "clips" / "lofi_script_hope_20260824_closing.json"

CAPTIONS = [
    "One window stayed lit through the whole cold night.",
    "It cost someone sleep to keep that light on.",
    "A kettle in the dark kitchen starts to steam.",
    "Someone woke early enough to risk boiling water.",
    "Curtains hold one stripe of sun before it spreads.",
    "Someone left them open before sun would come.",
    "The bus stop empties out under a clearing sky.",
    "Someone waited through rain to see that gap open.",
    "Hope is the light kept burning before dawn.",
]

VISUALS = [
    ("quiet street at first light", "one window still lit", "figure_centered", "woman"),
    ("street corner under a streetlamp", "streetlamp", "figure_centered", "silhouette"),
    ("kitchen at pale dawn", "kettle beginning to steam", "figure_centered", "man"),
    ("kitchen at pale dawn", "kettle beginning to steam", "object_focus", "object_focus"),
    ("bedroom curtains", "first stripe of sun", "object_focus", "object_focus"),
    ("bedroom curtains", "open curtains", "figure_centered", "silhouette"),
    ("bus stop after rain", "clearing sky", "wide_environment", "silhouette"),
    ("rain-streaked window at dusk", "rain on the window", "wide_environment", "silhouette"),
    ("quiet street at first light", "one window still lit", "figure_centered", "woman"),
]


def main() -> int:
    bank = rag._read_json(rag._store_path("lofi_theme_bank_relationship"), [])
    theme_row = next(
        (r for r in bank if isinstance(r, dict) and r.get("theme") == "hope"),
        {"theme": "hope", "module": "relationship"},
    )
    pool = setting_object_pool(theme_row)
    rhetoric = {
        "name": "parable_triad",
        "locked_thesis": "Hope costs something before it stays.",
        "locked_anchor": {
            "name": "one window still lit",
            "initial_state": "first seen",
            "final_state": "still burning at dawn",
        },
    }
    script = _thematic_from_lines(
        CAPTIONS,
        module="relationship",
        theme="hope",
        scene_count=9,
        hook_type="definition",
        pool=pool,
        retrieved=[],
        structure={},
        rhetoric=rhetoric,
        seed={"hooks": [], "details": []},
        writer="claude",
    )
    lines = [r for r in (script.get("lines") or []) if isinstance(r, dict)]
    for i, text in enumerate(CAPTIONS):
        if i >= len(lines):
            break
        lines[i]["text"] = text
        lines[i]["beat_text"] = text
        setting, obj, comp, subj = VISUALS[i]
        lines[i]["setting"] = setting
        lines[i]["key_object"] = obj
        lines[i]["composition_type"] = comp
        lines[i]["subject_type"] = subj
        lines[i]["setting_archetype"] = classify_setting_archetype(setting, obj)
        lines[i]["lighting_condition"] = ""
        lines[i]["close_variant"] = ""
        lines[i]["shot_scale"] = "medium"
    if lines:
        lines[0]["episode_theme"] = "hope"
        lines[0]["episode_module"] = "relationship"
    script["lines"] = lines
    script["monologue"] = " ".join(CAPTIONS)
    script["source_monologue"] = " ".join(CAPTIONS)
    script["monologue_split"] = True
    script["direct_beats"] = True
    script["theme"] = "hope"
    script["module"] = "relationship"
    script["arc_template"] = "thematic_arc"
    script["hook_type"] = "definition"
    script["rhetoric_pattern"] = "parable_triad"
    script["thesis"] = "Hope costs something before it stays."
    rag.stamp_close_variant_on_script(script, lines)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(script, indent=2, ensure_ascii=False), encoding="utf-8")
    print("wrote", OUT)
    for i, t in enumerate(CAPTIONS, 1):
        print(f"  {i}. ({len(t.split())}w/{len(t)}c) {t}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

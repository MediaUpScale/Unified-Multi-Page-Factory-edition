# -*- coding: utf-8 -*-
"""Gate-clean healing draft → Claude polish (fresh theme, not the shipped 5)."""
from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path

os.environ.setdefault("LOFI_SKIP_COHERENCE_LLM", "1")

from core_engine.economic_reel_lofi.claude_polish import polish_one, summarize_cost_log
from core_engine.economic_reel_lofi.script_agent import (
    _line_word_count,
    _narrative_gate_reasons,
)
from core_engine.economic_reel_lofi.setting_archetypes import (
    classify_composition_type,
    classify_setting_archetype,
)

STORE = Path("core_engine/economic_reel_lofi/store")
CAPS = [
    "Don't wait for the wound to close first.",
    "You fold the blanket without them.",
    "Because the room stays quiet now.",
    "Stay here even when it still aches.",
    "So you set the unused cup down.",
    "Yet you walk the dusk hall alone.",
    "You pause in the doorway.",
    "Though the blanket sits folded, you remain.",
    "You leave the lamp on anyway.",
]


def _gate(script: dict) -> list[str]:
    data = deepcopy(script)
    for row in data.get("lines") or []:
        if isinstance(row, dict):
            row["setting_archetype"] = classify_setting_archetype(
                row.get("setting", ""), row.get("key_object", "")
            )
            row["composition_type"] = classify_composition_type(row)
    return [r for r in _narrative_gate_reasons(data, "relationship") if r]


def main() -> None:
    src = json.loads((STORE / "locked_script_loneliness_structure_v1.json").read_text(encoding="utf-8"))
    src["theme"] = "healing"
    src["subtheme"] = ""
    src["rhetoric_pattern"] = "domino_chain"
    src["rhetoric_hook_overlay"] = ""
    src["thesis"] = "Healing stays when you sit with the ache instead of waiting it gone."
    src["anchor_object"] = {
        "name": "folded blanket",
        "initial_state": "still on the chair",
        "final_state": "folded, left in the room",
    }
    for row, text in zip(src["lines"], CAPS):
        row["text"] = text
        row["beat_text"] = text
        row["episode_theme"] = "healing"
        if row.get("key_object") == "empty chair":
            row["key_object"] = "folded blanket"
    src["monologue"] = " ".join(CAPS)
    before_holds = _gate(src)
    print("BEFORE HOLDS", before_holds or "none")
    for row in src["lines"]:
        text = str(row.get("text") or "")
        print(f"  {row['scene']} {_line_word_count(text)}w/{len(text)}c {text!r}")
    if before_holds:
        (STORE / "locked_script_healing_polish_src.json").write_text(
            json.dumps(src, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        print("source not gate-clean — not calling Claude")
        return
    polished = polish_one(src)
    print("AFTER")
    for row in polished.get("lines") or []:
        text = str(row.get("text") or "")
        print(f"  {row.get('scene')} {_line_word_count(text)}w/{len(text)}c {text!r}")
    after_holds = _gate(polished) if polished.get("polished") else ["polish missed"]
    print("AFTER HOLDS", after_holds or "none")
    print("COST", summarize_cost_log(10))
    dest = STORE / "locked_script_healing_format_polished.json"
    dest.write_text(
        json.dumps(polished if polished.get("polished") and not after_holds else src, indent=2, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )
    print("wrote", dest)


if __name__ == "__main__":
    main()

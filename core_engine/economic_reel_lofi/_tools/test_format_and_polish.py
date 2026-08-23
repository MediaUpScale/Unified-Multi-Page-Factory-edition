# -*- coding: utf-8 -*-
"""Format-guidance DeepSeek retest + Claude polish on a fresh theme."""
from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path

os.environ.setdefault("LOFI_SKIP_COHERENCE_LLM", "1")
os.environ.setdefault("LOFI_DEEPSEEK_TEMPERATURE", "1.15")
os.environ.setdefault("LOFI_DEEPSEEK_CANDIDATES", "4")

from core_engine.economic_reel_lofi import lofi_collections as rag
from core_engine.economic_reel_lofi.script_agent import (
    _line_word_count,
    _narrative_gate_reasons,
    generate_script,
)
from core_engine.economic_reel_lofi.setting_archetypes import (
    classify_composition_type,
    classify_setting_archetype,
)

STORE = Path("core_engine/economic_reel_lofi/store")
THEMES = ("healing", "time")


def _gate(script: dict) -> list[str]:
    data = deepcopy(script)
    for row in data.get("lines") or []:
        if isinstance(row, dict):
            row["setting_archetype"] = classify_setting_archetype(
                row.get("setting", ""), row.get("key_object", "")
            )
            row["composition_type"] = classify_composition_type(row)
    return [r for r in _narrative_gate_reasons(data, "relationship") if r]


def _print(script: dict) -> None:
    for row in script.get("lines") or []:
        text = str(row.get("text") or "")
        print(
            f"  {row.get('scene')} {str(row.get('beat_function') or '-'):12} "
            f"{_line_word_count(text)}w/{len(text)}c {text!r}"
        )


def main() -> None:
    rag.ensure_seeded()
    rag.reset_batch_scripts()
    rows = []
    polish_src = None
    for theme in THEMES:
        print("=" * 60, "DEEPSEEK", theme)
        script = generate_script(
            module="relationship",
            theme=theme,
            scene_count=9,
            skip_polish=True,
        )
        holds = list(script.get("narrative_holds") or _gate(script))
        print("rhetoric", script.get("rhetoric_pattern"), script.get("rhetoric_hook_overlay"))
        print("thesis", script.get("thesis"))
        print("anchor", (script.get("anchor_object") or {}).get("name"))
        _print(script)
        print("HOLDS", holds or "none")
        dest = STORE / f"locked_script_{theme}_format_test.json"
        dest.write_text(json.dumps(script, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        rows.append({"theme": theme, "holds": holds, "path": str(dest), "polished": False})
        if not holds and polish_src is None:
            polish_src = script

    if polish_src is None:
        print("NO GATE-CLEAN DEEPSEEK DRAFT — skip polish")
        (STORE / "format_polish_retest.json").write_text(
            json.dumps(rows, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        return

    theme = str(polish_src.get("theme") or "fresh")
    print("=" * 60, "POLISH", theme)
    from core_engine.economic_reel_lofi.claude_polish import polish_one, summarize_cost_log

    before = [str(r.get("text") or "") for r in (polish_src.get("lines") or [])]
    polished = polish_one(polish_src)
    after = [str(r.get("text") or "") for r in (polished.get("lines") or [])]
    print("BEFORE")
    for i, t in enumerate(before, 1):
        print(f"  {i} {t}")
    print("AFTER")
    for i, t in enumerate(after, 1):
        print(f"  {i} {t}")
    holds = _gate(polished) if polished.get("polished") else ["polish missed"]
    print("POLISH HOLDS", holds or "none")
    dest = STORE / f"locked_script_{theme}_format_polished.json"
    kept = polished if polished.get("polished") and not holds else polish_src
    dest.write_text(json.dumps(kept, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    rows.append(
        {
            "theme": theme,
            "stage": "polish",
            "polished": bool(kept.get("polished")),
            "holds": holds,
            "before": before,
            "after": after,
            "cost": summarize_cost_log(10),
            "path": str(dest),
        }
    )
    (STORE / "format_polish_retest.json").write_text(
        json.dumps(rows, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()

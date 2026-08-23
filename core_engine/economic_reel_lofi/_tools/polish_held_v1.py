# -*- coding: utf-8 -*-
"""Polish the gate-passing v1 held pack via Claude Batch API."""
from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path

os.environ.setdefault("LOFI_SKIP_COHERENCE_LLM", "1")

from core_engine.economic_reel_lofi import lofi_collections as rag
from core_engine.economic_reel_lofi.claude_polish import (
    polish_scripts_batch,
    summarize_cost_log,
)
from core_engine.economic_reel_lofi.script_agent import (
    _line_word_count,
    _narrative_gate_reasons,
)
from core_engine.economic_reel_lofi.setting_archetypes import (
    classify_composition_type,
    classify_setting_archetype,
)

STORE = Path("core_engine/economic_reel_lofi/store")
PACK = (
    "forgiveness_structure_v1",
    "loss_structure_v1",
    "loneliness_structure_v1",
    "regret_structure_v1",
    "belonging_structure_v1",
)


def _gate(script: dict) -> list[str]:
    data = deepcopy(script)
    for row in data.get("lines") or []:
        if isinstance(row, dict):
            row["setting_archetype"] = classify_setting_archetype(
                row.get("setting", ""), row.get("key_object", "")
            )
            row["composition_type"] = classify_composition_type(row)
    return [r for r in _narrative_gate_reasons(data, "relationship") if r]


def _new_holds(before: list[str], after: list[str]) -> list[str]:
    """Fail polish only on new structural holds, not history echoes of these same themes."""
    prev = set(before)
    out = []
    for h in after:
        if h in prev:
            continue
        if h.startswith("cross-run phrase/anchor reuse"):
            continue
        out.append(h)
    return out


def _print(script: dict) -> None:
    for row in script.get("lines") or []:
        text = str(row.get("text") or "")
        print(
            f"  {row.get('scene')} {row.get('beat_function'):12} "
            f"{_line_word_count(text)}w/{len(text)}c {text!r}"
        )


def main() -> None:
    scripts = []
    for name in PACK:
        path = STORE / f"locked_script_{name}.json"
        scripts.append(json.loads(path.read_text(encoding="utf-8")))
    rag.reset_batch_scripts()
    polished = polish_scripts_batch(scripts)
    rows = []
    for src, dst, name in zip(scripts, polished, PACK):
        theme = src.get("theme")
        print("=" * 60, theme)
        print("BEFORE")
        _print(src)
        kept = dst
        if dst.get("polished"):
            new_holds = _new_holds(_gate(src), _gate(dst))
            print("AFTER (claude draft)")
            _print(dst)
            if new_holds:
                print("POLISH BROKE GATES — keeping v1 finalist (no Claude retry)")
                for h in new_holds[:8]:
                    print("  -", h)
                kept = src
            else:
                print("AFTER (polish kept)")
        else:
            print("AFTER (unpolished — batch miss)")
            _print(src)
        dest = STORE / f"locked_script_{theme}_structure_polished.json"
        dest.write_text(json.dumps(kept, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        rows.append(
            {
                "theme": theme,
                "polished": bool(kept.get("polished")),
                "before": [str(r.get("text") or "") for r in (src.get("lines") or [])],
                "claude_draft": [str(r.get("text") or "") for r in (dst.get("lines") or [])]
                if dst.get("polished")
                else [],
                "after": [str(r.get("text") or "") for r in (kept.get("lines") or [])],
                "path": str(dest),
            }
        )
    report = STORE / "structure_pack_polish_report.json"
    report.write_text(json.dumps(rows, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    caps: dict[str, list[str]] = {}
    for row in rows:
        theme = str(row.get("theme") or "")
        for text in row.get("after") or []:
            norm = rag.normalize_caption_text(str(text))
            if len(norm.split()) >= 4:
                caps.setdefault(norm, []).append(theme)
    collisions = {k: v for k, v in caps.items() if len(set(v)) > 1}
    print("=" * 60, "BATCH DEDUP", collisions or "none")
    print("=" * 60, "COST", summarize_cost_log(10))
    print("report", report)


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""Compose one off-domain regret / open_question 9-line draft."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core_engine.economic_reel_lofi.claude_compose import compose_lines, format_compose_user

LIB = (
    Path(__file__).resolve().parents[1] / "data" / "structure_library_v2.json"
)


def main() -> int:
    raw = json.loads(LIB.read_text(encoding="utf-8"))
    rec = None
    for row in raw.get("rhetoric_patterns") or raw.get("patterns") or raw:
        if isinstance(row, dict) and row.get("name") == "open_question":
            rec = row
            break
    if rec is None:
        # library may nest under tools
        for key, val in raw.items():
            if not isinstance(val, list):
                continue
            for row in val:
                if isinstance(row, dict) and row.get("name") == "open_question":
                    rec = row
                    break
    if not rec:
        raise SystemExit("open_question pattern not found")
    user = format_compose_user(
        theme="regret",
        thesis="Regret costs something before it stays.",
        anchor={
            "name": "folded letter never sent",
            "initial_state": "first seen",
            "final_state": "changed by the last beats",
        },
        rhetoric=rec,
        pool_block=(
            "desk at night / folded letter never sent\n"
            "doorway after someone left / hand still on the frame\n"
            "street corner under a streetlamp / streetlamp"
        ),
    )
    lines = compose_lines(user, theme="regret")
    print("LINES")
    for i, t in enumerate(lines, 1):
        print(f"{i}. ({len(t.split())}w/{len(t)}c) {t}")
    out = Path(__file__).resolve().parents[1] / "store" / "regret_open_question_draft.json"
    out.write_text(json.dumps(lines, indent=2, ensure_ascii=False), encoding="utf-8")
    print("wrote", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

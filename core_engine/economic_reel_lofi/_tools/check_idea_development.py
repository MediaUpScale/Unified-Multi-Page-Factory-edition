# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path

from core_engine.economic_reel_lofi.script_agent import (
    _GOLD_CADENCE_BEATS,
    _GOLD_CADENCE_BEATS_B,
    _line_word_count,
    assess_connective_development,
    assess_insight_answers_hook,
    assess_object_churn,
    assess_recipe_pattern,
    _narrative_gate_reasons,
)


def _report_beats(name: str, beats: list[str], insight_i: int) -> None:
    print("==", name)
    for i, text in enumerate(beats, 1):
        words = _line_word_count(text)
        chars = len(text)
        flag = "OVER" if words > 9 or chars > 56 else "ok"
        print(f"  {i} {words}w {chars}c {flag} {text!r}")
    lines = []
    for i, text in enumerate(beats):
        fn = "hook" if i == 0 else ("insight" if i == insight_i else "turn")
        lines.append({"text": text, "beat_function": fn})
    print(" connect", assess_connective_development(lines).get("fails") or "pass")
    print(" recipe", assess_recipe_pattern(lines).get("fails") or "pass")
    print(" hookans", assess_insight_answers_hook(lines).get("fails") or "pass")


def main() -> None:
    _report_beats("gold A", list(_GOLD_CADENCE_BEATS), 3)
    _report_beats("gold B", list(_GOLD_CADENCE_BEATS_B), 3)
    for name in ("loss", "loneliness", "forgiveness"):
        data = json.loads(
            Path(f"core_engine/economic_reel_lofi/store/locked_script_{name}_v1.json").read_text(
                encoding="utf-8"
            )
        )
        print("== lock", name)
        for row in data["lines"]:
            text = row["text"]
            words = _line_word_count(text)
            chars = len(text)
            flag = "OVER" if words > 9 or chars > 56 else "ok"
            print(f"  {row['scene']} {words}w {chars}c {flag} {text!r}")
        print(" object", assess_object_churn(data["lines"], data["anchor_object"]["name"]))
        reasons = _narrative_gate_reasons(data, "relationship")
        print(
            " new-gates",
            [
                r
                for r in reasons
                if not r.startswith("cross-run")
            ]
            or "pass",
        )


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path("core_engine/economic_reel_lofi/store")

REWRITES = {
    "loss": [
        "Don't name the gap later.",
        "So you open the half-packed box.",
        "You touch the empty hanger.",
        "Leave the gap named now, not later.",
        "You set the unused key down.",
        "You walk the empty path.",
        "Still you stop in the kitchen dark.",
        "The hanger hangs alone now.",
        "You face the empty tide path.",
    ],
    "loneliness": [
        "Don't wait for the knock.",
        "Yet two cups sit by the empty chair.",
        "You leave the second place set.",
        "Stay. The knock is not coming.",
        "Though no one knocks, you fold the napkin.",
        "You cross the dark stairwell.",
        "You pause by the spare-room lamp.",
        "Since the chair faces out, you remain.",
        "You leave the porch light on.",
    ],
    "forgiveness": [
        "Don't make them earn it first.",
        "Because they will never earn it from you.",
        "You place the river stone.",
        "Set the bag down since they never earn it.",
        "You leave the letter open.",
        "You walk the dusk river wall.",
        "You unclench in the doorway.",
        "Yet the bag you set down sits empty.",
        "You walk away empty-handed.",
    ],
}

FILES = (
    "locked_script_loss_v1.json",
    "locked_script_loss_v1_assemble.json",
    "locked_script_loneliness_v1.json",
    "locked_script_loneliness_v1_assemble.json",
    "locked_script_forgiveness_v1.json",
    "locked_script_forgiveness_v1_assemble.json",
)


def apply(path: Path) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    script = data["script"] if isinstance(data.get("script"), dict) else data
    theme = str(script.get("theme") or "")
    lines_txt = REWRITES[theme]
    for row, text in zip(script["lines"], lines_txt):
        row["text"] = text
        row["beat_text"] = text
    script["monologue"] = " ".join(lines_txt)
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"updated {path} theme={theme}")


def main() -> None:
    for name in FILES:
        apply(ROOT / name)


if __name__ == "__main__":
    main()

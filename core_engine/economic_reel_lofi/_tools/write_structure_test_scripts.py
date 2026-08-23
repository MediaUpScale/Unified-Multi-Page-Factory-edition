# -*- coding: utf-8 -*-
"""Copy closing-pack locks, rewrite captions for structure_library review."""
from __future__ import annotations

import json
from pathlib import Path

STORE = Path(__file__).resolve().parents[1] / "store"

PACKS = {
    "forgiveness": {
        "src": "locked_script_forgiveness_v1.json",
        "rhetoric_pattern": "definition_then_evidence",
        "rhetoric_hook_overlay": "",
        "captions": [
            "Forgiveness starts before they earn it.",
            "You set the bag on the table.",
            "Because they never touch the river stone.",
            "Set the bag down without them earning it.",
            "You leave the letter open now.",
            "Yet you walk the dusk river wall.",
            "You unclench in the doorway.",
            "The bag you set down sits empty.",
            "You walk away empty-handed.",
        ],
    },
    "loss": {
        "src": "locked_script_loss_v1.json",
        "rhetoric_pattern": "negation_list_to_permission",
        "rhetoric_hook_overlay": "",
        "captions": [
            "Don't postpone naming what left.",
            "I don't close the half-packed box.",
            "Even if it aches, I leave the hanger empty.",
            "Leave the gap named now.",
            "So I set the unused key down.",
            "You walk the empty path.",
            "You stop in the kitchen dark.",
            "Still the hanger hangs alone now.",
            "You face the empty tide path.",
        ],
    },
    "loneliness": {
        "src": "locked_script_loneliness_v1.json",
        "rhetoric_pattern": "domino_chain",
        "rhetoric_hook_overlay": "",
        "captions": [
            "Don't wait for the knock.",
            "Since you wait, two cups sit by the chair.",
            "You leave the second place set.",
            "Stay here even when no one knocks.",
            "You fold the napkin for no one.",
            "Therefore you cross the stairwell alone now.",
            "You pause by the spare-room lamp.",
            "Though the chair sits empty, you remain.",
            "You leave the porch light on.",
        ],
    },
}


def main() -> None:
    for theme, spec in PACKS.items():
        src = STORE / spec["src"]
        data = json.loads(src.read_text(encoding="utf-8"))
        caps = spec["captions"]
        if len(data["lines"]) != 9 or len(caps) != 9:
            raise SystemExit(f"{theme}: expected 9 lines")
        for row, text in zip(data["lines"], caps):
            row["text"] = text
            row["beat_text"] = text
        data["monologue"] = " ".join(caps)
        data["rhetoric_pattern"] = spec["rhetoric_pattern"]
        data["rhetoric_hook_overlay"] = spec["rhetoric_hook_overlay"]
        dest = STORE / f"locked_script_{theme}_structure_v1.json"
        dest.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"wrote {dest.name}")


if __name__ == "__main__":
    main()

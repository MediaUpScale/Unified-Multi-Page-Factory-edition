# -*- coding: utf-8 -*-
"""Sanity-check object-churn on known false-positive polish drafts."""
from __future__ import annotations

from core_engine.economic_reel_lofi.script_agent import (
    _spoken_prop_stems,
    assess_object_churn,
)

CASES = [
    (
        "bag",
        [
            "Forgiveness starts before they've earned a single inch of it.",
            "You set the bag down on the table, still full.",
            "Because they never once touch the river stone.",
            "Set the bag down and let it go unearned.",
            "You leave the letter lying open now.",
            "Yet you walk the dusk-lit river wall alone.",
            "You unclench, slow, standing in the doorway.",
            "The bag you set down sits there, empty.",
            "You walk away with your hands finally empty.",
        ],
    ),
    (
        "hanger",
        [
            "Don't stall on naming what's gone.",
            "I leave the half-packed box open.",
            "Even if it stings, I let the hanger hang bare.",
            "Name the gap now, while it's still raw.",
            "So I set the unused key down on the table.",
            "You walk the emptied path.",
            "You stand still in the kitchen's dark.",
            "Still the hanger hangs alone.",
            "You face the tide path, empty, and keep walking.",
        ],
    ),
    (
        "seat",
        [
            "Don't save the words for later.",
            "You touch the seat where no one sits.",
            "Though the path lies empty for them now.",
            "A coat still hangs there, unworn, waiting.",
            "You stop in the hollow of the lobby.",
            "Because they left the seat cold behind them.",
            "You stand there, in the doorway.",
            "Don't wait. Say it at the seat.",
            "You walk back to the seat anyway.",
        ],
    ),
]


def main() -> None:
    for anchor, texts in CASES:
        lines = [{"text": t} for t in texts]
        stems = [_spoken_prop_stems(t) for t in texts]
        rec = assess_object_churn(lines, anchor)
        print(anchor, "supporting", rec["supporting"], "fails", rec["fails"] or "none")
        for i, s in enumerate(stems, 1):
            if s:
                print(f"  s{i} {sorted(s)} {texts[i-1]!r}")


if __name__ == "__main__":
    main()

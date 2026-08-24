# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path

from core_engine.economic_reel_lofi import lofi_collections as rag
from core_engine.economic_reel_lofi.setting_archetypes import classify_setting_archetype

JOBS = (
    {
        "lock": "core_engine/economic_reel_lofi/store/locked_script_loss_v1.json",
        "theme": "loss",
        "subtheme": "what_the_leaving_took",
        "close_variant": "silhouette",
        "close_character": "man",
        "close_target": "object",
        "source": "locked_script_loss_v1",
    },
    {
        "lock": "core_engine/economic_reel_lofi/store/locked_script_loneliness_v1.json",
        "theme": "loneliness",
        "subtheme": "the_room_that_stays_empty",
        "close_variant": "portrait_close",
        "close_character": "woman",
        "close_target": "portrait",
        "source": "locked_script_loneliness_v1",
    },
    {
        "lock": "core_engine/economic_reel_lofi/store/locked_script_forgiveness_v1.json",
        "theme": "forgiveness",
        "subtheme": "putting_the_weight_down",
        "close_variant": "silhouette",
        "close_character": "man",
        "close_target": "hands",
        "source": "locked_script_forgiveness_v1",
    },
)


def main() -> None:
    for job in JOBS:
        script = json.loads(Path(job["lock"]).read_text(encoding="utf-8"))
        used = [
            classify_setting_archetype(
                str(r.get("setting") or ""), str(r.get("key_object") or "")
            )
            for r in script["lines"]
        ]
        unique = sorted({a for a in used if a and a != "unknown"})
        rag.append_history(
            "relationship",
            str(script.get("monologue") or ""),
            meta={
                "theme": job["theme"],
                "hook_type": script.get("hook_type"),
                "anchor_object": script.get("anchor_object"),
                "arc_template": script.get("arc_template"),
                "close_variant": job["close_variant"],
                "close_character": job["close_character"],
                "close_target": job["close_target"],
                "setting_archetypes": unique,
                "setting_archetype_beats": used,
                "source": job["source"],
            },
        )
        rag.mark_theme_used("relationship", job["theme"], job["subtheme"])
        print(f"stamped {job['theme']} archetypes={unique}")
    print("recent closes", rag.recent_close_variants("relationship", 8))
    print("preceding", rag.preceding_setting_archetypes("relationship"))


if __name__ == "__main__":
    main()

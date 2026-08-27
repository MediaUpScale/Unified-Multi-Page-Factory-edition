# -*- coding: utf-8 -*-
"""Regen the 9 flagged distance stills, then episode QA; assemble only if clear."""
from __future__ import annotations

import json
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env", override=True, encoding="utf-8-sig")

from core.economic_reel_lofi.pipeline import (  # noqa: E402
    assess_cross_beat_style,
    assess_object_beat_visual_continuity,
)
from core.economic_reel_lofi.regen import (  # noqa: E402
    assemble_video_from_episode,
    load_episode_json,
    regenerate_flagged_scenes,
    save_episode_json,
)

EP = ROOT / "outputs/wonder_feed/clips/lofi_stills_distance_silence_that_speaks_20260826_222416_v01.json"


def main() -> None:
    print(f"[distance regen] episode={EP}")
    result = regenerate_flagged_scenes(EP, max_attempts=2, assemble=False)
    print(
        "[distance regen] per-scene",
        json.dumps(
            {
                "scenes": result.get("scenes"),
                "leftover": result.get("leftover"),
                "statuses": [
                    {
                        "scene": r.get("scene"),
                        "status": r.get("status"),
                        "qa_flaws": r.get("qa_flaws"),
                    }
                    for r in (result.get("results") or [])
                ],
            },
            indent=2,
        ),
    )
    ep = load_episode_json(EP)
    script = ep.get("script") if isinstance(ep.get("script"), dict) else {}
    lines = list(script.get("lines") or [])
    paths = [Path(p) for p in (ep.get("scene_images") or [])]
    xstyle = assess_cross_beat_style(paths, lines)
    obj = assess_object_beat_visual_continuity(lines, paths)
    script["cross_beat_style"] = xstyle
    script["object_beat_continuity"] = obj
    qa_flags = [str(f) for f in (ep.get("visual_qa_flags") or [])]
    for extra in list(xstyle.get("fails") or []) + list(obj.get("fails") or []):
        if extra not in qa_flags:
            qa_flags.append(str(extra))
    ep["visual_qa_flags"] = qa_flags
    ep["manual_review"] = bool(qa_flags or result.get("leftover"))
    ep["script"] = script
    save_episode_json(ep, EP)
    print("[distance QA] leftover", result.get("leftover"))
    print("[distance QA] object_beat_continuity.fails", obj.get("fails"))
    print("[distance QA] cross_beat_style.clusters", xstyle.get("clusters"))
    print("[distance QA] cross_beat_style.fails", xstyle.get("fails"))
    print("[distance QA] mismatches", xstyle.get("mismatches"))
    spoken = []
    for g in ep.get("object_gate_by_scene") or []:
        if not isinstance(g, dict):
            continue
        flaws = " ".join(str(x) for x in (g.get("qa_flaws") or []))
        if any(w in flaws.lower() for w in ("window", "plant", "mug", "curtain", "jar")):
            spoken.append({"scene": g.get("scene"), "flaws": g.get("qa_flaws")})
    print("[distance QA] spoken_prop-like flaws", spoken)
    leftover = list(result.get("leftover") or [])
    if leftover or obj.get("fails") or xstyle.get("fails"):
        print("[distance regen] HOLD — not assembling")
        return
    video = assemble_video_from_episode(ep)
    ep["video_path"] = str(video)
    ep["mode"] = "pilot_distance"
    save_episode_json(ep, EP)
    print(f"[distance regen] PILOT {video}")


if __name__ == "__main__":
    main()

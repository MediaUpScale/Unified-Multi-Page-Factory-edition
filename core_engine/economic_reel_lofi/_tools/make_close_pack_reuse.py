# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path

JOBS = (
    {
        "hold": "outputs/wonder_feed/clips/lofi_hold_loss_20260823_044007_v01.json",
        "lock": "core_engine/economic_reel_lofi/store/locked_script_loss_v1.json",
        "out": "core_engine/economic_reel_lofi/store/locked_script_loss_v1_reuse.json",
        "work": "outputs/wonder_feed/assets/lofi_run_20260823_044007_01",
        "regen": {5},
        "accept": set(),
    },
    {
        "hold": "outputs/wonder_feed/clips/lofi_hold_loneliness_20260823_044252_v02.json",
        "lock": "core_engine/economic_reel_lofi/store/locked_script_loneliness_v1.json",
        "out": "core_engine/economic_reel_lofi/store/locked_script_loneliness_v1_reuse.json",
        "work": "outputs/wonder_feed/assets/lofi_run_20260823_044252_02",
        "regen": {5},
        "accept": {8},
    },
    {
        "hold": "outputs/wonder_feed/clips/lofi_hold_forgiveness_20260823_044616_v03.json",
        "lock": "core_engine/economic_reel_lofi/store/locked_script_forgiveness_v1.json",
        "out": "core_engine/economic_reel_lofi/store/locked_script_forgiveness_v1_reuse.json",
        "work": "outputs/wonder_feed/assets/lofi_run_20260823_044616_03",
        "regen": {1, 5, 9},
        "accept": {2, 6},
    },
)


def main() -> None:
    for job in JOBS:
        hold = json.loads(Path(job["hold"]).read_text(encoding="utf-8"))
        lock = json.loads(Path(job["lock"]).read_text(encoding="utf-8"))
        old = {int(r["scene"]): r for r in hold["script"]["lines"]}
        images = []
        for i, row in enumerate(lock["lines"]):
            scene = i + 1
            if scene in job["regen"]:
                images.append(f"{job['work']}/_regen_scene_{scene:02d}")
                continue
            images.append(f"{job['work']}/scene_{scene:02d}.png")
            gate = (old.get(scene) or {}).get("default_object_gate") or {}
            if scene in job["accept"]:
                gate = dict(gate)
                gate["image_ok"] = True
                gate["manual_accept"] = True
            row["default_object_gate"] = gate
        dest = Path(job["out"])
        dest.write_text(
            json.dumps(
                {
                    "manual_accept_scenes": sorted(job["accept"]),
                    "work_dir": job["work"],
                    "scene_images": images,
                    "script": lock,
                },
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"wrote {dest}")
        for i, p in enumerate(images, 1):
            tag = "REGEN" if i in job["regen"] else (
                "ACCEPT" if i in job["accept"] else "REUSE"
            )
            print(f"  {i} {tag} {p}")


if __name__ == "__main__":
    main()

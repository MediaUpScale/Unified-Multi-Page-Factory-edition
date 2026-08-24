# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path


def gates_of(path: str) -> dict[int, dict]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    script = data.get("script") or data
    out = {}
    for row in script.get("lines") or []:
        if isinstance(row, dict) and row.get("default_object_gate"):
            out[int(row["scene"])] = row["default_object_gate"]
    return out


def write(lock: str, dest: str, work: str, images: list[str], accept: list[int], gates: dict) -> None:
    script = json.loads(Path(lock).read_text(encoding="utf-8"))
    for i, row in enumerate(script["lines"]):
        scene = i + 1
        if scene in gates:
            row["default_object_gate"] = gates[scene]
        if scene in accept:
            g = dict(row.get("default_object_gate") or {})
            g["image_ok"] = True
            g["manual_accept"] = True
            row["default_object_gate"] = g
    Path(dest).write_text(
        json.dumps(
            {
                "manual_accept_scenes": accept,
                "work_dir": work,
                "scene_images": images,
                "script": script,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    print("wrote", dest)


def main() -> None:
    old = gates_of("outputs/wonder_feed/clips/lofi_hold_loss_20260823_044007_v01.json")
    work = "outputs/wonder_feed/assets/lofi_run_20260823_044007_01"
    write(
        "core_engine/economic_reel_lofi/store/locked_script_loss_v1.json",
        "core_engine/economic_reel_lofi/store/locked_script_loss_v1_reuse.json",
        work,
        [f"{work}/_regen_scene_05" if i == 5 else f"{work}/scene_{i:02d}.png" for i in range(1, 10)],
        [],
        old,
    )

    g = gates_of("outputs/wonder_feed/clips/lofi_hold_forgiveness_20260823_044616_v03.json")
    g.update(gates_of("outputs/wonder_feed/clips/lofi_stills_forgiveness_20260823_060812_v03.json"))
    images = [
        "outputs/wonder_feed/assets/lofi_run_20260823_060812_03/scene_01.png",
        "outputs/wonder_feed/assets/lofi_run_20260823_044616_03/scene_02.png",
        "outputs/wonder_feed/assets/lofi_run_20260823_044616_03/scene_03.png",
        "outputs/wonder_feed/assets/lofi_run_20260823_044616_03/scene_04.png",
        "outputs/wonder_feed/assets/lofi_run_20260823_060812_03/scene_05.png",
        "outputs/wonder_feed/assets/lofi_run_20260823_044616_03/scene_06.png",
        "outputs/wonder_feed/assets/lofi_run_20260823_044616_03/scene_07.png",
        "outputs/wonder_feed/assets/lofi_run_20260823_044616_03/scene_08.png",
        "outputs/wonder_feed/assets/lofi_run_20260823_061123_02/scene_09.png",
    ]
    write(
        "core_engine/economic_reel_lofi/store/locked_script_forgiveness_v1.json",
        "core_engine/economic_reel_lofi/store/locked_script_forgiveness_v1_assemble.json",
        "outputs/wonder_feed/assets/lofi_run_20260823_044616_03",
        images,
        [2, 6, 9],
        g,
    )


if __name__ == "__main__":
    main()

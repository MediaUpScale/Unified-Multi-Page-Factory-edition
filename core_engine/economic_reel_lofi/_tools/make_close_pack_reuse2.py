# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path


def _gates(src: dict, n: int = 9) -> dict[int, dict]:
    script = src.get("script") or src
    out = {}
    for row in script.get("lines") or []:
        if isinstance(row, dict) and row.get("default_object_gate"):
            out[int(row["scene"])] = row["default_object_gate"]
    return out


def write_job(lock_path: str, dest: str, work: str, images: list[str], accept: list[int], gates: dict) -> None:
    lock = json.loads(Path(lock_path).read_text(encoding="utf-8"))
    for i, row in enumerate(lock["lines"]):
        scene = i + 1
        if scene in gates:
            row["default_object_gate"] = gates[scene]
        if scene in accept:
            gate = dict(row.get("default_object_gate") or {})
            gate["image_ok"] = True
            gate["manual_accept"] = True
            row["default_object_gate"] = gate
    Path(dest).write_text(
        json.dumps(
            {
                "manual_accept_scenes": accept,
                "work_dir": work,
                "scene_images": images,
                "script": lock,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    print("wrote", dest)


def main() -> None:
    lone_stills = json.loads(
        Path("outputs/wonder_feed/clips/lofi_stills_loneliness_20260823_060758_v02.json").read_text(
            encoding="utf-8"
        )
    )
    write_job(
        "core_engine/economic_reel_lofi/store/locked_script_loneliness_v1.json",
        "core_engine/economic_reel_lofi/store/locked_script_loneliness_v1_assemble.json",
        "outputs/wonder_feed/assets/lofi_run_20260823_060758_02",
        [f"outputs/wonder_feed/assets/lofi_run_20260823_060758_02/scene_{i:02d}.png" for i in range(1, 10)],
        [8],
        _gates(lone_stills),
    )

    old_loss = json.loads(
        Path("outputs/wonder_feed/clips/lofi_hold_loss_20260823_044007_v01.json").read_text(
            encoding="utf-8"
        )
    )
    work = "outputs/wonder_feed/assets/lofi_run_20260823_044007_01"
    images = []
    for i in range(1, 10):
        images.append(f"{work}/_regen_scene_05" if i == 5 else f"{work}/scene_{i:02d}.png")
    write_job(
        "core_engine/economic_reel_lofi/store/locked_script_loss_v1.json",
        "core_engine/economic_reel_lofi/store/locked_script_loss_v1_reuse.json",
        work,
        images,
        [],
        _gates(old_loss),
    )

    old_f = json.loads(
        Path("outputs/wonder_feed/clips/lofi_hold_forgiveness_20260823_044616_v03.json").read_text(
            encoding="utf-8"
        )
    )
    new_f = json.loads(
        Path("outputs/wonder_feed/clips/lofi_stills_forgiveness_20260823_060812_v03.json").read_text(
            encoding="utf-8"
        )
    )
    gates = _gates(old_f)
    gates.update(_gates(new_f))
    images = [
        "outputs/wonder_feed/assets/lofi_run_20260823_060812_03/scene_01.png",
        "outputs/wonder_feed/assets/lofi_run_20260823_044616_03/scene_02.png",
        "outputs/wonder_feed/assets/lofi_run_20260823_044616_03/scene_03.png",
        "outputs/wonder_feed/assets/lofi_run_20260823_044616_03/scene_04.png",
        "outputs/wonder_feed/assets/lofi_run_20260823_060812_03/scene_05.png",
        "outputs/wonder_feed/assets/lofi_run_20260823_044616_03/scene_06.png",
        "outputs/wonder_feed/assets/lofi_run_20260823_044616_03/scene_07.png",
        "outputs/wonder_feed/assets/lofi_run_20260823_044616_03/scene_08.png",
        "outputs/wonder_feed/assets/lofi_run_20260823_044616_03/_regen_scene_09",
    ]
    write_job(
        "core_engine/economic_reel_lofi/store/locked_script_forgiveness_v1.json",
        "core_engine/economic_reel_lofi/store/locked_script_forgiveness_v1_reuse.json",
        "outputs/wonder_feed/assets/lofi_run_20260823_044616_03",
        images,
        [2, 6],
        gates,
    )


if __name__ == "__main__":
    main()

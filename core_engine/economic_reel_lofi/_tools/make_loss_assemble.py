# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path

STILLS = Path("outputs/wonder_feed/clips/lofi_stills_loss_20260823_061347_v01.json")
WORK = "outputs/wonder_feed/assets/lofi_run_20260823_061347_01"
OUT = Path("core_engine/economic_reel_lofi/store/locked_script_loss_v1_assemble.json")


def main() -> None:
    stills = json.loads(STILLS.read_text(encoding="utf-8"))
    script = stills["script"]
    images = [f"{WORK}/scene_{i:02d}.png" for i in range(1, 10)]
    OUT.write_text(
        json.dumps(
            {
                "manual_accept_scenes": [],
                "work_dir": WORK,
                "scene_images": images,
                "script": script,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"wrote {OUT}")
    for i, p in enumerate(images, 1):
        print(f"  {i} REUSE {p}")


if __name__ == "__main__":
    main()

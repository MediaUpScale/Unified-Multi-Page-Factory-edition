# -*- coding: utf-8 -*-
"""Stamp signed-off structure v1 captions onto existing still envelopes."""
from __future__ import annotations

import json
from pathlib import Path

STORE = Path("core_engine/economic_reel_lofi/store")

PACK = {
    "forgiveness": "locked_script_forgiveness_v1_assemble.json",
    "loss": "locked_script_loss_v1_assemble.json",
    "loneliness": "locked_script_loneliness_v1_assemble.json",
}


def main() -> None:
    for theme, src_name in PACK.items():
        src = json.loads((STORE / src_name).read_text(encoding="utf-8"))
        v1 = json.loads((STORE / f"locked_script_{theme}_structure_v1.json").read_text(encoding="utf-8"))
        script = src["script"]
        caps = [str(r.get("text") or "") for r in v1["lines"]]
        if len(script["lines"]) != 9 or len(caps) != 9:
            raise SystemExit(f"{theme}: expected 9 lines")
        for row, text, vrow in zip(script["lines"], caps, v1["lines"]):
            row["text"] = text
            row["beat_text"] = text
            row["beat_function"] = vrow.get("beat_function") or row.get("beat_function")
        script["monologue"] = " ".join(caps)
        script["rhetoric_pattern"] = v1.get("rhetoric_pattern")
        script["rhetoric_hook_overlay"] = v1.get("rhetoric_hook_overlay") or ""
        script["thesis"] = v1.get("thesis") or script.get("thesis")
        out = {
            "manual_accept_scenes": list(range(1, 10)),
            "work_dir": src.get("work_dir"),
            "scene_images": src.get("scene_images"),
            "script": script,
        }
        dest = STORE / f"locked_script_{theme}_structure_v1_assemble.json"
        dest.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"wrote {dest.name}")
        for i, text in enumerate(caps, 1):
            print(f"  {i} {text}")


if __name__ == "__main__":
    main()

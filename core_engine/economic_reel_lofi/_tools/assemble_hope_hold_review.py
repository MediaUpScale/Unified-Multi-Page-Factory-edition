# -*- coding: utf-8 -*-
"""Assemble a review MP4 from a QA-hold package. Not postable."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core_engine.economic_reel_lofi import config as lofi_cfg  # noqa: E402
from core_engine.economic_reel_lofi.assembler import (  # noqa: E402
    assemble_lofi_reel,
    measure_vo_speech_duration,
)

HOLD = ROOT / "outputs" / "wonder_feed" / "clips" / "lofi_hold_hope_20260824_194933_v01.json"
OUT = ROOT / "outputs" / "wonder_feed" / "clips" / "lofi_reel_hope_20260824_194933_v01_review.mp4"


def main() -> int:
    hold = json.loads(HOLD.read_text(encoding="utf-8"))
    run_dir = Path(hold["work_dir"])
    lines = [r for r in (hold.get("script") or {}).get("lines") or [] if isinstance(r, dict)]
    captions = [str(r.get("text") or "") for r in lines]
    scenes = [run_dir / f"scene_{i:02d}.png" for i in range(1, len(captions) + 1)]
    voices = []
    durs = []
    beat = float(lofi_cfg.beat_duration_s())
    trail = float(lofi_cfg.VO_INTERLINE_SILENCE_S)
    n = len(captions)
    for i in range(n):
        vp = run_dir / f"vo_scene_{i + 1:02d}.mp3"
        voices.append(vp if vp.is_file() else None)
        vo_dur = float(measure_vo_speech_duration(vp)) if vp.is_file() else 0.0
        t = 0.0 if i >= n - 1 else trail
        dur_i, _ = lofi_cfg.slot_duration_for_vo(vo_dur, base_s=beat, trailing_silence_s=t)
        durs.append(dur_i)
        print(f"scene {i+1} vo={vo_dur:.2f}s slot={dur_i:.2f}s {captions[i]!r}")
    missing = [str(p) for p in scenes if not p.is_file()]
    if missing:
        print("missing stills", missing)
        return 1
    OUT.parent.mkdir(parents=True, exist_ok=True)
    assemble_lofi_reel(
        scenes,
        captions,
        OUT,
        engine_root=ROOT,
        page_id="wonder_feed",
        scene_duration_s=lofi_cfg.SCENE_DURATION_S,
        scene_durations=durs,
        caption_style=lofi_cfg.DEFAULT_CAPTION_STYLE,
        voice_paths=voices,
        word_timings_per_scene=None,
    )
    print("REVIEW MP4 (visual HOLD, not postable) ->", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

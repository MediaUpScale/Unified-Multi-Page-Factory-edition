# -*- coding: utf-8 -*-
"""Concat existing per-scene VO with the production edge-trim + 300ms pad; run silencedetect."""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from moviepy import concatenate_audioclips  # type: ignore  # noqa: E402

from core_engine.economic_reel_lofi import config as lofi_cfg  # noqa: E402
from core_engine.economic_reel_lofi.assembler import (  # noqa: E402
    _audio_clip_from_samples,
    _silence_audio_clip,
    normalize_vo_pcm,
)

RUN = ROOT / "outputs" / "wonder_feed" / "assets" / "lofi_run_20260824_175130_01"
OUT = ROOT / "outputs" / "wonder_feed" / "clips" / "vo_pace_probe" / "hope_concat_fixed_300ms.wav"


def silencedetect(path: Path, noise_db: float = -30.0, min_s: float = 0.15) -> list[float]:
    import imageio_ffmpeg

    exe = imageio_ffmpeg.get_ffmpeg_exe()
    cmd = [
        exe,
        "-hide_banner",
        "-i",
        str(path),
        "-af",
        f"silencedetect=noise={noise_db}dB:d={min_s}",
        "-f",
        "null",
        "-",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    blob = (proc.stderr or "") + (proc.stdout or "")
    durs = [float(x) for x in re.findall(r"silence_duration:\s*([0-9.]+)", blob)]
    return durs


def main() -> int:
    clips = []
    gap_s = float(lofi_cfg.VO_INTERLINE_SILENCE_S)
    files = sorted(RUN.glob("vo_scene_*.mp3"))
    if not files:
        print("no vo_scene_*.mp3 in", RUN)
        return 1
    to_close = []
    for i, path in enumerate(files):
        arr, sr, meta = normalize_vo_pcm(path)
        vac = _audio_clip_from_samples(arr, sr)
        to_close.append(vac)
        clips.append(vac)
        print(
            f"{path.name} file={float(meta.get('file_duration_s') or 0):.3f}s "
            f"norm={float(meta.get('duration_s') or 0):.3f}s "
            f"internal_flat={meta.get('internal_flat')}"
        )
        if i < len(files) - 1:
            sil = _silence_audio_clip(gap_s)
            to_close.append(sil)
            clips.append(sil)
    full = concatenate_audioclips(clips)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    full.write_audiofile(str(OUT), logger=None)
    print("wrote", OUT, f"dur={float(full.duration or 0):.3f}s")
    for c in to_close:
        try:
            c.close()
        except Exception:
            pass
    durs = silencedetect(OUT)
    print("silencedetect noise=-30dB min=0.15s durations_ms=", [round(d * 1000) for d in durs])
    inter = [d for d in durs if 0.20 <= d <= 0.45]
    outliers = [d for d in durs if d < 0.27 or d > 0.33]
    print(f"n_gaps={len(durs)} in_270_330ms={sum(1 for d in durs if 0.27 <= d <= 0.33)}")
    print("outliers_outside_270_330ms_ms=", [round(d * 1000) for d in outliers])
    print("cluster_200_450ms_ms=", [round(d * 1000) for d in inter])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

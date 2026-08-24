# -*- coding: utf-8 -*-
"""Isolated one-line VO pace probe. No video, no assembly."""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from avatar_engine.audio_engine import (
    apply_elevenlabs_voice_settings,
    generate_voiceover_with_timestamps,
    _audio_file_duration_s,
)
from core_engine.economic_reel_lofi import config as lofi_cfg
import imageio_ffmpeg

LINE = "Someone waited through rain to see that gap open"
WORDS = len(LINE.split())
OUT_DIR = ROOT / "outputs" / "wonder_feed" / "clips" / "vo_pace_probe"
SPEEDS = (0.70, 0.65, 0.60)
VOICE_ID = str(lofi_cfg.LOFI_VOICE_ID)


def _ffmpeg() -> str:
    return imageio_ffmpeg.get_ffmpeg_exe()


def silencedetect(path: Path, noise_db: float = -30.0, min_s: float = 0.15) -> dict:
    cmd = [
        _ffmpeg(),
        "-hide_banner",
        "-i",
        str(path),
        "-af",
        f"silencedetect=noise={noise_db}dB:d={min_s}",
        "-f",
        "null",
        "-",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    blob = (proc.stderr or "") + "\n" + (proc.stdout or "")
    starts = [float(x) for x in re.findall(r"silence_start:\s*([0-9.]+)", blob)]
    ends = [float(x) for x in re.findall(r"silence_end:\s*([0-9.]+)", blob)]
    durs = [float(x) for x in re.findall(r"silence_duration:\s*([0-9.]+)", blob)]
    if not durs and starts and ends:
        n = min(len(starts), len(ends))
        durs = [ends[i] - starts[i] for i in range(n)]
    total_gap = sum(max(0.0, d) for d in durs)
    longest = max(durs) if durs else 0.0
    return {
        "starts": starts,
        "ends": ends,
        "durs": [round(d, 3) for d in durs],
        "longest_s": round(longest, 3),
        "total_gap_s": round(total_gap, 3),
        "n_gaps": len(durs),
        "raw_tail": blob[-1200:],
    }


def synth(speed: float) -> dict:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"isolated_rain_line_speed_{speed:.2f}.mp3"
    native = speed >= 0.70
    if native:
        if not out.is_file():
            apply_elevenlabs_voice_settings(
                VOICE_ID,
                speed=speed,
                stability=1.0,
                similarity_boost=1.0,
                style=0.0,
                use_speaker_boost=True,
            )
            generate_voiceover_with_timestamps(
                LINE,
                out,
                voice_id=VOICE_ID,
                force_elevenlabs=True,
                expressive_mode=False,
                enable_ssml=False,
                speed=speed,
                voice_settings={
                    "stability": 1.0,
                    "similarity_boost": 1.0,
                    "style": 0.0,
                    "use_speaker_boost": True,
                    "speed": speed,
                },
            )
        method = "native_tts"
    else:
        src = OUT_DIR / "isolated_rain_line_speed_0.70.mp3"
        if not src.is_file():
            raise FileNotFoundError(src)
        # atempo < 1 slows speech. Native 0.65 duration ~= 0.70 * (0.70/0.65).
        factor = float(speed) / 0.70
        cmd = [
            _ffmpeg(),
            "-y",
            "-hide_banner",
            "-i",
            str(src),
            "-filter:a",
            f"atempo={factor:.4f}",
            str(out),
        ]
        subprocess.run(cmd, check=True, capture_output=True)
        method = (
            f"atempo_from_0.70 factor={factor:.3f} "
            f"(ElevenLabs settings/edit floor is 0.70; {speed:.2f} cannot be native)"
        )
    dur = float(_audio_file_duration_s(out))
    sil = silencedetect(out)
    wps = WORDS / dur if dur > 0.05 else 0.0
    rec = {
        "speed": speed,
        "method": method,
        "path": str(out),
        "words": WORDS,
        "duration_s": round(dur, 3),
        "wps": round(wps, 3),
        "longest_silence_s": sil["longest_s"],
        "total_gap_s": sil["total_gap_s"],
        "gap_pct": round(100.0 * sil["total_gap_s"] / dur, 1) if dur else 0.0,
        "n_gaps": sil["n_gaps"],
        "gap_durs": sil["durs"],
    }
    print(json.dumps(rec, indent=2))
    return rec


def probe_existing_vo_concat() -> dict:
    """VO files from the hope assemble, concatenated, no BGM."""
    run = ROOT / "outputs" / "wonder_feed" / "assets" / "lofi_run_20260824_143339_01"
    files = [run / f"vo_scene_{i:02d}.mp3" for i in range(1, 10)]
    files = [p for p in files if p.is_file()]
    if not files:
        return {"error": "no vo files"}
    list_path = OUT_DIR / "concat_list.txt"
    concat_out = OUT_DIR / "hope_vo_only_concat.mp3"
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    list_path.write_text(
        "\n".join(f"file '{p.resolve().as_posix()}'" for p in files),
        encoding="utf-8",
    )
    cmd = [
        _ffmpeg(),
        "-y",
        "-hide_banner",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(list_path),
        "-c",
        "copy",
        str(concat_out),
    ]
    subprocess.run(cmd, capture_output=True, text=True)
    dur = float(_audio_file_duration_s(concat_out))
    sil = silencedetect(concat_out)
    rec = {
        "path": str(concat_out),
        "n_files": len(files),
        "duration_s": round(dur, 3),
        "longest_silence_s": sil["longest_s"],
        "total_gap_s": sil["total_gap_s"],
        "gap_pct": round(100.0 * sil["total_gap_s"] / dur, 1) if dur else 0.0,
        "n_gaps": sil["n_gaps"],
        "gap_durs": sil["durs"],
    }
    print("[concat VO-only]", json.dumps(rec, indent=2))
    return rec


def probe_reel_mix() -> dict:
    mp4 = ROOT / "outputs" / "wonder_feed" / "clips" / "lofi_reel_hope_20260824_143339_v01.mp4"
    if not mp4.is_file():
        return {"error": "no mp4"}
    sil = silencedetect(mp4)
    rec = {
        "path": str(mp4),
        "longest_silence_s": sil["longest_s"],
        "total_gap_s": sil["total_gap_s"],
        "n_gaps": sil["n_gaps"],
        "gap_durs": sil["durs"][:20],
    }
    print("[mixed MP4]", json.dumps(rec, indent=2))
    return rec


def main() -> int:
    rows = [synth(s) for s in SPEEDS]
    apply_elevenlabs_voice_settings(
        VOICE_ID,
        speed=0.70,
        stability=1.0,
        similarity_boost=1.0,
        style=0.0,
        use_speaker_boost=True,
    )
    concat = probe_existing_vo_concat()
    mixed = probe_reel_mix()
    summary = {"isolated": rows, "vo_only_concat": concat, "mixed_mp4": mixed}
    out = OUT_DIR / "pace_probe_summary.json"
    out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print("wrote", out)
    print("\nSIDE BY SIDE")
    print(f"{'speed':<8}{'dur':>8}{'wps':>8}{'longest':>10}{'gap%':>8}{'n_gaps':>8}")
    for r in rows:
        print(
            f"{r['speed']:<8.2f}{r['duration_s']:>8.3f}{r['wps']:>8.3f}"
            f"{r['longest_silence_s']:>10.3f}{r['gap_pct']:>7.1f}%{r['n_gaps']:>8}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

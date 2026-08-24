# -*- coding: utf-8 -*-
"""Isolated ~10s probe: TTS breath commas + real inter-line silence. No video."""
from __future__ import annotations

import json
import math
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from avatar_engine.audio_engine import (  # noqa: E402
    apply_elevenlabs_voice_settings,
    generate_voiceover_with_timestamps,
    _audio_file_duration_s,
)
from core_engine.economic_reel_lofi import config as lofi_cfg  # noqa: E402
from core_engine.economic_reel_lofi._tools.vo_pace_probe import silencedetect  # noqa: E402
from core_engine.economic_reel_lofi.pipeline import (  # noqa: E402
    _tts_breath_commas,
    _tts_text_with_breaks,
)
import imageio_ffmpeg

# Approved hope 9-liner excerpts — enough for ~10s with two 300ms gaps.
LINES = [
    "One night I almost let it go dark",
    "But I paid for it with small stubborn hours",
    "When the light held, I finally believed it",
]
OUT_DIR = ROOT / "outputs" / "wonder_feed" / "clips" / "vo_pace_probe"
GAP_S = float(lofi_cfg.VO_INTERLINE_SILENCE_S)
VOICE_ID = str(lofi_cfg.LOFI_VOICE_ID)


def _ffmpeg() -> str:
    return imageio_ffmpeg.get_ffmpeg_exe()


def _make_silence(path: Path, seconds: float) -> None:
    cmd = [
        _ffmpeg(),
        "-y",
        "-hide_banner",
        "-f",
        "lavfi",
        "-i",
        "anullsrc=r=44100:cl=mono",
        "-t",
        f"{seconds:.3f}",
        "-q:a",
        "9",
        str(path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def _concat(files: list[Path], out: Path) -> None:
    lst = out.with_suffix(".txt")
    lst.write_text(
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
        str(lst),
        "-c:a",
        "libmp3lame",
        "-q:a",
        "4",
        str(out),
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def _expand_duck_windows(
    vo: Path,
    planned: list[tuple[float, float]],
    *,
    min_gap_s: float = 0.20,
) -> list[tuple[float, float]]:
    """Duck actual VO silences (SSML + inter-line), not just file-duration pads.

    TTS files often carry 150–250ms of trailing hush, so the 300ms insert
    merges and starts earlier than the planned window. silencedetect on the
    raw concat is the source of truth.
    """
    sil = silencedetect(vo, noise_db=-30.0, min_s=min_gap_s)
    detected = list(zip(sil["starts"], sil["ends"]))
    out: list[tuple[float, float]] = []
    for a, b in detected:
        if (float(b) - float(a)) >= min_gap_s:
            out.append((max(0.0, float(a) - 0.04), float(b) + 0.04))
    if not out:
        out = [(max(0.0, a - 0.28), b + 0.05) for a, b in planned]
    return out


def _mix_ducked(vo: Path, bgm: Path, out: Path, windows: list[tuple[float, float]]) -> None:
    full = float(lofi_cfg.BGM_VOLUME)
    gap = float(lofi_cfg.BGM_GAP_VOLUME)
    duck = _expand_duck_windows(vo, windows)
    parts = [f"{full}"]
    for a, b in duck:
        parts.append(f"if(between(t,{a:.3f},{b:.3f}),{gap},{parts[-1]})")
    expr = parts[-1] if len(parts) > 1 else str(full)
    # normalize=0: silent VO must not boost remaining BGM above -30dB.
    cmd = [
        _ffmpeg(),
        "-y",
        "-hide_banner",
        "-i",
        str(vo),
        "-i",
        str(bgm),
        "-filter_complex",
        f"[1:a]volume='{expr}':eval=frame[bg];[0:a][bg]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[out]",
        "-map",
        "[out]",
        "-c:a",
        "libmp3lame",
        "-q:a",
        "4",
        str(out),
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    apply_elevenlabs_voice_settings(
        VOICE_ID,
        speed=0.70,
        stability=1.0,
        similarity_boost=1.0,
        style=0.0,
        use_speaker_boost=True,
    )
    plan = []
    files: list[Path] = []
    words = 0
    for i, line in enumerate(LINES, 1):
        breathed = _tts_breath_commas(line)
        tts = _tts_text_with_breaks(breathed)
        rec = {
            "i": i,
            "caption": line,
            "breathed": breathed,
            "tts": tts,
            "already_had_comma": "," in line,
            "comma_added": breathed != line,
        }
        print(json.dumps(rec, ensure_ascii=False))
        plan.append(rec)
        words += len(line.split())
        out = OUT_DIR / f"pause_probe_line_{i:02d}.mp3"
        generate_voiceover_with_timestamps(
            tts,
            out,
            voice_id=VOICE_ID,
            force_elevenlabs=True,
            expressive_mode=False,
            enable_ssml="<break" in tts,
            speed=0.70,
            voice_settings={
                "stability": 1.0,
                "similarity_boost": 1.0,
                "style": 0.0,
                "use_speaker_boost": True,
                "speed": 0.70,
            },
        )
        rec["duration_s"] = round(float(_audio_file_duration_s(out)), 3)
        rec["line_sil"] = silencedetect(out)
        files.append(out)

    sil = OUT_DIR / "pause_probe_gap_300ms.mp3"
    _make_silence(sil, GAP_S)
    concat_files: list[Path] = []
    windows: list[tuple[float, float]] = []
    t = 0.0
    for i, f in enumerate(files):
        concat_files.append(f)
        t += float(_audio_file_duration_s(f))
        if i < len(files) - 1:
            windows.append((round(t, 3), round(t + GAP_S, 3)))
            concat_files.append(sil)
            t += GAP_S
    concat_out = OUT_DIR / "pause_probe_vo_concat.mp3"
    _concat(concat_files, concat_out)
    dur = float(_audio_file_duration_s(concat_out))
    sil_rep = silencedetect(concat_out)
    wps = words / dur if dur > 0.05 else 0.0

    mixed_rep = None
    from core_engine.economic_reel_lofi.assembler import list_library_bgm_tracks

    tracks = list_library_bgm_tracks(ROOT)
    if tracks:
        mixed = OUT_DIR / "pause_probe_vo_plus_bgm_ducked.mp3"
        _mix_ducked(concat_out, tracks[0], mixed, windows)
        mixed_rep = {
            "path": str(mixed),
            "duration_s": round(float(_audio_file_duration_s(mixed)), 3),
            **silencedetect(mixed),
        }

    summary = {
        "lines": plan,
        "gap_s": GAP_S,
        "planned_windows": windows,
        "vo_concat": {
            "path": str(concat_out),
            "words": words,
            "duration_s": round(dur, 3),
            "wps": round(wps, 3),
            "longest_silence_s": sil_rep["longest_s"],
            "total_gap_s": sil_rep["total_gap_s"],
            "gap_pct": round(100.0 * sil_rep["total_gap_s"] / dur, 1) if dur else 0.0,
            "n_gaps": sil_rep["n_gaps"],
            "gap_durs": sil_rep["durs"],
            "gap_starts": sil_rep["starts"],
        },
        "mixed_ducked": mixed_rep,
    }
    outp = OUT_DIR / "pause_probe_summary.json"
    outp.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print("wrote", outp)
    vc = summary["vo_concat"]
    print(
        f"VO concat dur={vc['duration_s']}s wps={vc['wps']} "
        f"gaps={vc['gap_durs']} longest={vc['longest_silence_s']} "
        f"gap%={vc['gap_pct']}"
    )
    if mixed_rep:
        print(
            f"MIXED ducked gaps={mixed_rep['durs']} "
            f"longest={mixed_rep['longest_s']} n={mixed_rep['n_gaps']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

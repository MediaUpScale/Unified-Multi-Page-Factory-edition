# -*- coding: utf-8 -*-
"""Full 9-line hope VO concat with breath commas + 300ms interline silence. No video."""
from __future__ import annotations

import json
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
from core_engine.economic_reel_lofi.assembler import list_library_bgm_tracks  # noqa: E402
from core_engine.economic_reel_lofi.pipeline import (  # noqa: E402
    _tts_breath_commas,
    _tts_text_with_breaks,
)
from core_engine.economic_reel_lofi._tools.vo_pause_probe import (  # noqa: E402
    _concat,
    _make_silence,
    _mix_ducked,
)

LINES = [
    "Hope was never a speech I gave myself",
    "So I stopped waiting for one big morning",
    "I kept leaving that window lit anyway",
    "One night I almost let it go dark",
    "But I paid for it with small stubborn hours",
    "Because staying lit costs more than saying so",
    "When the light held, I finally believed it",
    "Then the street noticed what I kept doing",
    "The window stayed lit because I stayed first",
]
OUT_DIR = ROOT / "outputs" / "wonder_feed" / "clips" / "vo_pace_probe"
GAP_S = float(lofi_cfg.VO_INTERLINE_SILENCE_S)
VOICE_ID = str(lofi_cfg.LOFI_VOICE_ID)


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
    files: list[Path] = []
    rows = []
    words = 0
    for i, line in enumerate(LINES, 1):
        breathed = _tts_breath_commas(line)
        tts = _tts_text_with_breaks(breathed)
        out = OUT_DIR / f"hope9_vo_{i:02d}.mp3"
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
        dur = float(_audio_file_duration_s(out))
        rec = {
            "scene": i,
            "caption": line,
            "breathed": breathed,
            "tts": tts,
            "duration_s": round(dur, 3),
            "line_sil": silencedetect(out),
        }
        print(json.dumps({k: rec[k] for k in ("scene", "breathed", "duration_s")}, ensure_ascii=False))
        rows.append(rec)
        files.append(out)
        words += len(line.split())

    sil = OUT_DIR / "pause_probe_gap_300ms.mp3"
    if not sil.is_file():
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
    concat_out = OUT_DIR / "hope9_vo_concat.mp3"
    _concat(concat_files, concat_out)
    dur = float(_audio_file_duration_s(concat_out))
    sil_rep = silencedetect(concat_out)

    mixed_rep = None
    tracks = list_library_bgm_tracks(ROOT)
    if tracks:
        mixed = OUT_DIR / "hope9_vo_plus_bgm_ducked.mp3"
        _mix_ducked(concat_out, tracks[0], mixed, windows)
        mixed_rep = {
            "path": str(mixed),
            "duration_s": round(float(_audio_file_duration_s(mixed)), 3),
            **silencedetect(mixed),
        }

    # Inter-line gaps only: those whose start is near a planned window
    interline = []
    for a, b in windows:
        hits = [
            d
            for s, d in zip(sil_rep["starts"], sil_rep["durs"])
            if abs(float(s) - float(a)) < 0.20 or (float(s) >= a - 0.05 and float(s) < b)
        ]
        interline.append(
            {
                "planned": [a, b],
                "detected_durs": hits,
                "ok": any(h >= 0.25 for h in hits),
            }
        )

    summary = {
        "lines": rows,
        "gap_s": GAP_S,
        "planned_windows": windows,
        "interline_check": interline,
        "vo_concat": {
            "path": str(concat_out),
            "words": words,
            "duration_s": round(dur, 3),
            "wps": round(words / dur, 3) if dur else 0,
            "longest_silence_s": sil_rep["longest_s"],
            "total_gap_s": sil_rep["total_gap_s"],
            "gap_pct": round(100.0 * sil_rep["total_gap_s"] / dur, 1) if dur else 0.0,
            "n_gaps": sil_rep["n_gaps"],
            "gap_durs": sil_rep["durs"],
            "gap_starts": sil_rep["starts"],
        },
        "mixed_ducked": mixed_rep,
    }
    outp = OUT_DIR / "hope9_vo_concat_summary.json"
    outp.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print("wrote", outp)
    vc = summary["vo_concat"]
    print(
        f"9-line VO dur={vc['duration_s']}s wps={vc['wps']} "
        f"n_gaps={vc['n_gaps']} longest={vc['longest_silence_s']} gap%={vc['gap_pct']}"
    )
    print("interline_ok", [x["ok"] for x in interline])
    if mixed_rep:
        print(
            f"MIXED n={mixed_rep['n_gaps']} durs={mixed_rep['durs']} "
            f"longest={mixed_rep['longest_s']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# -*- coding: utf-8 -*-
"""Short v2 speed samples for listen-pick. No video."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from avatar_engine.audio_engine import (  # noqa: E402
    generate_voiceover_with_timestamps,
    _audio_file_duration_s,
)
from core_engine.economic_reel_lofi import config as lofi_cfg  # noqa: E402
import imageio_ffmpeg  # noqa: E402

LINE = "Hope was never a speech I gave myself"
OUT = ROOT / "outputs" / "wonder_feed" / "clips" / "vo_pace_probe"
VOICE = str(lofi_cfg.LOFI_VOICE_ID)
MODEL = str(lofi_cfg.LOFI_TTS_MODEL)
WORDS = len(LINE.split())


def _dur(path: Path) -> float:
    ff = imageio_ffmpeg.get_ffmpeg_exe()
    r = subprocess.run(
        [ff, "-hide_banner", "-i", str(path), "-f", "null", "-"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    blob = (r.stderr or "") + (r.stdout or "")
    for line in blob.splitlines():
        if "Duration:" in line:
            print(path.name, line.strip())
            break
    return float(_audio_file_duration_s(path))


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    for speed in (0.80, 0.85, 0.90):
        name = f"lofi_voice_sample_v2_speed_{str(speed).replace('.', '_')}.mp3"
        out = OUT / name
        generate_voiceover_with_timestamps(
            LINE,
            out,
            voice_id=VOICE,
            model_id=MODEL,
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
        d = _dur(out)
        rows.append(
            {
                "speed": speed,
                "duration_s": round(d, 3),
                "wps": round(WORDS / d, 3) if d else 0,
                "file": str(out),
            }
        )
    summary = {
        "line": LINE,
        "model": MODEL,
        "ref_0_70_s": 3.34,
        "ref_1_00_s": 2.32,
        "samples": rows,
    }
    outp = OUT / "lofi_voice_speed_listen.json"
    outp.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

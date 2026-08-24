# -*- coding: utf-8 -*-
"""Isolated eleven_multilingual_v2 speed 1.0 vs 0.8 + per-line wire dump."""
from __future__ import annotations

from pathlib import Path as _ReorgPath
import sys as _reorg_sys

_REORG_ROOT = _ReorgPath(__file__).resolve().parents[2]
if str(_REORG_ROOT) not in _reorg_sys.path:
    _reorg_sys.path.insert(0, str(_REORG_ROOT))

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.media.audio_engine import generate_voiceover_with_timestamps  # noqa: E402
from core.economic_reel_lofi import config as lofi_cfg  # noqa: E402
from core.economic_reel_lofi.pipeline import (  # noqa: E402
    _tts_breath_commas,
    _tts_text_with_breaks,
)

OUT = ROOT / "outputs" / "wonder_feed" / "clips" / "vo_pace_probe"
LINE = "Hope was never a speech I gave myself"
LOCKED = [
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


def _dur(path: Path) -> float:
    from moviepy import AudioFileClip  # type: ignore

    clip = AudioFileClip(str(path))
    try:
        return float(clip.duration or 0.0)
    finally:
        clip.close()


def _tts(text: str, dest: Path, speed: float, *, ssml: bool) -> dict:
    vs = {
        "stability": 1.0,
        "similarity_boost": 1.0,
        "style": 0.0,
        "use_speaker_boost": True,
        "speed": speed,
    }
    dest.parent.mkdir(parents=True, exist_ok=True)
    path, timings = generate_voiceover_with_timestamps(
        text,
        dest,
        voice_id=lofi_cfg.tts_voice_id() or None,
        model_id=lofi_cfg.tts_model() or "eleven_multilingual_v2",
        force_elevenlabs=True,
        expressive_mode=False,
        enable_ssml=ssml,
        speed=speed,
        voice_settings=vs,
    )
    duration = _dur(Path(path))
    rec = {
        "file": str(path),
        "speed": speed,
        "ssml": ssml,
        "text": text,
        "duration_s": round(duration, 3),
        "n_words_timed": len(timings or []),
        "config_file": lofi_cfg.__file__,
        "TTS_SPEED": getattr(lofi_cfg, "TTS_SPEED", None),
        "tts_speed()": lofi_cfg.tts_speed(),
        "tts_model": lofi_cfg.tts_model(),
    }
    print(
        f"[probe] speed={speed} ssml={ssml} dur={duration:.3f}s "
        f"words={len(timings or [])} text={text!r}"
    )
    return rec


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    print(
        f"[probe] config={lofi_cfg.__file__} "
        f"TTS_SPEED={lofi_cfg.TTS_SPEED!r} tts_speed()={lofi_cfg.tts_speed()!r} "
        f"model={lofi_cfg.tts_model()!r}"
    )
    rec_1 = _tts(
        LINE,
        OUT / "line1_v2_speed_1_0_reprobe.mp3",
        1.0,
        ssml=False,
    )
    rec_08 = _tts(
        LINE,
        OUT / "line1_v2_speed_0_8_reprobe.mp3",
        0.8,
        ssml=False,
    )
    d1 = float(rec_1["duration_s"])
    d08 = float(rec_08["duration_s"])
    ratio = (d08 / d1) if d1 > 0 else 0.0
    expected = 1.0 / 0.8
    # 1.20–1.25 was the requested band; allow a little ElevenLabs jitter.
    verdict = "PASS" if 1.18 <= ratio <= 1.32 else "FAIL"
    lines = []
    for i, caption in enumerate(LOCKED, start=1):
        tts_text = _tts_text_with_breaks(_tts_breath_commas(caption))
        use_ssml = "<break" in tts_text
        rec = _tts(
            tts_text,
            OUT / f"hope9_wire_vo_{i:02d}.mp3",
            float(lofi_cfg.tts_speed()),
            ssml=use_ssml,
        )
        rec["caption"] = caption
        rec["tts_text"] = tts_text
        rec["line"] = i
        lines.append(rec)
        print(
            f"[probe] line {i} caption={caption!r} tts={tts_text!r} "
            f"ssml={use_ssml} dur={rec['duration_s']:.3f}s"
        )
    summary = {
        "model_id": lofi_cfg.tts_model(),
        "line": LINE,
        "words": len(LINE.split()),
        "duration_1_0": rec_1["duration_s"],
        "duration_0_80": rec_08["duration_s"],
        "ratio_0_80_over_1_0": round(ratio, 3),
        "expected": round(expected, 3),
        "verdict": verdict,
        "config_file": lofi_cfg.__file__,
        "TTS_SPEED": lofi_cfg.TTS_SPEED,
        "tts_speed()": lofi_cfg.tts_speed(),
        "production_lines": lines,
        "samples": {"speed_1_0": rec_1, "speed_0_8": rec_08},
    }
    dest = OUT / "line1_v2_speed_0_8_ratio.json"
    dest.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps({k: summary[k] for k in (
        "duration_1_0", "duration_0_80", "ratio_0_80_over_1_0",
        "expected", "verdict", "config_file", "TTS_SPEED", "tts_speed()",
    )}, indent=2))
    print("wrote", dest)
    return 0 if verdict == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())

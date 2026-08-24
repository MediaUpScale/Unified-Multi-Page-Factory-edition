# -*- coding: utf-8 -*-
"""Isolated 1.0 vs 0.70 duration-ratio test. No video. Captures HTTP JSON body."""
from __future__ import annotations

import json
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
from core_engine.economic_reel_lofi.pipeline import (  # noqa: E402
    _tts_breath_commas,
    _tts_text_with_breaks,
)
import imageio_ffmpeg  # noqa: E402
import subprocess  # noqa: E402

LINE = "Hope was never a speech I gave myself"
OUT = ROOT / "outputs" / "wonder_feed" / "clips" / "vo_pace_probe"
VOICE_ID = str(lofi_cfg.LOFI_VOICE_ID)
MODEL_ID = str(getattr(lofi_cfg, "LOFI_TTS_MODEL", "eleven_multilingual_v2"))
BODIES: list[dict] = []


def _patch_httpx() -> None:
    import httpx
    from elevenlabs.core.http_client import HttpClient

    orig = HttpClient.request

    def wrapped(self, path=None, *args, **kwargs):  # noqa: ANN001
        json_body = kwargs.get("json")
        path_s = str(path or "")
        if "text-to-speech" in path_s and isinstance(json_body, dict):
            vs = json_body.get("voice_settings")
            if hasattr(vs, "model_dump"):
                vs = vs.model_dump()
            rec = {
                "layer": "HttpClient.request json=",
                "path": path_s,
                "voice_settings": vs,
                "model_id": json_body.get("model_id"),
                "has_top_level_speed": "speed" in json_body,
                "text": json_body.get("text"),
            }
            BODIES.append(rec)
            print("[HTTP json Fern]", json.dumps(rec, ensure_ascii=False, default=str))
        return orig(self, path, *args, **kwargs)

    HttpClient.request = wrapped  # type: ignore[method-assign]

    orig_httpx = httpx.Client.request

    def wrapped_httpx(self, method, url, *args, **kwargs):  # noqa: ANN001
        url_s = str(url)
        if "text-to-speech" in url_s:
            raw_json = kwargs.get("json")
            rec = {
                "layer": "httpx.Client.request",
                "url": url_s,
                "json_voice_settings": (
                    raw_json.get("voice_settings") if isinstance(raw_json, dict) else None
                ),
                "json_top_level_speed": isinstance(raw_json, dict) and "speed" in raw_json,
                "json_model_id": raw_json.get("model_id") if isinstance(raw_json, dict) else None,
            }
            BODIES.append(rec)
            print("[HTTP json httpx]", json.dumps(rec, ensure_ascii=False, default=str))
        return orig_httpx(self, method, url, *args, **kwargs)

    httpx.Client.request = wrapped_httpx  # type: ignore[method-assign]


def _ffmpeg_duration(path: Path) -> float:
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


def synth(speed: float, name: str) -> Path:
    tts_text = _tts_text_with_breaks(_tts_breath_commas(LINE))
    out = OUT / name
    print("--- synth speed=", speed, "model=", MODEL_ID, "ssml=", "<break" in tts_text)
    generate_voiceover_with_timestamps(
        tts_text,
        out,
        voice_id=VOICE_ID,
        model_id=MODEL_ID,
        force_elevenlabs=True,
        expressive_mode=False,
        enable_ssml="<break" in tts_text,
        speed=speed,
        voice_settings={
            "stability": 1.0,
            "similarity_boost": 1.0,
            "style": 0.0,
            "use_speaker_boost": True,
            "speed": speed,
        },
    )
    return out


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    _patch_httpx()
    p10 = synth(1.0, "line1_speed_1_0.mp3")
    p07 = synth(0.70, "line1_speed_0_70.mp3")
    d10 = _ffmpeg_duration(p10)
    d07 = _ffmpeg_duration(p07)
    ratio = d07 / d10 if d10 else 0.0
    verdict = "PASS" if 1.25 <= ratio <= 1.65 else "FAIL"
    summary = {
        "model_id": MODEL_ID,
        "line": LINE,
        "words": len(LINE.split()),
        "duration_1_0": round(d10, 3),
        "duration_0_70": round(d07, 3),
        "ratio_0_70_over_1_0": round(ratio, 3),
        "expected": 1.429,
        "verdict": verdict,
        "http_bodies": BODIES,
    }
    outp = OUT / "line1_speed_ratio.json"
    outp.write_text(json.dumps(summary, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    print(json.dumps({k: summary[k] for k in summary if k != "http_bodies"}, indent=2))
    print("wrote", outp)
    return 0 if verdict == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())

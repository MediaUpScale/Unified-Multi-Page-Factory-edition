# -*- coding: utf-8 -*-
"""
Lightweight audio-engine verification (no full video pipeline).

Tests:
  1. TTS tag sanitization + speed=0.92
  2. Cinematic SFX braam impact
  3. Music API payload (MusicPrompt / 422 -> simple-prompt fallback)

Usage:
  python test_audio_engine.py
"""
from __future__ import annotations

import re
import sys
import traceback
from pathlib import Path

# Project root on sys.path
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import avatar_engine.audio_engine as _audio_engine  # noqa: E402
from avatar_engine.audio_engine import (  # noqa: E402
    _MUSIC_SIMPLE_PROMPT,
    _clamp_music_duration_ms,
    _is_unprocessable_entity,
    _sanitize_style_list,
    _styles_to_csv,
    build_mei_music_v1_plan,
    build_mei_music_v2_plan,
    generate_impact_sfx,
    generate_music_v2_bed,
    generate_voiceover,
    strip_tts_markers,
)

OUT_DIR = ROOT / "outputs"
TTS_OUT = OUT_DIR / "test_tts_clean.mp3"
SFX_OUT = OUT_DIR / "test_sfx_braam.mp3"
MUSIC_OUT = OUT_DIR / "test_music_track.mp3"

TTS_RAW = (
    "[stoic] The modern world offers a subtle illusion of choice... "
    "[deep voice] Master Mei commands your focus."
)
TTS_SPEED = 0.92

POSITIVE_STYLES_CSV = (
    "dark ambient drone, heavy sub-bass drone, dystopian cybernetic atmosphere"
)
NEGATIVE_STYLES_CSV = "upbeat, bright synth, vocals"


def _ok(label: str, detail: str = "") -> None:
    suffix = f" — {detail}" if detail else ""
    print(f"[PASS] {label}{suffix}")


def _fail(label: str, exc: BaseException) -> None:
    print(f"[FAIL] {label} — {type(exc).__name__}: {exc}")
    traceback.print_exc()


def _file_ok(path: Path, *, min_bytes: int = 1000) -> bool:
    return path.is_file() and path.stat().st_size >= min_bytes


def test_1_tts_sanitization_and_speed() -> bool:
    print("\n=== Test 1: TTS Tag Sanitization & Speed ===")
    try:
        # Explicit regex filter (same rule as production)
        regex_clean = re.sub(r"\[.*?\]", "", TTS_RAW)
        engine_clean = strip_tts_markers(TTS_RAW)
        if "[" in regex_clean or "]" in regex_clean:
            raise AssertionError(f"regex left brackets: {regex_clean!r}")
        if "[" in engine_clean or "]" in engine_clean:
            raise AssertionError(f"strip_tts_markers left brackets: {engine_clean!r}")
        if "stoic" in engine_clean.lower() or "deep voice" in engine_clean.lower():
            raise AssertionError(f"tag words leaked into TTS text: {engine_clean!r}")

        _ok("TTS sanitize (regex)", f"-> {regex_clean.strip()[:72]!r}...")
        _ok("TTS sanitize (strip_tts_markers)", f"-> {engine_clean[:72]!r}...")

        OUT_DIR.mkdir(parents=True, exist_ok=True)
        TTS_OUT.unlink(missing_ok=True)
        path = generate_voiceover(
            TTS_RAW,
            TTS_OUT,
            speed=TTS_SPEED,
            expressive_mode=True,
        )
        if not _file_ok(Path(path)):
            raise RuntimeError(f"TTS output missing/too small: {path}")
        kb = Path(path).stat().st_size / 1024
        _ok("TTS generate", f"speed={TTS_SPEED} -> {TTS_OUT.name} ({kb:.1f} KB)")
        print(f"[OK]   TTS module SUCCESS -> {TTS_OUT}")
        return True
    except Exception as exc:  # noqa: BLE001
        _fail("TTS module", exc)
        print("[ERR]  TTS module FAILED")
        return False


def test_2_sfx_braam() -> bool:
    print("\n=== Test 2: Cinematic SFX Impact ===")
    try:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        SFX_OUT.unlink(missing_ok=True)
        path = generate_impact_sfx(
            SFX_OUT,
            prompt="Cinematic Braam, Dystopian Sub-Bass Drop",
            duration_seconds=2.5,
        )
        if path is None or not _file_ok(Path(path), min_bytes=500):
            raise RuntimeError(f"SFX braam failed or empty: {path}")
        kb = Path(path).stat().st_size / 1024
        _ok("SFX braam", f"-> {SFX_OUT.name} ({kb:.1f} KB)")
        print(f"[OK]   SFX module SUCCESS -> {SFX_OUT}")
        return True
    except Exception as exc:  # noqa: BLE001
        _fail("SFX module", exc)
        print("[ERR]  SFX module FAILED")
        return False


def test_3_music_api_payload() -> bool:
    print("\n=== Test 3: Eleven Music API Fix (music_v2 / music_v1) ===")
    try:
        # Flat CSV styles -> list (SDK) + CSV round-trip
        pos_list = _sanitize_style_list(POSITIVE_STYLES_CSV)
        neg_list = _sanitize_style_list(NEGATIVE_STYLES_CSV)
        if len(pos_list) != 3 or len(neg_list) != 3:
            raise AssertionError(f"CSV style parse failed: {pos_list=} {neg_list=}")
        pos_csv = _styles_to_csv(pos_list)
        neg_csv = _styles_to_csv(neg_list)
        _ok("Style CSV -> list", f"pos={pos_list}")
        _ok("Style list -> CSV", f"pos={pos_csv!r} | neg={neg_csv!r}")

        # music_v2 chunk plan (primary)
        total_ms = 20_000
        v2_plan = build_mei_music_v2_plan(total_ms)
        if "chunks" not in v2_plan or "sections" in v2_plan:
            raise AssertionError(f"Expected music_v2 chunks plan, got keys={list(v2_plan)}")
        v2_plan["chunks"][0]["positive_styles"] = pos_list
        v2_plan["chunks"][0]["negative_styles"] = neg_list
        v2_plan["chunks"][0]["duration_ms"] = int(
            _clamp_music_duration_ms(10_000, lo=3_000, hi=120_000)
        )
        v2_plan["chunks"][1]["duration_ms"] = int(
            _clamp_music_duration_ms(10_000, lo=3_000, hi=120_000)
        )
        chunk0 = v2_plan["chunks"][0]
        for key in ("text", "duration_ms", "positive_styles", "negative_styles", "context_adherence"):
            if key not in chunk0:
                raise AssertionError(f"music_v2 chunk missing {key}")
        _ok("music_v2 chunk plan", f"chunks={len(v2_plan['chunks'])}")

        # music_v1 typed MusicPrompt (fallback schema)
        v1_plan = build_mei_music_v1_plan(total_ms)
        try:
            from elevenlabs import MusicPrompt  # type: ignore

            if isinstance(v1_plan, MusicPrompt):
                MusicPrompt.model_validate(v1_plan.model_dump())
                n_sec = len(v1_plan.sections)
            else:
                MusicPrompt.model_validate(v1_plan)
                n_sec = len(v1_plan["sections"])
            _ok("music_v1 MusicPrompt typed", f"sections={n_sec}")
        except ImportError:
            _ok("music_v1 MusicPrompt typed", "skipped (import unavailable)")

        class _Fake422(Exception):
            status_code = 422

        if not _is_unprocessable_entity(_Fake422()):
            raise AssertionError("422 detector failed")
        _ok("422 detector", "ready (prompt fallback still available)")
        _ok("Simple prompt fallback ready", _MUSIC_SIMPLE_PROMPT[:64] + "...")

        OUT_DIR.mkdir(parents=True, exist_ok=True)
        MUSIC_OUT.unlink(missing_ok=True)

        # Live compose — primary composition_plan must succeed (no prompt fallback)
        path = generate_music_v2_bed(MUSIC_OUT, duration_seconds=20.0)
        mode = _audio_engine.LAST_MUSIC_COMPOSE_MODE
        if path is None or not _file_ok(Path(path), min_bytes=500):
            raise RuntimeError(
                "Music generation returned no usable file "
                "(API + ambient + local fail-safes all empty)."
            )
        if not mode or not str(mode).startswith("plan/"):
            raise AssertionError(
                f"composition_plan did not win primary call; mode={mode!r} "
                "(expected plan/music_v2 or plan/music_v1, not prompt fallback)"
            )
        kb = Path(path).stat().st_size / 1024
        _ok("Music composition_plan primary", f"mode={mode} ({kb:.1f} KB)")
        print(f"[OK]   Music module SUCCESS -> {path} [{mode}]")
        return True
    except Exception as exc:  # noqa: BLE001
        _fail("Music module", exc)
        print("[ERR]  Music module FAILED")
        return False


def main() -> int:
    print("=" * 60)
    print("AUDIO ENGINE VERIFICATION")
    print(f"Output dir: {OUT_DIR}")
    print("=" * 60)

    results = {
        "TTS": test_1_tts_sanitization_and_speed(),
        "SFX": test_2_sfx_braam(),
        "Music": test_3_music_api_payload(),
    }

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    for name, ok in results.items():
        status = "SUCCESS" if ok else "FAILED"
        print(f"  {name:6s} -> {status}")

    all_ok = all(results.values())
    print()
    if all_ok:
        print("ALL MODULES PASSED — ElevenLabs audio payload path looks healthy.")
        return 0
    print("ONE OR MORE MODULES FAILED — inspect logs above.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

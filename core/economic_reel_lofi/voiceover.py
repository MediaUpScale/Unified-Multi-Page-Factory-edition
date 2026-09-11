# -*- coding: utf-8 -*-
"""Reusable TTS-first stage for ECONOMIC_REEL_LOFI."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from core.economic_reel_lofi import config as lofi_cfg


def _fingerprint(text: str, *, speed: float) -> str:
    payload = "|".join(
        (
            " ".join(str(text or "").split()),
            lofi_cfg.tts_voice_id(),
            lofi_cfg.tts_model(),
            f"{float(speed):.4f}",
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def ensure_script_voiceover(
    state: dict[str, Any],
    script: dict[str, Any],
    work_dir: Path | str,
) -> list[Path | None]:
    """Generate/reuse exact approved VO and persist elastic scene timing."""
    from agents.media.audio_engine import generate_voiceover_with_timestamps
    from core.economic_reel_lofi.assembler import measure_vo_speech_duration

    lines = [row for row in (script.get("lines") or []) if isinstance(row, dict)]
    target = Path(work_dir)
    target.mkdir(parents=True, exist_ok=True)
    from core.economic_reel_lofi.pipeline import _purge_stale_temp_voice_files

    keep_names = {f"vo_scene_{i + 1:02d}.mp3" for i in range(len(lines))}
    _purge_stale_temp_voice_files(target, keep_names=keep_names)
    old_paths = list(state.get("voice_paths") or [])
    old_timings = list(state.get("word_timings_per_scene") or [])
    old_fingerprints = list(state.get("voice_fingerprints") or [])
    paths: list[Path | None] = []
    timings_all: list[list[tuple[str, float, float]] | None] = []
    durations: list[float] = []
    audio_durations: list[float] = []
    fingerprints: list[str] = []

    voice_id = lofi_cfg.tts_voice_id()
    model_id = lofi_cfg.tts_model() or "eleven_multilingual_v2"
    normal_speed = float(lofi_cfg.tts_speed())

    for i, row in enumerate(lines):
        caption = str(row.get("text") or "")
        scene_speed = max(0.95, normal_speed) if i == 0 else normal_speed
        fp = _fingerprint(caption, speed=scene_speed)
        out = target / f"vo_scene_{i + 1:02d}.mp3"
        prior = Path(str(old_paths[i])) if i < len(old_paths) and old_paths[i] else None
        reusable = bool(
            out.is_file()
            and prior is not None
            and prior.resolve() == out.resolve()
            and i < len(old_fingerprints)
            and str(old_fingerprints[i]) == fp
        )
        timing = old_timings[i] if reusable and i < len(old_timings) else None

        def generate(text: str) -> tuple[Path, list[tuple[str, float, float]]]:
            generated, raw = generate_voiceover_with_timestamps(
                text,
                out,
                voice_id=voice_id or None,
                model_id=model_id,
                force_elevenlabs=True,
                expressive_mode=False,
                enable_ssml=False,
                speed=scene_speed,
                voice_settings={
                    "stability": 1.0,
                    "similarity_boost": 1.0,
                    "style": 0.0,
                    "use_speaker_boost": True,
                    "speed": scene_speed,
                },
            )
            clean = [
                (str(w), float(s), float(e))
                for w, s, e in (raw or [])
                if str(w).strip()
                and not str(w).startswith("<")
                and str(w).lower() not in {"break", "time"}
            ]
            return Path(generated), clean

        if not reusable and caption:
            print(
                f"[LOFI TTS-first] scene={i + 1} generating exact approved text "
                f"speed={scene_speed:.2f}"
            )
            out, timing = generate(caption)

        vo_dur = float(measure_vo_speech_duration(out)) if out and out.is_file() else 0.0
        if i == 0:
            slot = round(min(vo_dur + 0.2, 3.0), 3)
        elif i == len(lines) - 1:
            slot = round(vo_dur + 2.25, 3)
        else:
            slot = round(vo_dur + 0.4, 3)
        # Preserve the Gate-1 nominal duration for validator compatibility.
        # The actual assembly anchor lives separately and in scene_durations.
        row["audio_slot_s"] = slot
        row["vo_duration_s"] = round(vo_dur, 3)
        row["tts_speed"] = round(scene_speed, 3)
        paths.append(out if out and out.is_file() else None)
        timings_all.append(timing)
        durations.append(slot)
        audio_durations.append(round(vo_dur, 3))
        fingerprints.append(fp)

    script["monologue"] = " ".join(str(row.get("text") or "") for row in lines)
    state["script"] = script
    state["work_dir"] = str(target)
    state["voice_paths"] = [str(path) if path else None for path in paths]
    state["word_timings_per_scene"] = timings_all
    state["scene_durations"] = durations
    state["audio_durations_s"] = audio_durations
    state["voice_fingerprints"] = fingerprints
    state["duration_actual_s"] = round(sum(durations), 3)
    state["tts_stage_complete"] = True
    return paths

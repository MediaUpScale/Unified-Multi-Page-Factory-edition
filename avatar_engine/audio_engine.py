# -*- coding: utf-8 -*-
"""
ElevenLabs audio generation for ECONOMIC_REEL / Master Mei.

  generate_voiceover()              — TTS narration (speed + expressive model).
  generate_ambient_track()          — Legacy SFX ambient tile.
  generate_impact_sfx()             — Cinematic braam at t=0.
  generate_music_v2_bed()           — Dual-chunk music_v2 composition plan.
  generate_master_mei_soundscape()  — Music bed + impact SFX pair.
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any

import config as app_config

logger = logging.getLogger(__name__)

# Brian — deep, authoritative, high-engagement narrative tone
_DEFAULT_VOICE_ID: str = "nPczCjzI2devNBz1zQrb"
# Master Mei defaults to expressive eleven_v3; other pages may override
_DEFAULT_TTS_MODEL: str = "eleven_v3"

# Voice performance — stoic / authoritative Master Mei delivery
_DEFAULT_VOICE_SETTINGS = {
    "stability": 0.80,
    "similarity_boost": 0.85,
    "style": 0.20,           # mild expressiveness with eleven_v3
    "use_speaker_boost": True,
}
_NARRATION_SPEED: float = 0.86   # deliberate stoic pace (Master Mei 100–120 s band)
_AMBIENT_PROMPT: str = (
    "Dark ambient cinematic synth pad, deep sub-bass drone, subtle futuristic "
    "industrial machine hum, inspiring stoic atmosphere, seamless loop, 60 BPM, "
    "warm dark cinematic underscore, high production value, no vocals, no melody lead, "
    "NO rain, NO thunder, NO white noise, NO hiss, NO static, NO water drops, NO storm, "
    "NO generic noise bed"
)
_TARGET_SAMPLE_RATE_HZ: int = 48000
_SFX_CLIP_DURATION: float = 10.0   # single continuous dark cyberpunk drone (looped full reel)
_SFX_MIN_DURATION: float = 10.0
_MUSIC_MIN_DURATION_S: float = 40.0  # ElevenLabs BGM minimum generation length
_SFX_MODEL_ID: str = "eleven_text_to_sound_v2"
_IMPACT_SFX_PROMPT: str = "Cinematic Braam, Dystopian Sub-Bass Drop"
_IMPACT_SFX_DURATION_S: float = 2.5
_ATMOSPHERE_SFX_PROMPT: str = (
    "Continuous 10-second low-frequency dark cyberpunk atmospheric drone, ambient "
    "industrial drone, futuristic wasteland tension, dark hum, wind in ruins, "
    "seamless loop, no percussion hits, no braam stinger, no vocals"
)
_MUSIC_V2_MODEL: str = "music_v2"
_MUSIC_V1_MODEL: str = "music_v1"
# ElevenLabs Music duration bounds (ms) — section / total
_MUSIC_DURATION_MS_MIN: int = 5_000
_MUSIC_DURATION_MS_MAX: int = 180_000
_MUSIC_SECTION_MS_MIN: int = 3_000
_MUSIC_SECTION_MS_MAX: int = 120_000
_MUSIC_SIMPLE_PROMPT: str = (
    "Industrial cyberpunk percussion, driving cinematic drums from bar one, "
    "dark metallic hits, dystopian synth bass, 95 BPM, instrumental"
)
# Canonical Ancient Knowledge music-bed template (truncated to API line limits).
# Slow-tempo, minimal-percussion, documentary-mystery mood. NEVER upbeat.
_MYSTERY_MUSIC_TEMPLATE: str = (
    "Cinematic dark ancient mystery documentary soundtrack, deep ambient drones, "
    "sparse slow frame drums, eerie atmospheric pads, slow tempo, "
    "no driving beat, minimal percussion, sub-bass, ancient undertones, "
    "high quality, no voice, seamless loop."
)
# ElevenLabs Music API: section lines / chunk text max 200 chars (422 string_too_long).
_MUSIC_LINE_API_MAX: int = 200
_MUSIC_LINE_MAX_CHARS: int = 180  # hard truncate headroom under API 200
_MUSIC_PROMPT_TARGET_CHARS: int = 150  # LLM target (under hard truncate)
MUSIC_PROMPT_SYSTEM_DIRECTIVE: str = (
    "Generate a unique ElevenLabs music prompt for industrial cyberpunk percussion. "
    "Driving cinematic drums from the first beat, dark metallic hits, tense rhythmic "
    "pulse, dystopian urgency. Tempo approx 90–110 BPM. "
    "CONSTRAINT: Keep each section line/prompt concise, descriptive, and strictly under "
    "150 characters. Do not write long paragraphs or excessive descriptors."
)
MUSIC_PROMPT_MYSTERY_SYSTEM_DIRECTIVE: str = (
    "Generate a unique ElevenLabs music prompt for a cinematic dark ancient "
    "mystery documentary soundtrack. "
    "MOOD (non-negotiable): mysterious, enigmatic, dark, somber, documentary-"
    "mystery tone. Never upbeat, never triumphant, never bright, never major-"
    "key-sounding, never celebratory. "
    "TEMPO (non-negotiable): slow to moderate only. Slow tempo, minimal "
    "percussion, no driving beat. Approx 48–68 BPM. "
    "INSTRUMENTS: deep ambient drones, sparse slow frame drums, eerie "
    "atmospheric pads, sub-bass, low brass/strings, ancient undertones. "
    "Hook: subtle suspense. Mid/end: slower heavier darker atmosphere. "
    "High quality, no voice, seamless loop. "
    "STRICTLY FORBIDDEN: upbeat, energetic, fast-paced, danceable, EDM, pop, "
    "party, triumphant, bright, driving beat, fast BPM, industrial cyberpunk "
    "drums, synth leads, vocals with lyrics, long silent intro. "
    "CONSTRAINT: Keep each section line/prompt concise, descriptive, and "
    "strictly under 150 characters."
)
_MUSIC_PROMPT_DIRECTIVE_FILE: Path = (
    Path(__file__).resolve().parents[1]
    / "channels_config"
    / "master_mei"
    / "prompts"
    / "music_prompt_directive.txt"
)
_MUSIC_PROMPT_MYSTERY_DIRECTIVE_FILE: Path = (
    Path(__file__).resolve().parents[1]
    / "channels_config"
    / "ancient_knowledge"
    / "prompts"
    / "music_prompt_directive.txt"
)
# Final MoviePy mix levels (Master Mei AUDIO_CONFIG)
_MIX_SFX_VOLUME: float = 0.35
_MIX_BGM_VOLUME: float = 0.24
_MIX_VOICE_VOLUME: float = 1.0
_MIX_AMBIENT_FADE_IN_S: float = 0.2
_MIX_MUSIC_START_OFFSET_S: float = 0.5

# Strip raw code / bracket markers before TTS (never read aloud)
_TTS_ANGLE_RE = re.compile(r"<[^>]+>")
_TTS_CURLY_RE = re.compile(r"\{[^}]*\}")
_TTS_CODE_FENCE_RE = re.compile(r"`{1,3}[^`]*`{1,3}")


def strip_tts_markers(text: str) -> str:
    """Remove brackets, SSML, curly cues, and code fences from TTS input.

    Primary filter (mandatory): ``re.sub(r'\\[.*?\\]', '', text)`` so tags like
    ``[stoic]`` / ``[ACT 1]`` are never spoken aloud.
    """
    clean = text or ""
    # Mandatory bracket wipe (non-greedy, DOTALL for multi-line tags)
    clean = re.sub(r"\[.*?\]", "", clean, flags=re.DOTALL)
    clean = re.sub(r"<\s*break\s+[^>]*/?\s*>", " ... ", clean, flags=re.IGNORECASE)
    clean = _TTS_ANGLE_RE.sub(" ", clean)
    clean = _TTS_CURLY_RE.sub(" ", clean)
    clean = _TTS_CODE_FENCE_RE.sub(" ", clean)
    clean = re.sub(r"[ \t]{2,}", " ", clean)
    clean = re.sub(r"\n{3,}", "\n\n", clean)
    return clean.strip()


def _resolve_voice_settings(
    overrides: dict | None = None,
    *,
    expressive_mode: bool = False,
) -> dict:
    """Merge page-level VoiceSettings overrides onto engine defaults.

    Supports official ElevenLabs keys including ``speed`` (0.25–4.0).
    When *expressive_mode* is True, slightly raise ``style`` for eleven_v3.
    """
    merged = dict(_DEFAULT_VOICE_SETTINGS)
    if overrides:
        for key in ("stability", "similarity_boost", "style", "speed"):
            if key in overrides and overrides[key] is not None:
                try:
                    merged[key] = float(overrides[key])
                except (TypeError, ValueError):
                    pass
        if "use_speaker_boost" in overrides:
            merged["use_speaker_boost"] = bool(overrides["use_speaker_boost"])
    if expressive_mode and float(merged.get("style") or 0.0) < 0.20:
        merged["style"] = 0.25
    return merged


def _build_voice_settings_obj(_VoiceSettings, vs: dict, speed: float):
    """
    Build ElevenLabs VoiceSettings with speed inside voice_settings when supported.

    Official payload shape:
      {stability, similarity_boost, style, use_speaker_boost, speed}
    Falls back without ``speed`` kwarg on older SDKs.
    """
    common = dict(
        stability=vs["stability"],
        similarity_boost=vs["similarity_boost"],
        style=vs["style"],
        use_speaker_boost=vs["use_speaker_boost"],
    )
    _spd = float(vs.get("speed", speed) if vs.get("speed") is not None else speed)
    try:
        return _VoiceSettings(**common, speed=_spd), _spd
    except TypeError:
        return _VoiceSettings(**common), _spd


def generate_voiceover(
    text: str,
    output_path: Path,
    *,
    voice_id: str | None = None,
    model_id: str = _DEFAULT_TTS_MODEL,
    speed: float | None = None,
    voice_settings: dict | None = None,
    enable_ssml: bool | None = None,
    expressive_mode: bool = True,
) -> Path:
    """
    Generate a TTS voiceover from hook text using the ElevenLabs API.

    Parameters
    ----------
    text        : The hook / overlay text to narrate.
    output_path : Where to write the mp3 file.
    voice_id    : ElevenLabs voice UUID.  Defaults to Rachel.
    model_id    : ElevenLabs model.  Prefer ``eleven_multilingual_v2``.
    speed       : Optional narration speed multiplier (also accepted via voice_settings.speed).
    voice_settings : Optional dict overriding stability / similarity_boost / style / speed.
    enable_ssml : When True (or auto-detected via ``<break``), enable SSML parsing.

    Returns
    -------
    output_path on success.  Raises RuntimeError / ValueError on failure.

    Notes
    -----
    When ``ENABLE_REMOTE_GPU_WORKFLOWS=true``, routes to remote F5-TTS via
    ``core_engine.remote_gpu_manager.generate_audio`` and returns early.
    Legacy ElevenLabs code below is otherwise unchanged.
    """
    # --- Remote GPU adapter (opt-in only; legacy path untouched when false) ---
    # Runtime env check so --schedule-uploads long-runs honour flag flips.
    from core_engine.remote_gpu_manager import (  # noqa: PLC0415
        generate_audio as _remote_generate_audio,
        is_remote_gpu_enabled,
    )

    if is_remote_gpu_enabled("audio"):
        cleaned = strip_tts_markers(text)
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        page_id = (
            getattr(app_config, "ACTIVE_PAGE", None)
            or os.getenv("ACTIVE_PAGE")
            or None
        )
        logger.info(
            "Voiceover -> RemoteGPU F5-TTS (flow/env audio provider=remote_gpu) | "
            "page=%s | chars=%d -> %s",
            page_id or "?", len(cleaned), out,
        )
        result = _remote_generate_audio(
            cleaned,
            output_path=out,
            speed=speed,
            page_id=page_id,
        )
        return Path(result).resolve()

    try:
        from elevenlabs import ElevenLabs  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "elevenlabs package not installed. Run: pip install elevenlabs"
        ) from exc

    api_key = app_config.ELEVENLABS_API_KEY
    if not api_key:
        raise ValueError("ELEVENLABS_API_KEY not set. Add it to your .env file.")

    from elevenlabs import VoiceSettings as _VoiceSettings  # type: ignore

    client = ElevenLabs(api_key=api_key)
    vid = voice_id or _DEFAULT_VOICE_ID
    text = strip_tts_markers(text)
    _vs = _resolve_voice_settings(voice_settings, expressive_mode=expressive_mode)
    # Prefer explicit speed arg, else voice_settings.speed, else engine default
    if speed is not None:
        _speed = float(speed)
    elif _vs.get("speed") is not None:
        _speed = float(_vs["speed"])
    else:
        _speed = _NARRATION_SPEED
    _ssml = bool(enable_ssml) if enable_ssml is not None else False
    vs_obj, _speed = _build_voice_settings_obj(_VoiceSettings, _vs, _speed)
    _model = (model_id or _DEFAULT_TTS_MODEL).strip() or _DEFAULT_TTS_MODEL

    logger.info(
        "Generating voiceover | voice=%s | model=%s | chars=%d | speed=%.2f | "
        "stability=%.2f | expressive=%s | ssml=%s",
        vid, _model, len(text), _speed, _vs["stability"], expressive_mode, _ssml,
    )
    _tts_kwargs: dict = dict(
        voice_id=vid,
        text=text,
        model_id=_model,
        voice_settings=vs_obj,
        output_format="mp3_44100_128",
    )
    if _ssml:
        _tts_kwargs["enable_ssml_parsing"] = True
    # Some SDK builds accept expressive_mode; ignore if unsupported
    if expressive_mode:
        _tts_kwargs["expressive_mode"] = True
    try:
        audio_stream = client.text_to_speech.convert(**_tts_kwargs, speed=_speed)
    except TypeError:
        logger.debug("ElevenLabs SDK param mismatch — retrying with reduced kwargs")
        _tts_kwargs.pop("enable_ssml_parsing", None)
        _tts_kwargs.pop("expressive_mode", None)
        try:
            audio_stream = client.text_to_speech.convert(**_tts_kwargs, speed=_speed)
        except TypeError:
            audio_stream = client.text_to_speech.convert(**_tts_kwargs)
    except Exception as _tts_exc:
        # eleven_v3 unavailable → fall back to multilingual_v2 once
        if _model == "eleven_v3" and "model" in str(_tts_exc).lower():
            logger.warning("eleven_v3 unavailable (%s) — falling back to multilingual_v2", _tts_exc)
            _tts_kwargs["model_id"] = "eleven_multilingual_v2"
            _tts_kwargs.pop("expressive_mode", None)
            try:
                audio_stream = client.text_to_speech.convert(**_tts_kwargs, speed=_speed)
            except TypeError:
                audio_stream = client.text_to_speech.convert(**_tts_kwargs)
        else:
            raise

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "wb") as fh:
        for chunk in audio_stream:
            if chunk:
                fh.write(chunk)

    size = output_path.stat().st_size
    logger.info("Voiceover saved → %s (%d bytes)", output_path.name, size)
    return output_path


def _chars_to_word_timings(
    characters: list[str],
    start_times: list[float],
    end_times: list[float],
) -> list[tuple[str, float, float]]:
    """
    Group ElevenLabs character-level alignment data into word-level timing tuples.

    Returns a list of (word, start_seconds, end_seconds) covering the full narration.
    """
    words: list[tuple[str, float, float]] = []
    current_chars: list[str] = []
    word_start: float = 0.0

    for char, t0, t1 in zip(characters, start_times, end_times):
        if char.strip() == "":          # space / newline / punctuation-gap
            if current_chars:
                words.append(("".join(current_chars), word_start, t0))
                current_chars = []
        else:
            if not current_chars:
                word_start = t0
            current_chars.append(char)

    if current_chars and end_times:     # flush final word
        words.append(("".join(current_chars), word_start, end_times[-1]))

    return words


def pad_narration_to_minimum(voice_path: "Path", min_s: float) -> "Path":
    """Append silence so *voice_path* is never shorter than *min_s* (non-LLM)."""
    src = Path(voice_path)
    current = _audio_file_duration_s(src)
    if current <= 0.0 or current >= float(min_s) or not src.is_file():
        return src
    pad_s = float(min_s) - current + 0.05
    out = src.with_name(f"{src.stem}_padded{src.suffix}")
    try:
        from moviepy import AudioFileClip  # type: ignore[import]
        from moviepy import concatenate_audioclips  # type: ignore[import]
        from moviepy.audio.AudioClip import AudioArrayClip  # type: ignore[import]
        import numpy as _np_pad

        clip = AudioFileClip(str(src))
        _sr = 44100
        _sil = _np_pad.zeros((int(_sr * pad_s), 2), dtype=_np_pad.float32)
        silence = AudioArrayClip(_sil, fps=_sr)
        combined = concatenate_audioclips([clip, silence])
        out.parent.mkdir(parents=True, exist_ok=True)
        combined.write_audiofile(str(out), fps=_sr, logger=None)
        logger.info(
            "pad_narration_to_minimum | %.1fs + %.1fs silence → %.1fs → %s",
            current, pad_s, float(combined.duration or 0.0), out.name,
        )
        for _c in (clip, silence, combined):
            try:
                _c.close()
            except Exception:
                pass
        return out
    except Exception as exc:  # noqa: BLE001
        logger.warning("pad_narration_to_minimum failed (%s) — returning original.", exc)
        return src


def _audio_file_duration_s(path: Path) -> float:
    """Best-effort duration read for local audio (MoviePy → mutagen → 0)."""
    p = Path(path)
    if not p.is_file():
        return 0.0
    try:
        from moviepy import AudioFileClip  # type: ignore[import]

        with AudioFileClip(str(p)) as clip:
            return float(clip.duration or 0.0)
    except Exception:
        pass
    try:
        from mutagen import File as _MutagenFile  # type: ignore[import]

        meta = _MutagenFile(str(p))
        if meta is not None and getattr(meta, "info", None) is not None:
            return float(getattr(meta.info, "length", 0.0) or 0.0)
    except Exception:
        pass
    return 0.0


def approximate_word_timings(
    text: str,
    duration_s: float,
) -> list[tuple[str, float, float]]:
    """
    Build character-weighted word timings when a TTS backend has no alignment
    (e.g. remote F5-TTS). Longer words get proportionally more screen time.
    """
    cleaned = strip_tts_markers(text or "").strip()
    words = [w for w in cleaned.split() if w]
    if not words or duration_s <= 0.05:
        return []
    weights = [max(1, len(re.sub(r"[^\w]", "", w, flags=re.UNICODE))) for w in words]
    total_w = float(sum(weights)) or float(len(words))
    cursor = 0.0
    out: list[tuple[str, float, float]] = []
    for i, (word, weight) in enumerate(zip(words, weights)):
        span = duration_s * (weight / total_w)
        start = cursor
        end = duration_s if i == len(words) - 1 else min(duration_s, cursor + span)
        if end <= start:
            end = min(duration_s, start + 0.05)
        out.append((word, start, end))
        cursor = end
    return out


def generate_voiceover_with_timestamps(
    text: str,
    output_path: Path,
    *,
    voice_id: str | None = None,
    model_id: str = _DEFAULT_TTS_MODEL,
    speed: float | None = None,
    voice_settings: dict | None = None,
    enable_ssml: bool | None = None,
    expressive_mode: bool = True,
    force_elevenlabs: bool = False,
) -> tuple[Path, list[tuple[str, float, float]]]:
    """
    Generate a TTS voiceover AND return word-level timing data for auto-subtitles.

    Calls ElevenLabs ``convert_with_timestamps()`` (SDK v1.2+), parses the
    character-level alignment into ``[(word, start_s, end_s), ...]``, and saves
    the audio to ``output_path``.

    Returns
    -------
    (output_path, word_timings)
        word_timings is empty list [] when the timestamps endpoint is unavailable
        (older SDK versions) — the reel compiles normally without subtitles.

    Notes
    -----
    When ``ENABLE_REMOTE_GPU_WORKFLOWS=true``, delegates audio to remote F5-TTS
    via ``generate_voiceover`` and synthesizes character-weighted approximate
    word timings from the returned clip duration (F5 has no alignment API).
    Pass ``force_elevenlabs=True`` to skip F5 (needed when the page has no
    F5 voice reference, e.g. ancient_knowledge).
    """
    from core_engine.remote_gpu_manager import is_remote_gpu_enabled  # noqa: PLC0415

    if not force_elevenlabs and is_remote_gpu_enabled("audio"):
        path = generate_voiceover(
            text,
            output_path,
            voice_id=voice_id,
            model_id=model_id,
            speed=speed,
            voice_settings=voice_settings,
            enable_ssml=enable_ssml,
            expressive_mode=expressive_mode,
        )
        resolved = Path(path).resolve()
        dur = _audio_file_duration_s(resolved)
        word_timings = approximate_word_timings(text, dur)
        logger.info(
            "Voiceover+timestamps -> RemoteGPU F5-TTS | %s | dur=%.2fs | "
            "approx_timings=%d words",
            resolved.name, dur, len(word_timings),
        )
        return resolved, word_timings

    try:
        from elevenlabs import ElevenLabs, VoiceSettings as _VoiceSettings  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "elevenlabs package not installed. Run: pip install elevenlabs"
        ) from exc

    api_key = app_config.ELEVENLABS_API_KEY
    if not api_key:
        raise ValueError("ELEVENLABS_API_KEY not set. Add it to your .env file.")

    client = ElevenLabs(api_key=api_key)
    vid = voice_id or _DEFAULT_VOICE_ID
    text = strip_tts_markers(text)
    _vs = _resolve_voice_settings(voice_settings, expressive_mode=expressive_mode)
    if speed is not None:
        _speed = float(speed)
    elif _vs.get("speed") is not None:
        _speed = float(_vs["speed"])
    else:
        _speed = _NARRATION_SPEED
    _ssml = bool(enable_ssml) if enable_ssml is not None else False
    vs, _speed = _build_voice_settings_obj(_VoiceSettings, _vs, _speed)
    _model = (model_id or _DEFAULT_TTS_MODEL).strip() or _DEFAULT_TTS_MODEL

    logger.info(
        "Generating voiceover+timestamps | voice=%s | model=%s | chars=%d | "
        "speed=%.2f | stability=%.2f | expressive=%s | ssml=%s",
        vid, _model, len(text), _speed, _vs["stability"], expressive_mode, _ssml,
    )

    word_timings: list[tuple[str, float, float]] = []
    audio_bytes: bytes = b""

    try:
        # ElevenLabs SDK v2.x returns AudioWithTimestampsResponse.
        # IMPORTANT: the Pydantic field is named `audio_base_64` (underscore before 64)
        # even though the JSON alias is `audio_base64`.  Accessing `.audio_base64`
        # raises AttributeError; the correct Python attribute is `.audio_base_64`.
        _ts_kwargs: dict = dict(
            voice_id=vid,
            text=text,
            model_id=_model,
            voice_settings=vs,
            output_format="mp3_44100_128",
        )
        if _ssml:
            _ts_kwargs["enable_ssml_parsing"] = True
        if expressive_mode:
            _ts_kwargs["expressive_mode"] = True
        try:
            result = client.text_to_speech.convert_with_timestamps(
                **_ts_kwargs, speed=_speed
            )
        except TypeError:
            logger.debug("convert_with_timestamps: speed/ssml unsupported — retrying reduced kwargs")
            _ts_kwargs.pop("enable_ssml_parsing", None)
            _ts_kwargs.pop("expressive_mode", None)
            try:
                result = client.text_to_speech.convert_with_timestamps(
                    **_ts_kwargs, speed=_speed
                )
            except TypeError:
                result = client.text_to_speech.convert_with_timestamps(**_ts_kwargs)

        # Decode audio — try the correct field name first, then legacy/fallback names
        import base64 as _b64
        _raw_b64: str | None = (
            getattr(result, "audio_base_64", None)    # SDK v2.x Python attribute
            or getattr(result, "audio_base64", None)  # alias / future-proofing
        )
        if not _raw_b64:
            raise AttributeError(
                f"Cannot locate base64 audio on response type {type(result).__name__}. "
                f"Available attrs: {[a for a in dir(result) if not a.startswith('_')]}"
            )
        audio_bytes = _b64.b64decode(_raw_b64)

        # Extract character-level alignment and convert to word-level tuples
        al = getattr(result, "alignment", None) or getattr(result, "normalized_alignment", None)
        if al and getattr(al, "characters", None):
            word_timings = _chars_to_word_timings(
                al.characters,
                al.character_start_times_seconds,
                al.character_end_times_seconds,
            )
        logger.info("Subtitle alignment parsed: %d words", len(word_timings))

    except Exception as exc:
        # Only fall back for import/network errors, NOT for attribute errors
        # (those indicate an SDK API surface change that needs fixing, not silencing).
        logger.warning(
            "convert_with_timestamps() failed (%s). "
            "Falling back to convert() — no subtitle timing.", exc,
        )
        _fb_model = _model
        if _fb_model == "eleven_v3" and "model" in str(exc).lower():
            _fb_model = "eleven_multilingual_v2"
            logger.warning("eleven_v3 unavailable — falling back to multilingual_v2")
        _fb_kwargs: dict = dict(
            voice_id=vid, text=text, model_id=_fb_model,
            voice_settings=vs, output_format="mp3_44100_128",
        )
        try:
            audio_stream = client.text_to_speech.convert(**_fb_kwargs, speed=_speed)
        except TypeError:
            audio_stream = client.text_to_speech.convert(**_fb_kwargs)
        audio_bytes = b"".join(chunk for chunk in audio_stream if chunk)

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "wb") as fh:
        fh.write(audio_bytes)

    size = output_path.stat().st_size
    logger.info(
        "Voiceover saved → %s (%d bytes, %d words timed)",
        output_path.name, size, len(word_timings),
    )
    return output_path, word_timings


def apply_voice_loudnorm(
    audio_path: Path,
    *,
    i: float = -16.0,
    tp: float = -1.5,
    lra: float = 11.0,
    sample_rate: int = _TARGET_SAMPLE_RATE_HZ,
) -> Path:
    """
    Resample to 48 kHz + ffmpeg loudnorm (I=-16:TP=-1.5:LRA=11).

    Prevents clipping/distortion and guarantees consistent sample rate before
    the final AAC mix. Returns ``audio_path`` unchanged on failure.
    """
    import shutil
    import subprocess

    path = Path(audio_path)
    if not path.is_file():
        return path
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        logger.debug("apply_voice_loudnorm skipped — ffmpeg not found")
        return path

    sr = int(sample_rate) if sample_rate and int(sample_rate) > 0 else _TARGET_SAMPLE_RATE_HZ
    af = f"loudnorm=I={i}:TP={tp}:LRA={lra},aformat=sample_rates={sr}"
    tmp = path.with_suffix(".loudnorm_tmp.mp3")
    try:
        subprocess.run(
            [
                ffmpeg, "-y", "-i", str(path),
                "-af", af,
                "-ar", str(sr),
                "-codec:a", "libmp3lame", "-b:a", "192k",
                str(tmp),
            ],
            check=True,
            capture_output=True,
            timeout=180,
        )
        tmp.replace(path)
        logger.info(
            "VO loudnorm+resample applied (I=%.1f TP=%.1f LRA=%.1f ar=%d) → %s",
            i, tp, lra, sr, path.name,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("VO loudnorm skipped: %s", exc)
        if tmp.is_file():
            try:
                tmp.unlink()
            except OSError:
                pass
    return path


def resample_audio_48k(
    audio_path: Path,
    *,
    sample_rate: int = _TARGET_SAMPLE_RATE_HZ,
    apply_loudnorm: bool = True,
) -> Path:
    """Force any audio file to 48 kHz (optional loudnorm). Used for ambient/SFX."""
    if apply_loudnorm:
        return apply_voice_loudnorm(audio_path, sample_rate=sample_rate)
    import shutil
    import subprocess

    path = Path(audio_path)
    if not path.is_file():
        return path
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return path
    sr = int(sample_rate) if sample_rate and int(sample_rate) > 0 else _TARGET_SAMPLE_RATE_HZ
    tmp = path.with_suffix(".resample_tmp.mp3")
    try:
        subprocess.run(
            [
                ffmpeg, "-y", "-i", str(path),
                "-ar", str(sr),
                "-codec:a", "libmp3lame", "-b:a", "192k",
                str(tmp),
            ],
            check=True,
            capture_output=True,
            timeout=180,
        )
        tmp.replace(path)
        logger.info("Audio resampled to %d Hz → %s", sr, path.name)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Audio resample skipped: %s", exc)
        if tmp.is_file():
            try:
                tmp.unlink()
            except OSError:
                pass
    return path


# Backward-compatible alias (EQ path retired — loudnorm only)
def apply_authority_voice_eq(audio_path: Path) -> Path:
    return apply_voice_loudnorm(audio_path)


def generate_ambient_track(
    output_path: Path,
    *,
    duration_seconds: float = _SFX_CLIP_DURATION,   # kept for API compat; always clamped to 20 s
    prompt: str | None = None,
) -> "Path | None":
    """
    Generate a 20-second seamlessly-loopable dark-ambient tile via the
    ElevenLabs Sound Effects (SFX) API and save it to ``output_path``.

    Strategy
    --------
    * Always requests exactly ``_SFX_CLIP_DURATION`` (20 s) with ``loop=True``
      so the model guarantees smooth start/end join points.
    * ``reel_sequence_engine.compile_sequence_reel`` concatenates enough copies
      of this tile to span the full video duration — the per-call cost is
      therefore fixed at 800 ElevenLabs credits regardless of video length.
    * Falls back to ``assets/audio/ambient_mystery_loop.mp3`` (if present on
      disk) when the API call fails, rather than synthesising noise.

    Returns ``output_path`` on success, ``None`` on failure.
    """
    import requests as _requests  # type: ignore[import]

    api_key = app_config.ELEVENLABS_API_KEY
    if not api_key:
        logger.warning("ELEVENLABS_API_KEY not set — ambient track skipped.")
        return None

    sfx_prompt = prompt or _AMBIENT_PROMPT
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    # Single continuous 10s dark cyberpunk drone (looped for full video — no impact stingers)
    _sfx_dur = max(_SFX_MIN_DURATION, min(12.0, float(duration_seconds or _SFX_CLIP_DURATION)))
    if not prompt:
        sfx_prompt = _ATMOSPHERE_SFX_PROMPT

    payload = {
        "text": sfx_prompt,
        "model_id": _SFX_MODEL_ID,
        "duration_seconds": _sfx_dur,
        "loop": True,
        "prompt_influence": 0.7,
    }
    headers = {
        "xi-api-key": api_key,
        "Content-Type": "application/json",
    }

    try:
        logger.info(
            "Generating SFX ambient tile | model=%s dur=%.0fs loop=True (min>=%.0fs)",
            _SFX_MODEL_ID, _sfx_dur, _SFX_MIN_DURATION,
        )
        resp = _requests.post(
            "https://api.elevenlabs.io/v1/sound-generation",
            json=payload,
            headers=headers,
            timeout=15,
        )
        if resp.status_code >= 400:
            # Surface API body — 400s are often auth/key-format, not prompt bugs.
            _body = (resp.text or "").strip().replace("\n", " ")[:400]
            logger.warning(
                "ElevenLabs SFX HTTP %s | body=%s",
                resp.status_code, _body or "(empty)",
            )
        resp.raise_for_status()

        with open(output_path, "wb") as fh:
            fh.write(resp.content)

        # Ban rain/hiss: resample + light loudnorm so bed never distorts at mix
        resample_audio_48k(output_path, apply_loudnorm=True)
        logger.info(
            "Ambient SFX tile saved -> %s (%.1f KB)",
            output_path.name, len(resp.content) / 1024,
        )
        return output_path

    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "ElevenLabs SFX request failed (%s) — checking local fallback.",
            exc,
        )

    # Fallback: prefer cinematic pad; NEVER prefer legacy rain/martial rain loops
    for _rel in (
        Path("assets") / "master_mei" / "audio" / "ambient_cinematic_pad.mp3",
        Path("assets") / "audio" / "ambient_mystery_loop.mp3",
    ):
        _local = app_config.ENGINE_ROOT / _rel
        if _local.is_file():
            logger.info("SFX fallback → local asset %s", _local.name)
            return _local

    logger.warning(
        "Ambient audio unavailable — reel will use procedural cinematic drone. "
        "Drop a pad into assets/master_mei/audio/ambient_cinematic_pad.mp3 "
        "to add background music without an API call."
    )
    return None


def _write_audio_iterator(audio_iter: Any, output_path: Path) -> Path:
    """Write an ElevenLabs byte iterator / bytes payload to *output_path*."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "wb") as fh:
        if isinstance(audio_iter, (bytes, bytearray)):
            fh.write(audio_iter)
        else:
            for chunk in audio_iter:
                if chunk:
                    fh.write(chunk)
    return output_path


def generate_impact_sfx(
    output_path: Path,
    *,
    prompt: str = _IMPACT_SFX_PROMPT,
    duration_seconds: float = _IMPACT_SFX_DURATION_S,
) -> "Path | None":
    """
    Generate a 2–3 s cinematic braam / sub-bass drop for the t=0 video hook.

    Uses ``client.text_to_sound_effects.convert``. Returns None on failure.
    """
    api_key = app_config.ELEVENLABS_API_KEY
    if not api_key:
        logger.warning("ELEVENLABS_API_KEY not set — impact SFX skipped.")
        return None

    output_path = Path(output_path)
    dur = max(2.0, min(3.0, float(duration_seconds or _IMPACT_SFX_DURATION_S)))
    text = (prompt or _IMPACT_SFX_PROMPT).strip()

    try:
        from elevenlabs import ElevenLabs  # type: ignore

        client = ElevenLabs(api_key=api_key)
        logger.info("Generating impact SFX | dur=%.1fs | %s", dur, text[:80])
        audio = client.text_to_sound_effects.convert(
            text=text,
            duration_seconds=dur,
            prompt_influence=0.75,
            model_id=_SFX_MODEL_ID,
            output_format="mp3_44100_128",
        )
        _write_audio_iterator(audio, output_path)
        resample_audio_48k(output_path, apply_loudnorm=False)
        logger.info("Impact SFX saved → %s (%.1f KB)", output_path.name, output_path.stat().st_size / 1024)
        return output_path
    except Exception as exc:  # noqa: BLE001
        logger.warning("Impact SFX generation failed (%s) — continuing without hook hit.", type(exc).__name__)
        return None


def _sanitize_style_list(styles: Any) -> list[str]:
    """Normalize styles to a flat ``list[str]`` (SDK MusicPrompt / SongSection).

    Accepts a list, tuple, or a single comma-separated string. Nested lists are
    flattened. Empty / blank entries are dropped.
    """
    if styles is None:
        return []
    if isinstance(styles, str):
        parts = [p.strip() for p in styles.split(",")]
        return [p for p in parts if p]
    if isinstance(styles, (list, tuple)):
        out: list[str] = []
        for item in styles:
            if isinstance(item, (list, tuple)):
                out.extend(_sanitize_style_list(item))
            elif item is not None:
                s = str(item).strip()
                if s:
                    out.append(s)
        return out
    s = str(styles).strip()
    return [s] if s else []


def _styles_to_csv(styles: Any) -> str:
    """Flat comma-separated style string for prompt-mode music.compose calls."""
    return ", ".join(_sanitize_style_list(styles))


def _truncate_music_line(
    text: Any,
    max_chars: int = _MUSIC_LINE_MAX_CHARS,
) -> str:
    """Truncate a composition line/prompt under the Music API char limit."""
    return str(text or "")[: max(1, int(max_chars))]


def _enforce_composition_plan_line_limits(
    composition_plan: Any,
    max_chars: int = _MUSIC_LINE_MAX_CHARS,
) -> Any:
    """
    Hard-truncate section ``lines`` and chunk ``text`` fields before music.compose.

    Bulletproof fallback against ElevenLabs HTTP 422 ``string_too_long`` (max 200).
    Mutates dict plans in place; rebuilds typed MusicPrompt sections when needed.
    """
    if composition_plan is None:
        return composition_plan

    if isinstance(composition_plan, dict):
        sections = composition_plan.get("sections")
        if isinstance(sections, list):
            for section in sections:
                if (
                    isinstance(section, dict)
                    and "lines" in section
                    and isinstance(section["lines"], list)
                ):
                    section["lines"] = [
                        _truncate_music_line(line, max_chars)
                        for line in section["lines"]
                    ]
        chunks = composition_plan.get("chunks")
        if isinstance(chunks, list):
            for chunk in chunks:
                if isinstance(chunk, dict) and "text" in chunk:
                    chunk["text"] = _truncate_music_line(chunk["text"], max_chars)
        return composition_plan

    # Typed MusicPrompt / SongSection objects
    sections = getattr(composition_plan, "sections", None)
    if sections:
        for section in sections:
            lines = getattr(section, "lines", None)
            if isinstance(lines, list):
                truncated = [_truncate_music_line(line, max_chars) for line in lines]
                try:
                    section.lines = truncated
                except Exception:  # noqa: BLE001
                    pass
    return composition_plan


def _clamp_music_duration_ms(
    value: Any,
    *,
    lo: int = _MUSIC_DURATION_MS_MIN,
    hi: int = _MUSIC_DURATION_MS_MAX,
) -> int:
    """Cast duration to ``int`` ms and clamp to allowed Music API bounds."""
    try:
        ms = int(round(float(value)))
    except (TypeError, ValueError):
        ms = lo
    return max(lo, min(hi, ms))


def _is_unprocessable_entity(exc: BaseException) -> bool:
    """True for ElevenLabs HTTP 422 / UnprocessableEntityError."""
    name = type(exc).__name__
    if name == "UnprocessableEntityError":
        return True
    status = getattr(exc, "status_code", None)
    if status == 422:
        return True
    # Fern / httpx wrappers sometimes stash status on .status or .response
    if getattr(exc, "status", None) == 422:
        return True
    resp = getattr(exc, "response", None)
    if resp is not None and getattr(resp, "status_code", None) == 422:
        return True
    return "422" in str(exc) and "Unprocessable" in name


def _local_music_failsafe(output_path: Path | None = None) -> "Path | None":
    """Return a local ambient pad path so the reel pipeline never aborts."""
    for _rel in (
        Path("assets") / "master_mei" / "audio" / "ambient_cinematic_pad.mp3",
        Path("assets") / "audio" / "ambient_mystery_loop.mp3",
        Path("assets") / "master_mei" / "audio",
    ):
        _local = app_config.ENGINE_ROOT / _rel
        if _local.is_file():
            logger.warning(
                "Music API exhausted — using local ambient fail-safe: %s",
                _local.name,
            )
            return _local
        if _local.is_dir():
            for cand in sorted(_local.glob("*.mp3")):
                # Skip rain / storm beds (banned for Master Mei mix)
                low = cand.name.lower()
                if any(bad in low for bad in ("rain", "storm", "thunder", "water")):
                    continue
                logger.warning(
                    "Music API exhausted — using local ambient fail-safe: %s",
                    cand.name,
                )
                return cand
    logger.warning(
        "Music API exhausted and no local ambient pad found — "
        "reel will continue with normalized TTS + SFX only."
    )
    return None


# Last successful compose path for tests / diagnostics:
#   "plan/music_v2" | "plan/music_v1" | "prompt/..." | "ambient" | "local" | None
LAST_MUSIC_COMPOSE_MODE: str | None = None


def _mei_section_styles() -> tuple[list[str], list[str], list[str], list[str]]:
    """Shared positive/negative style lists — industrial cyberpunk percussion bed."""
    siege_pos = _sanitize_style_list([
        "industrial cyberpunk percussion",
        "driving cinematic drums from the start",
        "dark metallic hits",
        "tense rhythmic pulse",
        "dystopian synth bass",
        "tempo 90 to 110 BPM",
        "challenging epic build",
        "instrumental",
    ])
    siege_neg = _sanitize_style_list([
        "cheerful pop",
        "bright EDM drop",
        "soft ambient only",
        "long silent intro",
        "vocals",
        "lyrics",
        "acoustic folk",
    ])
    awaken_pos = _sanitize_style_list([
        "industrial percussion swell",
        "driving cinematic drums",
        "metallic cyber hits",
        "dark synth bass",
        "tempo 100 BPM",
        "inspiring dystopian resolve",
        "instrumental",
    ])
    awaken_neg = _sanitize_style_list([
        "overly cheerful",
        "pop acoustic",
        "screaming vocals",
        "lyrics",
        "soft piano ballad",
        "long fade-in silence",
    ])
    return siege_pos, siege_neg, awaken_pos, awaken_neg


def _mystery_section_styles() -> tuple[list[str], list[str], list[str], list[str]]:
    """Positive/negative styles — dark ancient mystery bed with mid-reel deepening."""
    hook_pos = _sanitize_style_list([
        "cinematic dark ancient mystery",
        "subtle suspense from the first second",
        "deep ambient drones",
        "eerie atmospheric pads",
        "slow tempo",
        "hushed tension",
        "instrumental",
        "no vocals",
    ])
    hook_neg = _sanitize_style_list([
        "upbeat",
        "EDM",
        "party",
        "driving drums",
        "cheerful pop",
        "vocals",
        "lyrics",
        "long silent intro",
        "fast BPM",
    ])
    depth_pos = _sanitize_style_list([
        "cinematic dark ancient mystery",
        "slower heavier darker mysterious rhythm",
        "slow heavy rhythmic frame drums",
        "deep sub-bass drone",
        "eerie atmospheric pads",
        "low brass and strings",
        "eerie woodwinds",
        "ancient Egyptian undertones",
        "slow tempo",
        "instrumental",
        "no vocals",
    ])
    depth_neg = _sanitize_style_list([
        "upbeat",
        "EDM",
        "party",
        "cheerful pop",
        "driving cyberpunk drums",
        "vocals",
        "lyrics",
        "bright synth lead",
        "fast BPM",
    ])
    return hook_pos, hook_neg, depth_pos, depth_neg


def _load_music_prompt_directive(
    directive_path: "Path | None" = None,
    *,
    style_profile: str = "warrior",
) -> str:
    profile = (style_profile or "warrior").strip().lower()
    candidates: list[Path] = []
    if directive_path is not None:
        candidates.append(Path(directive_path))
    if profile == "mystery":
        candidates.append(_MUSIC_PROMPT_MYSTERY_DIRECTIVE_FILE)
        default = MUSIC_PROMPT_MYSTERY_SYSTEM_DIRECTIVE
    else:
        candidates.append(_MUSIC_PROMPT_DIRECTIVE_FILE)
        default = MUSIC_PROMPT_SYSTEM_DIRECTIVE
    for path in candidates:
        try:
            if path.is_file():
                text = path.read_text(encoding="utf-8").strip()
                if text:
                    return text
        except Exception:  # noqa: BLE001
            continue
    return default


def generate_dynamic_music_prompt(
    topic: str = "",
    *,
    subject: str = "",
    directive_path: "Path | None" = None,
    style_profile: str = "warrior",
    channel_name: str = "",
) -> str:
    """
    LLM ``music_prompt`` task — unique ElevenLabs prompt per video.

    ``style_profile``:
      - ``warrior`` — industrial cyberpunk percussion (master_mei)
      - ``mystery`` — cinematic dark ancient mystery (hook suspense → heavier mid/end)
    Falls back to a deterministic unique prompt if Gemini is unavailable.
    """
    profile = (style_profile or "warrior").strip().lower()
    directive = _load_music_prompt_directive(
        directive_path, style_profile=profile,
    )
    theme = (topic or subject or "sovereignty against the digital matrix").strip()
    if profile == "mystery":
        fallback = _truncate_music_line(
            f"{_MYSTERY_MUSIC_TEMPLATE} Theme: {theme[:40]}.",
            _MUSIC_PROMPT_TARGET_CHARS,
        )
    else:
        fallback = _truncate_music_line(
            f"{_MUSIC_SIMPLE_PROMPT}. Theme: {theme[:40]}. No silent intro.",
            _MUSIC_PROMPT_TARGET_CHARS,
        )
    api_key = getattr(app_config, "GEMINI_API_KEY", "") or ""
    if not api_key:
        logger.info("music_prompt | no GEMINI_API_KEY — using deterministic fallback")
        return fallback

    # RAG-driven per-channel music guidance. If the channel has a
    # music_rules section it is prepended to the user block; if not, the
    # bridge returns "" and a WARNING is logged once per channel/section
    # (see core_engine/channel_rag_bridge.py gap docstring).
    _rag_music_block = ""
    try:
        from core_engine.channel_rag_bridge import get_music_guidance  # noqa: PLC0415

        _rag_music_block = get_music_guidance(channel_name)
    except Exception:  # noqa: BLE001
        _rag_music_block = ""

    user_block = (
        f"{directive}\n\n"
        + (f"{_rag_music_block}\n\n" if _rag_music_block else "")
        + f"Video topic: {theme}\n\n"
        + (
            "Progression: opening hook = subtle suspense drones; "
            "mid/end = slower heavier darker mysterious rhythm with frame drums "
            "and deep bass.\n\n"
            if profile == "mystery"
            else ""
        )
        + "Return ONLY the ElevenLabs music prompt as ONE concise line, strictly under "
        "150 characters. Do not write long paragraphs or excessive descriptors. "
        "No quotes, no markdown, no explanation."
    )
    try:
        from avatar_engine.providers.gemini_utils import (
            build_model_chain,
            generate_content_with_model_fallback,
            make_gemini_client_with_fallback,
        )

        client = make_gemini_client_with_fallback(api_key)
        chain = build_model_chain(client, capability_type="text", preferred=None)
        if not chain:
            chain = [
                "models/gemini-2.5-flash",
                "models/gemini-2.0-flash",
                "models/gemini-flash-latest",
            ]
        response = generate_content_with_model_fallback(
            client, chain, contents=[user_block],
        )
        text = ""
        if response is not None:
            text = (getattr(response, "text", None) or "").strip()
            if not text and getattr(response, "candidates", None):
                try:
                    parts = response.candidates[0].content.parts
                    text = " ".join(getattr(p, "text", "") or "" for p in parts).strip()
                except Exception:  # noqa: BLE001
                    text = ""
        # Strip fences / quotes
        text = re.sub(r"^```(?:\w+)?\s*|\s*```$", "", text).strip()
        text = text.strip('"').strip("'").strip()
        # Collapse whitespace / newlines into a single line before length checks
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) >= 20:
            # Soft-enforce style cues if LLM omitted them (then hard-cap)
            low = text.lower()
            if profile == "mystery":
                # Strip any upbeat / fast-tempo bleed the LLM slipped in.
                # Multi-word phrases before single tokens so we don't leave
                # orphan "drums" / "beat" fragments behind.
                _MYSTERY_FORBIDDEN = (
                    "driving beat", "driving drums", "driving percussion",
                    "percussion groove", "drum groove", "fast paced",
                    "fast-paced", "fast tempo", "high tempo",
                    "energetic", "upbeat", "danceable", "dance beat",
                    "dance track", "edm", "pop beat", "party", "festive",
                    "festa", "celebratory", "triumphant", "uplifting",
                    "bright", "major key", "punchy drums", "punchy beat",
                    "drum kit", "industrial cyberpunk drums",
                    "cinematic drums from bar one", "marching",
                    "martial cadence", "workout", "gym", "hype",
                    "epic heroic swell", "epic-heroic swell",
                    "80 bpm", "85 bpm", "90 bpm", "95 bpm", "100 bpm",
                    "105 bpm", "110 bpm", "115 bpm", "120 bpm",
                )
                for bad in _MYSTERY_FORBIDDEN:
                    if bad in low:
                        text = re.sub(
                            re.escape(bad), "", text, flags=re.IGNORECASE,
                        )
                        text = re.sub(r"\s+", " ", text).strip(" .,")
                        low = text.lower()
                if "bpm" not in low and len(text) < _MUSIC_PROMPT_TARGET_CHARS - 20:
                    text = f"{text} Slow tempo 55 BPM."
                if (
                    "drone" not in low
                    and "ambient" not in low
                    and len(text) < _MUSIC_PROMPT_TARGET_CHARS - 28
                ):
                    text = f"{text} Deep ambient drones."
                if (
                    "mysterious" not in low
                    and "mystery" not in low
                    and "dark" not in low
                    and "somber" not in low
                    and len(text) < _MUSIC_PROMPT_TARGET_CHARS - 16
                ):
                    text = f"{text} Dark mysterious."
                if (
                    "no beat" not in low
                    and "no driving" not in low
                    and "minimal percussion" not in low
                    and "sparse" not in low
                    and len(text) < _MUSIC_PROMPT_TARGET_CHARS - 22
                ):
                    text = f"{text} Minimal percussion, no driving beat."
                if (
                    "no voice" not in low
                    and "no vocal" not in low
                    and "instrumental" not in low
                    and len(text) < _MUSIC_PROMPT_TARGET_CHARS - 12
                ):
                    text = f"{text} No voice."
            else:
                if "bpm" not in low and len(text) < _MUSIC_PROMPT_TARGET_CHARS - 18:
                    text = f"{text} Tempo 95 BPM."
                if (
                    "drum" not in low
                    and "percussion" not in low
                    and len(text) < _MUSIC_PROMPT_TARGET_CHARS - 28
                ):
                    text = f"{text} Industrial cyberpunk drums."
            if "instrumental" not in low and len(text) < _MUSIC_PROMPT_TARGET_CHARS - 14:
                text = f"{text} Instrumental only."
            text = _truncate_music_line(text, _MUSIC_PROMPT_TARGET_CHARS)
            logger.info(
                "music_prompt | LLM generated (%d chars, style=%s)",
                len(text), profile,
            )
            return text
        logger.warning("music_prompt | LLM empty/short — using fallback")
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "music_prompt | LLM failed (%s: %s) — using fallback",
            type(exc).__name__, str(exc)[:160],
        )
    return fallback


def build_mei_music_v2_plan(
    total_ms: int,
    *,
    music_prompt: str = "",
    style_profile: str = "warrior",
) -> dict[str, Any]:
    """
    Build a ``music_v2`` chunk-based composition plan.

    Warrior: Chunk 1 Melancholic siege → Chunk 2 Challenging resolve.
    Mystery: Chunk 1 subtle hook suspense → Chunk 2 slower heavier darker rhythm.
    Optional ``music_prompt`` (LLM) is injected into both chunk texts.
    """
    total = _clamp_music_duration_ms(
        total_ms, lo=int(_MUSIC_MIN_DURATION_S * 1000), hi=_MUSIC_DURATION_MS_MAX,
    )
    chunk1 = _clamp_music_duration_ms(
        15_000, lo=_MUSIC_SECTION_MS_MIN, hi=_MUSIC_SECTION_MS_MAX,
    )
    chunk2 = _clamp_music_duration_ms(
        total - chunk1, lo=_MUSIC_SECTION_MS_MIN, hi=_MUSIC_SECTION_MS_MAX,
    )
    mystery = (style_profile or "warrior").strip().lower() == "mystery"
    if mystery:
        c1_pos, c1_neg, c2_pos, c2_neg = _mystery_section_styles()
        h1, h2 = "[Subtle Suspense] ", "[Darker Depth] "
        default_prompt = _MYSTERY_MUSIC_TEMPLATE
    else:
        c1_pos, c1_neg, c2_pos, c2_neg = _mei_section_styles()
        h1, h2 = "[Melancholic Siege] ", "[Challenging Resolve] "
        default_prompt = _MUSIC_SIMPLE_PROMPT
    # Cap prompt body so "HEADER + body + {instrumental}" stays ≤180 chars.
    _v2_body_budget = max(
        40,
        _MUSIC_LINE_MAX_CHARS - max(len(h1), len(h2)) - len(" {instrumental}"),
    )
    prompt_body = _truncate_music_line(
        (music_prompt or default_prompt).strip(),
        min(_MUSIC_PROMPT_TARGET_CHARS, _v2_body_budget),
    )

    plan = {
        "chunks": [
            {
                "text": f"{h1}{prompt_body} {{instrumental}}",
                "duration_ms": int(chunk1),
                "positive_styles": c1_pos,
                "negative_styles": c1_neg,
                "context_adherence": "high",
            },
            {
                "text": f"{h2}{prompt_body} {{instrumental}}",
                "duration_ms": int(chunk2),
                "positive_styles": c2_pos,
                "negative_styles": c2_neg,
                "context_adherence": "high",
            },
        ]
    }
    return _enforce_composition_plan_line_limits(plan)


def build_mei_music_v1_plan(
    total_ms: int,
    *,
    music_prompt: str = "",
    style_profile: str = "warrior",
) -> Any:
    """
    Build a typed ``MusicPrompt`` (``SongSection``) for ``music_v1`` only.

    Docs: MusicPrompt with ``sections`` / ``positive_global_styles`` is valid
    exclusively for ``model_id=\"music_v1\"``. Using it with ``music_v2`` → 422.
    """
    total = _clamp_music_duration_ms(total_ms, lo=18_000, hi=_MUSIC_DURATION_MS_MAX)
    section1 = _clamp_music_duration_ms(
        15_000, lo=_MUSIC_SECTION_MS_MIN, hi=_MUSIC_SECTION_MS_MAX,
    )
    section2 = _clamp_music_duration_ms(
        total - section1, lo=_MUSIC_SECTION_MS_MIN, hi=_MUSIC_SECTION_MS_MAX,
    )
    mystery = (style_profile or "warrior").strip().lower() == "mystery"
    if mystery:
        c1_pos, c1_neg, c2_pos, c2_neg = _mystery_section_styles()
        n1, n2 = "Subtle Suspense", "Darker Depth"
        default_prompt = _MYSTERY_MUSIC_TEMPLATE
        pos_global = _sanitize_style_list([
            "instrumental", "cinematic", "dark ambient", "ancient mystery",
        ])
        neg_global = _sanitize_style_list([
            "vocals", "lyrics", "pop", "upbeat", "cheerful", "EDM", "party",
        ])
    else:
        c1_pos, c1_neg, c2_pos, c2_neg = _mei_section_styles()
        n1, n2 = "Melancholic Siege", "Challenging Resolve"
        default_prompt = _MUSIC_SIMPLE_PROMPT
        pos_global = _sanitize_style_list([
            "instrumental", "cinematic", "melancholic", "cello",
        ])
        neg_global = _sanitize_style_list([
            "vocals", "lyrics", "pop", "upbeat", "cheerful", "fast drums",
        ])
    line = _truncate_music_line(
        (music_prompt or default_prompt).strip() or "{instrumental}",
        _MUSIC_LINE_MAX_CHARS,
    )

    try:
        from elevenlabs import MusicPrompt, SongSection  # type: ignore
    except ImportError:
        # Dict fallback matching MusicPrompt JSON schema
        plan = {
            "positive_global_styles": pos_global,
            "negative_global_styles": neg_global,
            "sections": [
                {
                    "section_name": n1,
                    "positive_local_styles": c1_pos,
                    "negative_local_styles": c1_neg,
                    "duration_ms": int(section1),
                    "lines": [line, "{instrumental}"],
                },
                {
                    "section_name": n2,
                    "positive_local_styles": c2_pos,
                    "negative_local_styles": c2_neg,
                    "duration_ms": int(section2),
                    "lines": [line, "{instrumental}"],
                },
            ],
        }
        return _enforce_composition_plan_line_limits(plan)

    plan = MusicPrompt(
        positive_global_styles=pos_global,
        negative_global_styles=neg_global,
        sections=[
            SongSection(
                section_name=n1,
                positive_local_styles=c1_pos,
                negative_local_styles=c1_neg,
                duration_ms=int(section1),
                lines=[line, "{instrumental}"],
            ),
            SongSection(
                section_name=n2,
                positive_local_styles=c2_pos,
                negative_local_styles=c2_neg,
                duration_ms=int(section2),
                lines=[line, "{instrumental}"],
            ),
        ],
    )
    return _enforce_composition_plan_line_limits(plan)


def generate_music_v2_bed(
    output_path: Path,
    *,
    duration_seconds: float = 80.0,
    music_prompt: str = "",
    topic: str = "",
    directive_path: "Path | None" = None,
    style_profile: str = "warrior",
    channel_name: str = "",
) -> "Path | None":
    """
    Compose a background music bed via ElevenLabs Music ``music.compose``.

    Fallback chain (never aborts the reel pipeline)
    -----------------------------------------------
    1. ``music_v2`` chunk ``composition_plan`` (no ``force_instrumental``)
    2. ``music_v1`` typed ``MusicPrompt`` / ``SongSection`` plan
    3. On HTTP 422 → simple prompt string (``force_instrumental`` allowed here)
    4. Legacy SFX ambient tile
    5. Local ambient pad — or ``None`` (TTS + SFX only)

    Important: ``force_instrumental`` may ONLY be sent with ``prompt``.
    Sending it with ``composition_plan`` returns HTTP 422.

    When ``music_prompt`` is empty, generates a unique LLM prompt via
    ``generate_dynamic_music_prompt(topic, style_profile=…)`` for every video.
    """
    global LAST_MUSIC_COMPOSE_MODE
    LAST_MUSIC_COMPOSE_MODE = None

    api_key = app_config.ELEVENLABS_API_KEY
    if not api_key:
        logger.warning("ELEVENLABS_API_KEY not set — music bed skipped.")
        LAST_MUSIC_COMPOSE_MODE = "local"
        return _local_music_failsafe(Path(output_path) if output_path else None)

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    # Generate ≥40s of BGM, then loop in mix
    _dur_s = max(_MUSIC_MIN_DURATION_S, float(duration_seconds or _MUSIC_MIN_DURATION_S))
    total_ms = _clamp_music_duration_ms(
        int(round(_dur_s * 1000.0)),
        lo=int(_MUSIC_MIN_DURATION_S * 1000),
        hi=_MUSIC_DURATION_MS_MAX,
    )
    _style = (style_profile or "warrior").strip().lower()
    dyn_prompt = _truncate_music_line(
        (music_prompt or "").strip(),
        _MUSIC_PROMPT_TARGET_CHARS,
    )
    if not dyn_prompt:
        dyn_prompt = generate_dynamic_music_prompt(
            topic=topic,
            directive_path=directive_path,
            style_profile=_style,
            channel_name=channel_name,
        )
    dyn_prompt = _truncate_music_line(dyn_prompt, _MUSIC_PROMPT_TARGET_CHARS)
    logger.info(
        "Music v2 bed request | duration=%.1fs (min=%.0fs) | prompt_chars=%d",
        _dur_s, _MUSIC_MIN_DURATION_S, len(dyn_prompt),
    )
    v2_plan = _enforce_composition_plan_line_limits(
        build_mei_music_v2_plan(
            total_ms, music_prompt=dyn_prompt, style_profile=_style,
        )
    )
    v1_plan = _enforce_composition_plan_line_limits(
        build_mei_music_v1_plan(
            total_ms, music_prompt=dyn_prompt, style_profile=_style,
        )
    )
    saw_422 = False

    def _persist(audio: Any, *, label: str) -> "Path | None":
        global LAST_MUSIC_COMPOSE_MODE
        _write_audio_iterator(audio, output_path)
        if output_path.is_file() and output_path.stat().st_size > 1000:
            resample_audio_48k(output_path, apply_loudnorm=True)
            LAST_MUSIC_COMPOSE_MODE = label
            logger.info(
                "Music bed saved → %s (%.1f KB, %s)",
                output_path.name, output_path.stat().st_size / 1024, label,
            )
            return output_path
        logger.warning("Music compose returned empty payload (%s).", label)
        return None

    try:
        from elevenlabs import ElevenLabs  # type: ignore

        try:
            from elevenlabs import UnprocessableEntityError as _UE  # type: ignore
        except Exception:  # noqa: BLE001
            _UE = ()  # type: ignore[assignment]

        client = ElevenLabs(api_key=api_key)

        # --- Pass 1a: music_v2 chunk plan (official v2 schema) ---
        # Never pass force_instrumental / music_length_ms with composition_plan.
        try:
            n_chunks = len(v2_plan.get("chunks") or [])
            logger.info(
                "Composing music bed | model=%s total_ms=%d chunks=%d",
                _MUSIC_V2_MODEL, total_ms, n_chunks,
            )
            v2_plan = _enforce_composition_plan_line_limits(v2_plan)
            audio = client.music.compose(
                composition_plan=v2_plan,  # type: ignore[arg-type]
                model_id=_MUSIC_V2_MODEL,  # type: ignore[arg-type]
            )
            saved = _persist(audio, label=f"plan/{_MUSIC_V2_MODEL}")
            if saved is not None:
                return saved
        except Exception as model_exc:  # noqa: BLE001
            if (_UE and isinstance(model_exc, _UE)) or _is_unprocessable_entity(model_exc):
                saw_422 = True
                logger.warning(
                    "music.compose composition_plan HTTP 422 (%s / %s: %s) — "
                    "trying music_v1 MusicPrompt.",
                    _MUSIC_V2_MODEL,
                    type(model_exc).__name__,
                    str(getattr(model_exc, "body", model_exc))[:200],
                )
            else:
                logger.warning(
                    "music.compose v2 plan failed (%s: %s) — trying music_v1.",
                    type(model_exc).__name__, str(model_exc)[:180],
                )

        # --- Pass 1b: music_v1 typed MusicPrompt / SongSection ---
        try:
            n_sec = (
                len(getattr(v1_plan, "sections", None) or [])
                if not isinstance(v1_plan, dict)
                else len(v1_plan.get("sections") or [])
            )
            logger.info(
                "Composing music bed | model=%s total_ms=%d sections=%d (MusicPrompt)",
                _MUSIC_V1_MODEL, total_ms, n_sec,
            )
            v1_plan = _enforce_composition_plan_line_limits(v1_plan)
            audio = client.music.compose(
                composition_plan=v1_plan,
                model_id=_MUSIC_V1_MODEL,
                respect_sections_durations=True,
            )
            saved = _persist(audio, label=f"plan/{_MUSIC_V1_MODEL}")
            if saved is not None:
                return saved
        except Exception as model_exc:  # noqa: BLE001
            if (_UE and isinstance(model_exc, _UE)) or _is_unprocessable_entity(model_exc):
                saw_422 = True
                logger.warning(
                    "music.compose composition_plan HTTP 422 (%s / %s: %s) — "
                    "will retry with simple prompt.",
                    _MUSIC_V1_MODEL,
                    type(model_exc).__name__,
                    str(getattr(model_exc, "body", model_exc))[:200],
                )
            else:
                logger.warning(
                    "music.compose v1 plan failed (%s: %s) — trying simple prompt.",
                    type(model_exc).__name__, str(model_exc)[:180],
                )

        # --- Pass 2: simple prompt (force_instrumental OK only here) ---
        simple_prompt = _truncate_music_line(
            dyn_prompt or (
                _MYSTERY_MUSIC_TEMPLATE if _style == "mystery" else _MUSIC_SIMPLE_PROMPT
            ),
            _MUSIC_LINE_MAX_CHARS,
        )
        if _style == "mystery":
            _style_tags = [
                "cinematic dark ancient mystery soundtrack",
                "deep ambient drones",
                "slow heavy rhythmic frame drums",
                "eerie atmospheric pads",
                "slow tempo",
                "instrumental no vocals",
            ]
        else:
            _style_tags = [
                "industrial cyberpunk percussion",
                "driving cinematic drums",
                "dark metallic hits",
                "tempo 95 BPM",
                "instrumental",
            ]
        style_csv = _styles_to_csv(_sanitize_style_list(_style_tags))
        if style_csv and style_csv.lower() not in simple_prompt.lower():
            # Keep total under line max even after style append
            room = _MUSIC_LINE_MAX_CHARS - len(simple_prompt) - 10
            if room > 20:
                simple_prompt = (
                    f"{simple_prompt}. Styles: {_truncate_music_line(style_csv, room)}"
                )
            simple_prompt = _truncate_music_line(simple_prompt, _MUSIC_LINE_MAX_CHARS)

        for model in (_MUSIC_V2_MODEL, _MUSIC_V1_MODEL):
            try:
                logger.info(
                    "Composing music bed (simple prompt)%s | model=%s total_ms=%d",
                    " after 422" if saw_422 else "",
                    model,
                    total_ms,
                )
                kwargs: dict[str, Any] = {
                    "prompt": simple_prompt,
                    "force_instrumental": True,
                    "music_length_ms": int(total_ms),
                }
                if model:
                    kwargs["model_id"] = model
                audio = client.music.compose(**kwargs)
                saved = _persist(audio, label=f"prompt/{model}")
                if saved is not None:
                    return saved
            except Exception as prompt_exc:  # noqa: BLE001
                if (_UE and isinstance(prompt_exc, _UE)) or _is_unprocessable_entity(prompt_exc):
                    saw_422 = True
                    logger.warning(
                        "music.compose simple prompt HTTP 422 (%s) — next fallback.",
                        model,
                    )
                else:
                    logger.warning(
                        "music.compose prompt failed for %s (%s: %s).",
                        model, type(prompt_exc).__name__, str(prompt_exc)[:180],
                    )

    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Music SDK unavailable (%s: %s) — falling back to ambient.",
            type(exc).__name__, str(exc)[:180],
        )

    # --- Pass 3: SFX ambient tile ---
    ambient = generate_ambient_track(output_path, duration_seconds=duration_seconds)
    if ambient is not None:
        LAST_MUSIC_COMPOSE_MODE = "ambient"
        return ambient

    # --- Pass 4: local pad / TTS+SFX-only ---
    LAST_MUSIC_COMPOSE_MODE = "local"
    return _local_music_failsafe(output_path)


def generate_master_mei_soundscape(
    output_dir: Path,
    *,
    stem: str = "mei",
    duration_seconds: float = 80.0,
    include_impact_sfx: bool = False,
    topic: str = "",
    music_prompt: str = "",
    directive_path: "Path | None" = None,
    style_profile: str = "warrior",
    channel_name: str = "",
) -> tuple["Path | None", "Path | None"]:
    """
    Generate music_v2 BGM bed (≥40s) + optional impact.

    Used by master_mei (warrior) and ancient_knowledge (mystery) three-channel mix.
    Atmosphere drone is generated separately via ``generate_ambient_track``.

    Returns ``(music_bed_path, impact_sfx_path|None)``.
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    music = generate_music_v2_bed(
        out / f"{stem}_music_v2.mp3",
        duration_seconds=max(_MUSIC_MIN_DURATION_S, float(duration_seconds or 40.0)),
        music_prompt=music_prompt,
        topic=topic,
        directive_path=directive_path,
        style_profile=style_profile,
        channel_name=channel_name,
    )
    impact: "Path | None" = None
    if include_impact_sfx:
        impact = generate_impact_sfx(
            out / f"{stem}_impact_braam.mp3",
            prompt=_IMPACT_SFX_PROMPT,
            duration_seconds=_IMPACT_SFX_DURATION_S,
        )
    return music, impact


# ---------------------------------------------------------------------------
# CTA cache + local audio assembly (credit optimization)
# ---------------------------------------------------------------------------

_DEFAULT_CTA_CACHE: Path = (
    Path(__file__).resolve().parents[1]
    / "channels_config"
    / "master_mei"
    / "audio"
    / "cta_cache.mp3"
)
_DEFAULT_BGM_DIR: Path = (
    Path(__file__).resolve().parents[1]
    / "channels_config"
    / "master_mei"
    / "audio"
    / "bgm"
)
_DEFAULT_SFX_LOOP: Path = (
    Path(__file__).resolve().parents[1]
    / "channels_config"
    / "master_mei"
    / "audio"
    / "sfx"
    / "dark_atmosphere_loop.wav"
)


def default_cta_cache_path() -> Path:
    """Canonical on-disk CTA cache for Master Mei (shared across reel variants)."""
    return _DEFAULT_CTA_CACHE


def default_bgm_folder() -> Path:
    return _DEFAULT_BGM_DIR


def default_sfx_loop_path() -> Path:
    return _DEFAULT_SFX_LOOP


def ensure_cached_cta_voiceover(
    cta_text: str,
    cache_path: "Path | None" = None,
    *,
    voice_id: str | None = None,
    model_id: str = _DEFAULT_TTS_MODEL,
    speed: float | None = None,
    voice_settings: dict | None = None,
    expressive_mode: bool = True,
    force_regenerate: bool = False,
) -> "Path | None":
    """
    Return a reusable CTA mp3, generating it via ElevenLabs only when missing.

    Saves ~30% TTS character spend by avoiding re-synthesizing the same CTA
    on every reel variant.
    """
    text = (cta_text or "").strip()
    if not text:
        return None
    out = Path(cache_path) if cache_path else default_cta_cache_path()
    if out.is_file() and out.stat().st_size > 500 and not force_regenerate:
        logger.info("CTA cache HIT → %s (%d bytes)", out, out.stat().st_size)
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    logger.info("CTA cache MISS — generating once | chars=%d → %s", len(text), out)
    return generate_voiceover(
        text,
        out,
        voice_id=voice_id,
        model_id=model_id,
        speed=speed,
        voice_settings=voice_settings,
        expressive_mode=expressive_mode,
    )


def generate_optimized_voiceover(
    text_body: str,
    output_path: Path,
    *,
    cta_cache_path: "Path | None" = None,
    cta_text: str = "",
    voice_id: str | None = None,
    model_id: str = _DEFAULT_TTS_MODEL,
    speed: float | None = None,
    voice_settings: dict | None = None,
    enable_ssml: bool | None = None,
    expressive_mode: bool = True,
    gap_s: float = 0.4,
) -> tuple[Path, float]:
    """
    Generate ElevenLabs speech only for the unique script body, then append a
    pre-recorded / cached CTA locally to preserve API credits.

    Returns ``(combined_mp3_path, total_duration_s)``.
    """
    from moviepy import AudioFileClip, concatenate_audioclips
    from moviepy.audio.AudioClip import AudioArrayClip
    import numpy as np

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    body_path = output_path.with_name(output_path.stem + "_body.mp3")

    generate_voiceover(
        text_body,
        body_path,
        voice_id=voice_id,
        model_id=model_id,
        speed=speed,
        voice_settings=voice_settings,
        enable_ssml=enable_ssml,
        expressive_mode=expressive_mode,
    )

    cache = Path(cta_cache_path) if cta_cache_path else default_cta_cache_path()
    if cta_text and (not cache.is_file() or cache.stat().st_size < 500):
        ensure_cached_cta_voiceover(
            cta_text,
            cache,
            voice_id=voice_id,
            model_id=model_id,
            speed=speed,
            voice_settings=voice_settings,
            expressive_mode=expressive_mode,
        )

    body_clip = AudioFileClip(str(body_path))
    body_dur = float(body_clip.duration or 0.0)

    if cache.is_file() and cache.stat().st_size > 500:
        cta_clip = AudioFileClip(str(cache))
        sr = 44100
        sil = np.zeros((int(sr * max(0.0, gap_s)), 2), dtype=np.float32)
        silence = AudioArrayClip(sil, fps=sr)
        combined = concatenate_audioclips([body_clip, silence, cta_clip])
        combined.write_audiofile(str(output_path), fps=sr, logger=None)
        total = float(combined.duration or (body_dur + gap_s + float(cta_clip.duration or 0.0)))
        for c in (body_clip, cta_clip, silence, combined):
            try:
                c.close()
            except Exception:
                pass
        logger.info(
            "Optimized VO | body=%.2fs + gap=%.2fs + cached CTA → total=%.2fs → %s",
            body_dur, gap_s, total, output_path.name,
        )
        return output_path, total

    # No CTA cache — copy body as final
    try:
        output_path.write_bytes(body_path.read_bytes())
    except Exception:
        body_clip.close()
        return body_path, body_dur
    body_clip.close()
    logger.warning("CTA cache not found — using narration body only → %s", output_path.name)
    return output_path, body_dur


def assemble_final_audio_track(
    vo_path: Path,
    output_path: Path,
    *,
    bgm_folder: "Path | None" = None,
    sfx_loop_path: "Path | None" = None,
    bgm_start_s: float = _MIX_MUSIC_START_OFFSET_S,
    bgm_fade_in_s: float = _MIX_AMBIENT_FADE_IN_S,
    bgm_fade_out_s: float = 3.0,
    sfx_volume: float = _MIX_SFX_VOLUME,
    bgm_volume: float = _MIX_BGM_VOLUME,
    sfx_fade_in_s: float = _MIX_AMBIENT_FADE_IN_S,
) -> Path:
    """
    Mix continuous ambient SFX + delayed randomized BGM under a voiceover file.

    Pure local MoviePy assemble — no ElevenLabs / Flux spend.
    """
    import random
    from moviepy import AudioFileClip, CompositeAudioClip
    from moviepy.audio.fx import AudioFadeIn, AudioFadeOut, AudioLoop, MultiplyVolume

    vo_path = Path(vo_path)
    output_path = Path(output_path)
    bgm_dir = Path(bgm_folder) if bgm_folder else default_bgm_folder()
    sfx_path = Path(sfx_loop_path) if sfx_loop_path else default_sfx_loop_path()

    if not vo_path.is_file():
        raise FileNotFoundError(f"Voiceover not found: {vo_path}")
    if not sfx_path.is_file():
        raise FileNotFoundError(f"SFX loop not found: {sfx_path}")
    if not bgm_dir.is_dir():
        raise FileNotFoundError(f"BGM folder not found: {bgm_dir}")

    bgm_files = [
        p for p in bgm_dir.iterdir()
        if p.is_file() and p.suffix.lower() in {".mp3", ".wav"}
    ]
    if not bgm_files:
        raise FileNotFoundError(f"No BGM mp3/wav in {bgm_dir}")

    vo_clip = AudioFileClip(str(vo_path))
    total_duration = float(vo_clip.duration or 0.0)

    sfx_clip = AudioFileClip(str(sfx_path))
    _sfx_fx: list[Any] = [
        AudioLoop(duration=total_duration),
        MultiplyVolume(float(sfx_volume)),
    ]
    if float(sfx_fade_in_s or 0.0) > 0.01:
        _sfx_fx.insert(0, AudioFadeIn(float(sfx_fade_in_s)))
    sfx_loop = sfx_clip.with_effects(_sfx_fx)

    selected = random.choice(bgm_files)
    bgm_clip = AudioFileClip(str(selected))
    play_s = max(1.0, total_duration - float(bgm_start_s))
    bgm_slice = bgm_clip.subclipped(0, min(float(bgm_clip.duration or 1.0), play_s))
    bgm_timed = bgm_slice.with_effects(
        [
            AudioFadeIn(float(bgm_fade_in_s)),
            AudioFadeOut(float(bgm_fade_out_s)),
            MultiplyVolume(float(bgm_volume)),
        ]
    ).with_start(float(bgm_start_s))

    final = CompositeAudioClip([sfx_loop, bgm_timed, vo_clip])
    output_path.parent.mkdir(parents=True, exist_ok=True)
    final.write_audiofile(str(output_path), fps=44100, logger=None)
    logger.info(
        "Final audio assembled | vo=%.1fs bgm=%s start=%.1fs → %s",
        total_duration, selected.name, bgm_start_s, output_path.name,
    )
    for c in (vo_clip, sfx_clip, sfx_loop, bgm_clip, bgm_slice, bgm_timed, final):
        try:
            c.close()
        except Exception:
            pass
    return output_path


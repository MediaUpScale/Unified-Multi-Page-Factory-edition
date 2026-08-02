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
_NARRATION_SPEED: float = 0.92   # slow, stoic, authoritative
_AMBIENT_PROMPT: str = (
    "Dark ambient cinematic synth pad, deep sub-bass drone, subtle futuristic "
    "industrial machine hum, inspiring stoic atmosphere, seamless loop, 60 BPM, "
    "warm dark cinematic underscore, high production value, no vocals, no melody lead, "
    "NO rain, NO thunder, NO white noise, NO hiss, NO static, NO water drops, NO storm, "
    "NO generic noise bed"
)
_TARGET_SAMPLE_RATE_HZ: int = 48000
_SFX_CLIP_DURATION: float = 20.0
_SFX_MODEL_ID: str = "eleven_text_to_sound_v2"
_IMPACT_SFX_PROMPT: str = "Cinematic Braam, Dystopian Sub-Bass Drop"
_IMPACT_SFX_DURATION_S: float = 2.5
_MUSIC_V2_MODEL: str = "music_v2"
_MUSIC_V1_MODEL: str = "music_v1"
# ElevenLabs Music duration bounds (ms) — section / total
_MUSIC_DURATION_MS_MIN: int = 5_000
_MUSIC_DURATION_MS_MAX: int = 180_000
_MUSIC_SECTION_MS_MIN: int = 3_000
_MUSIC_SECTION_MS_MAX: int = 120_000
_MUSIC_SIMPLE_PROMPT: str = (
    "Epic dark synth theme, heavy sub-bass drone, stoic cinematic atmosphere, dystopian 100 bpm"
)

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
    """
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
    """
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

    payload = {
        "text": sfx_prompt,
        "model_id": _SFX_MODEL_ID,
        "duration_seconds": _SFX_CLIP_DURATION,
        "loop": True,
        "prompt_influence": 0.7,
    }
    headers = {
        "xi-api-key": api_key,
        "Content-Type": "application/json",
    }

    try:
        logger.info(
            "Generating SFX ambient tile | model=%s dur=%.0fs loop=True",
            _SFX_MODEL_ID, _SFX_CLIP_DURATION,
        )
        resp = _requests.post(
            "https://api.elevenlabs.io/v1/sound-generation",
            json=payload,
            headers=headers,
            timeout=15,
        )
        resp.raise_for_status()

        with open(output_path, "wb") as fh:
            fh.write(resp.content)

        # Ban rain/hiss: resample + light loudnorm so bed never distorts at mix
        resample_audio_48k(output_path, apply_loudnorm=True)
        logger.info("Ambient SFX tile saved → %s (%.1f KB)", output_path.name, len(resp.content) / 1024)
        return output_path

    except Exception as exc:  # noqa: BLE001
        logger.warning("ElevenLabs SFX request failed (%s) — checking local fallback.", exc)

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
    """Shared positive/negative style lists for Matrix Siege → Sovereign Awakening."""
    siege_pos = _sanitize_style_list([
        "dark ambient drone",
        "heavy sub-bass drone",
        "eerie mechanical tension",
        "dystopian cybernetic atmosphere",
        "low frequency rumble",
        "instrumental",
        "cinematic production",
    ])
    siege_neg = _sanitize_style_list([
        "upbeat melodies",
        "bright synth",
        "fast drums",
        "vocals",
        "lyrics",
    ])
    awaken_pos = _sanitize_style_list([
        "epic dark synth theme",
        "inspiring cinematic pads",
        "driving stoic bassline",
        "heroic resolve",
        "powerful dark synthwave",
        "100 bpm",
        "instrumental",
    ])
    awaken_neg = _sanitize_style_list([
        "overly cheerful",
        "pop acoustic",
        "screaming vocals",
        "lyrics",
    ])
    return siege_pos, siege_neg, awaken_pos, awaken_neg


def build_mei_music_v2_plan(total_ms: int) -> dict[str, Any]:
    """
    Build a ``music_v2`` chunk-based composition plan.

    Official ElevenLabs ``music_v2`` layout (NOT MusicPrompt)::

        {
          "chunks": [
            {
              "text": "[Section]\\n{instrumental}",
              "duration_ms": int,          # 3000–120000
              "positive_styles": list[str],
              "negative_styles": list[str],
              "context_adherence": "high",
            },
            ...
          ]
        }

    Chunk 1 (0–15000 ms): Ominous Matrix Siege
    Chunk 2 (15000–total): Stoic Sovereign Awakening
    """
    total = _clamp_music_duration_ms(total_ms, lo=18_000, hi=_MUSIC_DURATION_MS_MAX)
    chunk1 = _clamp_music_duration_ms(
        15_000, lo=_MUSIC_SECTION_MS_MIN, hi=_MUSIC_SECTION_MS_MAX,
    )
    chunk2 = _clamp_music_duration_ms(
        total - chunk1, lo=_MUSIC_SECTION_MS_MIN, hi=_MUSIC_SECTION_MS_MAX,
    )
    siege_pos, siege_neg, awaken_pos, awaken_neg = _mei_section_styles()

    return {
        "chunks": [
            {
                "text": "[Ominous Matrix Siege]\n{instrumental}",
                "duration_ms": int(chunk1),
                "positive_styles": siege_pos,
                "negative_styles": siege_neg,
                "context_adherence": "high",
            },
            {
                "text": "[Stoic Sovereign Awakening]\n{instrumental}",
                "duration_ms": int(chunk2),
                "positive_styles": awaken_pos,
                "negative_styles": awaken_neg,
                "context_adherence": "high",
            },
        ]
    }


def build_mei_music_v1_plan(total_ms: int) -> Any:
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
    siege_pos, siege_neg, awaken_pos, awaken_neg = _mei_section_styles()

    try:
        from elevenlabs import MusicPrompt, SongSection  # type: ignore
    except ImportError:
        # Dict fallback matching MusicPrompt JSON schema
        return {
            "positive_global_styles": _sanitize_style_list([
                "instrumental", "cinematic", "dark ambient",
            ]),
            "negative_global_styles": _sanitize_style_list([
                "vocals", "lyrics", "pop", "upbeat", "cheerful",
            ]),
            "sections": [
                {
                    "section_name": "Ominous Matrix Siege",
                    "positive_local_styles": siege_pos,
                    "negative_local_styles": siege_neg,
                    "duration_ms": int(section1),
                    "lines": ["{instrumental}"],
                },
                {
                    "section_name": "Stoic Sovereign Awakening",
                    "positive_local_styles": awaken_pos,
                    "negative_local_styles": awaken_neg,
                    "duration_ms": int(section2),
                    "lines": ["{instrumental}"],
                },
            ],
        }

    return MusicPrompt(
        positive_global_styles=_sanitize_style_list([
            "instrumental", "cinematic", "dark ambient",
        ]),
        negative_global_styles=_sanitize_style_list([
            "vocals", "lyrics", "pop", "upbeat", "cheerful",
        ]),
        sections=[
            SongSection(
                section_name="Ominous Matrix Siege",
                positive_local_styles=siege_pos,
                negative_local_styles=siege_neg,
                duration_ms=int(section1),
                lines=["{instrumental}"],
            ),
            SongSection(
                section_name="Stoic Sovereign Awakening",
                positive_local_styles=awaken_pos,
                negative_local_styles=awaken_neg,
                duration_ms=int(section2),
                lines=["{instrumental}"],
            ),
        ],
    )


def generate_music_v2_bed(
    output_path: Path,
    *,
    duration_seconds: float = 80.0,
) -> "Path | None":
    """
    Compose a Master Mei background bed via ElevenLabs Music ``music.compose``.

    Fallback chain (never aborts the reel pipeline)
    -----------------------------------------------
    1. ``music_v2`` chunk ``composition_plan`` (no ``force_instrumental``)
    2. ``music_v1`` typed ``MusicPrompt`` / ``SongSection`` plan
    3. On HTTP 422 → simple prompt string (``force_instrumental`` allowed here)
    4. Legacy SFX ambient tile
    5. Local ambient pad — or ``None`` (TTS + SFX only)

    Important: ``force_instrumental`` may ONLY be sent with ``prompt``.
    Sending it with ``composition_plan`` returns HTTP 422.
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
    total_ms = _clamp_music_duration_ms(
        int(round(float(duration_seconds) * 1000.0)),
        lo=18_000,
        hi=_MUSIC_DURATION_MS_MAX,
    )
    v2_plan = build_mei_music_v2_plan(total_ms)
    v1_plan = build_mei_music_v1_plan(total_ms)
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
        simple_prompt = _MUSIC_SIMPLE_PROMPT
        style_csv = _styles_to_csv(
            _sanitize_style_list([
                "epic dark synth theme",
                "heavy sub-bass drone",
                "stoic cinematic atmosphere",
                "instrumental",
            ])
        )
        if style_csv and style_csv.lower() not in simple_prompt.lower():
            simple_prompt = f"{simple_prompt}. Styles: {style_csv}"

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
) -> tuple["Path | None", "Path | None"]:
    """
    Generate the dual-layer Master Mei soundscape.

    Returns ``(music_bed_path, impact_sfx_path)``.
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    music = generate_music_v2_bed(
        out / f"{stem}_music_v2.mp3",
        duration_seconds=duration_seconds,
    )
    impact = generate_impact_sfx(
        out / f"{stem}_impact_braam.mp3",
        prompt=_IMPACT_SFX_PROMPT,
        duration_seconds=_IMPACT_SFX_DURATION_S,
    )
    return music, impact

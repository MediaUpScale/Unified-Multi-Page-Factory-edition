# -*- coding: utf-8 -*-
"""
ElevenLabs audio generation for ECONOMIC_REEL.

  generate_voiceover()     — TTS narration from hook text.
  generate_ambient_track() — Dark ambient soundscape via ElevenLabs SFX API.

Both functions return the output Path on success and raise on unrecoverable
failure so the caller can decide whether to proceed without audio.
"""
from __future__ import annotations

import logging
from pathlib import Path

import config as app_config

logger = logging.getLogger(__name__)

# Brian — deep, authoritative, high-engagement narrative tone
# eleven_multilingual_v2 gives the best character-per-credit efficiency for long scripts
_DEFAULT_VOICE_ID: str = "nPczCjzI2devNBz1zQrb"
_DEFAULT_TTS_MODEL: str = "eleven_multilingual_v2"

# Voice performance settings tuned for psychological documentary narration:
#   stability=0.45  — slight expressiveness variance keeps the voice human
#   similarity_boost=0.85 — locks the deep vocal character tightly
#   style=0.15      — subtle emotional colouring without over-acting
#   speed=1.07      — 1.05–1.10× for crisp, energetic short-form delivery
_DEFAULT_VOICE_SETTINGS = {
    "stability": 0.45,
    "similarity_boost": 0.85,
    "style": 0.15,
    "use_speaker_boost": True,
}
_NARRATION_SPEED: float = 1.05   # 1.05× for crisp energetic short-form delivery
_AMBIENT_PROMPT: str = (
    "Dark ambient cinematic synth pad, deep sub-bass drone, subtle futuristic "
    "industrial machine hum, inspiring stoic atmosphere, seamless loop, 60 BPM, "
    "warm dark cinematic underscore, high production value, no vocals, no melody lead, "
    "NO rain, NO thunder, NO white noise, NO hiss, NO static, NO water drops, NO storm, "
    "NO generic noise bed"
)
_TARGET_SAMPLE_RATE_HZ: int = 48000
# Fixed SFX clip length — ElevenLabs loop=true guarantees a seamless 20 s tile.
# The MoviePy layer in reel_sequence_engine.py concatenates enough copies to
# cover the full video duration, so the SFX request is always exactly 20 s.
_SFX_CLIP_DURATION: float = 20.0
_SFX_MODEL_ID: str = "eleven_text_to_sound_v2"


def _resolve_voice_settings(overrides: dict | None = None) -> dict:
    """Merge page-level VoiceSettings overrides onto engine defaults.

    Supports official ElevenLabs keys including ``speed`` (0.25–4.0).
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
    _vs = _resolve_voice_settings(voice_settings)
    # Prefer explicit speed arg, else voice_settings.speed, else engine default
    if speed is not None:
        _speed = float(speed)
    elif _vs.get("speed") is not None:
        _speed = float(_vs["speed"])
    else:
        _speed = _NARRATION_SPEED
    _ssml = bool(enable_ssml) if enable_ssml is not None else ("<break" in (text or "").lower())
    vs_obj, _speed = _build_voice_settings_obj(_VoiceSettings, _vs, _speed)

    logger.info(
        "Generating voiceover | voice=%s | model=%s | chars=%d | speed=%.2f | stability=%.2f | ssml=%s",
        vid, model_id, len(text), _speed, _vs["stability"], _ssml,
    )
    _tts_kwargs: dict = dict(
        voice_id=vid,
        text=text,
        model_id=model_id or _DEFAULT_TTS_MODEL,
        voice_settings=vs_obj,
        output_format="mp3_44100_128",
    )
    if _ssml:
        _tts_kwargs["enable_ssml_parsing"] = True
    try:
        # Prefer speed inside voice_settings; also pass top-level for SDK variants
        audio_stream = client.text_to_speech.convert(**_tts_kwargs, speed=_speed)
    except TypeError:
        # Older SDK — speed / ssml params may be unsupported
        logger.debug("ElevenLabs SDK param mismatch — retrying with reduced kwargs")
        _tts_kwargs.pop("enable_ssml_parsing", None)
        try:
            audio_stream = client.text_to_speech.convert(**_tts_kwargs, speed=_speed)
        except TypeError:
            audio_stream = client.text_to_speech.convert(**_tts_kwargs)

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
    _vs = _resolve_voice_settings(voice_settings)
    if speed is not None:
        _speed = float(speed)
    elif _vs.get("speed") is not None:
        _speed = float(_vs["speed"])
    else:
        _speed = _NARRATION_SPEED
    _ssml = bool(enable_ssml) if enable_ssml is not None else ("<break" in (text or "").lower())
    vs, _speed = _build_voice_settings_obj(_VoiceSettings, _vs, _speed)

    logger.info(
        "Generating voiceover+timestamps | voice=%s | model=%s | chars=%d | speed=%.2f | stability=%.2f | ssml=%s",
        vid, model_id, len(text), _speed, _vs["stability"], _ssml,
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
            model_id=model_id or _DEFAULT_TTS_MODEL,
            voice_settings=vs,
            output_format="mp3_44100_128",
        )
        if _ssml:
            _ts_kwargs["enable_ssml_parsing"] = True
        try:
            result = client.text_to_speech.convert_with_timestamps(
                **_ts_kwargs, speed=_speed
            )
        except TypeError:
            logger.debug("convert_with_timestamps: speed/ssml unsupported — retrying reduced kwargs")
            _ts_kwargs.pop("enable_ssml_parsing", None)
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
        _fb_kwargs: dict = dict(
            voice_id=vid, text=text, model_id=model_id,
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

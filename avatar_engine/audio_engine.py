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
_DEFAULT_VOICE_SETTINGS = {
    "stability": 0.45,
    "similarity_boost": 0.85,
    "style": 0.15,
    "use_speaker_boost": True,
}
_AMBIENT_PROMPT: str = (
    "Deep moody ambient drone with slow reverb tails. "
    "Dark psychological tension, subtle low-frequency rumble, no melody or percussion."
)
# ElevenLabs SFX max duration is ~22 s per request
_SFX_MAX_DURATION: float = 22.0


def generate_voiceover(
    text: str,
    output_path: Path,
    *,
    voice_id: str | None = None,
    model_id: str = _DEFAULT_TTS_MODEL,
) -> Path:
    """
    Generate a TTS voiceover from hook text using the ElevenLabs API.

    Parameters
    ----------
    text        : The hook / overlay text to narrate.
    output_path : Where to write the mp3 file.
    voice_id    : ElevenLabs voice UUID.  Defaults to Rachel.
    model_id    : ElevenLabs model.  eleven_turbo_v2_5 is fast + high quality.

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

    logger.info(
        "Generating voiceover | voice=%s | model=%s | chars=%d",
        vid, model_id, len(text),
    )
    audio_stream = client.text_to_speech.convert(
        voice_id=vid,
        text=text,
        model_id=model_id,
        voice_settings=_VoiceSettings(
            stability=_DEFAULT_VOICE_SETTINGS["stability"],
            similarity_boost=_DEFAULT_VOICE_SETTINGS["similarity_boost"],
            style=_DEFAULT_VOICE_SETTINGS["style"],
            use_speaker_boost=_DEFAULT_VOICE_SETTINGS["use_speaker_boost"],
        ),
        output_format="mp3_44100_128",
    )

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
    vs = _VoiceSettings(
        stability=_DEFAULT_VOICE_SETTINGS["stability"],
        similarity_boost=_DEFAULT_VOICE_SETTINGS["similarity_boost"],
        style=_DEFAULT_VOICE_SETTINGS["style"],
        use_speaker_boost=_DEFAULT_VOICE_SETTINGS["use_speaker_boost"],
    )

    logger.info(
        "Generating voiceover+timestamps | voice=%s | model=%s | chars=%d",
        vid, model_id, len(text),
    )

    word_timings: list[tuple[str, float, float]] = []
    audio_bytes: bytes = b""

    try:
        # ElevenLabs SDK v2.x returns AudioWithTimestampsResponse.
        # IMPORTANT: the Pydantic field is named `audio_base_64` (underscore before 64)
        # even though the JSON alias is `audio_base64`.  Accessing `.audio_base64`
        # raises AttributeError; the correct Python attribute is `.audio_base_64`.
        result = client.text_to_speech.convert_with_timestamps(
            voice_id=vid,
            text=text,
            model_id=model_id,
            voice_settings=vs,
            output_format="mp3_44100_128",
        )

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
        audio_stream = client.text_to_speech.convert(
            voice_id=vid,
            text=text,
            model_id=model_id,
            voice_settings=vs,
            output_format="mp3_44100_128",
        )
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


def generate_ambient_track(
    output_path: Path,
    *,
    duration_seconds: float = 20.0,
    prompt: str | None = None,
) -> Path | None:
    """
    Generate a dark ambient soundscape via the ElevenLabs Sound Effects API.

    Returns output_path on success, None if the API is unavailable or fails
    (so the reel can still compile as voice-only).
    """
    try:
        from elevenlabs import ElevenLabs  # type: ignore
    except ImportError:
        logger.warning("elevenlabs not installed — ambient track skipped.")
        return None

    api_key = app_config.ELEVENLABS_API_KEY
    if not api_key:
        logger.warning("ELEVENLABS_API_KEY not set — ambient track skipped.")
        return None

    client = ElevenLabs(api_key=api_key)
    sfx_prompt = prompt or _AMBIENT_PROMPT
    clamped_duration = min(duration_seconds, _SFX_MAX_DURATION)

    try:
        logger.info("Generating ambient track | duration=%.1fs", clamped_duration)
        result = client.text_to_sound_effects.convert(
            text=sfx_prompt,
            duration_seconds=clamped_duration,
            prompt_influence=0.3,
        )

        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "wb") as fh:
            for chunk in result:
                if chunk:
                    fh.write(chunk)

        logger.info("Ambient track saved → %s", output_path.name)
        return output_path

    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Ambient track generation failed (%s) — reel will be voice-only.", exc
        )
        return None

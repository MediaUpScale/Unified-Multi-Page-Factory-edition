# -*- coding: utf-8 -*-
"""
reel_sequence_engine — 4-image 80-second sequence reel compiler.

Architecture
------------
A sequence reel differs from the single-image ECONOMIC_REEL in that the
80-second runtime is divided into N visual acts (default 4), each backed by
a *different* generated image.  The narration and word-level timestamps from
ElevenLabs are used to determine natural act boundaries, then one video clip
is rendered per act and the clips are concatenated into the final MP4.

Flow
----
1. Receive N pre-generated image paths (one per act).
2. Split ``word_timings`` into N equal-duration segments (or use natural
   sentence boundaries from ``act_boundaries`` when supplied).
3. For each act i:
     a. Extract the word timings that fall within [t_start_i, t_end_i].
     b. Offset those timings to be relative to t_start_i (t=0 for that clip).
     c. Build an act sub-clip using the reused core render helpers from
        ``avatar_engine.video_engine`` (``_make_frame`` factory internals).
4. Concatenate all act clips with ``moviepy.concatenate_videoclips``.
5. Attach the *full* audio (voice + ambient) to the concatenated clip.
6. Export as H.264 / AAC MP4.

LLM script generation
---------------------
``build_sequence_script_prompt(topic, niche, persona_voice, n_acts, duration_s)``
returns a prompt string that instructs the LLM to write an N-act script whose
total spoken length matches ``duration_s``.  Each act is separated by a
``[ACT N]`` marker so the caller can split and assign one image prompt per act.
"""
from __future__ import annotations

import gc
import logging
import math
import tempfile
from pathlib import Path
from typing import Callable

import numpy as np
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Canvas constants (match avatar_engine/video_engine.py)
# ---------------------------------------------------------------------------
_REEL_WIDTH: int = 1080
_REEL_HEIGHT: int = 1920
_DEFAULT_FPS: int = 30
_AMBIENT_VOLUME: float = 0.35   # clearly audible drone pad behind the narrator

# Default sequence configuration
_DEFAULT_N_ACTS: int = 4
_DEFAULT_DURATION: float = 80.0
_ZOOM_PER_ACT_START: float = 1.0
_ZOOM_PER_ACT_END: float = 1.12


# ---------------------------------------------------------------------------
# Visual identity helpers — vignette + grain
# ---------------------------------------------------------------------------

def _make_vignette(
    width: int,
    height: int,
    strength: float,
) -> "np.ndarray":
    """
    Pre-compute a float32 vignette mask shaped (height, width).

    Values range from 0.0 (centre — no darkening) to ``strength`` (corners).
    Applied as: ``pixel *= (1.0 - vignette_mask)`` so high strength = dark corners.

    The falloff is a smooth squared radial curve, beginning at ~40 % radius and
    reaching full strength at the frame edge/corners.
    """
    y = np.linspace(-1.0, 1.0, height, dtype=np.float32)
    x = np.linspace(-1.0, 1.0, width, dtype=np.float32)
    xx, yy = np.meshgrid(x, y)
    r = np.sqrt(xx ** 2 + yy ** 2)
    # Smooth falloff: zero inside 40 % radius, ramps to `strength` at corners
    raw = np.clip((r - 0.4) / 0.9, 0.0, 1.0) ** 2
    return (raw * strength).astype(np.float32)


# ---------------------------------------------------------------------------
# LLM script prompt builder
# ---------------------------------------------------------------------------

def build_sequence_script_prompt(
    topic: str,
    niche: str,
    persona_voice: str = "investigative, neutral, immersive",
    n_acts: int = _DEFAULT_N_ACTS,
    duration_s: float = _DEFAULT_DURATION,
    *,
    total_words_target: "int | None" = None,
    previously_generated_hooks: "list[str] | None" = None,
) -> str:
    """
    Build an LLM system+user prompt that produces an N-act spoken script.

    Parameters
    ----------
    total_words_target:
        Override the computed word count.  When provided the prompt instructs
        the LLM to hit exactly this many words total (spread evenly across acts).
        Use 130-140 for slow 80-second documentary narration (~100 WPM pace).
        Defaults to computing from ``duration_s`` at 130 WPM.
    """
    if total_words_target is not None:
        words_per_act = total_words_target // n_acts
        total_words = words_per_act * n_acts
    else:
        words_per_act = int((duration_s / n_acts) * (130 / 60))  # 130 wpm pace
        total_words = words_per_act * n_acts

    anti_repeat_block = ""
    if previously_generated_hooks:
        recent = previously_generated_hooks[-10:]
        lines = "\n".join(f"  - {h}" for h in recent)
        anti_repeat_block = (
            f"\n\nPREVIOUSLY USED OPENING LINES (DO NOT REPEAT OR PARAPHRASE):\n{lines}\n"
        )

    pacing_note = (
        f"Write for a SLOW, deliberate documentary delivery (~100 words per minute). "
        f"Each act must be approximately {words_per_act} words "
        f"(total EXACTLY ~{total_words} words across all {n_acts} acts)."
        if total_words_target is not None else
        f"Each act must be approximately {words_per_act} words "
        f"(total ~{total_words} words across all acts)."
    )

    return f"""You are writing a compelling {duration_s:.0f}-second documentary-style voiceover script.

TOPIC: {topic}
CHANNEL NICHE: {niche}
NARRATOR VOICE: {persona_voice}

STRICT RULES:
1. Divide the script into exactly {n_acts} acts using markers: [ACT 1], [ACT 2], ... [ACT {n_acts}].
2. {pacing_note}
3. NEVER claim any conspiracy or theory is factual. Use language like: "some researchers believe", "ancient records suggest", "according to legend", "one theory proposes".
4. Each act must feel visually distinct — the narrator should describe a different aspect, location, or era.
5. Begin ACT 1 with a provocative, highly engaging hook sentence that immediately captures curiosity.
6. Maintain an investigative, immersive, documentary tone throughout.
7. NO headers, NO bullet points, NO markdown — pure spoken prose only.
8. Output ONLY the script with [ACT N] markers. No preamble, no labels, no meta commentary.
{anti_repeat_block}
Write the complete {n_acts}-act script now:"""


# ---------------------------------------------------------------------------
# Act boundary splitter
# ---------------------------------------------------------------------------

def _split_word_timings_into_acts(
    word_timings: "list[tuple[str, float, float]]",
    n_acts: int,
    total_duration: float,
) -> "list[tuple[float, float, list[tuple[str, float, float]]]]":
    """
    Divide ``word_timings`` into ``n_acts`` segments of equal target duration.

    Returns a list of (act_start_t, act_end_t, relative_word_timings) tuples.
    ``relative_word_timings`` have their timestamps offset so act_start = 0.

    When ``word_timings`` is empty, returns n_acts equal empty-timing segments.
    """
    act_dur = total_duration / n_acts
    segments: list[tuple[float, float, list]] = []

    for i in range(n_acts):
        t_start = i * act_dur
        t_end = (i + 1) * act_dur if i < n_acts - 1 else total_duration

        # collect words in this window
        act_words = [
            (w, max(0.0, ws - t_start), max(0.0, we - t_start))
            for w, ws, we in word_timings
            if ws >= t_start and we <= t_end + 0.1
        ]
        segments.append((t_start, t_end, act_words))

    return segments


# ---------------------------------------------------------------------------
# Phrase-chunking helper — groups word_timings into short natural phrases
# ---------------------------------------------------------------------------

def _chunk_words_into_phrases(
    word_timings: "list[tuple[str, float, float]]",
    words_per_phrase: int = 4,
) -> "list[tuple[str, float, float]]":
    """
    Collapse ``word_timings`` (one entry per word) into phrase groups.

    Each phrase spans ``words_per_phrase`` consecutive words.  The phrase's
    start/end timestamps are taken from the first and last word in the group.
    This produces captions that show 3-5 words at once instead of a single
    flashing word, matching natural spoken cadence.

    Example (words_per_phrase=4):
        [("Ancient", 0.0, 0.5), ("ruins", 0.5, 0.9), ("were", 0.9, 1.2), ("found", 1.2, 1.7)]
        → [("Ancient ruins were found", 0.0, 1.7)]
    """
    if not word_timings or words_per_phrase <= 1:
        return word_timings
    phrases: list[tuple[str, float, float]] = []
    for i in range(0, len(word_timings), words_per_phrase):
        chunk = word_timings[i : i + words_per_phrase]
        phrase_text = " ".join(w for w, _, _ in chunk)
        phrase_start = chunk[0][1]
        phrase_end = chunk[-1][2]
        phrases.append((phrase_text, phrase_start, phrase_end))
    return phrases


# ---------------------------------------------------------------------------
# Per-act clip builder
# ---------------------------------------------------------------------------

def _build_act_clip(
    image_path: Path,
    act_duration: float,
    word_timings: "list[tuple[str, float, float]]",
    *,
    hook_text: str = "",
    enable_hook_text: bool = True,
    overlay_opacity: float = 0.35,
    font_path: str | None = None,
    subtitle_fontsize: int = 46,
    subtitle_y_position: "int | None" = None,
    hook_y_frac: float = 0.55,
    logo_static_array: "np.ndarray | None" = None,
    vignette_mask: "np.ndarray | None" = None,
    grain_intensity: float = 18.0,
    fps: int = _DEFAULT_FPS,
    zoom_start: float = _ZOOM_PER_ACT_START,
    zoom_end: float = _ZOOM_PER_ACT_END,
    act_index: int = 0,
    words_per_phrase: int = 4,
):
    """
    Build one MoviePy VideoClip for a single act.

    Parameters
    ----------
    enable_hook_text:
        When False the hook headline is never burned into the frame regardless
        of whether hook_text is non-empty.  Lower-third subtitles and the logo
        are unaffected.  Defaults to True for backward compatibility.
    vignette_mask:
        Pre-computed float32 array (H, W) from ``_make_vignette()``.  When
        supplied it is applied after the dark overlay to darken the corners,
        giving the footage a documentary/archival cinematic signature.
    grain_intensity:
        Amplitude of additive film grain noise in pixel value units (±).
        Set to 0 to disable.  Default 18.0.
    """
    from moviepy import VideoClip  # type: ignore[import]

    # Load and fit image to canvas
    img = Image.open(image_path).convert("RGBA")
    canvas_ratio = _REEL_WIDTH / _REEL_HEIGHT
    img_ratio = img.width / img.height
    if img_ratio > canvas_ratio:
        new_h = _REEL_HEIGHT
        new_w = int(new_h * img_ratio)
    else:
        new_w = _REEL_WIDTH
        new_h = int(new_w / img_ratio)
    img = img.resize((new_w, new_h), Image.LANCZOS)

    # Precompute grain noise once per clip (seeded per-act for determinism)
    _grain: "np.ndarray | None" = None
    if grain_intensity > 0:
        rng = np.random.default_rng(seed=act_index + 42)
        _grain = (
            rng.random((_REEL_HEIGHT, _REEL_WIDTH)) * (grain_intensity * 2) - grain_intensity
        ).astype(np.float32)

    # Resolve font
    _font_path: str | None = font_path
    _font_subtitle: "ImageFont.FreeTypeFont | ImageFont.ImageFont"
    _font_hook: "ImageFont.FreeTypeFont | ImageFont.ImageFont"
    try:
        if _font_path:
            _font_subtitle = ImageFont.truetype(_font_path, subtitle_fontsize)
            _font_hook = ImageFont.truetype(_font_path, max(28, int(subtitle_fontsize * 1.35)))
        else:
            _font_subtitle = ImageFont.load_default()
            _font_hook = ImageFont.load_default()
    except Exception:
        _font_subtitle = ImageFont.load_default()
        _font_hook = ImageFont.load_default()

    _subtitle_y = subtitle_y_position if subtitle_y_position is not None else int(_REEL_HEIGHT * 0.82)

    # Group individual word timestamps into short natural phrases (3-5 words).
    # This replaces the choppy single-word subtitle flicker with fluid readable lines.
    _display_timings = _chunk_words_into_phrases(word_timings, words_per_phrase)

    def _current_phrase(t: float) -> str:
        for phrase, ws, we in _display_timings:
            if ws <= t <= we:
                return phrase
        return ""

    def _make_frame(t: float) -> np.ndarray:
        # Ken Burns zoom — linear per-act scale
        scale = zoom_start + (zoom_end - zoom_start) * (t / max(act_duration, 0.01))
        scaled_w = int(img.width * scale)
        scaled_h = int(img.height * scale)
        scaled = img.resize((scaled_w, scaled_h), Image.LANCZOS)
        cx = (scaled_w - _REEL_WIDTH) // 2
        cy = (scaled_h - _REEL_HEIGHT) // 2
        cropped = scaled.crop((cx, cy, cx + _REEL_WIDTH, cy + _REEL_HEIGHT))

        arr = np.array(cropped.convert("RGB"), dtype=np.float32)

        # Dark overlay
        arr *= (1.0 - overlay_opacity)

        # Vignette — additional corner darkening for archival/documentary look
        if vignette_mask is not None:
            arr *= (1.0 - vignette_mask[:, :, np.newaxis])

        arr = np.clip(arr, 0, 255).astype(np.uint8)
        frame = Image.fromarray(arr, mode="RGB").convert("RGBA")
        draw = ImageDraw.Draw(frame)

        # Hook headline — only when explicitly enabled AND act is the first
        if enable_hook_text and hook_text and act_index == 0:
            hook_y = int(_REEL_HEIGHT * hook_y_frac)
            _draw_centered_text(draw, hook_text, _font_hook, hook_y, _REEL_WIDTH)

        # Lower-third phrase subtitle — 3-5 words per block for fluid readability.
        # _draw_wrapped_text handles long strings (e.g. the 8-word CTA outro)
        # without overflowing the 1080 px canvas.
        phrase = _current_phrase(t)
        if phrase:
            _draw_wrapped_text(
                draw, phrase, _font_subtitle, _subtitle_y, _REEL_WIDTH,
                fill=(255, 230, 0),
            )

        # Logo layer
        if logo_static_array is not None:
            lh, lw = logo_static_array.shape[:2]
            lx = (_REEL_WIDTH - lw) // 2
            ly = _REEL_HEIGHT - lh - 100
            frame_arr = np.array(frame)
            _alpha_composite_numpy(frame_arr, logo_static_array, lx, ly)
            frame = Image.fromarray(frame_arr)

        # Film grain — applied last so it sits over all layers
        rgb_arr = np.array(frame.convert("RGB"), dtype=np.float32)
        if _grain is not None:
            rgb_arr += _grain[:, :, np.newaxis]
        rgb_arr = np.clip(rgb_arr, 0, 255).astype(np.uint8)

        return rgb_arr

    clip = VideoClip(frame_function=_make_frame, duration=act_duration)
    clip = clip.with_fps(fps)
    return clip


def _draw_centered_text(
    draw: "ImageDraw.Draw",
    text: str,
    font,
    y_center: int,
    canvas_width: int,
    fill: tuple = (255, 255, 255),
) -> None:
    """Draw horizontally centered text at y_center."""
    try:
        bbox = draw.textbbox((0, 0), text, font=font)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
    except AttributeError:
        tw, th = draw.textsize(text, font=font)  # type: ignore[attr-defined]
    x = (canvas_width - tw) // 2
    y = y_center - th // 2
    draw.text((x, y), text, font=font, fill=fill)


def _draw_wrapped_text(
    draw: "ImageDraw.Draw",
    text: str,
    font,
    y_center: int,
    canvas_width: int,
    fill: tuple = (255, 255, 255),
    max_width_frac: float = 0.85,
    line_spacing: int = 12,
) -> None:
    """
    Draw text centered on the canvas, word-wrapping to stay within
    ``max_width_frac`` of the canvas width.

    Lines are stacked vertically and the whole block is vertically centred
    around ``y_center``.  Falls back to a single centered line when the text
    already fits within the safe zone (avoids unnecessary wrapping for short
    3-5 word subtitle phrases).
    """
    max_w = int(canvas_width * max_width_frac)

    # ── 1. Build wrapped lines ────────────────────────────────────────────────
    words = text.split()
    lines: list[str] = []
    current: list[str] = []
    for word in words:
        candidate = " ".join(current + [word])
        try:
            bbox = draw.textbbox((0, 0), candidate, font=font)
            tw = bbox[2] - bbox[0]
        except AttributeError:
            tw, _ = draw.textsize(candidate, font=font)  # type: ignore[attr-defined]
        if tw <= max_w or not current:
            current.append(word)
        else:
            lines.append(" ".join(current))
            current = [word]
    if current:
        lines.append(" ".join(current))

    if not lines:
        return

    # ── 2. Measure each line height ───────────────────────────────────────────
    line_heights: list[int] = []
    for line in lines:
        try:
            bbox = draw.textbbox((0, 0), line, font=font)
            th = bbox[3] - bbox[1]
        except AttributeError:
            _, th = draw.textsize(line, font=font)  # type: ignore[attr-defined]
        line_heights.append(max(1, th))

    total_h = sum(line_heights) + line_spacing * max(0, len(lines) - 1)

    # ── 3. Draw each line, vertically centred as a block ─────────────────────
    y = y_center - total_h // 2
    for line, lh in zip(lines, line_heights):
        _draw_centered_text(draw, line, font, y + lh // 2, canvas_width, fill=fill)
        y += lh + line_spacing


def _alpha_composite_numpy(
    base: np.ndarray,
    overlay: np.ndarray,
    x: int,
    y: int,
) -> None:
    """Alpha-composite ``overlay`` (RGBA uint8) onto ``base`` (RGB/RGBA uint8) in-place."""
    oh, ow = overlay.shape[:2]
    bh, bw = base.shape[:2]
    x1 = max(0, x)
    y1 = max(0, y)
    x2 = min(bw, x + ow)
    y2 = min(bh, y + oh)
    if x2 <= x1 or y2 <= y1:
        return
    ox1 = x1 - x
    oy1 = y1 - y
    ov = overlay[oy1: oy1 + (y2 - y1), ox1: ox1 + (x2 - x1)]
    alpha = ov[:, :, 3:4].astype(np.float32) / 255.0
    base[y1:y2, x1:x2, :3] = (
        ov[:, :, :3].astype(np.float32) * alpha
        + base[y1:y2, x1:x2, :3].astype(np.float32) * (1.0 - alpha)
    ).clip(0, 255).astype(np.uint8)


# ---------------------------------------------------------------------------
# Logo pre-renderer helper
# ---------------------------------------------------------------------------

def _prerender_logo(
    logo_image_path: "Path | None",
    logo_width_px: int,
    logo_opacity: float,
    logo_max_height_px: "int | None",
) -> "np.ndarray | None":
    """Load, scale, and alpha-premultiply the logo PNG; returns RGBA ndarray or None."""
    if logo_image_path is None or not logo_image_path.is_file():
        return None
    try:
        logo = Image.open(logo_image_path).convert("RGBA")
        scale = logo_width_px / logo.width
        new_h = int(logo.height * scale)
        if logo_max_height_px and new_h > logo_max_height_px:
            scale = logo_max_height_px / logo.height
            new_w = int(logo.width * scale)
            new_h = logo_max_height_px
        else:
            new_w = logo_width_px
        logo = logo.resize((new_w, new_h), Image.LANCZOS)
        arr = np.array(logo).astype(np.float32)
        arr[:, :, 3] = arr[:, :, 3] * logo_opacity
        return np.clip(arr, 0, 255).astype(np.uint8)
    except Exception as exc:
        logger.warning("Logo prerender failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Main public entry point
# ---------------------------------------------------------------------------

def compile_sequence_reel(
    image_paths: "list[Path]",
    hook_text: str,
    *,
    voice_audio: "Path | None" = None,
    ambient_audio: "Path | None" = None,
    output_path: "Path | None" = None,
    target_duration: float = _DEFAULT_DURATION,
    act_duration_s: "float | None" = None,
    word_timings: "list[tuple[str, float, float]] | None" = None,
    font_path: "str | None" = None,
    overlay_opacity: float = 0.35,
    enable_hook_text: bool = True,
    vignette_strength: float = 0.0,
    grain_intensity: float = 18.0,
    logo_image_path: "Path | None" = None,
    logo_width_px: int = 200,
    logo_y_offset_px: int = 100,
    logo_opacity: float = 0.85,
    logo_max_height_px: "int | None" = None,
    subtitle_fontsize: int = 56,
    subtitle_y_position: "int | None" = None,
    hook_y_frac: float = 0.50,
    page_id: str = "",
    fps: int = _DEFAULT_FPS,
    words_per_phrase: int = 4,
    # ── CTA extension ──────────────────────────────────────────────────────
    cta_text: str = "",
    cta_audio: "Path | None" = None,
    cta_duration_s: float = 5.0,
) -> Path:
    """
    Compile an N-image sequence reel from a list of background images.

    Each image covers an equal portion of the total duration
    (e.g. 4 images × 20s = 80s reel).

    Parameters
    ----------
    image_paths:
        Ordered list of image paths — one per act.  Must be non-empty.
    hook_text:
        Static headline.  Burned into Act 1 only when ``enable_hook_text=True``.
    voice_audio:
        Path to the full-length voiceover MP3/WAV.
    ambient_audio:
        Path to the ambient soundscape MP3/WAV (optional).
    output_path:
        Destination MP4 path.  Auto-generated in a temp dir if None.
    target_duration:
        Target total reel duration in seconds (used when no audio file exists).
    act_duration_s:
        Explicit per-act clip length in seconds.  When provided and no audio
        file is available, total_duration = n_acts × act_duration_s.  Ignored
        when actual audio length drives the timeline.
    word_timings:
        List of ``(word, start_s, end_s)`` from ElevenLabs timestamps.
        Used to synchronise word-level subtitle burns.
    enable_hook_text:
        When False the hook headline is not burned at the top of any frame.
        Lower-third subtitles and the logo are unaffected.
    vignette_strength:
        Corner darkening intensity (0 = off, 1 = full black corners).
        Pre-computed once and reused across all act clips.
    grain_intensity:
        Film grain amplitude in pixel value units (±).  0 = off.
    """
    from moviepy import AudioFileClip, concatenate_videoclips  # type: ignore[import]

    if not image_paths:
        raise ValueError("compile_sequence_reel: image_paths must not be empty.")

    n_acts = len(image_paths)

    # ── Canonical timeline — driven by voice audio duration ───────────────────
    # The video container ends exactly when the narrator finishes speaking.
    # Each act is voice_duration / n_acts so all acts share the audio proportionally.
    # If no voice audio is present, fall back to act_duration_s × n_acts (page config),
    # then to target_duration.
    _voice_actual_dur: float = 0.0
    if voice_audio and voice_audio.is_file():
        try:
            _tmp_ac = AudioFileClip(str(voice_audio))
            _voice_actual_dur = _tmp_ac.duration
            _tmp_ac.close()
            total_duration = _voice_actual_dur
        except Exception as _ae:
            logger.warning("Could not read voice audio duration: %s — using act_duration fallback", _ae)
            total_duration = (
                float(act_duration_s) * n_acts if act_duration_s and act_duration_s > 0
                else float(target_duration)
            )
    elif act_duration_s is not None and act_duration_s > 0:
        total_duration = float(act_duration_s) * n_acts
    else:
        total_duration = float(target_duration)

    act_duration_locked = total_duration / n_acts   # equal slice per act

    logger.info(
        "compile_sequence_reel | page=%s n_acts=%d total=%.1fs act=%.1fs "
        "voice_dur=%.1fs enable_hook=%s vignette=%.2f grain=%.1f",
        page_id, n_acts, total_duration, act_duration_locked,
        _voice_actual_dur, enable_hook_text, vignette_strength, grain_intensity,
    )

    # Split word timings into acts
    wt = word_timings or []
    act_segments = _split_word_timings_into_acts(wt, n_acts, total_duration)

    # Pre-render logo once (shared across all acts)
    logo_arr = _prerender_logo(logo_image_path, logo_width_px, logo_opacity, logo_max_height_px)

    # Pre-compute vignette mask once (shared across all acts)
    vignette_arr: "np.ndarray | None" = None
    if vignette_strength > 0:
        vignette_arr = _make_vignette(_REEL_WIDTH, _REEL_HEIGHT, vignette_strength)
        logger.debug("Vignette pre-computed | strength=%.2f", vignette_strength)

    # Build per-act clips — each exactly act_duration_locked seconds
    clips = []
    for i, (img_path, (t_start, t_end, act_wt)) in enumerate(
        zip(image_paths, act_segments)
    ):
        logger.info("Rendering act %d/%d | image=%s", i + 1, n_acts, img_path.name)
        clip = _build_act_clip(
            img_path,
            act_duration=act_duration_locked,   # ← always exactly 20.0 s
            word_timings=act_wt,
            hook_text=hook_text,
            enable_hook_text=enable_hook_text,
            overlay_opacity=overlay_opacity,
            font_path=font_path,
            subtitle_fontsize=subtitle_fontsize,
            subtitle_y_position=subtitle_y_position,
            hook_y_frac=hook_y_frac,
            logo_static_array=logo_arr,
            vignette_mask=vignette_arr,
            grain_intensity=grain_intensity,
            fps=fps,
            act_index=i,
            words_per_phrase=words_per_phrase,
        )
        clips.append(clip)

    # ── CTA extension clip (appended after all narrative acts) ───────────────
    # A 5-second closing scene on the last image with the follow CTA subtitle.
    # The clip is always static (no Ken Burns zoom) so the CTA text is legible.
    _narration_duration = total_duration   # save before extending for audio offsets
    _has_cta = bool(cta_text) or (cta_audio is not None and cta_audio.is_file())
    if _has_cta:
        logger.info("CTA | appending %.1fs extension: %s", cta_duration_s, cta_text[:60])
        cta_wt: list = [(cta_text, 0.0, cta_duration_s)] if cta_text else []
        cta_clip = _build_act_clip(
            image_paths[-1],              # last scene image — visual continuity
            act_duration=cta_duration_s,
            word_timings=cta_wt,
            hook_text="",
            enable_hook_text=False,
            overlay_opacity=overlay_opacity,
            font_path=font_path,
            subtitle_fontsize=subtitle_fontsize,
            subtitle_y_position=subtitle_y_position,
            hook_y_frac=hook_y_frac,
            logo_static_array=logo_arr,
            vignette_mask=vignette_arr,
            grain_intensity=grain_intensity,
            fps=fps,
            zoom_start=1.0, zoom_end=1.0,  # static frame — no zoom during CTA
            act_index=n_acts,              # unique seed for grain
            words_per_phrase=20,           # show full sentence as one block
        )
        clips.append(cta_clip)
        total_duration += cta_duration_s  # extend container to include CTA

    # Concatenate all acts (+ CTA if present)
    logger.info("Concatenating %d clip(s) → %.1fs total …", len(clips), total_duration)
    final_video = concatenate_videoclips(clips, method="compose")

    # Resolve output path
    if output_path is None:
        out_dir = Path(tempfile.mkdtemp())
        slug = page_id or "sequence"
        output_path = out_dir / f"{slug}_sequence_reel.mp4"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # ── Full audio mix ────────────────────────────────────────────────────────
    # Layer order:
    #   1. Narrator voiceover  — at t=0, full length as-is
    #   2. CTA audio            — starts at t=_narration_duration (no overlap)
    #   3. Ambient drone loop   — runs from t=0 to t=total_duration at 0.12 vol
    # A 1-second audio fade-out is applied to the composite mix so the video
    # never ends with an abrupt audio cut.
    audio_clips = []
    if voice_audio and voice_audio.is_file():
        try:
            vc = AudioFileClip(str(voice_audio))
            audio_clips.append(vc)
        except Exception as _ae:
            logger.warning("Voice audio load failed: %s", _ae)

    if _has_cta and cta_audio is not None and cta_audio.is_file():
        try:
            cta_ac = AudioFileClip(str(cta_audio))
            cta_ac = cta_ac.with_start(_narration_duration)   # offset to play after narrator
            audio_clips.append(cta_ac)
            logger.info("CTA audio | offset=%.1fs dur=%.1fs", _narration_duration, cta_ac.duration)
        except Exception as _ae:
            logger.warning("CTA audio load failed: %s", _ae)

    if ambient_audio and not ambient_audio.is_file():
        logger.warning(
            "Ambient track path supplied but file MISSING: %s — reel will be voice-only",
            ambient_audio,
        )
    if ambient_audio and ambient_audio.is_file():
        try:
            import math as _math
            logger.info(
                "Ambient track FOUND (%.1f KB) → %s | will be mixed at volume=%.2f",
                ambient_audio.stat().st_size / 1024,
                ambient_audio.name,
                _AMBIENT_VOLUME,
            )
            ac = AudioFileClip(str(ambient_audio))
            _amb_actual = ac.duration
            ac.close()

            if _amb_actual < total_duration:
                n_loops = _math.ceil(total_duration / _amb_actual)
                logger.info(
                    "Ambient (%.1fs) < reel (%.1fs) — looping ×%d",
                    _amb_actual, total_duration, n_loops,
                )
                from moviepy import concatenate_audioclips  # type: ignore[import]
                looped_parts = [AudioFileClip(str(ambient_audio)) for _ in range(n_loops)]
                ac_looped = concatenate_audioclips(looped_parts).subclipped(0, total_duration)
                audio_clips.append(ac_looped.with_volume_scaled(_AMBIENT_VOLUME))
            else:
                ac_trimmed = AudioFileClip(str(ambient_audio)).subclipped(0, total_duration)
                audio_clips.append(ac_trimmed.with_volume_scaled(_AMBIENT_VOLUME))
        except Exception as _ae:
            logger.warning("Ambient audio load failed: %s", _ae)

    if audio_clips:
        from moviepy import CompositeAudioClip  # type: ignore[import]
        if len(audio_clips) > 1:
            mixed = CompositeAudioClip(audio_clips)
            # Explicitly set duration on the composite so MoviePy never clips it
            # to the shortest constituent track.  Without this, the ambient loop
            # (which runs longer than the voice clip) may be silenced after the
            # voice ends.
            try:
                mixed = mixed.with_duration(total_duration)
            except Exception:
                pass  # fallback: let MoviePy infer duration
        else:
            mixed = audio_clips[0]
        # 1-second fade-out at the very end — prevents abrupt audio cut
        try:
            mixed = mixed.audio_fadeout(1.0)
        except AttributeError:
            try:
                from moviepy.audio.fx import AudioFadeOut  # type: ignore[import]
                mixed = mixed.with_effects([AudioFadeOut(1.0)])
            except Exception:
                pass  # fade-out unavailable in this MoviePy version — skip silently
        final_video = final_video.with_audio(mixed)

    # Write MP4
    logger.info("Writing sequence reel → %s", output_path)
    try:
        final_video.write_videofile(
            str(output_path),
            fps=fps,
            codec="libx264",
            audio_codec="aac",
            audio_fps=44100,
            preset="medium",
            threads=4,
            logger=None,
        )
    finally:
        for c in clips:
            try:
                c.close()
            except Exception:
                pass
        try:
            final_video.close()
        except Exception:
            pass
        for ac in audio_clips:
            try:
                ac.close()
            except Exception:
                pass
        gc.collect()

    logger.info("Sequence reel complete: %s", output_path)
    return output_path

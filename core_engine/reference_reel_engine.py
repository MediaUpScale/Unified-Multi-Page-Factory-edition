# -*- coding: utf-8 -*-
"""
core_engine/reference_reel_engine.py
=====================================
Reference-Based Reel Engine — REFERENCE_BASED_REELS post type.

Extracts unique, non-overlapping segments from raw reference MP4 footage,
overlays an LLM-generated viral hook text, blends lullaby-style ambient music,
renders a 9:16 (1080x1920) vertical MP4, uploads to Backblaze B2, and
writes entries to the PostPlanner xlsx and content_library.json.

Designed for the ``momma_circle`` page (PARENTAL_CONTENTS niche) but the
architecture is fully generic — any page can configure a
``REFERENCE_VIDEO_DIR`` in its page_config.py to use this engine.

Public API
----------
    from core_engine.reference_reel_engine import ReferenceReelEngine
    engine = ReferenceReelEngine(page_ctx, outputs_dir)
    results = engine.run(quantity=3, topic="My topic", clip_duration=30)
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import random
import re
import textwrap
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_LOG = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Optional heavy imports (graceful degradation when not installed)
# ---------------------------------------------------------------------------
try:
    from moviepy import (  # type: ignore[import]
        VideoFileClip,
        AudioFileClip,
        CompositeAudioClip,
        TextClip,
        CompositeVideoClip,
        ColorClip,
    )
    from moviepy.audio.fx import AudioFadeIn, AudioFadeOut  # type: ignore[import]
    _MOVIEPY_OK = True
except ImportError:
    try:
        from moviepy.editor import (  # type: ignore[import]
            VideoFileClip,
            AudioFileClip,
            CompositeAudioClip,
            TextClip,
            CompositeVideoClip,
            ColorClip,
        )
        from moviepy.audio.fx.all import (  # type: ignore[import]
            audio_fadein as AudioFadeIn,
            audio_fadeout as AudioFadeOut,
        )
        _MOVIEPY_OK = True
    except ImportError:
        _MOVIEPY_OK = False
        _LOG.warning("moviepy not installed — video rendering will be skipped.")

try:
    import numpy as np  # type: ignore[import]
    _NP_OK = True
except ImportError:
    _NP_OK = False

try:
    from PIL import Image, ImageDraw, ImageFont  # type: ignore[import]
    _PIL_OK = True
except ImportError:
    _PIL_OK = False

# ---------------------------------------------------------------------------
# Engine root (resolves relative paths in page_config)
# ---------------------------------------------------------------------------
_ENGINE_ROOT = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# Ambient audio prompt pool — uplifting, emotional, cinematic parenting energy.
# Randomly cycled per variant unless page_config overrides AMBIENT_SFX_PROMPT.
# Prompts are intentionally varied so repeated renders feel sonically distinct.
# ---------------------------------------------------------------------------
_AMBIENT_PROMPT_POOL: list[str] = [
    # User-specified core set
    "Uplifting acoustic guitar, emotional and heartwarming, bright inspirational melody, cinematic family warmth",
    "Upbeat acoustic ukulele and soft piano, joyful, inspiring, emotional parenting background music",
    "Heartwarming acoustic folk, cheerful, sweet emotional build-up, bright and hopeful",
    "Inspiring acoustic melody, warm, upbeat, touching and joyful family moment",
    # Extended variety
    "Bright acoustic fingerpicking guitar, cinematic heartfelt emotion, warm family energy, 78 BPM",
    "Cheerful acoustic guitar and light pizzicato strings, uplifting, positive, tender family vibe",
    "Acoustic piano with gentle percussion, inspiring and joyful, upbeat heartwarming parenting",
    "Playful ukulele and glockenspiel, bright cute baby moment, joyful and warm, positive energy",
]


# ===========================================================================
# Segment State Tracker
# ===========================================================================

class _SegmentTracker:
    """
    Persistent JSON store of every clip segment already extracted from
    each source video.

    Schema::

        {
          "filename.mp4": [
            {"start_sec": 0.0, "end_sec": 28.3},
            ...
          ]
        }
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._data: dict[str, list[dict[str, float]]] = self._load()

    # ------------------------------------------------------------------
    def _load(self) -> dict[str, list[dict[str, float]]]:
        if not self.path.is_file():
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                return raw
        except Exception as exc:
            _LOG.warning("Could not parse segment tracker at %s: %s", self.path, exc)
        return {}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self._data, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    # ------------------------------------------------------------------
    def used_ranges(self, filename: str) -> list[tuple[float, float]]:
        """Return list of (start_sec, end_sec) already used for *filename*."""
        return [
            (seg["start_sec"], seg["end_sec"])
            for seg in self._data.get(filename, [])
        ]

    def record(self, filename: str, start_sec: float, end_sec: float) -> None:
        """Persist a newly used segment."""
        self._data.setdefault(filename, []).append(
            {"start_sec": round(start_sec, 3), "end_sec": round(end_sec, 3)}
        )
        self._save()

    def total_used_seconds(self, filename: str) -> float:
        return sum(e - s for s, e in self.used_ranges(filename))


# ===========================================================================
# LLM helpers
# ===========================================================================

def _call_gemini(prompt: str, api_key: str, model: str = "models/gemini-2.5-flash") -> str:
    """Simple single-turn Gemini text call; returns stripped response text."""
    try:
        from avatar_engine.providers.gemini_utils import generate_text_with_client_chain
        resp = generate_text_with_client_chain(
            api_key=api_key,
            preferred_model=model,
            contents=prompt,
        )
        if resp is None:
            return ""
        if hasattr(resp, "text"):
            return resp.text.strip()
        return str(resp).strip()
    except Exception as exc:
        _LOG.warning("Gemini call failed: %s", exc)
        return ""


def _generate_hook_text(topic: str, niche_disclaimer: str, api_key: str) -> str:
    """
    Generate a viral on-screen hook for a 9:16 parenting reel.

    Dual-style engine — the LLM reads the topic and selects whichever style
    creates the higher emotional impact:

    STYLE A  Relatable / Punchy Humor
        Sharp, highly relatable toddler observations or punchlines.
        Four rotating humor angles (Absurd Comparisons, Illogical Logic,
        Dramatic Mood Swings, Unspoken Parenting Realities).
        Classic meme comparisons are allowed but must NOT be repeated verbatim.

    STYLE B  Deep Emotional Punch
        Profound, bittersweet reality-checks about how fast the baby/toddler
        phase disappears.  Core theme: "One day you'll put them down and never
        pick them up again — soak it all in."
    """
    system = (
        "You are a viral short-form video copywriter who writes on-screen text hooks "
        "for a warm, authentic parenting channel aimed at real mothers.\n"
        f"{niche_disclaimer}\n\n"
        "TASK\n"
        "Write exactly ONE hook for the given TOPIC. The hook will be burned directly "
        "onto a 9:16 vertical video, so it must be:\n"
        "  - 1-2 sentences maximum (15-30 words)\n"
        "  - Punchy, raw, and instantly relatable\n"
        "  - Clean and readable on a mobile screen\n"
        "  - Written in the voice of a real, authentic parent, NOT a brand\n\n"
        "CHOOSE the style that creates the HIGHEST emotional impact for the TOPIC:\n\n"
        "===== STYLE A: RELATABLE / PUNCHY HUMOR =====\n"
        "Use when the topic is about toddler chaos, daily parenting struggles, or funny "
        "parenting observations. Rotate through these four humor angles — pick ONE per run:\n\n"
        "  Angle 1 — ABSURD COMPARISONS (toddler as tiny chaotic adult):\n"
        "    Core idea: compare toddler behavior to adult scenarios (tiny boss, chaotic "
        "roommate, aggressive negotiator, drunk coworker). BE INVENTIVE — do NOT copy "
        "the examples verbatim.\n"
        "    Inspiration: 'Living with a toddler is basically having a tiny CEO who has "
        "no idea how the company works but absolutely will not take suggestions.'\n\n"
        "  Angle 2 — ILLOGICAL TODDLER LOGIC:\n"
        "    Core idea: the absurd, irrational rules toddlers enforce (wrong cup color, "
        "broken cracker tragedy, sock seam catastrophe).\n"
        "    Inspiration: 'My toddler just sobbed for 8 minutes because the banana "
        "was too banana-shaped. This is my life now.'\n\n"
        "  Angle 3 — DRAMATIC MOOD SWINGS:\n"
        "    Core idea: toddlers go from pure joy to full meltdown in under 3 seconds "
        "with zero warning and zero reason.\n"
        "    Inspiration: 'Toddler mood forecast: sunny, loving, delightful — "
        "then suddenly a Category 5 meltdown because you said good morning wrong.'\n\n"
        "  Angle 4 — UNSPOKEN PARENTING REALITIES:\n"
        "    Core idea: the chaotic reality of simple daily tasks — getting out the "
        "door, putting shoes on, leaving the playground, bedtime negotiations.\n"
        "    Inspiration: 'Leaving the house with a toddler is a 45-minute "
        "military operation where the enemy changes the plan every 6 minutes.'\n\n"
        "===== STYLE B: DEEP EMOTIONAL PUNCH =====\n"
        "Use when the topic is about milestones, growth, exhaustion, or the fleeting "
        "nature of the baby/toddler phase. Land a profound, bittersweet emotional blow.\n\n"
        "  Core theme: 'One day you will pick them up, put them down, "
        "and never pick them up again. You do not get a rewind button.'\n\n"
        "  Desired emotional tones (use as INSPIRATION — do NOT copy verbatim):\n"
        "  - The quiet ache of realising infancy is almost gone before you noticed.\n"
        "  - The messy floor, the noise, the chaos — one day it will all be clean "
        "and silent and you will miss every single second of this.\n"
        "  - The sleepless nights feel endless until suddenly they are a memory.\n"
        "  - You are not just surviving the chaos. You are living the days "
        "you will tell stories about for the rest of your life.\n\n"
        "STRICT OUTPUT RULES\n"
        "  - Return ONLY the hook text. No quotes, no hashtags, no explanation.\n"
        "  - 1-2 sentences, 15-30 words.\n"
        "  - Every run must produce a UNIQUE, FRESHLY SYNTHESISED hook. "
        "Never output a literal copy of any example above.\n"
        "  - No generic social-media filler phrases like 'let me show you', "
        "'here is my new routine', or 'read the caption below'.\n"
        "  - Sound like a real parent talking directly to another real parent."
    )
    full_prompt = f"{system}\n\nTOPIC: {topic}\n\nHOOK:"
    result = _call_gemini(full_prompt, api_key)
    if result:
        result = result.strip('"\'')
        return result[:240]
    return "One day you'll realise this exhausting, beautiful chaos was the best chapter of your life."


def _generate_caption(
    topic: str,
    niche_disclaimer: str,
    api_key: str,
    max_words: int = 220,
    hashtag_count: int = 12,
) -> str:
    """Ask the LLM for a long-form warm parenting caption with hashtags."""
    system = (
        "You are a warm, expert parenting content creator for a caring Instagram/TikTok account.\n"
        f"{niche_disclaimer}\n\n"
        f"Write a long-form caption (<= {max_words} words) for a short-form video post. "
        "Structure:\n"
        "1. Opening hook (1-2 lines) - relatable, emotionally engaging.\n"
        "2. Main body (5-8 sentences) - practical value, warm advice, or a heartfelt observation "
        "about the topic. Weave in the theme that baby phases are fleeting and precious.\n"
        "3. Soft call-to-action (1 line) - invite saves, shares, or comments. "
        "Hint at a future guide or protocol naturally without a hard pitch.\n"
        f"4. {hashtag_count} relevant hashtags on a new line.\n\n"
        "Return ONLY the caption text (no JSON, no extra explanation)."
    )
    full_prompt = f"{system}\n\nTOPIC: {topic}\n\nCAPTION:"
    result = _call_gemini(full_prompt, api_key)
    if result:
        return result.strip()
    return (
        f"This season of motherhood - with all its beautiful chaos - won't last forever.\n\n"
        f"Every challenge you're navigating right now around {topic} "
        f"is preparing you for the next chapter.\n\n"
        "Save this for the days when you need a reminder that you're doing better than you think.\n\n"
        "#momlife #momtips #newmom #toddlermom #motherhood #babysleep #momadvice "
        "#parentingtips #gentleparenting #momcommunity #babynutrition #momhacks"
    )


# ===========================================================================
# Audio helpers
# ===========================================================================

def _ambient_cache_key(prompt: str, duration_s: float) -> str:
    """Stable filename-safe cache key for a given prompt + duration combo."""
    raw = f"{prompt}|{round(duration_s, 1)}"
    return hashlib.md5(raw.encode()).hexdigest()[:12]


def _generate_or_fetch_ambient(
    prompt: str,
    duration_s: float,
    cache_dir: Path,
    api_key: str,
) -> "Path | None":
    """
    Return path to a loopable ambient MP3 matching *prompt* + *duration_s*.

    Strategy
    --------
    1. Check ``cache_dir`` for a pre-generated file matching the same
       prompt+duration key (MD5).
    2. If missing, call the ElevenLabs SFX API for a 20-second loopable tile.
    3. The MoviePy mixing step loops / trims the tile to the exact clip length.
    """
    import requests as _requests  # type: ignore[import]

    cache_dir.mkdir(parents=True, exist_ok=True)
    key = _ambient_cache_key(prompt, duration_s)
    cached = cache_dir / f"ambient_{key}.mp3"

    if cached.is_file():
        _LOG.info("Ambient cache hit: %s", cached.name)
        return cached

    if not api_key:
        _LOG.warning("ELEVENLABS_API_KEY not set - ambient audio skipped.")
        return _local_fallback_audio()

    # Generate a 20-second loopable tile — fixed credit cost regardless of clip length.
    _SFX_DURATION = 20.0
    payload = {
        "text": prompt,
        "model_id": "eleven_text_to_sound_v2",
        "duration_seconds": _SFX_DURATION,
        "loop": True,
        "prompt_influence": 0.7,
    }
    headers = {"xi-api-key": api_key, "Content-Type": "application/json"}

    try:
        _LOG.info(
            "Generating ambient SFX tile (%.0f s loopable) for clip=%.1f s | prompt: %s",
            _SFX_DURATION, duration_s, prompt[:60],
        )
        resp = _requests.post(
            "https://api.elevenlabs.io/v1/sound-generation",
            json=payload,
            headers=headers,
            timeout=20,
        )
        resp.raise_for_status()
        cached.write_bytes(resp.content)
        _LOG.info("Ambient SFX saved: %s (%.1f KB)", cached.name, len(resp.content) / 1024)
        return cached
    except Exception as exc:
        _LOG.warning("ElevenLabs SFX failed (%s) - trying local fallback.", exc)

    return _local_fallback_audio()


def _local_fallback_audio() -> "Path | None":
    candidates = [
        _ENGINE_ROOT / "assets" / "audio" / "lullaby_loop.mp3",
        _ENGINE_ROOT / "assets" / "audio" / "ambient_mystery_loop.mp3",
    ]
    for c in candidates:
        if c.is_file():
            _LOG.info("Using local fallback audio: %s", c.name)
            return c
    return None


# ===========================================================================
# Text-overlay helpers
# ===========================================================================

def _wrap_hook_lines(text: str, max_chars_per_line: int = 28) -> list[str]:
    """
    Wrap hook text into lines of at most *max_chars_per_line* characters.
    Default 28 chars keeps text well clear of frame edges on a 1080-wide canvas
    (roughly 20% safe margin on each side).
    """
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        test = f"{current} {word}".strip() if current else word
        if len(test) <= max_chars_per_line:
            current = test
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _burn_hook_overlay_pil(
    frame_array: "np.ndarray",
    hook_lines: list[str],
    font_path: str,
    font_size: int,
    font_color: tuple,
    stroke_color: tuple,
    stroke_width: int,
    y_frac: float,
    safe_margin_frac: float = 0.08,
) -> "np.ndarray":
    """
    Burn hook text onto a single frame (numpy uint8 H x W x 3) using PIL.

    *safe_margin_frac* (Fix #4): minimum horizontal margin as a fraction of
    canvas width — keeps text away from the video edges.  Default 0.08 = 8%.
    Returns the modified frame array.
    """
    if not _PIL_OK or not _NP_OK:
        return frame_array

    h, w = frame_array.shape[:2]
    img = Image.fromarray(frame_array)
    draw = ImageDraw.Draw(img)

    # Resolve font
    font: Any = None
    if font_path:
        abs_fp = _ENGINE_ROOT / font_path
        if abs_fp.is_file():
            try:
                font = ImageFont.truetype(str(abs_fp), font_size)
            except Exception:
                pass
    if font is None:
        try:
            font = ImageFont.load_default(size=font_size)
        except TypeError:
            font = ImageFont.load_default()

    line_h = font_size + 12   # +12 px leading between lines
    total_h = len(hook_lines) * line_h
    y_start = int(h * y_frac) - total_h // 2
    # Clamp so the block never starts above safe margin
    min_y = int(h * safe_margin_frac)
    y_start = max(y_start, min_y)

    for i, line in enumerate(hook_lines):
        bbox = draw.textbbox((0, 0), line, font=font)
        text_w = bbox[2] - bbox[0]
        # Centre horizontally, respecting safe margins
        x = max(int(w * safe_margin_frac), (w - text_w) // 2)
        y = y_start + i * line_h

        # Stroke pass
        if stroke_width > 0:
            for dx in range(-stroke_width, stroke_width + 1):
                for dy in range(-stroke_width, stroke_width + 1):
                    if dx == 0 and dy == 0:
                        continue
                    draw.text((x + dx, y + dy), line, font=font, fill=stroke_color)
        draw.text((x, y), line, font=font, fill=font_color)

    return np.array(img)


# ===========================================================================
# Video renderer
# ===========================================================================

def _make_even(n: int) -> int:
    """Round *n* down to nearest even integer (required for H.264 encoding)."""
    return n if n % 2 == 0 else n - 1


def _render_reference_reel(
    *,
    source_video: Path,
    start_sec: float,
    end_sec: float,
    output_path: Path,
    hook_lines: list[str],
    ambient_audio_path: "Path | None",
    page_cfg: dict[str, Any],
    logo_path: "Path | None" = None,
) -> Path:
    """
    Extract a clip from *source_video* [start_sec, end_sec], resize to 9:16,
    mute original audio, burn hook text, blend ambient audio, and export.

    Key behaviours
    --------------
    * Fix #1  — original video audio is ALWAYS muted; only the ElevenLabs
                ambient track plays in the final export.
    * Fix #2  — clip plays at natural 1.0x speed; no speed change applied.
    * Fix #6  — smart centre-crop that preserves aspect ratio correctly for
                both landscape and portrait source material.

    Returns *output_path* on success.
    """
    if not _MOVIEPY_OK:
        raise RuntimeError(
            "moviepy is required for REFERENCE_BASED_REELS. "
            "Install it with: pip install moviepy"
        )

    out_w: int = page_cfg.get("OUTPUT_WIDTH",  1080)
    out_h: int = page_cfg.get("OUTPUT_HEIGHT", 1920)
    fps: int   = page_cfg.get("OUTPUT_FPS",    30)

    font_path    = page_cfg.get("HOOK_FONT_PATH", "Fonts/Montserrat/static/Montserrat-Bold.ttf")
    font_size    = int(page_cfg.get("HOOK_FONT_SIZE",    55))   # Fix #4: reduced from 68 → 55
    font_color   = tuple(page_cfg.get("HOOK_FONT_COLOR",   (255, 255, 255)))
    stroke_color = tuple(page_cfg.get("HOOK_STROKE_COLOR", (0, 0, 0)))
    stroke_width = int(page_cfg.get("HOOK_STROKE_WIDTH",   3))
    hook_y_frac  = float(page_cfg.get("HOOK_Y_FRAC",       0.28))
    ambient_vol  = float(page_cfg.get("AMBIENT_VOLUME",    0.70))
    fade_in_s    = float(page_cfg.get("AMBIENT_FADE_IN_S",  0.5))
    fade_out_s   = float(page_cfg.get("AMBIENT_FADE_OUT_S", 1.0))

    # ── Load + trim at natural 1.0x speed (Fix #2) ────────────────────────
    _LOG.info(
        "Loading source clip %s [%.2f -> %.2f s]",
        source_video.name, start_sec, end_sec,
    )
    raw_clip = VideoFileClip(str(source_video)).subclipped(start_sec, end_sec)
    clip_dur = raw_clip.duration

    # ── Smart centre-crop to 9:16 (Fix #6) ────────────────────────────────
    src_w, src_h = raw_clip.size
    target_ratio = out_w / out_h   # 0.5625 for 9:16

    src_ratio = src_w / src_h
    if src_ratio > target_ratio:
        # Source is wider than 9:16 (e.g. landscape 16:9) — crop width, keep full height
        crop_w = _make_even(int(src_h * target_ratio))
        crop_h = _make_even(src_h)
        x1 = (src_w - crop_w) // 2
        y1 = 0
    else:
        # Source is taller than 9:16 (already portrait) — crop height, keep full width
        crop_w = _make_even(src_w)
        crop_h = _make_even(int(src_w / target_ratio))
        x1 = 0
        y1 = (src_h - crop_h) // 2

    video_clip = raw_clip.cropped(x1=x1, y1=y1, x2=x1 + crop_w, y2=y1 + crop_h)
    video_clip = video_clip.resized((out_w, out_h))

    # ── Mute original audio track (Fix #1) ────────────────────────────────
    # The source footage audio is always discarded; the ElevenLabs track is
    # the sole audio layer in the final export.
    video_clip = video_clip.without_audio()

    # ── Burn hook text via PIL frame transform (Fix #4) ───────────────────
    if hook_lines and _PIL_OK and _NP_OK:
        def _apply_hook(frame: "np.ndarray") -> "np.ndarray":
            return _burn_hook_overlay_pil(
                frame,
                hook_lines=hook_lines,
                font_path=font_path,
                font_size=font_size,
                font_color=font_color,
                stroke_color=stroke_color,
                stroke_width=stroke_width,
                y_frac=hook_y_frac,
                safe_margin_frac=0.08,
            )
        video_clip = video_clip.image_transform(_apply_hook)
    elif hook_lines:
        _LOG.warning("PIL/numpy not available - hook text overlay skipped.")

    # ── Blend ambient audio ────────────────────────────────────────────────
    final_audio = None   # original track already muted above

    if ambient_audio_path and ambient_audio_path.is_file():
        try:
            ambient_raw = AudioFileClip(str(ambient_audio_path))

            # Loop / trim to exact clip duration
            if ambient_raw.duration < clip_dur:
                repeats = math.ceil(clip_dur / ambient_raw.duration)
                try:
                    from moviepy import concatenate_audioclips  # type: ignore[import]
                except ImportError:
                    from moviepy.editor import concatenate_audioclips  # type: ignore[import]
                ambient_loop = concatenate_audioclips([ambient_raw] * repeats)
            else:
                ambient_loop = ambient_raw

            ambient_loop = ambient_loop.subclipped(0, clip_dur)

            # Apply fade-in / fade-out (cosmetic — skipped gracefully on failure)
            try:
                ambient_loop = AudioFadeIn(ambient_loop, fade_in_s)
                ambient_loop = AudioFadeOut(ambient_loop, fade_out_s)
            except Exception:
                try:
                    ambient_loop = ambient_loop.with_effects([
                        AudioFadeIn(fade_in_s), AudioFadeOut(fade_out_s),
                    ])
                except Exception:
                    pass

            ambient_loop = ambient_loop.with_volume_scaled(ambient_vol)
            final_audio = ambient_loop

        except Exception as _ae:
            _LOG.warning("Ambient audio blend failed (%s) - proceeding silent.", _ae)
    else:
        _LOG.warning("No ambient audio path supplied - export will be silent.")

    if final_audio is not None:
        video_clip = video_clip.with_audio(final_audio)

    # ── Export ─────────────────────────────────────────────────────────────
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _LOG.info(
        "Rendering %s | dur=%.1f s | %dx%d @ %d fps",
        output_path.name, clip_dur, out_w, out_h, fps,
    )
    video_clip.write_videofile(
        str(output_path),
        fps=fps,
        codec="libx264",
        audio_codec="aac",
        logger=None,
    )
    raw_clip.close()
    video_clip.close()
    _LOG.info("Render complete: %s", output_path.name)
    return output_path


# ===========================================================================
# Segment picker
# ===========================================================================

def _pick_segment(
    tracker: _SegmentTracker,
    source_videos: list[Path],
    clip_duration: float,
    max_attempts: int = 40,
) -> "tuple[Path, float, float] | None":
    """
    Find a *source_video* + [start, end] window that does not overlap with
    any previously recorded segment.

    Returns ``(video_path, start_sec, end_sec)`` or ``None`` when exhausted.
    """
    if not source_videos:
        return None

    random.shuffle(source_videos)

    for video_path in source_videos:
        fname = video_path.name
        used = tracker.used_ranges(fname)

        try:
            with VideoFileClip(str(video_path)) as _tmp:
                vid_dur = _tmp.duration
        except Exception as exc:
            _LOG.warning("Cannot probe %s: %s - skipping.", fname, exc)
            continue

        if vid_dur <= clip_duration + 1.0:
            _LOG.debug(
                "Video %s too short (%.1f s) for %.0f s clip.",
                fname, vid_dur, clip_duration,
            )
            continue

        for _ in range(max_attempts):
            start = round(random.uniform(0.0, vid_dur - clip_duration), 3)
            end   = round(start + clip_duration, 3)

            overlap = any(start < ue and end > us for us, ue in used)
            if not overlap:
                return video_path, start, end

    _LOG.warning(
        "Could not find a fresh unused segment across all %d source video(s).",
        len(source_videos),
    )
    return None


# ===========================================================================
# Main engine class
# ===========================================================================

class ReferenceReelEngine:
    """
    Orchestrates the full REFERENCE_BASED_REELS pipeline for one or more
    variants in a single run.

    Parameters
    ----------
    page_ctx:
        Active PageContext (from page_loader.load_page_context).
    outputs_dir:
        Root output directory for this page (e.g. outputs/momma_circle/).
    api_key_gemini:
        Google Gemini API key for LLM calls.
    api_key_elevenlabs:
        ElevenLabs API key for ambient audio generation.
    """

    def __init__(
        self,
        page_ctx: Any,
        outputs_dir: Path,
        *,
        api_key_gemini: str = "",
        api_key_elevenlabs: str = "",
    ) -> None:
        self.page_ctx       = page_ctx
        self.page_cfg: dict[str, Any] = getattr(page_ctx, "page_cfg", {})
        self.outputs_dir    = Path(outputs_dir)
        self.api_key_gemini = api_key_gemini
        self.api_key_elevenlabs = api_key_elevenlabs

        # Resolve reference video directory
        ref_dir_raw: str = self.page_cfg.get("REFERENCE_VIDEO_DIR", "")
        if ref_dir_raw:
            ref_dir_candidate = Path(ref_dir_raw)
            if not ref_dir_candidate.is_absolute():
                ref_dir_candidate = _ENGINE_ROOT / ref_dir_raw
        else:
            ref_dir_candidate = (
                _ENGINE_ROOT / "assets" / "Upload Video Reference" / "Momma Alice"
            )
        self.reference_video_dir: Path = ref_dir_candidate

        # Output subdirectories
        self.clips_dir   = self.outputs_dir / "clips"
        self.audio_cache = self.outputs_dir / "audio_cache"
        self.clips_dir.mkdir(parents=True, exist_ok=True)
        self.audio_cache.mkdir(parents=True, exist_ok=True)

        # Segment deduplication tracker
        self.tracker = _SegmentTracker(
            self.outputs_dir / "used_video_segments.json"
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _source_videos(self) -> list[Path]:
        if not self.reference_video_dir.is_dir():
            _LOG.warning(
                "Reference video directory not found: %s",
                self.reference_video_dir,
            )
            return []
        return [
            p for p in self.reference_video_dir.iterdir()
            if p.suffix.lower() in (".mp4", ".mov", ".avi", ".mkv")
            and p.is_file()
        ]

    def _run_stamp(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    def _output_path(self, slug: str, variant: int) -> Path:
        safe = re.sub(r"[^\w]+", "_", slug.lower())[:40].strip("_")
        return self.clips_dir / f"ref_reel_{safe}_v{variant:02d}.mp4"

    # ------------------------------------------------------------------
    # Public run
    # ------------------------------------------------------------------

    def run(
        self,
        quantity: int = 1,
        topic: "str | None" = None,
        clip_duration: "float | None" = None,
        postplanner_dir: "Path | None" = None,
        run_stamp: "str | None" = None,
        posting_slot_display: str = "",
        publish_to_b2: bool = True,
    ) -> dict[str, Any]:
        """
        Generate *quantity* reference-based reel variants.

        Returns an envelope dict compatible with the main.py summary printer.
        """
        qty = max(1, quantity)
        rs  = run_stamp or self._run_stamp()

        # Clip duration: CLI flag > page_config min/max midpoint
        dur_min = float(self.page_cfg.get("CLIP_DURATION_MIN_S", 15.0))
        dur_max = float(self.page_cfg.get("CLIP_DURATION_MAX_S", 60.0))
        if clip_duration is not None:
            clip_dur = max(dur_min, min(float(clip_duration), dur_max))
        else:
            clip_dur = (dur_min + dur_max) / 2.0

        # LLM context
        niche_disclaimer = getattr(self.page_ctx, "niche_disclaimer", "")
        topic_pool: list[str] = getattr(self.page_ctx, "topic_pool", [])
        max_cap_words: int = int(self.page_cfg.get("CAPTION_MAX_WORDS",    220))
        hashtag_count: int = int(self.page_cfg.get("CAPTION_HASHTAG_COUNT", 12))

        # Base ambient prompt from page_config (if set), else use the pool below
        _base_ambient_prompt: str = self.page_cfg.get("AMBIENT_SFX_PROMPT", "")

        # Source videos
        source_videos = self._source_videos()
        if not source_videos:
            _LOG.error(
                "No reference videos found at %s. Drop .mp4 files there and re-run.",
                self.reference_video_dir,
            )
            return {"mode": "live", "items": [], "error": "no_source_videos"}

        _LOG.info(
            "ReferenceReelEngine | qty=%d clip_dur=%.0fs sources=%d",
            qty, clip_dur, len(source_videos),
        )
        print(f"[refReel] {len(source_videos)} source video(s) found in "
              f"{self.reference_video_dir.name}")

        # Output paths
        content_library_path = self.outputs_dir / "content_library.json"
        pp_dir = postplanner_dir or (self.outputs_dir / "postplanner")
        Path(pp_dir).mkdir(parents=True, exist_ok=True)

        results: list[dict[str, Any]] = []

        for variant_idx in range(1, qty + 1):
            print(f"\n[refReel] -- Variant {variant_idx}/{qty} --")
            _variant_result: dict[str, Any] = {
                "variant_index": variant_idx,
                "topic": None,
                "hook_text": None,
                "caption": None,
                "video_path": None,
                "b2_url": None,
                "error": None,
            }

            # ── Pick topic ────────────────────────────────────────────────
            if topic:
                chosen_topic = topic
            elif topic_pool:
                chosen_topic = random.choice(topic_pool)
            else:
                chosen_topic = "The beautiful chaos of motherhood"
            _variant_result["topic"] = chosen_topic
            print(f"[refReel]   Topic : {chosen_topic[:70]}")

            # ── Pick ambient prompt (Fix #3: varied upbeat pool) ──────────
            if _base_ambient_prompt:
                ambient_prompt = _base_ambient_prompt
            else:
                ambient_prompt = random.choice(_AMBIENT_PROMPT_POOL)
            print(f"[refReel]   Audio : {ambient_prompt[:60]}")

            # ── Pick segment ──────────────────────────────────────────────
            seg = _pick_segment(self.tracker, list(source_videos), clip_dur)
            if seg is None:
                _LOG.error("No fresh segment available - variant %d skipped.", variant_idx)
                _variant_result["error"] = "no_fresh_segment"
                results.append(_variant_result)
                continue

            seg_video, seg_start, seg_end = seg
            actual_dur = seg_end - seg_start
            print(
                f"[refReel]   Clip  : {seg_video.name} "
                f"[{seg_start:.1f}s -> {seg_end:.1f}s] ({actual_dur:.1f}s)"
            )

            # ── Generate hook text ────────────────────────────────────────
            print("[refReel]   Generating hook text...")
            hook_text = _generate_hook_text(chosen_topic, niche_disclaimer, self.api_key_gemini)
            hook_lines = _wrap_hook_lines(hook_text, max_chars_per_line=28)
            _variant_result["hook_text"] = hook_text
            print(f"[refReel]   Hook  : {hook_text}")

            # ── Generate caption (Fix #5: log full caption) ───────────────
            print("[refReel]   Generating caption...")
            caption = _generate_caption(
                chosen_topic,
                niche_disclaimer,
                self.api_key_gemini,
                max_words=max_cap_words,
                hashtag_count=hashtag_count,
            )
            _variant_result["caption"] = caption
            # Fix #5 — print full caption to terminal for immediate review
            print(f"\n[refReel] Full Caption:\n{caption}\n")

            # ── Ambient audio ─────────────────────────────────────────────
            print("[refReel]   Preparing ambient audio...")
            ambient_path = _generate_or_fetch_ambient(
                prompt=ambient_prompt,
                duration_s=actual_dur,
                cache_dir=self.audio_cache,
                api_key=self.api_key_elevenlabs,
            )

            # ── Render video ──────────────────────────────────────────────
            slug = re.sub(r"[^\w\s]", "", chosen_topic)[:36].strip()
            out_path = self._output_path(slug, variant_idx)
            print(f"[refReel]   Rendering video -> {out_path.name}...")
            try:
                rendered = _render_reference_reel(
                    source_video=seg_video,
                    start_sec=seg_start,
                    end_sec=seg_end,
                    output_path=out_path,
                    hook_lines=hook_lines,
                    ambient_audio_path=ambient_path,
                    page_cfg=self.page_cfg,
                )
                _variant_result["video_path"] = str(rendered)
                size_mb = rendered.stat().st_size / 1_048_576
                print(f"[refReel]   Rendered  -> {rendered.name} ({size_mb:.1f} MB)")

                # Record segment only after successful render
                self.tracker.record(seg_video.name, seg_start, seg_end)

            except Exception as render_exc:
                _LOG.error(
                    "Render failed for variant %d: %s",
                    variant_idx, render_exc, exc_info=True,
                )
                _variant_result["error"] = f"render_error: {render_exc}"
                results.append(_variant_result)
                continue

            # ── B2 upload ─────────────────────────────────────────────────
            b2_url = ""
            if publish_to_b2 and rendered.is_file():
                try:
                    from avatar_engine.b2_client import B2VideoUploader
                    b2_url = B2VideoUploader().upload(rendered)
                    _variant_result["b2_url"] = b2_url
                    print(f"[refReel]   B2 URL    -> {b2_url}")
                except Exception as b2_exc:
                    _LOG.warning("B2 upload failed: %s", b2_exc)

            # ── Content library ───────────────────────────────────────────
            lib_entry: dict[str, Any] = {
                "topic": chosen_topic,
                "hook_text": hook_text,
                "final_caption": caption,
                "video_path": str(rendered),
                "b2_url": b2_url,
                "source_video": seg_video.name,
                "segment_start_sec": seg_start,
                "segment_end_sec": seg_end,
                "ambient_prompt": ambient_prompt,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            try:
                from avatar_engine.content_library import append_entry
                append_entry(content_library_path, lib_entry)
            except Exception as lib_exc:
                _LOG.warning("content_library write failed: %s", lib_exc)

            # ── PostPlanner xlsx ──────────────────────────────────────────
            media_url = b2_url or str(rendered)
            try:
                from avatar_engine.post_planner import (
                    append_postplanner_xlsx_row,
                    append_planner_row,
                )
                import config as _cfg

                append_postplanner_xlsx_row(
                    Path(pp_dir),
                    run_stamp=rs,
                    posting_time=posting_slot_display,
                    caption=caption,
                    media_url=media_url,
                )

                bulk_xlsx = self.outputs_dir / "automated_bulk_posts_import.xlsx"
                append_planner_row(
                    bulk_xlsx,
                    posting_time=posting_slot_display,
                    caption=caption,
                    url_link="",
                    media_url=media_url,
                    post_type_value="VIDEO",
                    template_path=getattr(_cfg, "BULK_POSTS_TEMPLATE_XLSX", None),
                )
                print("[refReel]   PostPlanner updated.")
            except Exception as pp_exc:
                _LOG.warning("PostPlanner write failed: %s", pp_exc)

            results.append(_variant_result)

        # ── Final summary ─────────────────────────────────────────────────
        successful = [r for r in results if r.get("video_path")]
        print(
            f"\n[refReel] Done: {len(successful)}/{qty} variant(s) rendered successfully."
        )

        return {
            "mode": "live",
            "post_type": "REFERENCE_BASED_REELS",
            "page_id": getattr(self.page_ctx, "page_id", "momma_circle"),
            "quantity": qty,
            "items": results,
            "successful": len(successful),
        }


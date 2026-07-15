# -*- coding: utf-8 -*-
"""
ECONOMIC_REEL video compiler.

Builds a vertical short-form MP4 from a graphite background image,
optional ElevenLabs voiceover, and optional ambient audio track.

Pipeline
--------
1. Load the rendered graphite image; resize/crop to 9:16 canvas (1080 × 1920).
2. Apply a slow, continuous Ken Burns zoom-in (1.0 → zoom_end) over the full
   clip duration — the same motion effect used by video_converter.py but driven
   by audio length instead of a fixed 7-second loop.
3. Duration is derived from the voice audio length + a 1.5-second tail.
   Falls back to ``target_duration`` when no voice track is supplied.
4. Mix narration (full volume) over the ambient track (_AMBIENT_VOLUME).
5. Export as vertical MP4 — H.264 / AAC, 9:16 frame, 30 fps.

Requirements
------------
  pip install moviepy numpy Pillow

ffmpeg must be on PATH (required by moviepy for audio encoding).
"""
from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Canvas constants
# ---------------------------------------------------------------------------
_REEL_WIDTH: int = 1080
_REEL_HEIGHT: int = 1920       # 9:16 vertical
_DEFAULT_DURATION: float = 30.0
_ZOOM_START: float = 1.0
_ZOOM_END: float = 1.12        # peak zoom at midpoint (12% in)
_ZOOM_FLOOR: float = 1.04      # graceful zoom-out floor for the second half
_AMBIENT_VOLUME: float = 0.22  # ambient sits quietly under narration
_DEFAULT_FPS: int = 30


_REEL_CTA: str = "Drop a comment with your answer.\nLet's unpack this below."


def compile_dynamic_reel(
    image_path: Path,
    hook_text: str,
    *,
    voice_audio: Path | None = None,
    ambient_audio: Path | None = None,
    output_path: Path | None = None,
    target_duration: float = _DEFAULT_DURATION,
    zoom_end: float = _ZOOM_END,
    fps: int = _DEFAULT_FPS,
    font_path: str | None = None,
    font_size_scale: float = 0.07,
    sub_text: str | None = _REEL_CTA,
    overlay_opacity: float = 0.35,
    word_timings: "list[tuple[str, float, float]] | None" = None,
    brand_label: str | None = None,
    logo_image_path: "Path | None" = None,
    subtitle_fontsize: int = 46,
    subtitle_y_position: "int | None" = None,
    logo_width_px: int = 160,
    logo_y_offset_px: int = 90,
    logo_opacity: float = 0.70,
    logo_max_height_px: "int | None" = None,
    hook_y_frac: float = 0.55,
    page_id: str = "",
) -> Path:
    """
    Compile a cinematic vertical reel from a CLEAN graphite background image asset.

    The background PNG arrives with logo watermark only (no baked text).
    This function renders the hook_text directly into each video frame via Pillow
    so the text participates in the Ken Burns zoom motion, never appearing twice.

    Parameters
    ----------
    image_path      : Rendered graphite PNG (logo-only, no text overlay).
    hook_text       : Narration hook — displayed on screen AND spoken as voiceover.
    voice_audio     : ElevenLabs TTS mp3.  Drives clip duration when supplied.
    ambient_audio   : Dark ambient soundscape mp3.  Mixed at _AMBIENT_VOLUME.
    output_path     : Output mp4 path.  Defaults to image_path.stem + '_reel.mp4'.
    target_duration : Fallback duration (seconds) when no voice track is supplied.
    zoom_end        : Final zoom scale factor (1.12 = 12 % zoom-in over clip lifetime).
    fps             : Output frame rate.
    font_path       : Absolute path to .ttf font file.  Uses Pillow system font if None.
    font_size_scale : Font size as fraction of canvas width (default 0.07 = 7 %).
    sub_text        : Optional engagement CTA rendered below the hook (default: reel CTA).
                      Pass None to suppress the secondary text layer entirely.
    overlay_opacity : 0-1 dark vignette applied over the zoomed frame before text is composited.
                      0.35 = 35 % black — improves text legibility on light graphite areas.

    Returns
    -------
    Path to the compiled MP4 file.
    """
    try:
        import numpy as np
        from PIL import Image as PILImage, ImageDraw, ImageFont
        # MoviePy 2.x uses top-level imports (no `.editor` submodule)
        from moviepy import (  # type: ignore
            AudioFileClip,
            CompositeAudioClip,
            VideoClip,
        )
    except ImportError as exc:
        raise RuntimeError(
            "video_engine requires moviepy>=2.0, numpy, and Pillow.\n"
            "Run: pip install moviepy numpy Pillow\n"
            f"Original error: {exc}"
        ) from exc

    # ── WONDER_FEED EXECUTION-LAYER HARDCODES ─────────────────────────────────
    # These constants are applied BEFORE any config value is used, ensuring that
    # variable fallback chains and partial config reads cannot drift the layout.
    # They override whatever was passed in from page_ctx or CLI flags.
    _WF_HOOK_Y_FRAC      = 0.50   # dead vertical centre  → set_position(('center','center'))
    _WF_SUBTITLE_Y       = 1400   # lower-third lock       → set_position(('center', 1400))
    _WF_SUBTITLE_FONT_SZ = 70     # subtitle font size      → subtitle_fontsize (base 50 + 20)
    _WF_LOGO_W_PX        = 380    # maximum prominence width → logo_clip.resize(width=380)
    _WF_LOGO_TOP_Y       = 1600   # absolute top-of-logo y  → set_position(('center', 1600))
    _WF_LOGO_OPACITY     = 0.98   # near-full brand opacity  → set_opacity(0.98)
    if page_id.lower() == "wonder_feed":
        hook_y_frac         = _WF_HOOK_Y_FRAC
        subtitle_y_position = _WF_SUBTITLE_Y
        subtitle_fontsize   = _WF_SUBTITLE_FONT_SZ
        logo_width_px       = _WF_LOGO_W_PX
        logo_opacity        = _WF_LOGO_OPACITY
        logger.info(
            "wonder_feed LAYOUT LOCK | hook_y_frac=%.2f | subtitle_y=%d | sub_font=%d"
            " | logo_w=%d | logo_top_y=%d | logo_opacity=%.0f%%",
            _WF_HOOK_Y_FRAC, _WF_SUBTITLE_Y, _WF_SUBTITLE_FONT_SZ,
            _WF_LOGO_W_PX, _WF_LOGO_TOP_Y, _WF_LOGO_OPACITY * 100,
        )

    logger.info(
        "compile_dynamic_reel START | overlay_opacity=%.2f (alpha=%d/255) | "
        "logo_w=%d | logo_top_y=%d | subtitle_font=%d | hook_y_frac=%.2f",
        overlay_opacity,
        int(255 * min(1.0, max(0.0, overlay_opacity))),
        logo_width_px,
        logo_y_offset_px,
        subtitle_fontsize,
        hook_y_frac,
    )

    image_path = Path(image_path)
    if output_path is None:
        output_path = image_path.parent / (image_path.stem + "_reel.mp4")
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # -------------------------------------------------------------------------
    # 1. Prepare 9:16 canvas from the graphite image
    # -------------------------------------------------------------------------
    pil_src = PILImage.open(image_path).convert("RGB")
    src_w, src_h = pil_src.size
    target_ratio = _REEL_WIDTH / _REEL_HEIGHT
    src_ratio = src_w / src_h

    if src_ratio > target_ratio:
        new_h = _REEL_HEIGHT
        new_w = int(src_w * _REEL_HEIGHT / src_h)
    else:
        new_w = _REEL_WIDTH
        new_h = int(src_h * _REEL_WIDTH / src_w)

    pil_scaled = pil_src.resize((new_w, new_h), PILImage.LANCZOS)
    left = (new_w - _REEL_WIDTH) // 2
    top  = (new_h - _REEL_HEIGHT) // 2
    pil_canvas = pil_scaled.crop((left, top, left + _REEL_WIDTH, top + _REEL_HEIGHT))
    base_array = np.array(pil_canvas)

    logger.info(
        "ECONOMIC_REEL | canvas ready %dx%d (source %dx%d) | hook: %s",
        _REEL_WIDTH, _REEL_HEIGHT, src_w, src_h, hook_text[:60],
    )

    # -------------------------------------------------------------------------
    # 2. Pre-render text overlay as a static transparent RGBA layer
    #    The layer is composited onto each zoomed frame at render time.
    # -------------------------------------------------------------------------
    # Resolve italic font path from same directory as the bold font (Poppins-Italic beside Poppins-Bold)
    _sub_font_path: str | None = None
    if font_path:
        import re as _re
        _sub_font_path = _re.sub(r"(?i)Poppins-Bold", "Poppins-Italic", font_path) or None

    # When word-level subtitles are active the dynamic subtitle stream owns the
    # lower-third zone.  Suppress the static CTA (sub_text) to avoid a layout
    # clash between the two text regions.
    _effective_sub_text = None if word_timings else sub_text

    text_overlay_array: np.ndarray | None = None
    if hook_text and hook_text.strip():
        text_overlay_array = _render_text_layer(
            hook_text.strip(),
            canvas_w=_REEL_WIDTH,
            canvas_h=_REEL_HEIGHT,
            font_path=font_path,
            font_size_scale=font_size_scale,
            sub_text=_effective_sub_text,
            sub_font_path=_sub_font_path,
            hook_y_frac=hook_y_frac,
        )

    # Pre-render brand label as a completely static RGBA layer.
    # SUPPRESSED when logo_image_path is explicitly provided: if the caller
    # declared a logo PNG path, we use that image or produce no watermark at
    # all — we never silently fall back to a text string.  This prevents the
    # "@ Wonder Feed" text ghost from appearing when logo.png fails to load.
    brand_label_array: np.ndarray | None = None
    if brand_label and brand_label.strip() and not logo_image_path:
        brand_label_array = _render_brand_label(
            brand_label.strip(),
            canvas_w=_REEL_WIDTH,
            canvas_h=_REEL_HEIGHT,
            font_path=font_path,
        )

    # Pre-render logo PNG as a static full-canvas RGBA layer.
    # Takes priority over brand_label text when the image file is available.
    # Width driven by logo_width_px (absolute pixels); y anchored via logo_y_offset_px.
    logo_static_array: np.ndarray | None = None
    if logo_image_path and Path(logo_image_path).is_file():
        try:
            _logo_pil = PILImage.open(logo_image_path).convert("RGBA")
            # Width-driven resize (absolute pixel target from config)
            _logo_target_w = max(1, logo_width_px)
            _logo_ratio = _logo_target_w / _logo_pil.width
            _logo_target_h = max(1, int(_logo_pil.height * _logo_ratio))
            # Enforce hard height cap (Zone C: max 45px per spec)
            if logo_max_height_px and _logo_target_h > logo_max_height_px:
                _logo_target_h = logo_max_height_px
                _logo_target_w = max(1, int(_logo_pil.width * (_logo_target_h / _logo_pil.height)))
            _logo_pil = _logo_pil.resize((_logo_target_w, _logo_target_h), PILImage.LANCZOS)
            # Apply configurable opacity (default 60% per Zone C spec)
            _opacity_factor = max(0.05, min(1.0, logo_opacity))
            _r, _g, _b, _a = _logo_pil.split()
            _a = _a.point(lambda px: int(px * _opacity_factor))
            _logo_pil = PILImage.merge("RGBA", (_r, _g, _b, _a))
            _logo_canvas = PILImage.new("RGBA", (_REEL_WIDTH, _REEL_HEIGHT), (0, 0, 0, 0))
            _logo_x = (_REEL_WIDTH - _logo_target_w) // 2
            # wonder_feed: absolute top-of-logo coordinate (matches set_position(('center',1720)))
            # All other pages: derive from logo_y_offset_px as normal.
            _logo_y = (
                _WF_LOGO_TOP_Y
                if page_id.lower() == "wonder_feed"
                else _REEL_HEIGHT - logo_y_offset_px - _logo_target_h
            )
            _logo_canvas.paste(_logo_pil, (_logo_x, _logo_y), _logo_pil)
            logo_static_array = np.array(_logo_canvas)
            logger.info(
                "Logo PNG pre-rendered: %dx%d at (%d, %d) — static, post-zoom layer"
                " [logo_width_px=%d, logo_y_offset_px=%d, opacity=%.0f%%]",
                _logo_target_w, _logo_target_h, _logo_x, _logo_y,
                logo_width_px, logo_y_offset_px, _opacity_factor * 100,
            )
        except Exception as _logo_exc:
            logger.warning(
                "Logo PNG load failed (%s) — falling back to brand_label text.", _logo_exc
            )

    # -------------------------------------------------------------------------
    # 2b. Pre-render subtitle word cache (one render per unique word)
    #     Called once at compile time; _make_frame() does a cheap dict lookup.
    # -------------------------------------------------------------------------
    _subtitle_cache: dict[str, np.ndarray] = {}
    if word_timings:
        for _wt_word, _, _ in word_timings:
            if _wt_word not in _subtitle_cache:
                _subtitle_cache[_wt_word] = _render_subtitle_word(
                    _wt_word,
                    canvas_w=_REEL_WIDTH,
                    canvas_h=_REEL_HEIGHT,
                    font_path=_sub_font_path,
                    font_size=subtitle_fontsize,
                    y_pos=subtitle_y_position,
                    y_frac=0.82,
                )
        logger.info(
            "Subtitle cache built: %d unique words [fontsize=%d, y_pos=%s]",
            len(_subtitle_cache), subtitle_fontsize, subtitle_y_position,
        )

    # -------------------------------------------------------------------------
    # 3. Determine total duration from voice audio
    # -------------------------------------------------------------------------
    duration = target_duration
    voice_clip: AudioFileClip | None = None

    if voice_audio and Path(voice_audio).is_file():
        try:
            voice_clip = AudioFileClip(str(voice_audio))
            # Use voice length + 2 s tail, floored at 30 s for short-form reel spec
            duration = max(30.0, voice_clip.duration + 2.0)
            logger.info("Voice audio %.1fs → reel duration %.1fs", voice_clip.duration, duration)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not load voice audio (%s) — using target_duration.", exc)

    # -------------------------------------------------------------------------
    # 4. Ken Burns zoom-in + text composite — one make_frame closure
    # -------------------------------------------------------------------------
    _duration = duration

    def _make_frame(t: float) -> np.ndarray:
        # ── FIXED TWO-STAGE ZOOM KEYFRAME ────────────────────────────────────
        # First 16 seconds : Sharp and deep magnification  1.00 → 1.35×
        # Post-16 seconds  : Progressive zoom-out           1.35 → 1.15×
        #
        # Equivalent to the requested formula:
        #   t ≤ 16: scale = 1.0 + (0.35 * (t / 16.0))
        #   t > 16: scale = 1.35 - (0.20 * (elapsed / max(remaining, 1.0)))
        #
        # Only the background layer participates in this transform.
        # Dark overlay (45%), text, subtitles, logo, and film grain are
        # composited as perfectly static post-zoom layers — never drift.
        _ZOOM_IN_DURATION  = 16.0
        _ZOOM_PEAK         = 1.35   # peak reached at t == 16.0
        _ZOOM_OUT_FLOOR    = 1.15   # floor reached at clip end (1.35 - 0.20)
        if t <= _ZOOM_IN_DURATION:
            scale = 1.0 + (_ZOOM_PEAK - 1.0) * (t / _ZOOM_IN_DURATION)
        else:
            _remaining = t - _ZOOM_IN_DURATION
            _total_out = max(_duration - _ZOOM_IN_DURATION, 1.0)
            scale = _ZOOM_PEAK - (_ZOOM_PEAK - _ZOOM_OUT_FLOOR) * (_remaining / _total_out)
        scale = max(scale, 1.0)     # clamp: never shrink below source dimensions

        zoom_w = int(_REEL_WIDTH * scale)
        zoom_h = int(_REEL_HEIGHT * scale)
        pil_frame = PILImage.fromarray(base_array).resize(
            (zoom_w, zoom_h), PILImage.LANCZOS
        )
        x0 = (zoom_w - _REEL_WIDTH) // 2
        y0 = (zoom_h - _REEL_HEIGHT) // 2
        pil_cropped = pil_frame.crop((x0, y0, x0 + _REEL_WIDTH, y0 + _REEL_HEIGHT))

        # ── LAYER 2: permanent black overlay matrix (45% opacity) ────────────
        # Composited directly over the zoomed background frame on EVERY render
        # tick — guarantees consistent contrast separation for subtitle text
        # regardless of how light or dark the graphite art layer is underneath.
        # alpha = int(255 × overlay_opacity) → 0.45 → alpha 114/255.
        if overlay_opacity > 0:
            pil_rgba = pil_cropped.convert("RGBA")
            _ov_alpha = int(255 * min(1.0, max(0.0, overlay_opacity)))
            dark_vignette = PILImage.new(
                "RGBA",
                (_REEL_WIDTH, _REEL_HEIGHT),
                (0, 0, 0, _ov_alpha),
            )
            pil_rgba.alpha_composite(dark_vignette)
            pil_cropped = pil_rgba.convert("RGB")

        # ── LAYER 3: static pre-rendered text (hook headline + CTA) ───────────
        if text_overlay_array is not None:
            pil_rgb = pil_cropped.convert("RGBA")
            text_layer = PILImage.fromarray(text_overlay_array, mode="RGBA")
            pil_rgb.alpha_composite(text_layer)
            pil_cropped = pil_rgb.convert("RGB")

        # ── LAYER 3b: dynamic word-level subtitle ─────────────────────────────
        # Binary-searched from the pre-rendered _subtitle_cache; zero PIL draw
        # calls at render time — only a dict lookup + one alpha_composite.
        if _subtitle_cache and word_timings:
            _active_word: str | None = None
            for _sw, _st0, _st1 in word_timings:
                if _st0 <= t < _st1:
                    _active_word = _sw
                    break
            if _active_word and _active_word in _subtitle_cache:
                pil_sub = pil_cropped.convert("RGBA")
                sub_layer = PILImage.fromarray(_subtitle_cache[_active_word], mode="RGBA")
                pil_sub.alpha_composite(sub_layer)
                pil_cropped = pil_sub.convert("RGB")

        # ── LAYER 3c: static brand logo (fully isolated from zoom transform) ──
        # Composited AFTER the zoom crop — coordinates pinned to canvas frame,
        # never shifting with the Ken Burns background motion.
        # Logo PNG takes priority; brand_label text is the fallback.
        if logo_static_array is not None:
            pil_lo = pil_cropped.convert("RGBA")
            lo_layer = PILImage.fromarray(logo_static_array, mode="RGBA")
            pil_lo.alpha_composite(lo_layer)
            pil_cropped = pil_lo.convert("RGB")
        elif brand_label_array is not None:
            pil_bl = pil_cropped.convert("RGBA")
            bl_layer = PILImage.fromarray(brand_label_array, mode="RGBA")
            pil_bl.alpha_composite(bl_layer)
            pil_cropped = pil_bl.convert("RGB")

        # ── LAYER 4: cinematic film grain (shutter flicker simulation) ────────
        # Variable opacity function: 0.02 + 0.015 * sin(t * 12.0)
        # Produces subtle luminance noise that makes deep charcoal textures
        # feel alive — completely invisible on smooth areas.
        frame = np.array(pil_cropped)
        grain_alpha = 0.02 + 0.015 * math.sin(t * 12.0)
        noise = np.random.randint(0, 64, frame.shape, dtype=np.uint8)
        frame = np.clip(
            frame.astype(np.int16) + (noise * grain_alpha).astype(np.int16),
            0, 255
        ).astype(np.uint8)
        return frame

    # MoviePy 2.x renamed the constructor kwarg from `make_frame` → `frame_function`.
    # Using the correct kwarg prevents the TypeError crash on newer installs.
    video_clip = VideoClip(frame_function=_make_frame, duration=duration)
    video_clip = video_clip.with_fps(fps)

    # -------------------------------------------------------------------------
    # 5. Audio mix: narration (full vol) over ambient (reduced vol)
    # -------------------------------------------------------------------------
    audio_tracks = []

    # MoviePy 2.x method renames:
    #   volumex()     → with_volume_scaled()
    #   set_duration()→ with_duration()
    #   set_audio()   → with_audio()
    if voice_clip is not None:
        audio_tracks.append(voice_clip.with_volume_scaled(1.0))

    if ambient_audio and Path(ambient_audio).is_file():
        try:
            amb_raw = AudioFileClip(str(ambient_audio))
            if amb_raw.duration < duration:
                loops_needed = int(duration / amb_raw.duration) + 1
                from moviepy import concatenate_audioclips  # type: ignore
                amb_clip = concatenate_audioclips([amb_raw] * loops_needed).with_duration(duration)
            else:
                amb_clip = amb_raw.with_duration(duration)
            audio_tracks.append(amb_clip.with_volume_scaled(_AMBIENT_VOLUME))
            logger.info("Ambient track mixed at %.0f%% volume", _AMBIENT_VOLUME * 100)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Ambient audio load failed (%s) — skipping.", exc)

    if audio_tracks:
        mixed_audio = (
            CompositeAudioClip(audio_tracks) if len(audio_tracks) > 1 else audio_tracks[0]
        )
        video_clip = video_clip.with_audio(mixed_audio)

    # -------------------------------------------------------------------------
    # 6. Export vertical MP4
    # -------------------------------------------------------------------------
    logger.info("Rendering ECONOMIC_REEL → %s (%.1fs @ %dfps)", output_path.name, duration, fps)
    try:
        video_clip.write_videofile(
            str(output_path),
            fps=fps,
            codec="libx264",
            audio_codec="aac",
            preset="fast",
            ffmpeg_params=["-crf", "20", "-pix_fmt", "yuv420p"],
            logger=None,
        )
    finally:
        # ── STEP 1: Close all clip handles before touching any files ──────────
        # Closing MUST happen before os.remove() — open handles cause WinError 32.
        try:
            if "video_clip" in dir() or video_clip is not None:
                video_clip.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            if voice_clip is not None:
                voice_clip.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            if "amb_clip" in dir():
                amb_clip.close()   # type: ignore[name-defined]
        except Exception:  # noqa: BLE001
            pass
        try:
            if "amb_raw" in dir():
                amb_raw.close()    # type: ignore[name-defined]
        except Exception:  # noqa: BLE001
            pass

        # ── STEP 2: Force GC to drop any lingering stream references ──────────
        import gc as _gc
        _gc.collect()

        # ── STEP 3: Safe audio temp-file deletion with OS-lock fallback ───────
        import os as _os_clean
        _audio_exts = (".mp3", ".wav", ".aac")
        _search_dirs = [output_path.parent, image_path.parent]
        for _adir in _search_dirs:
            if not _adir.is_dir():
                continue
            for _af in list(_adir.iterdir()):
                if _af.suffix.lower() in _audio_exts and _af.is_file():
                    try:
                        _os_clean.remove(_af)
                        logger.debug("Cleaned temp audio: %s", _af.name)
                    except OSError:
                        # Windows keeps the handle locked — skip silently
                        print(
                            f"| Temporary audio retained (locked by OS): "
                            f"{_af.name}"
                        )
                    except Exception as _ae:  # noqa: BLE001
                        logger.warning("Could not remove temp audio %s: %s", _af.name, _ae)

    logger.info("ECONOMIC_REEL compiled → %s", output_path.name)
    return output_path


# ---------------------------------------------------------------------------
# Internal helper: build a static RGBA text overlay layer (1080 × 1920)
# ---------------------------------------------------------------------------

def _render_text_layer(
    text: str,
    *,
    canvas_w: int = _REEL_WIDTH,
    canvas_h: int = _REEL_HEIGHT,
    font_path: str | None = None,
    font_size_scale: float = 0.07,
    text_color: tuple[int, int, int] = (255, 255, 255),
    stroke_color: tuple[int, int, int] = (0, 0, 0),
    stroke_width: int = 0,   # no outlines — clean transparent-background text only
    dim_alpha: int = 0,
    max_width_ratio: float = 0.80,
    sub_text: str | None = None,
    sub_font_path: str | None = None,  # italic font for CTA; falls back to system italic
    hook_y_frac: float = 0.55,  # Zone A vertical centre (0.30 = upper-middle for wonder_feed)
) -> "np.ndarray":
    """
    Render `text` centred on a transparent RGBA canvas.

    Layers (bottom-up):
      A. Subtle dark rectangle behind the text block (dim_alpha opacity).
      B. White text with a thin dark stroke for maximum graphite-on-graphite legibility.

    Returns a numpy uint8 RGBA array of shape (canvas_h, canvas_w, 4).
    """
    import textwrap
    import numpy as np
    from PIL import Image as PILImage, ImageDraw, ImageFont

    max_px_wide = int(canvas_w * max_width_ratio)
    base_size   = int(canvas_w * font_size_scale)

    # --- Load bold font (title / hook text) ---
    def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        if font_path:
            try:
                return ImageFont.truetype(font_path, size)
            except OSError:
                pass
        _bold_fallbacks = [
            "Poppins-Bold.ttf", "Poppins-SemiBold.ttf",
            "Montserrat-Bold.ttf", "Arial Bold.ttf", "arialbd.ttf", "trebucbd.ttf",
        ]
        for name in _bold_fallbacks:
            try:
                return ImageFont.truetype(name, size)
            except OSError:
                continue
        return ImageFont.load_default()

    # --- Load italic font (CTA / sub-text) ---
    def _load_italic_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        if sub_font_path:
            try:
                return ImageFont.truetype(sub_font_path, size)
            except OSError:
                pass
        # Try Poppins italic variants, then system italic fallbacks
        _italic_fallbacks = [
            "Poppins-Italic.ttf", "Poppins-LightItalic.ttf", "Poppins-MediumItalic.ttf",
            "Montserrat-Italic.ttf", "arial.ttf",
        ]
        for name in _italic_fallbacks:
            try:
                return ImageFont.truetype(name, size)
            except OSError:
                continue
        return _load_font(size)  # ultimate fallback: use bold font

    # --- Auto-fit: shrink font until all words fit within max_px_wide ---
    font_size = base_size
    font = _load_font(font_size)
    dummy = PILImage.new("RGBA", (canvas_w, canvas_h))
    draw  = ImageDraw.Draw(dummy)

    words = text.split()
    wrapped_lines: list[str] = []
    while font_size > 20:
        wrapped_lines = []
        current_line: list[str] = []
        for word in words:
            test_line = " ".join(current_line + [word])
            bbox = draw.textbbox((0, 0), test_line, font=font, stroke_width=stroke_width)
            if (bbox[2] - bbox[0]) > max_px_wide and current_line:
                wrapped_lines.append(" ".join(current_line))
                current_line = [word]
            else:
                current_line.append(word)
        if current_line:
            wrapped_lines.append(" ".join(current_line))

        # Verify widest line fits
        widest = max(
            draw.textbbox((0, 0), ln, font=font, stroke_width=stroke_width)[2]
            - draw.textbbox((0, 0), ln, font=font, stroke_width=stroke_width)[0]
            for ln in wrapped_lines
        ) if wrapped_lines else 0
        if widest <= max_px_wide:
            break
        font_size = int(font_size * 0.92)
        font = _load_font(font_size)

    # --- Measure block dimensions ---
    line_bboxes = [
        draw.textbbox((0, 0), ln, font=font, stroke_width=stroke_width)
        for ln in wrapped_lines
    ]
    line_heights = [bb[3] - bb[1] for bb in line_bboxes]
    line_widths  = [bb[2] - bb[0] for bb in line_bboxes]
    leading      = int(font_size * 0.18)
    block_h      = sum(line_heights) + leading * max(0, len(wrapped_lines) - 1)
    block_w      = max(line_widths) if line_widths else 0

    # Vertical centre driven by hook_y_frac (Zone A for wonder_feed = 0.30)
    block_top  = int(canvas_h * hook_y_frac) - block_h // 2
    pad_x, pad_y = int(canvas_w * 0.04), int(canvas_h * 0.018)

    # --- Compose RGBA layer ---
    layer = PILImage.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
    draw  = ImageDraw.Draw(layer)
    # rect_x* / rect_y* kept for sub_text positioning even though no box is drawn
    rect_x0 = (canvas_w - block_w) // 2 - pad_x
    rect_y0 = block_top - pad_y
    rect_x1 = (canvas_w + block_w) // 2 + pad_x
    rect_y1 = block_top + block_h + pad_y

    # No bounding box drawn — raw transparent background only.
    # Legibility is maintained purely via the stroke outline on each glyph.

    # Text lines (white + thicker stroke for legibility without a box)
    y = block_top
    for i, line in enumerate(wrapped_lines):
        line_w = line_widths[i]
        x = (canvas_w - line_w) // 2
        draw.text(
            (x, y),
            line,
            font=font,
            fill=(*text_color, 255),
            stroke_width=stroke_width,
            stroke_fill=(*stroke_color, 255),
        )
        y += line_heights[i] + leading

    # -------------------------------------------------------------------------
    # C. Secondary engagement CTA — smaller, lighter, below the hook block
    # -------------------------------------------------------------------------
    if sub_text and sub_text.strip():
        sub_font_size = max(18, int(font_size * 0.45))
        sub_font = _load_italic_font(sub_font_size)
        sub_lines = [ln.strip() for ln in sub_text.strip().splitlines() if ln.strip()]

        sub_bboxes = [
            draw.textbbox((0, 0), ln, font=sub_font, stroke_width=0)
            for ln in sub_lines
        ]
        sub_heights = [bb[3] - bb[1] for bb in sub_bboxes]
        sub_widths  = [bb[2] - bb[0] for bb in sub_bboxes]
        sub_leading = int(sub_font_size * 0.25)
        sub_block_h = sum(sub_heights) + sub_leading * max(0, len(sub_lines) - 1)
        sub_block_w = max(sub_widths) if sub_widths else 0

        # Position CTA just below the main block with a gap
        sub_gap  = int(canvas_h * 0.025)
        sub_top  = rect_y1 + pad_y + sub_gap

        # No backdrop — transparent background, stroke only (matches main text layer).

        sy = sub_top
        for i, sub_line in enumerate(sub_lines):
            sx = (canvas_w - sub_widths[i]) // 2
            draw.text(
                (sx, sy),
                sub_line,
                font=sub_font,
                fill=(255, 255, 255, 190),   # 75% opacity — softer than main hook
                stroke_width=0,              # no outlines on CTA text
            )
            sy += sub_heights[i] + sub_leading

    return np.array(layer)


# ---------------------------------------------------------------------------
# Subtitle renderer — one spoken word per call, cached at compile time
# ---------------------------------------------------------------------------

def _render_brand_label(
    label: str,
    *,
    canvas_w: int = _REEL_WIDTH,
    canvas_h: int = _REEL_HEIGHT,
    font_path: str | None = None,
    font_size: int = 28,
    y_offset_from_bottom: int = 90,
) -> "np.ndarray":
    """
    Render the page brand label (e.g. "@ Wonder Feed") as a static RGBA layer
    pinned to the bottom-centre of the frame.

    Composited AFTER the zoom transform so it never drifts, pulses, or scales
    with the Ken Burns background motion.

    Typography: small, Poppins-Light preferred → italic → regular fallback chain.
    Colour: white at 70% opacity.  Stroke: none.
    """
    import numpy as np
    from PIL import Image as _PI, ImageDraw as _ID, ImageFont as _IF

    layer = _PI.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
    draw = _ID.Draw(layer)

    _label_candidates = [
        font_path,
        "Poppins-Light.ttf",
        "Poppins-Regular.ttf",
        "Poppins-Bold.ttf",
        "Montserrat-Regular.ttf",
        "arial.ttf",
    ]
    font = None
    for _fp in _label_candidates:
        if not _fp:
            continue
        try:
            font = _IF.truetype(_fp, font_size)
            break
        except OSError:
            continue
    if font is None:
        font = _IF.load_default()

    bbox = draw.textbbox((0, 0), label, font=font, stroke_width=0)
    tw = bbox[2] - bbox[0]
    x = (canvas_w - tw) // 2
    y = canvas_h - y_offset_from_bottom

    draw.text(
        (x, y),
        label,
        font=font,
        fill=(255, 255, 255, 178),   # 70% opacity — elegant, non-intrusive
        stroke_width=0,
    )
    return np.array(layer)


def _render_subtitle_word(
    word: str,
    *,
    canvas_w: int = _REEL_WIDTH,
    canvas_h: int = _REEL_HEIGHT,
    font_path: str | None = None,
    font_size: int = 52,
    y_pos: "int | None" = None,
    y_frac: float = 0.82,
) -> "np.ndarray":
    """
    Render a single spoken word as a centre-aligned RGBA subtitle layer.

    Typography rules
    ----------------
    - Font: Poppins-Italic (falls back through italic system fonts to default)
    - Colour: yellow at ~94% opacity (255, 255, 0, 240)
    - Stroke: none (stroke_width=0)
    - Position: centred horizontally; ``y_pos`` (absolute pixels from top) when
      supplied, otherwise ``y_frac`` of canvas height (default 82%).

    Returns a ``(canvas_h, canvas_w, 4)`` uint8 RGBA numpy array.
    Called once per unique word at compile time and cached — zero per-frame cost.
    """
    import numpy as np
    from PIL import Image as _PI, ImageDraw as _ID, ImageFont as _IF

    layer = _PI.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
    draw = _ID.Draw(layer)

    # Italic font preference chain
    _italic_candidates = [
        font_path,
        "Poppins-Italic.ttf",
        "Poppins-LightItalic.ttf",
        "Poppins-MediumItalic.ttf",
        "Montserrat-Italic.ttf",
        "arial.ttf",
    ]
    font = None
    for _fp in _italic_candidates:
        if not _fp:
            continue
        try:
            font = _IF.truetype(_fp, font_size)
            break
        except OSError:
            continue
    if font is None:
        font = _IF.load_default()

    bbox = draw.textbbox((0, 0), word, font=font, stroke_width=0)
    tw = bbox[2] - bbox[0]
    x = (canvas_w - tw) // 2
    y = y_pos if y_pos is not None else int(canvas_h * y_frac)

    draw.text(
        (x, y),
        word,
        font=font,
        fill=(255, 255, 0, 240),   # bright yellow — high contrast on dark graphite backgrounds
        stroke_width=0,
    )
    return np.array(layer)

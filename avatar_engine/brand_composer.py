# -*- coding: utf-8 -*-
"""
Brand asset compositor — Unified Multi-Page Factory.

Provides three compositing operations applied AFTER raw Gemini background
generation and caption extraction, so overlay text is always available:

  apply_text_overlay()    — Pillow: modern minimalist cinematic typography.
                            10 % global canvas dim (Alpha=25) for legibility.
                            No bounding-box frame — background stays bright and vivid.
                            Soft directional drop-shadow (not a harsh halo).
                            Page-aware brand accent colours.
  apply_logo_watermark()  — Pillow: translucent brand logo in bottom-right.
                            Applied when logo/ dir contains a PNG.
  burn_text_on_video()    — ffmpeg drawtext + overlay: burns overlay_text
                            rock-solid while the Ken Burns background moves.

Design principles
-----------------
- Raw textless Gemini background is NEVER mutated.  New files are created.
- Background image stays BRIGHT and VIVID — only a 10 % global dark layer
  is applied so the image passes platform quality filters.
- No heavy bounding-box frames.  Text pops via soft drop-shadow + thin edge.
- Returns the original path unchanged on any failure so production never blocks.
- output_path defaults are alongside the source with a clear suffix so both
  the raw asset and the final composed asset exist on disk.
"""
from __future__ import annotations

import logging
import random
import subprocess
import sys
import unicodedata
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Visual constants
# ---------------------------------------------------------------------------
# Black overlay alpha for Layer 2 (applied over the raw background before text).
# 150/255 ≈ 59 % opacity — ensures the pencil-sketch illustration stays visible
# while the white text is guaranteed to read cleanly over it.
_CANVAS_DIM_ALPHA: int = 150         # RGBA: (0, 0, 0, 150)

_WATERMARK_ALPHA: float = 0.72       # logo opacity multiplier
_WATERMARK_MAX_W_RATIO: float = 0.22 # logo ≤ 22 % of image width
_WATERMARK_MARGIN_RATIO: float = 0.035

# Stroke / outline — 1 px thin, elegant, modern (not brutalist).
# A single-pixel dark contour makes white text crisp and readable
# over any background without adding visual weight.
_STROKE_WIDTH: int = 1               # strict 1 px thin outline

# ---------------------------------------------------------------------------
# Font pool — CLEAN GEOMETRIC / MODERN stack (ordered by priority)
# Montserrat and Poppins lead (installed via Google Fonts or Office 365).
# Trebuchet MS Bold and Arial Bold are reliable Windows system fonts.
# Heavy-weight fallbacks (Arial Black, Impact) only used as last resort.
# ---------------------------------------------------------------------------
_FONT_POOL: list[tuple[str, str]] = [
    # Geometric / high-end (top priority — clean, elegant, platform-native feel)
    ("Montserrat-Bold.ttf",        "Montserrat Bold"),
    ("Montserrat-SemiBold.ttf",    "Montserrat SemiBold"),
    ("Montserrat-ExtraBold.ttf",   "Montserrat ExtraBold"),
    ("Poppins-Bold.ttf",           "Poppins Bold"),
    ("Poppins-SemiBold.ttf",       "Poppins SemiBold"),
    # Reliable Windows system bold — always present
    ("trebucbd.ttf",               "Trebuchet MS Bold"),
    ("TREBUCBD.TTF",               "Trebuchet MS Bold"),
    ("arialbd.ttf",                "Arial Bold"),
    ("Arial Bold.ttf",             "Arial Bold"),
    ("Arial_Bold.ttf",             "Arial Bold"),
    # Cross-platform open-source
    ("Roboto-Bold.ttf",            "Roboto Bold"),
    ("DejaVuSans-Bold.ttf",        "DejaVu Sans Bold"),
    ("LiberationSans-Bold.ttf",    "Liberation Sans Bold"),
    ("NotoSans-Bold.ttf",          "Noto Sans Bold"),
    # Heavy weight — last resort only
    ("ariblk.ttf",                 "Arial Black"),
    ("ARIBLK.TTF",                 "Arial Black"),
]

# Fallback ordered list used when the random pool produces nothing
_FONT_FALLBACK: list[str] = [
    "trebucbd.ttf", "arialbd.ttf", "Arial Bold.ttf",
    "DejaVuSans-Bold.ttf", "ariblk.ttf", "LiberationSans-Bold.ttf",
]

# ---------------------------------------------------------------------------
# Page-aware brand colour palette
# Maps active page_id → accent colour candidates.
# Used for the 30 % chance text accent injection.
# ---------------------------------------------------------------------------
_PAGE_ACCENT_COLORS: dict[str, list[tuple[int, int, int]]] = {
    "wonder_feed": [
        (180, 210, 240),   # soft sky blue
        (230, 180, 195),   # rose gold blush
        (245, 230, 200),   # warm cream
        (200, 220, 245),   # pale cornflower
        (240, 200, 215),   # dusty rose
    ],
    "anna_protocol": [
        (255, 220, 100),   # warm gold
        (255, 235, 160),   # soft cream-gold
        (255, 200,  80),   # amber
        (240, 215, 140),   # parchment
        (255, 230, 130),   # honey
    ],
    "down_dirty": [
        (210, 255, 210),   # mint green
        (255, 240, 180),   # warm straw
        (200, 245, 230),   # sage
        (240, 210, 150),   # terra cotta cream
    ],
}
# Generic fallback palette when page_id not in map
_DEFAULT_ACCENT_COLORS: list[tuple[int, int, int]] = [
    (255, 220, 100),
    (255, 235, 160),
    (245, 230, 200),
    (220, 210, 245),
    (200, 235, 255),
]
_ACCENT_CHANCE: float = 0.30

# ---------------------------------------------------------------------------
# Typography auto-fit parameters
# ---------------------------------------------------------------------------
_TEXT_TARGET_WIDTH_RATIO: float = 0.72  # text wraps within centre 72 % of canvas
_AUTO_FIT_MAX_SIZE: int = 130
_AUTO_FIT_MIN_SIZE: int = 28
_AUTO_FIT_STEP: int = 4
_AUTO_FIT_MAX_LINES: int = 4


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def apply_text_overlay(
    bg_path: Path,
    overlay_text: str,
    *,
    logo_path: Path | None = None,
    output_path: Path | None = None,
    page_id: str | None = None,
    logo_size_scale: float = 0.18,
    logo_position: str = "bottom_right",
    font_path_override: str | None = None,
    font_size_scale: float = 0.08,
    font_color: tuple[int, int, int] = (255, 255, 255),
    text_outline_width: int | None = None,
    post_type: str = "",
) -> Path:
    """
    Composite centered cinematic typography onto a raw background.

    Exact 4-layer drawing order
    ---------------------------
    Layer 1  Raw background loaded as RGBA.
    Layer 2  Black rectangle composited over entire canvas:
               Image.new("RGBA", size, (0, 0, 0, 150)) → alpha_composite.
               Applied BEFORE any text so legibility is guaranteed.
    Layer 3  Text drawn exactly as-received from LLM (NO .title() casing fix).
               font_path_override is tried first; falls back to pool.
               Font size = int(canvas_width * font_size_scale), auto-fit downward.
               Pillow native stroke_width=1 / stroke_fill=(0,0,0) — no drop shadows.
    Layer 4  Logo pasted on top using logo_size_scale / logo_position.

    Parameters
    ----------
    bg_path           : Raw textless PNG from Gemini (never mutated).
    overlay_text      : Short engagement question / statement from LLM.
    logo_path         : Brand logo PNG with alpha channel.  Optional.
    output_path       : Explicit save path.  Default: bg_path.stem + '_final.png'.
    page_id           : Active page identifier for brand colour fallback.
    logo_size_scale   : Logo width as fraction of canvas width.
    logo_position     : 'top_left'|'top_right'|'bottom_left'|'bottom_right'|
                        'bottom_center'|'top_center'.
    font_path_override: Absolute path to .ttf font file (page config FONT_PATH).
    font_size_scale   : Font size as fraction of canvas width (e.g. 0.08 = 8 %).
    font_color        : RGB text colour (e.g. (255, 255, 255) for white).

    Returns
    -------
    Path of the composited image.  Returns bg_path unchanged on failure.
    """
    # LONG_CAPTION_IMAGE / ECONOMIC_REEL: illustration must stay 100% clean.
    # LONG_CAPTION_IMAGE — text belongs in the post caption only, not on the image.
    # ECONOMIC_REEL      — text will be rendered directly into video frames by
    #                      video_engine.compile_dynamic_reel(); baking it into the PNG
    #                      would cause a double-text artefact and block the Ken Burns
    #                      zoom from being applied correctly.
    if post_type in ("LONG_CAPTION_IMAGE", "ECONOMIC_REEL"):
        logger.info(
            "BrandComposer | %s detected — preserving pristine graphite background, "
            "applying logo only.",
            post_type,
        )
        if logo_path and logo_path.is_file():
            return apply_logo_watermark(bg_path, logo_path, output_path=output_path)
        return Path(bg_path)

    if not overlay_text.strip():
        if logo_path and logo_path.is_file():
            return apply_logo_watermark(bg_path, logo_path, output_path=output_path)
        return bg_path

    try:
        from PIL import Image, ImageDraw  # type: ignore

        bg_path = Path(bg_path)
        if output_path is None:
            output_path = bg_path.parent / (bg_path.stem + "_final.png")
        output_path = Path(output_path)

        # --- Sanitise text (strip rendering-breaking characters) ----------
        # IMPORTANT: do NOT apply .title() — keeps LLM casing intact.
        # .title() causes artefacts like "What'S" on contractions.
        clean_text = _sanitize_overlay_text(overlay_text)
        if not clean_text:
            clean_text = overlay_text.strip()
        # Use text exactly as the LLM produced it
        display_text = clean_text

        # ================================================================
        # LAYER 1: Load base image, force RGBA
        # ================================================================
        base_image = Image.open(bg_path).convert("RGBA")
        w, h = base_image.size

        # ================================================================
        # LAYER 2: Black overlay rectangle — alpha_composite BEFORE text
        # (0, 0, 0, 150) ≈ 59 % opacity — illustration stays visible,
        # white text reads perfectly over any image area.
        # ================================================================
        overlay = Image.new("RGBA", base_image.size, (0, 0, 0, _CANVAS_DIM_ALPHA))
        composited_image = Image.alpha_composite(base_image, overlay)

        # ================================================================
        # LAYER 3: Text with 1 px native Pillow stroke — NO drop shadows
        # Font loaded from page config path; auto-fit scales down if needed.
        # ================================================================
        # Scale-based starting font size from page config (clamped to valid range)
        scale_size = max(_AUTO_FIT_MIN_SIZE, min(int(w * font_size_scale), _AUTO_FIT_MAX_SIZE))

        _probe_draw = ImageDraw.Draw(composited_image)
        font_size, font, lines, _ = _auto_fit_font(
            _probe_draw, display_text, w,
            font_path=font_path_override,
            start_size=scale_size,
        )

        # Tight leading — unified punchy text block
        line_h  = font_size + max(4, int(font_size * 0.14))
        total_h = line_h * len(lines)
        text_y0 = (h - total_h) // 2

        # text_outline_width from page config (0 = no outline); falls back to _STROKE_WIDTH
        stroke_w = text_outline_width if text_outline_width is not None else _STROKE_WIDTH

        text_draw = ImageDraw.Draw(composited_image)
        y = text_y0
        for line in lines:
            line_w = _measure_text_width(text_draw, line, font, font_size)
            x = (w - line_w) // 2
            # Pillow native stroke — no drop shadows
            text_draw.text(
                (x, y), line,
                font=font,
                fill=font_color,
                stroke_width=stroke_w,
                stroke_fill=(0, 0, 0) if stroke_w > 0 else None,
            )
            y += line_h

        # ================================================================
        # LAYER 4: Logo pasted on top using page config size / position
        # ================================================================
        final_rgba = composited_image
        if logo_path and logo_path.is_file():
            final_rgba = _composite_logo(
                composited_image, logo_path,
                position=logo_position,
                size_scale=logo_size_scale,
            )

        final_rgba.convert("RGB").save(str(output_path), "PNG")

        try:
            import config as _cfg
            _pid = page_id or _cfg.ACTIVE_PAGE
        except Exception:
            _pid = page_id or ""
        logger.info(
            "BrandComposer [4-layer] → %s | page=%s | font_size=%d | logo_pos=%s | logo_scale=%.2f",
            output_path.name, _pid, font_size, logo_position, logo_size_scale,
        )
        return output_path

    except ImportError:
        logger.warning("Pillow not installed — text overlay skipped (pip install Pillow).")
        return bg_path
    except Exception as exc:  # noqa: BLE001
        logger.warning("Text overlay failed (%s) — using raw background.", exc, exc_info=True)
        return bg_path


def apply_logo_watermark(
    img_path: Path,
    logo_path: Path,
    *,
    output_path: Path | None = None,
    position: str = "bottom_right",
    size_scale: float | None = None,
) -> Path:
    """
    Apply a translucent brand logo watermark onto an existing image.

    Parameters
    ----------
    img_path   : Source image (any PIL-supported format).
    logo_path  : Brand PNG with alpha channel for transparency.
    output_path: Explicit path.  Default: img_path stem + '_wm.png'.
    position   : 'top_left' | 'top_right' | 'bottom_left' | 'bottom_right'.
    size_scale : Logo width as fraction of canvas width.  None = use default.

    Returns
    -------
    Path of the watermarked image.  Returns img_path unchanged on failure.
    """
    if not logo_path.is_file():
        return img_path

    try:
        from PIL import Image  # type: ignore

        img_path = Path(img_path)
        if output_path is None:
            output_path = img_path.parent / (img_path.stem + "_wm.png")
        output_path = Path(output_path)

        base = Image.open(img_path).convert("RGBA")
        result = _composite_logo(base, logo_path, position=position, size_scale=size_scale)
        result.convert("RGB").save(str(output_path), "PNG")
        logger.info(
            "BrandComposer | logo watermark → %s | pos=%s", output_path.name, position
        )
        return output_path

    except ImportError:
        logger.warning("Pillow not installed — logo watermark skipped.")
        return img_path
    except Exception as exc:  # noqa: BLE001
        logger.warning("Logo watermark failed (%s) — returning original.", exc, exc_info=True)
        return img_path


def burn_text_on_video(
    video_path: Path,
    overlay_text: str,
    *,
    logo_path: Path | None = None,
    output_path: Path | None = None,
) -> Path:
    """
    Burn overlay_text rock-solid onto a video using ffmpeg drawtext.

    The text stays completely static while the Ken Burns background animates
    underneath it.  Optionally composites a brand logo via overlay filter.

    Requires ffmpeg on PATH.  Falls back to the original video_path on any
    failure so the pipeline never blocks.

    Parameters
    ----------
    video_path   : Source MP4 (Ken Burns output).
    overlay_text : Text to burn in.  Empty string skips drawtext.
    logo_path    : Optional brand PNG for video watermark.
    output_path  : Explicit path.  Default: video_path stem + '_final.mp4'.

    Returns
    -------
    Path to the final video.  Returns video_path unchanged on failure.
    """
    video_path = Path(video_path)
    if output_path is None:
        output_path = video_path.parent / (video_path.stem + "_final.mp4")
    output_path = Path(output_path)

    has_text = bool(overlay_text.strip())
    has_logo = bool(logo_path and logo_path.is_file())
    if not has_text and not has_logo:
        return video_path

    if not _ffmpeg_available():
        logger.warning("BrandComposer | ffmpeg not found — video text burn skipped.")
        return video_path

    try:
        vf_chain, extra_inputs = _build_video_filter(overlay_text, logo_path)

        cmd: list[str] = ["ffmpeg", "-y", "-i", str(video_path)]
        cmd.extend(extra_inputs)
        cmd += [
            "-vf", vf_chain,
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-preset", "fast",
            "-crf", "20",
            str(output_path),
        ]

        result = subprocess.run(cmd, capture_output=True, timeout=180)
        if result.returncode != 0:
            stderr = result.stderr.decode("utf-8", errors="replace")[:600]
            raise RuntimeError(f"ffmpeg exited {result.returncode}: {stderr}")

        logger.info("BrandComposer | video text burned → %s", output_path.name)
        return output_path

    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Video text burn failed (%s) — returning original video.", exc, exc_info=True,
        )
        return video_path


# ---------------------------------------------------------------------------
# Public API — TEXT_QUOTE background generator
# ---------------------------------------------------------------------------

def generate_text_quote_background(
    size: tuple[int, int] = (1080, 1350),
    *,
    page_id: str | None = None,
    output_path: Path | None = None,
) -> Path:
    """
    Generate a clean, modern minimalist solid-color background for TEXT_QUOTE posts.

    No Gemini API call — the background is created entirely in Pillow using the
    page's brand identity palette.  The text layer is applied on top by the
    standard ``apply_text_overlay()`` call in the compositing phase.

    Parameters
    ----------
    size        : (width, height) in pixels.  Default 1080×1350 (4:5 portrait).
    page_id     : Active page slug for brand colour selection.
    output_path : Where to save the PNG.  If None, a temp path is auto-generated.

    Returns
    -------
    Path to the saved background PNG.
    """
    # Page-brand gradient palettes (dark base → slightly lighter top edge)
    _BRAND_BG: dict[str, tuple[tuple[int, int, int], tuple[int, int, int]]] = {
        "wonder_feed":    ((10, 18, 35),   (22, 38, 62)),   # deep navy
        "anna_protocol":  ((14, 10, 6),    (30, 22, 12)),   # dark earth-gold
        "down_dirty":     ((8, 18, 6),     (18, 34, 12)),   # deep forest
    }
    try:
        import config as _cfg
        _pid = page_id or _cfg.ACTIVE_PAGE
    except Exception:  # noqa: BLE001
        _pid = page_id or ""

    top_color, bottom_color = _BRAND_BG.get(_pid, ((10, 10, 10), (28, 28, 28)))

    try:
        from PIL import Image  # type: ignore

        w, h = size
        bg = Image.new("RGB", (w, h), top_color)

        # Vertical gradient: blend from top_color → bottom_color
        for y in range(h):
            t = y / max(h - 1, 1)
            r = int(top_color[0] + (bottom_color[0] - top_color[0]) * t)
            g = int(top_color[1] + (bottom_color[1] - top_color[1]) * t)
            b = int(top_color[2] + (bottom_color[2] - top_color[2]) * t)
            for x in range(w):
                bg.putpixel((x, y), (r, g, b))

    except ImportError:
        logger.warning("Pillow not installed — TEXT_QUOTE background skipped.")
        raise

    if output_path is None:
        from datetime import datetime, timezone
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%SZ")
        output_path = Path(f"text_quote_bg_{ts}.png")

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    bg.save(str(output_path), "PNG")
    logger.info("TEXT_QUOTE background saved: %s | page=%s", output_path.name, _pid)
    return output_path.resolve()


# ---------------------------------------------------------------------------
# Private helpers — Pillow
# ---------------------------------------------------------------------------

def _render_line_with_stroke(
    draw: Any,
    xy: tuple[int, int],
    text: str,
    font: Any,
    fill: tuple[int, int, int],
) -> None:
    """
    Draw a single text line with a crisp dark outline stroke.

    Uses Pillow's built-in ``stroke_width`` / ``stroke_fill`` where available
    (Pillow ≥ 6.2.0).  Falls back to a manual 8-direction offset render for
    older Pillow builds.

    Parameters
    ----------
    draw  : ``ImageDraw.Draw`` instance.
    xy    : (x, y) top-left anchor of the text baseline.
    text  : The string to render (already sanitised).
    font  : ``ImageFont`` object.
    fill  : RGB colour tuple for the main text.
    """
    x, y = xy
    stroke_color = (0, 0, 0)

    # Try native Pillow stroke support first (clean, sub-pixel accurate)
    try:
        draw.text(
            (x, y), text, font=font, fill=fill,
            stroke_width=_STROKE_WIDTH,
            stroke_fill=stroke_color,
        )
        return
    except TypeError:
        pass  # older Pillow — fall through to manual approach

    # Manual 8-direction stroke for Pillow < 6.2.0
    for dx, dy in (
        (-_STROKE_WIDTH, 0), (_STROKE_WIDTH, 0),
        (0, -_STROKE_WIDTH), (0, _STROKE_WIDTH),
        (-_STROKE_WIDTH, -_STROKE_WIDTH), (_STROKE_WIDTH, -_STROKE_WIDTH),
        (-_STROKE_WIDTH, _STROKE_WIDTH),  (_STROKE_WIDTH, _STROKE_WIDTH),
    ):
        draw.text((x + dx, y + dy), text, font=font, fill=stroke_color)
    draw.text((x, y), text, font=font, fill=fill)


def _sanitize_overlay_text(text: str) -> str:
    """
    Strip characters that render as empty boxes (□) or invisible glyphs
    when PIL draws text with a standard bold sans-serif font (Arial Black,
    Impact, etc.).

    PIL does NOT perform font-fallback the way a browser or OS renderer does.
    Any character absent from the chosen font file is rendered as a hollow
    rectangle □.  This function aggressively removes all such characters while
    preserving the readable text content:

    Removed categories / ranges
    ---------------------------
    * Unicode control / format / surrogate / private-use characters (Cc, Cs, Co, Cf)
    * Zero-width / directional markers by explicit codepoint
    * Variation selectors   U+FE00–U+FE0F  (emoji presentation modifiers)
    * Miscellaneous Symbols U+2600–U+26FF  (☀ ☁ ♥ etc. — unsupported in Arial)
    * Dingbats              U+2700–U+27BF  (✂ ✈ etc.)
    * Box-drawing           U+2500–U+257F
    * Block elements        U+2580–U+259F
    * Geometric shapes      U+25A0–U+25FF  (□ ■ ▪ etc. — the exact culprit)
    * Supplementary Multilingual Plane U+10000+
      (all emoji in this range: 😅😭🔥❤️ etc. — no glyph in Arial Black on Windows)
    * Supplemental Symbols  U+1F000–U+1FFFF  (explicit belt-and-suspenders)
    * Regional indicators   U+1F1E0–U+1F1FF  (flag sequences)

    Preserved
    ---------
    ASCII printable, Latin Extended (A/B), common punctuation, smart quotes,
    ellipsis, em-dash, and standard whitespace.
    """
    _INVISIBLE_CP = frozenset({
        0x00AD,  # soft hyphen
        0x200B,  # zero-width space
        0x200C,  # zero-width non-joiner
        0x200D,  # zero-width joiner
        0x200E,  # left-to-right mark
        0x200F,  # right-to-left mark
        0x2028,  # line separator
        0x2029,  # paragraph separator
        0xFEFF,  # BOM / zero-width no-break space
        0x2060,  # word joiner
        0x2061,  # function application
        0x2062,  # invisible times
        0x2063,  # invisible separator
        0x2064,  # invisible plus
        0xFFF9,  # interlinear annotation anchor
        0xFFFA,  # interlinear annotation separator
        0xFFFB,  # interlinear annotation terminator
    })

    def _should_drop(cp: int) -> bool:
        # Explicit codepoints
        if cp in _INVISIBLE_CP:
            return True
        # Control characters (ASCII and C1)
        if cp < 0x20 and cp not in (0x09, 0x0A, 0x0D):
            return True
        if 0x7F <= cp <= 0x9F:
            return True
        # Surrogates
        if 0xD800 <= cp <= 0xDFFF:
            return True
        # Private Use Area (BMP)
        if 0xE000 <= cp <= 0xF8FF:
            return True
        # Specials block
        if 0xFFF0 <= cp <= 0xFFFF:
            return True
        # Box-drawing characters
        if 0x2500 <= cp <= 0x257F:
            return True
        # Block elements
        if 0x2580 <= cp <= 0x259F:
            return True
        # Geometric shapes (□ ■ are here — the primary culprit)
        if 0x25A0 <= cp <= 0x25FF:
            return True
        # Miscellaneous symbols (☀ ☁ ♥ etc.) — not in Arial Black
        if 0x2600 <= cp <= 0x26FF:
            return True
        # Dingbats (✂ ✈ ✔ etc.) — not in Arial Black
        if 0x2700 <= cp <= 0x27BF:
            return True
        # Variation selectors (emoji presentation modifiers, invisible)
        if 0xFE00 <= cp <= 0xFE0F:
            return True
        # Entire Supplementary Multilingual Plane and above (emoji: 😅 🔥 💀 etc.)
        # PIL draws every one of these as □ when using Arial Black on Windows.
        if cp >= 0x10000:
            return True
        return False

    result: list[str] = []
    emoji_stripped = 0
    for ch in text:
        cp = ord(ch)
        if _should_drop(cp):
            if cp >= 0x2600:   # approximate: anything in the symbol/emoji ranges
                emoji_stripped += 1
            continue
        # Belt-and-suspenders: drop by Unicode general category
        try:
            cat = unicodedata.category(ch)
            if cat in ("Cc", "Cs", "Co", "Cf"):
                continue
        except Exception:  # noqa: BLE001
            pass
        result.append(ch)

    if emoji_stripped:
        logger.debug(
            "_sanitize_overlay_text: stripped %d emoji/symbol char(s) from PIL render path "
            "(emoji are preserved in caption/JSON outputs — only the image text layer is affected).",
            emoji_stripped,
        )

    cleaned = "".join(result).strip()
    # Collapse any runs of whitespace left by stripped sequences
    import re as _re
    cleaned = _re.sub(r"[ \t]{2,}", " ", cleaned)
    # Strip trailing punctuation-only noise (e.g. lone "?" after emoji removal)
    cleaned = cleaned.rstrip(" .,;:-")

    # Safety net: if everything was stripped (extreme edge-case — hook was
    # entirely emoji), fall back to ASCII-only version of the original.
    if not cleaned and text.strip():
        cleaned = "".join(ch for ch in text if 0x20 <= ord(ch) <= 0x7E).strip()
        logger.warning(
            "_sanitize_overlay_text: full strip left empty string; "
            "falling back to ASCII-only extraction: %r", cleaned
        )

    return cleaned


def _get_font_dirs() -> list[Path]:
    """Return ordered list of directories to search for font files."""
    import os
    dirs: list[Path] = []
    if sys.platform == "win32":
        dirs += [
            Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts",
            Path.home() / "AppData/Local/Microsoft/Windows/Fonts",
        ]
    dirs += [
        Path("/usr/share/fonts/truetype/dejavu"),
        Path("/usr/share/fonts/truetype"),
        Path("/usr/share/fonts"),
        Path("/Library/Fonts"),
        Path.home() / "Library/Fonts",
    ]
    try:
        import PIL
        dirs.append(Path(PIL.__file__).parent)
    except Exception:
        pass
    return dirs


def _pick_random_font_path() -> str | None:
    """
    Randomly select an available TrueType font from _FONT_POOL.
    Returns the absolute path string or None if nothing is found.
    """
    dirs = _get_font_dirs()
    pool = list(_FONT_POOL)
    random.shuffle(pool)
    for fname, _label in pool:
        for d in dirs:
            p = d / fname
            if p.is_file():
                return str(p)
    return None


def _load_font(size: int, font_path: str | None = None) -> Any:
    """Return an ImageFont for the given pixel size.

    If ``font_path`` is a valid absolute path it is tried first; falls back
    to the ordered ``_FONT_FALLBACK`` list, then Pillow's built-in default.
    """
    try:
        from PIL import ImageFont  # type: ignore

        if font_path:
            try:
                return ImageFont.truetype(font_path, size)
            except Exception:
                pass

        for fname in _FONT_FALLBACK:
            for d in _get_font_dirs():
                p = d / fname
                if p.is_file():
                    try:
                        return ImageFont.truetype(str(p), size)
                    except Exception:
                        continue

        try:
            return ImageFont.load_default(size=size)
        except TypeError:
            return ImageFont.load_default()

    except Exception:
        return None


def _measure_text_width(draw: Any, text: str, font: Any, font_size: int) -> int:
    """Return the pixel width of text, with a rough fallback."""
    try:
        bbox = draw.textbbox((0, 0), text, font=font)
        return bbox[2] - bbox[0]
    except Exception:
        return len(text) * max(1, font_size // 2)


def _auto_fit_font(
    draw: Any,
    text: str,
    image_width: int,
    *,
    font_path: str | None = None,
    start_size: int | None = None,
) -> tuple[int, Any, list[str], int]:
    """
    Return (font_size, font, wrapped_lines, max_text_width) such that the text
    fits within the centre 72 % of the canvas in at most _AUTO_FIT_MAX_LINES lines.

    Starts from ``start_size`` (or ``_AUTO_FIT_MAX_SIZE`` if not given) and
    steps down by ``_AUTO_FIT_STEP`` until the wrapping fits.  Passing
    ``start_size = int(canvas_width * font_size_scale)`` makes the font scale
    match the page config's FONT_SIZE_SCALE rather than a global constant.
    """
    max_start = start_size if (start_size is not None) else _AUTO_FIT_MAX_SIZE
    # Clamp to valid range
    max_start = max(_AUTO_FIT_MIN_SIZE, min(max_start, 200))
    max_text_w = int(image_width * _TEXT_TARGET_WIDTH_RATIO)
    for size in range(max_start, _AUTO_FIT_MIN_SIZE - 1, -_AUTO_FIT_STEP):
        font = _load_font(size, font_path=font_path)
        lines = _wrap_text(draw, text, font, max_text_w)
        if len(lines) <= _AUTO_FIT_MAX_LINES:
            return size, font, lines, max_text_w
    font = _load_font(_AUTO_FIT_MIN_SIZE, font_path=font_path)
    lines = _wrap_text(draw, text, font, max_text_w)
    return _AUTO_FIT_MIN_SIZE, font, lines, max_text_w


def _wrap_text(draw: Any, text: str, font: Any, max_width: int) -> list[str]:
    """Word-wrap text to fit within max_width pixels, preserving newlines."""
    raw_lines = text.splitlines() if "\n" in text else [text]
    result: list[str] = []
    for raw_line in raw_lines:
        words = raw_line.split()
        current = ""
        for word in words:
            candidate = (current + " " + word).strip()
            try:
                from PIL import ImageDraw  # type: ignore  # noqa: F401
                bbox = draw.textbbox((0, 0), candidate, font=font)
                w = bbox[2] - bbox[0]
            except Exception:
                w = len(candidate) * 20
            if w <= max_width:
                current = candidate
            else:
                if current:
                    result.append(current)
                current = word
        if current:
            result.append(current)
    return result or [text]


def _composite_logo(
    base: Any,
    logo_path: Path,
    *,
    position: str = "bottom_right",
    size_scale: float | None = None,
) -> Any:
    """
    Scale logo, adjust opacity, and paste onto base at the specified corner.

    Parameters
    ----------
    base       : RGBA or RGB PIL Image to composite onto.
    logo_path  : Path to brand PNG with alpha channel.
    position   : One of 'top_left', 'top_right', 'bottom_left', 'bottom_right'.
                 Legacy aliases 'bottom_center' and 'center' also accepted.
    size_scale : Logo width as fraction of canvas width (e.g. 0.15 = 15 %).
                 Overrides _WATERMARK_MAX_W_RATIO when supplied.

    Returns
    -------
    RGBA copy of base with logo pasted in place.
    """
    from PIL import Image  # type: ignore

    base_rgba = base.convert("RGBA")
    w, h = base_rgba.size

    logo = Image.open(logo_path).convert("RGBA")

    # --- Scale to target width (size_scale takes priority over global constant) ---
    target_scale = size_scale if size_scale is not None else _WATERMARK_MAX_W_RATIO
    target_w = max(20, int(w * target_scale))
    if logo.width != target_w:
        ratio  = target_w / logo.width
        target_h = max(1, int(logo.height * ratio))
        logo = logo.resize((target_w, target_h), Image.LANCZOS)

    # --- Modulate alpha channel for desired opacity ---
    r, g, b, a = logo.split()
    a = a.point(lambda px: int(px * _WATERMARK_ALPHA))
    logo = Image.merge("RGBA", (r, g, b, a))

    margin_x = int(w * _WATERMARK_MARGIN_RATIO)
    margin_y = int(h * _WATERMARK_MARGIN_RATIO)
    lw, lh = logo.size

    # All six positions (four corners + two centered options)
    _positions: dict[str, tuple[int, int]] = {
        "top_left":      (margin_x,             margin_y),
        "top_right":     (w - lw - margin_x,    margin_y),
        "top_center":    ((w - lw) // 2,         margin_y),
        "bottom_left":   (margin_x,              h - lh - margin_y),
        "bottom_right":  (w - lw - margin_x,    h - lh - margin_y),
        "bottom_center": ((w - lw) // 2,         h - lh - margin_y),
        # Legacy alias
        "center":        ((w - lw) // 2,         (h - lh) // 2),
    }
    x, y = _positions.get(position.lower().strip(), _positions["bottom_right"])

    out = base_rgba.copy()
    out.paste(logo, (x, y), logo)
    logger.debug(
        "_composite_logo | position=%s | size=%dx%d | scale=%.2f | canvas=%dx%d",
        position, lw, lh, target_scale, w, h,
    )
    return out


# ---------------------------------------------------------------------------
# Private helpers — ffmpeg
# ---------------------------------------------------------------------------

def _ffmpeg_available() -> bool:
    try:
        probe = subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=5)
        return probe.returncode == 0
    except Exception:
        return False


def _escape_ffmpeg_text(text: str) -> str:
    """Escape special characters for ffmpeg drawtext filter value."""
    return (
        text
        .replace("\\", "\\\\")
        .replace("'",  "\\'")
        .replace(":",  "\\:")
        .replace("\n", " ")
    )


def _get_ffmpeg_font() -> str | None:
    """Return a usable TrueType font path for ffmpeg, or None."""
    import os

    dirs: list[Path] = []
    if sys.platform == "win32":
        dirs.append(Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts")
    dirs += [
        Path("/usr/share/fonts/truetype/dejavu"),
        Path("/usr/share/fonts/truetype"),
    ]
    for fname in ["arialbd.ttf", "Arial Bold.ttf", "DejaVuSans-Bold.ttf"]:
        for d in dirs:
            p = d / fname
            if p.is_file():
                # ffmpeg font paths: forward slashes, colons escaped on Windows
                return str(p).replace("\\", "/").replace(":", "\\:")
    return None


def _build_video_filter(
    overlay_text: str,
    logo_path: Path | None,
) -> tuple[str, list[str]]:
    """
    Build the ffmpeg -vf filter chain string and any extra -i input arguments.

    Returns (filter_chain_string, extra_input_args).
    When logo is present the logo PNG must be passed as a second -i input,
    which is reflected in extra_input_args = ['-i', str(logo_path)].
    """
    has_text = bool(overlay_text.strip())
    has_logo = bool(logo_path and logo_path.is_file())

    # -----------------------------------------------------------------
    # Case 1: text only
    # -----------------------------------------------------------------
    if has_text and not has_logo:
        font_arg = _get_ffmpeg_font()
        font_clause = f":fontfile='{font_arg}'" if font_arg else ""
        safe = _escape_ffmpeg_text(overlay_text.strip())
        vf = (
            f"drawtext=text='{safe}'{font_clause}"
            f":fontcolor=white:fontsize=64"
            f":x=(w-text_w)/2:y=(h-text_h)/2-50"
            f":box=1:boxcolor=black@0.55:boxborderw=20"
            f":shadowx=3:shadowy=3:shadowcolor=black@0.8"
        )
        return vf, []

    # -----------------------------------------------------------------
    # Case 2: logo only
    # -----------------------------------------------------------------
    if not has_text and has_logo:
        vf = (
            "[1:v]scale=iw*0.22:-1,format=rgba,"
            "colorchannelmixer=aa=0.72[logo];"
            "[0:v][logo]overlay=main_w-overlay_w-40:main_h-overlay_h-40"
        )
        return vf, ["-i", str(logo_path)]

    # -----------------------------------------------------------------
    # Case 3: text + logo
    # -----------------------------------------------------------------
    font_arg = _get_ffmpeg_font()
    font_clause = f":fontfile='{font_arg}'" if font_arg else ""
    safe = _escape_ffmpeg_text(overlay_text.strip())
    drawtext = (
        f"drawtext=text='{safe}'{font_clause}"
        f":fontcolor=white:fontsize=64"
        f":x=(w-text_w)/2:y=(h-text_h)/2-50"
        f":box=1:boxcolor=black@0.55:boxborderw=20"
        f":shadowx=3:shadowy=3:shadowcolor=black@0.8"
    )
    vf = (
        f"[0:v]{drawtext}[txt];"
        "[1:v]scale=iw*0.22:-1,format=rgba,"
        "colorchannelmixer=aa=0.72[logo];"
        "[txt][logo]overlay=main_w-overlay_w-40:main_h-overlay_h-40"
    )
    return vf, ["-i", str(logo_path)]

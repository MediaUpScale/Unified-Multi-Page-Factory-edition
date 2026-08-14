# -*- coding: utf-8 -*-
"""
LOFI caption typography — selectable styles.

DEFAULT: ``rounded_hand`` — Comic Sans MS Bold (bold rounded handwritten).
Identified from the double-caption comparison plate where this face sat above
Lora Italic; confirmed installed as ``Fonts/ComicSans/ComicSansMS-Bold.ttf``
(and Windows ``comicbd.ttf``).

Secondary: ``lora_italic`` — thin italic serif (Lora), kept selectable.
"""
from __future__ import annotations

from pathlib import Path
from typing import Sequence

from PIL import Image as PILImage
from PIL import ImageDraw, ImageFilter, ImageFont

from core_engine.economic_reel_lofi import config as lofi_cfg

# ── Shared layout (do not share ECONOMIC_REEL yellow outline style) ──────────
CAPTION_COLOR: tuple[int, int, int, int] = (255, 255, 255, 255)
SHADOW_COLOR: tuple[int, int, int, int] = (0, 0, 0, 102)  # ≈ rgba(0,0,0,0.40)
SHADOW_OFFSET_PX: tuple[int, int] = (2, 2)
SHADOW_BLUR_RADIUS: float = 6.0
MAX_WIDTH_FRAC: float = 0.80
# Upper-middle third (~35–45% from top); avoid top 15% safe zone
CAPTION_CENTER_Y_FRAC: float = 0.40
LINE_HEIGHT_FRAC: float = 0.052  # ~5.2% of frame height per line
LINE_GAP_FRAC: float = 0.028
LETTER_SPACING_PX: float = 0.0

# DEFAULT — bold rounded handwritten (Comic Sans MS Bold)
ROUNDED_HAND_FONT_CANDIDATES: tuple[str, ...] = (
    "Fonts/ComicSans/ComicSansMS-Bold.ttf",
    "Fonts/ComicSans/comicbd.ttf",
    r"C:\Windows\Fonts\comicbd.ttf",
    # Soft fallbacks if Comic Sans unavailable
    "Fonts/Poppins/Poppins-Bold.ttf",
    "Fonts/Montserrat/static/Montserrat-Bold.ttf",
)

# Secondary — thin italic serif
LORA_ITALIC_FONT_CANDIDATES: tuple[str, ...] = (
    "Fonts/Lora/Lora-Italic.ttf",
    "Fonts/Lora/EBGaramond-Italic.ttf",
    "Fonts/Lora/CormorantGaramond-Italic.ttf",
)

STYLE_FONT_MAP: dict[str, tuple[str, ...]] = {
    "rounded_hand": ROUNDED_HAND_FONT_CANDIDATES,
    "lora_italic": LORA_ITALIC_FONT_CANDIDATES,
}

STYLE_DISPLAY_NAME: dict[str, str] = {
    "rounded_hand": "Comic Sans MS Bold",
    "lora_italic": "Lora Italic",
}

# Watermark handle (same family as caption style, smaller, low opacity)
WATERMARK_COLOR: tuple[int, int, int, int] = (255, 255, 255, 140)  # ~0.55 opacity
WATERMARK_SHADOW_COLOR: tuple[int, int, int, int] = (0, 0, 0, 70)
WATERMARK_SIZE_FRAC: float = 0.028  # of frame height
WATERMARK_BOTTOM_MARGIN_FRAC: float = 0.045


def normalize_caption_style(style: str | None) -> str:
    key = (style or lofi_cfg.DEFAULT_CAPTION_STYLE).strip().lower()
    if key not in STYLE_FONT_MAP:
        raise ValueError(
            f"caption style must be one of {sorted(STYLE_FONT_MAP)} (got {style!r})"
        )
    return key


def resolve_lofi_caption_font(
    engine_root: Path,
    *,
    size: int | None = None,
    style: str | None = None,
) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Resolve caption font for the requested style (default: rounded_hand)."""
    style_key = normalize_caption_style(style)
    if size is None:
        size = max(28, int(lofi_cfg.REEL_HEIGHT * LINE_HEIGHT_FRAC))
    candidates = STYLE_FONT_MAP[style_key]
    for rel in candidates:
        path = Path(rel)
        if not path.is_absolute():
            path = engine_root / rel
        if path.is_file():
            try:
                return ImageFont.truetype(str(path), size=size)
            except Exception:  # noqa: BLE001
                continue
    raise FileNotFoundError(
        f"LOFI caption style {style_key!r} requires one of: " + ", ".join(candidates)
    )


def wrap_lofi_caption(
    text: str,
    font: ImageFont.ImageFont,
    max_width: int,
    draw: ImageDraw.ImageDraw,
    *,
    max_lines: int = 2,
) -> list[str]:
    words = (text or "").split()
    if not words:
        return []
    lines: list[str] = []
    cur = words[0]
    for i, w in enumerate(words[1:], start=1):
        trial = f"{cur} {w}"
        bbox = draw.textbbox((0, 0), trial, font=font)
        if (bbox[2] - bbox[0]) <= max_width:
            cur = trial
            continue
        lines.append(cur)
        cur = w
        if len(lines) == max_lines - 1:
            # Last allowed line absorbs the remainder (never drop words).
            rest = " ".join(words[i:])
            lines.append(rest)
            return lines[:max_lines]
    lines.append(cur)
    return lines[:max_lines]


def _draw_soft_text(
    canvas: PILImage.Image,
    *,
    lines_metrics: list[tuple[str, int, int]],
    font: ImageFont.ImageFont,
    y0: int,
    line_gap: int,
    width: int,
    fill: tuple[int, int, int, int],
    shadow_fill: tuple[int, int, int, int],
    shadow_offset: tuple[int, int] = SHADOW_OFFSET_PX,
    shadow_blur: float = SHADOW_BLUR_RADIUS,
) -> PILImage.Image:
    shadow = PILImage.new("RGBA", canvas.size, (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow)
    ox, oy = shadow_offset
    y_s = y0
    for ln, lw, lh in lines_metrics:
        x = (width - lw) // 2
        shadow_draw.text((x + ox, y_s + oy), ln, font=font, fill=shadow_fill)
        y_s += lh + line_gap
    shadow = shadow.filter(ImageFilter.GaussianBlur(radius=shadow_blur))
    out = PILImage.alpha_composite(canvas, shadow)

    draw = ImageDraw.Draw(out)
    y_t = y0
    for ln, lw, lh in lines_metrics:
        x = (width - lw) // 2
        draw.text((x, y_t), ln, font=font, fill=fill)
        y_t += lh + line_gap
    return out


def render_lofi_caption_layer(
    text: str,
    *,
    engine_root: Path,
    width: int = lofi_cfg.REEL_WIDTH,
    height: int = lofi_cfg.REEL_HEIGHT,
    style: str | None = None,
) -> PILImage.Image:
    """
    White caption + soft blurred drop shadow, center-aligned upper-middle (~40%).

    Default style is ``rounded_hand`` (Comic Sans MS Bold).
    Pass ``style='lora_italic'`` for the secondary thin italic serif.
    """
    style_key = normalize_caption_style(style)
    canvas = PILImage.new("RGBA", (width, height), (0, 0, 0, 0))
    probe = ImageDraw.Draw(canvas)
    max_w = int(width * MAX_WIDTH_FRAC)
    size = max(28, int(height * LINE_HEIGHT_FRAC))
    font = resolve_lofi_caption_font(engine_root, size=size, style=style_key)
    lines = wrap_lofi_caption(text, font, max_w, probe)
    for _ in range(8):
        if not lines:
            break
        overflow = False
        for ln in lines:
            bbox = probe.textbbox((0, 0), ln, font=font)
            if (bbox[2] - bbox[0]) > max_w:
                overflow = True
                break
        if not overflow:
            break
        size = max(22, int(size * 0.92))
        font = resolve_lofi_caption_font(engine_root, size=size, style=style_key)
        lines = wrap_lofi_caption(text, font, max_w, probe)
    if not lines:
        return canvas

    line_gap = int(height * LINE_GAP_FRAC)
    metrics: list[tuple[str, int, int]] = []
    for ln in lines:
        bbox = probe.textbbox((0, 0), ln, font=font)
        lw = bbox[2] - bbox[0]
        lh = bbox[3] - bbox[1]
        metrics.append((ln, lw, lh))

    total_h = sum(m[2] for m in metrics) + line_gap * (len(metrics) - 1)
    y_center = int(height * CAPTION_CENTER_Y_FRAC)
    y = max(int(height * 0.16), y_center - total_h // 2)

    return _draw_soft_text(
        canvas,
        lines_metrics=metrics,
        font=font,
        y0=y,
        line_gap=line_gap,
        width=width,
        fill=CAPTION_COLOR,
        shadow_fill=SHADOW_COLOR,
    )


def render_lofi_watermark_layer(
    handle: str,
    *,
    engine_root: Path,
    width: int = lofi_cfg.REEL_WIDTH,
    height: int = lofi_cfg.REEL_HEIGHT,
    opacity: float = 0.55,
    style: str | None = None,
) -> PILImage.Image:
    """Channel handle in the active caption family — smaller, low-opacity white."""
    style_key = normalize_caption_style(style)
    canvas = PILImage.new("RGBA", (width, height), (0, 0, 0, 0))
    text = (handle or "").strip()
    if not text:
        return canvas

    size = max(18, int(height * WATERMARK_SIZE_FRAC))
    font = resolve_lofi_caption_font(engine_root, size=size, style=style_key)
    draw = ImageDraw.Draw(canvas)
    bbox = draw.textbbox((0, 0), text, font=font)
    lw, lh = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x = (width - lw) // 2
    y = height - lh - int(height * WATERMARK_BOTTOM_MARGIN_FRAC)

    alpha = int(255 * max(0.15, min(1.0, opacity)))
    fill = (255, 255, 255, alpha)
    shadow_fill = (0, 0, 0, max(40, alpha // 2))

    return _draw_soft_text(
        canvas,
        lines_metrics=[(text, lw, lh)],
        font=font,
        y0=y,
        line_gap=0,
        width=width,
        fill=fill,
        shadow_fill=shadow_fill,
        shadow_offset=(1, 1),
        shadow_blur=5.0,
    )


def default_test_caption() -> str:
    """Realistic seed line (not lorem). Falls back to the fixed baseline string."""
    seed = lofi_cfg.DATA_DIR / "seed_reference_structures.json"
    try:
        import json

        rows = json.loads(seed.read_text(encoding="utf-8"))
        if isinstance(rows, list):
            for row in rows:
                lines: Sequence[str] = row.get("lines") or []
                if len(lines) >= 5:
                    return str(lines[4]).strip()
                if lines:
                    return str(lines[0]).strip()
    except Exception:  # noqa: BLE001
        pass
    return "some things are worth the wait"

# -*- coding: utf-8 -*-
"""
LOFI caption typography — thin italic serif (Lora), white, soft shadow.

FINAL style (supersedes earlier Inter/Montserrat Medium sans spec).
Matched to "@Whispers" reference videos: delicate + soft + legible.
"""
from __future__ import annotations

from pathlib import Path
from typing import Sequence

from PIL import Image as PILImage
from PIL import ImageDraw, ImageFilter, ImageFont

from core_engine.economic_reel_lofi import config as lofi_cfg

# ── LOFI caption style constants (do not share with ECONOMIC_REEL yellow) ───
CAPTION_COLOR: tuple[int, int, int, int] = (255, 255, 255, 255)
SHADOW_COLOR: tuple[int, int, int, int] = (0, 0, 0, 102)  # ≈ rgba(0,0,0,0.40)
SHADOW_OFFSET_PX: tuple[int, int] = (2, 2)
SHADOW_BLUR_RADIUS: float = 6.0
MAX_WIDTH_FRAC: float = 0.80
# Upper-middle third (~35–45% from top); avoid top 15% safe zone
CAPTION_CENTER_Y_FRAC: float = 0.40
LINE_HEIGHT_FRAC: float = 0.052  # ~5.2% of frame height per line
LINE_GAP_FRAC: float = 0.028     # generous for italic ascenders/descenders
# Native Lora Italic tracking — do not add extra letter-spacing
LETTER_SPACING_PX: float = 0.0

# Italic-only chain. Never fall back to upright serif or any sans.
FONT_RELATIVE_CANDIDATES: tuple[str, ...] = (
    "Fonts/Lora/Lora-Italic.ttf",
    "Fonts/Lora/EBGaramond-Italic.ttf",
    "Fonts/Lora/CormorantGaramond-Italic.ttf",
)

# Watermark handle (same family, smaller, low opacity)
WATERMARK_COLOR: tuple[int, int, int, int] = (255, 255, 255, 140)  # ~0.55 opacity
WATERMARK_SHADOW_COLOR: tuple[int, int, int, int] = (0, 0, 0, 70)
WATERMARK_SIZE_FRAC: float = 0.028  # of frame height
WATERMARK_BOTTOM_MARGIN_FRAC: float = 0.045


def resolve_lofi_caption_font(
    engine_root: Path,
    *,
    size: int | None = None,
) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Lora Italic (Regular/Light) — italic slant is mandatory."""
    if size is None:
        size = max(28, int(lofi_cfg.REEL_HEIGHT * LINE_HEIGHT_FRAC))
    for rel in FONT_RELATIVE_CANDIDATES:
        path = engine_root / rel
        if path.is_file():
            try:
                return ImageFont.truetype(str(path), size=size)
            except Exception:  # noqa: BLE001
                continue
    raise FileNotFoundError(
        "LOFI caption requires an italic serif font. Install one of: "
        + ", ".join(FONT_RELATIVE_CANDIDATES)
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
) -> PILImage.Image:
    """
    White Lora Italic caption, soft blurred drop shadow,
    center-aligned in the upper-middle third (~40% from top).
    """
    canvas = PILImage.new("RGBA", (width, height), (0, 0, 0, 0))
    probe = ImageDraw.Draw(canvas)
    max_w = int(width * MAX_WIDTH_FRAC)
    # Shrink italic caption until every wrapped line fits within max width.
    size = max(28, int(height * LINE_HEIGHT_FRAC))
    font = resolve_lofi_caption_font(engine_root, size=size)
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
        font = resolve_lofi_caption_font(engine_root, size=size)
        lines = wrap_lofi_caption(text, font, max_w, probe)
    if not lines:
        return canvas

    line_gap = int(height * LINE_GAP_FRAC)
    metrics: list[tuple[str, int, int]] = []
    for ln in lines:
        # Use full line bbox so italic overhang is included
        bbox = probe.textbbox((0, 0), ln, font=font)
        lw = bbox[2] - bbox[0]
        lh = bbox[3] - bbox[1]
        metrics.append((ln, lw, lh))

    total_h = sum(m[2] for m in metrics) + line_gap * (len(metrics) - 1)
    # Keep caption center in 35–45% band; clamp away from top 15% safe zone
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
) -> PILImage.Image:
    """
    Channel handle in the same Lora Italic family — smaller, low-opacity white.
    Bottom-center, matching the "@Whispers" cohesive watermark treatment.
    """
    canvas = PILImage.new("RGBA", (width, height), (0, 0, 0, 0))
    text = (handle or "").strip()
    if not text:
        return canvas

    size = max(18, int(height * WATERMARK_SIZE_FRAC))
    font = resolve_lofi_caption_font(engine_root, size=size)
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

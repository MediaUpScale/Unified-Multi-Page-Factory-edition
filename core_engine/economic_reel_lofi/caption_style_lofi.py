# -*- coding: utf-8 -*-
"""
LOFI caption typography — selectable font library.

DEFAULT: ``caveat`` — Caveat-VariableFont_wght
  Fonts/Caveat/Caveat-VariableFont_wght.ttf

Library (kept for easy swaps on wonder_feed + momma_circle):
  - playwrite_nz_basic — PlaywriteNZBasic-VariableFont_wght
  - more_sugar_thin   — More Sugar Thin (prior default)
  - lora_italic       — Lora Italic
  - rounded_hand      — Comic Sans MS Bold
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
LINE_HEIGHT_FRAC: float = 0.033  # body size; override via CAPTION_LINE_HEIGHT_FRAC
LINE_GAP_FRAC: float = 0.028
LETTER_SPACING_PX: float = -1.5  # tighter tracking; overridden by CAPTION_LETTER_SPACING_PX


def _letter_spacing() -> float:
    return float(
        getattr(lofi_cfg, "CAPTION_LETTER_SPACING_PX", None) or LETTER_SPACING_PX
    )


def _text_width_spaced(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.ImageFont,
    spacing: float,
) -> int:
    if not text:
        return 0
    if abs(spacing) < 0.01:
        bbox = draw.textbbox((0, 0), text, font=font)
        return bbox[2] - bbox[0]
    total = 0
    for i, ch in enumerate(text):
        bbox = draw.textbbox((0, 0), ch, font=font)
        total += bbox[2] - bbox[0]
        if i < len(text) - 1:
            total += int(spacing)
    return total


def _draw_text_spaced(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    text: str,
    font: ImageFont.ImageFont,
    fill: tuple[int, int, int, int],
    spacing: float,
) -> None:
    x, y = xy
    if abs(spacing) < 0.01:
        draw.text((x, y), text, font=font, fill=fill)
        return
    for i, ch in enumerate(text):
        draw.text((x, y), ch, font=font, fill=fill)
        bbox = draw.textbbox((0, 0), ch, font=font)
        x += (bbox[2] - bbox[0]) + int(spacing)

# ── Font library (paths relative to engine root) ────────────────────────────
FONT_LIBRARY: dict[str, dict[str, object]] = {
    "edu_nsw": {
        "display": "Edu NSW ACT Foundation Bold",
        "candidates": (
            "Fonts/EduNSW/EduNSWACTFoundation-Bold.ttf",
            "Fonts/EduNSW/EduNSWACTFoundation-SemiBold.ttf",
            "Fonts/EduNSW/EduNSWACTFoundation-VariableFont_wght.ttf",
            "Fonts/EduNSW/EduNSWACTFoundation-Regular.ttf",
        ),
    },
    "caveat": {
        "display": "Caveat",
        "candidates": (
            "Fonts/Caveat/Caveat-VariableFont_wght.ttf",
            "Fonts/Caveat/Caveat[wght].ttf",
        ),
    },
    "playwrite_nz_basic": {
        "display": "Playwrite NZ Basic",
        "candidates": (
            "Fonts/PlaywriteNZBasic/PlaywriteNZBasic-VariableFont_wght.ttf",
            "Fonts/PlaywriteNZBasic/PlaywriteNZBasic[wght].ttf",
        ),
    },
    "more_sugar_thin": {
        "display": "More Sugar Thin",
        "candidates": (
            "Fonts/MoreSugar/MoreSugar-Thin.ttf",
            "Fonts/MoreSugar/MoreSugar-Thin.otf",
        ),
    },
    "lora_italic": {
        "display": "Lora Italic",
        "candidates": (
            "Fonts/Lora/Lora-Italic.ttf",
            "Fonts/Lora/EBGaramond-Italic.ttf",
            "Fonts/Lora/CormorantGaramond-Italic.ttf",
        ),
    },
    "rounded_hand": {
        "display": "Comic Sans MS Bold",
        "candidates": (
            "Fonts/ComicSans/ComicSansMS-Bold.ttf",
            "Fonts/ComicSans/comicbd.ttf",
            r"C:\Windows\Fonts\comicbd.ttf",
            "Fonts/Poppins/Poppins-Bold.ttf",
            "Fonts/Montserrat/static/Montserrat-Bold.ttf",
        ),
    },
}

# Derived maps (kept for existing call sites)
STYLE_FONT_MAP: dict[str, tuple[str, ...]] = {
    key: tuple(str(p) for p in (meta["candidates"] or ()))  # type: ignore[index]
    for key, meta in FONT_LIBRARY.items()
}
STYLE_DISPLAY_NAME: dict[str, str] = {
    key: str(meta["display"]) for key, meta in FONT_LIBRARY.items()
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


def list_caption_font_library() -> list[dict[str, str]]:
    """Return library entries for logging / CLI discovery."""
    default = lofi_cfg.DEFAULT_CAPTION_STYLE
    rows: list[dict[str, str]] = []
    for key, meta in FONT_LIBRARY.items():
        cands = [str(p) for p in (meta.get("candidates") or ())]
        rows.append(
            {
                "style": key,
                "display": str(meta.get("display") or key),
                "primary_file": cands[0] if cands else "",
                "default": "yes" if key == default else "",
            }
        )
    return rows


def resolve_lofi_caption_font(
    engine_root: Path,
    *,
    size: int | None = None,
    style: str | None = None,
) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Resolve caption font for the requested style (default: edu_nsw Bold)."""
    style_key = normalize_caption_style(style)
    line_frac = float(
        getattr(lofi_cfg, "CAPTION_LINE_HEIGHT_FRAC", None) or LINE_HEIGHT_FRAC
    )
    if size is None:
        size = max(22, int(lofi_cfg.REEL_HEIGHT * line_frac))
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
    spacing = _letter_spacing()
    shadow = PILImage.new("RGBA", canvas.size, (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow)
    ox, oy = shadow_offset
    y_s = y0
    for ln, lw, lh in lines_metrics:
        x = (width - lw) // 2
        _draw_text_spaced(
            shadow_draw, (x + ox, y_s + oy), ln, font, shadow_fill, spacing,
        )
        y_s += lh + line_gap
    shadow = shadow.filter(ImageFilter.GaussianBlur(radius=shadow_blur))
    out = PILImage.alpha_composite(canvas, shadow)

    draw = ImageDraw.Draw(out)
    y_t = y0
    for ln, lw, lh in lines_metrics:
        x = (width - lw) // 2
        _draw_text_spaced(draw, (x, y_t), ln, font, fill, spacing)
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

    Default style is ``edu_nsw`` (Edu NSW ACT Foundation Bold).
    """
    style_key = normalize_caption_style(style)
    canvas = PILImage.new("RGBA", (width, height), (0, 0, 0, 0))
    probe = ImageDraw.Draw(canvas)
    max_w = int(width * MAX_WIDTH_FRAC)
    line_frac = float(
        getattr(lofi_cfg, "CAPTION_LINE_HEIGHT_FRAC", None) or LINE_HEIGHT_FRAC
    )
    min_frac = float(
        getattr(lofi_cfg, "CAPTION_MIN_LINE_HEIGHT_FRAC", None) or line_frac
    )
    size = max(22, int(height * line_frac))
    min_size = max(22, int(height * min_frac))
    font = resolve_lofi_caption_font(engine_root, size=size, style=style_key)
    lines = wrap_lofi_caption(text, font, max_w, probe)
    # Wrap first. Only shrink if a wrapped line still overflows — never
    # collapse to a tiny one-line size (that undid previous size bumps).
    for _ in range(4):
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
        nxt = max(min_size, int(size * 0.94))
        if nxt >= size:
            break
        size = nxt
        font = resolve_lofi_caption_font(engine_root, size=size, style=style_key)
        lines = wrap_lofi_caption(text, font, max_w, probe)
    if not lines:
        return canvas

    line_gap = int(height * LINE_GAP_FRAC)
    spacing = _letter_spacing()
    metrics: list[tuple[str, int, int]] = []
    for ln in lines:
        lw = _text_width_spaced(probe, ln, font, spacing)
        bbox = probe.textbbox((0, 0), ln, font=font)
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


def _smoothstep01(u: float) -> float:
    u = max(0.0, min(1.0, float(u)))
    return u * u * (3.0 - 2.0 * u)


def render_lofi_caption_layer_word_fade(
    text: str,
    word_timings: Sequence[tuple[str, float, float]] | None,
    t: float,
    *,
    engine_root: Path,
    width: int = lofi_cfg.REEL_WIDTH,
    height: int = lofi_cfg.REEL_HEIGHT,
    style: str | None = None,
    fade_s: float | None = None,
    scene_duration_s: float | None = None,
    hold_s: float | None = None,
) -> PILImage.Image:
    """
    Progressive word fade-in synced to speech.

    Already-spoken words stay fully visible; the current word soft-fades in.
    Falls back to a static full caption when timings are missing.
    """
    words = (text or "").split()
    if not words:
        return PILImage.new("RGBA", (width, height), (0, 0, 0, 0))

    timings = list(word_timings or [])
    if not timings:
        # Show full line after a tiny delay so stills aren't blank at t=0
        if t < 0.05:
            return PILImage.new("RGBA", (width, height), (0, 0, 0, 0))
        return render_lofi_caption_layer(
            text, engine_root=engine_root, width=width, height=height, style=style,
        )

    fade = float(
        fade_s
        if fade_s is not None
        else getattr(lofi_cfg, "CAPTION_WORD_FADE_S", 0.20)
    )
    fade = max(0.05, min(0.45, fade))

    # Align timing list to caption words (prefer timing words; pad/truncate)
    timed: list[tuple[str, float, float]] = []
    for i, w in enumerate(words):
        if i < len(timings):
            _tw, start, end = timings[i]
            timed.append((w, float(start), float(end)))
        else:
            # Estimate trailing words from last known end
            last_end = timed[-1][2] if timed else 0.0
            step = 0.28
            timed.append((w, last_end + (i - len(timings)) * step, last_end + (i - len(timings) + 1) * step))

    hold = float(
        hold_s
        if hold_s is not None
        else getattr(lofi_cfg, "CAPTION_HOLD_S", 0.45)
    )
    scene_dur = float(scene_duration_s) if scene_duration_s else None
    # Hold window: once the last word should be on screen, keep the FULL line
    # visible until the cut — never leave a mid-clause fragment at the cut.
    last_reveal = max((start + fade) for _w, start, _end in timed) if timed else 0.0
    force_full = False
    if scene_dur is not None and scene_dur > 0:
        force_full = t >= max(0.0, scene_dur - max(0.12, hold))
    if t >= last_reveal:
        force_full = True

    opacities: list[float] = []
    for _w, start, _end in timed:
        if force_full:
            opacities.append(1.0)
        elif t < start:
            opacities.append(0.0)
        elif t >= start + fade:
            opacities.append(1.0)
        else:
            opacities.append(_smoothstep01((t - start) / fade))

    if max(opacities) < 0.01:
        return PILImage.new("RGBA", (width, height), (0, 0, 0, 0))

    style_key = normalize_caption_style(style)
    canvas = PILImage.new("RGBA", (width, height), (0, 0, 0, 0))
    probe = ImageDraw.Draw(canvas)
    max_w = int(width * MAX_WIDTH_FRAC)
    side_pad = int(width * 0.06)
    line_frac = float(
        getattr(lofi_cfg, "CAPTION_LINE_HEIGHT_FRAC", None) or LINE_HEIGHT_FRAC
    )
    min_frac = float(
        getattr(lofi_cfg, "CAPTION_MIN_LINE_HEIGHT_FRAC", None) or line_frac
    )
    size = max(22, int(height * line_frac))
    min_size = max(22, int(height * min_frac))
    spacing = _letter_spacing()
    max_lines = 3

    def _metrics_for_size(px: int) -> tuple:
        font_i = resolve_lofi_caption_font(engine_root, size=px, style=style_key)
        space_bbox = probe.textbbox((0, 0), " ", font=font_i)
        space_i = max(1, space_bbox[2] - space_bbox[0] + int(spacing))
        widths_i = [_text_width_spaced(probe, w, font_i, spacing) for w in words]
        lines_i: list[list[int]] = [[]]
        run = 0
        for i, ww in enumerate(widths_i):
            add = ww if not lines_i[-1] else ww + space_i
            if lines_i[-1] and run + add > max_w and len(lines_i) < max_lines:
                lines_i.append([i])
                run = ww
            else:
                lines_i[-1].append(i)
                run += add
        overflow = False
        for line in lines_i:
            lw = sum(widths_i[i] for i in line) + space_i * max(0, len(line) - 1)
            if lw > max_w:
                overflow = True
                break
        return font_i, space_i, widths_i, lines_i, overflow

    font, space_px, word_widths, lines_idx, overflow = _metrics_for_size(size)
    while overflow and size > min_size:
        nxt = max(min_size, int(size * 0.94))
        if nxt >= size:
            break
        size = nxt
        font, space_px, word_widths, lines_idx, overflow = _metrics_for_size(size)

    line_gap = int(height * LINE_GAP_FRAC)
    ascent_bbox = probe.textbbox((0, 0), "Ay", font=font)
    line_h = ascent_bbox[3] - ascent_bbox[1]
    total_h = line_h * len(lines_idx) + line_gap * (len(lines_idx) - 1)
    y_center = int(height * CAPTION_CENTER_Y_FRAC)
    y0 = max(int(height * 0.16), y_center - total_h // 2)

    def _line_x(lw: int) -> int:
        x = (width - lw) // 2
        return max(side_pad, min(x, width - lw - side_pad))

    # Soft shadow of currently-visible glyphs
    shadow = PILImage.new("RGBA", (width, height), (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow)
    ox, oy = SHADOW_OFFSET_PX
    y = y0
    for line in lines_idx:
        lw = sum(word_widths[i] for i in line) + space_px * max(0, len(line) - 1)
        x = _line_x(lw)
        for j, wi in enumerate(line):
            op = opacities[wi]
            if op < 0.01:
                x += word_widths[wi] + (space_px if j < len(line) - 1 else 0)
                continue
            a = int(SHADOW_COLOR[3] * op)
            _draw_text_spaced(
                shadow_draw,
                (x + ox, y + oy),
                words[wi],
                font,
                (0, 0, 0, a),
                spacing,
            )
            x += word_widths[wi] + (space_px if j < len(line) - 1 else 0)
        y += line_h + line_gap
    shadow = shadow.filter(ImageFilter.GaussianBlur(radius=SHADOW_BLUR_RADIUS))
    out = PILImage.alpha_composite(canvas, shadow)

    draw = ImageDraw.Draw(out)
    y = y0
    for line in lines_idx:
        lw = sum(word_widths[i] for i in line) + space_px * max(0, len(line) - 1)
        x = _line_x(lw)
        for j, wi in enumerate(line):
            op = opacities[wi]
            if op < 0.01:
                x += word_widths[wi] + (space_px if j < len(line) - 1 else 0)
                continue
            a = int(255 * op)
            _draw_text_spaced(
                draw, (x, y), words[wi], font, (255, 255, 255, a), spacing,
            )
            x += word_widths[wi] + (space_px if j < len(line) - 1 else 0)
        y += line_h + line_gap
    return out


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

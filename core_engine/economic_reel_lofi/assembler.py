# -*- coding: utf-8 -*-
"""
MoviePy assembler — Ken Burns + duotone grading + captions + channel logo.

Reuses slow-zoom framing similar to compile_dynamic_reel without mutating it.
"""
from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import Any, Sequence

import numpy as np
from PIL import Image as PILImage
from PIL import ImageEnhance, ImageOps

from core_engine.economic_reel_lofi import config as lofi_cfg
from core_engine.economic_reel_lofi.caption_style_lofi import (
    render_lofi_caption_layer,
    render_lofi_watermark_layer,
)

_LOG = logging.getLogger(__name__)


def apply_duotone(
    rgb: np.ndarray,
    *,
    shadow: tuple[int, int, int] | None = None,
    highlight: tuple[int, int, int] | None = None,
    bands: int | None = None,
) -> np.ndarray:
    """
    Poster-style luminance remap (not a smooth gradient wash).

    1. Convert to grayscale via Rec.601 luminance (discards source hue).
    2. Quantize luminance into a few discrete bands (default 4) so skies /
       fills read as solid color blocks with hard-ish tone edges.
    3. Map each band onto the shadow→highlight palette (no continuous lerp).
    """
    img = rgb.astype(np.float32)
    lum = 0.299 * img[..., 0] + 0.587 * img[..., 1] + 0.114 * img[..., 2]
    t = np.clip(lum / 255.0, 0.0, 1.0)
    # Mild contrast before quantize so midtones don't collapse into one band
    t = np.power(t, 0.88)

    n = int(bands if bands is not None else lofi_cfg.DUOTONE_TONAL_BANDS)
    n = max(2, min(8, n))
    # Snap to n discrete levels in [0, 1] (e.g. 0, 1/3, 2/3, 1 for n=4)
    levels = np.round(t * (n - 1)) / float(n - 1)
    levels = np.clip(levels, 0.0, 1.0)

    sh = np.array(shadow if shadow is not None else lofi_cfg.DUOTONE_SHADOW, dtype=np.float32)
    hi = np.array(
        highlight if highlight is not None else lofi_cfg.DUOTONE_HIGHLIGHT,
        dtype=np.float32,
    )
    out = sh + (hi - sh) * levels[..., None]
    return np.clip(out, 0, 255).astype(np.uint8)


def apply_vignette(rgb: np.ndarray, strength: float = lofi_cfg.VIGNETTE_STRENGTH) -> np.ndarray:
    """Optional edge darken. Strength ~0 disables (avoids soft radial sky glow)."""
    if strength is None or float(strength) <= 0.001:
        return rgb
    h, w = rgb.shape[:2]
    y, x = np.ogrid[:h, :w]
    cy, cx = (h - 1) / 2.0, (w - 1) / 2.0
    dist = np.sqrt(((x - cx) / cx) ** 2 + ((y - cy) / cy) ** 2)
    mask = 1.0 - np.clip(dist * strength, 0.0, strength)
    mask = mask[..., None]
    return np.clip(rgb.astype(np.float32) * mask, 0, 255).astype(np.uint8)


def apply_film_grain(
    rgb: np.ndarray,
    *,
    seed: int = 0,
    t: float = 0.0,
) -> np.ndarray:
    """Same grain math used in the video assembler frame loop."""
    h, w = rgb.shape[:2]
    grain_alpha = lofi_cfg.GRAIN_INTENSITY * (0.85 + 0.15 * math.sin(t * 11.0))
    noise = np.random.RandomState(int(seed)).randint(0, 64, (h, w, 3), dtype=np.uint8)
    return np.clip(
        rgb.astype(np.int16) + (noise * grain_alpha).astype(np.int16),
        0,
        255,
    ).astype(np.uint8)


def prep_base_frame(
    image_path: Path,
    *,
    shadow: tuple[int, int, int] | None = None,
    highlight: tuple[int, int, int] | None = None,
) -> np.ndarray:
    """Load, cover-fit to reel canvas, duotone + light contrast + vignette."""
    im = PILImage.open(image_path).convert("RGB")
    im = ImageOps.fit(
        im,
        (lofi_cfg.REEL_WIDTH, lofi_cfg.REEL_HEIGHT),
        method=PILImage.LANCZOS,
        centering=(0.5, 0.5),
    )
    arr = apply_duotone(np.array(im), shadow=shadow, highlight=highlight)
    # Mild contrast after remap — keep low so shadow hue stays visible
    pil = PILImage.fromarray(arr)
    pil = ImageEnhance.Contrast(pil).enhance(1.08)
    # Vignette nearly off by default (flat poster look)
    return apply_vignette(np.array(pil), strength=lofi_cfg.VIGNETTE_STRENGTH)


def grade_still_frame(
    image_path: Path,
    *,
    grain_seed: int = 42,
    shadow: tuple[int, int, int] | None = None,
    highlight: tuple[int, int, int] | None = None,
    mood_id: str | None = None,
) -> np.ndarray:
    """
    Production grading for a single still: duotone + contrast + vignette + grain.

    Shared by ``test_preview`` and the video assembler so approved stills match ship.
    Pass mood-matched ``shadow``/``highlight`` (or ``mood_id``) for color variety.
    """
    if mood_id and (shadow is None or highlight is None):
        mood = lofi_cfg.lighting_mood_by_id(mood_id)
        if shadow is None:
            shadow = tuple(mood["shadow"])
        if highlight is None:
            highlight = tuple(mood["highlight"])
    sh = tuple(shadow) if shadow is not None else tuple(lofi_cfg.DUOTONE_SHADOW)
    hi = tuple(highlight) if highlight is not None else tuple(lofi_cfg.DUOTONE_HIGHLIGHT)
    print(
        "[LOFI grade] apply_duotone ACTIVE (posterize bands="
        f"{lofi_cfg.DUOTONE_TONAL_BANDS}) | "
        f"mood={mood_id or 'custom'} | "
        f"shadow={sh} highlight={hi} | vignette={lofi_cfg.VIGNETTE_STRENGTH} | "
        f"source={image_path}"
    )
    _LOG.info(
        "LOFI grade_still_frame | mood=%s | bands=%s | duotone shadow=%s highlight=%s | vignette=%s | %s",
        mood_id,
        lofi_cfg.DUOTONE_TONAL_BANDS,
        sh,
        hi,
        lofi_cfg.VIGNETTE_STRENGTH,
        image_path,
    )
    return apply_film_grain(
        prep_base_frame(image_path, shadow=sh, highlight=hi),
        seed=grain_seed,
        t=0.5,
    )


def render_logo_layer(
    logo_path: Path | None,
    *,
    opacity: float,
    scale: float,
) -> np.ndarray | None:
    if not logo_path or not Path(logo_path).is_file():
        return None
    try:
        logo = PILImage.open(logo_path).convert("RGBA")
    except Exception as exc:  # noqa: BLE001
        _LOG.warning("logo load failed: %s", exc)
        return None
    target_w = max(1, int(lofi_cfg.REEL_WIDTH * float(scale)))
    ratio = target_w / logo.width
    target_h = max(1, int(logo.height * ratio))
    logo = logo.resize((target_w, target_h), PILImage.LANCZOS)
    r, g, b, a = logo.split()
    a = a.point(lambda v: int(v * max(0.05, min(1.0, opacity))))
    logo = PILImage.merge("RGBA", (r, g, b, a))
    canvas = PILImage.new("RGBA", (lofi_cfg.REEL_WIDTH, lofi_cfg.REEL_HEIGHT), (0, 0, 0, 0))
    x = (lofi_cfg.REEL_WIDTH - target_w) // 2
    y = lofi_cfg.REEL_HEIGHT - target_h - 48
    canvas.paste(logo, (x, y), logo)
    return np.array(canvas)


def assemble_lofi_reel(
    scene_images: Sequence[Path],
    captions: Sequence[str],
    output_mp4: Path,
    *,
    engine_root: Path,
    page_id: str,
    scene_duration_s: float = lofi_cfg.SCENE_DURATION_S,
    moods: Sequence[dict[str, Any] | str | None] | None = None,
    caption_style: str | None = None,
) -> Path:
    """Compile scene stills into a vertical MP4 with Ken Burns + LOFI grade."""
    # MoviePy 2.x top-level imports (matches avatar_engine/video_engine.py)
    try:
        from moviepy import VideoClip, concatenate_videoclips  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "assembler requires moviepy>=2.0, numpy, Pillow.\n"
            f"Original error: {exc}"
        ) from exc

    if len(scene_images) != len(captions):
        raise ValueError("scene_images and captions length mismatch")
    if not scene_images:
        raise ValueError("no scenes to assemble")

    cfg = lofi_cfg.channel_assembly_cfg(page_id)
    style = caption_style or lofi_cfg.DEFAULT_CAPTION_STYLE
    if cfg.get("use_text_watermark", True):
        logo_layer = np.array(
            render_lofi_watermark_layer(
                str(cfg.get("watermark_handle") or ""),
                engine_root=engine_root,
                opacity=float(cfg.get("logo_opacity", 0.55)),
                style=style,
            )
        )
    else:
        logo_path = lofi_cfg.resolve_logo_path(page_id, engine_root)
        logo_layer = render_logo_layer(
            logo_path,
            opacity=float(cfg.get("logo_opacity", 0.5)),
            scale=float(cfg.get("logo_scale", 0.06)),
        )

    clips = []
    z0 = lofi_cfg.KEN_BURNS_ZOOM_START
    z1 = lofi_cfg.KEN_BURNS_ZOOM_END

    for idx, (img_path, caption) in enumerate(zip(scene_images, captions)):
        mood_raw: Any = None
        if moods is not None and idx < len(moods):
            mood_raw = moods[idx]
        if isinstance(mood_raw, dict):
            mood = mood_raw
        elif isinstance(mood_raw, str) and mood_raw.strip():
            mood = lofi_cfg.lighting_mood_by_id(mood_raw)
        else:
            mood = lofi_cfg.select_lighting_mood(key=idx)
        sh = tuple(mood.get("shadow") or lofi_cfg.DUOTONE_SHADOW)
        hi = tuple(mood.get("highlight") or lofi_cfg.DUOTONE_HIGHLIGHT)
        base = prep_base_frame(Path(img_path), shadow=sh, highlight=hi)
        cap_layer = np.array(
            render_lofi_caption_layer(
                str(caption), engine_root=engine_root, style=style,
            )
        )
        h, w = base.shape[:2]

        def _make_frame(t, _base=base, _cap=cap_layer, _logo=logo_layer, _idx=idx):
            progress = max(0.0, min(1.0, t / max(scene_duration_s, 0.001)))
            if _idx % 2 == 0:
                zoom = z0 + (z1 - z0) * progress
            else:
                zoom = z1 - (z1 - z0) * progress
            zoom = max(1.0, zoom)
            crop_w = int(w / zoom)
            crop_h = int(h / zoom)
            x0 = (w - crop_w) // 2
            y0 = (h - crop_h) // 2
            cropped = _base[y0 : y0 + crop_h, x0 : x0 + crop_w]
            frame = np.array(
                PILImage.fromarray(cropped).resize((w, h), PILImage.LANCZOS)
            )
            frame = apply_film_grain(
                frame, seed=int(t * 1000) + _idx * 17, t=t,
            )

            rgba = PILImage.fromarray(frame).convert("RGBA")
            rgba = PILImage.alpha_composite(rgba, PILImage.fromarray(_cap))
            if _logo is not None:
                rgba = PILImage.alpha_composite(rgba, PILImage.fromarray(_logo))
            return np.array(rgba.convert("RGB"))

        clip = VideoClip(frame_function=_make_frame, duration=float(scene_duration_s))
        clip = clip.with_fps(lofi_cfg.REEL_FPS)
        clips.append(clip)

    final = concatenate_videoclips(clips, method="compose")

    output_mp4 = Path(output_mp4)
    output_mp4.parent.mkdir(parents=True, exist_ok=True)
    try:
        final.write_videofile(
            str(output_mp4),
            fps=lofi_cfg.REEL_FPS,
            codec="libx264",
            audio=False,
            preset="fast",
            ffmpeg_params=["-crf", "20", "-pix_fmt", "yuv420p"],
            logger=None,
        )
    finally:
        try:
            final.close()
        except Exception:  # noqa: BLE001
            pass
        for c in clips:
            try:
                c.close()
            except Exception:  # noqa: BLE001
                pass

    _LOG.info("LOFI reel assembled → %s (%.1fs)", output_mp4, len(scene_images) * scene_duration_s)
    return output_mp4

# -*- coding: utf-8 -*-
"""
MoviePy assembler — Ken Burns + duotone grading + captions + channel logo.

Reuses slow-zoom framing similar to compile_dynamic_reel without mutating it.
"""
from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import Sequence

import numpy as np
from PIL import Image as PILImage
from PIL import ImageEnhance, ImageOps

from core_engine.economic_reel_lofi import config as lofi_cfg
from core_engine.economic_reel_lofi.caption_style_lofi import (
    render_lofi_caption_layer,
    render_lofi_watermark_layer,
)

_LOG = logging.getLogger(__name__)


def apply_duotone(rgb: np.ndarray) -> np.ndarray:
    """Map luminance through a two-stop duotone curve."""
    img = rgb.astype(np.float32)
    # Luminance
    lum = 0.299 * img[..., 0] + 0.587 * img[..., 1] + 0.114 * img[..., 2]
    t = np.clip(lum / 255.0, 0.0, 1.0)
    # Slight contrast curve
    t = np.power(t, 0.92)
    shadow = np.array(lofi_cfg.DUOTONE_SHADOW, dtype=np.float32)
    highlight = np.array(lofi_cfg.DUOTONE_HIGHLIGHT, dtype=np.float32)
    out = shadow + (highlight - shadow) * t[..., None]
    return np.clip(out, 0, 255).astype(np.uint8)


def apply_vignette(rgb: np.ndarray, strength: float = lofi_cfg.VIGNETTE_STRENGTH) -> np.ndarray:
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


def prep_base_frame(image_path: Path) -> np.ndarray:
    """Load, cover-fit to reel canvas, duotone + light contrast + vignette."""
    im = PILImage.open(image_path).convert("RGB")
    im = ImageOps.fit(
        im,
        (lofi_cfg.REEL_WIDTH, lofi_cfg.REEL_HEIGHT),
        method=PILImage.LANCZOS,
        centering=(0.5, 0.5),
    )
    arr = apply_duotone(np.array(im))
    pil = PILImage.fromarray(arr)
    pil = ImageEnhance.Contrast(pil).enhance(1.08)
    return apply_vignette(np.array(pil))


def grade_still_frame(
    image_path: Path,
    *,
    grain_seed: int = 42,
) -> np.ndarray:
    """
    Production grading for a single still: duotone + contrast + vignette + grain.

    Shared by ``test_preview`` and the video assembler so approved stills match ship.
    """
    print(
        "[LOFI grade] apply_duotone ACTIVE | "
        f"shadow={tuple(lofi_cfg.DUOTONE_SHADOW)} "
        f"highlight={tuple(lofi_cfg.DUOTONE_HIGHLIGHT)} "
        f"source={image_path}"
    )
    _LOG.info(
        "LOFI grade_still_frame | duotone shadow=%s highlight=%s | %s",
        lofi_cfg.DUOTONE_SHADOW,
        lofi_cfg.DUOTONE_HIGHLIGHT,
        image_path,
    )
    return apply_film_grain(prep_base_frame(image_path), seed=grain_seed, t=0.5)


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
    if cfg.get("use_text_watermark", True):
        logo_layer = np.array(
            render_lofi_watermark_layer(
                str(cfg.get("watermark_handle") or ""),
                engine_root=engine_root,
                opacity=float(cfg.get("logo_opacity", 0.55)),
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
        base = prep_base_frame(Path(img_path))
        cap_layer = np.array(
            render_lofi_caption_layer(str(caption), engine_root=engine_root)
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

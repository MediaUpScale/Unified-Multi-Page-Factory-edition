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
    render_lofi_caption_layer_word_fade,
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


def apply_vignette(rgb: np.ndarray, strength: float | None = None) -> np.ndarray:
    """Dark vintage vignette — corner darken, transparent center."""
    if strength is None:
        if not bool(getattr(lofi_cfg, "ENABLE_VIGNETTE", True)):
            return rgb
        strength = float(getattr(lofi_cfg, "VIGNETTE_STRENGTH", 0.18))
    strength = float(strength)
    if strength <= 0.001:
        return rgb
    strength = max(0.0, min(0.45, strength))
    h, w = rgb.shape[:2]
    y, x = np.ogrid[:h, :w]
    cy, cx = (h - 1) / 2.0, (w - 1) / 2.0
    # Normalized radial distance; corners ≈ 1.0+
    dist = np.sqrt(((x - cx) / max(cx, 1.0)) ** 2 + ((y - cy) / max(cy, 1.0)) ** 2)
    # Soft falloff: center untouched, corners darkened by ``strength``
    edge = np.clip((dist - 0.35) / 0.95, 0.0, 1.0)
    edge = edge * edge * (3.0 - 2.0 * edge)  # smoothstep
    mask = (1.0 - strength * edge)[..., None]
    return np.clip(rgb.astype(np.float32) * mask, 0, 255).astype(np.uint8)


def _smootherstep01(u: float) -> float:
    """Ken Perlin smootherstep on [0,1] — soft acceleration / deceleration."""
    u = max(0.0, min(1.0, float(u)))
    return u * u * u * (u * (u * 6.0 - 15.0) + 10.0)


def light_pulse_brightness_factor(t: float, seed: int = 3) -> float:
    """
    Global sine brightness factor, independent of Ken Burns zoom.

    brightness_factor = 1 + AMP * sin(2π t / PERIOD + phase)
    """
    if not bool(getattr(lofi_cfg, "ENABLE_LIGHT_BREATH", True)):
        return 1.0
    amp = float(getattr(lofi_cfg, "LIGHT_BREATH_AMP", 0.065))
    period = float(getattr(lofi_cfg, "LIGHT_BREATH_PERIOD_S", 6.5))
    amp = max(0.0, min(0.12, amp))
    period = max(5.5, min(8.5, period))
    if amp < 0.001:
        return 1.0
    phase = ((int(seed) % 97) * 0.13) % (2.0 * math.pi)
    return 1.0 + amp * math.sin(2.0 * math.pi * (t / period) + phase)


def apply_animated_light_pulse(
    rgb: np.ndarray,
    *,
    t: float,
    seed: int = 0,
) -> np.ndarray:
    """
    Post-process breathing light — independent of baked artwork lighting.

    Uses ``light_pulse_brightness_factor`` then biases the delta toward
    bright sources (streetlamp / window / headlights).
    """
    factor = light_pulse_brightness_factor(t, seed=seed)
    pulse = factor - 1.0
    if abs(pulse) < 0.0005:
        return rgb
    amp = float(getattr(lofi_cfg, "LIGHT_BREATH_AMP", 0.065))
    source_bias = float(getattr(lofi_cfg, "LIGHT_BREATH_SOURCE_BIAS", 0.70))
    bloom = float(getattr(lofi_cfg, "LIGHT_BREATH_BLOOM", 0.040))

    img = rgb.astype(np.float32)
    lum = 0.299 * img[..., 0] + 0.587 * img[..., 1] + 0.114 * img[..., 2]
    source = np.clip((lum - 145.0) / 85.0, 0.0, 1.0)
    source = (
        source
        + np.roll(source, 3, 0)
        + np.roll(source, -3, 0)
        + np.roll(source, 3, 1)
        + np.roll(source, -3, 1)
    ) / 5.0
    source = (
        source
        + np.roll(source, 6, 0)
        + np.roll(source, -6, 0)
        + np.roll(source, 6, 1)
        + np.roll(source, -6, 1)
    ) / 5.0

    global_w = 1.0 - source_bias
    weight = (global_w + source_bias * source)[..., None]
    out = img * (1.0 + pulse * weight)

    if pulse > 0.0 and bloom > 0.0:
        warm = np.array([1.12, 1.04, 0.92], dtype=np.float32)
        bloom_amt = (pulse / max(amp, 1e-6)) * bloom * source[..., None]
        out = out + out * bloom_amt * (warm - 1.0)

    return np.clip(out, 0, 255).astype(np.uint8)


def clamp_baked_highlights(rgb: np.ndarray) -> np.ndarray:
    """
    Soft-knee highlight clamp on the still, before Ken Burns.

    Pulls blown whites / chest-collar bloom back without a LUT or duotone.
    """
    knee = float(getattr(lofi_cfg, "HIGHLIGHT_SOFT_KNEE", 208.0))
    compress = float(getattr(lofi_cfg, "HIGHLIGHT_COMPRESS", 0.48))
    ceiling = float(getattr(lofi_cfg, "HIGHLIGHT_CLAMP_CEILING", 236.0))
    img = rgb.astype(np.float32)
    lum = 0.299 * img[..., 0] + 0.587 * img[..., 1] + 0.114 * img[..., 2]
    over = np.maximum(lum - knee, 0.0)
    if float(np.max(over)) < 0.5:
        return np.clip(img, 0, ceiling).astype(np.uint8)
    pull = 1.0 - compress * (over / (over + 40.0))
    out = img * pull[..., None]
    return np.clip(out, 0, ceiling).astype(np.uint8)


def apply_film_grain(
    rgb: np.ndarray,
    *,
    seed: int = 0,
    t: float = 0.0,
) -> np.ndarray:
    """Animated monochrome film grain. Disabled when using the reference overlay."""
    if not bool(getattr(lofi_cfg, "ENABLE_PROCEDURAL_GRAIN", False)):
        return rgb
    h, w = rgb.shape[:2]
    opacity = float(getattr(lofi_cfg, "GRAIN_OPACITY", 0.13))
    opacity = max(0.0, min(0.20, opacity))
    if opacity < 0.001:
        # Legacy additive fallback
        grain_alpha = lofi_cfg.GRAIN_INTENSITY * (0.85 + 0.15 * math.sin(t * 11.0))
        noise = np.random.RandomState(int(seed)).randint(0, 64, (h, w, 3), dtype=np.uint8)
        return np.clip(
            rgb.astype(np.int16) + (noise * grain_alpha).astype(np.int16),
            0,
            255,
        ).astype(np.uint8)

    # Uncached RNG keyed by frame time — do not reuse arrays across frames
    rng = np.random.default_rng(
        (int(seed) ^ (int(t * 1000.0) * 2654435761)) & 0x7FFFFFFF
    )
    half = bool(getattr(lofi_cfg, "GRAIN_HALF_RES", True))
    nh, nw = (max(1, h // 2), max(1, w // 2)) if half else (h, w)
    mono = rng.normal(loc=0.0, scale=1.0, size=(nh, nw)).astype(np.float32)
    # Mild blur → finer optical grain (not blocky)
    mono = (
        mono
        + np.roll(mono, 1, 0)
        + np.roll(mono, -1, 0)
        + np.roll(mono, 1, 1)
        + np.roll(mono, -1, 1)
    ) / 5.0
    if half:
        mono = np.array(
            PILImage.fromarray(
                np.clip(mono * 32.0 + 128.0, 0, 255).astype(np.uint8), mode="L"
            ).resize((w, h), PILImage.BILINEAR),
            dtype=np.float32,
        )
        mono = (mono - 128.0) / 32.0
    grain = np.clip(0.5 + mono * 0.12, 0.0, 1.0)[..., None]

    base = rgb.astype(np.float32) / 255.0
    # Overlay blend
    overlay = np.where(
        base < 0.5,
        2.0 * base * grain,
        1.0 - 2.0 * (1.0 - base) * (1.0 - grain),
    )
    # Shadow density: more grain in darks
    lum = 0.299 * base[..., 0] + 0.587 * base[..., 1] + 0.114 * base[..., 2]
    dark_boost = (1.0 - lum) * 0.55 + 0.45
    local_op = opacity * dark_boost[..., None]
    out = base * (1.0 - local_op) + overlay * local_op
    return np.clip(out * 255.0, 0, 255).astype(np.uint8)


def apply_caption_film_multiply(
    rgb: np.ndarray,
    *,
    seed: int = 0,
    t: float = 0.0,
) -> np.ndarray:
    """
    Film-stock multiply texture behind captions (depth without a solid box).

    Static plate per scene (t ignored for noise) — avoids TV-static flicker.
    Soft vertical band around caption center; text is composited AFTER this.
    """
    del t  # intentionally unused — plate must not animate per frame
    op = float(getattr(lofi_cfg, "CAPTION_FILM_MULTIPLY_OPACITY", 0.20))
    op = max(0.0, min(0.35, op))
    if op < 0.001:
        return rgb
    h, w = rgb.shape[:2]
    rng = np.random.default_rng(int(seed) & 0x7FFFFFFF)
    nh, nw = max(1, h // 2), max(1, w // 2)
    mono = rng.normal(0.0, 1.0, size=(nh, nw)).astype(np.float32)
    mono = (
        mono
        + np.roll(mono, 2, 0)
        + np.roll(mono, -2, 0)
        + np.roll(mono, 2, 1)
        + np.roll(mono, -2, 1)
    ) / 5.0
    plate = np.clip(0.58 + mono * 0.09, 0.32, 0.88)
    plate = np.array(
        PILImage.fromarray(
            np.clip(plate * 255.0, 0, 255).astype(np.uint8), mode="L"
        ).resize((w, h), PILImage.BILINEAR),
        dtype=np.float32,
    ) / 255.0
    plate = plate[..., None]
    y = np.linspace(0.0, 1.0, h, dtype=np.float32)[:, None]
    band = np.exp(-((y - 0.46) ** 2) / (2.0 * 0.12 ** 2))
    weight = (band * op)[..., None]
    base = rgb.astype(np.float32) / 255.0
    multiplied = base * plate
    out = base * (1.0 - weight) + multiplied * weight
    return np.clip(out * 255.0, 0, 255).astype(np.uint8)


_DUST_TILE_CACHE: dict[int, np.ndarray] = {}


def _build_dust_tile(seed: int = 11) -> np.ndarray:
    """Soft particle tile (RGBA float 0–1) drifted upward each frame."""
    if seed in _DUST_TILE_CACHE:
        return _DUST_TILE_CACHE[seed]
    from PIL import ImageDraw as _ImageDraw

    w, h = lofi_cfg.REEL_WIDTH, lofi_cfg.REEL_HEIGHT
    tile = PILImage.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = _ImageDraw.Draw(tile)
    rng = np.random.default_rng(seed)
    n = int(getattr(lofi_cfg, "DUST_PARTICLE_COUNT", 48))
    base_a = float(getattr(lofi_cfg, "DUST_PARTICLE_OPACITY", 0.12))
    for _ in range(max(8, n)):
        px = int(rng.integers(8, w - 8))
        py = int(rng.integers(8, h - 8))
        pr = int(rng.integers(1, 3))
        pa = int(255 * base_a * float(rng.uniform(0.35, 1.0)))
        draw.ellipse((px - pr, py - pr, px + pr, py + pr), fill=(255, 255, 255, pa))
    arr = np.array(tile, dtype=np.float32) / 255.0
    _DUST_TILE_CACHE[seed] = arr
    return arr


def apply_dust_particles(rgb: np.ndarray, *, t: float, seed: int = 11) -> np.ndarray:
    if not bool(getattr(lofi_cfg, "ENABLE_DUST_PARTICLES", False)):
        return rgb
    tile = _build_dust_tile(seed=seed)
    h, w = rgb.shape[:2]
    # Drift upward ~18 px/s
    shift = int((t * 18.0) % h)
    shifted = np.roll(tile, -shift, axis=0)
    if shifted.shape[0] != h or shifted.shape[1] != w:
        shifted = np.array(
            PILImage.fromarray((shifted * 255).astype(np.uint8)).resize(
                (w, h), PILImage.BILINEAR
            ),
            dtype=np.float32,
        ) / 255.0
    base = rgb.astype(np.float32) / 255.0
    a = shifted[..., 3:4]
    rgb_p = shifted[..., :3]
    out = base * (1.0 - a) + np.clip(base + rgb_p * 0.55, 0, 1) * a
    return np.clip(out * 255.0, 0, 255).astype(np.uint8)


def ensure_dust_overlay_asset(engine_root: Path) -> Path:
    """
    Reference film-grain / dust overlay (black bg, light particles).
    Prefer the configured mp4; never procedurally generate when it exists.
    """
    rel = str(
        getattr(
            lofi_cfg,
            "DUST_OVERLAY_REL",
            "channels_config/wonder_feed/overlays/overlay film grain.mp4",
        )
    )
    path = Path(engine_root) / rel
    if path.is_file() and path.stat().st_size > 10_000:
        return path
    npz_path = path.with_suffix(".npz")
    if bool(getattr(lofi_cfg, "DUST_OVERLAY_PREFER_NPZ", False)) and npz_path.is_file():
        return npz_path
    # Last-resort generate only if the reference file is missing
    path.parent.mkdir(parents=True, exist_ok=True)
    from PIL import ImageDraw as _ImageDraw

    w, h = lofi_cfg.REEL_WIDTH, lofi_cfg.REEL_HEIGHT
    fps = int(lofi_cfg.REEL_FPS)
    duration_s = 8.0
    n_frames = int(duration_s * fps)
    rng = np.random.default_rng(20260817)

    scratch = PILImage.new("RGB", (w, h), (0, 0, 0))
    sd = _ImageDraw.Draw(scratch)
    for _ in range(40):
        x0 = int(rng.integers(0, w))
        y0 = int(rng.integers(0, h))
        x1 = int(np.clip(x0 + rng.integers(-80, 80), 0, w - 1))
        y1 = int(np.clip(y0 + rng.integers(-400, 400), 0, h - 1))
        val = int(rng.integers(40, 120))
        sd.line([(x0, y0), (x1, y1)], fill=(val, val, val), width=1)
    scratch_arr = np.array(scratch, dtype=np.float32)

    n_p = 90
    px = rng.uniform(0, w, size=n_p)
    py = rng.uniform(0, h, size=n_p)
    pr = rng.uniform(0.8, 2.4, size=n_p)
    pspd = rng.uniform(8.0, 28.0, size=n_p)
    pbright = rng.uniform(90, 220, size=n_p)

    frames: list[np.ndarray] = []
    for fi in range(n_frames):
        t = fi / float(fps)
        canvas = scratch_arr.copy()
        for i in range(n_p):
            x = int(px[i]) % w
            y = int((py[i] - pspd[i] * t) % h)
            r = max(1, int(pr[i]))
            b = float(pbright[i])
            y0b, y1b = max(0, y - r), min(h, y + r + 1)
            x0b, x1b = max(0, x - r), min(w, x + r + 1)
            canvas[y0b:y1b, x0b:x1b] = np.maximum(canvas[y0b:y1b, x0b:x1b], b)
        if fi % 17 == 0:
            cx = int(rng.integers(0, w))
            cy = int(rng.integers(0, max(1, h // 2)))
            yy, xx = np.ogrid[:h, :w]
            blob = np.exp(
                -(((xx - cx) ** 2) / (2 * 90**2) + ((yy - cy) ** 2) / (2 * 60**2))
            )
            canvas = np.maximum(canvas, (blob * 70.0)[..., None])
        frames.append(np.clip(canvas, 0, 255).astype(np.uint8))

    try:
        from moviepy import ImageSequenceClip  # type: ignore

        clip = ImageSequenceClip(frames, fps=fps)
        clip.write_videofile(
            str(path),
            fps=fps,
            codec="libx264",
            audio=False,
            preset="ultrafast",
            ffmpeg_params=["-pix_fmt", "yuv420p", "-crf", "23"],
            logger=None,
        )
        clip.close()
    except Exception as exc:  # noqa: BLE001
        npz = path.with_suffix(".npz")
        np.savez_compressed(npz, frames=np.stack(frames, axis=0), fps=fps)
        _LOG.warning("dust overlay mp4 failed (%s) — wrote %s", exc, npz)
        return npz

    _LOG.info("Generated dust overlay asset → %s", path)
    print(f"[LOFI assemble] generated dust overlay -> {path}")
    return path


_OVERLAY_FRAME_CACHE: dict[str, np.ndarray] = {}


def _load_overlay_frames(path: Path) -> tuple[np.ndarray, float]:
    """Return (frames[N,H,W,3] uint8, fps)."""
    key = str(path.resolve())
    if key in _OVERLAY_FRAME_CACHE:
        return _OVERLAY_FRAME_CACHE[key], float(lofi_cfg.REEL_FPS)
    if path.suffix.lower() == ".npz":
        data = np.load(str(path))
        frames = data["frames"]
        fps = float(data["fps"]) if "fps" in data else float(lofi_cfg.REEL_FPS)
        _OVERLAY_FRAME_CACHE[key] = frames
        return frames, fps
    from moviepy import VideoFileClip  # type: ignore

    clip = VideoFileClip(str(path))
    fps = float(clip.fps or lofi_cfg.REEL_FPS)
    frames_list = []
    dur = float(clip.duration or 0.1)
    for ft in np.arange(0, dur, 1.0 / max(fps, 1.0)):
        frames_list.append(clip.get_frame(min(ft, dur - 1e-3)))
    clip.close()
    frames = np.stack(frames_list, axis=0).astype(np.uint8)
    _OVERLAY_FRAME_CACHE[key] = frames
    return frames, fps


def apply_dust_overlay_screen(
    rgb: np.ndarray,
    *,
    t: float,
    overlay_frames: np.ndarray | None = None,
    overlay_getter=None,
    start_offset_s: float = 0.0,
    fps: float = 30.0,
    overlay_duration_s: float | None = None,
) -> np.ndarray:
    """Screen-blend reference overlay (below caption). Loop if short, trim if long."""
    if not bool(getattr(lofi_cfg, "ENABLE_DUST_OVERLAY", True)):
        return rgb
    op = float(getattr(lofi_cfg, "DUST_OVERLAY_OPACITY", 0.32))
    op = max(0.0, min(0.45, op))
    if op < 0.001:
        return rgb
    ov = None
    t_ov = t + start_offset_s
    if overlay_getter is not None:
        ov = overlay_getter(t_ov)
    elif overlay_frames is not None and len(overlay_frames) > 0:
        n = len(overlay_frames)
        idx = int((t_ov * fps) % n)
        ov = overlay_frames[idx]
    if ov is None:
        return rgb
    ov = np.asarray(ov)
    h, w = rgb.shape[:2]
    if ov.shape[0] != h or ov.shape[1] != w:
        ov = np.array(
            PILImage.fromarray(ov.astype(np.uint8)).resize((w, h), PILImage.BILINEAR),
            dtype=np.float32,
        )
    else:
        ov = ov.astype(np.float32)
    base = rgb.astype(np.float32)
    a = base / 255.0
    b = (ov / 255.0) * op
    out = 1.0 - (1.0 - a) * (1.0 - b)
    return np.clip(out * 255.0, 0, 255).astype(np.uint8)


def list_library_bgm_tracks(engine_root: Path) -> list[Path]:
    """Library BGM from wonder_feed/audio/bgm — excludes generated lofi_bed* files."""
    bgm_dir = Path(engine_root) / str(
        getattr(lofi_cfg, "BGM_DIR_REL", "channels_config/wonder_feed/audio/bgm")
    )
    if not bgm_dir.is_dir():
        return []
    exclude = tuple(
        getattr(lofi_cfg, "BGM_EXCLUDE_PREFIXES", ("lofi_bed",))
    )
    out: list[Path] = []
    for p in sorted(bgm_dir.glob("*.mp3")):
        if not p.is_file() or p.stat().st_size < 1000:
            continue
        name = p.name.lower()
        if any(name.startswith(pref.lower()) for pref in exclude):
            continue
        out.append(p)
    return out


def pick_library_bgm(engine_root: Path, *, seed: int | None = None) -> Path | None:
    tracks = list_library_bgm_tracks(engine_root)
    if not tracks:
        return None
    rng = np.random.default_rng(
        int(seed if seed is not None else np.random.randint(0, 2**31 - 1))
    )
    return Path(tracks[int(rng.integers(0, len(tracks)))])


def prep_base_frame(
    image_path: Path,
    *,
    shadow: tuple[int, int, int] | None = None,
    highlight: tuple[int, int, int] | None = None,
) -> np.ndarray:
    """
    Load + cover-fit to reel canvas.

    When ``LOFI_APPLY_GRADING`` is False (default), preserves Flux/riso palette —
    no duotone/LUT. Legacy grading path kept behind the flag only.
    """
    im = PILImage.open(image_path).convert("RGB")
    im = ImageOps.fit(
        im,
        (lofi_cfg.REEL_WIDTH, lofi_cfg.REEL_HEIGHT),
        method=PILImage.LANCZOS,
        centering=(0.5, 0.5),
    )
    arr = np.array(im)
    if bool(getattr(lofi_cfg, "ENABLE_HIGHLIGHT_CLAMP", True)):
        arr = clamp_baked_highlights(arr)
    if not bool(getattr(lofi_cfg, "LOFI_APPLY_GRADING", False)):
        return arr
    arr = apply_duotone(arr, shadow=shadow, highlight=highlight)
    pil = PILImage.fromarray(arr)
    pil = ImageEnhance.Contrast(pil).enhance(1.08)
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
        f"[LOFI grade] apply_duotone={'ACTIVE' if getattr(lofi_cfg, 'LOFI_APPLY_GRADING', False) else 'SKIPPED'} "
        f"(bands={lofi_cfg.DUOTONE_TONAL_BANDS}) | "
        f"mood={mood_id or 'custom'} | "
        f"shadow={sh} highlight={hi} | vignette={lofi_cfg.VIGNETTE_STRENGTH} | "
        f"source={image_path}"
    )
    _LOG.info(
        "LOFI grade_still_frame | grading=%s | mood=%s | %s",
        getattr(lofi_cfg, "LOFI_APPLY_GRADING", False),
        mood_id,
        image_path,
    )
    graded = prep_base_frame(image_path, shadow=sh, highlight=hi)
    return apply_film_grain(graded, seed=grain_seed, t=0.5)


def render_logo_layer(
    logo_path: Path | None,
    *,
    opacity: float,
    scale: float,
    bottom_px: int = 48,
) -> np.ndarray | None:
    if not logo_path or not Path(logo_path).is_file():
        return None
    try:
        logo = PILImage.open(logo_path).convert("RGBA")
    except Exception as exc:  # noqa: BLE001
        _LOG.warning("logo load failed: %s", exc)
        return None
    # Knock out near-black *background* (flood from corners) so a black-plate
    # PNG does not stamp a rectangle over the reel. Letterforms on the badge stay.
    arr = np.array(logo)
    h0, w0 = arr.shape[:2]
    lum = arr[..., :3].min(axis=2)
    is_bg = lum <= 22
    vis = np.zeros((h0, w0), dtype=bool)
    stack = []
    for y, x in ((0, 0), (0, w0 - 1), (h0 - 1, 0), (h0 - 1, w0 - 1)):
        if is_bg[y, x]:
            stack.append((y, x))
            vis[y, x] = True
    while stack:
        y, x = stack.pop()
        arr[y, x, 3] = 0
        if y > 0 and not vis[y - 1, x] and is_bg[y - 1, x]:
            vis[y - 1, x] = True
            stack.append((y - 1, x))
        if y + 1 < h0 and not vis[y + 1, x] and is_bg[y + 1, x]:
            vis[y + 1, x] = True
            stack.append((y + 1, x))
        if x > 0 and not vis[y, x - 1] and is_bg[y, x - 1]:
            vis[y, x - 1] = True
            stack.append((y, x - 1))
        if x + 1 < w0 and not vis[y, x + 1] and is_bg[y, x + 1]:
            vis[y, x + 1] = True
            stack.append((y, x + 1))
    logo = PILImage.fromarray(arr)
    target_w = max(1, int(lofi_cfg.REEL_WIDTH * float(scale)))
    ratio = target_w / max(1, logo.width)
    target_h = max(1, int(logo.height * ratio))
    logo = logo.resize((target_w, target_h), PILImage.LANCZOS)
    r, g, b, a = logo.split()
    a = a.point(lambda v: int(v * max(0.05, min(1.0, opacity))))
    logo = PILImage.merge("RGBA", (r, g, b, a))
    canvas = PILImage.new("RGBA", (lofi_cfg.REEL_WIDTH, lofi_cfg.REEL_HEIGHT), (0, 0, 0, 0))
    x = (lofi_cfg.REEL_WIDTH - target_w) // 2
    inset = max(8, int(bottom_px))
    y = lofi_cfg.REEL_HEIGHT - target_h - inset
    canvas.paste(logo, (x, y), logo)
    print(
        f"[LOFI assemble] logo={Path(logo_path).name} scale={scale:.3f} "
        f"px={target_w}x{target_h} pos=bottom_center y={y} inset={inset} every_scene=True"
    )
    return np.array(canvas)


def compute_caption_scene_duration_s(
    word_timings: Sequence[tuple[str, float, float]] | None,
    voice_path: Path | None,
    *,
    base_s: float | None = None,
    fade_s: float | None = None,
    hold_s: float | None = None,
) -> tuple[float, bool, dict[str, float]]:
    """
    Scene length must cover full caption reveal + a readable hold.

    Never shorter than *base_s* (default 3s). If speech needs more time,
    extend the scene instead of cutting mid-clause.
    """
    base = float(base_s if base_s is not None else lofi_cfg.SCENE_DURATION_S)
    fade = float(
        fade_s if fade_s is not None else getattr(lofi_cfg, "CAPTION_WORD_FADE_S", 0.20)
    )
    hold = float(
        hold_s if hold_s is not None else getattr(lofi_cfg, "CAPTION_HOLD_S", 0.45)
    )
    last_end = 0.0
    last_start = 0.0
    if word_timings:
        last_end = max(float(e) for _w, _s, e in word_timings)
        last_start = max(float(s) for _w, s, _e in word_timings)
    vo_dur = 0.0
    if voice_path is not None and Path(voice_path).is_file():
        try:
            from avatar_engine.audio_engine import _audio_file_duration_s

            vo_dur = float(_audio_file_duration_s(Path(voice_path)))
        except Exception:  # noqa: BLE001
            vo_dur = 0.0
    speech_end = max(last_end, last_start + fade, vo_dur)
    needed = speech_end + hold
    dur = max(base, needed)
    extended = dur > base + 0.04
    meta = {
        "base_s": base,
        "vo_dur": round(vo_dur, 3),
        "last_word_start": round(last_start, 3),
        "last_word_end": round(last_end, 3),
        "needed_s": round(needed, 3),
        "duration_s": round(dur, 3),
    }
    return round(dur, 3), extended, meta


def assemble_lofi_reel(
    scene_images: Sequence[Path],
    captions: Sequence[str],
    output_mp4: Path,
    *,
    engine_root: Path,
    page_id: str,
    scene_duration_s: float = lofi_cfg.SCENE_DURATION_S,
    scene_durations: Sequence[float] | None = None,
    moods: Sequence[dict[str, Any] | str | None] | None = None,
    caption_style: str | None = None,
    bgm_path: Path | None = None,
    voice_paths: Sequence[Path | None] | None = None,
    word_timings_per_scene: Sequence[Sequence[tuple[str, float, float]] | None] | None = None,
) -> Path:
    """
    Compile Wonder Feed LOFI relationship/reflection reels.

    Stack: base (no crushing LUT) → light pulse → uniform grain → dust →
    film multiply (behind text) → word-fade caption → logo.
    Mixes per-scene VO + library BGM. Does not affect Ancient Knowledge.
    """
    try:
        from moviepy import (  # type: ignore
            AudioFileClip,
            CompositeAudioClip,
            VideoClip,
            concatenate_audioclips,
            concatenate_videoclips,
        )
    except ImportError as exc:
        raise RuntimeError(
            "assembler requires moviepy>=2.0, numpy, Pillow.\n"
            f"Original error: {exc}"
        ) from exc

    if len(scene_images) != len(captions):
        raise ValueError("scene_images and captions length mismatch")
    if not scene_images:
        raise ValueError("no scenes to assemble")

    n_scenes = len(scene_images)
    scene_durs: list[float] = []
    for i in range(n_scenes):
        preset = None
        if scene_durations is not None and i < len(scene_durations):
            preset = float(scene_durations[i])
        vp = None
        if voice_paths is not None and i < len(voice_paths):
            vp = voice_paths[i]
        timings_i = None
        if word_timings_per_scene is not None and i < len(word_timings_per_scene):
            timings_i = word_timings_per_scene[i]
        dur_i, extended_i, dur_meta = compute_caption_scene_duration_s(
            timings_i,
            Path(vp) if vp else None,
            base_s=scene_duration_s,
        )
        if preset is not None:
            dur_i = max(dur_i, preset)
        scene_durs.append(dur_i)
        cap_preview = str(captions[i] if i < len(captions) else "")
        flag = " EXTENDED" if extended_i or (preset is not None and preset > scene_duration_s + 0.04) else ""
        print(
            f"[LOFI caption-timing] scene={i + 1}{flag} dur={dur_i:.2f}s "
            f"vo={dur_meta['vo_dur']:.2f}s last_word_end={dur_meta['last_word_end']:.2f}s "
            f"needed={dur_meta['needed_s']:.2f}s text={cap_preview!r}"
        )
        if flag:
            print(
                f"[LOFI caption-timing] FLAG scene {i + 1} extended past "
                f"{scene_duration_s:.1f}s so the line can fully display + hold"
            )

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
            opacity=float(cfg.get("logo_opacity", 0.85)),
            scale=float(cfg.get("logo_scale", 0.14)),
            bottom_px=int(cfg.get("logo_bottom_px", 48)),
        )
        if logo_layer is None:
            print(f"[LOFI assemble] WARN logo missing path={logo_path}")

    clips = []
    z0 = float(lofi_cfg.KEN_BURNS_ZOOM_START)
    z_cap = float(getattr(lofi_cfg, "KEN_BURNS_ZOOM_CAP", 1.05))
    z1 = min(z_cap, float(lofi_cfg.KEN_BURNS_ZOOM_END))
    pan_amp_x = float(getattr(lofi_cfg, "KEN_BURNS_PAN_AMP_X", 5.0))
    pan_amp_y = float(getattr(lofi_cfg, "KEN_BURNS_PAN_AMP_Y", 3.0))
    enable_pan = bool(getattr(lofi_cfg, "ENABLE_KINETIC_PAN", True))
    grading_on = bool(getattr(lofi_cfg, "LOFI_APPLY_GRADING", False))

    # Reference film-grain overlay — loop/trim to final duration; Screen below caption
    overlay_getter = None
    overlay_clip = None
    overlay_start = 0.0
    overlay_fps = float(lofi_cfg.REEL_FPS)
    if bool(getattr(lofi_cfg, "ENABLE_DUST_OVERLAY", True)):
        try:
            from moviepy import VideoFileClip as _VFC  # type: ignore

            ov_path = ensure_dust_overlay_asset(Path(engine_root))
            overlay_clip = _VFC(str(ov_path))
            ov_dur = float(overlay_clip.duration or 1.0)
            overlay_fps = float(overlay_clip.fps or lofi_cfg.REEL_FPS)
            overlay_start = 0.0
            def overlay_getter(t, _clip=overlay_clip, _dur=ov_dur):
                u = float(t) % max(_dur, 0.001)
                u = min(max(0.0, u), _dur - 1e-3)
                return _clip.get_frame(u)
            print(
                f"[LOFI assemble] grain_overlay={ov_path.name} "
                f"src_dur={ov_dur:.2f}s loop_or_trim=True screen_op="
                f"{getattr(lofi_cfg, 'DUST_OVERLAY_OPACITY', 0.32)} below_caption=True"
            )
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("grain overlay load failed: %s", exc)
            print(f"[LOFI assemble] WARN grain overlay failed: {exc}")
            overlay_clip = None
            overlay_getter = None

    # Brightness-factor debug table (independent of zoom) at 0.5s steps
    n_est = len(scene_images)
    total_est = float(sum(scene_durs))
    pulse_rows: list[dict[str, float]] = []
    if bool(getattr(lofi_cfg, "LIGHT_BREATH_DEBUG", True)):
        print("[LOFI pulse] brightness_factor vs time (independent of Ken Burns zoom)")
        t_s = 0.0
        while t_s <= total_est + 0.001:
            bf = light_pulse_brightness_factor(t_s, seed=3)
            pulse_rows.append({"t": round(t_s, 3), "brightness_factor": round(bf, 5)})
            print(f"  t={t_s:5.2f}s  brightness_factor={bf:.4f}")
            t_s += 0.5

    print(
        f"[LOFI assemble] grading={grading_on} | "
        f"light_pulse={lofi_cfg.ENABLE_LIGHT_BREATH} amp={lofi_cfg.LIGHT_BREATH_AMP} | "
        f"procedural_grain={getattr(lofi_cfg, 'ENABLE_PROCEDURAL_GRAIN', False)} | "
        f"multiply={getattr(lofi_cfg, 'CAPTION_FILM_MULTIPLY_OPACITY', 0):.2f} | "
        f"vignette={getattr(lofi_cfg, 'ENABLE_VIGNETTE', True)}:"
        f"{getattr(lofi_cfg, 'VIGNETTE_STRENGTH', 0):.2f} | "
        f"kenburns={z0:.3f}->{z1:.3f} ease | pan={enable_pan} "
        f"amp=({pan_amp_x:.0f},{pan_amp_y:.0f}) | "
        f"highlight_clamp={getattr(lofi_cfg, 'ENABLE_HIGHLIGHT_CLAMP', True)} | "
        f"caption={style} size_frac={getattr(lofi_cfg, 'CAPTION_LINE_HEIGHT_FRAC', 0)} "
        f"size_px={int(lofi_cfg.REEL_HEIGHT * float(getattr(lofi_cfg, 'CAPTION_LINE_HEIGHT_FRAC', 0.034)))} "
        f"word_fade=True | voice={getattr(lofi_cfg, 'LOFI_VOICE_ID', '')} "
        f"speed={getattr(lofi_cfg, 'LOFI_VOICE_SPEED', None)}"
    )

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
        timings: Sequence[tuple[str, float, float]] | None = None
        if word_timings_per_scene is not None and idx < len(word_timings_per_scene):
            timings = word_timings_per_scene[idx]
        h, w = base.shape[:2]
        this_dur = float(scene_durs[idx])
        t_offset = float(sum(scene_durs[:idx]))
        # Alternate pan/zoom direction per scene (still slow ease)
        pan_sign = 1.0 if (idx % 2 == 0) else -1.0
        zoom_out = idx % 2 == 1

        def _make_frame(
            t,
            _base=base,
            _logo=logo_layer,
            _idx=idx,
            _caption=str(caption),
            _timings=timings,
            _t_offset=t_offset,
            _dur=this_dur,
            _pan_sign=pan_sign,
            _zoom_out=zoom_out,
            _ov_get=overlay_getter,
            _ov_start=overlay_start,
        ):
            # Ease-in-out across FULL scene duration — slow drift
            u = max(0.0, min(1.0, t / max(_dur, 0.001)))
            ease = _smootherstep01(u)
            if _zoom_out:
                zoom = z1 - (z1 - z0) * ease
            else:
                zoom = z0 + (z1 - z0) * ease
            zoom = max(1.0, min(z_cap, zoom))
            crop_w = int(w / zoom)
            crop_h = int(h / zoom)
            # Kinetic pan — same ease, subtle amplitude
            if enable_pan:
                pan_e = math.sin(ease * math.pi)
                ox = int(_pan_sign * pan_amp_x * pan_e)
                oy = int((-_pan_sign) * pan_amp_y * pan_e * 0.6)
            else:
                ox = oy = 0
            x0 = (w - crop_w) // 2 + ox
            y0 = (h - crop_h) // 2 + oy
            x0 = max(0, min(w - crop_w, x0))
            y0 = max(0, min(h - crop_h, y0))
            cropped = _base[y0 : y0 + crop_h, x0 : x0 + crop_w]
            frame = np.array(
                PILImage.fromarray(cropped).resize((w, h), PILImage.LANCZOS)
            )
            t_global = _t_offset + float(t)
            # Stack: light → vignette → grain overlay (screen) → multiply → caption → logo
            frame = apply_animated_light_pulse(frame, t=t_global, seed=3)
            if bool(getattr(lofi_cfg, "ENABLE_PROCEDURAL_GRAIN", False)):
                frame = apply_film_grain(frame, seed=17, t=t_global)
            frame = apply_vignette(frame)
            frame = apply_dust_overlay_screen(
                frame,
                t=t_global,
                overlay_getter=_ov_get,
                start_offset_s=_ov_start,
                fps=overlay_fps,
            )
            if bool(getattr(lofi_cfg, "ENABLE_DUST_PARTICLES", False)):
                frame = apply_dust_particles(frame, t=t_global, seed=11)
            frame = apply_caption_film_multiply(frame, seed=41, t=0.0)

            cap = render_lofi_caption_layer_word_fade(
                _caption,
                _timings,
                float(t),
                engine_root=engine_root,
                style=style,
                scene_duration_s=_dur,
            )
            rgba = PILImage.fromarray(frame).convert("RGBA")
            rgba = PILImage.alpha_composite(rgba, cap)
            if _logo is not None:
                rgba = PILImage.alpha_composite(rgba, PILImage.fromarray(_logo))
            return np.array(rgba.convert("RGB"))

        clip = VideoClip(frame_function=_make_frame, duration=float(this_dur))
        clip = clip.with_fps(lofi_cfg.REEL_FPS)
        clips.append(clip)

    final = concatenate_videoclips(clips, method="compose")
    total_dur = float(final.duration or sum(scene_durs))

    # ── Audio: per-scene VO (padded to scene_duration) + library BGM ─────────
    audio_clips_to_close: list[Any] = []
    mixed = None
    vo_parts: list[Any] = []
    if voice_paths:
        for idx, vp in enumerate(voice_paths):
            slot = float(scene_durs[idx] if idx < len(scene_durs) else scene_duration_s)
            if vp is None or not Path(vp).is_file():
                # Silence for this scene slot
                try:
                    from moviepy.audio.AudioClip import AudioClip as _AC  # type: ignore

                    silence = _AC(
                        lambda t: 0,
                        duration=float(slot),
                        fps=44100,
                    )
                    vo_parts.append(silence)
                    audio_clips_to_close.append(silence)
                except Exception:  # noqa: BLE001
                    continue
                continue
            vac = AudioFileClip(str(vp))
            audio_clips_to_close.append(vac)
            vdur = float(vac.duration or 0.0)
            if vdur > slot + 0.05:
                print(
                    f"[LOFI assemble] WARN scene {idx + 1} VO {vdur:.2f}s > "
                    f"slot {slot:.2f}s — keeping full VO (no trim)"
                )
            elif vdur < slot - 0.05:
                try:
                    from moviepy import concatenate_audioclips as _cat  # type: ignore
                    from moviepy.audio.AudioClip import AudioClip as _AC  # type: ignore

                    pad = _AC(
                        lambda t: 0,
                        duration=float(slot - vdur),
                        fps=44100,
                    )
                    vac = _cat([vac, pad])
                    audio_clips_to_close.append(pad)
                except Exception:  # noqa: BLE001
                    pass
            vac = vac.with_volume_scaled(
                float(getattr(lofi_cfg, "LOFI_VOICE_VOLUME", 1.0))
            )
            vo_parts.append(vac)

    bgm = bgm_path or pick_library_bgm(
        engine_root, seed=hash(str(output_mp4)) % (2**31)
    )
    bac = None
    if bgm is None or not Path(bgm).is_file():
        msg = (
            f"LOFI BGM required but no library .mp3 found under "
            f"{Path(engine_root) / lofi_cfg.BGM_DIR_REL}"
        )
        if bool(getattr(lofi_cfg, "REQUIRE_BGM", True)):
            raise RuntimeError(msg)
        _LOG.warning(msg)
    else:
        bac = AudioFileClip(str(bgm))
        audio_clips_to_close.append(bac)
        bgm_dur = float(bac.duration or 0.0)
        if bgm_dur > total_dur + 0.05:
            max_start = max(0.0, bgm_dur - total_dur)
            start = float(
                np.random.default_rng(
                    hash(str(output_mp4) + Path(bgm).name) % (2**31)
                ).random()
                * max_start
            )
            bac = bac.subclipped(start, start + total_dur)
        elif bgm_dur < total_dur - 0.05:
            loops = int(math.ceil(total_dur / max(bgm_dur, 0.1))) + 1
            bac = concatenate_audioclips([bac] * max(1, loops))
            bac = bac.subclipped(0, total_dur)
        else:
            bac = bac.subclipped(0, total_dur)
        bac = bac.with_volume_scaled(float(getattr(lofi_cfg, "BGM_VOLUME", 0.38)))
        print(
            f"[LOFI assemble] BGM={Path(bgm).name} "
            f"vol={getattr(lofi_cfg, 'BGM_VOLUME', 0.38)} trimmed={total_dur:.1f}s"
        )

    audio_attached = False
    try:
        if vo_parts:
            vo_full = concatenate_audioclips(vo_parts)
            if float(vo_full.duration or 0) > total_dur + 0.05:
                vo_full = vo_full.subclipped(0, total_dur)
            audio_clips_to_close.append(vo_full)
            layers = [vo_full]
            if bac is not None:
                layers.append(bac)
            mixed = CompositeAudioClip(layers).with_duration(total_dur)
            final = final.with_audio(mixed)
            audio_attached = True
            print(f"[LOFI assemble] VO scenes={len(vo_parts)} + BGM mixed")
        elif bac is not None:
            final = final.with_audio(bac)
            audio_attached = True
            print("[LOFI assemble] BGM only (no VO paths)")
        elif bool(getattr(lofi_cfg, "REQUIRE_VOICEOVER", False)):
            raise RuntimeError("LOFI voiceover required but no VO clips were provided")
    except Exception as exc:  # noqa: BLE001
        if bool(getattr(lofi_cfg, "REQUIRE_BGM", True)) or bool(
            getattr(lofi_cfg, "REQUIRE_VOICEOVER", False)
        ):
            raise RuntimeError(f"LOFI audio mix failed: {exc}") from exc
        _LOG.warning("audio mix failed: %s", exc)

    output_mp4 = Path(output_mp4)
    output_mp4.parent.mkdir(parents=True, exist_ok=True)
    try:
        final.write_videofile(
            str(output_mp4),
            fps=lofi_cfg.REEL_FPS,
            codec="libx264",
            audio=audio_attached,
            audio_codec="aac" if audio_attached else None,
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
        for ac in audio_clips_to_close:
            try:
                ac.close()
            except Exception:  # noqa: BLE001
                pass
        if mixed is not None:
            try:
                mixed.close()
            except Exception:  # noqa: BLE001
                pass
        if overlay_clip is not None:
            try:
                overlay_clip.close()
            except Exception:  # noqa: BLE001
                pass

    if pulse_rows:
        try:
            import json as _json

            dbg = Path(output_mp4).with_name(Path(output_mp4).stem + "_pulse_debug.json")
            dbg.write_text(
                _json.dumps(
                    {
                        "amp": float(lofi_cfg.LIGHT_BREATH_AMP),
                        "period_s": float(lofi_cfg.LIGHT_BREATH_PERIOD_S),
                        "note": "brightness_factor is independent of Ken Burns zoom",
                        "samples": pulse_rows,
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            print(f"[LOFI pulse] debug written -> {dbg}")
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("pulse debug write failed: %s", exc)

    _LOG.info("LOFI reel assembled -> %s (%.1fs)", output_mp4, total_dur)
    print(f"[LOFI assemble] wrote {output_mp4}")
    return output_mp4

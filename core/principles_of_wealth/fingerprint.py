# -*- coding: utf-8 -*-
"""Wealth wrappers around the generic uniqueness engine + thumbnail re-sign.

Video uniqueness is delegated to ``core.utils.fingerprint_engine`` so this
module only handles Processed/ naming, skip-existing, and thumbnail EXIF wipe.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

from core.utils.fingerprint_engine import apply_video_uniqueness

_LOG = logging.getLogger(__name__)


def _processed_name(src: Path, *, kind: str) -> str:
    stem = src.stem
    if kind == "thumb":
        return f"{stem}_signed.png"
    return f"{stem}_signed.mp4"


def regenerate_fingerprint(
    input_path: str | Path,
    output_path: str | Path,
    *,
    hwaccel: bool = True,
    hw_encode: bool = False,
    crf: int = 23,
    preset: str = "ultrafast",
) -> Path:
    """Re-encode *input_path* via the generic uniqueness engine."""
    print(f"[Wealth] Processing (fast uniqueness): {Path(input_path).name}")
    return Path(
        apply_video_uniqueness(
            str(input_path),
            str(output_path),
            {
                "hwaccel": hwaccel,
                "hw_encode": hw_encode,
                "crf": crf,
                "preset": preset,
            },
        )
    )


def resign_thumbnail(
    input_path: str | Path,
    output_path: str | Path,
    *,
    noise_opacity: float = 0.01,
) -> Path:
    """Add a 1% opacity noise layer and save without EXIF metadata."""
    from PIL import Image, ImageEnhance, ImageFilter
    import numpy as np

    src = Path(input_path)
    dest = Path(output_path)
    if not src.is_file():
        raise FileNotFoundError(src)
    dest.parent.mkdir(parents=True, exist_ok=True)

    img = None
    last_exc: Exception | None = None
    for attempt in range(1, 4):
        try:
            with Image.open(src) as opened:
                img = opened.convert("RGB")
            break
        except OSError as exc:
            last_exc = exc
            _LOG.warning(
                "Thumbnail open failed for %s (attempt %s/3): %s",
                src.name,
                attempt,
                exc,
            )
    if img is None:
        raise OSError(f"Could not read thumbnail {src}: {last_exc}") from last_exc
    arr = np.asarray(img, dtype=np.float32)
    rng = np.random.default_rng()
    noise = rng.integers(0, 256, size=arr.shape, dtype=np.uint8).astype(np.float32)
    opacity = max(0.0, min(0.08, float(noise_opacity)))
    mixed = np.clip(arr * (1.0 - opacity) + noise * opacity, 0, 255).astype(np.uint8)
    out = Image.fromarray(mixed, mode="RGB")
    # Tiny extra structural change without a visible quality hit.
    out = ImageEnhance.Sharpness(out).enhance(1.02)
    out = out.filter(ImageFilter.UnsharpMask(radius=0.6, percent=8, threshold=3))
    dest_png = dest.with_suffix(".png")
    out.save(dest_png, format="PNG", optimize=True)
    _LOG.info("Thumbnail re-signed | %s → %s", src.name, dest_png.name)
    print(f"[Wealth] Thumbnail re-signed: {src.name}")
    return dest_png


def already_processed(dest: Path) -> bool:
    return dest.is_file() and dest.stat().st_size > 0


def default_processed_path(
    src: Path,
    processed_dir: Path,
    *,
    kind: str,
) -> Path:
    return processed_dir / _processed_name(src, kind=kind)


def process_pair(
    src: Optional[str | Path],
    processed_dir: Path,
    *,
    kind: str,
    skip_existing: bool = True,
    hwaccel: bool = True,
    hw_encode: bool = False,
) -> str:
    """Process one video or thumbnail. Returns the output path (or '' if no src)."""
    if not src:
        return ""
    src_path = Path(src)
    dest = default_processed_path(src_path, processed_dir, kind=kind)
    if skip_existing and already_processed(dest):
        print(f"[Wealth] Skip existing: {dest.name}")
        return str(dest)
    if kind == "thumb":
        return str(resign_thumbnail(src_path, dest))
    return str(
        regenerate_fingerprint(
            src_path,
            dest,
            hwaccel=hwaccel,
            hw_encode=hw_encode,
        )
    )


def process_shorts(
    sources: list[str],
    processed_dir: Path,
    *,
    skip_existing: bool = True,
    hwaccel: bool = True,
    hw_encode: bool = False,
) -> list[str]:
    """Re-sign every Short in *sources* into Processed/ with the ``_signed`` suffix."""
    out: list[str] = []
    for i, src in enumerate(sources, start=1):
        print(f"[Wealth] Short {i}/{len(sources)}")
        signed = process_pair(
            src,
            processed_dir,
            kind="short",
            skip_existing=skip_existing,
            hwaccel=hwaccel,
            hw_encode=hw_encode,
        )
        if signed:
            out.append(signed)
    return out


def env_hw_encode() -> bool:
    return os.getenv("WEALTH_HW_ENCODE", "").strip().lower() in {"1", "true", "yes"}

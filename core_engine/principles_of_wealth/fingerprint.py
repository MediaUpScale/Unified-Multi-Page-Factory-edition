# -*- coding: utf-8 -*-
"""Uniqueness pass for library assets (video bitstream + thumbnail signature).

Re-encodes with FFmpeg ``ultrafast`` (optional GPU decode) so the machine is
not frozen by a from-scratch render. Applies a 2-pixel crop, 0.5% timing
shift, and strips container metadata. Thumbnails get a 1% opacity noise
layer and are saved without EXIF.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional

_LOG = logging.getLogger(__name__)


def resolve_ffmpeg() -> str:
    ff = shutil.which("ffmpeg")
    if ff:
        return ff
    try:
        import imageio_ffmpeg  # type: ignore

        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and Path(exe).is_file():
            return str(exe)
    except Exception:  # noqa: BLE001
        pass
    for cand in (
        Path.home() / "AppData/Local/Programs/Stremio/ffmpeg.exe",
        Path(r"C:\ffmpeg\bin\ffmpeg.exe"),
    ):
        if cand.is_file():
            return str(cand)
    raise RuntimeError(
        "ffmpeg not found on PATH, imageio_ffmpeg, or common install paths."
    )


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
    """Re-encode *input_path* so container + bitstream hashes change.

    Visual change is a 2-pixel crop and 0.5% presentation-timestamp shift.
    Audio is resampled at 1.005× then returned to 44.1 kHz.
    """
    src = Path(input_path)
    dest = Path(output_path)
    if not src.is_file():
        raise FileNotFoundError(src)
    if src.stat().st_size <= 0:
        raise ValueError(f"Refusing to process empty file: {src}")

    dest.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg = resolve_ffmpeg()

    video_codec = ["-c:v", "h264_nvenc", "-preset", "p1", "-cq", str(crf)] if hw_encode else [
        "-c:v",
        "libx264",
        "-preset",
        preset,
        "-crf",
        str(crf),
    ]

    def _build(use_hwaccel: bool) -> list[str]:
        cmd = [ffmpeg, "-y"]
        if use_hwaccel:
            cmd.extend(["-hwaccel", "auto"])
        cmd.extend(
            [
                "-i",
                str(src),
                "-vf",
                "crop=in_w-2:in_h-2,setpts=0.995*PTS",
                "-af",
                "asetrate=44100*1.005,aresample=44100",
                *video_codec,
                "-c:a",
                "aac",
                "-b:a",
                "128k",
                "-map_metadata",
                "-1",
                "-movflags",
                "+faststart",
                str(dest),
            ]
        )
        return cmd

    _LOG.info("Uniqueness pass | %s → %s", src.name, dest)
    print(f"[Wealth] Processing (fast uniqueness): {src.name}")
    cmd = _build(hwaccel)
    result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    if result.returncode != 0 and hwaccel:
        _LOG.warning(
            "hwaccel uniqueness pass failed for %s — retrying software decode.",
            src.name,
        )
        cmd = _build(False)
        result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    if result.returncode != 0:
        err = (result.stderr or b"").decode("utf-8", errors="replace")[-800:]
        raise RuntimeError(f"ffmpeg uniqueness pass failed for {src.name}:\n{err}")
    if not dest.is_file() or dest.stat().st_size <= 0:
        raise RuntimeError(f"ffmpeg wrote no output for {src.name}")
    return dest


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

    img = Image.open(src).convert("RGB")
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


def env_hw_encode() -> bool:
    return os.getenv("WEALTH_HW_ENCODE", "").strip().lower() in {"1", "true", "yes"}

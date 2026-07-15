# -*- coding: utf-8 -*-
"""
HYBRID_VIDEO format converter — Ken Burns zoom loop renderer.

Converts a generated still image into a 7-second slow-zoom MP4 loop using
a Ken Burns effect (gradual zoom-in or zoom-out). Activated when the pipeline
runs with --format HYBRID_VIDEO.

Requirements
------------
This module requires either:
  - ffmpeg on PATH (preferred — GPU-accelerated, fastest)
  - OR opencv-python (cv2) as a Python-only fallback (pip install opencv-python)
  - OR Pillow + imageio[ffmpeg] as a second fallback (pip install imageio[ffmpeg])

If none of the above are available, make_zoom_loop() raises ImportError with
a clear installation message, and the main pipeline degrades gracefully (skips
the video step and logs a WARNING).

Output
------
Writes a file alongside the source image with the same stem but .mp4 extension.
Example: castor_oil_v01_20260530_123456Z.png → castor_oil_v01_20260530_123456Z_loop.mp4

Ken Burns parameters (configurable)
------------------------------------
ZOOM_SCALE_START : float  — starting zoom factor (1.0 = native resolution)
ZOOM_SCALE_END   : float  — ending zoom factor (1.08 = 8% zoom-in)
FPS              : int    — output frames per second
DURATION_SECONDS : int    — default loop duration (7 seconds per spec)
"""
from __future__ import annotations

import logging
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Zoom effect parameters
# ---------------------------------------------------------------------------
ZOOM_SCALE_START: float = 1.00   # Start at native crop (no zoom)
ZOOM_SCALE_END: float = 1.08     # End 8% zoomed in (subtle Ken Burns)
FPS: int = 25
DEFAULT_DURATION_SECONDS: int = 7


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def make_zoom_loop(
    image_path: Path,
    *,
    duration_seconds: int = DEFAULT_DURATION_SECONDS,
    fps: int = FPS,
    zoom_start: float = ZOOM_SCALE_START,
    zoom_end: float = ZOOM_SCALE_END,
    output_path: Path | None = None,
) -> Path:
    """
    Convert a still image into a slow Ken Burns zoom MP4 loop.

    Attempts renderers in order:
      1. ffmpeg subprocess (fastest, requires ffmpeg on PATH)
      2. opencv-python (Python-only fallback)
      3. imageio + Pillow (last resort)

    Parameters
    ----------
    image_path:
        Absolute path to the source PNG/JPEG image.
    duration_seconds:
        Length of the output video in seconds. Default: 7 (per spec).
    fps:
        Frames per second. Default: 25.
    zoom_start:
        Initial crop scale factor. 1.0 = full frame. Values > 1.0 zoom in.
    zoom_end:
        Final crop scale factor. Values > zoom_start produce a zoom-in effect.
    output_path:
        Explicit output path. If None, writes alongside source image with
        '_loop.mp4' suffix.

    Returns
    -------
    Path
        Absolute path to the written MP4 file.

    Raises
    ------
    ImportError
        If no compatible video renderer is available.
    RuntimeError
        If rendering fails across all available backends.
    """
    image_path = Path(image_path).resolve()
    if not image_path.is_file():
        raise FileNotFoundError(f"Source image not found: {image_path}")

    if output_path is None:
        output_path = image_path.parent / (image_path.stem + "_loop.mp4")
    output_path = Path(output_path).resolve()

    total_frames = duration_seconds * fps

    logger.info(
        "HYBRID_VIDEO | converting %s → %s | %ds @ %dfps | zoom %.2f→%.2f",
        image_path.name,
        output_path.name,
        duration_seconds,
        fps,
        zoom_start,
        zoom_end,
    )

    errors: list[str] = []

    # ------------------------------------------------------------------
    # Backend 1: ffmpeg subprocess
    # ------------------------------------------------------------------
    try:
        result = _render_with_ffmpeg(
            image_path,
            output_path=output_path,
            duration_seconds=duration_seconds,
            fps=fps,
            zoom_start=zoom_start,
            zoom_end=zoom_end,
        )
        logger.info("HYBRID_VIDEO | ffmpeg render complete: %s", result.name)
        return result
    except _FfmpegNotFoundError:
        logger.debug("ffmpeg not found on PATH; trying opencv fallback.")
        errors.append("ffmpeg: not found on PATH")
    except Exception as exc:  # noqa: BLE001
        logger.warning("ffmpeg render failed (%s); trying opencv fallback.", exc)
        errors.append(f"ffmpeg: {exc}")

    # ------------------------------------------------------------------
    # Backend 2: opencv-python
    # ------------------------------------------------------------------
    try:
        result = _render_with_opencv(
            image_path,
            output_path=output_path,
            total_frames=total_frames,
            fps=fps,
            zoom_start=zoom_start,
            zoom_end=zoom_end,
        )
        logger.info("HYBRID_VIDEO | opencv render complete: %s", result.name)
        return result
    except ImportError:
        logger.debug("opencv-python (cv2) not installed; trying imageio fallback.")
        errors.append("opencv: not installed (pip install opencv-python)")
    except Exception as exc:  # noqa: BLE001
        logger.warning("opencv render failed (%s); trying imageio fallback.", exc)
        errors.append(f"opencv: {exc}")

    # ------------------------------------------------------------------
    # Backend 3: imageio + Pillow
    # ------------------------------------------------------------------
    try:
        result = _render_with_imageio(
            image_path,
            output_path=output_path,
            total_frames=total_frames,
            fps=fps,
            zoom_start=zoom_start,
            zoom_end=zoom_end,
        )
        logger.info("HYBRID_VIDEO | imageio render complete: %s", result.name)
        return result
    except ImportError:
        errors.append(
            "imageio: not installed (pip install imageio[ffmpeg]). "
            "Install at least one of: ffmpeg (system), opencv-python, or imageio[ffmpeg]."
        )
    except Exception as exc:  # noqa: BLE001
        errors.append(f"imageio: {exc}")

    # All backends failed.
    error_detail = " | ".join(errors)
    raise RuntimeError(
        f"HYBRID_VIDEO render failed — no working backend found. "
        f"Errors: {error_detail}. "
        f"Install ffmpeg (https://ffmpeg.org/download.html) for the fastest conversion."
    )


# ---------------------------------------------------------------------------
# Renderer backends (private)
# ---------------------------------------------------------------------------

class _FfmpegNotFoundError(RuntimeError):
    pass


def _render_with_ffmpeg(
    image_path: Path,
    output_path: Path,
    *,
    duration_seconds: int,
    fps: int,
    zoom_start: float,
    zoom_end: float,
) -> Path:
    """
    Render using ffmpeg's zoompan filter.

    The zoompan expression animates scale from zoom_start to zoom_end linearly
    across all frames. Output is H.264 in an MP4 container, optimised for
    social media upload (yuv420p pixel format).
    """
    try:
        probe = subprocess.run(
            ["ffmpeg", "-version"],
            capture_output=True,
            timeout=5,
        )
        if probe.returncode != 0:
            raise _FfmpegNotFoundError("ffmpeg returned non-zero on --version")
    except FileNotFoundError as exc:
        raise _FfmpegNotFoundError("ffmpeg executable not found on PATH") from exc

    total_frames = duration_seconds * fps

    # zoompan expression: linear interpolation from zoom_start to zoom_end.
    # z=... : zoom expression per frame
    # x=(iw-iw/zoom)/2 : centre the crop horizontally
    # y=(ih-ih/zoom)/2 : centre the crop vertically
    # d=1 : duration of zoom per "input frame" (we use a single still image)
    zoom_expr = (
        f"z={zoom_start:.4f}+({zoom_end:.4f}-{zoom_start:.4f})*on/{total_frames}"
    )

    cmd = [
        "ffmpeg", "-y",
        "-loop", "1",
        "-i", str(image_path),
        "-vf", (
            f"zoompan='{zoom_expr}':x='(iw-iw/zoom)/2':y='(ih-ih/zoom)/2'"
            f":d={total_frames}:s=iw:fps={fps}"
        ),
        "-t", str(duration_seconds),
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-preset", "fast",
        "-crf", "20",
        str(output_path),
    ]

    result = subprocess.run(cmd, capture_output=True, timeout=120)
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"ffmpeg exited {result.returncode}: {stderr}")

    return output_path


def _render_with_opencv(
    image_path: Path,
    output_path: Path,
    *,
    total_frames: int,
    fps: int,
    zoom_start: float,
    zoom_end: float,
) -> Path:
    """Render Ken Burns effect using opencv-python's VideoWriter."""
    import cv2  # type: ignore
    import numpy as np  # type: ignore

    img = cv2.imread(str(image_path))
    if img is None:
        raise RuntimeError(f"cv2.imread failed to load {image_path}")

    h, w = img.shape[:2]
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(output_path), fourcc, fps, (w, h))

    try:
        for frame_idx in range(total_frames):
            t = frame_idx / max(total_frames - 1, 1)
            scale = zoom_start + (zoom_end - zoom_start) * t

            # Compute the cropped region size.
            crop_w = int(w / scale)
            crop_h = int(h / scale)
            x0 = (w - crop_w) // 2
            y0 = (h - crop_h) // 2

            cropped = img[y0: y0 + crop_h, x0: x0 + crop_w]
            resized = cv2.resize(cropped, (w, h), interpolation=cv2.INTER_LINEAR)
            writer.write(resized)
    finally:
        writer.release()

    if not output_path.is_file():
        raise RuntimeError("opencv VideoWriter produced no output file.")
    return output_path


def _render_with_imageio(
    image_path: Path,
    output_path: Path,
    *,
    total_frames: int,
    fps: int,
    zoom_start: float,
    zoom_end: float,
) -> Path:
    """Render Ken Burns effect using imageio + Pillow."""
    import imageio  # type: ignore
    from PIL import Image

    img = Image.open(image_path).convert("RGB")
    w, h = img.size

    with imageio.get_writer(str(output_path), fps=fps, codec="libx264", quality=7) as writer:
        for frame_idx in range(total_frames):
            t = frame_idx / max(total_frames - 1, 1)
            scale = zoom_start + (zoom_end - zoom_start) * t

            crop_w = int(w / scale)
            crop_h = int(h / scale)
            x0 = (w - crop_w) // 2
            y0 = (h - crop_h) // 2

            cropped = img.crop((x0, y0, x0 + crop_w, y0 + crop_h))
            frame = cropped.resize((w, h), Image.LANCZOS)
            import numpy as np  # type: ignore
            writer.append_data(np.array(frame))

    if not output_path.is_file():
        raise RuntimeError("imageio produced no output file.")
    return output_path

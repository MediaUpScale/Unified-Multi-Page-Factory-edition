# -*- coding: utf-8 -*-
"""Standalone FFmpeg uniqueness pass — no channel or page logic.

Re-encodes a video so container + bitstream hashes change while the picture
stays visually identical: metadata strip, 2 px crop, micro color jitter,
0.5% presentation-timestamp shift, and a matching audio rate nudge.

CLI
---
    python core/utils/fingerprint_engine.py --input path/to/video.mp4 --output path/to/signed.mp4
"""
from __future__ import annotations

import argparse
import logging
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

_LOG = logging.getLogger(__name__)

_DEFAULT_OPTIONS: dict[str, Any] = {
    "hwaccel": True,
    "hw_encode": False,
    "crf": 23,
    "preset": "ultrafast",
    "crop_px": 2,
    "speed": 0.995,
    "audio_rate_mul": 1.005,
    "sample_rate": 44100,
    "audio_bitrate": "128k",
    "strip_metadata": True,
    "color_jitter": True,
    "brightness": 0.003,
    "contrast": 1.004,
    "saturation": 1.006,
}


def resolve_ffmpeg() -> str:
    """Return an ffmpeg executable path (PATH, imageio_ffmpeg, or common installs)."""
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


def _merged_options(options: Optional[dict[str, Any]]) -> dict[str, Any]:
    merged = dict(_DEFAULT_OPTIONS)
    if options:
        merged.update({k: v for k, v in options.items() if v is not None})
    return merged


def _video_filter(opts: dict[str, Any]) -> str:
    crop_px = max(0, int(opts.get("crop_px", 2)))
    speed = float(opts.get("speed", 0.995))
    parts: list[str] = []
    if crop_px > 0:
        parts.append(f"crop=in_w-{crop_px}:in_h-{crop_px}")
    if opts.get("color_jitter", True):
        brightness = float(opts.get("brightness", 0.003))
        contrast = float(opts.get("contrast", 1.004))
        saturation = float(opts.get("saturation", 1.006))
        parts.append(
            f"eq=brightness={brightness}:contrast={contrast}:saturation={saturation}"
        )
    parts.append(f"setpts={speed}*PTS")
    return ",".join(parts)


def _audio_filter(opts: dict[str, Any]) -> str:
    sample_rate = int(opts.get("sample_rate", 44100))
    rate_mul = float(opts.get("audio_rate_mul", 1.005))
    return f"asetrate={sample_rate}*{rate_mul},aresample={sample_rate}"


def _build_cmd(
    ffmpeg: str,
    src: Path,
    dest: Path,
    opts: dict[str, Any],
    *,
    use_hwaccel: bool,
) -> list[str]:
    crf = int(opts.get("crf", 23))
    if opts.get("hw_encode"):
        video_codec = ["-c:v", "h264_nvenc", "-preset", "p1", "-cq", str(crf)]
    else:
        video_codec = [
            "-c:v",
            "libx264",
            "-preset",
            str(opts.get("preset", "ultrafast")),
            "-crf",
            str(crf),
        ]

    cmd = [ffmpeg, "-y"]
    if use_hwaccel:
        cmd.extend(["-hwaccel", "auto"])
    cmd.extend(
        [
            "-i",
            str(src),
            "-vf",
            _video_filter(opts),
            "-af",
            _audio_filter(opts),
            *video_codec,
            "-c:a",
            "aac",
            "-b:a",
            str(opts.get("audio_bitrate", "128k")),
        ]
    )
    if opts.get("strip_metadata", True):
        cmd.extend(["-map_metadata", "-1"])
    cmd.extend(["-movflags", "+faststart", str(dest)])
    return cmd


def apply_video_uniqueness(
    input_path: str,
    output_path: str,
    options: dict | None = None,
) -> str:
    """Re-encode *input_path* into *output_path* with a uniqueness pass.

    *options* (all optional):
      hwaccel, hw_encode, crf, preset, crop_px, speed, audio_rate_mul,
      sample_rate, audio_bitrate, strip_metadata, color_jitter,
      brightness, contrast, saturation.
    """
    src = Path(input_path)
    dest = Path(output_path)
    if not src.is_file():
        raise FileNotFoundError(src)
    if src.stat().st_size <= 0:
        raise ValueError(f"Refusing to process empty file: {src}")

    dest.parent.mkdir(parents=True, exist_ok=True)
    opts = _merged_options(options)
    ffmpeg = resolve_ffmpeg()
    use_hwaccel = bool(opts.get("hwaccel", True))

    _LOG.info("Uniqueness pass | %s → %s", src.name, dest)
    result = subprocess.run(
        _build_cmd(ffmpeg, src, dest, opts, use_hwaccel=use_hwaccel),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0 and use_hwaccel:
        _LOG.warning(
            "hwaccel uniqueness pass failed for %s — retrying software decode.",
            src.name,
        )
        result = subprocess.run(
            _build_cmd(ffmpeg, src, dest, opts, use_hwaccel=False),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
    if result.returncode != 0:
        err = (result.stderr or b"").decode("utf-8", errors="replace")[-800:]
        raise RuntimeError(f"ffmpeg uniqueness pass failed for {src.name}:\n{err}")
    if not dest.is_file() or dest.stat().st_size <= 0:
        raise RuntimeError(f"ffmpeg wrote no output for {src.name}")
    return str(dest)


def _parse_cli(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="fingerprint_engine.py",
        description=(
            "Channel-agnostic FFmpeg uniqueness pass: strip metadata, "
            "nudge audio rate, apply micro color jitter and a slight speed shift."
        ),
    )
    parser.add_argument("--input", required=True, help="Source video path.")
    parser.add_argument("--output", required=True, help="Signed output MP4 path.")
    parser.add_argument("--no-hwaccel", action="store_true")
    parser.add_argument(
        "--hw-encode",
        action="store_true",
        help="Use h264_nvenc instead of libx264 ultrafast.",
    )
    parser.add_argument("--crf", type=int, default=None)
    parser.add_argument("--preset", default=None)
    parser.add_argument("--no-color-jitter", action="store_true")
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = _parse_cli(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%H:%M:%S",
    )
    options = {
        "hwaccel": not args.no_hwaccel,
        "hw_encode": args.hw_encode,
        "crf": args.crf,
        "preset": args.preset,
        "color_jitter": not args.no_color_jitter,
    }
    dest = apply_video_uniqueness(args.input, args.output, options)
    print(f"[fingerprint] Signed → {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

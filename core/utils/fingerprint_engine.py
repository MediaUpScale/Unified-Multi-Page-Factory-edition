# -*- coding: utf-8 -*-
"""Standalone FFmpeg uniqueness pass — no channel or page logic.

Re-encodes a video so container + bitstream hashes change while the picture
stays visually identical: metadata strip, 2 px crop, micro color jitter,
0.5% presentation-timestamp shift, and a matching audio rate nudge.

Any folder (or file) is accepted. Signed outputs always land in a ``processes``
sibling folder next to the source.

CLI
---
    python core/utils/fingerprint_engine.py "path/to/folder"
    python core/utils/fingerprint_engine.py "path/to/video.mp4"
    python core/utils/fingerprint_engine.py --input "path/to/folder"
"""
from __future__ import annotations

import argparse
import logging
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v"}
SIGNED_SUFFIX = "_signed"
PROCESSES_DIRNAME = "processes"
_RESERVED_OUTPUT_DIRS = frozenset({PROCESSES_DIRNAME, "Processed"})

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


def resolve_processes_dir(source_dir: str | Path) -> Path:
    """Create ``<source_dir>/processes`` and return it."""
    dest = Path(source_dir) / PROCESSES_DIRNAME
    dest.mkdir(parents=True, exist_ok=True)
    return dest


def signed_output_name(src: Path) -> str:
    stem = src.stem.rstrip("_")
    return f"{stem}{SIGNED_SUFFIX}.mp4"


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

    _LOG.info("Uniqueness pass | %s -> %s", src.name, dest)
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


def _is_video(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS


def _inside_reserved_output(path: Path, root: Path) -> bool:
    try:
        relative = path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return any(part in _RESERVED_OUTPUT_DIRS for part in relative.parts)


def iter_source_videos(
    folder: Path,
    *,
    recursive: bool = False,
    skip_dir: Optional[Path] = None,
) -> list[Path]:
    """Return source videos in *folder*, ignoring signed copies and output dirs."""
    skip_resolved = skip_dir.resolve() if skip_dir else None
    iterator = folder.rglob("*") if recursive else folder.iterdir()
    found: list[Path] = []
    for path in iterator:
        if not _is_video(path):
            continue
        if path.stem.endswith(SIGNED_SUFFIX):
            continue
        if _inside_reserved_output(path, folder):
            continue
        if skip_resolved is not None:
            try:
                path.resolve().relative_to(skip_resolved)
                continue
            except ValueError:
                pass
        found.append(path)
    return sorted(found)


def apply_folder_uniqueness(
    input_dir: str,
    output_dir: Optional[str] = None,
    options: dict | None = None,
    *,
    skip_existing: bool = True,
    recursive: bool = False,
) -> list[str]:
    """Sign every video in *input_dir* into ``<input_dir>/processes``."""
    src_dir = Path(input_dir)
    if not src_dir.is_dir():
        raise NotADirectoryError(src_dir)
    dest_dir = Path(output_dir) if output_dir else resolve_processes_dir(src_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    videos = iter_source_videos(src_dir, recursive=recursive, skip_dir=dest_dir)
    if not videos:
        _LOG.warning("No source videos found in %s", src_dir)
        print(f"[fingerprint] No source videos in {src_dir}")
        return []

    signed: list[str] = []
    total = len(videos)
    for i, src in enumerate(videos, start=1):
        dest = dest_dir / signed_output_name(src)
        if skip_existing and dest.is_file() and dest.stat().st_size > 0:
            print(f"[fingerprint] Skip existing {i}/{total}: {dest.name}")
            signed.append(str(dest))
            continue
        print(f"[fingerprint] {i}/{total} {src.name}")
        try:
            signed.append(apply_video_uniqueness(str(src), str(dest), options))
        except Exception as exc:  # noqa: BLE001
            _LOG.error("Failed %s: %s", src.name, exc)
            print(f"[fingerprint] FAILED {src.name}: {exc}")
            continue
        print(f"[fingerprint] Signed -> {dest}")
    return signed


def sign_path(
    input_path: str | Path,
    output_path: Optional[str | Path] = None,
    options: dict | None = None,
    *,
    skip_existing: bool = True,
    recursive: bool = False,
) -> list[str]:
    """Sign a file or a folder. Folder/file outputs go under ``processes/``."""
    src = Path(input_path)
    if src.is_dir():
        return apply_folder_uniqueness(
            str(src),
            str(output_path) if output_path else None,
            options,
            skip_existing=skip_existing,
            recursive=recursive,
        )
    if not src.is_file():
        raise FileNotFoundError(src)
    dest = (
        Path(output_path)
        if output_path
        else resolve_processes_dir(src.parent) / signed_output_name(src)
    )
    if dest.is_dir():
        dest = dest / signed_output_name(src)
    return [apply_video_uniqueness(str(src), str(dest), options)]


def _parse_cli(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="fingerprint_engine.py",
        description=(
            "Channel-agnostic FFmpeg uniqueness pass. Pass any folder or video; "
            f"signed copies are written to a '{PROCESSES_DIRNAME}/' folder "
            "in the same directory."
        ),
    )
    parser.add_argument(
        "path",
        nargs="?",
        help="Source video file, or a folder of videos.",
    )
    parser.add_argument(
        "--input",
        dest="input_flag",
        default=None,
        help="Same as the positional path. Overrides the positional value.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help=(
            "Optional override. Folder mode: destination folder. "
            f"File mode: signed MP4 path. Default: <source>/{PROCESSES_DIRNAME}."
        ),
    )
    parser.add_argument(
        "--recursive",
        action="store_true",
        help="In folder mode, also process videos in subfolders.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="In folder mode, re-sign even when the output already exists.",
    )
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
    source = args.input_flag or args.path
    if not source:
        raise SystemExit("Pass a folder or file: fingerprint_engine.py <path>")
    options = {
        "hwaccel": not args.no_hwaccel,
        "hw_encode": args.hw_encode,
        "crf": args.crf,
        "preset": args.preset,
        "color_jitter": not args.no_color_jitter,
    }
    signed = sign_path(
        source,
        args.output,
        options,
        skip_existing=not args.force,
        recursive=args.recursive,
    )
    if len(signed) == 1 and not Path(source).is_dir():
        print(f"[fingerprint] Signed -> {signed[0]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

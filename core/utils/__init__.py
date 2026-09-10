# -*- coding: utf-8 -*-
"""Channel-agnostic utilities shared across the factory."""

from core.utils.fingerprint_engine import (
    PROCESSES_DIRNAME,
    apply_folder_uniqueness,
    apply_video_uniqueness,
    resolve_ffmpeg,
    resolve_processes_dir,
    sign_path,
)

__all__ = [
    "PROCESSES_DIRNAME",
    "apply_folder_uniqueness",
    "apply_video_uniqueness",
    "resolve_ffmpeg",
    "resolve_processes_dir",
    "sign_path",
]

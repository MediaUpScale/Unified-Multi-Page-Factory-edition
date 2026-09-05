# -*- coding: utf-8 -*-
"""Channel-agnostic utilities shared across the factory."""

from core.utils.fingerprint_engine import apply_video_uniqueness, resolve_ffmpeg

__all__ = [
    "apply_video_uniqueness",
    "resolve_ffmpeg",
]

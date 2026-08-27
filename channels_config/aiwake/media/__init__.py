# -*- coding: utf-8 -*-
"""Media stack: TTS, terminal-UI rendering and the VFX hook chain.

Imports are kept lazy at the package level so importing :mod:`aiwake` in a
headless environment never requires MoviePy or Pillow.
"""
from __future__ import annotations

from .audio import AudioAsset, TTSEngine, TTSError, build_engine
from .vfx import FrameContext, available_hooks, register_vfx

__all__ = [
    "AudioAsset",
    "FrameContext",
    "TTSEngine",
    "TTSError",
    "available_hooks",
    "build_engine",
    "register_vfx",
]

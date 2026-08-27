# -*- coding: utf-8 -*-
"""Factory output / scratch paths.

Pipeline artifacts (renders, logs, MoviePy temps) belong under ``outputs/``,
never the process CWD or the repo root.
"""
from __future__ import annotations

from pathlib import Path

_FACTORY_ROOT: Path = Path(__file__).resolve().parents[1]


def factory_root() -> Path:
    return _FACTORY_ROOT


def outputs_dir() -> Path:
    return _FACTORY_ROOT / "outputs"


def pipeline_logs_dir() -> Path:
    path = outputs_dir() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def pipeline_tmp_dir(*parts: str) -> Path:
    path = outputs_dir() / "tmp"
    for part in parts:
        path = path / part
    path.mkdir(parents=True, exist_ok=True)
    return path


def moviepy_temp_audio_dir() -> str:
    """Directory MoviePy uses for ``*TEMP_MPY_wvf_snd*`` temp audio."""
    return str(pipeline_tmp_dir("moviepy"))

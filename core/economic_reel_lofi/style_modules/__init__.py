# -*- coding: utf-8 -*-
"""Swappable visual-style registry. Default: riso_retro_flat_v4."""
from __future__ import annotations

import os
from typing import Any

from core.economic_reel_lofi.style_modules.riso_retro_flat_v4 import (
    STYLE as RISO_RETRO_FLAT_V4,
    StyleConfig,
    as_profile_dict,
)

_REGISTRY: dict[str, StyleConfig] = {
    RISO_RETRO_FLAT_V4.id: RISO_RETRO_FLAT_V4,
    "style-riso_painting_retro_vintage": RISO_RETRO_FLAT_V4,
}


def active_style_id() -> str:
    raw = str(os.environ.get("LOFI_STYLE_MODULE") or "").strip()
    return raw or RISO_RETRO_FLAT_V4.id


def get_active_style() -> StyleConfig:
    key = active_style_id()
    return _REGISTRY.get(key) or RISO_RETRO_FLAT_V4


def get_style_profile_dict(name: str | None = None) -> dict[str, Any]:
    if name:
        found = _REGISTRY.get(str(name).strip())
        if found is not None:
            return as_profile_dict(found)
    return as_profile_dict(get_active_style())


__all__ = [
    "StyleConfig",
    "active_style_id",
    "get_active_style",
    "get_style_profile_dict",
]

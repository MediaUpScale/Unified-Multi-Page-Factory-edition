# -*- coding: utf-8 -*-
"""
BasePageProfile — structural Protocol that every page_config.py should satisfy.

This is a *duck-typing* Protocol (PEP 544).  Existing pages do NOT need to
inherit from it — the loader validates conformance at runtime using
``isinstance(page_cfg_module, BasePageProfile)`` after calling
``runtime_checkable``.

Minimum required attributes for a valid page profile
-----------------------------------------------------
  PAGE_ID               str   — machine slug, matches directory name
  PAGE_DISPLAY_NAME     str   — human-readable channel name
  CONTENT_NICHE         str   — one-line niche description
  DEFAULT_AVATAR_MODE   str   — "ON" or "OFF"
  DEFAULT_FORMAT        str   — one of VALID_FORMATS
  ATMOSPHERE_STYLE      str   — base image generation atmosphere directive
  COST_TIER             str   — "nano" | "economic" | "premium"
  ENABLE_COST_TRACKING  bool  — whether to write cost telemetry per asset
  TOPIC_POOL            list  — rotating topic seeds

Optional (but recommended) attributes
--------------------------------------
  ELEVENLABS_VOICE_ID   str
  ELEVENLABS_MODEL      str
  REEL_DURATION         float
  ENABLE_SEQUENCE_REEL  bool  — True = 4-image 80-second sequence reel
  REEL_IMAGE_COUNT      int   — number of images in sequence reel (default 4)
  ILLUSTRATION_STYLE    str
  FONT_PATH             str
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class BasePageProfile(Protocol):
    """Minimum interface contract for a page_config module."""

    PAGE_ID: str
    PAGE_DISPLAY_NAME: str
    CONTENT_NICHE: str
    DEFAULT_AVATAR_MODE: str
    DEFAULT_FORMAT: str
    ATMOSPHERE_STYLE: str
    COST_TIER: str
    ENABLE_COST_TRACKING: bool
    TOPIC_POOL: list


def validate_page_profile(module: object, page_id: str) -> list[str]:
    """
    Check that ``module`` satisfies BasePageProfile.

    Returns a list of missing attribute names (empty = fully compliant).
    Does not raise; callers decide how to handle warnings.
    """
    required = [
        "PAGE_ID", "PAGE_DISPLAY_NAME", "CONTENT_NICHE",
        "DEFAULT_AVATAR_MODE", "DEFAULT_FORMAT", "ATMOSPHERE_STYLE",
        "COST_TIER", "ENABLE_COST_TRACKING", "TOPIC_POOL",
    ]
    missing = [attr for attr in required if not hasattr(module, attr)]
    return missing

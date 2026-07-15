# -*- coding: utf-8 -*-
"""
Master Mei — page-level configuration.

Persona: stoic discipline, controlled performance, mastery through repetition.
Avatar: ON-capable (female, East-Asian, early-40s, minimalist aesthetic).
Niche: stoic philosophy, high-performance habits, cold exposure, discipline science.
"""
from __future__ import annotations

PAGE_DISPLAY_NAME = "Master Mei — Discipline by Design"

CONTENT_NICHE = (
    "stoic discipline, cold exposure science, performance habits, "
    "minimalist productivity, controlled environment mastery"
)

DEFAULT_AVATAR_MODE = "ON"
DEFAULT_FORMAT = "IMAGE_AVATAR"
IMAGE_ASPECT_RATIO = "3:4"
USES_AVATAR_REFERENCE = True

ATMOSPHERE_STYLE = (
    "Cold, minimalist cinematic photography. Frozen lake at dawn, mist rising. "
    "Concrete and steel training facility, single overhead light. "
    "Dark monastery corridors with candlelight. Sparse negative space, high contrast. "
    "Tones: slate grey, ice blue, muted charcoal. No human subjects. Ultra-realistic."
)

PINTEREST_BOARD_ID: str = ""

# ---------------------------------------------------------------------------
# Brand logo layout — controls watermark size and corner placement.
# LOGO_SIZE_SCALE : float 0.0–1.0 — logo width as a fraction of canvas width.
# LOGO_POSITION   : 'top_left' | 'top_right' | 'bottom_left' | 'bottom_right'
# ---------------------------------------------------------------------------
LOGO_SIZE_SCALE: float = 0.15      # 15 % of canvas width (minimal)
LOGO_POSITION: str = "top_right"

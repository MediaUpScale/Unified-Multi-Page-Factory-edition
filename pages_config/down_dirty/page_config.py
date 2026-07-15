# -*- coding: utf-8 -*-
"""
Down & Dirty — page-level configuration.

Persona: matrix escape, financial sovereignty, raw mindset, anti-system thinking.
Avatar: OFF by default (purely aesthetic atmospheric background).
Niche: red-pill financial mindset, anti-fragility, sovereign living, digital nomad freedom.
"""
from __future__ import annotations

PAGE_DISPLAY_NAME = "Down & Dirty — Matrix Escape & Raw Mindset"

CONTENT_NICHE = (
    "financial sovereignty, anti-fragility, matrix escape mindset, "
    "sovereign living, digital nomad, raw discipline, systems thinking"
)

DEFAULT_AVATAR_MODE = "OFF"
DEFAULT_FORMAT = "IMAGE_AVATAR"
IMAGE_ASPECT_RATIO = "9:16"
USES_AVATAR_REFERENCE = False

ATMOSPHERE_STYLE = (
    "Moody, raw, cinematic urban photography. Rainy neon-lit streets at night, "
    "wet asphalt reflecting orange and cyan neon signs. "
    "Abandoned industrial rooftops overlooking a sprawling city at dusk. "
    "Cracked concrete, rusted metal, single fluorescent tube in a dark corridor. "
    "Tones: deep navy, neon orange, acid yellow, charcoal black. "
    "High contrast. No human subjects. Blade Runner aesthetic meets documentary grit."
)

PINTEREST_BOARD_ID: str = ""

# ---------------------------------------------------------------------------
# Brand logo layout — controls watermark size and corner placement.
# LOGO_SIZE_SCALE : float 0.0–1.0 — logo width as a fraction of canvas width.
# LOGO_POSITION   : 'top_left' | 'top_right' | 'bottom_left' | 'bottom_right'
# ---------------------------------------------------------------------------
LOGO_SIZE_SCALE: float = 0.16      # 16 % of canvas width
LOGO_POSITION: str = "bottom_left"

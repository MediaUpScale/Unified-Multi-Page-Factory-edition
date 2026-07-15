# -*- coding: utf-8 -*-
"""
Anna Protocol — page-level configuration for the Unified Multi-Page Factory.

All values here supplement (and can override) global config.py defaults.
The pipeline reads this file dynamically at runtime when --page anna_protocol is active.
"""
from __future__ import annotations

# Display name surfaced in logs and production summaries.
PAGE_DISPLAY_NAME = "The Holistic Legacy — Anna's Protocol"

# Niche descriptor — injected into atmospheric prompts when --avatar OFF.
CONTENT_NICHE = "holistic longevity, natural remedies, ancestral wellness, biochemistry"

# Default avatar flag for this page. CLI --avatar overrides this.
DEFAULT_AVATAR_MODE = "ON"

# Default output format. CLI --format overrides this.
DEFAULT_FORMAT = "IMAGE_AVATAR"

# Gemini aspect ratio used by VisualArchitect when page is active.
IMAGE_ASPECT_RATIO = "3:4"

# Whether this page supports a human likeness reference image.
USES_AVATAR_REFERENCE = True

# Atmospheric visual style used when --avatar OFF is active.
# Describes purely environmental / botanical imagery — no human subjects.
ATMOSPHERE_STYLE = (
    "Warm macro nature photography. Botanical close-ups: morning dew on herb leaves, "
    "golden hour streaming through a forest canopy, apothecary jars filled with dried roots "
    "on cedar shelves, stone mortar beside fresh-cut sprigs on a wooden board. "
    "Earthy tones — sage green, terracotta, warm amber. No human subjects. "
    "Shot on 35mm film or high-end smartphone candid aesthetic. Ultra-realistic."
)

# Pinterest board ID override (leave empty to use global .env value).
PINTEREST_BOARD_ID: str = ""

# ---------------------------------------------------------------------------
# Brand logo layout — controls watermark size and corner placement.
# LOGO_SIZE_SCALE : float 0.0–1.0 — logo width as a fraction of canvas width.
# LOGO_POSITION   : 'top_left' | 'top_right' | 'bottom_left' | 'bottom_right'
# ---------------------------------------------------------------------------
LOGO_SIZE_SCALE: float = 0.18      # 18 % of canvas width
LOGO_POSITION: str = "bottom_right"

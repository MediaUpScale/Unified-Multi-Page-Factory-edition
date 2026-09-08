# -*- coding: utf-8 -*-
"""
Master Mei sequence-reel chrome — single source of truth.

All ECONOMIC_REEL / SEQUENCE_REEL subtitle typography (size, fill, stroke,
phrase length, lower-third Y) is inherited from
``channels_config.master_mei.page_config``. Do not hardcode sizes elsewhere.
"""
from __future__ import annotations

from channels_config.master_mei import page_config as _mei

SUBTITLE_FONTSIZE: int = int(_mei.SUBTITLE_FONTSIZE)
SUBTITLE_Y_POSITION: int = int(_mei.SUBTITLE_Y_POSITION)
SUBTITLE_WORDS_PER_PHRASE: int = int(_mei.SUBTITLE_WORDS_PER_PHRASE)
SUBTITLE_FILL: tuple[int, int, int] = tuple(int(c) for c in _mei.SUBTITLE_FILL)
SUBTITLE_STROKE_FILL: tuple[int, int, int] = tuple(
    int(c) for c in _mei.SUBTITLE_STROKE_FILL
)
SUBTITLE_STROKE_WIDTH: int = int(_mei.SUBTITLE_STROKE_WIDTH)
FONT_PATH: str = str(_mei.FONT_PATH)

# Two-line CTA sits slightly above the body phrase so it clears the logo.
CTA_SUBTITLE_Y_POSITION: int = max(200, SUBTITLE_Y_POSITION - 50)


def as_dict() -> dict:
    return {
        "fontsize": SUBTITLE_FONTSIZE,
        "y": SUBTITLE_Y_POSITION,
        "cta_y": CTA_SUBTITLE_Y_POSITION,
        "wpp": SUBTITLE_WORDS_PER_PHRASE,
        "fill": SUBTITLE_FILL,
        "stroke_fill": SUBTITLE_STROKE_FILL,
        "stroke_width": SUBTITLE_STROKE_WIDTH,
        "font_path": FONT_PATH,
    }

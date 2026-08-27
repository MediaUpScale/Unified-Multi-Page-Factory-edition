# -*- coding: utf-8 -*-
"""
Endless Summer Paradise — 1950s surreal / vintage retro architecture ingest.

Source masters live on the sibling MEDIAUPSCALE production drive. The factory
reads them, applies duration + metadata gates, and schedules via the YouTube
publisher (private + publishAt).

Metadata strategy (US high-RPM):
  Titles  → [Hook] — 1950s Surreal Jell-O Waterpark | Brand/Sub-theme
  Desc    → above-fold keywords → dense mid context → playlist/CTA/disclaimer
  Locale  → en-US spelling + snippet.defaultLanguage / defaultAudioLanguage
  Category→ Film & Animation (1) for cinematic visual-art / architecture

Entry point:
    python channels_config/endless_summer_paradise/esp_main.py scan
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Core profile
# ---------------------------------------------------------------------------
PAGE_ID: str = "endless_summer_paradise"
PAGE_DISPLAY_NAME: str = "Endless Summer Paradise"
CONTENT_NICHE: str = (
    "1950s Surreal / Vintage Retro Architecture — Mid-Century Aesthetic Visual Art"
)
CONTENT_TYPE: str = "Library ingest / Cinematic surreal architecture curation"

DEFAULT_AVATAR_MODE: str = "OFF"
DEFAULT_FORMAT: str = "REFERENCE_BASED_REELS"
IMAGE_ASPECT_RATIO: str = "16:9"
USES_AVATAR_REFERENCE: bool = False

COST_TIER: str = "economic"
ENABLE_COST_TRACKING: bool = True
ECONOMIC_BRAIN_MODE: bool = True

# ---------------------------------------------------------------------------
# Source library — read-only from the factory
# ---------------------------------------------------------------------------
SOURCE_DIRECTORY: str = (
    r"G:\My Drive\Z sosFiles\Z_act\@ NETWORK\@ MEDIAUPSCALE_FACTORY"
    r"\Endless_Summers_Paradise - Production"
)
REFERENCE_VIDEO_DIR: str = SOURCE_DIRECTORY
PROCESSED_SUBFOLDER: str = "Processed"

GLOBAL_VIDEO_LIBRARY_PATH: str = (
    r"G:\My Drive\Z sosFiles\Z_act\@ NETWORK\@ MEDIAUPSCALE_FACTORY"
    r"\FACTORY_OUTPUT\global_video_library.json"
)

# ---------------------------------------------------------------------------
# Duration gate — process ONLY runtime strictly greater than 40 seconds
# ---------------------------------------------------------------------------
MIN_DURATION_SECONDS: float = 40.0  # skip when duration <= 40.0
MASTER_FILENAME_SUFFIX: str = "_ULTIMATE_MASTER.mp4"

# ---------------------------------------------------------------------------
# Automation flags
# ---------------------------------------------------------------------------
DISCLAIMER_REQUIRED: bool = True
FINGERPRINT_BYPASS: bool = True
SHORTS_TO_LONG_LINKING: bool = False

# ---------------------------------------------------------------------------
# YouTube API locale + category (US audience targeting)
# ---------------------------------------------------------------------------
# Entertainment (24) — US aesthetic / ambient visual RPM path.
# Alternatives: 1 Film & Animation, 10 Music (ambient loops).
YOUTUBE_CATEGORY_ID: str = "24"  # Entertainment — US aesthetic / ambient visual RPM
YOUTUBE_DEFAULT_LANGUAGE: str = "en-US"
YOUTUBE_DEFAULT_AUDIO_LANGUAGE: str = "en-US"

YOUTUBE_DEFAULT_PRIVACY_LONG: str = "private"
YOUTUBE_DEFAULT_PRIVACY_SHORT: str = "private"

YOUTUBE_PLAYLIST_TITLE: str = (
    "Endless Summer Paradise — 1950s Surreal Architecture & Retro Dreamscapes"
)
YOUTUBE_PLAYLIST_DESCRIPTION: str = (
    "Cinematic 1950s surreal Jell-O waterpark architecture, mid-century modern "
    "design, vintage aesthetic visual art, and retro dreamscape atmospheres from "
    "Endless Summer Paradise. Curated for US viewers searching cinematic "
    "surrealism, 4K visual art, and mid-century architecture aesthetics.\n\n"
    "Disclaimer: Atmospheric entertainment and visual art content. Not "
    "affiliated with any travel destination or resort brand."
)

# Title structure constants
YOUTUBE_TITLE_CORE_ANCHOR: str = "1950s Surreal Jell-O Waterpark"
YOUTUBE_TITLE_BRAND: str = "Endless Summer Paradise"
YOUTUBE_TITLE_SUFFIX: str = " | Endless Summer Paradise"
YOUTUBE_SHORT_TITLE_SUFFIX: str = " | Endless Summer Paradise"

# High-CPM / high-CTR keyword anchors (US market) — woven, never stuffed.
YOUTUBE_KEYWORD_ANCHORS: list = [
    "1950s mid-century architecture",
    "vintage aesthetic",
    "4K visual art",
    "retro dreamscape",
    "cinematic surrealism",
    "mid-century modern design",
    "retro-futurism",
    "visual relaxation",
]

YOUTUBE_DEFAULT_TAGS: list = [
    "endless summer paradise",
    "1950s surreal jell-o waterpark",
    "1950s mid-century architecture",
    "mid-century modern design",
    "vintage aesthetic",
    "retro dreamscape",
    "cinematic surrealism",
    "4K visual art",
    "retro futurism",
    "surreal architecture",
    "visual relaxation",
    "jelly kingdom",
    "impossible architecture",
    "vintage retro architecture",
    "american mid century aesthetic",
]

SEO_TARGETS: list = [
    "1950s mid-century architecture",
    "vintage aesthetic 4K visual art",
    "retro dreamscape cinematic surrealism",
    "1950s surreal jell-o waterpark",
    "mid-century modern design visuals",
    "retro-futurism architecture aesthetic",
    "cinematic surreal architecture",
    "visual relaxation mid-century",
    "vintage retro architecture video",
    "endless summer paradise",
]

# Optional sub-theme tails when stem implies a motif (kept short for 100-char titles).
YOUTUBE_SUBTHEME_MAP: dict = {
    "oasis": "Mid-Century Oasis",
    "mirage": "Retro Dreamscape",
    "daydream": "Vintage Aesthetic",
    "cascade": "4K Visual Art",
    "geyser": "Cinematic Surrealism",
    "quake": "Impossible Architecture",
    "springs": "Visual Relaxation",
    "deluge": "Retro-Futurism",
    "garden": "Mid-Century Design",
    "bay": "Vintage Retro Architecture",
    "vista": "1950s Mid-Century Architecture",
    "social": "American Mid-Century Aesthetic",
}

# Above-the-fold opener (first ~3 lines) — keyword-dense, US English.
YOUTUBE_DESCRIPTION_HOOK_TEMPLATE: str = (
    "{hook_line}\n"
    "A cinematic study of 1950s mid-century architecture, vintage aesthetic design, "
    "and retro dreamscape atmosphere rendered as 4K visual art.\n"
    "Explore translucent Jell-O waterpark geometry, mid-century modern color, and "
    "cinematic surrealism built for visual relaxation."
)

# Middle dense context (semantic SEO for higher-tier US recommendations).
YOUTUBE_DESCRIPTION_CONTEXT: str = (
    "Endless Summer Paradise visualizes a fictional 1950s American leisure "
    "landscape where mid-century modern design meets impossible material physics. "
    "Each frame is composed as cinematic surrealism: translucent slides, elastic "
    "water, and vintage aesthetic lighting that reads as both nostalgic and "
    "futuristic. The series sits at the intersection of retro-futurism, 4K visual "
    "art, and mid-century architecture storytelling for viewers who search design, "
    "atmosphere, and visual relaxation rather than travel ads.\n\n"
    "This channel targets US audiences interested in 1950s mid-century architecture, "
    "vintage retro architecture, and high-craft aesthetic video. Scenes emphasize "
    "center-framed environmental scale, soft noon and golden-hour color, and the "
    "slow optical rhythm of surreal waterpark structures. The goal is immersive "
    "visualizing — a calm, design-forward escape that YouTube can classify alongside "
    "cinematic art, architecture aesthetics, and premium ambient entertainment."
)

YOUTUBE_DESCRIPTION_CTA: str = (
    "Subscribe to Endless Summer Paradise for more 1950s surreal architecture, "
    "vintage aesthetic 4K visual art, and retro dreamscape atmospheres.\n\n"
    "Playlist: {playlist}"
)

# Full template kept for backward compatibility with older callers.
YOUTUBE_DESCRIPTION_TEMPLATE: str = (
    "A cinematic study of 1950s mid-century architecture, vintage aesthetic design, "
    "and retro dreamscape atmosphere rendered as 4K visual art.\n"
    "Explore translucent Jell-O waterpark geometry, mid-century modern color, and "
    "cinematic surrealism built for visual relaxation.\n\n"
    + YOUTUBE_DESCRIPTION_CONTEXT
    + "\n\n"
    "Subscribe to Endless Summer Paradise for more 1950s surreal architecture, "
    "vintage aesthetic 4K visual art, and retro dreamscape atmospheres.\n\n"
    "Playlist: Endless Summer Paradise — 1950s Surreal Architecture & Retro Dreamscapes\n\n"
    "#EndlessSummerParadise #MidCenturyArchitecture #VintageAesthetic #RetroDreamscape "
    "#CinematicSurrealism #4KVisualArt #RetroFuturism\n\n"
    "Disclaimer: Atmospheric entertainment and visual art content. Not affiliated "
    "with any travel destination or resort brand."
)

YOUTUBE_SHORT_DESCRIPTION_TEMPLATE: str = (
    "1950s surreal Jell-O waterpark architecture in under a minute — mid-century "
    "modern design, vintage aesthetic color, and cinematic surrealism from "
    "Endless Summer Paradise.\n\n"
    "Watch more 4K visual art and retro dreamscapes on the channel playlist.\n\n"
    "#Shorts #EndlessSummerParadise #MidCenturyArchitecture #VintageAesthetic "
    "#CinematicSurrealism #4KVisualArt\n\n"
    "Disclaimer: Atmospheric entertainment and visual art content. Not affiliated "
    "with any travel destination or resort brand."
)

YOUTUBE_HASHTAG_LINE: str = (
    "#EndlessSummerParadise #MidCenturyArchitecture #VintageAesthetic "
    "#RetroDreamscape #CinematicSurrealism #4KVisualArt #RetroFuturism "
    "#SurrealArchitecture #VisualRelaxation"
)

# ---------------------------------------------------------------------------
# Visual / caption defaults (ingest path; no generation)
# ---------------------------------------------------------------------------
FONT_PATH: str = "Fonts/Montserrat/static/Montserrat-Bold.ttf"
FONT_SIZE_SCALE: float = 0.07
FONT_COLOR: tuple = (245, 245, 240)
TEXT_OUTLINE_WIDTH: int = 2
LOGO_SIZE_SCALE: float = 0.28
LOGO_POSITION: str = "bottom_center"
CAPTION_SIGNATURE: str = "© Endless Summer Paradise | 1950s Surreal Architecture"

ENABLE_SEQUENCE_REEL: bool = False
ENABLE_SKETCH_STYLE: bool = False
ENABLE_HORROR_TRANSFORMATIONS: bool = False
USE_STYLE_REFERENCE: bool = False
PINTEREST_BOARD_ID: str = ""

TOPIC_POOL: list = [
    "1950s surreal Jell-O waterpark architecture",
    "Mid-century modern translucent slide oasis",
    "Vintage aesthetic retro dreamscape lagoon",
    "Cinematic surrealism at a jelly-bright plaza",
    "4K visual art of impossible mid-century design",
    "Retro-futurism noon light through gelatin walls",
    "American mid-century leisure landscape daydream",
    "Visual relaxation inside vintage retro architecture",
]

NICHE_DISCLAIMER: str = (
    "CHANNEL CONTEXT: Endless Summer Paradise — 1950s surreal / vintage retro "
    "architecture and mid-century aesthetic visual art for a US audience. "
    "Titles follow: [Evocative Visual Hook] — 1950s Surreal Jell-O Waterpark | "
    "[Sub-theme or Channel Brand]. "
    "Use US English spelling only (color, center, visualizing). "
    "Weave high-CPM anchors naturally: 1950s mid-century architecture, vintage "
    "aesthetic, 4K visual art, retro dreamscape, cinematic surrealism. "
    "Descriptions: keyword-rich top 3 lines, dense mid-section context, then "
    "playlist + subscribe CTA + one plain-text disclaimer with no emojis or "
    "warning icons. "
    "Tone: cinematic, design-forward, nostalgic, high-visual, never travel hard-sell."
)

DISCLAIMER_LINE: str = (
    "Disclaimer: Atmospheric entertainment and visual art content. Not affiliated "
    "with any travel destination or resort brand."
)

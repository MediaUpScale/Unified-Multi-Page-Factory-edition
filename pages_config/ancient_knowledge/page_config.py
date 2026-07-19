# -*- coding: utf-8 -*-
"""
Ancient Knowledge — page-level configuration.

Persona: investigative documentary channel exploring ancient history,
lost civilisations, unbelievable historical facts, world conspiracies,
and ancient mysteries.

Disclaimer: This channel presents theories and historical accounts
as entertainment and education only. We do NOT claim any conspiracy
or theory is factual.

Cost mode: HARDCODED to "nano" (economic/lightweight) for all generation.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Core profile fields — satisfies BasePageProfile protocol
# ---------------------------------------------------------------------------
PAGE_ID: str = "ancient_knowledge"
PAGE_DISPLAY_NAME: str = "Ancient Knowledge"

CONTENT_NICHE: str = (
    "ancient history, lost civilisations, unbelievable historical facts, "
    "world conspiracies, ancient mysteries, unexplained archaeological sites, "
    "forbidden archaeology, and suppressed historical narratives"
)

DEFAULT_AVATAR_MODE: str = "OFF"
DEFAULT_FORMAT: str = "IMAGE_BACKGROUND"
IMAGE_ASPECT_RATIO: str = "4:5"
USES_AVATAR_REFERENCE: bool = False

# ---------------------------------------------------------------------------
# Cost tier — hardcoded to nano / economic for this channel
# ---------------------------------------------------------------------------
COST_TIER: str = "nano"          # drives CostTracker pricing keys
ENABLE_COST_TRACKING: bool = True  # write cost telemetry JSON per asset

# Explicit image model override — bypasses all auto-discovery and tier defaults.
# Set to the confirmed-live banana tier (absolute cheapest in the live API list).
IMAGE_MODEL_OVERRIDE: str = "models/nano-banana-pro-preview"

# ---------------------------------------------------------------------------
# Economic brain mode — force lightweight models on every run
# ---------------------------------------------------------------------------
ECONOMIC_BRAIN_MODE: bool = True   # page-level override; respected by main.py bootstrap

# ---------------------------------------------------------------------------
# Atmosphere / visual style
# Hyper-realistic, cinematic historical photography — NOT stylised/illustrated.
# ---------------------------------------------------------------------------
ATMOSPHERE_STYLE: str = (
    "Hyper-realistic cinematic historical photography. Dramatic chiaroscuro lighting, "
    "deep atmospheric shadows, gritty aged stone and earth textures, raw archival aesthetic. "
    "35mm documentary film grain, desaturated ochre and deep shadow colour grading. "
    "Feels like a National Geographic or BBC documentary still frame. "
    "Ancient ruins, crumbling temples, hieroglyphs, underground chambers, torchlight, "
    "misty jungle canopy, desert dunes, star maps carved in stone. "
    "NO cartoon, NO illustration, NO watercolour, NO sketch. Full photographic realism only."
)

ILLUSTRATION_STYLE: str = (
    "Hyper-realistic, cinematic documentary photography. Raw, gritty stone textures, "
    "dramatic high-contrast shadows, authentic archival historical aesthetics. "
    "Warm torchlight, ancient inscriptions, weathered artefacts, epic wide-angle perspectives. "
    "Colour grade: deep ochre, shadow black, aged parchment tones. Film grain overlay. "
    "NO illustration, NO sketch, NO vector art."
)

# ---------------------------------------------------------------------------
# Negative prompt terms — strip lifestyle/relationship terms from inherited prompts
# ---------------------------------------------------------------------------
PROMPT_NEGATIVE_TERMS: list = [
    "relationship", "attachment", "emotional", "therapy", "love",
    "couple", "man and woman", "persona", "mask", "horror", "sketch",
    "graphite", "pencil drawing", "charcoal", "azure blue",
    "lifestyle photography", "morning light", "linen", "journaling",
    "cozy", "bokeh", "terracotta", "blush pink",
]

# ---------------------------------------------------------------------------
# Typography
# ---------------------------------------------------------------------------
FONT_PATH: str = "Fonts/Montserrat/static/Montserrat-Bold.ttf"
FONT_SIZE_SCALE: float = 0.07
FONT_COLOR: tuple = (255, 240, 180)   # warm parchment/gold — legible on dark stones
TEXT_OUTLINE_WIDTH: int = 2            # subtle outline for readability over dark textures

# ---------------------------------------------------------------------------
# Brand logo layout
# ---------------------------------------------------------------------------
LOGO_SIZE_SCALE: float = 0.22
LOGO_POSITION: str = "bottom_center"

# ---------------------------------------------------------------------------
# ECONOMIC_REEL / SEQUENCE_REEL — ElevenLabs voice settings
# Voice: deep, authoritative, documentary narrator
# TTS_VOICE_PREFERENCE is a human-readable label for documentation;
# ELEVENLABS_VOICE_ID is the actual API identifier used in TTS calls.
# ---------------------------------------------------------------------------
ELEVENLABS_VOICE_ID: str = "WdZjiN0nNcik2LBjOHiv"   # Direct voice ID — wise/mysterious narrator
ELEVENLABS_MODEL: str = "eleven_multilingual_v2"        # Best char-per-credit efficiency
TTS_VOICE_PREFERENCE: str = "WdZjiN0nNcik2LBjOHiv"    # Human-readable label mirrors voice ID

# ---------------------------------------------------------------------------
# Sequence reel configuration
# ENABLE_SEQUENCE_REEL: True = use 4-image 80-second reel (core_engine)
# REEL_IMAGE_COUNT:     number of images generated and stitched
# ---------------------------------------------------------------------------
REEL_DURATION: float = 65.0          # narration target: 65s × 4 acts + 5s CTA = 70s total
REEL_ACT_DURATION: float = 16.25     # 65.0 / 4 acts — equal visual slice per act
ENABLE_SEQUENCE_REEL: bool = True    # engage 4-image sequence reel for ECONOMIC_REEL
REEL_IMAGE_COUNT: int = 4            # exactly 4 distinct images per reel

REEL_OVERLAY_OPACITY: float = 0.30   # lighter overlay — let the cinematic image breathe

# ---------------------------------------------------------------------------
# SEQUENCE_REEL — visual identity layer
# ---------------------------------------------------------------------------
VIGNETTE_STRENGTH: float = 0.60      # dark corner vignette (0 = off, 1 = full black corners)
GRAIN_INTENSITY: float = 22.0        # film grain amplitude in pixel value units (default 18)
ENABLE_TOP_HOOK_TEXT: bool = False   # do not burn headline at top; only lower-third subtitles

# ---------------------------------------------------------------------------
# SEQUENCE_REEL — video layout
# ---------------------------------------------------------------------------
HOOK_Y_FRAC: float = 0.25             # hook headline in the upper quarter
SUBTITLE_FONTSIZE: int = 56           # large, bold, legible on dark stone frames
SUBTITLE_Y_POSITION: int = 1500       # safely in lower third
LOGO_WIDTH: int = 420          # 1.4× prior size (300→420) for mobile readability
LOGO_MAX_HEIGHT: int = 115     # 1.4× prior cap  (82→115)
LOGO_OPACITY: float = 0.75
LOGO_BOTTOM_MARGIN: int = 90

# ---------------------------------------------------------------------------
# Style flags
# ---------------------------------------------------------------------------
ENABLE_SKETCH_STYLE: bool = False        # photorealistic only — no sketch
ENABLE_HORROR_TRANSFORMATIONS: bool = False

USE_STYLE_REFERENCE: bool = False
STYLE_CHARACTERS: str = ""

# ---------------------------------------------------------------------------
# TOPIC_POOL — rotating subject seeds for ECONOMIC_REEL / SMART_BAIT
# ---------------------------------------------------------------------------
TOPIC_POOL: list = [
    "The lost city of Atlantis and the evidence scientists refuse to discuss",
    "Ancient Egyptian technologies that modern science cannot fully explain",
    "The real purpose of the pyramids — beyond burial chambers",
    "Göbekli Tepe and the 12,000-year-old civilisation that rewrites history",
    "The Antikythera mechanism — who really built the world's first computer",
    "Lost city of Paititi — the golden Incan capital hidden in the Amazon",
    "The Nazca Lines and theories about why they were built",
    "Ancient nuclear war — the archaeological evidence from Mohenjo-daro",
    "The Dendera light bulbs — ancient Egyptian electricity or religious art",
    "Tartaria and the mudflood theory that challenges mainstream history",
    "The Sumerian tablets and what they claim about human origins",
    "Oak Island and the 200-year mystery that still has no answer",
    "The Baghdad Battery — did ancient Mesopotamians discover electricity",
    "Coral Castle and how one man allegedly moved multi-ton stones alone",
    "Ancient maps that show Antarctica without ice — impossible or real",
    "The Voynich manuscript — the coded book that no one has ever decoded",
    "Giants in ancient history — bones, myths, and suppressed evidence",
    "The Ark of the Covenant — where it went and what it might really be",
    "Stonehenge and the forgotten civilisation that built it",
    "The Library of Alexandria — what was really lost and who destroyed it",
    "Ancient underwater cities discovered around the world",
    "The mysterious Elongated Skulls of Paracas — human or something else",
    "Easter Island and the real reason the statues were built",
    "The Philadelphia Experiment — government teleportation or wartime myth",
    "Ancient Indian flying machines — the Vimana texts and their claims",
]

# ---------------------------------------------------------------------------
# Niche disclaimer — injected into LLM system prompts
# ---------------------------------------------------------------------------
NICHE_DISCLAIMER: str = (
    "IMPORTANT CHANNEL DISCLAIMER: This channel presents conspiracy theories, "
    "ancient mysteries, and historical accounts for educational and entertainment purposes only. "
    "We respect every race, culture, and nation. We do NOT claim any of these theories are true. "
    "Use language like: 'some researchers believe', 'ancient records suggest', "
    "'according to legend', 'one theory proposes', 'historians debate whether'. "
    "Never present a theory as established fact."
)

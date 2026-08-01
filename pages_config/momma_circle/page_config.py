# -*- coding: utf-8 -*-
"""
Momma Circle — page-level configuration.

Niche: PARENTAL_CONTENTS
A warm, high-value parenting channel for busy mothers covering:
  baby nutrition & healthy eating, sleep routines, daily schedule
  optimisation, productivity, gentle discipline, and the precious
  fleeting nature of the baby/toddler season.

Content type: REFERENCE_BASED_REELS
Source clips are extracted from real reference video footage placed in
  assets/Upload Video Reference/Momma Alice/
and overlaid with an LLM-generated viral hook + upbeat inspirational ambient audio.
Hook style alternates between lighthearted toddler humor and emotional milestone content.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Core profile
# ---------------------------------------------------------------------------
PAGE_ID: str = "momma_circle"
PAGE_DISPLAY_NAME: str = "Momma Circle"
CONTENT_NICHE: str = "PARENTAL_CONTENTS"

DEFAULT_AVATAR_MODE: str = "OFF"
DEFAULT_FORMAT: str = "REFERENCE_BASED_REELS"
IMAGE_ASPECT_RATIO: str = "9:16"
USES_AVATAR_REFERENCE: bool = False

# ---------------------------------------------------------------------------
# Cost / model tier
# ---------------------------------------------------------------------------
COST_TIER: str = "economic"
ENABLE_COST_TRACKING: bool = True
ECONOMIC_BRAIN_MODE: bool = True

# ---------------------------------------------------------------------------
# Reference video source directory
# Full absolute path to the raw MP4 footage folder.
# The engine also accepts a relative path from the engine root.
# ---------------------------------------------------------------------------
REFERENCE_VIDEO_DIR: str = (
    r"assets\Upload Video Reference\Momma Alice"
)

# ---------------------------------------------------------------------------
# Clip segmentation defaults (overridable via --clip-duration CLI flag)
# ---------------------------------------------------------------------------
CLIP_DURATION_MIN_S: float = 15.0   # minimum clip length in seconds
CLIP_DURATION_MAX_S: float = 60.0   # maximum clip length in seconds

# ---------------------------------------------------------------------------
# Output video specs
# ---------------------------------------------------------------------------
OUTPUT_WIDTH: int  = 1080
OUTPUT_HEIGHT: int = 1920
OUTPUT_FPS: int    = 30

# ---------------------------------------------------------------------------
# ElevenLabs voice (used only when generating a voiceover CTA; ambient uses SFX API)
# ---------------------------------------------------------------------------
ELEVENLABS_VOICE_ID: str  = "21m00Tcm4TlvDq8ikWAM"   # Rachel — warm, friendly
ELEVENLABS_MODEL: str     = "eleven_multilingual_v2"
TTS_VOICE_PREFERENCE: str = "Rachel - warm, friendly mother"

# ---------------------------------------------------------------------------
# Ambient audio SFX prompt (ElevenLabs sound-generation)
# Leave empty to let the engine cycle the upbeat/inspirational prompt pool
# defined in reference_reel_engine.py (_AMBIENT_PROMPT_POOL).
# Set a non-empty string here to pin every render to a specific prompt.
# ---------------------------------------------------------------------------
AMBIENT_SFX_PROMPT: str = ""   # empty = use engine pool (recommended)
AMBIENT_VOLUME: float = 0.65   # slightly louder — no competing original audio
AMBIENT_FADE_IN_S: float  = 0.5
AMBIENT_FADE_OUT_S: float = 1.0

# ---------------------------------------------------------------------------
# Text overlay — on-video viral hook
# ---------------------------------------------------------------------------
HOOK_FONT_PATH: str = "Fonts/Montserrat/static/Montserrat-Bold.ttf"
HOOK_FONT_SIZE: int = 55          # px on 1080-wide canvas (reduced 68 -> 55 for safe margins)
HOOK_FONT_COLOR: tuple = (255, 255, 255)
HOOK_STROKE_COLOR: tuple = (0, 0, 0)
HOOK_STROKE_WIDTH: int   = 3
HOOK_Y_FRAC: float = 0.28         # vertical centre of hook block (upper-middle)
HOOK_MAX_WIDTH_FRAC: float = 0.85 # max text width as fraction of canvas width
HOOK_BG_ALPHA: int = 0            # 0 = no background box; set 0-180 for semi-transparent box

# ---------------------------------------------------------------------------
# Caption / content settings
# ---------------------------------------------------------------------------
CAPTION_MAX_WORDS: int = 220
CAPTION_HASHTAG_COUNT: int = 12
CAPTION_SIGNATURE: str = "© Momma Circle | by MediaUpScale"

# ---------------------------------------------------------------------------
# Post Planner / B2 export
# ---------------------------------------------------------------------------
LOGO_SIZE_SCALE: float = 0.28
LOGO_POSITION: str = "bottom_center"
LOGO_OPACITY: float = 0.80

# ---------------------------------------------------------------------------
# Topic pool — cycled when no explicit --topic is provided
# Parenting topics: each must be warm, engaging, and relatable to mothers.
# ---------------------------------------------------------------------------
TOPIC_POOL: list = [
    # ── Emotional / milestone ────────────────────────────────────────────────
    "How to get your baby to sleep through the night — the 3-step routine that changed everything",
    "What I feed my toddler in a day (realistic, not Pinterest-perfect)",
    "The invisible work of motherhood that nobody counts",
    "How a 20-minute morning routine transformed my entire day as a mom",
    "Your baby's first year is almost over — here is what no one tells you about it",
    "Simple meal prep ideas that actually work with a baby on your hip",
    "Why your 'mom guilt' is a sign you are doing something right",
    "The 5-minute bedtime routine that calmed our whole house",
    "What gentle discipline actually looks like at 18 months",
    "Signs your baby is ready to drop a nap — and what to do about it",
    "The phase you are in right now will not last — read this before it is gone",
    "How to protect your energy as a new mom without feeling selfish",
    "Introducing solids: the timeline, the foods, and what nobody warned me about",
    "Being a 'boring' predictable mom is actually the best thing you can do",
    "The mental load of motherhood is real — here is how to offload some of it",
    "Your baby does not need more toys — here is what they actually need from you",
    "Creating a daily rhythm with a baby that gives YOU breathing room",
    "How to talk to your toddler so they actually listen",
    "The season of chaos you are in right now is the one you will miss most",
    # ── Humor / relatable ────────────────────────────────────────────────────
    "Having a toddler is like having a tiny lawyer who argues every single rule",
    "Nobody warned me that toddler bedtime would become a 90-minute negotiation",
    "Things I said today that I never thought I would say before becoming a mom",
    "My toddler's new hobby is telling me 'no' with complete confidence",
    "Gentle parenting is great until your toddler gentle-parents you back",
    "The unhinged things toddlers do that secretly make you proud",
    "Why every mom secretly loves the 5 minutes after bedtime in complete silence",
    "Packing a diaper bag vs actually leaving the house: a study in chaos",
    "Toddler logic is just adult logic but with zero consequences and infinite confidence",
    "When your baby does something absurd and you have to pretend to be serious",
]

# ---------------------------------------------------------------------------
# Niche disclaimer — injected into LLM system prompts
# ---------------------------------------------------------------------------
NICHE_DISCLAIMER: str = (
    "CHANNEL CONTEXT: This is a warm, supportive parenting channel for busy mothers. "
    "Content tone must be empathetic, loving, practical, and uplifting — never judgemental. "
    "Focus on the following pillars: baby nutrition, sleep routines, daily schedule optimisation, "
    "productivity for mothers, gentle discipline, and the fleeting preciousness of the baby/toddler season. "
    "Core narrative theme: time is fleeting, baby phases pass quickly, and despite the daily chaos "
    "this is a precious season of life worth savouring. "
    "Soft monetisation: naturally position content to prepare the audience for structured guides "
    "and daily protocols — but never as a hard sales pitch. "
    "Avoid: preachy tone, unattainable perfection, shaming, medical advice, harsh discipline content."
)

# -*- coding: utf-8 -*-
"""
Wonder Feed — page-level configuration.

Persona: emotional intelligence, relationship science, attachment-aware growth.
Avatar: OFF by default (purely atmospheric / aesthetic background imagery).
Niche: emotional resilience, attachment theory, relationship psychology, inner child work.
"""
from __future__ import annotations

PAGE_DISPLAY_NAME = "Wonder Feed — Emotional Intelligence & Relationships"

DEBUG_MODE: bool = False   # Set True only for local dev; keeps console output clean in production

CONTENT_NICHE = (
    "emotional intelligence, attachment theory, relationship psychology, "
    "inner child healing, nervous system regulation, secure attachment"
)

DEFAULT_AVATAR_MODE = "OFF"
DEFAULT_FORMAT = "IMAGE_AVATAR"
IMAGE_ASPECT_RATIO = "4:5"
USES_AVATAR_REFERENCE = False

ATMOSPHERE_STYLE = (
    "Warm, soft lifestyle photography. Morning light through linen curtains, "
    "journaling on a wooden desk beside a cup of tea. Dried flowers in earthenware. "
    "Open notebook with handwritten words, slightly out of focus. "
    "Bokeh fairy lights. Soft terracotta, blush pink, warm cream tones. "
    "No human subjects. Shot on 35mm film. Emotionally safe, aesthetically cozy."
)

PINTEREST_BOARD_ID: str = ""

# ---------------------------------------------------------------------------
# Typography — controls text overlay rendering for SMART_BAIT posts.
# FONT_PATH       : Relative path to .ttf from engine root (Fonts/ folder).
# FONT_SIZE_SCALE : Float — font size as fraction of canvas width (0.08 = 8%).
# FONT_COLOR      : RGB tuple for the main text colour.
# ---------------------------------------------------------------------------
FONT_PATH: str = "Fonts/Poppins/Poppins-Bold.ttf"  # Strict relative path
FONT_SIZE_SCALE: float = 0.08       # Float: 8% of canvas width
FONT_COLOR: tuple = (255, 255, 255)  # White
TEXT_OUTLINE_WIDTH: int = 0         # PIL stroke_width (0 = no outline)
STYLE_REFERENCE_DIR: str = "Fonts/Poppins/style_reference/"  # Aesthetic reference images for cartoon style

# ---------------------------------------------------------------------------
# Brand logo layout — controls watermark size and corner placement.
# LOGO_SIZE_SCALE : float 0.0–1.0 — logo width as a fraction of canvas width.
# LOGO_POSITION   : 'top_left' | 'top_right' | 'bottom_left' | 'bottom_right'
#                   'bottom_center' | 'top_center'
# ---------------------------------------------------------------------------
LOGO_SIZE_SCALE: float = 0.24       # Float: Percentage of canvas width (0.24 = 24%)
LOGO_POSITION: str = "bottom_center"  # Options: 'top_left', 'top_right', 'bottom_left', 'bottom_right', 'bottom_center', 'top_center'

# ---------------------------------------------------------------------------
# Illustration / visual style directive.
# Used for LONG_CAPTION_IMAGE (atmosphere) and as a supplementary Gemini
# image prompt modifier for SMART_BAIT posts.
# ---------------------------------------------------------------------------
ILLUSTRATION_STYLE: str = (
    "Highly detailed pencil sketch style illustration, but restricted to only 3 colors: "
    "dark azure blue, black, and white. Dramatic lighting, expressive. "
    "Clean, minimalist, no messy details."
)

# ---------------------------------------------------------------------------
# ECONOMIC_REEL — ElevenLabs voice + reel duration settings
# ---------------------------------------------------------------------------
ELEVENLABS_VOICE_ID: str = "ThT5KcBeYPX3keUQqHPh"  # Dorothy — warm, high-empathy narrative voice
ELEVENLABS_MODEL: str = "eleven_multilingual_v2"   # Best character-to-credit efficiency
REEL_DURATION: float = 30.0                         # seconds — target reel length
REEL_OVERLAY_OPACITY: float = 0.45                  # 0-1 dark vignette over the graphite base

# ---------------------------------------------------------------------------
# ECONOMIC_REEL — video layout: subtitle and logo positioning
# SUBTITLE_FONTSIZE    : int  — subtitle text size in pixels.
# SUBTITLE_Y_POSITION  : int  — absolute Y-pixel from canvas top for subtitles.
# LOGO_WIDTH           : int  — logo image width in pixels (absolute, not fractional).
# LOGO_Y_OFFSET        : int  — distance in pixels from the bottom canvas edge to the
#                               bottom of the logo image.
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# 1080×1920 VERTICAL PORTRAIT — THREE-ZONE LAYOUT MATRIX
#
#  ZONE A  Upper Middle  y ~ 288–850 px   → PRIMARY HOOK TEXT (HOOK_Y_FRAC)
#  ZONE B  Lower Middle  y ~ 850–1632 px  → DYNAMIC SUBTITLES (SUBTITLE_Y_POSITION)
#  ZONE C  Bottom 15%   y ~ 1632–1920 px → BRAND LOGO (LOGO_BOTTOM_MARGIN)
#
# CRITICAL RULE: Zone A must sit entirely above Zone B.
# Never invert this hierarchy — subtitle must be below hook, logo must be below subtitle.
# ---------------------------------------------------------------------------
HOOK_Y_FRAC: float = 0.30         # Hook text vertical centre at 30% of canvas height (~576px)
SUBTITLE_FONTSIZE: int = 50       # Subtitle word size — readable over charcoal backgrounds
SUBTITLE_Y_POSITION: int = 1380   # Zone B: lower-middle — safely below hook block (Zone A)
LOGO_WIDTH: int = 180             # Maximum logo width in absolute pixels (per section 3 spec)
LOGO_MAX_HEIGHT: int = 45         # Maximum logo height in absolute pixels (per section 3 spec)
LOGO_OPACITY: float = 0.60        # 60% opacity — authentic, non-distracting blend on charcoal
LOGO_BOTTOM_MARGIN: int = 100     # Zone C: pixels from absolute bottom canvas edge to logo bottom

USE_STYLE_REFERENCE: bool = True   # Boolean toggle (True/False)
STYLE_CHARACTERS: str = (
    "A realistic man and a woman in an intense, dramatic relationship dynamic. "
    "They are the consistent recurring personas."
)

# ---------------------------------------------------------------------------
# Sketch / fine-art visual style directive for image generation.
# ENABLE_SKETCH_STYLE : bool  — True = use sketch pipeline; False = photorealistic fallback.
# SKETCH_STYLE_PROMPT : str   — injected into the Gemini prompt as the style directive.
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Single permanent base visual style — used for ALL post formats.
# No dynamic switching; this string is sent to Gemini every time.
# ---------------------------------------------------------------------------
BASE_GRAPHITE_PROMPT: str = (
    "A full-bleed, edge-to-edge, ultra-realistic master-level traditional graphite pencil "
    "drawing. The charcoal sketch must entirely fill the portrait frame — absolutely NO white "
    "margins, NO decorative paper borders, NO outer photo frames, NO background matting, and "
    "NO letterboxing of any kind. Features exquisite, hyper-detailed hand-drawn shading, "
    "intricate pencil strokes, and soft charcoal blending. COLOR PALETTE: Muted dark azure "
    "blue tones, deep charcoal black, and sharp white highlights only. ABSOLUTELY NO flat "
    "vectors, NO colorful cartoon graphics, NO solid black ink outlines, NO interior window "
    "frames, and NO borders enclosing the art. VISUAL COMPOSITION: A dark psychological "
    "surrealism scene featuring exactly two characters (one man and one woman) filling the "
    "entire canvas naturally. Only ONE character may exhibit a surreal transformation or "
    "psychological deception (like holding or peeling away a theatrical smiling human "
    "face-mask). The other character must remain a normal, realistic, emotionally vulnerable "
    "human to ground the scene's realism."
)

ENABLE_SKETCH_STYLE: bool = True
ENABLE_HORROR_TRANSFORMATIONS: bool = True  # New toggle: masks/monsters ON/OFF
SKETCH_STYLE_PROMPT: str = (
    "A raw, full-bleed hyper-realistic graphite pencil sketch. Features heavy charcoal "
    "cross-hatch shading, fine line work, and deep chiaroscuro lighting. The sketch must "
    "completely occupy 100% of the canvas as a continuous background art layer. "
    "COLOR SYSTEM: Strictly monochrome, limited entirely to dark azure blue tones, deep "
    "charcoal black, and white highlights. ABSOLUTELY NO flat vectors, NO colorful cartoon "
    "graphics, NO interior window frames, NO window sills, and NO borders enclosing the art."
)
RAW_GRAPHITE_HORROR_PROMPT: str = (
    "A full-frame, ultra-realistic, master-level traditional graphite pencil drawing on "
    "fine-grain paper texture. Features exquisite, hyper-detailed hand-drawn shading and "
    "soft charcoal blending. COLOR PALETTE: Muted dark azure blue tones, deep charcoal, "
    "and sharp white highlights. NO flat vector lines, NO cartoon shading, and NO borders. "
    "VISUAL STYLE: High-end dark psychological surrealism. Depict a deep metaphorical concept "
    "of deception, such as a person holding or peeling away a theatrical smiling human "
    "face-mask to reveal an intense, highly dramatic, altered surreal expression beneath to "
    "represent betrayal and hidden dual personalities."
)

# ---------------------------------------------------------------------------
# TOPIC_POOL — rotating topic seeds for ECONOMIC_REEL / SMART_BAIT production.
# When no --topic flag is supplied and the PDF corpus is empty, the pipeline
# picks a fresh topic from this pool instead of falling back to the generic
# "Holistic vitality protocol" placeholder, guaranteeing varied content every run.
# ---------------------------------------------------------------------------
TOPIC_POOL: list = [
    "Why we stay in relationships long past their expiration date",
    "The trauma bond that feels exactly like deep love",
    "When your nervous system confuses anxiety for attraction",
    "Why emotionally unavailable people feel like home",
    "The silent ways we betray ourselves to keep the peace",
    "Why people who hurt us are the hardest ones to leave",
    "How anxious attachment rewires your definition of love",
    "The moment you realise you were in love with potential, not the person",
    "Why we mistake intensity and chaos for passion",
    "How unhealed childhood wounds choose our romantic partners",
    "The exhausting performance of being 'low maintenance' to keep someone",
    "Why saying nothing teaches people exactly how to treat you",
    "The fear of abandonment disguised as deep devotion",
    "When loyalty to someone becomes disloyalty to yourself",
    "Why the people who love-bomb us feel like the answer to everything",
    "How to stop chasing people who are emotionally half-present",
    "The difference between being chosen and being convenient",
    "Why healing feels lonelier than the relationship that broke you",
    "How high empathy becomes a vulnerability that attracts narcissists",
    "The moment self-respect becomes more important than being loved",
]

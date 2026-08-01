# -*- coding: utf-8 -*-
"""
Master Mei | Mind Control — page-level configuration.

Persona: Eastern Master / Protocol for Liberation mentor.
Niche: Pai Mei cold discipline, financial warfare, traps of illusion (seduction),
       classical wisdom (Sun Tzu, Marcus Aurelius, Musashi, Buddhist detachment).
Visual: metaphorical cyberpunk × ancestral temple — BAN repetitive face close-ups.
Avatar: ON — cycles reference portraits from assets/master_mei/avatars/.

Cost mode: HARDCODED to "nano" (economic/lightweight) for all generation.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Core profile fields — satisfies BasePageProfile protocol
# ---------------------------------------------------------------------------
PAGE_ID: str = "master_mei"
PAGE_DISPLAY_NAME: str = "Master Mei | Mind Control"

CONTENT_NICHE: str = (
    "Protocol for Liberation: Pai Mei cold discipline, financial warfare & wealth creation, "
    "traps of illusion (seduction & narcissism), and classical strategy — Sun Tzu, "
    "Marcus Aurelius, Miyamoto Musashi, Buddhist mental detachment — for men navigating "
    "a cruel, distracted, dystopian modern world"
)

# ---------------------------------------------------------------------------
# YouTube — Master Mei | Mind Control ONLY (do not reuse on other pages)
# ---------------------------------------------------------------------------
YOUTUBE_PLAYLIST_TITLE: str = "Stoic Financial Freedom & Wealth Mindset"
YOUTUBE_PLAYLIST_DESCRIPTION: str = (
    "Welcome to the official Master Mei playlist — a Protocol for Liberation. "
    "Master Mei blends Pai Mei cold discipline, Stoic and classical strategy "
    "(Sun Tzu, Marcus Aurelius, Musashi), and financial sovereignty so men can escape "
    "digital slavery, reject seductive distraction, and compound unshakeable wealth. "
    "Subscribe to Master Mei | Mind Control for ancient wisdom applied to a dystopian modern world."
)
YOUTUBE_DEFAULT_TAGS: list = [
    "master mei",
    "mind control",
    "stoicism",
    "financial freedom",
    "wealth mindset",
    "stoic philosophy",
    "business strategy",
    "self discipline",
    "executive mindset",
    "personal finance",
    "shorts",
]

DEFAULT_AVATAR_MODE: str = "ON"
DEFAULT_FORMAT: str = "IMAGE_AVATAR"
IMAGE_ASPECT_RATIO: str = "3:4"
USES_AVATAR_REFERENCE: bool = True

# ---------------------------------------------------------------------------
# Cost tier — hardcoded to nano / economic for this channel
# ---------------------------------------------------------------------------
COST_TIER: str = "nano"
ENABLE_COST_TRACKING: bool = True

# Explicit image model override — bypasses all auto-discovery and tier defaults.
# Explicit image model override — Imagen SKU (never flash-lite text models)
IMAGE_MODEL_OVERRIDE: str = "black-forest-labs/FLUX.1-schnell"

# ---------------------------------------------------------------------------
# Economic brain mode — force lightweight models on every run
# ---------------------------------------------------------------------------
ECONOMIC_BRAIN_MODE: bool = True

# ---------------------------------------------------------------------------
# Narrative mode — drives script/prompt structure in caption + sequence engines
# ---------------------------------------------------------------------------
NARRATIVE_MODE: str = "warrior_discipline"

# ---------------------------------------------------------------------------
# Atmosphere / visual style
# Cinematic 4K martial/monastic photography — dark, intense, atmospheric.
# ---------------------------------------------------------------------------
# Neutral grade only — NEVER list Master Mei + cyberpunk slaves in one global blob.
# Per-scene subjects come from ROLE A/B/C builders (avatar_engine.visual_roles).
ATMOSPHERE_STYLE: str = (
    "Ultra-realistic cinematic 8k photography, detailed skin textures, octane render, "
    "volumetric lighting. Narrative contrast across SEPARATE scenes: traditional temple "
    "wisdom, organic disciple hardship training, OR neon dystopian tech addiction — "
    "never fused into one subject. "
    "STRICT BAN: ordinary teenagers in bedrooms, mundane modern rooms, casual lifestyle "
    "photography, soft wellness aesthetics. "
    "Colour grade rotates per role: ember gold temple light OR cold neon cyan/magenta."
)

# Neutral cinematic lock ONLY — subjects come from per-scene ROLE A/B/C builders.
# NEVER list Master Mei + cyberpunk slaves in the same global string (prompt leakage).
ILLUSTRATION_STYLE: str = (
    "Cinematic 8k photorealistic raw photography, detailed skin textures, octane render, "
    "volumetric lighting. Contrast narrative across scenes: traditional temple wisdom, "
    "organic disciple training, OR neon dystopian tech addiction — never mixed in one frame. "
    "NO teens in bedrooms. NO lifestyle soft-focus."
)

# ---------------------------------------------------------------------------
# Negative prompt terms — strip lifestyle/relationship contamination
# ---------------------------------------------------------------------------
PROMPT_NEGATIVE_TERMS: list = [
    "relationship therapy", "couple", "romantic", "cozy", "pastel",
    "morning light linen", "journaling wellness", "blush pink", "terracotta",
    "sketch", "graphite", "pencil drawing", "charcoal illustration",
    "azure blue sketch", "horror mask", "cartoon", "anime",
    "bright gym neon", "influencer selfie", "smiling lifestyle",
    "teenager bedroom", "teen bedroom", "smartphone desk", "ordinary bedroom",
    "casual living room", "modern apartment selfie",
    "deformed samurai", "warped body", "extra limbs", "mutated hands",
    "glitched geometry", "bad anatomy", "blurry faces", "distorted limbs",
    "extra fingers", "warped faces",
    "Chinese characters", "Japanese characters", "Korean characters", "CJK text",
    "kanji", "hangul", "hiragana", "katakana", "oriental calligraphy",
    "Chinese neon signs", "Japanese neon signs", "Asian neon typography",
    "static portrait", "blank stare", "looking at viewer", "idle pose",
]

# ---------------------------------------------------------------------------
# Typography
# ---------------------------------------------------------------------------
FONT_PATH: str = "Fonts/Montserrat/static/Montserrat-Bold.ttf"
FONT_SIZE_SCALE: float = 0.07
FONT_COLOR: tuple = (230, 235, 240)   # cold steel-white — legible on dark frames
TEXT_OUTLINE_WIDTH: int = 2

# ---------------------------------------------------------------------------
# Brand logo layout
# ---------------------------------------------------------------------------
LOGO_SIZE_SCALE: float = 0.30
LOGO_POSITION: str = "top_right"

# ---------------------------------------------------------------------------
# ECONOMIC_REEL / SEQUENCE_REEL — ElevenLabs voice settings
# Voice ID xf4rL1r9hbGxkztMunKX — deep, slow, highly meditative ancient master
# ---------------------------------------------------------------------------
ELEVENLABS_VOICE_ID: str = "xf4rL1r9hbGxkztMunKX"
ELEVENLABS_MODEL: str = "eleven_multilingual_v2"
TTS_VOICE_PREFERENCE: str = (
    "Master Mei — ancient, deep, authoritative, calm, grounded Eastern master; "
    "stoic Pai Mei sternness; slow deliberate delivery via punctuation pauses; never rushes"
)
# Slightly slower deliberate pace (0.92×) — avoids rushed delivery / distortion
TTS_NARRATION_SPEED: float = 0.92

# Official ElevenLabs voice_settings payload for Master Mei (stoic / deliberate)
ELEVENLABS_VOICE_SETTINGS: dict = {
    "stability": 0.80,
    "similarity_boost": 0.85,
    "style": 0.0,
    "use_speaker_boost": True,
    "speed": 0.92,
}

# ElevenLabs must receive PURE spoken text — never emotion/expression tags
AUDIO_BEHAVIOR_TAGS: list = [
    "[cackles]", "[dry laugh]", "[giggles]", "[cold chuckle]",
    "[arrogant scoff]", "[deep subtle laugh]", "[chuckle]", "[scoff]",
]
STRIP_AUDIO_TAGS_BEFORE_TTS: bool = True
# Pacing via punctuation (... / short sentences) — do NOT send SSML to ElevenLabs
TTS_ENABLE_SSML: bool = False

# ---------------------------------------------------------------------------
# Master Mei — MANDATORY visual DNA (Frame 1 + all Mei-present scenes)
# Likeness locked to pages_config/master_mei/avatar_reference/avatar.png
#
# Pure visual description ONLY — no rule/instruction sentences. This string
# is sent almost verbatim to the FLUX image API (via sanitize_prompt_for_flux
# as a safety net), so it must read as a plain physical description, not as
# code-level instructions. Identity lock (avatar.png binding), single-master
# exclusivity, and sparse-appearance frequency (max 3 shots) are ALL enforced
# in Python — see mei_visual.compute_mei_appearance_slots() and the
# reference_image_path wiring in main.py — never as English "rules" here.
# ---------------------------------------------------------------------------
MASTER_MEI_VISUAL_DNA: str = (
    "Master Mei is an OLDER East Asian sage (approx 72 years old), inspired by Pai Mei "
    "fused with an ancient Eastern master: implacable, uncompromising, stern, demanding. "
    "long white hair tied in a traditional topknot/bun, long flowing white beard, "
    "wise deeply lined face, calm authoritative presence. "
    "Wearing traditional dark robes with gold accents (deep charcoal / black silk with gold trim). "
    "NEVER a middle-aged short-haired warrior. NEVER modern athletic wear. "
    "NEVER clean-shaven. NEVER black hair. "
    "STRICT SINGLE MASTER RULE: Master Mei is the ONLY master/elder allowed."
)

# Force avatar.png likeness on Master Mei–present frames ONLY (Shot 1 + Final)
# Canonical path (resolved against ENGINE_ROOT):
#   pages_config/master_mei/avatar_reference/avatar.png
SEQUENCE_FORCE_AVATAR_OFF: bool = False
AVATAR_REFERENCE_PATH: str = (
    r"pages_config/master_mei/avatar_reference/avatar.png"
)
# Strong likeness lock — zero hallucination of generic old masters
AVATAR_IMAGE_WEIGHT: float = 0.92

# ---------------------------------------------------------------------------
# Sequence reel — July-24 golden keyframe cadence (v12/v14/v15 reference)
# Hard-cap 8–10 images per clip — NO dense ~19-image micro-segmentation
# Narration 150–180 words @ 0.92× + 1 s gap + CTA
# ---------------------------------------------------------------------------
REEL_DURATION: float = 80.0
REEL_SECONDS_PER_ACT: float = 9.0    # ~80s / 9 ≈ 9 keyframes
REEL_ACT_DURATION: float = 9.0
ENABLE_SEQUENCE_REEL: bool = True
# Total Images = round(audio_seconds / REEL_SECONDS_PER_ACT), clamped to
# [REEL_IMAGE_MIN_COUNT, REEL_IMAGE_COUNT] — hardcoded math, never LLM-guessed.
REEL_IMAGE_MIN_COUNT: int = 8        # floor: golden-era keyframe count
REEL_IMAGE_COUNT: int = 10           # ceiling: never more than 10 distinct shots
REEL_OVERLAY_OPACITY: float = 0.32
REEL_TAIL_PAD_S: float = 2.0
REEL_FORCE_EXACT_DURATION: bool = False
# 150–180 words @ 0.80× deliberate delivery ≈ 80–90 s story + separate CTA
REEL_NARRATION_WORDS: int = 165
REEL_NARRATION_MIN_WORDS: int = 150
REEL_NARRATION_MAX_WORDS: int = 180

# Single approved CTA — stitched AFTER narration; never duplicated inside the script
REEL_CTA_TEXT: str = (
    "Master your mind. Follow Master Mei to reclaim your sovereignty."
)

# ---------------------------------------------------------------------------
# MODULE 3 — Modular style reference configuration
# Style images are loaded dynamically from pages_config/master_mei/style_reference/
# (falls back to STYLE_REFERENCE_DIR override below if set). 2–3 images max,
# weight 0.65–0.80 (IP-Adapter / style-ref strength where the backend supports it).
# ---------------------------------------------------------------------------
STYLE_REFERENCE_DIR: str = ""        # optional override; default = auto pages_config/<page>/style_reference/
# Re-aligned to reinforce dense flesh-bolted hardware (not clean fitness looks).
STYLE_REFERENCE_WEIGHT: float = 0.55
STYLE_REFERENCE_MAX_IMAGES: int = 3

# Fixed Master Style Anchor — July-24 golden hybrid lock
MASTER_STYLE_ANCHOR: str = (
    "Cinematic 8k photorealistic raw photography, detailed skin textures, "
    "octane render, volumetric lighting — role-separated contrast: "
    "ancestral temple OR organic training OR cyberpunk dystopia (never fused)"
)

# ---------------------------------------------------------------------------
# Ambient audio — Dark Atmospheric Synth Pad + Inspiring Cinematic Sub-Bass Drone
# Bed 14–18%; ducked under VO (~55% of bed) so it stays impactful without clipping.
# ---------------------------------------------------------------------------
AMBIENT_VOLUME: float = 0.16
VOICE_VOLUME_GAIN: float = 1.0       # no raw API/stream gain — loudnorm handles levels
AMBIENT_SFX_GAIN_MUL: float = 1.0    # disable compounding SFX boost (distortion fix)
AMBIENT_DUCK_RATIO: float = 0.55     # ambient × this while voice plays
AMBIENT_SFX_PROMPT: str = (
    "Dark atmospheric synth pad combined with an inspiring cinematic sub-bass drone, "
    "deep low-frequency swell, subtle inspirational harmonic pad, seamless loop, 60 BPM, "
    "warm dark cinematic underscore, no vocals, no melody lead, "
    "NO rain, NO thunder, NO white noise, NO hiss, NO static, NO water drops, NO storm"
)
# Prefer new cinematic pad; do NOT use legacy rain/martial rain loops
AMBIENT_AUDIO_RELPATH: str = "assets/master_mei/audio/ambient_cinematic_pad.mp3"

# ---------------------------------------------------------------------------
# Hook / Act-1 environments — Master Mei ONLY (Shot 1).
# Stark monolithic shrine OR high stone ledge over ash wasteland.
# NEVER monastery ice, bamboo terraces, or piles of cyber junk.
# ---------------------------------------------------------------------------
HOOK_ENVIRONMENTS: list = [
    "mist-covered ancient mountain temple courtyard at dawn",
    "rain-soaked ancestral samurai temple under storm light",
    "high-altitude stone training ground above the clouds",
    "roaring waterfall training ledge beside an ancient temple",
    "temple balcony overlooking misty mountains — no neon, no city tech",
    "snowy peak with wind-blown snow and distant pagoda silhouette",
    "bamboo forest path with soft morning fog",
    "tatami room with tea set and soft paper-lantern light",
]

# ---------------------------------------------------------------------------
# Avatar asset cycling — priority directory for Master Mei likeness refs
# ---------------------------------------------------------------------------
AVATAR_ASSETS_DIR: str = "assets/master_mei/avatars"

# ---------------------------------------------------------------------------
# SEQUENCE_REEL — visual identity layer
# ---------------------------------------------------------------------------
VIGNETTE_STRENGTH: float = 0.55
GRAIN_INTENSITY: float = 20.0
ENABLE_TOP_HOOK_TEXT: bool = False
ENABLE_FLICKER: bool = True          # torch / storm flicker
ENABLE_LIGHT_RAYS: bool = True       # volumetric rain/moonlight shafts
ENABLE_DUST_PARTICLES: bool = True   # mist / rain particulate drift
ENABLE_LIGHT_REFRACTION: bool = False

# ---------------------------------------------------------------------------
# SEQUENCE_REEL — video layout
# ---------------------------------------------------------------------------
HOOK_Y_FRAC: float = 0.25
# V4 Cinematic Gold / Warm Yellow subtitles — Montserrat Bold, lower-third (~18% from bottom)
SUBTITLE_FONTSIZE: int = 58
SUBTITLE_Y_POSITION: int = 1570  # ~18% from bottom; never over Mei face/action
SUBTITLE_WORDS_PER_PHRASE: int = 4  # 3–5 words; 100% word-sync via timings
SUBTITLE_FILL: tuple = (255, 204, 0)         # #FFCC00 cinematic warm yellow
SUBTITLE_STROKE_FILL: tuple = (0, 0, 0)      # clean black outer stroke
SUBTITLE_STROKE_WIDTH: int = 3
LOGO_WIDTH: int = 360
LOGO_MAX_HEIGHT: int = 100
LOGO_OPACITY: float = 0.75
LOGO_BOTTOM_MARGIN: int = 90

# ---------------------------------------------------------------------------
# Style flags
# ---------------------------------------------------------------------------
ENABLE_SKETCH_STYLE: bool = False
ENABLE_HORROR_TRANSFORMATIONS: bool = False
USE_STYLE_REFERENCE: bool = False
STYLE_CHARACTERS: str = ""

PINTEREST_BOARD_ID: str = ""

# ---------------------------------------------------------------------------
# TOPIC_POOL — rotating seeds across four Liberation Protocol pillars
# ---------------------------------------------------------------------------
TOPIC_POOL: list = [
    # ── Pillar A: Pai Mei Cold Discipline ────────────────────────────────────
    "Cheap dopamine is the softest cage modern men walk into willingly",
    "Pai Mei discipline rejects comfort — condition the body until craving loses authority",
    "Self-discipline is not motivation — it is a nervous system forged under extreme conditions",
    "The man who cannot refuse pleasure will never command capital or himself",
    "Hard truths first: soft living is training for slavery",
    "Quit the dopamine drip or watch ambition dissolve into noise",

    # ── Pillar B: Financial Warfare & Wealth Creation ────────────────────────
    "The digital rat race is a panopticon — glowing cables where chains used to be",
    "Focus is capital — spend it on noise and you bankrupt the mission",
    "Build unshakeable assets or remain a cog in someone else's empire",
    "Financial sovereignty is a training protocol — not a motivational quote",
    "Money follows the operator who treats his calendar like capital allocation",
    "Escape wage slavery by compounding attention into unshakeable wealth",

    # ── Pillar C: Traps of Illusion (Sedução & Narcisismo) ───────────────────
    "Seduction is a battlefield — walk past the siren or become its slave",
    "Hyper-attractive distraction is engineered to derail the warrior mid-path",
    "Narcissistic allure feeds on your attention capital — starve it",
    "Reject superficial illusion; mastery is cold refusal without negotiation",
    "The siren of the phone and the siren of the flesh use the same trap",
    "Beauty without purpose is a weapon aimed at your discipline",

    # ── Pillar D: Classical Wisdom & Strategy ────────────────────────────────
    "Sun Tzu mapped the enemy — today the enemy is your own unlocked phone",
    "Marcus Aurelius built an inner citadel — build yours before the market opens",
    "Musashi's Book of Five Rings: strike once with discipline, not a hundred times with impulse",
    "Buddhist mental detachment: watch craving rise and refuse to obey it",
    "Classical strategy applied to dystopia — know the terrain of attention warfare",
    "Ancient wisdom is not nostalgia — it is the only protocol that survives modernity",
]

# ---------------------------------------------------------------------------
# Niche directives — injected into LLM system prompts (IMMUTABLE DNA)
# ---------------------------------------------------------------------------
NICHE_DISCLAIMER: str = (
    "CHANNEL DIRECTIVE — MASTER MEI | MIND CONTROL (V4.0 CINEMATIC STORY ARC — IMMUTABLE): "
    "Engine: V4.0_CINEMATIC_STORY_ARC. Coherent Beginning/Middle/End story — NEVER tip lists. "
    "EXPLICIT philosopher citations required (Sun Tzu Art of War / Marcus Aurelius Stoicism / "
    "Siddhartha Gautama Buddhism / Foucault Panopticon). ONE thinker + ONE concept per video. "
    "Tone: highly stern, accessible, cinematic — ancient wisdom × biomechanical cyberpunk. "
    "AUDIENCE: Western / US men — self-mastery, tech-critique, financial sovereignty. "
    "3-ACT ARC (~80–90 s): "
    "(I) SEDUCTION & PHILOSOPHICAL DIAGNOSIS — cite authority; Machine's silent war; "
    "(II) FORGE OF DISCIPLINE — Master Mei 50%+; sword katas in cold rain/neon smoke; "
    "(III) LIBERATION & WEALTH SOVEREIGNTY — 'Sever the cords. Protect your focus and "
    "your capital… reclaim your ultimate freedom.' "
    "WORD TARGET: 150–180 words MAX at 0.80×. "
    "DO NOT write Follow/Subscribe CTA inside narration — stitched separately after. "
    "Approved CTA (separate audio only): "
    "'Master your mind. Follow Master Mei to reclaim your sovereignty.' "
    "CLOTHING SAFETY: no nudity, no nipple exposure, no gym-tank fitness looks. "
    "VISUAL: Western-majority enslaved masses + stylish women on holographic/VR feeds (Act I); "
    "Master Mei Oriental/Asian ancestral forge guide (Act II); cord-severing liberation (Act III). "
    "BAN distorted limbs, extra fingers, warped faces, static portraits, idle samurai. "
    "VOICE: pure spoken prose, ellipses between heavy truths. "
    "FORBIDDEN: emotion tags, SSML, wellness fluff, hustle-bro clichés, therapy-speak."
)

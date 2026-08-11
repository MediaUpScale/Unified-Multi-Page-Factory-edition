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
ELEVENLABS_MODEL: str = "eleven_v3"  # expressive stoic delivery
TTS_EXPRESSIVE_MODE: bool = True
TTS_VOICE_PREFERENCE: str = (
    "Master Mei — ancient, deep, authoritative, calm, grounded Eastern master; "
    "stoic Pai Mei sternness; slow deliberate delivery via punctuation pauses; never rushes"
)
# Deliberate stoic pace (0.85–0.90×) + strategic <break time="1.5s"/> → 100–120 s
TTS_NARRATION_SPEED: float = 0.86

# Official ElevenLabs voice_settings payload for Master Mei (stoic / deliberate)
ELEVENLABS_VOICE_SETTINGS: dict = {
    "stability": 0.80,
    "similarity_boost": 0.85,
    "style": 0.20,           # mild expressiveness with eleven_v3
    "use_speaker_boost": True,
    "speed": 0.86,
}

# Strip emotion/expression tags; keep strategic SSML break pauses for pacing
AUDIO_BEHAVIOR_TAGS: list = [
    "[cackles]", "[dry laugh]", "[giggles]", "[cold chuckle]",
    "[arrogant scoff]", "[deep subtle laugh]", "[chuckle]", "[scoff]",
]
STRIP_AUDIO_TAGS_BEFORE_TTS: bool = True
# Allow <break time="1.5s"/> after key philosophical impacts (100–120 s target)
TTS_ENABLE_SSML: bool = True

# ---------------------------------------------------------------------------
# Master Mei — MANDATORY visual DNA (Frame 1 + all Mei-present scenes)
# Likeness locked to channels_config/master_mei/avatar_reference/avatar.png
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
    "Master Mei — wise East Asian martial arts grandmaster meditating serenely. "
    "Pure radiant snow-white hair (zero gray/ash/silver) in a high topknot with "
    "hairpin, plus two long distinct white strands falling down both sides of his "
    "chest and shoulders. Iconic extra-long ultra-thick snow-white eyebrows sweeping "
    "dramatically outward past his temples. Full majestic snow-white beard extending "
    "to mid-chest with matching flowing snow-white mustache. Weathered skin, deep-set "
    "eyes, calm meditative expression. White inner robe, black outer vest with gold "
    "lapel embroidery, wooden mala prayer beads. "
    "OLDER East Asian sage (approx 72 years old), implacable, uncompromising, stern. "
    "NEVER gray or silver hair. NEVER a middle-aged short-haired warrior. "
    "NEVER modern athletic wear. NEVER clean-shaven. NEVER black hair. "
    "STRICT SINGLE MASTER RULE: Master Mei is the ONLY master/elder allowed."
)

# Force avatar.png likeness on Master Mei–present frames ONLY (Shot 1 + Final)
# Canonical path (resolved against ENGINE_ROOT):
#   channels_config/master_mei/avatar_reference/avatar.png
SEQUENCE_FORCE_AVATAR_OFF: bool = False
AVATAR_REFERENCE_PATH: str = (
    r"channels_config/master_mei/avatar_reference/avatar.png"
)
# Strong likeness lock — zero hallucination of generic old masters
AVATAR_IMAGE_WEIGHT: float = 0.92

# ---------------------------------------------------------------------------
# Sequence reel — July-24 golden keyframe cadence (v12/v14/v15 reference)
# Hard-cap 8–10 images per clip — NO dense ~19-image micro-segmentation
# Dynamic duration profiles (see mei_narrative.resolve_mei_duration_profile):
#   90–100s → 8 frames  | Scene1 ≤8s | body 10–12s
#  100–112s → 9 frames  | Scene1 ≤8s | body 10–12s
#  112–120s → 10 frames | Scene1 ≤8s | body 10–12s
# ---------------------------------------------------------------------------
REEL_DURATION: float = 105.0         # primary target: 90–120 s band
REEL_SECONDS_PER_ACT: float = 11.0   # body scenes ~10–12s (Scene 1 capped at 8s)
REEL_ACT_DURATION: float = 11.0
REEL_HOOK_MAX_S: float = 8.0         # Scene 1 (meditation hook) hard cap
REEL_BODY_MIN_S: float = 10.0
REEL_BODY_MAX_S: float = 12.0
ENABLE_SEQUENCE_REEL: bool = True
# Frame count from duration profile (8 / 9 / 10); hard clamps
REEL_IMAGE_MIN_COUNT: int = 8
REEL_IMAGE_COUNT: int = 10
REEL_OVERLAY_OPACITY: float = 0.32
REEL_TAIL_PAD_S: float = 2.0
REEL_FORCE_EXACT_DURATION: bool = False
# Defaults mirror ~105 s / 9-frame profile (overridden dynamically at runtime)
REEL_NARRATION_WORDS: int = 142
REEL_NARRATION_MIN_WORDS: int = 130
REEL_NARRATION_MAX_WORDS: int = 155
# Max consecutive training/exercise scenes (composition rule)
REEL_MAX_CONSEC_TRAINING: int = 2

# Single approved CTA — stitched AFTER narration; never duplicated inside the script
REEL_CTA_TEXT: str = (
    "Master your mind. Follow Master Mei to reclaim your sovereignty."
)

# ---------------------------------------------------------------------------
# MODULE 3 — Modular style reference configuration
# Style images are loaded dynamically from channels_config/master_mei/style_reference/
# (falls back to STYLE_REFERENCE_DIR override below if set). 2–3 images max,
# weight 0.65–0.80 (IP-Adapter / style-ref strength where the backend supports it).
# ---------------------------------------------------------------------------
STYLE_REFERENCE_DIR: str = ""        # optional override; default = auto channels_config/<page>/style_reference/
# Re-aligned to reinforce dense flesh-bolted hardware (not clean fitness looks).
STYLE_REFERENCE_WEIGHT: float = 0.55
STYLE_REFERENCE_MAX_IMAGES: int = 3

# Fixed Master Style Anchor — July-24 golden hybrid lock
MASTER_STYLE_ANCHOR: str = (
    "Cinematic natural photography, detailed skin textures, volumetric lighting — "
    "role-separated contrast: ancestral temple OR organic training OR cyberpunk "
    "dystopia attention monopoly era (never fused). No quality tag spam."
)

# ---------------------------------------------------------------------------
# Three-channel mix — VO 1.0 + ambient SFX 0.35 @ t=0 + BGM 0.24 @ t=0.5
# Industrial cyberpunk percussion bed; near-instant ambient fade-in.
# ---------------------------------------------------------------------------
AMBIENT_VOLUME: float = 0.24         # BGM / music bed (MoviePy volumex 0.24; −20% from 0.30)
VOICE_VOLUME_GAIN: float = 1.0       # narration primary — unity
IMPACT_SFX_VOLUME: float = 0.0       # DISABLED — percussion lives in the music bed
ATMOSPHERE_SFX_VOLUME: float = 0.35  # ambient audible from t=0 (prevents dead air)
ATMOSPHERE_SFX_FADE_IN: float = 0.2  # near-instantaneous ambient fade-in
AMBIENT_SFX_GAIN_MUL: float = 1.0    # no extra mul — volumes are absolute
AMBIENT_DUCK_RATIO: float = 0.85     # light duck only — keep tension audible
MASTER_AUDIO_GAIN: float = 1.0       # unity master (avoid clipping)
USE_MUSIC_V2_BED: bool = True        # prefer music_v2 composition plan over local pad
MUSIC_V2_MIN_SECONDS: float = 40.0   # ElevenLabs BGM minimum generation length
ATMOSPHERE_SFX_MIN_SECONDS: float = 10.0  # single 10s drone tile (looped 100%)
DISABLE_IMPACT_SFX: bool = True      # hard off — percussion comes from BGM track
MUSIC_TRACK_STYLE: str = "industrial_cyberpunk_percussion"
# Audio Mix Configuration
BGM_START_TIME: float = 0.5          # Music starts at 0.5s (no long delayed fade-in)
BGM_FADE_IN_DURATION: float = 0.2    # near-instant music entry
SFX_VOLUME_GAIN_DB: float = 0.0      # absolute volumes; no extra dB boost
AMBIENT_SFX_PROMPT: str = (
    "Continuous 10-second industrial cyberpunk atmospheric bed, dark metallic drone, "
    "factory tension hum, dystopian machinery rumble, seamless loop, no vocals"
)
IMPACT_SFX_PROMPT: str = "Cinematic Braam, Dystopian Sub-Bass Drop"
# Local pad used only when music_v2 generation fails
AMBIENT_AUDIO_RELPATH: str = "assets/master_mei/audio/ambient_cinematic_pad.mp3"

# ---------------------------------------------------------------------------
# Hook / Act-1 environments — Master Mei ONLY (Shot 1, ≤8s).
# Weighted at runtime 70% high-altitude nature / 30% open-air mountain shrines.
# NEVER generic indoor temples, cyber junk, neon city, or modern tech.
# ---------------------------------------------------------------------------
HOOK_ENVIRONMENTS_PRIMARY: list = [
    "towering jagged Himalayan precipice above a sea of clouds",
    "high alpine cliff ledge overlooking endless cloud valleys",
    "misty mountain ridge summit under cold wind",
    "natural mountain spring carved into living rock at high altitude",
    "rocky Alps precipice at golden hour above rolling fog",
    "snow-dusted peak ledge with vast alpine vista",
    "sheer storm-lit cliff above a boiling sea of clouds",
    "high-altitude stone outcrop above endless mist valleys",
]
HOOK_ENVIRONMENTS_SECONDARY: list = [
    "open-air ancient stone shrine on a mountain terrace during rain",
    "outdoor mountain courtyard temple veiled in thick fog",
    "rain-soaked open-air stone shrine overlooking alpine ridges",
    "misty outdoor mountain temple courtyard with wet stone and incense haze",
]
# Flat pool kept for page_loader / legacy callers (picker enforces 70/30)
HOOK_ENVIRONMENTS: list = HOOK_ENVIRONMENTS_PRIMARY + HOOK_ENVIRONMENTS_SECONDARY

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
SUBTITLE_WORDS_PER_PHRASE: int = 4  # 3–5 words / display; hard-capped at 2 wrapped lines
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
    "POV LOCK: The narrator IS Master Mei — write ONLY in FIRST PERSON "
    "('I', 'My disciples', 'I demand', 'My path'). "
    "PROHIBITED: third-person 'Master Mei' / 'Follow Master Mei' inside narration body. "
    "DEEP PHILOSOPHY (3-STEP, no name-dropping): (1) Define the mechanism in one clear sentence; "
    "(2) Expose how algorithms and impulse gratification exploit it to hijack focus and extract capital; "
    "(3) Present my radical mental and financial discipline as the sovereign antidote. "
    "FINANCIAL SOVEREIGNTY: Money = energy and freedom — NEVER stock tips, interest rates, or budgeting. "
    "Distracted mind → impulse consumption and wealth transferred to system owners; "
    "sovereign mind → retain focus, stop the financial bleed, build unbreakable capital and autonomy. "
    "Tone: highly stern, accessible, cinematic — ancestral discipline × dystopian tech critique. "
    "AUDIENCE: Western / US men — self-mastery, tech-critique, financial sovereignty. "
    "Follow MASTER SCRIPTWRITER directive: Insidious Leak → Filter of Resistance → "
    "Purposeful Capital & Sovereign Legacy. Attention + Capital are one linked drain. "
    "DYNAMIC DURATION: 80s→110–125w/7f; 100s→140–155w/9f; 120s→170–185w/10f. "
    "PACING: Insert <break time=\"1.5s\"/> after key philosophical impacts. "
    "PHRASE BLACKLIST (never reuse): citadel, biomechanical cords, sirens, "
    "'Master Mei offers no comfort', relentless practice, 'silent war for your essence', techno-slave. "
    "Every episode needs a UNIQUE allegory — never recycle the same lesson template. "
    "DO NOT write Follow/Subscribe CTA inside narration — stitched separately after. "
    "Approved CTA (separate audio only): "
    "'Master your mind. Follow Master Mei to reclaim your sovereignty.' "
    "CLOTHING SAFETY: no nudity, no nipple exposure, no gym-tank fitness looks. "
    "VISUAL: cinematic wide angle / extreme long shot / full body / epic scope only — "
    "BAN close-up, tight shot, face zoom, macro, single monk portrait, sweating close-up. "
    "FORBIDDEN: emotion tags (except allowed break pauses), wellness fluff, hustle-bro clichés."
)

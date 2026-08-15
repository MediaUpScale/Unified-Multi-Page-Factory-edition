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

from config import words_for_duration

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

# Explicit image model — Together AI FLUX.1-schnell (ultra cost-efficient)
IMAGE_MODEL_OVERRIDE: str = "black-forest-labs/FLUX.1-schnell"

# ---------------------------------------------------------------------------
# Economic brain mode — force lightweight models on every run
# ---------------------------------------------------------------------------
ECONOMIC_BRAIN_MODE: bool = True   # page-level override; respected by main.py bootstrap

# ---------------------------------------------------------------------------
# Atmosphere / visual style
# Hyper-realistic, cinematic historical photography — NOT stylised/illustrated.
# ---------------------------------------------------------------------------
ATMOSPHERE_STYLE: str = (
    "Ultra-realistic cinematic photography of iconic ancient monuments — Great Pyramids, Baalbek, "
    "Göbekli Tepe, Puma Punku, Sacsayhuamán, Easter Island, Stonehenge. "
    "Dramatic single-source lighting: warm amber torchlight or cold blue-white ethereal glow. "
    "Volumetric light shafts cutting through atmospheric haze and dust particles. "
    "Strong directional RIM LIGHTING tracing the edges of megalithic stone blocks. "
    "Deep chiaroscuro contrast — rich shadow pools balanced by intense focal highlights. "
    "35mm documentary film grain, desaturated ochre and shadow-black colour grade. "
    "Speculative impossible elements integrated naturally: precision machining, alien glyphs, "
    "advanced-technology artefacts, out-of-place objects, impossible construction scale. "
    "NO flat lighting, NO muddy underexposed scenes, NO clean CGI. Full photographic realism only."
)

ILLUSTRATION_STYLE: str = (
    "Ultra-realistic, high-contrast cinematic documentary photograph. "
    "Iconic real-world monument (Pyramid, Baalbek megalith, Göbekli Tepe pillar, Puma Punku block) "
    "anchoring the frame — instantly recognisable global landmark. "
    "Single dramatic light source: warm amber fire or cold ethereal moonlight. "
    "Volumetric dust-haze beams, strong rim lighting on carved stone edges, deep shadow contrast. "
    "Impossible anomalous detail woven into the scene naturally: precision tool marks, "
    "alien-script glyphs, impossible engineering, speculative ancient technology. "
    "35mm film grain, deep ochre and shadow black colour grade. "
    "NO illustration, NO sketch, NO CGI, NO flat lighting, NO modern elements."
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
LOGO_SIZE_SCALE: float = 0.38      # static image watermark — matches reel pixel ratio (420/1080)
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
TTS_NARRATION_SPEED: float = 1.0                       # natural pace — never speed up AK reels
ELEVENLABS_VOICE_SETTINGS: dict = {
    "stability": 0.80,
    "similarity_boost": 0.85,
    "style": 0.20,
    "use_speaker_boost": True,
    "speed": 1.0,
}

# F5-TTS remote voice reference (channels_config/.../voice_reference/).
# Filenames are optional overrides — default convention is
#   ancient_knowledge_voice_ref_10s.wav + matching .txt
# VOICE_REFERENCE_AUDIO: str = "ancient_knowledge_voice_ref_10s.wav"
# VOICE_REFERENCE_TEXT: str = "ancient_knowledge_voice_ref_10s.txt"

# ---------------------------------------------------------------------------
# Remote GPU Flux LoRA (ENABLE_REMOTE_GPU_WORKFLOWS=true)
# Set REMOTE_GPU_LORA_NAME = None / "" to run base Flux with no LoRA.
# Trigger word is auto-prepended into every remote image prompt for this page.
# ---------------------------------------------------------------------------
REMOTE_GPU_LORA_NAME: str = "IDunnohowtonameLora-lenovo-trigger-l3n0v0"
REMOTE_GPU_LORA_TRIGGER: str = "l3n0v0"
REMOTE_GPU_LORA_STRENGTH: float = 0.8
# Hosted URL for Together image_loras (HF resolve link). Local .safetensors name
# above is used by ComfyUI; Together cannot read local paths — upload once, paste URL.
REMOTE_GPU_LORA_URL: str = ""

# ---------------------------------------------------------------------------
# Sequence reel — ECONOMIC_REEL (still/parallax). Independent of WAN_REEL.
# Target window 70–90 s (default midpoint 80). Scene pacing via shared
# plan_scenes() engine (SCENE_DURATION) — not hardcoded act counts.
# Clip weights at compile scale to actual audio length (never hard-cut VO).
# ---------------------------------------------------------------------------
REEL_DURATION_TARGET_MIN: float = 80.0
REEL_DURATION_TARGET_MAX: float = 90.0
REEL_DURATION: float = 85.0          # planning target (= video_length default)
PACING_SEQUENCE: list = [3, 3, 4, 4]  # legacy fallback shape only — AK now uses plan_bucket_act_durations (self-calibrating body weights, no slack absorption)
REUSE_EXISTING_IMAGES: bool = True
ENABLE_SUBTITLE_PADDING: bool = True
ENCODING_PRESET: str = "ultrafast"
# Shared pacing engine (core_engine/scene_pacing.py). CLI --scene-duration wins.
SCENE_DURATION: str = "progressive"
SCENE_PROGRESSIVE_START_S: float = 4.0
SCENE_PROGRESSIVE_STEP_EVERY: int = 3
SCENE_PROGRESSIVE_STEP_S: float = 1.0
SCENE_PROGRESSIVE_CAP_S: float = 7.5
REEL_SECONDS_PER_ACT: float = 5.5    # legacy dense fallback only
REEL_ACT_DURATION: float = 5.5
ENABLE_SEQUENCE_REEL: bool = True
REEL_IMAGE_MIN_COUNT: int = 8        # soft floor for plan_scenes
REEL_IMAGE_COUNT: int = 16           # soft ceiling for plan_scenes @ ~80 s
REEL_NARRATION_WORDS: int = words_for_duration(REEL_DURATION_TARGET_MIN)
REEL_NARRATION_MIN_WORDS: int = words_for_duration(REEL_DURATION_TARGET_MIN)
REEL_NARRATION_MAX_WORDS: int = words_for_duration(REEL_DURATION_TARGET_MIN) + 20
CONTENT_LIBRARY_LOOKBACK: int = 40   # reject repeating a monument/theme in last N posts

# WAN_REEL duration window — separate from ECONOMIC_REEL (do not merge).
# Animated blocks use FIXED clip length (never progressive image pacing).
# Wan reads duration_s via prepare_video_workflow → Duration PrimitiveFloat.
WAN_REEL_DURATION_TARGET_MIN: float = 70.0
WAN_REEL_DURATION_TARGET_MAX: float = 120.0
WAN_REEL_DURATION: float = 70.0      # default test: 10 × fixed:7
WAN_SCENE_DURATION: str = "fixed:7"  # parameterised Wan clip length

REEL_OVERLAY_OPACITY: float = 0.30   # lighter overlay — let the cinematic image breathe

# Narrative + CTA (read generically via page_loader)
NARRATIVE_MODE: str = "investigative"
REEL_CTA_TEXT: str = "Follow Ancient Knowledge for more hidden mysteries."

# ---------------------------------------------------------------------------
# Three-channel audio mix (ported from master_mei topology)
# VO 1.0 + atmosphere SFX loop + music_v2 BGM bed (mystery prompt)
# Local AMBIENT_AUDIO_RELPATH is fallback ONLY when generation fails.
# ---------------------------------------------------------------------------
USE_MUSIC_V2_BED: bool = True
# Mix vs VO at 1.0: −18 dB = 0.126 (unducked CTA/tail), −20 dB = 0.100 under narration.
AMBIENT_VOLUME: float = 0.126        # BGM bed ≈ −18 dB relative to voiceover
ATMOSPHERE_SFX_VOLUME: float = 0.12  # soft atmosphere (also ducked under VO)
ATMOSPHERE_SFX_FADE_IN: float = 0.2
AMBIENT_SFX_GAIN_MUL: float = 1.0
AMBIENT_DUCK_RATIO: float = 0.80     # under VO: 0.126 × 0.80 ≈ 0.101 ≈ −20 dB
BGM_START_TIME: float = 0.5
BGM_FADE_IN_DURATION: float = 0.35
MUSIC_V2_MIN_SECONDS: float = 40.0
AMBIENT_SFX_PROMPT: str = (
    "Dark mysterious ancient temple atmosphere, deep sub-bass stone chamber drone, "
    "distant wind through ruins, eerie low resonance, no rhythm, no percussion, "
    "no melody, no vocals, seamless loop"
)
AMBIENT_AUDIO_RELPATH: str = "assets/audio/ambient_mystery_loop.mp3"
MUSIC_PROMPT_DIRECTIVE_RELPATH: str = (
    "channels_config/ancient_knowledge/prompts/music_prompt_directive.txt"
)
AMBIENT_MUSIC_STYLE: str = "mystery"  # music_prompt soft-enforce profile

# ---------------------------------------------------------------------------
# SEQUENCE_REEL — visual identity layer
# ---------------------------------------------------------------------------
VIGNETTE_STRENGTH: float = 0.60      # dark corner vignette (0 = off, 1 = full black corners)
GRAIN_INTENSITY: float = 22.0        # film grain amplitude in pixel value units (default 18)
ENABLE_TOP_HOOK_TEXT: bool = False   # do not burn headline at top; only lower-third subtitles
ENABLE_FLICKER: bool = True          # ±5 % torch/flame brightness oscillation — archival atmosphere
ENABLE_LIGHT_RAYS: bool = True       # warm-gold volumetric beam sweeping across each scene
ENABLE_DUST_PARTICLES: bool = True   # floating dust drifting upward — ruin/chamber atmosphere
ENABLE_LIGHT_REFRACTION: bool = False  # prismatic crystal glow (enable per-topic if crystal subject)

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
# Hook strategy: each topic opens with a REAL recognisable anchor, then
# introduces the impossible / extraterrestrial / suppressed-history element.
# ---------------------------------------------------------------------------
TOPIC_POOL: list = [
    # ── Pyramids & Egypt ────────────────────────────────────────────────────
    "The Great Pyramid — precision that modern engineers say is impossible to replicate today",
    "Inside the Great Pyramid's hidden chambers — what ground-penetrating radar just found",
    "The Sphinx enclosure erosion that suggests it was built before the last ice age",
    "Ancient Egyptian glyphs that show what appears to be a helicopter, submarine, and aircraft",
    "The Dendera Temple light bulb reliefs — ancient Egyptian electricity or sacred symbol",
    "The Osireion at Abydos and the megalithic construction that predates all known Egyptian history",

    # ── Baalbek & Impossible Megaliths ──────────────────────────────────────
    "The 1,500-tonne Trilithon stones of Baalbek — no crane on Earth can lift them today",
    "Baalbek's Hajar al-Wubr stone — the largest single quarried block in human history",
    "Puma Punku's H-blocks at 13,000 feet — machined precision no Bronze Age tool could achieve",
    "Sacsayhuamán's 100-tonne interlocking stones — no mortar, no gaps, no explanation",
    "The Nazca Lines — vast geoglyphs only visible and meaningful from 1,500 feet in the air",

    # ── Göbekli Tepe & Pre-Flood Civilisations ──────────────────────────────
    "Göbekli Tepe was deliberately buried 10,000 years ago — by whom and why",
    "Göbekli Tepe's animal carvings encode a comet impact that triggered the Younger Dryas",
    "Plato's Atlantis — the geological evidence researchers say has been hiding in plain sight",
    "Tartaria and the global mudflood theory — where did all these buried first-floor windows go",
    "The Younger Dryas Impact — a civilisation-ending comet 12,800 years ago that reset humanity",

    # ── OOPArts & Impossible Artefacts ──────────────────────────────────────
    "The Antikythera Mechanism — a 2,000-year-old analogue computer the ancient world should not have had",
    "The Baghdad Battery — Mesopotamian artefacts that appear to be ancient galvanic cells",
    "The Voynich Manuscript — an undeciphered illustrated book that has defeated every cryptographer",
    "Ancient Indian Vimana flying machines — the Sanskrit texts that describe anti-gravity aircraft",
    "The Paracas Elongated Skulls — cranial volumes 25 % larger than any human on record",
    "The Crystal Skulls of ancient Mesoamerica — why modern analysis found no tool marks",

    # ── Lost Cities & Hidden History ────────────────────────────────────────
    "Yonaguni Monument — Japan's submerged stepped pyramid 90 feet under the Pacific",
    "The lost golden city of El Dorado — new Lidar scans in the Amazon just changed everything",
    "Dwarka: India's sunken city 40 metres below the Gulf of Khambhat, dated to 9,000 BC",
    "The Library of Alexandria — what was really inside, and who burned it and why",
    "Easter Island's Moai were once standing in the ocean floor — what the underwater survey found",
    "Oak Island's Money Pit — 200 years of excavation, six deaths, and no definitive answer",

    # ── Ancient Astronaut & ET Contact ──────────────────────────────────────
    "The Anunnaki tablets of Sumer — ancient Mesopotamians who claimed gods arrived from the sky",
    "The Nazca Lines' spider glyph — the only known representation of a spider found nowhere in Peru",
    "Prehistoric cave paintings across four continents show the same disc-shaped flying object",
    "The Sirius Mystery — how did the Dogon tribe in Mali know about Sirius B 300 years before telescopes",
    "The ancient nuclear blast at Mohenjo-daro — vitrified stone, epicentre, and 50,000 casualties",
    "The Saqqara Bird — a 2,200-year-old carved wooden object that aerodynamically flies as a glider",
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
    "Never present a theory as established fact. "
    "HOOK STRATEGY: Always open with a REAL, RECOGNISABLE world anchor "
    "(an iconic monument, a famous discovery, a confirmed historical site) "
    "and then introduce the HIGH-CONCEPT IMPOSSIBLE element — "
    "the precision that should not exist, the scale no modern machine could achieve, "
    "the artefact that defies its era, or the alien/advanced-technology influence. "
    "This anchor → impossibility structure maximises curiosity and scroll-stopping engagement."
)

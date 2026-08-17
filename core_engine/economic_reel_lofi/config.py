# -*- coding: utf-8 -*-
"""ECONOMIC_REEL_LOFI — duration, style, and per-channel assembly config."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

# ── Scene timing ────────────────────────────────────────────────────────────
SCENE_DURATION_S: float = 3.0
DEFAULT_DURATION_S: int = 27  # 9 scenes × 3 s
MIN_DURATION_S: int = 27
MAX_DURATION_S: int = 27
MIN_SCENES: int = 9
MAX_SCENES: int = 9

# ── Script / validation ─────────────────────────────────────────────────────
MAX_CAPTION_CHARS: int = 42  # one line at ~65px; ~3–6 words
MAX_CAPTION_WORDS: int = 7
CAPTION_HOLD_S: float = 0.55  # breath after the last word before a cut
AUTHORITY_QUOTE_PROBABILITY: float = 0.0  # story monologue, not quote-maxims
SCRIPT_MAX_RETRIES: int = 3
DEDUP_SIMILARITY_THRESHOLD: float = 0.85
IMAGE_MAX_RETRIES_PER_SCENE: int = 2

VALID_MODULES: frozenset[str] = frozenset({"relationship", "parenting"})
MODULE_PAGE_GATES: dict[str, frozenset[str]] = {
    "relationship": frozenset({"momma_circle", "wonder_feed"}),
    "parenting": frozenset({"momma_circle"}),
}
VALID_PAGES: frozenset[str] = frozenset({"momma_circle", "wonder_feed"})

# Riso library is the primary prompt source (verbatim). This base is fallback only
# when a prompt is missing style language — never used to override library palettes.
LOFI_STYLE_BASE: str = (
    "riso print illustration, rich saturated nostalgic color palette, "
    "bold flat color blocks with fine hand-inked linework, grainy halftone texture, "
    "no smooth gradient, hard-edged color shading, vintage poster illustration style, "
    "no photography, vertical 9:16 composition"
)
# Verbatim riso prompts — do not prepend mood lighting that remaps palette.
USE_RISO_PROMPT_LIBRARY: bool = True
RISO_PROMPT_LIBRARY_REL: str = "core_engine/economic_reel_lofi/store/riso_prompt_library_v2.json"
# OFF — duotone/LUT crushed scene palettes (blue wash / TV-static first frames).
LOFI_APPLY_GRADING: bool = False

# Per-scene palette rotation + matching grading duotone pairs.
# Avoid "light source" / glow / backlight wording — those bias Flux toward radial skies.
# Keep each palette phrase short so base+palette+scene stays under ~300 chars.
LIGHTING_MOODS: tuple[dict[str, Any], ...] = (
    {
        "id": "amber_dusk",
        "lighting": "flat warm amber and teal palette",
        "shadow": (28, 72, 88),
        "highlight": (250, 185, 105),
    },
    {
        "id": "moonlit",
        "lighting": "flat cool blue and navy palette",
        "shadow": (18, 32, 78),
        "highlight": (190, 210, 235),
    },
    {
        "id": "overcast",
        "lighting": "flat sage grey and cream palette",
        "shadow": (42, 48, 58),
        "highlight": (220, 218, 210),
    },
    {
        "id": "golden_hour",
        "lighting": "flat gold and plum palette",
        "shadow": (55, 42, 62),
        "highlight": (255, 205, 95),
    },
    {
        "id": "indigo_night",
        "lighting": "flat indigo and pale lavender palette",
        "shadow": (16, 18, 58),
        "highlight": (165, 175, 225),
    },
)

# Backward-compat alias: base + first mood lighting (not used for generation).
LOFI_STYLE_PREFIX: str = f"{LOFI_STYLE_BASE}, {LIGHTING_MOODS[0]['lighting']}"

LOFI_NEGATIVE_PROMPT: str = (
    "photorealistic, photograph, 3d render, cgi, smooth vector art, "
    "radial glow, volumetric lighting, soft atmospheric haze, gradient sky wash, "
    "blown highlights, white bloom, halo, overexposed whites, blown-out chest, "
    "collar bloom, vignette bleed, overbright center wash, "
    "painterly blended lighting, monochromatic, grayscale, silhouette only, no linework, "
    "text, watermark, logo, typography, subtitles, ui, deformed hands, extra fingers, "
    "garbled text, nsfw, nude, explicit"
)
# Appended to Flux prompts (does not remap riso palettes).
LOFI_PROMPT_EXPOSURE_GUARD: str = (
    "even midtone exposure, no blown highlights, no white bloom or halo, "
    "no overexposed chest or collar, no vignette bleed"
)

# Soft highlight clamp in prep_base_frame (before Ken Burns) — kills baked bloom.
ENABLE_HIGHLIGHT_CLAMP: bool = True
HIGHLIGHT_SOFT_KNEE: float = 208.0
HIGHLIGHT_COMPRESS: float = 0.48
HIGHLIGHT_CLAMP_CEILING: float = 236.0

# ── Video canvas / motion ───────────────────────────────────────────────────
REEL_WIDTH: int = 1080
REEL_HEIGHT: int = 1920
REEL_FPS: int = 30
# Ken Burns — barely-perceptible drift (3–5% scale), ease-in-out, no parallax
KEN_BURNS_ZOOM_START: float = 1.00
KEN_BURNS_ZOOM_END: float = 1.04  # 4% across the scene
KEN_BURNS_ZOOM_CAP: float = 1.05  # hard ceiling
KEN_BURNS_EASE: str = "smootherstep"  # ease-in-out
# Tiny residual drift only — reference is near-static
ENABLE_KINETIC_PAN: bool = True
KEN_BURNS_PAN_AMP_X: float = 5.0
KEN_BURNS_PAN_AMP_Y: float = 3.0
# Animated lighting pulse (measurable luminance delta ~0.5–1s apart)
ENABLE_LIGHT_BREATH: bool = True
LIGHT_BREATH_AMP: float = 0.065  # more perceptible than 0.045
LIGHT_BREATH_PERIOD_S: float = 6.5
LIGHT_BREATH_SOURCE_BIAS: float = 0.70  # bias toward bright sources
LIGHT_BREATH_BLOOM: float = 0.012  # keep off baked chest/collar bloom
LIGHT_BREATH_DEBUG: bool = True  # log brightness_factor independently of zoom
# Procedural numpy grain — OFF; replaced by reference overlay film grain.mp4
ENABLE_PROCEDURAL_GRAIN: bool = False
GRAIN_OPACITY: float = 0.0
GRAIN_HALF_RES: bool = True
# Film-stock multiply BEHIND caption only
CAPTION_FILM_MULTIPLY_OPACITY: float = 0.18
# Reference film-grain overlay (Screen), below caption; loop/trim to video duration
ENABLE_DUST_OVERLAY: bool = True
DUST_OVERLAY_REL: str = "channels_config/wonder_feed/overlays/overlay film grain.mp4"
DUST_OVERLAY_PREFER_NPZ: bool = False
DUST_OVERLAY_OPACITY: float = 0.32  # 20–35% screen
DUST_OVERLAY_BLEND: str = "screen"
# Dark vintage vignette (independent of particles)
ENABLE_VIGNETTE: bool = True
VIGNETTE_STRENGTH: float = 0.18  # ~15–20% corner darken
# Legacy procedural dust (disabled when overlay asset is used)
ENABLE_DUST_PARTICLES: bool = False
DUST_PARTICLE_COUNT: int = 48
DUST_PARTICLE_OPACITY: float = 0.12
# Library BGM — random pick, trimmed to clip length (no generated beds)
BGM_DIR_REL: str = "channels_config/wonder_feed/audio/bgm"
BGM_VOLUME: float = 0.38  # duck under VO
BGM_EXCLUDE_PREFIXES: tuple[str, ...] = ("lofi_bed",)
REQUIRE_BGM: bool = True
# Voiceover (ElevenLabs)
ENABLE_VOICEOVER: bool = True
LOFI_VOICE_ID: str = "hNtG3AcS155nfu8sfWXk"
LOFI_VOICE_VOLUME: float = 1.0
LOFI_VOICE_SPEED: float = 0.80  # ElevenLabs settings API (0.7–1.2); 0.9 still read fast
REQUIRE_VOICEOVER: bool = True
# Defaults = amber_dusk pair (legacy grading path only; LOFI_APPLY_GRADING=False)
DUOTONE_SHADOW: tuple[int, int, int] = (28, 72, 88)
DUOTONE_HIGHLIGHT: tuple[int, int, int] = (250, 185, 105)
DUOTONE_TONAL_BANDS: int = 4
GRAIN_INTENSITY: float = 0.028  # legacy still-grade additive

# Text-handle watermark height as fraction of frame
WATERMARK_SIZE_FRAC: float = 0.018

# Caption typography — Edu NSW ACT Foundation Bold, Whispers-small, tight tracking
DEFAULT_CAPTION_STYLE: str = "edu_nsw"
CAPTION_LINE_HEIGHT_FRAC: float = 0.034  # ~65px at 1920h; one-line target
CAPTION_MIN_LINE_HEIGHT_FRAC: float = 0.032  # wrap/shrink is rare fallback only
CAPTION_LETTER_SPACING_PX: float = -1.5  # tighter tracking
CAPTION_WORD_FADE_S: float = 0.20  # soft per-word fade-in
CAPTION_STYLES: frozenset[str] = frozenset(
    {
        "edu_nsw",
        "caveat",
        "playwrite_nz_basic",
        "more_sugar_thin",
        "lora_italic",
        "rounded_hand",
    }
)

# ── Per-channel watermark / brand ───────────────────────────────────────────
CHANNEL_ASSEMBLY: dict[str, dict[str, Any]] = {
    "momma_circle": {
        "logo_candidates": [
            "assets/logos/momma_circle_watermark.png",
            "channels_config/momma_circle/logo/logo.png",
        ],
        # Text watermark — preferred for LOFI cohesion vs PNG logo
        "watermark_handle": "@Momma Circle",
        "logo_position": "bottom_center",
        "logo_opacity": 0.55,
        "logo_scale": 0.04,  # was 0.06 (~33% smaller)
        "caption_color": (255, 255, 255),
        "use_text_watermark": True,
    },
    "wonder_feed": {
        "logo_candidates": [
            "channels_config/wonder_feed/logo/logo.png",
        ],
        "watermark_handle": "@Wonder Feed",
        "logo_position": "bottom_center",
        "logo_opacity": 0.88,
        "logo_scale": 0.161,  # +15% vs 0.14
        "logo_bottom_px": 70,  # raise ~30% vs 48px inset
        "caption_color": (255, 255, 255),
        # Use channel PNG logo on LOFI stills/reels (not text handle).
        "use_text_watermark": False,
    },
}

_PACKAGE_DIR = Path(__file__).resolve().parent
DATA_DIR = _PACKAGE_DIR / "data"
STORE_DIR = _PACKAGE_DIR / "store"  # JSON-backed RAG store (Chroma optional)


def lighting_mood_by_id(mood_id: str | None) -> dict[str, Any]:
    """Resolve a mood dict by id; default to amber_dusk."""
    want = (mood_id or "").strip().lower()
    for row in LIGHTING_MOODS:
        if str(row["id"]) == want:
            return dict(row)
    return dict(LIGHTING_MOODS[0])


def select_lighting_mood(
    key: str | int | None = None,
    *,
    mood_id: str | None = None,
) -> dict[str, Any]:
    """
    Pick a lighting/mood variant.

    - Explicit ``mood_id`` wins when provided.
    - Else stable hash of ``key`` (theme/scene/index) rotates through the bank.
    - Else first mood (amber_dusk).
    """
    if mood_id:
        return lighting_mood_by_id(mood_id)
    if key is None or key == "":
        return dict(LIGHTING_MOODS[0])
    raw = str(key).encode("utf-8")
    digest = hashlib.md5(raw).hexdigest()
    idx = int(digest[:8], 16) % len(LIGHTING_MOODS)
    return dict(LIGHTING_MOODS[idx])


def build_style_prefix(mood: dict[str, Any] | None = None) -> str:
    """Short positive style prefix with per-scene lighting descriptor swapped in."""
    m = mood or LIGHTING_MOODS[0]
    lighting = str(m.get("lighting") or "").strip()
    if lighting:
        return f"{LOFI_STYLE_BASE}, {lighting}"
    return LOFI_STYLE_BASE


def scene_count_for_duration(duration_s: int | float) -> int:
    """Scene count = round(duration / SCENE_DURATION_S), clamped to min/max."""
    d = float(duration_s)
    n = int(round(d / SCENE_DURATION_S))
    return max(MIN_SCENES, min(MAX_SCENES, n))


def validate_duration(duration_s: int | float) -> int:
    d = int(round(float(duration_s)))
    if d < MIN_DURATION_S or d > MAX_DURATION_S:
        raise ValueError(
            f"--duration must be {MIN_DURATION_S}–{MAX_DURATION_S} seconds "
            f"(got {d})"
        )
    return d


def validate_module_for_page(module: str, page_id: str) -> str:
    mod = (module or "relationship").strip().lower()
    page = (page_id or "").strip().lower()
    if mod not in VALID_MODULES:
        raise ValueError(
            f"--module must be one of {sorted(VALID_MODULES)} (got {module!r})"
        )
    allowed = MODULE_PAGE_GATES.get(mod, frozenset())
    if page not in allowed:
        raise ValueError(
            f"--module {mod!r} is not allowed for --page {page!r}. "
            f"Allowed pages for this module: {sorted(allowed)}"
        )
    if page not in VALID_PAGES:
        raise ValueError(
            f"ECONOMIC_REEL_LOFI supports pages {sorted(VALID_PAGES)} "
            f"(got {page!r})"
        )
    return mod


def resolve_logo_path(page_id: str, engine_root: Path) -> Path | None:
    cfg = CHANNEL_ASSEMBLY.get(page_id.lower()) or {}
    for rel in cfg.get("logo_candidates") or []:
        p = Path(rel)
        if not p.is_absolute():
            p = engine_root / p
        if p.is_file():
            return p
    return None


def channel_assembly_cfg(page_id: str) -> dict[str, Any]:
    return dict(CHANNEL_ASSEMBLY.get(page_id.lower()) or CHANNEL_ASSEMBLY["wonder_feed"])

# -*- coding: utf-8 -*-
"""ECONOMIC_REEL_LOFI — duration, style, and per-channel assembly config."""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any

# ── Scene timing ────────────────────────────────────────────────────────────
SCENE_DURATION_S: float = 3.0
# Thematic default: 9 beats × 3.0s = 27.0s (loss baseline). Writer may emit 8–9;
# anything above 9 is clamped unless --duration is explicitly longer than 27s.
LOCK_FIXED_BEAT_DURATION: bool = True
# Never hard-trim finished VO. If a beat's VO exceeds 3.0s, that slot extends
# (small variance, typically 3.0–3.4s). Do not add CAPTION_HOLD_S on top.
NEVER_TRIM_VOICEOVER: bool = True
VO_SLOT_PAD_S: float = 0.05
DEFAULT_DURATION_S: int = 27  # thematic default total; writer target = 9 scenes
MIN_DURATION_S: int = 15
MAX_DURATION_S: int = 30
MIN_SCENES: int = 8
MAX_SCENES: int = 12
THEMATIC_DEFAULT_SCENES: int = 9
THEMATIC_MAX_SCENES: int = 9

# ── Script / validation ─────────────────────────────────────────────────────
MAX_CAPTION_CHARS: int = 42  # object-arc one-liner
MAX_CAPTION_WORDS: int = 7
# Thematic beats stay short (4–9 words) but may be sentence fragments.
THEMATIC_MAX_CAPTION_CHARS: int = 56
THEMATIC_MAX_CAPTION_WORDS: int = 9
THEMATIC_ARC_ID: str = "thematic_arc"
# Wonder Feed + Momma Circle production default (object arcs remain in the bank).
DEFAULT_ARC_BY_MODULE: dict[str, str] = {
    "relationship": "thematic_arc",
    "parenting": "thematic_arc",
}
CAPTION_HOLD_S: float = 0.55  # breath after the last word before a cut
AUTHORITY_QUOTE_PROBABILITY: float = 0.0  # story monologue, not quote-maxims
SCRIPT_MAX_RETRIES: int = 3
DEDUP_SIMILARITY_THRESHOLD: float = 0.85
# Auto-reject if a script copies stored wf1–4 / mc1–3 transcripts.
REFERENCE_OVERLAP_THRESHOLD: float = 0.80
# Total tries per scene = IMAGE_MAX_RETRIES_PER_SCENE + 1.
# Cap at 2 attempts so a systematic fail cannot 3–4× its own cost.
IMAGE_MAX_RETRIES_PER_SCENE: int = 1
IMAGE_ATTEMPTS_PER_SCENE: int = 2
# Stop the episode once image calls would exceed this × beat count.
IMAGE_CALL_BUDGET_MULT: float = 2.0


def image_call_budget(n_beats: int) -> int:
    n = max(1, int(n_beats))
    return max(n, int(math.ceil(n * IMAGE_CALL_BUDGET_MULT)))
# Core Mode (a) retrieval — not quote/philosopher mode (b)
CORE_DETAIL_COUNT: int = 3
REQUIRE_ANCHOR_OBJECT: bool = True
REQUIRE_BEAT_CONCRETENESS: bool = True
THEMATIC_HOOK_TYPES: frozenset[str] = frozenset(
    {"bold_claim", "question", "statistic", "definition", "rhetorical_question"}
)


def is_thematic_arc(arc_id: str | None) -> bool:
    return str(arc_id or "").strip() == THEMATIC_ARC_ID


def caption_limits(arc_id: str | None = None) -> tuple[int, int]:
    """Return (max_words, max_chars) for this arc."""
    if is_thematic_arc(arc_id):
        return THEMATIC_MAX_CAPTION_WORDS, THEMATIC_MAX_CAPTION_CHARS
    return MAX_CAPTION_WORDS, MAX_CAPTION_CHARS


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
# Parallel v2 identity bank (does NOT overwrite the live riso library file).
USE_VISUAL_IDENTITY_V2: bool = True
VISUAL_IDENTITY_V2_REL: str = (
    "core_engine/economic_reel_lofi/store/visual_identity_bank_v2.json"
)
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
    "flat graphic, solid color field, empty poster, no outlines, abstract blob, "
    "text, watermark, logo, typography, subtitles, ui, deformed hands, extra fingers, "
    "garbled text, nsfw, nude, explicit"
)
# Appended to Flux prompts (does not remap riso palettes).
LOFI_PROMPT_EXPOSURE_GUARD: str = (
    "even midtone exposure, no blown highlights, no white bloom or halo, "
    "no overexposed chest or collar, no vignette bleed"
)
LOFI_PROMPT_LINEWORK_GUARD: str = (
    "fine black ink outlines, detailed interior objects, not a flat graphic, "
    "not a solid color field, not an empty silhouette poster"
)

# ── FLUX.1-dev (ECONOMIC_REEL_LOFI_FLUXDEV) ────────────────────────────────
# Schnell constants above are unchanged. CFG=0 is inherent to Schnell; do not
# try to make LOFI_NEGATIVE_PROMPT steer that path.
LOFI_DEV_IMAGE_MODEL: str = "black-forest-labs/FLUX.1-dev"
LOFI_DEV_IMAGE_STEPS: int = 20
# Mid of the 3.5–5.0 test range. Override per probe if needed.
LOFI_DEV_GUIDANCE_SCALE: float = 4.0
DEFAULT_VISUAL_IDENTITY_PROFILE: str = "style-riso_painting_retro_vintage"
# Live ECONOMIC_REEL_LOFI stays Schnell unless this is "dev".
# Override: set env LOFI_FLUX_BACKEND=dev for a Flux Dev episode.
LOFI_FLUX_BACKEND: str = str(os.environ.get("LOFI_FLUX_BACKEND") or "schnell").strip()


def uses_flux_dev() -> bool:
    return LOFI_FLUX_BACKEND.lower() in {"dev", "flux_dev", "flux.1-dev"}


# Exact 9:16 (0.5625), both axes ÷16, 0.92 MP — under Flux Dev's ~1 MP native
# budget. 1080×1920 delivery is a uniform 1.5× scale (no crop, no stretch).
# Previous 768×1344 was 0.5714 and forced ImageOps.fit to crop ~1.6%.
LOFI_IMAGE_WIDTH: int = 720
LOFI_IMAGE_HEIGHT: int = 1280
LOFI_IMAGE_WIDTH_LEGACY: int = 768
LOFI_IMAGE_HEIGHT_LEGACY: int = 1344
# Mean wall-clock s/image observed on DeepInfra Dev @ 768×1344 × 20 steps
# (2026-08-22 hope run, ~11 calls / ~200 s). Used only to scale a time guess.
LOFI_DEV_SEC_PER_IMAGE_LEGACY: float = 18.0

# Bump when the locked look changes (silhouette, negatives, profile).
STILL_STYLE_VERSION: str = "riso_retro_flat_v2"


def current_still_style_tag() -> str:
    """Identity stamped on every still this process writes."""
    if uses_flux_dev():
        return (
            f"{DEFAULT_VISUAL_IDENTITY_PROFILE}/dev/{STILL_STYLE_VERSION}"
        )
    return f"schnell_live/schnell/{STILL_STYLE_VERSION}"


def still_style_sidecar_path(image_path: Path) -> Path:
    p = Path(image_path)
    return p.with_name(f"{p.stem}.style.json")


def write_still_style_sidecar(
    image_path: Path,
    *,
    run_id: str = "",
    reused: bool = False,
    style_tag: str | None = None,
) -> dict[str, Any]:
    rec = {
        "style_tag": str(style_tag or current_still_style_tag()),
        "visual_identity_profile": (
            DEFAULT_VISUAL_IDENTITY_PROFILE if uses_flux_dev() else "schnell_live"
        ),
        "flux_backend": "dev" if uses_flux_dev() else "schnell",
        "style_version": STILL_STYLE_VERSION,
        "pipeline_run": run_id or Path(image_path).parent.name,
        "reused": bool(reused),
        "gen_width": LOFI_IMAGE_WIDTH,
        "gen_height": LOFI_IMAGE_HEIGHT,
        "aspect": round(LOFI_IMAGE_WIDTH / LOFI_IMAGE_HEIGHT, 6),
    }
    src = Path(image_path)
    if src.is_file():
        try:
            from PIL import Image as PILImage

            with PILImage.open(src) as im:
                rec["gen_width"], rec["gen_height"] = int(im.size[0]), int(im.size[1])
                if rec["gen_height"]:
                    rec["aspect"] = round(rec["gen_width"] / rec["gen_height"], 6)
        except Exception:  # noqa: BLE001
            pass
    side = still_style_sidecar_path(image_path)
    side.write_text(json.dumps(rec, indent=2), encoding="utf-8")
    return rec


def read_still_style_sidecar(image_path: Path) -> dict[str, Any] | None:
    side = still_style_sidecar_path(image_path)
    if not side.is_file():
        return None
    try:
        data = json.loads(side.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    return data if isinstance(data, dict) else None


def still_style_tag_of(image_path: Path) -> str | None:
    rec = read_still_style_sidecar(image_path)
    if not rec:
        return None
    tag = str(rec.get("style_tag") or "").strip()
    return tag or None


def allow_mixed_era_assemble() -> bool:
    return str(os.environ.get("LOFI_ALLOW_MIXED_ERA") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def lofi_image_cost_per_call_usd(
    width: int | None = None,
    height: int | None = None,
    schnell_steps: int | None = None,
) -> tuple[float, dict[str, Any]]:
    """
    Per-image USD for the backend this process will actually POST.

    Dev live path bills DeepInfra FLUX-1-dev
    ($0.009 × w/1024 × h/1024 × steps/25), not Together's $0.025 flat
    (Together serverless Dev is unavailable). Schnell uses DeepInfra's
    $0.0005 × megapixel × steps formula.
    """
    from avatar_engine.providers.together_image import (
        estimate_deepinfra_dev_cost_usd,
        estimate_deepinfra_schnell_cost_usd,
        estimate_together_image_cost,
    )

    width = int(width if width is not None else LOFI_IMAGE_WIDTH)
    height = int(height if height is not None else LOFI_IMAGE_HEIGHT)

    if uses_flux_dev():
        steps = int(LOFI_DEV_IMAGE_STEPS)
        usd = estimate_deepinfra_dev_cost_usd(width, height, steps)
        return usd, {
            "backend": "dev",
            "provider": "deepinfra",
            "model": LOFI_DEV_IMAGE_MODEL,
            "steps": steps,
            "guidance_scale": LOFI_DEV_GUIDANCE_SCALE,
            "allow_lora": False,
            "usd_per_image": usd,
            "formula": "0.009*(w/1024)*(h/1024)*(steps/25)",
            "together_flat_usd": estimate_together_image_cost(LOFI_DEV_IMAGE_MODEL),
            "note": (
                "Together serverless Flux Dev is unavailable; "
                "billed DeepInfra FLUX-1-dev formula."
            ),
        }
    steps = int(schnell_steps or 4)
    usd = estimate_deepinfra_schnell_cost_usd(width, height, steps)
    return usd, {
        "backend": "schnell",
        "provider": "deepinfra",
        "model": "black-forest-labs/FLUX-1-schnell",
        "steps": steps,
        "usd_per_image": usd,
        "formula": "0.0005*(w/1024)*(h/1024)*steps",
        "note": "DeepInfra Schnell dashboard formula.",
    }


def lofi_image_cost_delta() -> dict[str, Any]:
    """Old 768×1344 vs current 720×1280, same backend/steps. For run-log delta."""
    old_usd, old_meta = lofi_image_cost_per_call_usd(
        LOFI_IMAGE_WIDTH_LEGACY, LOFI_IMAGE_HEIGHT_LEGACY
    )
    new_usd, new_meta = lofi_image_cost_per_call_usd(
        LOFI_IMAGE_WIDTH, LOFI_IMAGE_HEIGHT
    )
    px_old = LOFI_IMAGE_WIDTH_LEGACY * LOFI_IMAGE_HEIGHT_LEGACY
    px_new = LOFI_IMAGE_WIDTH * LOFI_IMAGE_HEIGHT
    px_ratio = px_new / px_old
    cost_ratio = new_usd / old_usd if old_usd else 0.0
    drift = abs(cost_ratio - px_ratio)
    scaling = (
        "linear_with_pixels"
        if drift < 0.01
        else ("better_than_linear" if cost_ratio < px_ratio else "worse_than_linear")
    )
    time_est = (
        LOFI_DEV_SEC_PER_IMAGE_LEGACY * px_ratio
        if uses_flux_dev()
        else None
    )
    return {
        "old_size": f"{LOFI_IMAGE_WIDTH_LEGACY}x{LOFI_IMAGE_HEIGHT_LEGACY}",
        "new_size": f"{LOFI_IMAGE_WIDTH}x{LOFI_IMAGE_HEIGHT}",
        "old_mp": round(px_old / 1e6, 4),
        "new_mp": round(px_new / 1e6, 4),
        "old_usd_per_image": round(old_usd, 6),
        "new_usd_per_image": round(new_usd, 6),
        "usd_delta": round(new_usd - old_usd, 6),
        "pixel_ratio": round(px_ratio, 4),
        "cost_ratio": round(cost_ratio, 4),
        "scaling": scaling,
        "steps": new_meta.get("steps"),
        "formula": new_meta.get("formula"),
        "time_est_s_linear": None if time_est is None else round(time_est, 2),
        "time_baseline_s": LOFI_DEV_SEC_PER_IMAGE_LEGACY if uses_flux_dev() else None,
        "time_baseline_note": (
            "legacy 768x1344 Dev ~18s/image; time guess assumes attention-bound "
            "linear scaling with pixels"
            if uses_flux_dev()
            else None
        ),
        "old_meta": old_meta,
        "new_meta": new_meta,
    }


# LOFI-owned Dev negative for style-riso_painting_retro_vintage.
# Do NOT merge MANDATORY_NEGATIVE_PROMPT. Environment may be painterly;
# ban photo/camera, glamour skin, text, clutter, hands, nsfw.
LOFI_DEV_NEGATIVE_PROMPT: str = (
    "photorealistic, photograph, photography, photorealistic rendering, "
    "3d render, cgi, "
    "beauty lighting, glamour portrait, off-shoulder, sensual pose, fashion close-up, "
    "frontal close-up face, three-quarter beauty crop, looking at viewer, "
    "mug, cup, coffee cup, coffee, drinking glass, wine glass, phone, smartphone, "
    "keys, keyring, laptop, extra clutter, extra props, extra objects, "
    "deformed hands, incorrect hands, extra fingers, extra hands, fused fingers, "
    "missing fingers, hands merging into torso, bad anatomy, mutated hands, "
    "standing on the mattress, standing on top of the bed, figures standing on bedding, "
    "bokeh, depth of field, lens flare, dslr, camera lens, photographic blur, "
    "photographic light falloff, cinematic lighting, volumetric sunset, "
    "lens-like glow, atmospheric perspective haze, photoreal sky gradient, "
    "golden hour photography, photo-grain, "
    "legible text, readable letters, watermark, signature, logo, typography, "
    "subtitles, ui, garbled text, "
    "monochrome, grayscale, blown highlights, white bloom, halo, overexposed whites, "
    "radial glow, volumetric lighting, nsfw, nude, explicit, gore"
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
DUST_OVERLAY_OPACITY: float = 1.0  # single-pass screen
DUST_OVERLAY_BLEND: str = "screen"
DUST_OVERLAY_OPACITY_CAP: float = 1.0
DUST_OVERLAY_GAIN: float = 2.0  # multiply overlay pixels before blend, clip 255
# Two-pass chroma restore — OFF
DUST_OVERLAY_COLOR_OPACITY: float = 0.0
DUST_OVERLAY_CHROMA_GAIN: float = 0.0
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
CAPTION_LINE_HEIGHT_FRAC: float = 0.033  # ~63px at 1920h; one point down from 0.034
CAPTION_MIN_LINE_HEIGHT_FRAC: float = 0.031
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
        "logo_opacity": 0.95,
        "logo_scale": 0.224,  # +5% vs 0.213
        "logo_bottom_px": 389,  # center ≈ 412px from bottom (was ~262; +150)
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


def thematic_max_scenes(duration_s: int | float | None = None) -> int:
    """Hard cap for thematic_arc. Default 9; only rises if duration > 27s."""
    cap = int(THEMATIC_MAX_SCENES)
    if duration_s is None:
        return cap
    d = float(duration_s)
    if d > float(THEMATIC_DEFAULT_SCENES) * float(SCENE_DURATION_S) + 0.05:
        n = int(round(d / SCENE_DURATION_S))
        return max(cap, min(MAX_SCENES, n))
    return cap


def scene_count_for_duration(
    duration_s: int | float,
    *,
    thematic: bool = True,
) -> int:
    """Writer target scene count. Thematic default is 9, not round(24/3)=8."""
    if thematic:
        d = float(duration_s)
        if d > float(THEMATIC_DEFAULT_SCENES) * float(SCENE_DURATION_S) + 0.05:
            n = int(round(d / SCENE_DURATION_S))
            return max(THEMATIC_DEFAULT_SCENES, min(MAX_SCENES, n))
        return int(THEMATIC_DEFAULT_SCENES)
    d = float(duration_s)
    n = int(round(d / SCENE_DURATION_S))
    return max(MIN_SCENES, min(MAX_SCENES, n))


def slot_duration_for_vo(
    vo_dur: float,
    *,
    base_s: float | None = None,
) -> tuple[float, bool]:
    """Return (slot_s, extended). Slot is at least 3.0s; grows to fit VO, never shrinks VO."""
    base = float(base_s if base_s is not None else SCENE_DURATION_S)
    vo = max(0.0, float(vo_dur or 0.0))
    pad = float(VO_SLOT_PAD_S)
    if vo > base + 0.05:
        return round(vo + pad, 3), True
    return base, False


def duration_for_beat_count(n_beats: int) -> float:
    """Nominal total if every beat is exactly SCENE_DURATION_S (VO-extend can add a little)."""
    n = max(1, int(n_beats))
    return round(float(n) * float(SCENE_DURATION_S), 3)


def beat_duration_s() -> float:
    return float(SCENE_DURATION_S)


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

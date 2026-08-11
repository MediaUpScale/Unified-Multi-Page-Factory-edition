# -*- coding: utf-8 -*-
"""ECONOMIC_REEL_LOFI — duration, style, and per-channel assembly config."""
from __future__ import annotations

from pathlib import Path
from typing import Any

# ── Scene timing ────────────────────────────────────────────────────────────
SCENE_DURATION_S: float = 4.0
DEFAULT_DURATION_S: int = 34
MIN_DURATION_S: int = 30
MAX_DURATION_S: int = 38
MIN_SCENES: int = 7
MAX_SCENES: int = 10

# ── Script / validation ─────────────────────────────────────────────────────
MAX_CAPTION_CHARS: int = 90
AUTHORITY_QUOTE_PROBABILITY: float = 0.15
SCRIPT_MAX_RETRIES: int = 3
DEDUP_SIMILARITY_THRESHOLD: float = 0.85
IMAGE_MAX_RETRIES_PER_SCENE: int = 2

VALID_MODULES: frozenset[str] = frozenset({"relationship", "parenting"})
MODULE_PAGE_GATES: dict[str, frozenset[str]] = {
    "relationship": frozenset({"momma_circle", "wonder_feed"}),
    "parenting": frozenset({"momma_circle"}),
}
VALID_PAGES: frozenset[str] = frozenset({"momma_circle", "wonder_feed"})

# Fixed Flux style prefix — ink / graphic-novel LOFI look (no LoRA).
LOFI_STYLE_PREFIX: str = (
    "hand-inked graphic novel illustration, visible dark ink linework and contour lines, "
    "halftone dot shading in shadow areas, visible textured paper grain, "
    "warm amber highlight light source against cool teal shadow tones, "
    "detailed character rendering (hair strands, clothing fabric folds, posture), "
    "detailed environment texture (grass blades, clouds, distant birds or structures), "
    "illustrated comic-panel style, vertical 9:16 composition — "
    "explicitly NOT photographic, NOT smooth vector, NOT flat gradient background, "
    "NOT monochromatic, no text, no watermark, no logo"
)

LOFI_NEGATIVE_PROMPT: str = (
    "photorealistic, photograph, 3d render, cgi, smooth vector art, flat color fill, "
    "flat gradient sky, monochromatic, grayscale, silhouette only, no linework, "
    "text, watermark, logo, typography, subtitles, ui, deformed hands, extra fingers, "
    "garbled text, nsfw, nude, explicit"
)

# ── Video canvas ────────────────────────────────────────────────────────────
REEL_WIDTH: int = 1080
REEL_HEIGHT: int = 1920
REEL_FPS: int = 30
KEN_BURNS_ZOOM_START: float = 1.00
KEN_BURNS_ZOOM_END: float = 1.12
DUOTONE_SHADOW: tuple[int, int, int] = (20, 45, 55)       # cool teal-dark
DUOTONE_HIGHLIGHT: tuple[int, int, int] = (245, 175, 100)  # warm amber
GRAIN_INTENSITY: float = 0.035
VIGNETTE_STRENGTH: float = 0.42

# Text-handle watermark height as fraction of frame (was 0.028 → ~33% smaller).
WATERMARK_SIZE_FRAC: float = 0.018

# ── Per-channel watermark / brand ───────────────────────────────────────────
CHANNEL_ASSEMBLY: dict[str, dict[str, Any]] = {
    "momma_circle": {
        "logo_candidates": [
            "assets/logos/momma_circle_watermark.png",
            "channels_config/momma_circle/logo/logo.png",
        ],
        # Text watermark (Lora Italic) — preferred for LOFI cohesion vs PNG logo
        "watermark_handle": "@Momma Circle",
        "logo_position": "bottom_center",
        "logo_opacity": 0.55,
        "logo_scale": 0.04,  # was 0.06 (~33% smaller)
        "caption_color": (255, 255, 255),
        "use_text_watermark": True,
    },
    "wonder_feed": {
        "logo_candidates": [
            "assets/logos/wonder_feed_watermark.png",
            "channels_config/wonder_feed/logo/logo.png",
        ],
        "watermark_handle": "@Wonder Feed",
        "logo_position": "bottom_center",
        "logo_opacity": 0.55,
        "logo_scale": 0.04,  # was 0.06 (~33% smaller)
        "caption_color": (255, 255, 255),
        "use_text_watermark": True,
    },
}

_PACKAGE_DIR = Path(__file__).resolve().parent
DATA_DIR = _PACKAGE_DIR / "data"
STORE_DIR = _PACKAGE_DIR / "store"  # JSON-backed RAG store (Chroma optional)


def scene_count_for_duration(duration_s: int | float) -> int:
    """Scene count = round(duration / 4), clamped to sane min/max."""
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

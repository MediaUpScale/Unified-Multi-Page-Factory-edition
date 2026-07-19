# -*- coding: utf-8 -*-
"""
Central paths and credentials — Unified Multi-Page Factory edition.

Environment variables load from `.env` in the project root.

Supported keys
--------------
    GEMINI_API_KEY (or GOOGLE_API_KEY), ANTHROPIC_API_KEY,
    GEMINI_IMAGE_MODEL, GEMINI_IMAGE_ASPECT_RATIO,
    GEMINI_RESEARCH_MODEL, CLAUDE_MODEL,
    GEMINI_ECONOMIC_BRAIN_MODEL, ECONOMIC_BRAIN_MODE (true/false),
    DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL,
    REFERENCE_IMAGE_PATH, DIGITAL_PRODUCTS_PATH, OUTPUTS_DIR, PDF_CHUNK_CHAR_LIMIT,
    IMGBB_API_KEY, ANTHROPIC_API_VERSION,
    PUBLISHING_SCHEDULE (e.g. "3h" or "90m" spacing between variant posts)

Page-aware paths
----------------
Path variables that are per-page (PERSONA_DNA_PATH, MASTER_DNA_PATH,
REFERENCE_IMAGE_PATH, DIGITAL_PRODUCTS_PATH, PAGE_OUTPUTS_DIR, ASSETS_DIR,
LIBRARY_DIR, CONTENT_LIBRARY_PATH, POST_PLANNER_XLSX) resolve dynamically
based on the ACTIVE_PAGE environment variable, which is set by main.py before
any module-level import. Defaults to 'anna_protocol' for full backward
compatibility.
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

ENGINE_ROOT: Path = Path(__file__).resolve().parent
DOTENV_PATH: Path = ENGINE_ROOT / ".env"

# ---------------------------------------------------------------------------
# .env loading
# ---------------------------------------------------------------------------

def _load_project_dotenv() -> tuple[Path, bool]:
    resolved = DOTENV_PATH.expanduser().resolve()
    if resolved.is_file():
        return resolved, bool(load_dotenv(dotenv_path=resolved, override=True, encoding="utf-8-sig"))
    return resolved, False


_DOTENV_RESOLVED_PATH, DOTENV_LOADED_FROM_FILE = _load_project_dotenv()


def print_dotenv_bootstrap() -> None:
    if DOTENV_LOADED_FROM_FILE:
        print(f"[bootstrap] .env loaded: {_DOTENV_RESOLVED_PATH}")
    else:
        print(f"[bootstrap] .env not loaded from {_DOTENV_RESOLVED_PATH}")


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _bool_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _resolve_path(value: str | None, default: Path) -> Path:
    return Path((value or str(default))).expanduser()


def _parse_schedule_minutes(raw: str | None) -> int | None:
    """Parse '3h', '90m', '120' (bare minutes) → integer minutes; None if unset."""
    if not raw:
        return None
    raw = raw.strip().lower()
    m = re.fullmatch(r"(\d+)\s*h(?:ours?)?", raw)
    if m:
        return int(m.group(1)) * 60
    m = re.fullmatch(r"(\d+)\s*m(?:in(?:utes?)?)?", raw)
    if m:
        return int(m.group(1))
    if raw.isdigit():
        return int(raw)
    return None


# ---------------------------------------------------------------------------
# Safe fallback model IDs
# ---------------------------------------------------------------------------
SAFE_GEMINI_TEXT_MODEL: str = "models/gemini-2.5-flash"
SAFE_GEMINI_IMAGE_MODEL: str = "models/gemini-3-pro-image-preview"
SAFE_CLAUDE_MODEL: str = "claude-3-5-sonnet-latest"

# ---------------------------------------------------------------------------
# API keys & versioning
# ---------------------------------------------------------------------------
GEMINI_API_KEY: str | None = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
ANTHROPIC_API_KEY: str | None = os.getenv("ANTHROPIC_API_KEY")
ANTHROPIC_API_VERSION: str = (os.getenv("ANTHROPIC_API_VERSION") or "2023-06-01").strip()
IMGBB_API_KEY: str | None = os.getenv("IMGBB_API_KEY")

# ---------------------------------------------------------------------------
# DeepSeek — OpenAI-compatible endpoint for economic operations
# ---------------------------------------------------------------------------
DEEPSEEK_API_KEY: str | None = os.getenv("DEEPSEEK_API_KEY") or None
DEEPSEEK_BASE_URL: str = (os.getenv("DEEPSEEK_BASE_URL") or "https://api.deepseek.com/v1").strip()
DEEPSEEK_MODEL: str = (os.getenv("DEEPSEEK_MODEL") or "deepseek-chat").strip()

# ---------------------------------------------------------------------------
# ElevenLabs — voiceover TTS + ambient SFX for ECONOMIC_REEL
# ---------------------------------------------------------------------------
ELEVENLABS_API_KEY: str | None = os.getenv("ELEVENLABS_API_KEY") or None

# ---------------------------------------------------------------------------
# Model IDs
# ---------------------------------------------------------------------------
GEMINI_RESEARCH_MODEL: str = os.getenv("GEMINI_RESEARCH_MODEL", SAFE_GEMINI_TEXT_MODEL)
GEMINI_ECONOMIC_BRAIN_MODEL: str = os.getenv("GEMINI_ECONOMIC_BRAIN_MODEL", SAFE_GEMINI_TEXT_MODEL)
GEMINI_ECONOMIC_IMAGE_MODEL: str = os.getenv(
    "GEMINI_ECONOMIC_IMAGE_MODEL",
    "models/gemini-2.0-flash-preview-image-generation",   # cheapest working flash image model
)
# Nano/banana tier — Imagen 3 Fast (uses generate_images API, cheapest per image).
# Activated automatically when page_config.py declares COST_TIER = "nano".
GEMINI_NANO_IMAGE_MODEL: str = os.getenv(
    "GEMINI_NANO_IMAGE_MODEL",
    "models/imagen-3.0-fast-generate-001",
)
CLAUDE_MODEL: str = os.getenv("CLAUDE_MODEL", SAFE_CLAUDE_MODEL)
GEMINI_IMAGE_MODEL: str = os.getenv("GEMINI_IMAGE_MODEL", SAFE_GEMINI_IMAGE_MODEL)
GEMINI_IMAGE_ASPECT_RATIO: str = os.getenv("GEMINI_IMAGE_ASPECT_RATIO", "3:4")
ECONOMIC_BRAIN_MODE: bool = _bool_env("ECONOMIC_BRAIN_MODE", False)
GEMINI_IMAGE_MODEL_PREFERENCE: str = "models/gemini-3-pro-image-preview"

# ---------------------------------------------------------------------------
# Engagement-format defaults (CLI-overridable via --cta and --post-type)
# ---------------------------------------------------------------------------
CTA_ENABLED: bool = _bool_env("CTA_ENABLED", True)
POST_TYPE: str = os.getenv("POST_TYPE", "STANDARD_QUOTE").strip().upper()

# ---------------------------------------------------------------------------
# Active page — set by main.py via ACTIVE_PAGE env var before any import.
# Defaults to 'anna_protocol' for full backward compatibility.
# ---------------------------------------------------------------------------
ACTIVE_PAGE: str = os.getenv("ACTIVE_PAGE", "anna_protocol")
PAGES_CONFIG_ROOT: Path = ENGINE_ROOT / "pages_config"
ACTIVE_PAGE_DIR: Path = PAGES_CONFIG_ROOT / ACTIVE_PAGE

# ---------------------------------------------------------------------------
# Page-aware persona paths
# ---------------------------------------------------------------------------
PERSONA_DNA_PATH: Path = ACTIVE_PAGE_DIR / "persona_dna.py"
MASTER_DNA_PATH: Path = ACTIVE_PAGE_DIR / "master_dna.json"

# Fallback: legacy avatar_engine/master_dna.json for anna_protocol
# if pages_config hasn't been set up yet.
if not MASTER_DNA_PATH.is_file() and ACTIVE_PAGE == "anna_protocol":
    MASTER_DNA_PATH = ENGINE_ROOT / "avatar_engine" / "master_dna.json"
if not PERSONA_DNA_PATH.is_file() and ACTIVE_PAGE == "anna_protocol":
    PERSONA_DNA_PATH = ENGINE_ROOT / "avatar_engine" / "persona_dna.py"

# ---------------------------------------------------------------------------
# Page-aware asset paths
# ---------------------------------------------------------------------------

# Reference avatar: prefer pages_config/{page}/avatar_reference/avatar.png,
# then fall back to the legacy hardcoded Drive path for anna_protocol.
_page_ref_avatar: Path = ACTIVE_PAGE_DIR / "avatar_reference" / "avatar.png"
_REFERENCE_AVATAR_LEGACY = Path(
    r"G:\My Drive\Z sosFiles\Z_act\@ NETWORK\@_Content 2026\The Holistic Legacy - Anna's Protocol"
    r"\Anna's Automated Image Posts Engine\avatar_reference\avatar.png",
)
_ref_avatar_default: Path = (
    _page_ref_avatar if _page_ref_avatar.parent.is_dir() else _REFERENCE_AVATAR_LEGACY
)
REFERENCE_IMAGE_PATH: Path = _resolve_path(
    os.getenv("REFERENCE_IMAGE_PATH"), _ref_avatar_default
)

# Digital products (PDF corpus): prefer pages_config/{page}/product_reference/
_page_digital_products: Path = ACTIVE_PAGE_DIR / "product_reference"
_DEFAULT_DIGITAL_PRODUCTS = ENGINE_ROOT / "product_reference" / "Digital Products"
DIGITAL_PRODUCTS_PATH: Path = _resolve_path(
    os.getenv("DIGITAL_PRODUCTS_PATH"),
    _page_digital_products if _page_digital_products.is_dir() else _DEFAULT_DIGITAL_PRODUCTS,
)

PDF_CHUNK_CHAR_LIMIT: int = int(os.getenv("PDF_CHUNK_CHAR_LIMIT", "48000"))

# ---------------------------------------------------------------------------
# Output paths — page-namespaced under outputs/{page}/
# ---------------------------------------------------------------------------
_DEFAULT_OUTPUTS = ENGINE_ROOT / "outputs"
OUTPUTS_DIR: Path = _resolve_path(os.getenv("OUTPUTS_DIR"), _DEFAULT_OUTPUTS)

# Page-namespaced output root.  All per-page artifacts (images, library JSON,
# Excel planners) land here so pages never share or collide on outputs.
PAGE_OUTPUTS_DIR: Path = OUTPUTS_DIR / ACTIVE_PAGE
ASSETS_DIR: Path = PAGE_OUTPUTS_DIR / "assets"
LIBRARY_DIR: Path = PAGE_OUTPUTS_DIR / "library"
CONTENT_LIBRARY_PATH: Path = PAGE_OUTPUTS_DIR / "content_library.json"

_SAMPLE_BULK_V3: Path = ENGINE_ROOT / "sample_bulk_posts_import_3.xlsx"
_SAMPLE_BULK_LEGACY: Path = ENGINE_ROOT / "sample_bulk_posts_import.xlsx"
BULK_POSTS_TEMPLATE_XLSX: Path = _SAMPLE_BULK_V3 if _SAMPLE_BULK_V3.is_file() else _SAMPLE_BULK_LEGACY
POST_PLANNER_XLSX: Path = PAGE_OUTPUTS_DIR / "automated_bulk_posts_import.xlsx"

_LEGACY_PLANNER_XLSX: Path = ENGINE_ROOT / "automated_bulk_posts_import.xlsx"

# ---------------------------------------------------------------------------
# Publishing schedule (Instagram / PostPlanner)
# ---------------------------------------------------------------------------
PUBLISHING_SCHEDULE: str | None = os.getenv("PUBLISHING_SCHEDULE") or None
PUBLISHING_INTERVAL_MINUTES: int | None = _parse_schedule_minutes(PUBLISHING_SCHEDULE)

# ---------------------------------------------------------------------------
# Pinterest safe-drip interval
# ---------------------------------------------------------------------------
def _parse_interval_hours(name: str, default: float) -> float:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        return float(raw.strip())
    except ValueError:
        return default


MIN_INTERVAL_HOURS: float = _parse_interval_hours("MIN_INTERVAL_HOURS", 3.0)
MAX_INTERVAL_HOURS: float = _parse_interval_hours("MAX_INTERVAL_HOURS", 6.0)
PINTEREST_PINS_PER_DAY: int = int(os.getenv("PINTEREST_PINS_PER_DAY", "4"))

# ---------------------------------------------------------------------------
# Directory bootstrap (create if missing)
# ---------------------------------------------------------------------------
OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
PAGE_OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
ASSETS_DIR.mkdir(parents=True, exist_ok=True)
LIBRARY_DIR.mkdir(parents=True, exist_ok=True)

if _LEGACY_PLANNER_XLSX.is_file() and not POST_PLANNER_XLSX.exists():
    import shutil
    try:
        shutil.copy2(_LEGACY_PLANNER_XLSX, POST_PLANNER_XLSX)
    except OSError:
        logger.debug("Legacy planner copy skipped.", exc_info=True)


# ---------------------------------------------------------------------------
# Dynamic model discovery helpers (called by CaptionEngine / adapters)
# ---------------------------------------------------------------------------

def get_best_claude_model(anthropic_client: object | None = None) -> str:
    """
    Query the Anthropic Models API and return the best available conversational
    model. Falls back to SAFE_CLAUDE_MODEL if the API call fails.
    """
    if anthropic_client is None:
        return CLAUDE_MODEL or SAFE_CLAUDE_MODEL
    try:
        page = anthropic_client.models.list()  # type: ignore[union-attr]
        models = list(getattr(page, "data", None) or page)
        for priority in ("sonnet", "haiku"):
            for m in models:
                mid = str(getattr(m, "id", "") or "").lower()
                if priority in mid and "claude" in mid:
                    logger.debug("Dynamic Claude model resolved: %s", mid)
                    return mid
        for m in models:
            mid = str(getattr(m, "id", "") or "")
            if "claude" in mid.lower():
                return mid
    except Exception as exc:  # noqa: BLE001
        logger.debug("Claude model discovery failed (%s); using configured fallback.", exc)
    return CLAUDE_MODEL or SAFE_CLAUDE_MODEL


def get_best_gemini_text_model(client: object | None = None) -> str:  # type: ignore[type-arg]
    """
    Query Gemini models.list() and return the highest-scoring GA text model.
    Falls back to SAFE_GEMINI_TEXT_MODEL if discovery fails.
    """
    if client is None:
        return GEMINI_RESEARCH_MODEL or SAFE_GEMINI_TEXT_MODEL
    try:
        from avatar_engine.providers.gemini_utils import (  # avoid circular at module load
            _list_models,
            _parse_version_score,
            _strip_model_id,
            _supports_generate_content,
        )
        candidates = []
        for m in _list_models(client):
            mid = _strip_model_id(getattr(m, "name", None))
            if not mid:
                continue
            low = mid.lower()
            if not any(k in low for k in ("flash", "pro")):
                continue
            if "image" in low or "vision" in low or "embed" in low:
                continue
            if not _supports_generate_content(m):
                continue
            candidates.append((mid, _parse_version_score(mid)))
        if candidates:
            best = max(candidates, key=lambda x: x[1])[0]
            logger.debug("Dynamic Gemini text model resolved: %s", best)
            return best
    except Exception as exc:  # noqa: BLE001
        logger.debug("Gemini model discovery failed (%s); using fallback.", exc)
    return GEMINI_RESEARCH_MODEL or SAFE_GEMINI_TEXT_MODEL


# ---------------------------------------------------------------------------
# Avatar helpers
# ---------------------------------------------------------------------------

def reference_avatar_resolved_path() -> Path:
    return REFERENCE_IMAGE_PATH.resolve()


def reference_avatar_exists() -> bool:
    return REFERENCE_IMAGE_PATH.is_file()


def warn_if_reference_avatar_missing() -> None:
    if reference_avatar_exists():
        return
    logger.warning(
        "Reference likeness file not found at %s. Image generation falls back to text-only prompting.",
        reference_avatar_resolved_path(),
    )

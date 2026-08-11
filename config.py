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
    DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL (optional secondary fallback),
    TEXT_LLM_PRIMARY (default: gemini),
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
# Text: Gemini Flash. Image: Together AI FLUX.1-schnell (Gemini image deprecated).
# ---------------------------------------------------------------------------
SAFE_GEMINI_TEXT_MODEL: str = "models/gemini-2.5-flash"
SAFE_GEMINI_IMAGE_MODEL: str = "black-forest-labs/FLUX.1-schnell"  # Together FLUX (name legacy)
SAFE_GEMINI_IMAGE_FALLBACK_2: str = "black-forest-labs/FLUX.1-schnell"
SAFE_GEMINI_IMAGE_FALLBACK_3: str = "black-forest-labs/FLUX.1-schnell"
SAFE_CLAUDE_MODEL: str = "claude-3-5-sonnet-latest"

# ---------------------------------------------------------------------------
# Model Router — cost-first by default; premium ONLY when explicitly enabled
# ---------------------------------------------------------------------------
# USE_PREMIUM_MODEL=true  OR  MODEL_TIER=premium  → flagship Pro SKUs
# Otherwise always cheap: gemini-2.5-flash / gemini-*-flash-image
USE_PREMIUM_MODEL: bool = _bool_env("USE_PREMIUM_MODEL", False)
MODEL_TIER: str = (os.getenv("MODEL_TIER") or ("premium" if USE_PREMIUM_MODEL else "cheap")).strip().lower()
USE_OPENROUTER_AUTO: bool = _bool_env("USE_OPENROUTER_AUTO", False)

# Hard timeout for every image API call (prevents terminal hangs)
IMAGE_API_TIMEOUT_S: float = float(os.getenv("IMAGE_API_TIMEOUT_S") or "25")

# ---------------------------------------------------------------------------
# API keys & versioning
# ---------------------------------------------------------------------------
GEMINI_API_KEY: str | None = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
# Together AI — primary image backend (FLUX.1-schnell)
TOGETHER_API_KEY: str | None = os.getenv("TOGETHER_API_KEY") or None
TOGETHER_IMAGE_MODEL: str = (
    os.getenv("TOGETHER_IMAGE_MODEL") or "black-forest-labs/FLUX.1-schnell"
).strip()
TOGETHER_IMAGE_STEPS: int = int(os.getenv("TOGETHER_IMAGE_STEPS") or "4")
# Estimated USD — dynamic per model via together_image.estimate_together_image_cost()
# Schnell default; override model with TOGETHER_IMAGE_MODEL without changing this floor.
TOGETHER_IMAGE_COST_USD: float = float(os.getenv("TOGETHER_IMAGE_COST_USD") or "0.003")
# Global Together AI rate limiter (process-wide, thread-safe): forces strictly
# sequential image requests (max 1 in-flight at any time across ALL variant
# workers) with a small micro-delay between calls, to avoid 429 storms when
# multiple bulk-production workers run concurrently. Only gates image calls —
# LLM research, TTS, video compile, and uploads stay fully concurrent. This is
# the sync/threading equivalent of an ``asyncio.Semaphore(1)`` — the pipeline
# itself is fully synchronous (threading-based), so a real asyncio primitive
# would require converting main.py's ThreadPoolExecutor orchestration to an
# event loop; this threading.Semaphore delivers an identical hard guarantee
# (exactly 1 in-flight Together request, process-wide) without that rewrite.
TOGETHER_MIN_CALL_INTERVAL_S: float = float(os.getenv("TOGETHER_MIN_CALL_INTERVAL_S") or "0.2")
TOGETHER_IMAGE_MAX_RETRIES: int = int(os.getenv("TOGETHER_IMAGE_MAX_RETRIES") or "4")
# 429 back-off is short and precise (server-hinted Retry-After when present,
# else this fixed short ladder) — never the old long exponential chains.
TOGETHER_429_BACKOFF_MIN_S: float = float(os.getenv("TOGETHER_429_BACKOFF_MIN_S") or "1.0")
TOGETHER_429_BACKOFF_MAX_S: float = float(os.getenv("TOGETHER_429_BACKOFF_MAX_S") or "2.5")

# Native portrait for all Together/FLUX outputs (test + production)
DRAFT_IMAGE_SIZE: tuple[int, int] = (768, 1344)
PRODUCTION_IMAGE_SIZE: tuple[int, int] = (768, 1344)

# VisualQA_Agent critic calibration (also mirrored in VisualQA_Agent/config.py)
MAX_RETRIES: int = 5
QUALITY_THRESHOLD: float = 6.0

ANTHROPIC_API_KEY: str | None = os.getenv("ANTHROPIC_API_KEY")
ANTHROPIC_API_VERSION: str = (os.getenv("ANTHROPIC_API_VERSION") or "2023-06-01").strip()
IMGBB_API_KEY: str | None = os.getenv("IMGBB_API_KEY")
IMAGE_PROVIDER: str = (os.getenv("IMAGE_PROVIDER") or "together").strip().lower()

# ---------------------------------------------------------------------------
# Remote GPU (ComfyUI / RunPod) — opt-in adapter; legacy paths unchanged when false
# ---------------------------------------------------------------------------
# When ENABLE_REMOTE_GPU_WORKFLOWS=true, thin routers in get_image_adapter() and
# generate_voiceover() delegate to core_engine.remote_gpu_manager. When false
# (default), Together / ElevenLabs / MoviePy continue exactly as before.
ENABLE_REMOTE_GPU_WORKFLOWS: bool = _bool_env("ENABLE_REMOTE_GPU_WORKFLOWS", False)
# Default: serverless (always on-demand). Pod (comfyui) is the explicit exception.
REMOTE_GPU_MODE: str = (os.getenv("REMOTE_GPU_MODE") or "runpod").strip().lower()


def _strip_url_fragment(url: str) -> str:
    """Drop browser ``#workflow-uuid`` fragments from ComfyUI proxy URLs."""
    raw = (url or "").strip().split("#", 1)[0].strip().rstrip("/")
    return raw


REMOTE_GPU_BASE_URL: str = _strip_url_fragment(
    os.getenv("REMOTE_GPU_BASE_URL") or os.getenv("COMFYUI_BASE_URL") or ""
)
REMOTE_GPU_WORKFLOWS_DIR: str = (
    os.getenv("REMOTE_GPU_WORKFLOWS_DIR") or "remote_GPU_workflows"
).strip()
REMOTE_GPU_POLL_INTERVAL_S: float = float(os.getenv("REMOTE_GPU_POLL_INTERVAL_S") or "2.0")
REMOTE_GPU_TIMEOUT_S: float = float(os.getenv("REMOTE_GPU_TIMEOUT_S") or "600")
REMOTE_GPU_DEFAULT_REF_AUDIO: str = (os.getenv("REMOTE_GPU_DEFAULT_REF_AUDIO") or "").strip()
# Optional exact transcript for the global F5 reference clip (or use sibling .txt)
REMOTE_GPU_DEFAULT_REF_TEXT: str = (os.getenv("REMOTE_GPU_DEFAULT_REF_TEXT") or "").strip()
# Empty = auto-select per channel (LoRA graph when REMOTE_GPU_LORA_NAME set)
REMOTE_GPU_FLUX_WORKFLOW: str = (os.getenv("REMOTE_GPU_FLUX_WORKFLOW") or "").strip()
RUNPOD_API_KEY: str | None = os.getenv("RUNPOD_API_KEY") or None
RUNPOD_ENDPOINT_ID: str = (os.getenv("RUNPOD_ENDPOINT_ID") or "").strip()
RUNPOD_ENDPOINT_URL: str = _strip_url_fragment(os.getenv("RUNPOD_ENDPOINT_URL") or "")
# Max concurrent remote-GPU jobs (serverless workers / Comfy queue depth).
# Not hardcoded — set to match endpoint worker count (1, 5, 10, …).
REMOTE_GPU_MAX_PARALLEL: int = max(
    1, int(os.getenv("REMOTE_GPU_MAX_PARALLEL") or os.getenv("REMOTE_GPU_WORKERS") or "5")
)
# Pricing tier hints for CostTracker GPU line items
# Pod: community (~$0.34/hr RTX 4090) | secure (~$0.69/hr)
RUNPOD_CLOUD_TYPE: str = (os.getenv("RUNPOD_CLOUD_TYPE") or "community").strip().lower()
# Serverless: flex (full rate) | active (−40% always-on workers)
RUNPOD_SERVERLESS_WORKER_TYPE: str = (
    os.getenv("RUNPOD_SERVERLESS_WORKER_TYPE") or "flex"
).strip().lower()
# Optional overrides (USD); 0 / empty → built-in table in cost_tracker
RUNPOD_POD_RTX4090_USD_PER_HOUR: float = float(
    os.getenv("RUNPOD_POD_RTX4090_USD_PER_HOUR") or "0"
)
RUNPOD_SERVERLESS_RTX4090_USD_PER_SEC: float = float(
    os.getenv("RUNPOD_SERVERLESS_RTX4090_USD_PER_SEC") or "0"
)

# ---------------------------------------------------------------------------
# DeepSeek — OPTIONAL secondary fallback only (Gemini is the primary text brain)
# Models (2026): deepseek-v4-flash | deepseek-v4-pro
# Legacy ``deepseek-chat`` is rejected by the API (HTTP 400) — never use it.
# ---------------------------------------------------------------------------
DEEPSEEK_API_KEY: str | None = os.getenv("DEEPSEEK_API_KEY") or None
DEEPSEEK_BASE_URL: str = (os.getenv("DEEPSEEK_BASE_URL") or "https://api.deepseek.com/v1").strip()


def _normalize_deepseek_model(raw: str, *, default: str) -> str:
    name = (raw or default).strip() or default
    if name.lower() in {"deepseek-chat", "deepseek-coder", "deepseek-chat-v3"}:
        return default
    return name


DEEPSEEK_FLASH_MODEL: str = _normalize_deepseek_model(
    os.getenv("DEEPSEEK_FLASH_MODEL") or os.getenv("DEEPSEEK_MODEL") or "deepseek-v4-flash",
    default="deepseek-v4-flash",
)
DEEPSEEK_PRO_MODEL: str = _normalize_deepseek_model(
    os.getenv("DEEPSEEK_PRO_MODEL") or "deepseek-v4-pro",
    default="deepseek-v4-pro",
)
DEEPSEEK_MODEL: str = DEEPSEEK_FLASH_MODEL

# ---------------------------------------------------------------------------
# Text LLM routing — Gemini is ALWAYS the primary provider for all pages
# ---------------------------------------------------------------------------
# "gemini" (default) | "deepseek" (legacy override — not recommended)
TEXT_LLM_PRIMARY: str = (os.getenv("TEXT_LLM_PRIMARY") or "gemini").strip().lower()
# Minimum words accepted from sequence voiceover before Gemini retry/fallback
SEQUENCE_VOICEOVER_MIN_WORDS: int = int(os.getenv("SEQUENCE_VOICEOVER_MIN_WORDS") or "110")

# ---------------------------------------------------------------------------
# ElevenLabs — voiceover TTS + ambient SFX for ECONOMIC_REEL
# ---------------------------------------------------------------------------
ELEVENLABS_API_KEY: str | None = os.getenv("ELEVENLABS_API_KEY") or None


def elevenlabs_api_key_is_secret_format(key: str | None = None) -> bool:
    """
    True when *key* looks like an ElevenLabs **secret** API key (``sk_…``).

    ElevenLabs distinguishes Key ID vs secret: sending a Key ID yields HTTP 400
    ``api_key_id_used_as_api_key``. A 64-char hex blob without ``sk_`` is almost
    always the ID, not the secret.
    """
    raw = (key if key is not None else ELEVENLABS_API_KEY) or ""
    return str(raw).strip().startswith("sk_")


def assert_elevenlabs_api_key_usable(key: str | None = None) -> None:
    """Raise with a clear fix hint when the env value is a Key ID, not a secret."""
    raw = (key if key is not None else ELEVENLABS_API_KEY) or ""
    if not str(raw).strip():
        raise RuntimeError(
            "ELEVENLABS_API_KEY is empty. Add the secret key from the ElevenLabs "
            "dashboard (it must start with 'sk_')."
        )
    if not elevenlabs_api_key_is_secret_format(raw):
        raise RuntimeError(
            "ELEVENLABS_API_KEY is not a secret key (must start with 'sk_'). "
            "ElevenLabs error api_key_id_used_as_api_key means this value is a "
            "Key ID (or other non-secret), not the API secret. Open the ElevenLabs "
            "dashboard → API Keys → copy the secret shown at creation/rotation "
            "(sk_…), paste it into .env as ELEVENLABS_API_KEY, and re-run. "
            "model_api_flows does not remap this variable — the .env value itself "
            "is what gets sent as xi-api-key."
        )

# ---------------------------------------------------------------------------
# YouTube Data API v3 — OAuth2 upload
# ---------------------------------------------------------------------------
# Set ENABLE_YOUTUBE_UPLOAD=true in .env to automatically publish every
# compiled reel.  CLI flags --publish-youtube / --upload-youtube override this.
ENABLE_YOUTUBE_UPLOAD: bool = os.getenv("ENABLE_YOUTUBE_UPLOAD", "false").strip().lower() in (
    "true", "1", "yes",
)
YOUTUBE_PRIVACY_STATUS: str = os.getenv("YOUTUBE_PRIVACY_STATUS", "unlisted").strip().lower()
# Path to Google OAuth2 desktop client-secrets file (never commit this file).
YOUTUBE_CLIENT_SECRETS: str = os.getenv(
    "YOUTUBE_CLIENT_SECRETS",
    str(Path(__file__).resolve().parent / "client_secret.json"),
)
# Per-page OAuth refresh tokens: credentials/tokens/youtube_token_{page}.json
YOUTUBE_TOKEN_DIR: str = os.getenv(
    "YOUTUBE_TOKEN_DIR",
    str(Path(__file__).resolve().parent / "credentials" / "tokens"),
)
# Daily upload-quota (~20 videos/channel/day) pending queue — populated when
# YouTubeQuotaExceededError is caught; replayed via --resume-youtube-queue.
YOUTUBE_PENDING_QUEUE_PATH: str = os.getenv(
    "YOUTUBE_PENDING_QUEUE_PATH",
    str(Path(__file__).resolve().parent / "credentials" / "pending_youtube_uploads.json"),
)

# ---------------------------------------------------------------------------
# Model IDs — Gemini 2.5 Flash is the default primary text engine for ALL pages
# ---------------------------------------------------------------------------
GEMINI_RESEARCH_MODEL: str = os.getenv("GEMINI_RESEARCH_MODEL", SAFE_GEMINI_TEXT_MODEL)
GEMINI_ECONOMIC_BRAIN_MODEL: str = os.getenv(
    "GEMINI_ECONOMIC_BRAIN_MODEL",
    SAFE_GEMINI_TEXT_MODEL,  # models/gemini-2.5-flash
)
GEMINI_ECONOMIC_IMAGE_MODEL: str = os.getenv(
    "GEMINI_ECONOMIC_IMAGE_MODEL",
    SAFE_GEMINI_IMAGE_MODEL,  # models/gemini-3.1-flash-image
)
GEMINI_NANO_IMAGE_MODEL: str = os.getenv(
    "GEMINI_NANO_IMAGE_MODEL",
    SAFE_GEMINI_IMAGE_MODEL,
)
CLAUDE_MODEL: str = os.getenv("CLAUDE_MODEL", SAFE_CLAUDE_MODEL)
GEMINI_IMAGE_MODEL: str = os.getenv("GEMINI_IMAGE_MODEL", SAFE_GEMINI_IMAGE_MODEL)
GEMINI_IMAGE_ASPECT_RATIO: str = os.getenv("GEMINI_IMAGE_ASPECT_RATIO", "3:4")
ECONOMIC_BRAIN_MODE: bool = _bool_env("ECONOMIC_BRAIN_MODE", False)
GEMINI_IMAGE_MODEL_PREFERENCE: str = SAFE_GEMINI_IMAGE_MODEL

# Ordered live Gemini image chain — CHEAP tier only (no pro drift)
IMAGE_MODEL_FALLBACK_CHAIN: list[str] = [
    SAFE_GEMINI_IMAGE_MODEL,        # models/gemini-3.1-flash-image
    SAFE_GEMINI_IMAGE_FALLBACK_2,   # models/gemini-2.5-flash-image
]
# Premium image SKU — only appended when USE_PREMIUM_MODEL / MODEL_TIER=premium
PREMIUM_IMAGE_MODEL: str = SAFE_GEMINI_IMAGE_FALLBACK_3
PREMIUM_TEXT_MODEL: str = "models/gemini-2.5-pro"


def normalize_image_model_id(raw: str | None) -> str:
    """
    Normalize image model IDs.

    Together FLUX Schnell is the primary backend — pass those IDs through.
    Legacy Gemini image SKUs are remapped to FLUX for cost efficiency.
    """
    flux = "black-forest-labs/FLUX.1-schnell"
    name = (raw or flux).strip() or flux
    low = name.lower().removeprefix("models/")

    # Together / FLUX — keep as-is
    if "flux" in low or "black-forest-labs" in low:
        return name if "/" in name else f"black-forest-labs/{low}"

    # Legacy Gemini / Imagen image SKUs → FLUX Schnell
    if (
        low.startswith("imagen")
        or ("gemini" in low and "image" in low)
        or "flash-lite-image" in low
    ):
        logger.info("Image SKU '%s' remapped → Together %s", name, flux)
        return flux

    return name


# Sanitize env overrides — legacy Gemini image IDs remap to Together FLUX
GEMINI_ECONOMIC_IMAGE_MODEL = normalize_image_model_id(GEMINI_ECONOMIC_IMAGE_MODEL)
GEMINI_NANO_IMAGE_MODEL = normalize_image_model_id(GEMINI_NANO_IMAGE_MODEL)
GEMINI_IMAGE_MODEL = normalize_image_model_id(GEMINI_IMAGE_MODEL)
TOGETHER_IMAGE_MODEL = normalize_image_model_id(TOGETHER_IMAGE_MODEL)

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
CHANNELS_CONFIG_ROOT: Path = ENGINE_ROOT / "channels_config"
_LEGACY_PAGES_CONFIG_ROOT: Path = ENGINE_ROOT / "pages_config"
# Prefer channels_config/; fall back to historic pages_config/ if still present.
PAGES_CONFIG_ROOT: Path = (
    CHANNELS_CONFIG_ROOT
    if CHANNELS_CONFIG_ROOT.is_dir()
    else _LEGACY_PAGES_CONFIG_ROOT
)
ACTIVE_PAGE_DIR: Path = (
    (CHANNELS_CONFIG_ROOT / ACTIVE_PAGE)
    if (CHANNELS_CONFIG_ROOT / ACTIVE_PAGE).is_dir()
    else (_LEGACY_PAGES_CONFIG_ROOT / ACTIVE_PAGE)
)

# ---------------------------------------------------------------------------
# Page-aware persona paths
# ---------------------------------------------------------------------------
PERSONA_DNA_PATH: Path = ACTIVE_PAGE_DIR / "persona_dna.py"
MASTER_DNA_PATH: Path = ACTIVE_PAGE_DIR / "master_dna.json"

# Fallback: legacy avatar_engine/master_dna.json for anna_protocol
# if channels_config hasn't been set up yet.
if not MASTER_DNA_PATH.is_file() and ACTIVE_PAGE == "anna_protocol":
    MASTER_DNA_PATH = ENGINE_ROOT / "avatar_engine" / "master_dna.json"
if not PERSONA_DNA_PATH.is_file() and ACTIVE_PAGE == "anna_protocol":
    PERSONA_DNA_PATH = ENGINE_ROOT / "avatar_engine" / "persona_dna.py"

# ---------------------------------------------------------------------------
# Page-aware asset paths
# ---------------------------------------------------------------------------

# Reference avatar: prefer channels_config/{page}/avatar_reference/avatar.png,
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

# Digital products (PDF corpus): prefer channels_config/{page}/product_reference/
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
# Cost-first router bootstrap (cheap unless USE_PREMIUM_MODEL / MODEL_TIER)
# ---------------------------------------------------------------------------
try:
    from avatar_engine.providers.model_router import (
        resolve_tier as _resolve_model_tier,
        route_model,
        sync_config_defaults as _sync_model_router_defaults,
    )

    _sync_model_router_defaults()
    # If premium explicitly enabled, expand image fallback chain to include Pro
    if _resolve_model_tier() == "premium":
        IMAGE_MODEL_FALLBACK_CHAIN = [
            PREMIUM_IMAGE_MODEL,
            SAFE_GEMINI_IMAGE_MODEL,
            SAFE_GEMINI_IMAGE_FALLBACK_2,
        ]
        if not os.getenv("GEMINI_IMAGE_MODEL"):
            GEMINI_IMAGE_MODEL = normalize_image_model_id(PREMIUM_IMAGE_MODEL)
        if not os.getenv("GEMINI_ECONOMIC_BRAIN_MODEL"):
            GEMINI_ECONOMIC_BRAIN_MODEL = PREMIUM_TEXT_MODEL
        if not os.getenv("GEMINI_RESEARCH_MODEL"):
            GEMINI_RESEARCH_MODEL = PREMIUM_TEXT_MODEL
except Exception as _router_exc:  # noqa: BLE001
    logger.debug("Model router bootstrap skipped (%s)", _router_exc)


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

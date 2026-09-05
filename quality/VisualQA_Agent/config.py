# -*- coding: utf-8 -*-
"""
VisualQA_Agent configuration — API keys, global outputs paths, guardrails.

Judge outputs live under:
``{OUTPUT_PATH}/{channel}/VisualQA_Agent_Judge/{attempts,approved,logs}/``

Style references remain under ``channels_config/{channel}/style_reference/``.
"""
from __future__ import annotations

import os
from pathlib import Path

from utils.pipeline_paths import outputs_root, page_outputs_dir, pipeline_logs_dir

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
AGENT_ROOT: Path = Path(__file__).resolve().parent
PROJECT_ROOT: Path = AGENT_ROOT.parents[1]  # quality/VisualQA_Agent → factory root
FACTORY_ROOT: Path = PROJECT_ROOT  # alias
CHANNELS_CONFIG_ROOT: Path = PROJECT_ROOT / "channels_config"
_LEGACY_PAGES_CONFIG_ROOT: Path = PROJECT_ROOT / "pages_config"
# Prefer channels_config/; fall back to historic pages_config/ if present.
PAGES_CONFIG_ROOT: Path = (
    CHANNELS_CONFIG_ROOT
    if CHANNELS_CONFIG_ROOT.is_dir()
    else _LEGACY_PAGES_CONFIG_ROOT
)
OUTPUTS_ROOT: Path = outputs_root()
JUDGE_FOLDER_NAME: str = "VisualQA_Agent_Judge"

# Load parent factory .env if present (does not override already-set env vars).
_DOTENV = PROJECT_ROOT / ".env"
if _DOTENV.is_file():
    try:
        from dotenv import load_dotenv

        load_dotenv(_DOTENV, override=False)
    except ImportError:
        pass

# Prefer parent config values when the factory package is importable.
try:
    import config as _factory_cfg  # type: ignore

    _FACTORY_GEMINI = getattr(_factory_cfg, "GEMINI_API_KEY", None)
    _FACTORY_TOGETHER = getattr(_factory_cfg, "TOGETHER_API_KEY", None)
    _FACTORY_DEEPINFRA = getattr(_factory_cfg, "DEEPINFRA_API_KEY", None)
    _FACTORY_FLUX = getattr(_factory_cfg, "TOGETHER_IMAGE_MODEL", None)
    _FACTORY_REMOTE_GPU = bool(
        getattr(_factory_cfg, "ENABLE_REMOTE_GPU_WORKFLOWS", False)
    )
except Exception:  # noqa: BLE001
    _FACTORY_GEMINI = None
    _FACTORY_TOGETHER = None
    _FACTORY_DEEPINFRA = None
    _FACTORY_FLUX = None
    _FACTORY_REMOTE_GPU = False

# ---------------------------------------------------------------------------
# API keys (google-genai + Together AI / FLUX)
# ---------------------------------------------------------------------------
GEMINI_API_KEY: str | None = (
    os.getenv("GEMINI_API_KEY")
    or os.getenv("GOOGLE_API_KEY")
    or _FACTORY_GEMINI
)
TOGETHER_API_KEY: str | None = (
    os.getenv("TOGETHER_API_KEY") or _FACTORY_TOGETHER
)
DEEPINFRA_API_KEY: str | None = (
    os.getenv("DEEPINFRA_API_KEY") or _FACTORY_DEEPINFRA
)
DEEPINFRA_OPENAI_BASE_URL: str = (
    os.getenv("DEEPINFRA_OPENAI_BASE_URL") or "https://api.deepinfra.com/v1/openai"
).strip()
DEEPINFRA_FLUX_SCHNELL_MODEL: str = (
    os.getenv("DEEPINFRA_FLUX_SCHNELL_MODEL") or "black-forest-labs/FLUX-1-schnell"
).strip()

GEMINI_CRITIC_MODEL: str = os.getenv(
    "VISUALQA_GEMINI_MODEL", "models/gemini-2.5-flash-lite"
)
GEMINI_REWRITE_MODEL: str = os.getenv(
    "VISUALQA_REWRITE_MODEL", "models/gemini-2.5-flash-lite"
)
FLUX_MODEL: str = (
    os.getenv("TOGETHER_IMAGE_MODEL")
    or _FACTORY_FLUX
    or "black-forest-labs/FLUX.1-schnell"
)
FLUX_FALLBACK_MODEL: str = "black-forest-labs/FLUX.1-dev"

# Mirror factory remote-GPU flag (env wins when explicitly set)
_REMOTE_GPU_ENV = (os.getenv("ENABLE_REMOTE_GPU_WORKFLOWS") or "").strip().lower()
if _REMOTE_GPU_ENV:
    ENABLE_REMOTE_GPU_WORKFLOWS: bool = _REMOTE_GPU_ENV in ("1", "true", "yes", "on")
else:
    ENABLE_REMOTE_GPU_WORKFLOWS = bool(_FACTORY_REMOTE_GPU)

# ---------------------------------------------------------------------------
# Concise positive style anchors (no negative-word stuffing for FLUX Schnell)
# ---------------------------------------------------------------------------
MASTER_STYLE_ANCHOR: str = (
    "biomechanical cyberpunk wuxia, cinematic 8k photorealistic raw photography, "
    "detailed skin textures, octane render, volumetric lighting, neon rain, "
    "glowing fiber-optic neural wires, 35mm film grain, dark cinematic photorealism"
)

MANDATORY_NEGATIVE_PROMPT: str = (
    "Gossip Goblin, tactical gear, gore, body horror, H.R. Giger, fitness model, "
    "gym bro, teens in bedrooms, smartphone desk, lifestyle soft-focus, "
    "pastel wellness, midjourney, --ar, --no"
)

POSITIVE_TEXTURE_ANCHOR: str = MASTER_STYLE_ANCHOR
ANTI_STUDIO_TRIGGERS: str = MASTER_STYLE_ANCHOR

MASTER_MEI_VISUAL_ANCHOR: str = (
    "Master Mei, older East Asian sage, long white hair bun, long white beard, "
    "dark/gold robes, seated in meditation in an ancestral samurai temple / "
    "misty mountain sacred ground — hybrid ancestral mysticism × cyberpunk dystopia"
)

# Native portrait for all outputs (test + production)
DRAFT_IMAGE_SIZE: tuple[int, int] = (768, 1344)
PRODUCTION_IMAGE_SIZE: tuple[int, int] = (768, 1344)

DEFAULT_STYLE_REFERENCE_FOLDER: Path = (
    PAGES_CONFIG_ROOT / "master_mei" / "style_reference"
).resolve()

MAX_PROMPT_WORDS: int = 180  # FLUX.1-schnell natural-language budget (120–180 words)

# ---------------------------------------------------------------------------
# Cost & safety guardrails
# ---------------------------------------------------------------------------
MAX_RETRIES: int = 5
QUALITY_THRESHOLD: float = 6.0  # /10 — realistic FLUX Schnell pass-bar
# Hard cap: clean fitness / studio commercial looks can never score above this.
CLEAN_MODEL_HARD_CAP: float = 4.0
LOG_COSTS: bool = True

# ---------------------------------------------------------------------------
# Cost-control & feature-flag guardrails
# ---------------------------------------------------------------------------
# Master kill-switch. When off, evaluate_image() short-circuits to a mock PASS
# (no Gemini vision call) so downstream pipelines (orchestrator, reel_visual_qa,
# agent_loop) keep running without burning vision tokens. Enable via .env:
#     VISUAL_QA_ENABLED=true
VISUAL_QA_ENABLED: bool = (
    (os.getenv("VISUAL_QA_ENABLED") or "").strip().lower()
    in ("1", "true", "yes", "on")
)

# Vision payload downscale cap (max dimension, aspect preserved) + JPEG quality.
# Input tokens scale ~with pixel area, so capping to 512px cuts per-request cost
# dramatically (~70–80%). Send JPEG (q85) instead of uncompressed high-res PNG.
VISION_MAX_DIM: int = int(os.getenv("VISUAL_QA_MAX_DIM", "512") or "512")
VISION_JPEG_QUALITY: int = int(os.getenv("VISUAL_QA_JPEG_QUALITY", "85") or "85")

COST_GEMINI_FLASH_USD: float = 0.00015
# Cheapest viable Vision model cost (per 1k tokens) for cost-logging only —
# gemini-2.5-flash-lite. Kept separate from the old flash rate so the ledger
# reflects the new, cheaper provider tier.
COST_GEMINI_FLASH_LITE_USD: float = float(
    os.getenv("VISUAL_QA_FLASH_LITE_USD", "0.000032") or "0.000032"
)
# DEPRECATED: flat pre-migration Together.ai rate. Schnell now runs on DeepInfra
# (formula-based billing: $0.0005 x (w/1024) x (h/1024) x steps — see
# agents.media.providers.together_image.estimate_deepinfra_schnell_cost_usd,
# the single source of truth). Still read by estimate_flux_cost() below /
# image_generator.py:359 — left in place rather than removed since that call
# site wasn't part of this pass. See _agent_log/20260820_cost-audit.md.
COST_FLUX_SCHNELL_USD: float = 0.003
COST_FLUX_DEV_USD: float = 0.025

# ---------------------------------------------------------------------------
# Agent-local persistence (Chroma / DNA store — not image outputs)
# ---------------------------------------------------------------------------
CHROMA_PERSIST_DIR: Path = AGENT_ROOT / "chroma_db"
IMAGE_EXTENSIONS: tuple[str, ...] = (".png", ".jpg", ".jpeg", ".webp")


def channel_config_root(channel_name: str) -> Path:
    """``channels_config/{channel}/`` (config + style refs only)."""
    primary = CHANNELS_CONFIG_ROOT / channel_name
    if primary.is_dir():
        return primary.resolve()
    legacy = _LEGACY_PAGES_CONFIG_ROOT / channel_name
    if legacy.is_dir():
        return legacy.resolve()
    return primary.resolve()


def channel_output_root(channel_name: str) -> Path:
    """``{OUTPUT_PATH}/{channel}/`` (channel media root)."""
    return page_outputs_dir(channel_name)


def judge_root(channel_name: str) -> Path:
    """``{OUTPUT_PATH}/{channel}/VisualQA_Agent_Judge/``."""
    return (channel_output_root(channel_name) / JUDGE_FOLDER_NAME).resolve()


def style_folder_for_channel(channel_name: str) -> Path:
    """``channels_config/{channel}/style_reference`` (fallback: master_mei)."""
    candidate = channel_config_root(channel_name) / "style_reference"
    if candidate.is_dir():
        return candidate
    return DEFAULT_STYLE_REFERENCE_FOLDER


def attempts_dir(channel_name: str) -> Path:
    """``outputs/{channel}/VisualQA_Agent_Judge/attempts/``."""
    return judge_root(channel_name) / "attempts"


def approved_dir(channel_name: str) -> Path:
    """``outputs/{channel}/VisualQA_Agent_Judge/approved/``."""
    return judge_root(channel_name) / "approved"


def logs_dir(channel_name: str) -> Path:
    """``outputs/{channel}/VisualQA_Agent_Judge/logs/``."""
    return judge_root(channel_name) / "logs"


def failed_shots_path(channel_name: str) -> Path:
    """``outputs/{channel}/VisualQA_Agent_Judge/logs/failed_shots.json``."""
    return logs_dir(channel_name) / "failed_shots.json"


def ensure_channel_dirs(channel_name: str) -> dict[str, Path]:
    """
    Create Judge attempt / approved / logs directories if missing.

    Returns keys: root, judge, attempts, approved, logs, style_reference.
    """
    paths = {
        "root": channel_output_root(channel_name),
        "judge": judge_root(channel_name),
        "attempts": attempts_dir(channel_name),
        "approved": approved_dir(channel_name),
        "logs": logs_dir(channel_name),
        "style_reference": style_folder_for_channel(channel_name),
    }
    for key in ("attempts", "approved", "logs"):
        paths[key].mkdir(parents=True, exist_ok=True)
    CHROMA_PERSIST_DIR.mkdir(parents=True, exist_ok=True)
    return paths


def ensure_runtime_dirs(channel_name: str | None = None) -> None:
    """Create agent chroma dir; optionally ensure a channel's output dirs."""
    CHROMA_PERSIST_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUTS_ROOT.mkdir(parents=True, exist_ok=True)
    if channel_name:
        ensure_channel_dirs(channel_name)


def estimate_flux_cost(model_id: str) -> float:
    mid = (model_id or "").lower()
    if "dev" in mid:
        return COST_FLUX_DEV_USD
    return COST_FLUX_SCHNELL_USD


# Legacy aliases
channel_root = channel_config_root
OUTPUTS_DIR: Path = OUTPUTS_ROOT
LOGS_DIR: Path = pipeline_logs_dir() / "VisualQA_Agent"
LOGS_DIR.mkdir(parents=True, exist_ok=True)
FAILED_SHOTS_PATH: Path = LOGS_DIR / "failed_shots.json"

# -*- coding: utf-8 -*-
"""
Cost-first Model Router — single source of truth for Gemini (and optional) SKUs.

Default behaviour
-----------------
Always select the cheapest suitable model for the task (``cheap`` tier).
Premium / flagship models are used ONLY when explicitly requested via:

* Environment: ``USE_PREMIUM_MODEL=true`` or ``MODEL_TIER=premium``
* Call-site: ``tier="premium"`` / ``use_premium=True``
* Explicit ``model_override`` / page ``IMAGE_MODEL_OVERRIDE`` (honoured as-is)

Error handling / retry chains stay inside the active tier — cheap runs never
silently escalate to Pro / flagship SKUs.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Literal

logger = logging.getLogger(__name__)

TaskType = Literal[
    "text",
    "research",
    "caption",
    "voiceover_script",
    "image",
    "image_sequence",
]
TierName = Literal["cheap", "premium"]

# ---------------------------------------------------------------------------
# Canonical SKUs (cost-first defaults)
# ---------------------------------------------------------------------------
CHEAP_TEXT_PRIMARY: str = "models/gemini-2.5-flash"
CHEAP_TEXT_CHAIN: list[str] = [
    "models/gemini-2.5-flash",
    "models/gemini-2.0-flash",
    "models/gemini-flash-latest",
    "models/gemini-1.5-flash-latest",
]

PREMIUM_TEXT_PRIMARY: str = "models/gemini-2.5-pro"
PREMIUM_TEXT_CHAIN: list[str] = [
    "models/gemini-2.5-pro",
    "models/gemini-2.5-flash",  # safe demotion within premium request only
    "models/gemini-1.5-pro-latest",
]

CHEAP_IMAGE_PRIMARY: str = "black-forest-labs/FLUX.1-schnell"
CHEAP_IMAGE_CHAIN: list[str] = [
    "black-forest-labs/FLUX.1-schnell",
]

# Premium *image* within Together = higher-tier FLUX (still Together, not Gemini)
PREMIUM_IMAGE_PRIMARY: str = "black-forest-labs/FLUX.1-dev"
PREMIUM_IMAGE_CHAIN: list[str] = [
    "black-forest-labs/FLUX.1-dev",
    "black-forest-labs/FLUX.1-schnell",  # safe demotion
]

# Together image cost estimates (USD / image) — mirrored in together_image.py
TOGETHER_IMAGE_COST_USD: dict[str, float] = {
    "black-forest-labs/FLUX.1-schnell": 0.003,
    "black-forest-labs/FLUX.1-dev": 0.025,
    "black-forest-labs/FLUX.1-pro": 0.050,
    "stabilityai/stable-diffusion-xl-base-1.0": 0.008,
}

# Optional low-cost OpenRouter route label (logged; used when OPENROUTER enabled)
OPENROUTER_AUTO: str = "openrouter/auto"

_PREMIUM_SLUG_MARKERS: tuple[str, ...] = (
    "pro-image",
    "gemini-2.5-pro",
    "gemini-3-pro",
    "gemini-1.5-pro",
    "gemini-pro",
)


@dataclass(frozen=True)
class ModelRoute:
    """Resolved model selection for one task."""

    task: TaskType
    tier: TierName
    model_id: str
    chain: tuple[str, ...]
    explicit_override: bool = False

    def as_list(self) -> list[str]:
        return list(self.chain)


def _bool_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _strip(name: str | None) -> str:
    if not name:
        return ""
    raw = str(name).strip().removeprefix("models/")
    low = raw.lower()
    # Keep org/model paths intact (Together, OpenRouter)
    if low.startswith("openrouter/") or low.startswith("black-forest-labs/") or low.startswith("stabilityai/"):
        return raw.strip()
    if "/" in raw:
        raw = raw.rsplit("/", maxsplit=1)[-1]
    return raw.strip()


def _full(slug_or_full: str) -> str:
    s = (slug_or_full or "").strip()
    if not s:
        return s
    low = s.lower()
    if (
        low.startswith("openrouter/")
        or low.startswith("models/")
        or low.startswith("black-forest-labs/")
        or low.startswith("stabilityai/")
    ):
        return s
    if s.lower().startswith("gemini") or s.lower().startswith("imagen"):
        return f"models/{_strip(s)}"
    return s


def _is_premium_sku(model_id: str) -> bool:
    low = (model_id or "").lower()
    if not low:
        return False
    # Together higher-tier image models count as premium for chain filtering
    if "flux.1-dev" in low or "flux-dev" in low or "flux.1-pro" in low or "flux-pro" in low:
        return True
    if "schnell" in low:
        return False
    if "black-forest-labs" in low and "schnell" in low:
        return False
    if "flash" in low and "pro" not in low:
        return False
    slug = _strip(model_id).lower()
    return any(m in slug for m in _PREMIUM_SLUG_MARKERS)


def estimate_image_route_cost(model_id: str) -> float:
    """USD estimate for a Together (or legacy) image model id."""
    try:
        from avatar_engine.providers.together_image import estimate_together_image_cost

        return float(estimate_together_image_cost(model_id))
    except Exception:  # noqa: BLE001
        mid = (model_id or "").lower()
        for key, price in TOGETHER_IMAGE_COST_USD.items():
            if key.lower() in mid or mid in key.lower():
                return float(price)
        if "schnell" in mid:
            return 0.003
        if "dev" in mid:
            return 0.025
        if "sdxl" in mid:
            return 0.008
        return 0.003


def resolve_tier(
    *,
    tier: str | None = None,
    use_premium: bool | None = None,
    page_cost_tier: str | None = None,
) -> TierName:
    """
    Resolve cheap vs premium.

    Priority
    --------
    1. Explicit ``use_premium=True`` / ``tier="premium"``
    2. Env ``USE_PREMIUM_MODEL`` / ``MODEL_TIER``
    3. Page ``COST_TIER=premium`` only (nano/economic → cheap)
    4. Default → cheap
    """
    if use_premium is True:
        return "premium"
    if use_premium is False:
        return "cheap"

    if tier is not None:
        t = str(tier).strip().lower()
        if t in ("premium", "pro", "flagship", "high"):
            return "premium"
        if t in ("cheap", "economy", "economic", "nano", "low", "cost"):
            return "cheap"

    if _bool_env("USE_PREMIUM_MODEL", False):
        return "premium"
    env_tier = (os.getenv("MODEL_TIER") or "").strip().lower()
    if env_tier in ("premium", "pro", "flagship", "high"):
        return "premium"
    if env_tier in ("cheap", "economy", "economic", "nano", "low", "cost"):
        return "cheap"

    page = (page_cost_tier or "").strip().lower()
    if page in ("premium", "pro"):
        return "premium"

    return "cheap"


def _dedupe(models: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for m in models:
        full = _full(m)
        if not full:
            continue
        key = full.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(full)
    return out


def _filter_chain_to_tier(chain: list[str], tier: TierName) -> list[str]:
    """Drop premium SKUs from cheap chains (no fallback drift)."""
    if tier == "premium":
        return _dedupe(chain)
    return _dedupe([m for m in chain if not _is_premium_sku(m)])


def _default_chains(task: TaskType, tier: TierName) -> tuple[str, list[str]]:
    is_image = task in ("image", "image_sequence")
    if is_image:
        # Honour TOGETHER_IMAGE_MODEL when set (e.g. FLUX.1-dev), else Schnell
        try:
            from avatar_engine.providers.together_image import (
                FLUX_SCHNELL_MODEL,
                default_together_image_model,
            )

            env_model = default_together_image_model()
        except Exception:  # noqa: BLE001
            env_model = CHEAP_IMAGE_PRIMARY
            FLUX_SCHNELL_MODEL = CHEAP_IMAGE_PRIMARY  # noqa: N806

        if tier == "premium":
            primary = env_model if _is_premium_sku(env_model) else PREMIUM_IMAGE_PRIMARY
            chain = [primary, *PREMIUM_IMAGE_CHAIN, FLUX_SCHNELL_MODEL]
            return primary, _dedupe(chain)
        # Cheap: prefer env model only if it is not a premium Together SKU
        if env_model and not _is_premium_sku(env_model):
            primary = env_model
        else:
            primary = CHEAP_IMAGE_PRIMARY
        return primary, _dedupe([primary, *CHEAP_IMAGE_CHAIN, FLUX_SCHNELL_MODEL])
    if tier == "premium":
        return PREMIUM_TEXT_PRIMARY, list(PREMIUM_TEXT_CHAIN)
    # Optional OpenRouter auto route when explicitly enabled for cheap text
    if _bool_env("USE_OPENROUTER_AUTO", False) and task in (
        "text", "research", "caption", "voiceover_script",
    ):
        return OPENROUTER_AUTO, [OPENROUTER_AUTO, *CHEAP_TEXT_CHAIN]
    return CHEAP_TEXT_PRIMARY, list(CHEAP_TEXT_CHAIN)


def log_model_route(route: ModelRoute) -> None:
    """
    Runtime transparency.

    Image routes are quiet by default (batch loops). Set MODEL_ROUTER_VERBOSE=true
    to print every image selection. Text routes still print once.
    """
    verbose = _bool_env("MODEL_ROUTER_VERBOSE", False)
    is_image = route.task in ("image", "image_sequence")
    extra = ""
    if is_image:
        est = estimate_image_route_cost(route.model_id)
        extra = f" | Est. Cost: ${est:.3f}/img"
    msg = (
        f"[Model Router] Task: {route.task} | Tier: {route.tier} | "
        f"Active Model: {route.model_id}{extra}"
    )
    if (not is_image) or verbose:
        print(msg, flush=True)
    logger.info(
        "Model Router | task=%s tier=%s model=%s override=%s chain=%s",
        route.task,
        route.tier,
        route.model_id,
        route.explicit_override,
        list(route.chain),
    )


def format_video_cost_summary(
    *,
    image_count: int,
    image_model_label: str = "FLUX Schnell",
    image_cost_usd: float,
    pipeline_cost_usd: float,
    usd_to_brl: float | None = None,
) -> str:
    """Single compact cost line for end-of-video logging."""
    rate = usd_to_brl
    if rate is None:
        try:
            rate = float(os.getenv("USD_TO_BRL") or "5.15")
        except ValueError:
            rate = 5.15
    brl = float(pipeline_cost_usd) * float(rate)
    return (
        f"[Cost Summary] Video generated | Total Images: {int(image_count)} "
        f"({image_model_label}) | Total Image Cost: ${float(image_cost_usd):.3f} USD | "
        f"Estimated Total Pipeline Cost: ${float(pipeline_cost_usd):.3f} USD "
        f"(~R$ {brl:.2f} BRL)"
    )


def route_model(
    task: TaskType,
    *,
    tier: str | None = None,
    use_premium: bool | None = None,
    page_cost_tier: str | None = None,
    model_override: str | None = None,
    preferred: str | None = None,
    log: bool = True,
) -> ModelRoute:
    """
    Resolve primary model + same-tier fallback chain for *task*.

    Parameters
    ----------
    model_override:
        Explicit SKU (env / page / request). When set, becomes primary; the
        remainder of the chain stays within the resolved tier (unless the
        override itself is premium — then tier flips to premium).
    preferred:
        Soft preference inserted at the front of the tier chain (still filtered).
    """
    resolved = resolve_tier(
        tier=tier,
        use_premium=use_premium,
        page_cost_tier=page_cost_tier,
    )

    override = (model_override or "").strip() or None
    explicit = False
    if override:
        explicit = True
        if _is_premium_sku(override):
            resolved = "premium"
        primary = _full(override)
        _, base_chain = _default_chains(task, resolved)
        chain = _filter_chain_to_tier([primary, *base_chain], resolved)
        # If override was cheap but somehow filtered out, keep it
        if primary not in chain:
            chain = [primary, *chain]
    else:
        primary, base_chain = _default_chains(task, resolved)
        pref = (preferred or "").strip() or None
        ordered = [pref, primary, *base_chain] if pref else [primary, *base_chain]
        chain = _filter_chain_to_tier([m for m in ordered if m], resolved)
        if not chain:
            chain = [_full(primary)]
        primary = chain[0]

    route = ModelRoute(
        task=task,
        tier=resolved,
        model_id=primary,
        chain=tuple(chain),
        explicit_override=explicit,
    )
    if log:
        log_model_route(route)
    return route


def text_model(**kwargs) -> ModelRoute:
    """Shorthand for caption / research / voiceover text tasks."""
    task: TaskType = kwargs.pop("task", "text")  # type: ignore[assignment]
    if task not in ("text", "research", "caption", "voiceover_script"):
        task = "text"
    return route_model(task, **kwargs)


def image_model(**kwargs) -> ModelRoute:
    """Shorthand for image generation tasks."""
    task: TaskType = kwargs.pop("task", "image")  # type: ignore[assignment]
    if task not in ("image", "image_sequence"):
        task = "image"
    return route_model(task, **kwargs)


def sync_config_defaults() -> None:
    """
    Push cheap-tier defaults into ``config`` module attributes when premium
    is not enabled — keeps legacy callers cost-safe without rewriting all sites.
    """
    try:
        import config as app_config
    except Exception:  # noqa: BLE001
        return

    tier = resolve_tier(page_cost_tier=getattr(app_config, "ACTIVE_PAGE_COST_TIER", None))
    if tier == "premium":
        return

    # Text
    app_config.SAFE_GEMINI_TEXT_MODEL = CHEAP_TEXT_PRIMARY
    if not os.getenv("GEMINI_RESEARCH_MODEL"):
        app_config.GEMINI_RESEARCH_MODEL = CHEAP_TEXT_PRIMARY
    if not os.getenv("GEMINI_ECONOMIC_BRAIN_MODEL"):
        app_config.GEMINI_ECONOMIC_BRAIN_MODEL = CHEAP_TEXT_PRIMARY

    # Image — never inject pro into default chain on cheap tier
    app_config.SAFE_GEMINI_IMAGE_MODEL = CHEAP_IMAGE_PRIMARY
    app_config.IMAGE_MODEL_FALLBACK_CHAIN = list(CHEAP_IMAGE_CHAIN)
    if not os.getenv("GEMINI_IMAGE_MODEL"):
        app_config.GEMINI_IMAGE_MODEL = CHEAP_IMAGE_PRIMARY
    if not os.getenv("GEMINI_ECONOMIC_IMAGE_MODEL"):
        app_config.GEMINI_ECONOMIC_IMAGE_MODEL = CHEAP_IMAGE_PRIMARY
    if not os.getenv("GEMINI_NANO_IMAGE_MODEL"):
        app_config.GEMINI_NANO_IMAGE_MODEL = CHEAP_IMAGE_PRIMARY

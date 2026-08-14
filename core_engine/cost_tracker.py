# -*- coding: utf-8 -*-
"""
CostTracker — per-run financial tracking for the Unified Multi-Page Factory.

Records an estimated USD cost for every generation operation (image, text, audio)
and writes a structured telemetry JSON to outputs/{page_id}/library/.
The ``estimated_cost`` field is also merged into each durable post JSON via
``CostTracker.annotate_payload()``.

Pricing constants
-----------------
All costs are approximations in USD; update ``_PRICE`` when provider pricing changes.

Image generation uses Together AI ``FLUX.1-schnell`` at **$0.003 / image**
(Gemini image pricing is deprecated / aliased to the same rate).

Usage
-----
    from core_engine.cost_tracker import CostTracker

    tracker = CostTracker(page_id="ancient_knowledge", cost_tier="nano")

    tracker.track_image("image_nano")
    tracker.track_text("text_deepseek", char_count=4000)
    tracker.track_audio(char_count=1200, sfx=True)

    payload["estimated_cost"] = tracker.total_usd()
    tracker.write_telemetry(outputs_dir / "library", variant_index=1)
"""
from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger(__name__)

_VALID_TIERS = frozenset({"nano", "economic", "premium"})


def _safe_float(value: Any, default: float = 0.0) -> float:
    """Coerce *value* to float; never raise. None / invalid → *default*."""
    if value is None:
        return default
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    if out != out:  # NaN
        return default
    return out


def _safe_int(value: Any, default: int = 0) -> int:
    """Coerce *value* to int; never raise. None / invalid → *default*."""
    if value is None:
        return default
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _normalize_tier(value: Any) -> Literal["nano", "economic", "premium"]:
    raw = str(value or "economic").lower().strip()
    if raw in _VALID_TIERS:
        return raw  # type: ignore[return-value]
    logger.warning("CostTracker | unknown cost_tier=%r — falling back to 'economic'", value)
    return "economic"

# ---------------------------------------------------------------------------
# Pricing table (USD per call/unit, approximate as of mid-2026)
# ---------------------------------------------------------------------------
_PRICE: dict[str, float] = {
    # Image generation — Together AI (dynamic by model)
    "image_flux_schnell":  0.003_0,   # FLUX.1-schnell (default / cheapest)
    "image_flux_dev":      0.025_0,   # FLUX.1-dev
    "image_flux_pro":      0.050_0,   # FLUX.1-pro
    "image_sdxl":          0.008_0,   # SDXL / Stable Diffusion XL
    "image_nano":          0.003_0,   # alias → Schnell
    "image_economic":      0.003_0,   # alias → Schnell
    "image_premium":       0.025_0,   # alias → FLUX.1-dev
    # Deprecated Gemini image keys (aliased; images no longer use Gemini)
    "image_gemini_flash":  0.003_0,
    "image_gemini_pro":    0.025_0,

    # Text / LLM — approximate per inference call (not per token for simplicity)
    "text_deepseek":       0.000_2,   # DeepSeek V4 — optional secondary fallback only
    "text_gemini_flash":   0.000_4,   # Gemini 2.5 Flash text (PRIMARY default)
    "text_gemini_pro":     0.002_0,   # Gemini Pro text
    "text_claude_haiku":   0.001_0,   # Claude Haiku
    "text_claude_sonnet":  0.005_0,   # Claude Sonnet

    # Audio — ElevenLabs TTS
    "tts_per_char":        0.000_030, # ~$30 / 1M characters
    "sfx_per_call":        0.002_0,   # per SFX generation call

    # Remote GPU — RunPod RTX 4090 (override via env — see track_gpu_seconds)
    # Pod on-demand (public pricing page): Community $0.34/hr, Secure $0.69/hr
    "gpu_pod_rtx4090_community_per_sec": 0.34 / 3600.0,   # ≈ $0.0000944/s
    "gpu_pod_rtx4090_secure_per_sec":    0.69 / 3600.0,   # ≈ $0.0001917/s
    # Serverless: official endpoint-config table lists "4090 PRO" at $0.00031/s
    # (docs.runpod.io/serverless/endpoints/endpoint-configurations). Older blog
    # posts cited $0.00044 flex / $0.00026 active — do NOT prefer those when they
    # disagree with the current docs table. Active workers: apply −40% when the
    # account still uses that discount; confirm in RunPod console when keyed.
    "gpu_serverless_rtx4090_flex_per_sec":   0.000_31,
    "gpu_serverless_rtx4090_active_per_sec": 0.000_186,  # 0.00031 * 0.60
}

# Map cost_tier → default image model key (all tiers → FLUX Schnell)
_TIER_IMAGE_KEY: dict[str, str] = {
    "nano":     "image_flux_schnell",
    "economic": "image_flux_schnell",
    "premium":  "image_flux_schnell",
}

# Map cost_tier → default text model key
_TIER_TEXT_KEY: dict[str, str] = {
    "nano":     "text_gemini_flash",   # Gemini primary even on nano pages
    "economic": "text_gemini_flash",
    "premium":  "text_claude_sonnet",
}


@dataclass
class CostTracker:
    """
    Thread-safe accumulator of generation cost estimates.

    Parameters
    ----------
    page_id:
        The active page slug (e.g. ``ancient_knowledge``).
    cost_tier:
        ``"nano"`` | ``"economic"`` | ``"premium"``.
        Controls which default pricing keys are used when callers omit
        the explicit ``model_key`` argument.
    """

    page_id: str
    cost_tier: Literal["nano", "economic", "premium"] = "economic"

    _entries: list[dict] = field(default_factory=list, repr=False, init=False)
    _total_usd: float = field(default=0.0, repr=False, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, init=False)

    def __post_init__(self) -> None:
        self.page_id = str(self.page_id or "unknown").strip() or "unknown"
        object.__setattr__(self, "cost_tier", _normalize_tier(self.cost_tier))

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _add(self, operation: str, model_key: str, units: float, cost: float) -> float:
        units_f = max(0.0, _safe_float(units, 0.0))
        cost_f = max(0.0, _safe_float(cost, 0.0))
        entry = {
            "operation": str(operation or "unknown"),
            "model_key": str(model_key or "unknown"),
            "units": units_f,
            "cost_usd": round(cost_f, 8),
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        with self._lock:
            self._entries.append(entry)
            self._total_usd = _safe_float(self._total_usd, 0.0) + cost_f
        logger.debug(
            "CostTracker | page=%s op=%s model=%s cost=$%.6f  total=$%.6f",
            self.page_id, operation, model_key, cost_f, self._total_usd,
        )
        return cost_f

    # ------------------------------------------------------------------
    # Public tracking methods
    # ------------------------------------------------------------------

    def track_image(
        self,
        model_key: str | None = None,
        count: int = 1,
    ) -> float:
        """
        Record the estimated cost for ``count`` image generation calls.

        Parameters
        ----------
        model_key:
            One of the keys in ``_PRICE`` (e.g. ``"image_nano"``).
            Defaults to the tier-appropriate key.
        count:
            Number of images generated (for multi-image sequence reels).
        """
        try:
            n = max(1, _safe_int(count, 1))
            key = model_key or _TIER_IMAGE_KEY.get(self.cost_tier, "image_flux_schnell")
            price_per = _safe_float(
                _PRICE.get(key, _PRICE["image_flux_schnell"]),
                _PRICE["image_flux_schnell"],
            )
            if price_per <= 0:
                price_per = _PRICE["image_flux_schnell"]
            total = price_per * n
            return self._add("image_generation", key, float(n), total)
        except Exception as exc:  # noqa: BLE001
            logger.warning("CostTracker.track_image failed (%s) — recording $0 fallback", exc)
            return 0.0

    def track_text(
        self,
        model_key: str | None = None,
        char_count: int = 2000,
    ) -> float:
        """
        Record the estimated cost for one LLM text inference call.

        Parameters
        ----------
        model_key:
            One of the ``text_*`` keys in ``_PRICE``.
        char_count:
            Approximate character count of the prompt+response (informational only;
            current pricing is flat-per-call for simplicity).
        """
        try:
            chars = max(0, _safe_int(char_count, 0))
            key = model_key or _TIER_TEXT_KEY.get(self.cost_tier, "text_gemini_flash")
            price = _safe_float(
                _PRICE.get(key, _PRICE["text_gemini_flash"]),
                _PRICE["text_gemini_flash"],
            )
            return self._add("text_generation", key, float(chars), price)
        except Exception as exc:  # noqa: BLE001
            logger.warning("CostTracker.track_text failed (%s) — recording $0 fallback", exc)
            return 0.0

    def track_audio(
        self,
        char_count: int = 0,
        sfx: bool = False,
        model_key: str = "tts_per_char",
    ) -> float:
        """
        Record the estimated cost for ElevenLabs TTS + optional SFX.

        Parameters
        ----------
        char_count:
            Character count of the TTS script (drives per-char cost).
        sfx:
            True if a separate SFX generation call was also made.
        """
        try:
            chars = max(0, _safe_int(char_count, 0))
            tts_cost = _safe_float(_PRICE.get("tts_per_char"), 0.0) * chars
            sfx_cost = _safe_float(_PRICE.get("sfx_per_call"), 0.0) if sfx else 0.0
            total = tts_cost + sfx_cost
            return self._add(
                "audio_generation",
                "elevenlabs_tts" + ("+sfx" if sfx else ""),
                float(chars),
                total,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("CostTracker.track_audio failed (%s) — recording $0 fallback", exc)
            return 0.0

    def track_gpu_seconds(
        self,
        seconds: float,
        *,
        mode: str | None = None,
        jobs: int = 1,
        model_key: str | None = None,
    ) -> float:
        """
        Record estimated RunPod GPU cost for *seconds* of billed GPU time.

        Parameters
        ----------
        seconds:
            Wall-clock GPU time to bill. For **pod** mode this is typically
            dedicated uptime attributed to the run; for **serverless** it is
            the sum of per-job active GPU seconds (workers bill in parallel).
        mode:
            ``\"comfyui\"`` / ``\"pod\"`` → pod hourly rate.
            ``\"runpod\"`` / ``\"serverless\"`` → serverless per-second rate.
            Default: ``REMOTE_GPU_MODE`` from config/env.
        jobs:
            Informational unit count (e.g. number of image jobs).
        """
        import os

        secs = max(0.0, _safe_float(seconds, 0.0))
        if secs <= 0:
            return 0.0
        try:
            return self._track_gpu_seconds_inner(secs, mode=mode, jobs=jobs, model_key=model_key)
        except Exception as exc:  # noqa: BLE001
            logger.warning("CostTracker.track_gpu_seconds failed (%s) — recording $0 fallback", exc)
            return 0.0

    def _track_gpu_seconds_inner(
        self,
        secs: float,
        *,
        mode: str | None,
        jobs: int,
        model_key: str | None,
    ) -> float:
        import os

        mode_raw = (
            mode
            or os.getenv("REMOTE_GPU_MODE")
            or "comfyui"
        ).strip().lower()
        is_serverless = mode_raw in ("runpod", "serverless")

        # Optional explicit $/unit overrides from env/config
        try:
            import config as app_config

            pod_override = float(getattr(app_config, "RUNPOD_POD_RTX4090_USD_PER_HOUR", 0) or 0)
            srv_override = float(
                getattr(app_config, "RUNPOD_SERVERLESS_RTX4090_USD_PER_SEC", 0) or 0
            )
            cloud = str(getattr(app_config, "RUNPOD_CLOUD_TYPE", "community") or "community").lower()
            worker_t = str(
                getattr(app_config, "RUNPOD_SERVERLESS_WORKER_TYPE", "flex") or "flex"
            ).lower()
        except Exception:
            pod_override = float(os.getenv("RUNPOD_POD_RTX4090_USD_PER_HOUR") or 0)
            srv_override = float(os.getenv("RUNPOD_SERVERLESS_RTX4090_USD_PER_SEC") or 0)
            cloud = (os.getenv("RUNPOD_CLOUD_TYPE") or "community").strip().lower()
            worker_t = (os.getenv("RUNPOD_SERVERLESS_WORKER_TYPE") or "flex").strip().lower()

        if model_key:
            key = model_key
            rate = _safe_float(_PRICE.get(key), 0.0)
            if rate <= 0:
                logger.warning(
                    "CostTracker | unknown GPU model_key=%r — using community pod rate",
                    key,
                )
                key = "gpu_pod_rtx4090_community_per_sec"
                rate = _safe_float(_PRICE.get(key), 0.0)
        elif is_serverless:
            if srv_override > 0:
                key = "gpu_serverless_rtx4090_custom_per_sec"
                rate = srv_override
            elif worker_t in ("active", "always_on", "min_workers"):
                key = "gpu_serverless_rtx4090_active_per_sec"
                rate = _PRICE[key]
            else:
                key = "gpu_serverless_rtx4090_flex_per_sec"
                rate = _PRICE[key]
        else:
            if pod_override > 0:
                key = "gpu_pod_rtx4090_custom_per_sec"
                rate = pod_override / 3600.0
            elif cloud in ("secure", "secure_cloud"):
                key = "gpu_pod_rtx4090_secure_per_sec"
                rate = _PRICE[key]
            else:
                key = "gpu_pod_rtx4090_community_per_sec"
                rate = _PRICE[key]

        rate = max(0.0, _safe_float(rate, 0.0))
        total = rate * secs
        return self._add(
            "gpu_compute",
            key,
            secs,
            total,
        )

    # ------------------------------------------------------------------
    # Aggregation
    # ------------------------------------------------------------------

    def total_usd(self) -> float:
        """Return the running total estimated cost in USD."""
        with self._lock:
            return self._total_usd

    def to_dict(self) -> dict:
        """
        Return a serialisable snapshot of the cost run.

        Compatible with ``write_atomic_json`` / ``merge_update_json``
        from ``avatar_engine.durable_library``.
        """
        with self._lock:
            return {
                "page_id": self.page_id,
                "cost_tier": self.cost_tier,
                "total_estimated_usd": round(self._total_usd, 6),
                "breakdown": list(self._entries),
                "tracked_at": datetime.now(timezone.utc).isoformat(),
            }

    def annotate_payload(self, payload: dict) -> dict:
        """
        Inject ``estimated_cost`` and ``cost_tier`` into a durable post payload
        *in-place* and return the same dict.

        This is the canonical hook called just before ``write_atomic_json``
        so every persisted post JSON carries cost telemetry automatically.
        """
        if not isinstance(payload, dict):
            payload = {}
        payload["estimated_cost"] = round(_safe_float(self.total_usd(), 0.0), 6)
        payload["cost_tier"] = self.cost_tier
        return payload

    def write_telemetry(
        self,
        library_dir: Path,
        variant_index: int = 1,
    ) -> Path:
        """
        Write a standalone cost telemetry JSON to ``library_dir``.

        File name: ``cost_{page_id}_{stamp}_v{variant:02d}.json``

        Returns the absolute path of the written file.
        """
        try:
            dest = Path(library_dir).expanduser().resolve()
        except Exception as exc:  # noqa: BLE001
            logger.warning("CostTracker | invalid library_dir=%r (%s)", library_dir, exc)
            dest = Path.cwd() / "outputs" / "cost_telemetry"
        try:
            dest.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.warning("CostTracker | telemetry mkdir failed (%s)", exc)
            return dest / "cost_unwritten.json"
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        vix = max(1, _safe_int(variant_index, 1))
        fname = f"cost_{self.page_id}_{stamp}_v{vix:02d}.json"
        out = dest / fname
        try:
            out.write_text(
                json.dumps(self.to_dict(), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            logger.info(
                "CostTracker | telemetry written: %s  total=$%.6f",
                out.name, self.total_usd(),
            )
        except OSError as exc:
            logger.warning("CostTracker | failed to write telemetry: %s", exc)
        return out


def print_cost_summary(
    variant_index: int,
    total_variants: int,
    images_this_reel: int,
    total_batch_images: int,
    total_reel_cost: float,
) -> None:
    """
    Prints an accurate summary log separating individual reel assets from cumulative batch metrics.
    """
    try:
        v = max(1, _safe_int(variant_index, 1))
        n = max(1, _safe_int(total_variants, 1))
        imgs = max(0, _safe_int(images_this_reel, 0))
        batch = max(0, _safe_int(total_batch_images, 0))
        cost = max(0.0, _safe_float(total_reel_cost, 0.0))
        print("=" * 62)
        print(f"| COST ANALYSIS SUMMARY - REEL {v}/{n}")
        print("=" * 62)
        print(
            f"  Visual Assets (This Reel) : {imgs} AI Base Images "
            f"-> Compiled to MP4 Reel v{v}"
        )
        print(
            f"  Batch Visual Assets Total : {batch} AI Base Images generated so far"
        )
        print(f"  Estimated Cost (This Reel): ${cost:.4f} USD")
        print("=" * 62)
    except Exception as exc:  # noqa: BLE001
        logger.warning("print_cost_summary failed (%s) — skipping block", exc)
        print("| COST ANALYSIS SUMMARY | (unavailable)")

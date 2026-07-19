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
from typing import Literal

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pricing table (USD per call/unit, approximate as of mid-2026)
# ---------------------------------------------------------------------------
_PRICE: dict[str, float] = {
    # Image generation — per generation call
    "image_nano":          0.000_3,   # Gemini Flash image (economic/nano tier)
    "image_economic":      0.001_0,   # Gemini Flash standard
    "image_premium":       0.006_0,   # Gemini Pro image

    # Text / LLM — approximate per inference call (not per token for simplicity)
    "text_deepseek":       0.000_2,   # DeepSeek Chat — cheapest tier
    "text_gemini_flash":   0.000_4,   # Gemini 2.5 Flash text
    "text_gemini_pro":     0.002_0,   # Gemini Pro text
    "text_claude_haiku":   0.001_0,   # Claude Haiku
    "text_claude_sonnet":  0.005_0,   # Claude Sonnet

    # Audio — ElevenLabs TTS
    "tts_per_char":        0.000_030, # ~$30 / 1M characters
    "sfx_per_call":        0.002_0,   # per SFX generation call
}

# Map cost_tier → default image model key
_TIER_IMAGE_KEY: dict[str, str] = {
    "nano":     "image_nano",
    "economic": "image_economic",
    "premium":  "image_premium",
}

# Map cost_tier → default text model key
_TIER_TEXT_KEY: dict[str, str] = {
    "nano":     "text_deepseek",
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

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _add(self, operation: str, model_key: str, units: float, cost: float) -> float:
        entry = {
            "operation": operation,
            "model_key": model_key,
            "units": units,
            "cost_usd": round(cost, 8),
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        with self._lock:
            self._entries.append(entry)
            self._total_usd += cost
        logger.debug(
            "CostTracker | page=%s op=%s model=%s cost=$%.6f  total=$%.6f",
            self.page_id, operation, model_key, cost, self._total_usd,
        )
        return cost

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
        key = model_key or _TIER_IMAGE_KEY.get(self.cost_tier, "image_economic")
        price_per = _PRICE.get(key, _PRICE["image_economic"])
        total = price_per * max(1, count)
        return self._add("image_generation", key, float(count), total)

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
        key = model_key or _TIER_TEXT_KEY.get(self.cost_tier, "text_gemini_flash")
        price = _PRICE.get(key, _PRICE["text_gemini_flash"])
        return self._add("text_generation", key, float(char_count), price)

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
        tts_cost = _PRICE["tts_per_char"] * max(0, char_count)
        sfx_cost = _PRICE["sfx_per_call"] if sfx else 0.0
        total = tts_cost + sfx_cost
        return self._add(
            "audio_generation",
            "elevenlabs_tts" + ("+sfx" if sfx else ""),
            float(char_count),
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
        payload["estimated_cost"] = round(self.total_usd(), 6)
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
        library_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        fname = f"cost_{self.page_id}_{stamp}_v{variant_index:02d}.json"
        out = library_dir / fname
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

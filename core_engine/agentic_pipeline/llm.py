# -*- coding: utf-8 -*-
"""Thin Gemini text helper shared by ThemeCurator / Copywriter / VisualDirector."""
from __future__ import annotations

import json
import logging
import re
import threading
from typing import Any

_LOG = logging.getLogger(__name__)

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE | re.MULTILINE)

# CostTracker reported from the currently running node (thread-local).  Nodes
# call ``set_cost_tracker`` for the duration of their run so every Gemini
# round-trip is charged to the shared tracker safely.
_CUR_TRACKER = threading.local()


def set_cost_tracker(tracker: Any | None) -> None:
    """Bind a CostTracker to the calling thread for the next LLM calls."""
    _CUR_TRACKER.tracker = tracker


def _report_cost(response: Any, char_est: int) -> None:
    """Safely charge prompt/completion token counts to the thread's tracker."""
    tracker = getattr(_CUR_TRACKER, "tracker", None)
    if tracker is None:
        return
    try:
        um = getattr(response, "usage_metadata", None) or {}
        prompt_tok = 0
        completion_tok = 0
        if isinstance(um, dict):
            prompt_tok = int(float(um.get("prompt_token_count") or 0))
            completion_tok = int(float(um.get("candidates_token_count") or 0))
        else:
            prompt_tok = int(float(getattr(um, "prompt_token_count", 0) or 0))
            completion_tok = int(float(getattr(um, "candidates_token_count", 0) or 0))
        total_tokens = prompt_tok + completion_tok
        if total_tokens <= 0:
            total_tokens = max(1, int(char_est or 0) // 4)
        tracker.track_text(
            "text_gemini_flash",
            token_count=total_tokens,
        )
    except Exception as exc:  # noqa: BLE001
        # Cost reporting must NEVER crash the pipeline.
        _LOG.debug("CostTracker report skipped (%s)", exc)


def _client():
    import config as app_config
    from avatar_engine.providers.gemini_utils import make_gemini_client_with_fallback

    key = getattr(app_config, "GEMINI_API_KEY", None)
    if not key:
        raise RuntimeError("GEMINI_API_KEY missing — cannot run agentic text nodes.")
    return make_gemini_client_with_fallback(key)


def _model_id() -> str:
    import config as app_config

    mid = (
        getattr(app_config, "GEMINI_ECONOMIC_BRAIN_MODEL", None)
        or "models/gemini-2.5-flash"
    )
    if not str(mid).startswith("models/"):
        mid = f"models/{mid}"
    return str(mid)


def complete_text(prompt: str, *, system: str | None = None) -> str:
    """Single-turn Gemini completion. Returns stripped text (may be empty)."""
    from avatar_engine.providers.gemini_utils import generate_content_with_model_fallback

    blob = prompt.strip()
    if system:
        blob = f"{system.strip()}\n\n{blob}"
    client = _client()
    chain = [_model_id(), "models/gemini-2.5-flash", "models/gemini-2.0-flash"]
    response = generate_content_with_model_fallback(
        client, chain, contents=[blob],
    )
    _report_cost(response, char_est=len(blob))
    text_attr = getattr(response, "text", None)
    text = text_attr() if callable(text_attr) else (text_attr or "")
    return str(text).strip()


def complete_json(prompt: str, *, system: str | None = None) -> dict[str, Any]:
    """Gemini completion parsed as a JSON object. Returns {} on failure."""
    raw = complete_text(prompt, system=system)
    if not raw:
        return {}
    cleaned = _FENCE_RE.sub("", raw).strip()
    try:
        data = json.loads(cleaned)
        return data if isinstance(data, dict) else {}
    except Exception:
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if not match:
            _LOG.warning("LLM JSON parse failed; returning empty dict.")
            return {}
        try:
            data = json.loads(match.group(0))
            return data if isinstance(data, dict) else {}
        except Exception:
            _LOG.warning("LLM JSON parse failed on extracted object.")
            return {}

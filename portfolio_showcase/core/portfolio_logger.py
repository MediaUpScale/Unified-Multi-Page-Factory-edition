# -*- coding: utf-8 -*-
"""Portfolio Architecture Logger — event-bus subscriber, not an engine import.

Listens to Aiwake pub/sub events and writes recruiter-facing telemetry to
``portfolio_showcase/data/projects/aiwake/architecture.json``. This file must not import
``channels_config.aiwake`` — the engine can be sold without this folder.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_LOG = logging.getLogger("portfolio_showcase")

SHOWCASE_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = SHOWCASE_ROOT / "data"
DEFAULT_JSON = DATA_DIR / "projects" / "aiwake" / "architecture.json"

PORTFOLIO_SUCCESS_MESSAGE = (
    "Portfolio Module: Architecture telemetry recorded successfully for frontend delivery."
)

_TIER_NAMES = ("OPENING", "PRESSURE", "CONTRADICTION", "EXISTENTIAL", "TERMINAL")

_SOLID = [
    {
        "principle": "SRP",
        "applied": True,
        "where": "observers/core.py — each observer owns one side-effect",
    },
    {
        "principle": "OCP",
        "applied": True,
        "where": "models/base.py LLMProvider and media/audio.py TTSEngine extension points",
    },
    {
        "principle": "LSP",
        "applied": True,
        "where": "SilentTTSEngine / EdgeTTSEngine and Offline / OpenRouter providers are substitutable",
    },
    {
        "principle": "ISP",
        "applied": True,
        "where": "DebateObserver.interests — listeners opt into the events they handle",
    },
    {
        "principle": "DIP",
        "applied": True,
        "where": "DebateRoom depends on LLMProvider ABC, not a concrete HTTP client",
    },
]


def _tier_for_turn(turn_index: int) -> str:
    return _TIER_NAMES[min(max(0, int(turn_index) // 2), len(_TIER_NAMES) - 1)]


class PortfolioLogger:
    """Records design-pattern telemetry for a future Next.js / React showcase."""

    def __init__(self, *, data_path: Path | None = None) -> None:
        self.data_path = Path(data_path) if data_path is not None else DEFAULT_JSON
        self._bound = False
        self._t0 = time.perf_counter()
        self._clock0 = datetime.now(timezone.utc)
        self._reset_state()

    def _reset_state(self) -> None:
        self.session_id = ""
        self.topic = ""
        self.models: dict[str, str] = {}
        self.agents: list[dict[str, Any]] = []
        self.rag_recalls: list[dict[str, Any]] = []
        self.decision_tree: list[dict[str, Any]] = []
        self.milestones: list[dict[str, Any]] = []
        self.listener_names: list[str] = []
        self.tts_engine = ""
        self.llm_providers: dict[str, str] = {}
        self.media: dict[str, Any] = {
            "audio_mixed": False,
            "video_rendered": False,
            "typewriter_core_only": True,
            "typewriter_gain_db": -15.0,
        }
        self.end_reason = ""

    def bind(self, bus: Any) -> None:
        """Subscribe to the engine event bus. Safe to call more than once."""
        if self._bound:
            return
        mapping = (
            ("ON_PIPELINE_START", self._on_pipeline_start),
            ("ON_DEBATE_STARTED", self._on_debate_started),
            ("ON_PROMPT_PREPARED", self._on_prompt_prepared),
            ("ON_TURN_COMPLETE", self._on_turn_complete),
            ("ON_GUARDRAIL_TRIPPED", self._on_guardrail),
            ("ON_AUDIO_MIXED", self._on_audio_mixed),
            ("ON_VIDEO_RENDERED", self._on_video_rendered),
            ("ON_DEBATE_ENDED", self._on_debate_ended),
            ("ON_PIPELINE_FINISH", self._on_pipeline_finish),
        )
        for event, handler in mapping:
            bus.subscribe(event, handler)
        self._bound = True

    def _elapsed(self) -> float:
        return round(time.perf_counter() - self._t0, 4)

    def _on_pipeline_start(self, payload: dict[str, Any]) -> None:
        self._reset_state()
        self._t0 = time.perf_counter()
        self._clock0 = datetime.now(timezone.utc)
        self.session_id = str(payload.get("session_id") or "")
        self.topic = str(payload.get("topic") or "")
        self.llm_providers = dict(payload.get("llm_providers") or {})
        self.models = dict(payload.get("models") or {})
        if payload.get("typewriter_gain_db") is not None:
            self.media["typewriter_gain_db"] = float(payload["typewriter_gain_db"])
        if payload.get("scroll_s") is not None:
            self.media["scroll_s"] = float(payload["scroll_s"])
        if payload.get("send_flash_s") is not None:
            self.media["send_flash_s"] = float(payload["send_flash_s"])
        engine = str(payload.get("audio_engine") or "")
        if engine:
            self.tts_engine = engine
        self.decision_tree.append(
            {
                "step": "llm_strategy",
                "pattern": "Strategy",
                "orchestrator_model": payload.get("orchestrator_model"),
                "target_model": payload.get("target_model"),
                "orchestrator_provider": self.llm_providers.get("orchestrator"),
                "target_provider": self.llm_providers.get("target"),
            }
        )
        self.decision_tree.append(
            {
                "step": "tts_strategy",
                "pattern": "Strategy",
                "engine": self.tts_engine or payload.get("audio_engine"),
                "swappable": ["edge", "silent"],
            }
        )
        self.milestones.append({"event": "ON_PIPELINE_START", "t_s": 0.0})

    def _on_debate_started(self, payload: dict[str, Any]) -> None:
        self.session_id = str(payload.get("session_id") or self.session_id)
        self.topic = str(payload.get("topic") or self.topic)
        self.models = dict(payload.get("models") or payload.get("detail", {}).get("models") or self.models)
        self.listener_names = list(payload.get("observers") or [])
        agents = payload.get("agents")
        if isinstance(agents, list) and agents:
            self.agents = [dict(item) for item in agents if isinstance(item, dict)]
            self.llm_providers.update(
                {str(item.get("role")): str(item.get("provider") or "") for item in self.agents}
            )
        self.milestones.append({"event": "ON_DEBATE_STARTED", "t_s": self._elapsed(), "models": dict(self.models)})

    def _on_prompt_prepared(self, payload: dict[str, Any]) -> None:
        detail = dict(payload.get("detail") or {})
        preview = list(payload.get("extra_context_preview") or detail.get("extra_context_preview") or [])
        blocks = int(payload.get("extra_context_blocks") or detail.get("extra_context_blocks") or 0)
        recall = {
            "turn_index": payload.get("turn_index"),
            "role": payload.get("role") or detail.get("role"),
            "model": payload.get("model_slug") or detail.get("model_slug"),
            "provider": payload.get("provider") or detail.get("provider"),
            "blocks": blocks,
            "chars": int(payload.get("extra_context_chars") or detail.get("extra_context_chars") or 0),
            "preview": [str(item) for item in preview],
            "applied": blocks > 0,
        }
        self.rag_recalls.append(recall)
        self.milestones.append(
            {"event": "ON_PROMPT_PREPARED", "t_s": self._elapsed(), "turn_index": payload.get("turn_index")}
        )

    def _on_turn_complete(self, payload: dict[str, Any]) -> None:
        utt = dict(payload.get("utterance") or {})
        turn_index = int(utt.get("turn_index") or payload.get("turn_index") or 0)
        role = str(utt.get("role") or payload.get("role") or "")
        model = str(utt.get("model_slug") or "")
        self.decision_tree.append(
            {
                "step": "generation",
                "turn_index": turn_index,
                "role": role,
                "model": model,
                "escalation_tier": _tier_for_turn(turn_index),
                "latency_ms": utt.get("latency_ms", 0),
                "char_count": utt.get("char_count", 0),
                "pattern": "Strategy",
            }
        )
        self.milestones.append(
            {
                "event": "ON_TURN_COMPLETE",
                "t_s": self._elapsed(),
                "turn_index": turn_index,
                "role": role,
                "model": model,
                "latency_ms": utt.get("latency_ms", 0),
            }
        )

    def _on_guardrail(self, payload: dict[str, Any]) -> None:
        detail = dict(payload.get("detail") or {})
        self.decision_tree.append(
            {
                "step": "guardrail",
                "turn_index": payload.get("turn_index"),
                "violations": payload.get("violations") or detail.get("violations"),
                "pattern": "Decorator",
            }
        )

    def _on_audio_mixed(self, payload: dict[str, Any]) -> None:
        self.media["audio_mixed"] = True
        detail = dict(payload.get("detail") or {})
        merged = {**detail, **{k: v for k, v in payload.items() if k not in {"event", "detail"}}}
        self.media.update(merged)
        self.tts_engine = str(payload.get("engine") or detail.get("engine") or self.tts_engine)
        self.milestones.append({"event": "ON_AUDIO_MIXED", "t_s": self._elapsed()})

    def _on_video_rendered(self, payload: dict[str, Any]) -> None:
        self.media["video_rendered"] = True
        path = payload.get("video_path") or (payload.get("detail") or {}).get("video_path")
        if path:
            self.media["video_path"] = path
        self.milestones.append({"event": "ON_VIDEO_RENDERED", "t_s": self._elapsed()})

    def _on_debate_ended(self, payload: dict[str, Any]) -> None:
        detail = dict(payload.get("detail") or {})
        self.end_reason = str(payload.get("reason") or detail.get("reason") or self.end_reason)
        self.milestones.append(
            {
                "event": "ON_DEBATE_ENDED",
                "t_s": self._elapsed(),
                "reason": self.end_reason,
                "turns": payload.get("turns") or detail.get("turns"),
            }
        )

    def _on_pipeline_finish(self, payload: dict[str, Any]) -> None:
        pipeline_s = payload.get("pipeline_s")
        if payload.get("end_reason"):
            self.end_reason = str(payload["end_reason"])
        self.finalize(pipeline_s=float(pipeline_s) if pipeline_s is not None else None)

    def document(self, *, pipeline_s: float | None = None) -> dict[str, Any]:
        elapsed = pipeline_s if pipeline_s is not None else (time.perf_counter() - self._t0)
        return {
            "schema_version": "1.0",
            "frontend": {
                "consumer": "next.js",
                "framework": "react",
                "showcase": "minimalist-architecture-portfolio",
            },
            "session_id": self.session_id,
            "topic": self.topic,
            "started_at": self._clock0.isoformat(),
            "ended_at": datetime.now(timezone.utc).isoformat(),
            "pipeline_execution_s": round(float(elapsed), 4),
            "end_reason": self.end_reason,
            "agents": self.agents,
            "models": self.models,
            "llm_providers": self.llm_providers,
            "rag": {
                "applied": any(item.get("applied") for item in self.rag_recalls),
                "recalls": self.rag_recalls,
            },
            "decision_tree": self.decision_tree,
            "milestones": self.milestones,
            "media": self.media,
            "patterns": {
                "observer": {
                    "applied": True,
                    "pattern": "Observer",
                    "broadcaster": "EventBus",
                    "listeners": self.listener_names or ["PortfolioLogger"],
                },
                "strategy": {
                    "applied": True,
                    "pattern": "Strategy",
                    "llm_provider": self.llm_providers.get("orchestrator", ""),
                    "tts_engine": self.tts_engine,
                    "swappable": ["LLMProvider", "TTSEngine"],
                    "note": "Dynamically swapping TTS or LLM providers never reaches room.py",
                },
                "solid": _SOLID,
            },
        }

    def finalize(self, *, pipeline_s: float | None = None) -> Path:
        """Write ``aiwake_architecture.json`` and print the frontend-delivery line."""
        doc = self.document(pipeline_s=pipeline_s)
        self.data_path.parent.mkdir(parents=True, exist_ok=True)
        self.data_path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
        print(PORTFOLIO_SUCCESS_MESSAGE)
        _LOG.info("architecture telemetry -> %s", self.data_path)
        return self.data_path


__all__ = ["DEFAULT_JSON", "PORTFOLIO_SUCCESS_MESSAGE", "PortfolioLogger"]

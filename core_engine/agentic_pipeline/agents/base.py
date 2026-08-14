# -*- coding: utf-8 -*-
"""Abstract agent contract — every node is a pure State → State transform."""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod

from core_engine.agentic_pipeline import llm
from core_engine.agentic_pipeline.state import PipelineState

_LOG = logging.getLogger(__name__)


class BaseAgent(ABC):
    """Decoupled pipeline node. Never imports sibling agents."""

    name: str = "base"

    @abstractmethod
    def run(self, state: PipelineState) -> PipelineState:
        """Mutate and return ``state``. Must not raise out of the orchestrator."""
        raise NotImplementedError

    def _bind_cost_tracker(self, state: PipelineState) -> None:
        """Charge all LLM round-trips in this node to ``state.cost_tracker``."""
        llm.set_cost_tracker(getattr(state, "cost_tracker", None))

    @staticmethod
    def _track_image(state: PipelineState, count: int = 1) -> None:
        tracker = getattr(state, "cost_tracker", None)
        if tracker is None or count <= 0:
            return
        try:
            tracker.track_image(count=max(1, int(count)))
        except Exception as exc:  # noqa: BLE001
            _LOG.debug("CostTracker image skipped (%s)", exc)

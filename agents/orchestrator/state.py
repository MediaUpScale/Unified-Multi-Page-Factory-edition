# -*- coding: utf-8 -*-
"""
Shared pipeline state — the only contract between the four agents.

Every node reads from and writes to a ``PipelineState``. Agents never call
each other directly; the orchestrator owns the graph.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class QAStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    FAILED = "failed"
    SKIPPED = "skipped"


class PipelineNodeError(BaseModel):
    """Non-fatal error recorded on a node so the batch can continue."""

    node: str
    message: str
    attempt: int = 0
    timestamp_utc: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class QAVerdict(BaseModel):
    """Return payload of ``VisualQAAgent`` — boolean gate + rewrite feedback."""

    is_approved: bool = False
    feedback_string: str = ""
    score: float = Field(default=0.0, ge=0.0, le=10.0)
    flaws: list[str] = Field(default_factory=list)
    criteria_checked: list[str] = Field(default_factory=list)

    @field_validator("score")
    @classmethod
    def _round_score(cls, v: float) -> float:
        return round(float(v), 2)


class PipelineState(BaseModel):
    """
    Canonical context object that flows ThemeCurator → Copywriter →
    VisualDirector ⇄ VisualQA.

    Required contract fields: ``theme``, ``caption``, ``image_prompt``,
    ``generated_image_paths``, ``qa_status``.
    """

    # Identity
    channel_id: str = "ancient_knowledge"
    post_index: int = 0
    seed_topic: str = ""
    post_type: str = "SMART_BAIT"

    # Contract fields
    theme: str = ""
    caption: str = ""
    image_prompt: str = ""
    generated_image_paths: list[str] = Field(default_factory=list)
    qa_status: QAStatus = QAStatus.PENDING

    # Visual / copy extras
    overlay_text: str = ""
    visual_hook: str = ""
    geo_anchor: str = ""
    caption_mode: str = ""
    pinterest_title: str = ""
    pinterest_description: str = ""
    final_image_path: str = ""

    # Sentence-starter enforcement (anti-repetition across a batch).
    # ``reserved_starter`` is the folder/style this slot MUST open with;
    # ``used_starters`` is a shared registry of starters already consumed in
    # this batch so each post opens distinctly.
    reserved_starter: str = ""
    used_starters: list[str] = Field(default_factory=list)

    # QA loop
    qa_verdict: QAVerdict | None = None
    qa_feedback: str = ""
    retry_count: int = 0
    max_retries: int = 1
    best_score_fallback: bool = False
    prompt_history: list[str] = Field(default_factory=list)

    # Batch isolation
    errors: list[PipelineNodeError] = Field(default_factory=list)
    last_node: str = ""
    output_dir: str = ""

    # Optional per-pipeline CostTracker. Plain attribute so ``extra=forbid``
    # does not reject it — never serialized / used in to_envelope_item.
    cost_tracker: Any | None = Field(default=None, exclude=True)

    model_config = {"extra": "forbid"}

    @property
    def latest_image_path(self) -> str:
        return self.generated_image_paths[-1] if self.generated_image_paths else ""

    @property
    def is_approved(self) -> bool:
        return self.qa_status == QAStatus.APPROVED

    def record_error(self, node: str, message: str, *, attempt: int = 0) -> None:
        self.errors.append(
            PipelineNodeError(node=node, message=str(message)[:800], attempt=attempt)
        )

    def to_envelope_item(self) -> dict[str, Any]:
        """Shape expected by ``main.py`` production summary / durable writes."""
        img = self.final_image_path or self.latest_image_path
        return {
            "variant_index": self.post_index + 1,
            "theme": self.theme,
            "caption": self.caption,
            "overlay_text": self.overlay_text,
            "image_prompt": self.image_prompt,
            "local_image_path": img,
            "image_path": img,
            "generated_image_paths": list(self.generated_image_paths),
            "qa_status": self.qa_status.value,
            "qa_feedback": self.qa_feedback,
            "qa_score": self.qa_verdict.score if self.qa_verdict else 0.0,
            "retry_count": self.retry_count,
            "best_score_fallback": self.best_score_fallback,
            "caption_mode": self.caption_mode or "agentic",
            "pinterest_title": self.pinterest_title,
            "pinterest_description": self.pinterest_description,
            "geo_anchor": self.geo_anchor,
            "visual_hook": self.visual_hook,
            "reserved_starter": self.reserved_starter,
            "used_starters": list(self.used_starters),
            "errors": [e.model_dump() for e in self.errors],
            "channel_id": self.channel_id,
        }

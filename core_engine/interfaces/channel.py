# -*- coding: utf-8 -*-
"""
BaseChannelConfig — strict per-channel contract.

Core engines (CaptionEngine, VisualArchitect, subject_brain, main.py) must
depend on this ABC only. They must not import avatar_engine.persona_dna or
read another page's master_dna.json.

Each channels_config/{page}/channel_adapter.py implements this interface
against that page's own JSON schema (A, B, or hybrid). RAG retrieval is
owned by the adapter: the engine asks get_rag_context(topic); it never
chooses PDF vs Chroma vs prompt-files itself.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, TypedDict


class VisualRules(TypedDict, total=False):
    """Canonical visual payload returned by ``get_visual_rules()``."""

    draw_style: str
    avatar_mode: str
    aspect_ratio: str
    atmosphere_style: str
    illustration_style: str
    environments: list[str]
    forbidden_styles: list[str]
    negative_terms: list[str]
    negative_prompt: str
    prompt_lock: str
    enable_sketch: bool
    camera_default: str
    depth_layers: str
    colour_palette: list[str]
    master_criteria: list[str]
    lighting_style: str


class BaseChannelConfig(ABC):
    """Every channel adapter must implement these five methods."""

    @property
    @abstractmethod
    def channel_id(self) -> str:
        """Machine slug, matching ``channels_config/{channel_id}/``."""

    @property
    @abstractmethod
    def display_name(self) -> str:
        """Human-readable channel name for prompts and planner rows."""

    @abstractmethod
    def get_system_prompt(self) -> str:
        """Full persona / voice / disclaimer block for LLM system prompts."""

    @abstractmethod
    def get_visual_rules(self) -> dict[str, Any]:
        """
        Environments, draw style, locks, negatives.

        Keys should match ``VisualRules``. ``prompt_lock`` is the native
        image-prompt override so ``main.py`` does not need page_id if/elif.
        """

    @abstractmethod
    def get_ctas(self) -> list[str]:
        """Authorized CTA lines or keywords for this channel only."""

    @abstractmethod
    def get_narrative_angles(self, mode: str = "") -> list[str]:
        """
        Hook / research angles for ``mode`` (e.g. investigative, default).

        An adapter must not return another channel's angles. Unknown modes
        fall back to this channel's own default list.
        """

    @abstractmethod
    def get_rag_context(self, topic: str) -> str:
        """
        Channel-owned retrieval for ``topic``.

        Implementations choose the store: PDF corpus, VisualQA DNA / Chroma,
        LOFI JSON banks, or Mei prompt templates. Return a prompt-ready
        string (empty if nothing relevant).
        """

    def compose_image_prompt(self, subject: str) -> str:
        """
        Default image-prompt composer from ``get_visual_rules()``.

        Isolated adapters override this with a native lock (e.g. photoreal
        monument). Legacy channels keep using VisualArchitect's own builder.
        """
        rules = self.get_visual_rules()
        lock = str(rules.get("prompt_lock") or "").strip()
        subj = (subject or "").strip() or "the named subject"
        style = str(rules.get("illustration_style") or rules.get("atmosphere_style") or "")
        if lock:
            try:
                return lock.format(subject=subj, style=style)
            except (KeyError, ValueError, IndexError):
                return f"{lock} SUBJECT: {subj}"
        return f"{style} SUBJECT: {subj}".strip()

    def get_niche_topics(self) -> list[str]:
        """Channel-owned topic bank. Empty for legacy pages."""
        return []

# -*- coding: utf-8 -*-
"""
ChannelFactory — hybrid router.

Phase 3/4: ancient_knowledge is isolated behind AncientKnowledgeAdapter.
Every other ACTIVE_PAGE still receives LegacyChannelAdapter (persona_dna +
page_config) so Anna / Mei / Wonder Feed / LOFI keep working unchanged.
"""
from __future__ import annotations

import os

from core_engine.interfaces.channel import BaseChannelConfig

ISOLATED_CHANNEL_IDS = frozenset({"ancient_knowledge"})


class ChannelFactory:
    """Instantiate the adapter for ``ACTIVE_PAGE`` (or an explicit slug)."""

    @staticmethod
    def is_isolated(channel_id: str | None) -> bool:
        return (channel_id or "").strip().lower() in ISOLATED_CHANNEL_IDS

    @staticmethod
    def create(channel_id: str | None = None) -> BaseChannelConfig:
        cid = (channel_id or os.environ.get("ACTIVE_PAGE") or "anna_protocol").strip()
        if cid == "ancient_knowledge":
            from channels_config.ancient_knowledge.channel_adapter import (
                AncientKnowledgeAdapter,
            )

            return AncientKnowledgeAdapter()
        from core_engine.interfaces.legacy_adapter import LegacyChannelAdapter

        return LegacyChannelAdapter(channel_id=cid)

    @classmethod
    def from_env(cls) -> BaseChannelConfig:
        """Resolve ``os.environ['ACTIVE_PAGE']`` — the hybrid-router entry point."""
        return cls.create(os.environ.get("ACTIVE_PAGE"))

# -*- coding: utf-8 -*-
"""
ChannelFactory — hybrid router.

Phase 3/4: ancient_knowledge and endless_summer_paradise are isolated behind
their own adapters. Every other ACTIVE_PAGE still receives LegacyChannelAdapter
(persona_dna + page_config) so Anna / Mei / Wonder Feed / LOFI keep working
unchanged.
"""
from __future__ import annotations

import os

from core.interfaces.channel import BaseChannelConfig

ISOLATED_CHANNEL_IDS = frozenset({"ancient_knowledge", "endless_summer_paradise"})


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
        if cid == "endless_summer_paradise":
            from channels_config.endless_summer_paradise.channel_adapter import (
                EndlessSummerParadiseAdapter,
            )

            return EndlessSummerParadiseAdapter()
        from core.interfaces.legacy_adapter import LegacyChannelAdapter

        return LegacyChannelAdapter(channel_id=cid)

    @classmethod
    def from_env(cls) -> BaseChannelConfig:
        """Resolve ``os.environ['ACTIVE_PAGE']`` — the hybrid-router entry point."""
        return cls.create(os.environ.get("ACTIVE_PAGE"))

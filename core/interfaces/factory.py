# -*- coding: utf-8 -*-
"""
ChannelFactory — hybrid router.

Any ``channels_config/{slug}/channel_adapter.py`` that exposes a
``BaseChannelConfig`` subclass is loaded dynamically. Channels without an
adapter still receive ``LegacyChannelAdapter`` (persona_dna + page_config)
so Anna / Mei / Wonder Feed / LOFI keep working unchanged.
"""
from __future__ import annotations

import importlib
import inspect
import os
from typing import Type

from core.interfaces.channel import BaseChannelConfig

# Known isolated slugs (kept for callers that import the constant).
# Isolation is also true whenever a dedicated channel_adapter.py exists.
ISOLATED_CHANNEL_IDS = frozenset({"ancient_knowledge", "endless_summer_paradise"})


def _adapter_class(channel_id: str) -> Type[BaseChannelConfig] | None:
    """Return the first BaseChannelConfig subclass in the channel adapter module."""
    cid = (channel_id or "").strip()
    if not cid:
        return None
    try:
        mod = importlib.import_module(f"channels_config.{cid}.channel_adapter")
    except ImportError:
        return None
    for _name, obj in inspect.getmembers(mod, inspect.isclass):
        if (
            issubclass(obj, BaseChannelConfig)
            and obj is not BaseChannelConfig
            and obj.__module__ == mod.__name__
        ):
            return obj
    return None


class ChannelFactory:
    """Instantiate the adapter for ``ACTIVE_PAGE`` (or an explicit slug)."""

    @staticmethod
    def is_isolated(channel_id: str | None) -> bool:
        cid = (channel_id or "").strip().lower()
        if not cid:
            return False
        if cid in ISOLATED_CHANNEL_IDS:
            return True
        return _adapter_class(cid) is not None

    @staticmethod
    def create(channel_id: str | None = None) -> BaseChannelConfig:
        cid = (channel_id or os.environ.get("ACTIVE_PAGE") or "anna_protocol").strip()
        adapter_cls = _adapter_class(cid)
        if adapter_cls is not None:
            return adapter_cls()
        from core.interfaces.legacy_adapter import LegacyChannelAdapter

        return LegacyChannelAdapter(channel_id=cid)

    @classmethod
    def from_env(cls) -> BaseChannelConfig:
        """Resolve ``os.environ['ACTIVE_PAGE']`` — the hybrid-router entry point."""
        return cls.create(os.environ.get("ACTIVE_PAGE"))

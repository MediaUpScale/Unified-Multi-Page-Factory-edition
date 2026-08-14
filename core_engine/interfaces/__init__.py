# -*- coding: utf-8 -*-
"""Channel contracts — engines depend on this package, never on a page JSON schema."""
from __future__ import annotations

from core_engine.interfaces.channel import BaseChannelConfig, VisualRules
from core_engine.interfaces.factory import ChannelFactory, ISOLATED_CHANNEL_IDS
from core_engine.interfaces.legacy_adapter import LegacyChannelAdapter

__all__ = [
    "BaseChannelConfig",
    "VisualRules",
    "ChannelFactory",
    "ISOLATED_CHANNEL_IDS",
    "LegacyChannelAdapter",
]

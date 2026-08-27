# -*- coding: utf-8 -*-
"""Channel contracts — engines depend on this package, never on a page JSON schema."""
from __future__ import annotations

from core.interfaces.channel import BaseChannelConfig, VisualRules
from core.interfaces.factory import ChannelFactory, ISOLATED_CHANNEL_IDS
from core.interfaces.legacy_adapter import LegacyChannelAdapter

__all__ = [
    "BaseChannelConfig",
    "VisualRules",
    "ChannelFactory",
    "ISOLATED_CHANNEL_IDS",
    "LegacyChannelAdapter",
]

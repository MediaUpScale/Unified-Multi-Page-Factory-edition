# -*- coding: utf-8 -*-
"""Aiwake utilities — event bus and other cross-cutting helpers.

The optional portfolio showcase lives outside this package and is never
imported from here.
"""
from __future__ import annotations

from .event_bus import (
    EventBus,
    attach_optional_plugins,
    bus,
    emit,
    reset,
    subscribe,
)

__all__ = [
    "EventBus",
    "attach_optional_plugins",
    "bus",
    "emit",
    "reset",
    "subscribe",
]

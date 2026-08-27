# -*- coding: utf-8 -*-
"""Process-wide pub/sub for the Aiwake engine.

The debate loop and pipeline emit named events. Optional plugins (the
portfolio showcase) may subscribe. The bus never imports those plugins by
module name: it loads ``portfolio_showcase/core/plugin.py`` by file path when
the file exists, and otherwise ``emit`` is a no-op.

Deleting ``portfolio_showcase/`` must not raise ``ImportError``.
"""
from __future__ import annotations

import importlib.util
import logging
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable, MutableMapping

try:
    from ..settings import ENGINE_ROOT
except ImportError:  # pragma: no cover — standalone extraction
    ENGINE_ROOT = Path(__file__).resolve().parents[3]  # type: ignore[assignment]

_LOG = logging.getLogger("aiwake.event_bus")

Listener = Callable[[dict[str, Any]], None]

# Canonical event names. The room maps RoomEvent values onto these; the
# pipeline emits the pipeline-scoped ones directly.
ON_PIPELINE_START = "ON_PIPELINE_START"
ON_DEBATE_STARTED = "ON_DEBATE_STARTED"
ON_TURN_STARTED = "ON_TURN_STARTED"
ON_PROMPT_PREPARED = "ON_PROMPT_PREPARED"
ON_TURN_COMPLETE = "ON_TURN_COMPLETE"
ON_GUARDRAIL_TRIPPED = "ON_GUARDRAIL_TRIPPED"
ON_PROVIDER_ERROR = "ON_PROVIDER_ERROR"
ON_EXCHANGE_COMPLETE = "ON_EXCHANGE_COMPLETE"
ON_AUDIO_MIXED = "ON_AUDIO_MIXED"
ON_VIDEO_RENDERED = "ON_VIDEO_RENDERED"
ON_DEBATE_ENDED = "ON_DEBATE_ENDED"
ON_PIPELINE_FINISH = "ON_PIPELINE_FINISH"

_ROOM_EVENT_TO_BUS: dict[str, str] = {
    "debate_started": ON_DEBATE_STARTED,
    "turn_started": ON_TURN_STARTED,
    "prompt_prepared": ON_PROMPT_PREPARED,
    "utterance": ON_TURN_COMPLETE,
    "guardrail_tripped": ON_GUARDRAIL_TRIPPED,
    "provider_error": ON_PROVIDER_ERROR,
    "turn_completed": ON_EXCHANGE_COMPLETE,
    "audio_mixed": ON_AUDIO_MIXED,
    "video_rendered": ON_VIDEO_RENDERED,
    "debate_ended": ON_DEBATE_ENDED,
}

_PLUGIN_RELATIVE = Path("portfolio_showcase") / "core" / "plugin.py"


class EventBus:
    """In-process pub/sub. Listener exceptions never propagate to emitters."""

    def __init__(self) -> None:
        self._listeners: MutableMapping[str, list[Listener]] = defaultdict(list)

    def subscribe(self, event: str, callback: Listener) -> None:
        if callback not in self._listeners[event]:
            self._listeners[event].append(callback)

    def unsubscribe(self, event: str, callback: Listener) -> None:
        bucket = self._listeners.get(event)
        if not bucket:
            return
        try:
            bucket.remove(callback)
        except ValueError:
            return

    def emit(self, event: str, payload: dict[str, Any] | None = None) -> None:
        body = dict(payload or {})
        body.setdefault("event", event)
        for callback in tuple(self._listeners.get(event, ())):
            try:
                callback(body)
            except Exception:  # noqa: BLE001 — a broken plugin must not kill the engine
                _LOG.exception("event listener failed on %s", event)

    def clear(self) -> None:
        self._listeners.clear()

    @property
    def listener_count(self) -> int:
        return sum(len(bucket) for bucket in self._listeners.values())


bus = EventBus()
_plugin_attached = False


def subscribe(event: str, callback: Listener) -> None:
    bus.subscribe(event, callback)


def emit(event: str, payload: dict[str, Any] | None = None) -> None:
    bus.emit(event, payload)


def event_name_for(room_event: str) -> str:
    """Map a ``RoomEvent`` value onto the public bus name."""
    return _ROOM_EVENT_TO_BUS.get(room_event, room_event)


def reset() -> None:
    """Drop listeners and the plugin flag. Tests only."""
    global _plugin_attached
    bus.clear()
    _plugin_attached = False


def attach_optional_plugins(*, factory_root: Path | None = None) -> bool:
    """Load the showcase plugin when it is present on disk.

    Returns True when a plugin bound successfully. Missing files, import
    failures, and attach() exceptions all return False — the engine continues.
    """
    global _plugin_attached
    if _plugin_attached:
        return True
    root = Path(factory_root) if factory_root is not None else Path(ENGINE_ROOT)
    plugin_path = root / _PLUGIN_RELATIVE
    if not plugin_path.is_file():
        return False
    try:
        spec = importlib.util.spec_from_file_location(
            "_aiwake_optional_portfolio_plugin",
            plugin_path,
        )
        if spec is None or spec.loader is None:
            return False
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        attach = getattr(module, "attach", None)
        if not callable(attach):
            return False
        attach(bus)
    except Exception:  # noqa: BLE001
        _LOG.debug("optional portfolio plugin not attached", exc_info=True)
        sys.modules.pop("_aiwake_optional_portfolio_plugin", None)
        return False
    _plugin_attached = True
    return True


__all__ = [
    "EventBus",
    "ON_AUDIO_MIXED",
    "ON_DEBATE_ENDED",
    "ON_DEBATE_STARTED",
    "ON_EXCHANGE_COMPLETE",
    "ON_GUARDRAIL_TRIPPED",
    "ON_PIPELINE_FINISH",
    "ON_PIPELINE_START",
    "ON_PROMPT_PREPARED",
    "ON_PROVIDER_ERROR",
    "ON_TURN_COMPLETE",
    "ON_TURN_STARTED",
    "ON_VIDEO_RENDERED",
    "attach_optional_plugins",
    "bus",
    "emit",
    "event_name_for",
    "reset",
    "subscribe",
]

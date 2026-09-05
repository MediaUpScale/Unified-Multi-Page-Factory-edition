# -*- coding: utf-8 -*-
"""Aiwake — autonomous AI debate module.

An encapsulated content engine: two swappable language models interrogate each
other under hard output guardrails, a memory layer keeps the questions
escalating instead of looping, and the exchange is rendered as a dark terminal-UI
reel with audio-synced typing.

Public surface — everything else is an implementation detail::

    from channels_config.aiwake import run_pipeline
    result = run_pipeline(topic="Is grief a slow update?", turns=4)
    print(result.video_path)

Heavy media dependencies (MoviePy, Pillow) are imported lazily inside
:mod:`.pipeline`, so importing this package in a headless environment is cheap
and safe.
"""
from __future__ import annotations

from .contracts import DebateTranscript, SpeakerRole, Utterance
from .memory import DebateMemory
from .models import LLMFactory, LLMProvider
from .orchestrator import DebateResult, Provocateur
from .pipeline import BulkPipelineResult, PipelineResult, run_bulk_pipeline, run_pipeline
from .room import DebateObserver, DebateRoom, Participant, RoomEvent
from .settings import AiwakeSettings, load_settings

__version__ = "0.1.0"

__all__ = [
    "AiwakeSettings",
    "DebateMemory",
    "DebateObserver",
    "DebateResult",
    "DebateRoom",
    "DebateTranscript",
    "LLMFactory",
    "LLMProvider",
    "Participant",
    "BulkPipelineResult",
    "PipelineResult",
    "Provocateur",
    "RoomEvent",
    "SpeakerRole",
    "Utterance",
    "__version__",
    "load_settings",
    "run_bulk_pipeline",
    "run_pipeline",
]

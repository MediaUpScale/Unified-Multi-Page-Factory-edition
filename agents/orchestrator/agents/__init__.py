# -*- coding: utf-8 -*-
from __future__ import annotations

from agents.orchestrator.agents.theme_curator import ThemeCuratorAgent
from agents.orchestrator.agents.visual_director import VisualDirectorAgent
from agents.orchestrator.agents.visual_qa import VisualQAAgent

__all__ = [
    "ThemeCuratorAgent",
    "CopywriterAgent",
    "VisualDirectorAgent",
    "VisualQAAgent",
]


def __getattr__(name: str):
    if name == "CopywriterAgent":
        from agents.writer.copywriter import CopywriterAgent

        return CopywriterAgent
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

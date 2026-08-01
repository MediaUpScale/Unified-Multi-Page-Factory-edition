# -*- coding: utf-8 -*-
"""
VisualQA_Agent — modular generate → critique → auto-correct pipeline.

Public entrypoint: ``from VisualQA_Agent.agent_loop import run_visual_qa_loop``
"""
from __future__ import annotations

__version__ = "1.0.0"

__all__ = [
    "config",
    "channel_rag",
    "visual_critic",
    "image_generator",
    "agent_loop",
]

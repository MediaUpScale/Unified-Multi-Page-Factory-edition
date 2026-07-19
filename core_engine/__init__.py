# -*- coding: utf-8 -*-
"""
core_engine — framework-agnostic modules shared across all page profiles.

Every module here is page-agnostic: it accepts configuration as parameters
(or via the PageContext dataclass) rather than hard-wiring any page slug.
Adding a new page never requires editing this package.

Public surface
--------------
  CostTracker         — per-run financial tracking, writes estimated_cost telemetry.
  compile_sequence_reel — 4-image 80-second reel compiler.
  BasePageProfile     — structural Protocol that every page_config.py should satisfy.
"""
from __future__ import annotations

from core_engine.cost_tracker import CostTracker
from core_engine.reel_sequence_engine import compile_sequence_reel, build_sequence_script_prompt
from core_engine.page_profile import BasePageProfile

__all__ = [
    "CostTracker",
    "compile_sequence_reel",
    "build_sequence_script_prompt",
    "BasePageProfile",
]

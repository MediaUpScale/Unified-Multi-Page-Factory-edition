# -*- coding: utf-8 -*-
"""ECONOMIC_REEL_LOFI — isolated LOFI economic reel pipeline (does not touch ECONOMIC_REEL)."""
from __future__ import annotations

from core_engine.economic_reel_lofi.pipeline import run_economic_reel_lofi
from core_engine.economic_reel_lofi.test_preview import run_lofi_test_preview
from core_engine.post_type_registry import register_post_type

register_post_type("ECONOMIC_REEL_LOFI", run_economic_reel_lofi)

__all__ = ["run_economic_reel_lofi", "run_lofi_test_preview"]

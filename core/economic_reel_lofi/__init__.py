# -*- coding: utf-8 -*-
"""ECONOMIC_REEL_LOFI — isolated LOFI economic reel pipeline (does not touch ECONOMIC_REEL)."""
from __future__ import annotations

from core.post_type_registry import register_post_type

__all__ = ["run_economic_reel_lofi", "run_lofi_test_preview"]


def run_economic_reel_lofi(*args, **kwargs):
    from core.economic_reel_lofi.pipeline import run_economic_reel_lofi as _run

    return _run(*args, **kwargs)


def run_lofi_test_preview(*args, **kwargs):
    from core.economic_reel_lofi.test_preview import run_lofi_test_preview as _run

    return _run(*args, **kwargs)


register_post_type("ECONOMIC_REEL_LOFI", run_economic_reel_lofi)

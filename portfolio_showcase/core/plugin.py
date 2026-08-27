# -*- coding: utf-8 -*-
"""Optional entry point loaded by the Aiwake event bus via file path.

The engine never ``import``s this package. ``attach(bus)`` is invoked with the
engine's EventBus instance so this module does not need to import Aiwake.

Loaded two ways:
- as ``portfolio_showcase.core.plugin`` (tests, manual imports)
- via ``importlib.util.spec_from_file_location`` (engine plugin hook)
The second path has no package context, so the logger is loaded by sibling path.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

_LOGGER = None


def _load_logger_class() -> type:
    try:
        from .portfolio_logger import PortfolioLogger  # type: ignore[no-redef]

        return PortfolioLogger
    except ImportError:
        path = Path(__file__).resolve().with_name("portfolio_logger.py")
        name = "_portfolio_showcase_logger"
        cached = sys.modules.get(name)
        if cached is not None:
            return cached.PortfolioLogger
        spec = importlib.util.spec_from_file_location(name, path)
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot load portfolio logger from {path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module.PortfolioLogger


def attach(bus: Any) -> Any:
    """Subscribe the singleton logger to ``bus``. Idempotent."""
    global _LOGGER
    if _LOGGER is None:
        _LOGGER = _load_logger_class()()
    _LOGGER.bind(bus)
    return _LOGGER

# -*- coding: utf-8 -*-
"""
Post-type → pipeline runner registry.

ECONOMIC_REEL stays on the legacy produce() path (not registered here) so
behavior cannot regress. New early-dispatch post-types register a callable.
"""
from __future__ import annotations

from typing import Any, Callable

Runner = Callable[..., dict[str, Any]]

# Populated lazily / at import of each pipeline package.
POST_TYPE_REGISTRY: dict[str, Runner] = {}


def register_post_type(name: str, runner: Runner) -> None:
    key = str(name or "").strip().upper()
    if not key:
        raise ValueError("post-type name is empty")
    POST_TYPE_REGISTRY[key] = runner


def get_post_type_runner(name: str) -> Runner | None:
    return POST_TYPE_REGISTRY.get(str(name or "").strip().upper())


def registered_post_types() -> list[str]:
    return sorted(POST_TYPE_REGISTRY.keys())

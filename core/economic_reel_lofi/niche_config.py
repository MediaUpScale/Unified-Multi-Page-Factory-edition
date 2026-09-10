# -*- coding: utf-8 -*-
"""Active niche lookup for Stage 1 notes.

Presets (few-shot examples + aesthetic wrappers) live in ``niche_presets``.
This module remains the pipeline-facing stub so existing callers keep working.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from core.economic_reel_lofi.niche_presets import (
    get_niche_preset,
    normalize_niche_key,
    preset_notes,
)


@dataclass(frozen=True)
class NicheConfig:
    id: str
    label: str
    module: str
    audience: str
    voice_notes: str


def get_active_niche(module: str | None = None) -> NicheConfig:
    raw = str(os.environ.get("LOFI_NICHE") or "").strip().lower()
    key = normalize_niche_key(raw or module)
    preset = get_niche_preset(key)
    return NicheConfig(
        id=preset.key,
        label=preset.label,
        module=preset.key,
        audience=preset.label,
        voice_notes=preset.voice_notes,
    )


def niche_stage1_notes(module: str | None = None) -> dict[str, Any]:
    notes = preset_notes(get_niche_preset(module))
    notes.update(
        {
            "niche_id": get_active_niche(module).id,
            "niche_label": get_active_niche(module).label,
            "audience": get_active_niche(module).audience,
            "voice_notes": get_active_niche(module).voice_notes,
        }
    )
    return notes

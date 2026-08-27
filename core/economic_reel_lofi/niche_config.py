# -*- coding: utf-8 -*-
"""Swappable niche/theme-family stub.

Mirrors the visual-style module: Stage 1 may read ``get_active_niche()``
for audience/voice constraints. Only ``relationship_reflective`` is
implemented this round. Parenting and other niches are future work —
do not block pipeline restructure on them.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class NicheConfig:
    id: str
    label: str
    module: str
    audience: str
    voice_notes: str


RELATIONSHIP_REFLECTIVE = NicheConfig(
    id="relationship_reflective",
    label="reflective adult relationship",
    module="relationship",
    audience="adults in intimate relationships",
    voice_notes=(
        "First-person, concrete, unperformed. No parenting advice. "
        "No therapist-speak slogans."
    ),
)

PARENTING_STUB = NicheConfig(
    id="parenting",
    label="parenting (stub — not implemented this round)",
    module="parenting",
    audience="parents",
    voice_notes="",
)

_REGISTRY: dict[str, NicheConfig] = {
    RELATIONSHIP_REFLECTIVE.id: RELATIONSHIP_REFLECTIVE,
    "relationship": RELATIONSHIP_REFLECTIVE,
    PARENTING_STUB.id: PARENTING_STUB,
}


def get_active_niche(module: str | None = None) -> NicheConfig:
    raw = str(os.environ.get("LOFI_NICHE") or "").strip().lower()
    if raw and raw in _REGISTRY:
        return _REGISTRY[raw]
    mod = str(module or "relationship").strip().lower()
    if mod == "parenting":
        return PARENTING_STUB
    return RELATIONSHIP_REFLECTIVE


def niche_stage1_notes(module: str | None = None) -> dict[str, Any]:
    n = get_active_niche(module)
    return {
        "niche_id": n.id,
        "niche_label": n.label,
        "audience": n.audience,
        "voice_notes": n.voice_notes,
    }

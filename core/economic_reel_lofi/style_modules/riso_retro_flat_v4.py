# -*- coding: utf-8 -*-
"""riso_retro_flat_v4 — isolated visual-style module.

Swap this file (or ACTIVE_STYLE_ID) to change look, model, LoRA, CFG, and
warm_frac thresholds. Script generation, beat translation, and QA gate
*logic* must not import style-specific nouns from here except via
``get_active_style()``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class StyleConfig:
    """Everything a later LoRA/model swap should touch in one place."""

    id: str
    profile_name: str
    still_style_version: str
    model: str
    steps: int
    guidance_scale: float
    allow_lora: bool
    lora_id: str
    lora_trigger: str
    width: int
    height: int
    open: str
    technique: str
    mood: str
    format: str
    linework_guard: str
    exposure_guard: str
    # Structural safety only (anatomy / photoreal / nsfw / text).
    # Scene-object exclusions do not belong here.
    style_negative: str
    palettes: dict[str, str]
    palette_by_arc: dict[str, str]
    characters: dict[str, str]
    # Full-scene warm_frac. Macros (macro_no_setting) never use sunset labels.
    warm_sunset_doorway: float
    warm_indoor_lamp: float
    warm_sunset_disc: float
    macro_sunset_eligible: bool
    extra: dict[str, Any] = field(default_factory=dict)


STYLE = StyleConfig(
    id="riso_retro_flat_v4",
    profile_name="style-riso_painting_retro_vintage",
    still_style_version="riso_retro_flat_v4",
    model="black-forest-labs/FLUX.1-dev",
    steps=20,
    guidance_scale=5.5,
    allow_lora=False,
    lora_id="",
    lora_trigger="",
    width=720,
    height=1280,
    # Belonging 20260824_064214_v02 envelope. Rich gouache + vintage poster
    # is the approved look; unsigned/blank-corners stay out (those pull marks).
    open=(
        "A risograph print close up illustration in vintage poster style."
    ),
    technique=(
        "Bold flat gouache color blocks, fine hand-inked linework, and "
        "paper-grain halftone with hard-edged color shading. Paper grain. "
        "Visible canvas grain and heavy brush texture."
    ),
    mood="Evoking a quiet, sorrowful mood.",
    format="Illustration, vertical 9:16 composition.",
    linework_guard=(
        "fine black ink outlines, paper grain, hard-edged color planes"
    ),
    exposure_guard="even midtone exposure, hard-edged color planes",
    style_negative=(
        "deformed hands, extra fingers, fused fingers, bad anatomy, "
        "photorealistic, 3d render, cgi, dslr, bokeh, depth of field, "
        "nsfw, explicit, text, watermark, logo, letters, cursive, "
        "signage, signature, autograph, readable text"
    ),
    palettes={
        "WARM": (
            "Nostalgic warm color palette dominated by dusty rose, "
            "warm amber highlights, and soft olive green shadows."
        ),
        "COLD": (
            "Cold, desaturated color palette dominated by pale silver, "
            "moody charcoal grey, and soft ash white."
        ),
        "CONTRAST": (
            "Contrasting color palette dominated by warm amber highlights "
            "against cold indigo-violet shadows."
        ),
    },
    palette_by_arc={"act1": "WARM", "act2": "COLD", "act3": "CONTRAST"},
    characters={
        "woman": (
            "a beautiful, sad woman with dark hair and a [expression] "
            "expression on her face"
        ),
        "man": (
            "a quiet, sad man with dark hair and a [expression] "
            "expression on his face"
        ),
        "couple": (
            "a man and a woman, both dark-haired, standing close with "
            "[expression] expressions"
        ),
        "silhouette": (
            "a lone silhouetted figure, featureless against the light, "
            "in a [expression] posture"
        ),
    },
    warm_sunset_doorway=0.10,
    warm_indoor_lamp=0.04,
    warm_sunset_disc=0.12,
    macro_sunset_eligible=False,
)


def as_profile_dict(style: StyleConfig | None = None) -> dict[str, Any]:
    """Shape expected by assemble_v2_prompt_dev / get_visual_identity_profile."""
    s = style or STYLE
    return {
        "name": s.profile_name,
        "open": s.open,
        "technique": s.technique,
        "mood": s.mood,
        "format": s.format,
        "characters": dict(s.characters),
        "palettes": dict(s.palettes),
        "palette_by_arc": dict(s.palette_by_arc),
        "guards": {
            "saturation": "Keep colors saturated and printed.",
            "text_legibility": (
                "Every mark anywhere in this image is ink linework, "
                "halftone texture, or flat printed color. This includes "
                "skin, clothing, hair, background, paper, signs, screens, and "
                "all four corners of the frame. The illustration fills the "
                "frame edge to edge with scene content."
            ),
            "exposure": s.exposure_guard,
        },
        "lora_trigger": s.lora_trigger,
        "lora_id": s.lora_id,
        "style_id": s.id,
    }

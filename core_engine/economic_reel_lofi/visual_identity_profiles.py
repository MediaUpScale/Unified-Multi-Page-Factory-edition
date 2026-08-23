# -*- coding: utf-8 -*-
"""
Swappable visual-identity profiles for the FLUXDEV prompt path.

Schnell still reads assemble_v2_prompt + visual_identity_bank_v2.json.
This registry is Dev-only. One live profile today; LoRA fields are unused.
"""
from __future__ import annotations

import copy
from typing import Any

# Dev production default: locked risograph-on-Dev look (probe 20260821_233033).
# Distinct from style-surreal_retro (not registered — do not fold in).
DEFAULT_VISUAL_IDENTITY_PROFILE: str = "style-riso_painting_retro_vintage"

# lofi_risograph_v1 is closed for Schnell — do not edit that entry.
# style-riso_painting_retro_vintage is the locked Dev alias of that same
# open/technique/palette (risograph-on-Dev). lofi_painterly_v1 stays selectable.
_RISO_PAINTING_RETRO_NAMES: frozenset[str] = frozenset(
    {
        "style-riso_painting_retro_vintage",
        "lofi_risograph_v1",
    }
)


def is_riso_painting_retro_vintage(name: str | None) -> bool:
    """True for the locked Dev riso style and its Schnell-era source name."""
    return str(name or "").strip().lower() in _RISO_PAINTING_RETRO_NAMES


VISUAL_IDENTITY_PROFILES: dict[str, dict[str, Any]] = {
    "lofi_risograph_v1": {
        "open": "A risograph print close up illustration in vintage poster style.",
        "technique": (
            "Bold flat color blocks, fine hand-inked linework, and a grainy "
            "halftone texture with hard-edged color shading and no smooth gradients."
        ),
        "mood": "Evoking a quiet, sorrowful mood.",
        "format": "No photography, just illustration, vertical 9:16 composition.",
        "characters": {
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
        "palettes": {
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
        "palette_by_arc": {
            "act1": "WARM",
            "act2": "COLD",
            "act3": "CONTRAST",
        },
        "guards": {
            "saturation": (
                "Keep colors saturated and printed, not monochrome, not grayscale."
            ),
            "text_legibility": (
                "Every mark anywhere in this image is either ink linework, "
                "halftone texture, or flat printed color — never a legible "
                "letter, number, or word, anywhere in the frame. This includes "
                "skin, clothing, hair, background, paper, signs, screens, and "
                "all four corners of the frame. No watermark, no signature, "
                "no caption, no logo, no small stamped text tucked in a corner "
                "— the illustration fills the frame edge to edge with scene "
                "content only, nothing else."
            ),
            "exposure": (
                "even midtone exposure, no blown highlights, no white bloom "
                "or halo, no overexposed chest or collar, no vignette bleed"
            ),
        },
        # Extension point for a later LoRA phase. Unused while allow_lora=False.
        "lora_trigger": "",
        "lora_id": "",
    },
    "lofi_painterly_v1": {
        "open": (
            "A painted illustration on textured canvas, vintage print quality, "
            "soft brushwork, muted nostalgic color."
        ),
        "technique": (
            "Visible canvas and paper grain, soft brush texture, blended painted "
            "shadows, muted vintage-print finish. Painterly softness is the look."
        ),
        "mood": "Evoking a quiet, sorrowful mood.",
        "format": "No photography, painted illustration only, vertical 9:16 composition.",
        "characters": {
            "woman": (
                "a woman with long dark hair, everyday clothes, [expression] posture"
            ),
            "man": (
                "a quiet man with dark hair, everyday clothes, [expression] posture"
            ),
            "couple": (
                "a man and a woman, both dark-haired, in everyday clothes, "
                "[expression] postures, not posed for a camera"
            ),
            "silhouette": (
                "a lone silhouetted figure, featureless against the light, "
                "in a [expression] posture"
            ),
        },
        "palettes": {
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
        "palette_by_arc": {
            "act1": "WARM",
            "act2": "COLD",
            "act3": "CONTRAST",
        },
        "guards": {
            "saturation": (
                "Keep colors muted and painted, not monochrome, not grayscale."
            ),
            "text_legibility": (
                "No legible letter, number, word, watermark, signature, caption, "
                "or logo anywhere in the frame, including corners. Brush texture "
                "and canvas grain only — scene content fills the frame edge to edge."
            ),
            "exposure": (
                "even midtone exposure, no blown highlights, no white bloom "
                "or halo, no overexposed chest or collar, no vignette bleed"
            ),
        },
        "lora_trigger": "",
        "lora_id": "",
    },
}

# Locked Dev name — same blocks as lofi_risograph_v1. Nested dicts are
# deep-copied at lookup so later lock tweaks do not mutate the Schnell entry.
VISUAL_IDENTITY_PROFILES["style-riso_painting_retro_vintage"] = copy.deepcopy(
    VISUAL_IDENTITY_PROFILES["lofi_risograph_v1"]
)
VISUAL_IDENTITY_PROFILES["style-riso_painting_retro_vintage"]["technique"] = (
    "Bold flat gouache color blocks, fine hand-inked linework, and paper-grain "
    "halftone with hard-edged color shading. No smooth photographic gradients, "
    "no lens falloff, no cinematic bloom. Paper grain, not photo-grain."
)


def get_visual_identity_profile(
    name: str | None = None,
) -> dict[str, Any]:
    """Return a shallow-copied profile dict. Unknown names fall back to default."""
    key = str(name or DEFAULT_VISUAL_IDENTITY_PROFILE).strip() or (
        DEFAULT_VISUAL_IDENTITY_PROFILE
    )
    raw = VISUAL_IDENTITY_PROFILES.get(key)
    if raw is None:
        raw = VISUAL_IDENTITY_PROFILES.get(DEFAULT_VISUAL_IDENTITY_PROFILE) or (
            VISUAL_IDENTITY_PROFILES["lofi_risograph_v1"]
        )
    out = dict(raw)
    out["name"] = key
    return out

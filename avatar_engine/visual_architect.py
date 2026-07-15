# -*- coding: utf-8 -*-
"""
VisualArchitect -- cinematic image-prompt builder for the Unified Multi-Page Factory.

All scene libraries (environments, angles, actions, times-of-day) are loaded
exclusively from the active page's master_dna.json via the persona_dna module.
Zero hard-coding of keywords, environments, or themes here.

Avatar modes
------------
ON  (default) — builds a full portrait prompt anchored to the page persona's
                 physical description, environments, and actions.  A reference
                 image is subsequently passed to GeminiImageAdapter to lock
                 facial likeness.

OFF           — bypasses all human-subject elements and generates a purely
                 atmospheric / environmental prompt based on the page's
                 ATMOSPHERE_STYLE string.  No reference image is sent to the
                 image model; the output is high-fidelity thematic scenery.
"""
from __future__ import annotations

import random
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import config as app_config
from .persona_dna import (
    ANNA_ACTIONS,
    CAMERA_ANGLES,
    ENVIRONMENTS,
    LEGACY_RULE,
    TIMES_OF_DAY,
    visual_style_block,
)

_QUALITY_SUFFIX = (
    "Ultra-realistic. Shot on 35mm film or high-end smartphone candid aesthetic. "
    "No AI skin blur, no 'plastic' smoothing. Visible pores, natural fine lines, healthy inner glow. "
    "Physically accurate lighting, restrained colour grading. "
    "INDISTINGUISHABLE FROM A REAL PHOTOGRAPH."
)

_ATMOSPHERIC_QUALITY_SUFFIX = (
    "Ultra-realistic environmental photography. No human subjects present in frame. "
    "CRITICAL REQUIREMENT: The image must be BRIGHT, VIVID, and richly-lit — "
    "vibrant colours, clear visual identity, high tonal range. "
    "DO NOT produce dark, murky, underexposed, or low-contrast scenes. "
    "The background must be immediately identifiable as a high-quality photographic asset. "
    "Physically accurate lighting — no artificial glow, no HDR over-processing. "
    "Shot on medium-format film or high-end mirrorless. "
    "Restrained colour grading. INDISTINGUISHABLE FROM A REAL PHOTOGRAPH."
)

_CARTOON_QUALITY_SUFFIX = (
    "STYLE OVERRIDE — CARTOON / 2.5D ILLUSTRATION MODE:\n"
    "Modern 2.5D flat illustration. Vibrant stylized vector art. "
    "Clean, crisp edges with deliberate cel-shading. "
    "Volumetric lighting with bold colour blocking — no photorealistic textures. "
    "Clear foreground / midground / background layer separation for animation readiness. "
    "Lo-fi aesthetic with warm, saturated palette. "
    "Characters and props rendered as clean graphic assets, not photographs. "
    "Ideal for motion graphics, Reels, and animated post pipelines. "
    "DO NOT render photorealistic skin, hair, or environmental textures."
)


class VisualArchitect:
    """Translate topic briefs into high-variance, photoreal image prompts."""

    def build_prompt(
        self,
        topic_brief: str,
        *,
        avatar_mode: str = "ON",
        atmosphere_style: str | None = None,
        aspect_ratio: str | None = None,
        variation_index: int = 0,
        total_variants: int = 1,
        force_kid: bool | None = None,
        style: str = "NATURAL",
    ) -> str:
        """
        Build a cinematography-ready image prompt.

        Parameters
        ----------
        topic_brief:
            The topic / subject (e.g. 'Celtic Salt hydration', 'Cold plunge protocol').
        avatar_mode:
            'ON'  — full persona portrait prompt with physical description + actions.
            'OFF' — purely atmospheric/environmental prompt; no human subject.
        atmosphere_style:
            Overrides the default atmospheric style when avatar_mode='OFF'.
            Typically sourced from the active page's ATMOSPHERE_STYLE string.
            For SMART_BAIT: contains the hyper-literal scene derived from the
            engagement hook (e.g. 'dramatic fire silhouette' for an ex-on-fire hook).
        aspect_ratio:
            Override the config default (e.g. '3:4', '9:16').
        variation_index:
            Used to seed deterministic variation spread across a batch.
        total_variants:
            Total size of the batch (for variant labelling).
        force_kid:
            True/False override; None = probabilistic (per Legacy Rule probability).
            Ignored when avatar_mode='OFF'.
        style:
            'NATURAL' (default) — photorealistic cinematic output.
            'CARTOON' — Modern 2.5D flat illustration / stylized vector art.
                        Appends cartoon visual language directives.
        """
        rng = random.Random()  # fresh RNG each call — full randomness

        env = rng.choice(ENVIRONMENTS) if ENVIRONMENTS else {
            "name": "Wild Fields",
            "desc": "Open meadow, golden-hour back-light.",
        }
        angle = rng.choice(CAMERA_ANGLES) if CAMERA_ANGLES else "Medium close-up"
        time_of_day = rng.choice(TIMES_OF_DAY) if TIMES_OF_DAY else "Golden hour"
        ratio = aspect_ratio or app_config.GEMINI_IMAGE_ASPECT_RATIO

        variant_note = ""
        if total_variants > 1:
            variant_note = (
                f"\nCreative variant {variation_index + 1} of {total_variants}. "
                "Maximise compositional freshness while preserving thematic coherence."
            )

        use_cartoon = style.upper() == "CARTOON"
        style_suffix = _CARTOON_QUALITY_SUFFIX if use_cartoon else ""

        # ------------------------------------------------------------------
        # AVATAR OFF — purely atmospheric / environmental prompt
        # ------------------------------------------------------------------
        if avatar_mode == "OFF":
            atm_style = atmosphere_style or (
                "Cinematic environmental photography. Moody, high-fidelity. "
                "No human subjects."
            )
            quality_block = style_suffix if use_cartoon else _ATMOSPHERIC_QUALITY_SUFFIX
            return (
                f"CINEMATIC ENVIRONMENTAL PHOTOGRAPHY — NO HUMAN SUBJECTS\n\n"
                f"CONCEPT / THEME: {topic_brief.strip()}\n\n"
                f"HYPER-LITERAL VISUAL DIRECTIVE\n"
                f"(Treat as an EXACT scene description — translate every element from the\n"
                f"directive below directly into the frame.  Do NOT substitute generic\n"
                f"stock scenes.  If the directive says 'fire/embers', render fire/embers.\n"
                f"If it says 'tropical island beach', render a tropical island beach.):\n\n"
                f"{atm_style}\n\n"
                f"SCENE ENVIRONMENT: {env['name']}\n{env['desc']}\n\n"
                f"CAMERA APPROACH: {angle}\n\n"
                f"TIME OF DAY / LIGHTING: {time_of_day}\n\n"
                f"BRIGHTNESS MANDATE: Produce a vivid, well-lit image with rich colour "
                f"saturation — bright enough to pass automated quality filters on Facebook "
                f"and Instagram. Avoid uniformly dark or murky compositions.\n"
                f"AVOID: generic coffee cups, blank notebooks, empty office desks, "
                f"or any unrelated lifestyle props unless the theme explicitly calls for them.\n\n"
                f"Aspect ratio target: {ratio}\n"
                f"{variant_note}\n\n"
                f"{quality_block}"
            )

        # ------------------------------------------------------------------
        # AVATAR ON — full persona portrait prompt
        # ------------------------------------------------------------------
        action = rng.choice(ANNA_ACTIONS) if ANNA_ACTIONS else "standing quietly in the landscape"

        legacy_prob = LEGACY_RULE.get("Probability", 0.15)
        include_kid = force_kid if force_kid is not None else (rng.random() < legacy_prob)
        kid_desc = LEGACY_RULE.get(
            "Description",
            "A young grandchild (toddler, 2-4 years old) is present nearby.",
        )
        kid_line = f"\nLEGACY ELEMENT: {kid_desc}" if include_kid else ""

        visual = visual_style_block()
        quality_block = style_suffix if use_cartoon else _QUALITY_SUFFIX

        return (
            f"{visual}\n\n"
            f"TOPIC: {topic_brief.strip()}\n\n"
            f"ENVIRONMENT: {env['name']}\n{env['desc']}\n\n"
            f"CAMERA ANGLE: {angle}\n\n"
            f"TIME OF DAY / LIGHTING: {time_of_day}\n\n"
            f"SUBJECT ACTION: Subject is {action}\n"
            f"{kid_line}"
            f"{variant_note}\n\n"
            f"Aspect ratio target: {ratio}\n\n"
            f"{quality_block}"
        )

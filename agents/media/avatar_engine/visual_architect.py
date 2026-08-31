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
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import config as app_config
from agents.media.persona_dna import (
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


# ---------------------------------------------------------------------------
# anna_protocol — metaphor → concrete photography + style lock
# ---------------------------------------------------------------------------

_ANNA_STYLE_LOCK = (
    "Warm cinematic realistic botanical and nature photography. Earthy tones — "
    "sage green, terracotta, warm amber, cedar wood. Organic storytelling. "
    "Golden-hour window light or forest canopy. Shot on 35mm film. "
    "Ultra-realistic still life or environmental photograph. "
    "Kitchen apothecary, herbs, salt crystals, wooden boards, ceramic cups, "
    "morning dew, stone mortar — never a diagram and never a lab render."
)

_ANNA_NEGATIVE_BAN = (
    "STRICT BAN: no sci-fi elements, no glowing blue veins, no neon lighting, "
    "no floating anatomical diagrams, no medical cutaways, no x-ray bodies, "
    "no circuit boards, no electrical grids, no holograms, no futuristic UI, "
    "no HUD, no graphic elements, no infographics, no arrows, no icons, "
    "no text overlays, no letters, no words, no numbers, no slide labels, "
    "no captions, no watermarks, no typography of any kind on the image."
)

_METAPHOR_TO_CONCRETE: tuple[tuple[str, str], ...] = (
    (r"\belectrical grids?\b", "branching herb roots in dark soil"),
    (r"\belectric grids?\b", "branching herb roots in dark soil"),
    (r"\bcellular gates?\b", "dew-beaded leaf pores in morning light"),
    (r"\bcell gates?\b", "dew-beaded leaf pores in morning light"),
    (r"\badrenal ignitions?\b", "sunlit morning tea with warming herbs"),
    (r"\bignition\b", "steam rising from a ceramic cup in warm window light"),
    (r"\bglowing veins?\b", "leaf veins backlit by golden hour"),
    (r"\bneural|synapse|voltage|circuit|wiring\b", "vine tendrils along a stone wall"),
    (r"\bhologram|hud|neon\b", "amber glass bottles on a cedar shelf"),
    (r"\banatomical diagrams?\b", "hands sorting dried roots on linen"),
    (r"\bfuturistic ui\b", "a wooden apothecary table"),
)

_METAPHOR_TOKEN_RE = re.compile(
    r"\b(sci-?fi|neon|hologram|hud|circuit|voltage|ignition|electrical|"
    r"glowing veins?|floating diagram|anatomical|x-ray|cyber)\b",
    re.IGNORECASE,
)

_SLIDE_LABEL_RE = re.compile(
    r"\bSLIDE\s+\d+\s*[—\-–:].*?(?=\s{2,}|\(|$)",
    re.IGNORECASE,
)


def ground_image_concept(text: str, *, page_id: str = "") -> str:
    """Translate copy metaphors into a concrete photographic subject.

    Raw caption / hook language is never a safe image prompt — especially on
    anna_protocol, where 'electrical grid' and 'adrenal ignition' otherwise
    render as sci-fi anatomy. Returns a short scene string with those
    metaphors replaced and leftover sci-fi tokens stripped.
    """
    scene = _SLIDE_LABEL_RE.sub(" ", text or "")
    scene = re.sub(r"\s+", " ", scene).strip()
    if not scene:
        return ""
    if (page_id or "").strip().lower() == "anna_protocol":
        for pattern, concrete in _METAPHOR_TO_CONCRETE:
            scene = re.sub(pattern, concrete, scene, flags=re.IGNORECASE)
        scene = _METAPHOR_TOKEN_RE.sub("", scene)
        scene = re.sub(r"\s+", " ", scene).strip(" ,.;")
        if not scene:
            scene = "warm botanical still life: herbs, salt, and ceramic on wood"
    return scene


def anna_style_suffix() -> str:
    return f"{_ANNA_STYLE_LOCK} {_ANNA_NEGATIVE_BAN}"


def build_carousel_slide_prompt(
    *,
    topic: str,
    visual_subject: str,
    facet: str,
    slide_num: int,
    total: int,
    page_id: str = "",
    atmosphere_style: str = "",
) -> str:
    """Build a text-free, distinct carousel slide prompt.

    Slide titles / G-frame labels are never included — image models otherwise
    paint them onto the canvas.
    """
    pid = (page_id or "").strip().lower()
    grounded = ground_image_concept(
        visual_subject or topic, page_id=pid,
    ) or ground_image_concept(topic, page_id=pid)
    style = (atmosphere_style or "").strip()
    if pid == "anna_protocol":
        style = style or _ANNA_STYLE_LOCK
        ban = _ANNA_NEGATIVE_BAN
    else:
        ban = (
            "No text, no letters, no numbers, no slide labels, no captions, "
            "no watermarks, no typography on the image."
        )
    return (
        f"{style} "
        f"CONCRETE PHOTOGRAPHIC SCENE: {grounded}. "
        f"THIS FRAME'S ORIGINAL ANGLE: {facet} "
        f"Same theme as the carousel, completely different composition from every "
        f"other frame ({slide_num} of {total}). Do not zoom or crop another slide. "
        f"{ban} "
        f"Photoreal photograph only. Never render words or UI."
    )


class VisualArchitect:
    """Translate topic briefs into high-variance, photoreal image prompts."""

    def __init__(self, *, channel=None) -> None:
        from core.interfaces.factory import ChannelFactory

        self._channel = channel or ChannelFactory.from_env()

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

        Isolated channels (ancient_knowledge) skip Wild Fields / brightness
        mandate and return the adapter's native compose_image_prompt().
        """
        from core.interfaces.factory import ChannelFactory

        ch = self._channel
        if ChannelFactory.is_isolated(getattr(ch, "channel_id", "")):
            return ch.compose_image_prompt(topic_brief)

        page_id = str(getattr(ch, "channel_id", "") or "").strip().lower()
        topic_brief = ground_image_concept(topic_brief, page_id=page_id) or topic_brief

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
            if page_id == "anna_protocol":
                quality_block = f"{quality_block}\n{anna_style_suffix()}"
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
        if page_id == "anna_protocol":
            quality_block = f"{quality_block}\n{anna_style_suffix()}"

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

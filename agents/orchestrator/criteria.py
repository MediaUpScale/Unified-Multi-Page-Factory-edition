# -*- coding: utf-8 -*-
"""
Master visual criteria + prompt DNA for the Ancient Knowledge agentic pipeline.

Injected into ``VisualQAAgent`` as the judgement rubric and into
``VisualDirectorAgent`` as positive generation constraints.
"""
from __future__ import annotations

MAX_RETRIES: int = 1  # one fallback → r01 + r02 only (never r03)
QUALITY_THRESHOLD: float = 5.5
CHANNEL_ID: str = "ancient_knowledge"

# Hard cap: only truly corrupt / toy-CGI garbage is forced below this.
CGI_HARD_CAP: float = 4.0

MASTER_CRITERIA: tuple[str, ...] = (
    "Atmospheric cinematic lighting is preferred (dramatic source, dust/haze, "
    "contrast). Do NOT fail a clean high-atmosphere FLUX render solely for "
    "even lighting, digital polish, or missing 35mm film grain.",
    "Photoreal FLUX stills of ancient ruins, artefacts, and structures are "
    "valid — including fantasy or surreal interpretations. Do NOT require a "
    "recognisable real-world monument unless the generation prompt names one.",
    "Reject ONLY severe visual flaws: severe anatomical deformations, "
    "unreadable floating artifacts, or corrupt/broken renders. "
    "Illustration, sketch, cartoon, or unusable garbage still fail.",
)

STYLE_ANCHOR: str = (
    "Ultra-realistic cinematic documentary photograph. "
    "Iconic real-world ancient monument anchoring the frame. "
    "Dramatic single-source light, volumetric dust-haze beams, "
    "rim lighting on carved stone, deep chiaroscuro. "
    "35mm film grain, ochre and shadow-black colour grade. "
    "Impossible anomalous detail integrated naturally. "
    "Full-bleed immersive environment, no frames, no borders."
)

NEGATIVE_PROMPT: str = (
    "illustration, sketch, cartoon, graphite, pencil drawing, CGI, 3D render, "
    "video game, Unreal Engine, plastic skin, studio softbox, flat lighting, "
    "front-facing postcard, symmetrical tourist photo, modern clothing, "
    "smartphone, cars, text overlay, watermark, logo, picture frame, "
    "gallery mockup, white margin, image-within-image, "
    "archway, doorway, portal, window frame, silhouette frame, "
    "looking through a door, looking from inside to outside, dark foreground frame"
)

CAMERA_REWRITE_CYCLE: tuple[str, ...] = (
    "CAMERA REWRITE: wide aerial drone, 400 metres above, open sky, "
    "monument centred, never framed by architecture.",
    "CAMERA REWRITE: macro close-up of glowing alien inscriptions and "
    "impossible mechanical tooling filling the entire frame.",
    "CAMERA REWRITE: over-the-shoulder 1920s archaeologist examining a "
    "bizarre relic on a crate at the dig, desert horizon behind.",
    "CAMERA REWRITE: low-angle worm's-eye looking UP at megalithic blocks, "
    "dramatic sky, viewer at the base in open air — no interior frames.",
)

GEO_ANCHORS: tuple[str, ...] = (
    "Great Pyramid of Giza",
    "Baalbek Trilithon",
    "Göbekli Tepe",
    "Puma Punku",
    "Easter Island Moai",
    "Stonehenge",
    "Sacsayhuamán",
    "Nazca Lines",
    "Sphinx enclosure",
    "Yonaguni Monument",
)

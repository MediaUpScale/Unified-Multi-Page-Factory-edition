# -*- coding: utf-8 -*-
"""
Master visual criteria + prompt DNA for the Ancient Knowledge agentic pipeline.

Injected into ``VisualQAAgent`` as the judgement rubric and into
``VisualDirectorAgent`` as positive generation constraints.
"""
from __future__ import annotations

MAX_RETRIES: int = 3
QUALITY_THRESHOLD: float = 7.0
CHANNEL_ID: str = "ancient_knowledge"

# Hard cap: generic CGI / front-facing 3D look can never pass.
CGI_HARD_CAP: float = 4.0

MASTER_CRITERIA: tuple[str, ...] = (
    "Must have dynamic lighting — single dramatic source (warm amber torchlight "
    "or cold ethereal moonlight), volumetric shafts through dust/haze, strong "
    "rim light on megalithic stone edges, high-contrast chiaroscuro. "
    "Reject flat, even, or muddy lighting.",
    "Must not look like a generic front-facing 3D render — no clean CGI, "
    "no smooth plastic surfaces, no studio-lit video-game screenshot, "
    "no symmetrical dead-on monument postcard. Prefer oblique, aerial, "
    "low-angle, or first-person immersive camera.",
    "Must match the ancient mystery aesthetic — ultra-realistic cinematic "
    "historical photography of a recognisable real-world monument "
    "(Pyramid, Baalbek, Göbekli Tepe, Puma Punku, Easter Island, Stonehenge, "
    "Sacsayhuamán) with an anomalous/impossible detail woven in naturally. "
    "35mm documentary film grain, desaturated ochre and shadow-black grade. "
    "No illustration, sketch, cartoon, or modern elements.",
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

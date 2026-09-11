# -*- coding: utf-8 -*-
"""Modular niche presets for the single-pass LOFI writer + image wrapper.

Each niche owns its golden few-shot references and the Aesthetic Master
notes applied to writer-supplied visual concepts before Flux/Together. The
active style module owns the final rendering vocabulary.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


from core.economic_reel_lofi.style_modules.riso_retro_flat_v4 import (
    STYLE as RISO_STYLE,
)


RISO_PREFIX = f"{RISO_STYLE.open} {RISO_STYLE.technique}".strip()
RELATIONSHIP_LOCATION_ANCHOR = (
    "a story-specific cinematic environment chosen for this narrative"
)
PARENTING_LOCATION_ANCHOR = (
    "a story-specific lived-in environment shared by parent and child"
)


@dataclass(frozen=True)
class NichePreset:
    key: str
    label: str
    aesthetic_prefix: str
    negative_prompt: str
    few_shot_examples: tuple[str, ...]
    voice_notes: str
    visual_notes: str

    def few_shot_block(self) -> str:
        numbered = []
        for i, example in enumerate(self.few_shot_examples, start=1):
            numbered.append(f"EXAMPLE {i}:\n\"{example}\"")
        return (
            "GOLDEN FEW-SHOT REFERENCES — learn cadence, emotional depth, and "
            "hook energy. Do not copy their wording or force their structure:\n\n"
            + "\n\n".join(numbered)
        )


RELATIONSHIP = NichePreset(
    key="relationship",
    label="reflective adult relationship",
    aesthetic_prefix=RISO_PREFIX,
    negative_prompt=RISO_STYLE.style_negative,
    few_shot_examples=(
        "Once Kafka said, what is love? After all, it is quite simple. Love is "
        "everything which enhances, widens and enriches our life. In its heights "
        "and in its depths, it makes every moment of life worth living.",
        "Let people lose you. Let them misunderstand you. Let them create their "
        "own stories. Don't rush to fix them. Let time answer what you never "
        "needed to explain. Every truth reveals itself when the right time comes.",
        "Never ask a liar why they lied. To explain it, they would have to lie "
        "again. One lie is never born alone. It always needs another to keep "
        "alive, then another to protect the last, until even they forget where "
        "the truth ended and the lie becomes the only story they remember.",
        "If someone values you, they make time for you. Genuine love is easy to "
        "recognize because it shows up in simple ways. It's a text to check in, "
        "a callback when they said they would, a plan that actually happens. "
        "Even on busy days, they still find a moment because you matter to them, "
        "because they want to stay connected.",
        "I kept my words to myself, but I saw everything and noticed everything. "
        "When I stay quiet, it doesn't mean I'm clueless. Not everything needs "
        "an immediate response, and sometimes the best approach is to let people "
        "reveal themselves over time.",
        "Once the rain is over, an umbrella becomes a burden to everyone. The "
        "day a blind man sees, the first thing he throws away is the stick that "
        "helped him all his life. When the sun returns, the first thing people "
        "extinguish is the candle that guided them through the dark. That's how "
        "loyalty ends. When benefits stop.",
    ),
    voice_notes=(
        "First-person, concrete, unperformed. No parenting advice. "
        "No therapist-speak slogans."
    ),
    visual_notes=(
        "Atmospheric painterly risograph, golden rim-lit silhouettes, gouache "
        "depth, paper tooth, and fine ink. Never photoreal or flat vector. "
        "Footwear or cropped feet. Mood-match the spoken beat."
    ),
)

PARENTING = NichePreset(
    key="parenting",
    label="parenting presence and time",
    aesthetic_prefix=RISO_PREFIX,
    negative_prompt=RISO_STYLE.style_negative,
    few_shot_examples=(
        "They won't remember how clean the house was. They will remember if "
        "you were present when they looked up.",
        "One day you will put your child down and never pick them up again. "
        "Honor the moments that slip away quietly.",
    ),
    voice_notes=(
        "Intimate, present-tense observation of childhood time. No lectures, "
        "no productivity tips, no shame. Write from inside the ache of "
        "presence and the moments that disappear."
    ),
    visual_notes=(
        "Warm domestic twilight, parent-and-child silhouettes, wooden toys, "
        "worn shoes, painterly risograph gouache, paper grain, golden rim light. "
        "Never photoreal or flat vector."
    ),
)

_REGISTRY: dict[str, NichePreset] = {
    RELATIONSHIP.key: RELATIONSHIP,
    PARENTING.key: PARENTING,
}

_LOCATION_TERMS = (
    "kitchen",
    "hallway",
    "street",
    "train",
    "station",
    "bedroom",
    "living room",
    "park",
    "diner",
    "booth",
    "table",
    "window",
    "apartment",
    "office",
    "platform",
    "sidewalk",
    "cafe",
    "beach",
    "forest",
)
_UNSAFE_HUMAN_TERMS = (
    "front-facing",
    "front facing",
    "direct eye contact",
    "looking at camera",
    "smiling mouth",
    "speaking mouth",
)
_CAMERA_ARC = (
    "wide establishing angle",
    "medium side angle",
    "over-the-shoulder angle",
    "macro object detail",
    "low angle",
    "soft-focus silhouette",
    "high angle",
    "quiet closing detail",
)


def normalize_niche_key(value: str | None) -> str:
    raw = str(value or "").strip().lower()
    if raw in {"relationship_reflective", "relationship"}:
        return "relationship"
    if raw in {"parenting", "momma_circle"}:
        return "parenting"
    return "relationship"


def get_niche_preset(key: str | None = None) -> NichePreset:
    return _REGISTRY[normalize_niche_key(key)]


def wrap_visual_prompt(
    visual_concept: str,
    niche: str | NichePreset | None = None,
) -> str:
    """Compatibility wrapper using scene-one's active Risograph palette."""
    del niche
    return build_flux_prompt(visual_concept, 1)[0]


def build_flux_prompt(
    beat_visual_concept: str,
    scene_idx: int,
) -> tuple[str, str]:
    """Build the complete Flux prompt from the active style and 3-act palette."""
    scene = max(1, int(scene_idx))
    palette_key = "WARM" if scene <= 3 else ("COLD" if scene <= 6 else "CONTRAST")
    concept = " ".join(str(beat_visual_concept or "").split()).rstrip(".,; ")
    positive = (
        f"{RISO_STYLE.open} {RISO_STYLE.technique} "
        f"{RISO_STYLE.palettes[palette_key]} {concept}. "
        f"{RISO_STYLE.mood} {RISO_STYLE.linework_guard}, {RISO_STYLE.format}"
    )
    return " ".join(positive.split()), RISO_STYLE.style_negative


def anchor_visual_concept(
    location_anchor: str,
    visual_concept: str,
    *,
    scene: int = 1,
) -> str:
    """Attach the writer's story world without erasing its scene staging."""
    anchor = " ".join(str(location_anchor or "").split()).rstrip(".,; ")
    concept = " ".join(str(visual_concept or "").split()).rstrip(".,; ")
    if not anchor:
        raise ValueError("single-pass writer returned no location_anchor")
    if not concept:
        raise ValueError("single-pass writer returned an empty visual_concept")
    concept_low = concept.lower()
    unsafe_human = any(term in concept_low for term in _UNSAFE_HUMAN_TERMS)
    if unsafe_human:
        angle = _CAMERA_ARC[(max(1, int(scene)) - 1) % len(_CAMERA_ARC)]
        concept = (
            f"{angle}, natural side profile or silhouette, changing light across "
            "tactile surfaces in the declared environment"
        )
    if concept.lower().startswith(anchor.lower()):
        return concept
    return f"{anchor}. {concept}"


def inject_prompt_fields(
    script: dict[str, Any],
    niche: str | NichePreset | None = None,
) -> dict[str, Any]:
    """Add full Flux fields without another model call or output-token cost."""
    preset = niche if isinstance(niche, NichePreset) else get_niche_preset(
        str(niche or script.get("niche") or script.get("module") or "relationship")
    )
    fallback_anchor = (
        PARENTING_LOCATION_ANCHOR
        if preset.key == "parenting"
        else RELATIONSHIP_LOCATION_ANCHOR
    )
    anchor = str(script.get("location_anchor") or fallback_anchor).strip()
    script["location_anchor"] = anchor
    rows = [row for row in (script.get("lines") or []) if isinstance(row, dict)]
    for i, row in enumerate(rows, start=1):
        anchored = anchor_visual_concept(
            anchor,
            str(row.get("visual_concept") or ""),
            scene=int(row.get("scene") or i),
        )
        row["visual_concept"] = anchored
        positive, negative = build_flux_prompt(anchored, int(row.get("scene") or i))
        row["final_positive_prompt"] = positive
        row["negative_prompt"] = negative
    script["lines"] = rows
    script["niche"] = preset.key
    script["aesthetic_wrapper"] = preset.aesthetic_prefix
    return script


def writer_visual_clause(preset: NichePreset) -> str:
    participants = (
        "Keep the parent-child relationship legible through natural shared action."
        if preset.key == "parenting"
        else "Keep recurring people visually coherent without forcing one protagonist."
    )
    return (
        "VISUAL STAGING — Choose a fresh, story-specific environment; do not default "
        "to a doorway, giant sun, isolated cup, hallway, or sunset alley. Strong "
        "options include a midnight diner, rainy subway platform, artist studio at "
        "3 AM, misty coastal overlook, old library aisle, laundromat, ferry deck, "
        "or high-rise balcony, but invent others when the story asks for them. "
        "The location_anchor names the coherent story world, not a mandatory prop. "
        "Across eight beats, vary wide establishing shots, moody medium profiles, "
        "over-the-shoulder views, evocative silhouettes, tactile environmental "
        "details, and a wide atmospheric resolution. Every prop must belong in its "
        "real context: cups on tables or counters, bags on racks or seats, books on "
        "desks or shelves. Never scatter symbolic objects on floors or thresholds. "
        f"{participants} Render as {preset.visual_notes} No front-facing portrait, "
        "direct eye contact, visible speaking mouth, photorealism, or flat vector. "
        "Do not include the style prefix; the pipeline adds it."
    )


def writer_system_extras(preset: NichePreset) -> str:
    return f"{preset.few_shot_block()}\n\n{writer_visual_clause(preset)}"


def preset_notes(preset: NichePreset) -> dict[str, Any]:
    return {
        "niche_id": preset.key,
        "niche_label": preset.label,
        "audience": preset.label,
        "voice_notes": preset.voice_notes,
        "aesthetic_prefix": preset.aesthetic_prefix,
        "negative_prompt": preset.negative_prompt,
    }

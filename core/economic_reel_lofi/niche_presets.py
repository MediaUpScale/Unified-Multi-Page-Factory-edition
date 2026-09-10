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
    "a vintage warm-paper world of interiors, doorways, and dusk streets"
)
_RELATIONSHIP_CHARACTER_ARC = (
    "Warm intimate interior: a dark-haired woman sitting on a bed or beside a sunlit window, golden hour dust motes, deep shadows",
    "The Doorway Motif: a striking silhouette standing in an open doorway looking out at a massive warm glowing sunset disc",
    "The Graphic Minimalist Prop: a single isolated symbolic object on textured cream amber paper ground, vintage steaming kettle or folded envelope or lone suitcase or ceramic teacups",
    "Emotional close-up: delicate profile of the dark-haired woman, soft sorrowful eyes, fine ink cross-hatching, wind in her hair",
    "Atmospheric hallway or domestic interior with a glowing doorway, warm floorboards, and long evening shadows",
    "Contemplative exterior: silhouette of a figure under an umbrella or leaning on a balcony looking at rain falling through an amber streetlamp beam",
    "Wide cinematic setting: wet street corner, solitary train tracks at dusk, or rain streaking down a quiet window pane",
    "Emotional resolution: a couple walking together down a warm narrow alley at sunset with bags, or a silhouette stepping forward into the morning sun",
)
PARENTING_LOCATION_ANCHOR = "a cozy child's bedroom doorway at twilight"
_PARENTING_CHARACTER_ARC = (
    "Wide warm hook illustration of a dark-haired parent beside the window, young child nearby",
    "Medium shot of the dark-haired parent folding a small blanket beside the young child",
    "Atmospheric detail of a wooden toy under a warm lamp, the parent's hand and child nearby",
    "Silhouette of the dark-haired parent watching the young child sleep peacefully",
    "Over-the-shoulder view of the parent looking into the night while the child rests nearby",
    "Stylized risograph side profile of the loving weary dark-haired parent beside the child",
    "Silhouette of the dark-haired parent gently holding the young child's small hand",
    "Peaceful dawn light around parent and child, quiet presence and gratitude",
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
        "Rain, quiet distance between two people, gouache blocks, halftone, and "
        "fine ink contours. Mood-match the spoken beat without literalizing it."
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
        "Warm domestic light, small hands, wooden toys, a bedroom doorway, "
        "nostalgic Risograph gouache, paper grain, and fine ink linework."
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
    """Attach the immutable location programmatically to a relative shot."""
    anchor = " ".join(str(location_anchor or "").split()).rstrip(".,; ")
    concept = " ".join(str(visual_concept or "").split()).rstrip(".,; ")
    if not anchor:
        raise ValueError("single-pass writer returned no location_anchor")
    if not concept:
        raise ValueError("single-pass writer returned an empty visual_concept")
    anchor_low = anchor.lower()
    concept_low = concept.lower()
    drifted = any(
        term in concept_low and term not in anchor_low for term in _LOCATION_TERMS
    )
    unsafe_human = any(term in concept_low for term in _UNSAFE_HUMAN_TERMS)
    if drifted or unsafe_human:
        angle = _CAMERA_ARC[(max(1, int(scene)) - 1) % len(_CAMERA_ARC)]
        concept = (
            f"{angle}, anonymous silhouette or inanimate detail, changing light "
            "across tactile surfaces"
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
    if preset.key == "relationship":
        script["location_anchor"] = RELATIONSHIP_LOCATION_ANCHOR
    elif preset.key == "parenting":
        script["location_anchor"] = PARENTING_LOCATION_ANCHOR
    anchor = str(script.get("location_anchor") or "").strip()
    rows = [row for row in (script.get("lines") or []) if isinstance(row, dict)]
    for i, row in enumerate(rows, start=1):
        if preset.key == "relationship":
            concept = " ".join(str(row.get("visual_concept") or "").split())
            anchored = (
                f"{anchor}. {_RELATIONSHIP_CHARACTER_ARC[(i - 1) % 8]}. "
                f"{concept}"
            ).rstrip(". ")
        else:
            anchored = anchor_visual_concept(
                anchor,
                str(row.get("visual_concept") or ""),
                scene=int(row.get("scene") or i),
            )
        if preset.key == "parenting":
            anchored = (
                f"{anchor}. {_PARENTING_CHARACTER_ARC[(i - 1) % 8]}. "
                f"{anchored.removeprefix(anchor).lstrip('. ')}"
            ).rstrip(". ")
        row["visual_concept"] = anchored
        positive, negative = build_flux_prompt(anchored, int(row.get("scene") or i))
        row["final_positive_prompt"] = positive
        row["negative_prompt"] = negative
    script["lines"] = rows
    script["niche"] = preset.key
    script["aesthetic_wrapper"] = preset.aesthetic_prefix
    return script


def writer_visual_clause(preset: NichePreset) -> str:
    if preset.key == "relationship":
        return (
            "VISUAL DIRECTION — poetic vintage risograph print art with flat "
            "gouache, paper grain, and warm nostalgic tones. Set location_anchor "
            f"exactly to: \"{RELATIONSHIP_LOCATION_ANCHOR}\". Preserve emotional "
            "continuity across this proven 8-beat formula: 1 hook, warm intimate "
            "interior, dark-haired woman on a bed or beside a sunlit window, "
            "golden hour dust motes, deep shadows; 2 the Doorway Motif, a striking "
            "silhouette in an open doorway looking out at a massive warm glowing "
            "sunset disc; 3 the Graphic Minimalist Prop, one isolated symbolic "
            "object on textured cream/amber paper (steaming kettle, folded "
            "envelope, lone suitcase, or ceramic teacups); 4 emotional close-up, "
            "delicate profile of the dark-haired woman, soft sorrowful eyes, fine "
            "ink cross-hatching, wind in her hair; 5 atmospheric hallway or "
            "domestic interior with a glowing doorway, warm floorboards, long "
            "evening shadows; 6 contemplative exterior, silhouette under an "
            "umbrella or on a balcony watching rain through an amber streetlamp "
            "beam; 7 wide cinematic wet street corner, solitary train tracks at "
            "dusk, or rain on a quiet window pane; 8 emotional resolution, a "
            "couple walking down a warm narrow alley at sunset with bags, or a "
            "silhouette stepping forward into the morning sun. Matte gouache, "
            "cream, terracotta, and amber — never neon, glossy anime, or "
            "searchlights. No front-facing portrait, direct eye contact, or "
            "photorealism. Do not include the style prefix; the pipeline adds it."
        )
    if preset.key == "parenting":
        return (
            "VISUAL DIRECTION — illustrated Risograph parenting micro-drama. Set "
            f"location_anchor exactly to: \"{PARENTING_LOCATION_ANCHOR}\". Every "
            "scene stays there. A dark-haired parent and young child embody the "
            "fleeting passage of time. Use this framing arc in order: 1 wide warm "
            "hook of parent at the window at dusk, child nearby; 2 medium parent "
            "folding a small blanket or holding a warm cup; 3 wooden toy under a "
            "warm lamp with parent and child still grounded nearby; 4 parent "
            "silhouette watching the child sleep; 5 over-the-shoulder toward the "
            "night, feeling time pass; 6 loving, weary risograph parent profile; "
            "7 parent holding the child's small hand; 8 peaceful dawn light, "
            "presence and gratitude. Use gouache blocks, paper grain, halftone, "
            "and fine ink linework. No front-facing portrait, direct eye contact, "
            "photorealism, or sterile empty-room B-roll. Do not include the style "
            "prefix; the pipeline adds it."
        )
    return (
        "VISUAL CONCEPTS: choose one specific physical location_anchor first. "
        "Every visual_concept is a RELATIVE shot inside or directly facing that "
        "exact anchor; never name or imply another place. Progress only through "
        "camera angle, light, texture, and micro-actions. Keep each concept "
        "10–16 words to control latency. "
        f"Niche look: {preset.visual_notes} "
        "Allowed people: hands, silhouettes, over-the-shoulder, back-of-head, "
        "soft-focus profiles. Never request front portraits, direct eye contact, "
        "or visible smiling/speaking mouths. Do not include the aesthetic prefix; "
        "the pipeline wraps it programmatically."
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

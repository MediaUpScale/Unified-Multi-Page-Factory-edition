# -*- coding: utf-8 -*-
"""
Visual identity v2 — assemble Flux prompts from locked blocks + beat fields.

Live riso_prompt_library_v2.json is never overwritten. This bank is parallel
until a full render is approved.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from core.economic_reel_lofi import config as lofi_cfg

SUBJECT_TYPES: frozenset[str] = frozenset(
    {"woman", "man", "couple", "silhouette", "object_focus"}
)
ARC_ACTS: tuple[str, ...] = ("act1", "act2", "act3")

# Hands-free Schnell bank: no small object in a hand near the face.
# Distinct stems (cap = 1 per episode). Scenery / empty-handed figures only.
DEFAULT_SETTING_OBJECT_PAIRS: tuple[dict[str, str], ...] = (
    {"setting": "bedroom, unmade empty bed", "key_object": "unmade empty bed"},
    {"setting": "rain-streaked window at dusk", "key_object": "rain on the window"},
    {"setting": "empty train platform at night", "key_object": "empty platform"},
    {"setting": "hallway with a closed suitcase on the floor", "key_object": "closed suitcase on the floor"},
    {"setting": "empty park path at dusk", "key_object": "distant horizon"},
    {"setting": "coat hook by the door", "key_object": "two coats hanging"},
    {"setting": "window overlooking a quiet street", "key_object": "empty chair"},
    {"setting": "hallway near an open door", "key_object": "open doorway"},
    {"setting": "street corner under a streetlamp", "key_object": "streetlamp"},
    {"setting": "parked car at evening, door open", "key_object": "empty passenger seat of a car"},
    {"setting": "empty closed room, blank wall", "key_object": "blank wall"},
    {"setting": "sofa in evening light", "key_object": "folded blanket"},
)

_CONCRETE_NOUN_RE = re.compile(
    r"\b("
    r"mugs?|cups?|coats?|doors?|beds?|chairs?|phones?|letters?|tables?|"
    r"sinks?|windows?|benches?|shoes?|keys?|lamps?|kitchen|bedrooms?|"
    r"hallways?|streets?|bags?|photos?|photographs?|sweaters?|fridges?|"
    r"umbrellas?|suitcases?|tickets?|clocks?|tea|journals?|mirrors?|"
    r"stoops?|porch(?:es)?|buses|bus|pillows?|hooks?|drawers?|closets?|"
    r"faucets?|counters?|sofas?|blankets?|envelopes?|lists?|radios?|"
    r"ashtrays?|kettles?|notebooks?|calendars?|papers?|plants?|stones?|"
    r"boxes|box|drinks?|pass(?:es)?|seats?|hangers?|sleeves?|hands?|"
    r"laces?|plates?|face|faces|water|numbers?|voicemails?|towels?|"
    r"bills?|locks?|bowls?|mail|piles?|doorways?|"
    r"sky|alleys?|rooftops?|buildings?|"
    r"cribs?|sippy|bears?|crayons?|washcloths?|rugs?|timers?|"
    r"backpacks?|storybooks?|nightstands?|snacks?|crumbs?|toys?|"
    r"bottles?|cushions?|remotes?|tins?|drawings?|nightlights?|"
    r"dishes|curtains?|couches?"
    r")\b",
    re.IGNORECASE,
)

_CONCRETE_ACTION_RE = re.compile(
    r"\b(wait|waits|waited|waiting|sit|sits|sat|sitting|leave|leaves|left|"
    r"set|sets|hang|hangs|hung|hold|holds|held|open|opens|opened|close|"
    r"closes|closed|pour|pours|poured|rinse|rinses|rinsed|walk|walks|walked|"
    r"tie|ties|tied|grab|grabs|grabbed|splash|splashes|splashed|put|puts|"
    r"pick|picks|picked|fold|folds|folded|refill|refilled|drop|drops|dropped|"
    r"collect|collected|save|saved|call|calls|called|stand|stands|stood|"
    r"stay|stays|stayed|turn|turns|turned|look|looks|looked|reach|reaches|"
    r"reached|keep|keeps|kept|show|showed|come|came|go|went|bring|brought|"
    r"carry|carried|touch|touched|wash|washed|fill|filled|empty|emptied|"
    r"lock|locked|unlock|unlocked|press|pressed|lift|lifted|place|placed|"
    r"wear|wore|worn|pull|pulled|start|started|switch|switched|click|"
    r"clicked|watch|watched|slide|slid|spread|spreaded|drape|draped|"
    r"pouring|tying|holding|leaving|opening|closing)\b",
    re.IGNORECASE,
)

_PACKAGE_DIR = Path(__file__).resolve().parent
_DEFAULT_BANK = _PACKAGE_DIR / "store" / "visual_identity_bank_v2.json"
_CHARACTER_SHEETS = _PACKAGE_DIR / "store" / "character_identity_sheets.json"


def load_character_identity_sheets() -> dict[str, Any]:
    """Persistent WOMAN/MAN reference sheets — not regenerated per theme."""
    try:
        raw = json.loads(_CHARACTER_SHEETS.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raw = {}
    return raw if isinstance(raw, dict) else {}


def character_sheet_prompt(who: str) -> str:
    sheets = load_character_identity_sheets()
    key = "man" if str(who or "").strip().lower() == "man" else "woman"
    row = sheets.get(key) if isinstance(sheets.get(key), dict) else {}
    block = str(row.get("prompt_block") or "").strip()
    if block:
        return block
    if key == "man":
        return (
            "the same recurring man: short-to-medium dark hair, slightly "
            "disheveled; taller, broad-shouldered, slightly hunched; light "
            "stubble when the face is visible; dark cool-neutral clothes"
        )
    return (
        "the same recurring woman: long near-black slightly wavy hair, "
        "loose or half-up; slim; dark or hazel-green eyes and a defined brow; "
        "earth-tone sweater, midi dress, or long coat, solid color"
    )


def portrait_close_identity_clause(who: str) -> str:
    """Painterly head-and-shoulders portrait — locked appearance, not paraphrased."""
    sheet = character_sheet_prompt(who)
    return (
        f"Medium close-up portrait of {sheet}, head and shoulders in frame, "
        "full face visible — eyes, nose, mouth, and some hair. "
        "High-neck sweater covering throat, collarbones, shoulders, and chest "
        "to the jaw."
    )


def face_object_scale_clause(obj: str, who: str = "woman") -> str:
    """Face+object beats need explicit relative scale or Flux oversized-floats the prop."""
    noun = str(obj or "").strip() or "the object"
    noun = re.sub(r"^(a|an|the)\s+", "", noun, flags=re.I)
    poss = "her" if str(who).strip().lower() != "man" else "his"
    return (
        f"The {noun} is smaller than {poss} face, held low at one side of the "
        f"frame, occupying a corner of the shot, clearly secondary to the face."
    )


def hand_object_scale_clause(obj: str) -> str:
    noun = str(obj or "").strip() or "the object"
    noun = re.sub(r"^(a|an|the)\s+", "", noun, flags=re.I)
    return (
        f"The {noun} is smaller than the hand, held to one side of the frame, "
        f"occupying a corner of the shot, clearly secondary to the grip."
    )


# Pre-v4 object_focus language. "2-3 tone gradient" was read as a hard
# vertical color-block split; atmosphere now matches working silhouette
# beats (doorway / window / backlit sky, light and shade).
_OBJECT_FOCUS_ENV_TEXTURE_EXACT = (
    "The object sits in a real environment with paper grain, color gradient, "
    "and canvas texture — not a woodcut, not a flat ink diagram on blank paper, "
    "not isolated linework."
)
_OBJECT_FOCUS_ENV_EXTRAS = (
    "Same flat gouache risograph atmosphere as a furnished room — paper tooth, "
    "hard color blocks, printed dusk. Walls, furniture, fabric, floor, and "
    "window light are painted as flat gouache color blocks with hard printed "
    "edges and visible paper grain."
)
_OBJECT_FOCUS_TEXTURE_SETTING = (
    "furnished room, paper tooth, printed dusk"
)
_PERSEVERANCE_FIGURE_ENV = (
    "Printed atmosphere: visible paper tooth and a slight riso color offset. "
    "The surrounding environment — sky, foliage, room, fabric, furniture — "
    "is painted as flat gouache color blocks with hard printed edges and "
    "visible paper grain. Only the human figure is a silhouette; the room "
    "stays readable as poster shapes."
)
_HOOK_SOLID_SHAPE_EXACT = (
    "solid flat opaque silhouette, no interior linework, no visible "
    "individual hair strands, no visible ear detail — one unbroken flat shape."
)
_HOOK_NO_SKIN_EXACT = "flat opaque black with no skin"
_HOOK_DOUBLE_EXPOSURE_MEDIUM = (
    "A richly detailed hand-painted illustration in vintage poster style, "
    "visible canvas grain and heavy brush texture, muted sepia-and-teal duotone."
)
_PORTRAIT_SKIN_LIGHT_EXACT = (
    "Two distinct light sources: a warm tungsten amber lamp on one side of "
    "the face, a cool blue teal ambient source on the other. The skin itself "
    "shifts hue — amber skin on one side of the nose, jaw, and cheek, teal "
    "skin on the other — a color-temperature change, not only lighter and darker."
)
_PORTRAIT_FLAT_SKIN_EXACT = (
    "Flat illustration skin: hard-edged color planes, ink outline, visible "
    "paper grain, no photographic specular highlights, no pore detail, "
    "no individual hair-strand detail."
)
_FLAT_OBJECT_SURFACE = (
    "Ceramic as flat color-plane shading with visible paper grain and ink "
    "outline, a painted reflection, hard-edged color."
)
_FLAT_SPECULAR_HIGHLIGHT = (
    "One flat hard-edged highlight shape on the mug surface, no glossy "
    "gradient, no photographic reflection blur."
)
_GOLDEN_HOUR_SAT = (
    "Saturated golden-hour warmth: dusty rose, rich amber, olive shadow. "
    "Full color saturation. Light and shade stay; color stays rich."
)
_HOOK_SOLID_NEGATIVE = (
    "rendered skin, facial features, glossy portrait"
)
# Keep "illustration" — last test bundled it with print/poster and killed
# the flat-illustration identity. Isolate print/poster only.
_PAINT_MEDIUM_OPEN = (
    "An illustration, flat hard-edged color planes, ink outline, visible paper grain."
)
_PAINT_MEDIUM_TECH = (
    "Illustration, flat hard-edged color planes, ink outline, visible paper grain."
)
_PAINT_MEDIUM_FMT = "Illustration, vertical 9:16 composition."
_ARTWORK_MEDIUM_WORD_RE = re.compile(
    r"\b(?:prints?|printed|printing|posters?)\b",
    re.I,
)
_OBJECT_SHOT_VARIETY: dict[str, str] = {
    "held_steam": (
        "Held in one hand, steam rising, three-quarter side view."
    ),
    "rest_front": (
        "Resting, front three-quarter view, quiet."
    ),
    "rest_high_steam": (
        "Resting, high angle looking down into the open rim, faint steam rising."
    ),
    "rest_low_reflect": (
        "Resting, low side angle, the mug's reflection visible on the surface."
    ),
}
_HANDHELD_LINE_RE = re.compile(
    r"\b(put(?:ting)?\s+it\s+away|almost|go\s+by\s+it|carry|hold|held|picked)\b",
    re.I,
)


def object_focus_texture_scene(obj: str, *, variety: str = "", setting: str = "") -> str:
    """Affirmative still-life paragraph. Exact 'not a woodcut' clause is stamped at assemble."""
    noun = str(obj or "").strip() or "the object"
    noun = re.sub(r"^(a|an|the)\s+", "", noun, flags=re.I)
    place = " ".join(str(setting or "").split()) or "a furnished room"
    vary = _OBJECT_SHOT_VARIETY.get(str(variety or "").strip(), "")
    vary_bit = f" {vary}" if vary else ""
    return (
        f"A painterly risograph still-life of {noun} in {place}. "
        f"The {noun} sits in a real environment with paper grain, color "
        f"gradient, and canvas texture. {_OBJECT_FOCUS_ENV_EXTRAS}{vary_bit} "
        f"{_FLAT_OBJECT_SURFACE} {_FLAT_SPECULAR_HIGHLIGHT} {_GOLDEN_HOUR_SAT} "
        f"The {noun} occupies the subject of the frame."
    )


def handheld_object_scene(row: dict[str, Any]) -> str:
    who = str(row.get("close_character") or "woman").strip().lower()
    if who not in {"woman", "man"}:
        who = "woman"
    appearance = character_sheet_prompt(who)
    row["character_appearance"] = appearance
    obj = str(
        row.get("episode_spoken_motif")
        or row.get("key_object")
        or "the object"
    ).strip()
    obj = re.sub(r"^(a|an|the)\s+", "", obj, flags=re.I)
    place = " ".join(
        str(row.get("setting") or row.get("episode_place") or "").split()
    ) or "a furnished room"
    scale = hand_object_scale_clause(obj)
    return (
        f"Close crop on one hand of {appearance}, wrist and forearm continuing "
        f"off the frame edge, sleeve of the same wardrobe. "
        f"The {obj} is held in that hand. {scale} "
        f"A painterly risograph still-life of {obj} in {place}. "
        f"The {obj} sits in a real environment with paper grain, color "
        f"gradient, and canvas texture. {_OBJECT_FOCUS_ENV_EXTRAS} "
        "Cropped at the forearm. Wrist and sleeve only."
    )


def apply_handheld_motif_beat(lines: list[dict[str, Any]]) -> None:
    """At least one isolated motif beat puts the object in the woman's hand."""
    if any(
        isinstance(r, dict) and r.get("hand_held_motif") for r in (lines or [])
    ):
        return
    motif = episode_spoken_motif(lines)
    if not motif:
        return
    scored: list[tuple[int, int]] = []
    for i, row in enumerate(lines or []):
        if not isinstance(row, dict) or i == 0 or row.get("hook_template"):
            continue
        if concept_is_locked(row) or row.get("abstract_license"):
            continue
        if beat_shot_type(row) != "macro":
            continue
        text = str(row.get("text") or row.get("beat_text") or "")
        if not line_requires_spoken_motif(text, motif):
            continue
        score = 3 if _HANDHELD_LINE_RE.search(text) else 1
        scored.append((score, i))
    if not scored:
        return
    scored.sort(key=lambda x: (-x[0], x[1]))
    i = scored[0][1]
    row = lines[i]
    row["hand_held_motif"] = True
    row["subject_type"] = "object_focus"
    row["framing"] = "macro_no_setting"
    row["close_character"] = "woman"
    row["key_object"] = motif
    desc = handheld_object_scene(row)
    row["scene_description"] = desc
    row["visual_concept"] = desc
    stamp_shot_type(row)
    print(f"[LOFI handheld] scene={i + 1} {motif!r} in woman's hand")


def ensure_object_focus_texture_prompt(prompt: str, beat: dict[str, Any]) -> str:
    """Restore the exact texture sentence after negation stripping.

    Symbolic / landscape no-object beats reuse subject_type=object_focus
    but must not inherit the ceramic/mug/furnished-room still-life extras.
    """
    if str(beat.get("abstract_license") or "").strip() in {"symbolic", "landscape"}:
        return prompt
    st = str(beat.get("subject_type") or "").strip().lower().replace(" ", "_")
    fr = str(beat.get("framing") or "").strip().lower().replace(" ", "_")
    if st != "object_focus" and fr != "macro_no_setting":
        return prompt
    text = " ".join((prompt or "").split())
    if "not a woodcut" not in text.lower():
        text = " ".join((text + " " + _OBJECT_FOCUS_ENV_TEXTURE_EXACT).split())
    if "furnished room" not in text.lower():
        text = " ".join((text + " " + _OBJECT_FOCUS_ENV_EXTRAS).split())
    vary = _OBJECT_SHOT_VARIETY.get(str(beat.get("object_shot_variant") or ""), "")
    if vary and vary.lower() not in text.lower():
        text = " ".join((text + " " + vary).split())
    if "flat color-plane shading" not in text.lower():
        text = " ".join((text + " " + _FLAT_OBJECT_SURFACE).split())
    if "saturated golden-hour" not in text.lower():
        text = " ".join((text + " " + _GOLDEN_HOUR_SAT).split())
    if "flat hard-edged highlight" not in text.lower():
        text = " ".join((text + " " + _FLAT_SPECULAR_HIGHLIGHT).split())
    return text


def ensure_hook_solid_shape_prompt(prompt: str, beat: dict[str, Any]) -> str:
    """Keep birds-from-head + double-exposure after negation stripping."""
    if not beat.get("hook_template") and beat_shot_type(beat) != "hook":
        return prompt
    text = " ".join((prompt or "").split())
    dissolve = str(beat.get("dissolve_element") or "").strip()
    if dissolve and dissolve.lower() not in text.lower():
        text = " ".join(
            (
                text
                + f" The silhouette edge at the head dissolves into {dissolve}."
            ).split()
        )
    if "double-exposure" not in text.lower() and "double exposure" not in text.lower():
        overlay = str(beat.get("hook_memory_overlay") or "").strip()
        if overlay:
            text = " ".join(
                (
                    text
                    + " The silhouette is overlaid with a faded, blurred "
                    + f"double-exposure of {overlay} bleeding through the dark shape, "
                    + "soft and out of focus, like a fading memory."
                ).split()
            )
    if "no skin" not in text.lower():
        text = " ".join((text + " " + _HOOK_NO_SKIN_EXACT).split())
    return text


def ensure_figure_environment_prompt(prompt: str, beat: dict[str, Any]) -> str:
    """Restore perseverance furnished-room language after negation stripping."""
    if beat.get("hook_template") or beat_shot_type(beat) == "hook":
        return prompt
    st = str(beat.get("subject_type") or "").strip().lower().replace(" ", "_")
    if st not in {"woman", "man", "couple", "silhouette"} and beat_shot_type(beat) not in {
        "portrait",
        "silhouette",
    }:
        return prompt
    text = " ".join((prompt or "").split())
    if "sky, foliage, room, fabric, furniture" not in text.lower():
        text = " ".join((text + " " + _PERSEVERANCE_FIGURE_ENV).split())
    return text


def ensure_portrait_skin_light_prompt(prompt: str, beat: dict[str, Any]) -> str:
    if beat_shot_type(beat) != "portrait":
        return prompt
    text = " ".join((prompt or "").split())
    if "flat illustration skin" not in text.lower():
        text = " ".join((text + " " + _PORTRAIT_FLAT_SKIN_EXACT).split())
    return text


def scrub_artwork_medium_words(text: str) -> str:
    """Drop print/poster only. Keep illustration — that is the style identity."""
    cleaned = _ARTWORK_MEDIUM_WORD_RE.sub("", text or "")
    cleaned = re.sub(r"\s+([,.:;])", r"\1", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    return cleaned.strip()


def compose_hook_silhouette_negative() -> str:
    """Hook solid-shape terms first so the CLIP cap cannot drop them."""
    from core.economic_reel_lofi.config import (
        CLIP_NEGATIVE_TOKEN_CAP,
        _cap_clip_phrases,
        compose_beat_negative,
    )

    base = compose_beat_negative()
    return _cap_clip_phrases(
        f"{_HOOK_SOLID_NEGATIVE}, {base}", CLIP_NEGATIVE_TOKEN_CAP
    )


def pick_hook_dissolve(theme: str, thesis: str, meaning: str) -> str:
    """Meaning-driven flock at the silhouette edge. Never a universal default."""
    blob = f"{theme} {thesis} {meaning}".lower()
    if any(w in blob for w in ("release", "depart", "leave", "away", "fly", "gone")):
        return "a flock of small birds breaking from the silhouette edge"
    if any(w in blob for w in ("hope", "light", "morning", "dawn")):
        return "drifting light particles and pale motes"
    if any(w in blob for w in ("rain", "storm", "tear")):
        return "falling leaves in a thin cluster"
    if any(w in blob for w in ("pack", "carry", "keep", "grief", "loss", "memory", "mug")):
        return "drifting embers and ash motes"
    if any(w in blob for w in ("break", "shatter", "crack")):
        return "shards of shattering glass"
    if any(w in blob for w in ("smoke", "quiet", "silence")):
        return "drifting smoke wisps"
    return "drifting embers and ash motes"


def pick_hook_orb(theme: str, thesis: str, lighting: str) -> str:
    blob = f"{theme} {thesis} {lighting}".lower()
    if any(w in blob for w in ("night", "moon", "blue_hour", "dusk")):
        return "a huge dramatic glowing moon"
    if any(w in blob for w in ("morning", "dawn", "sun")):
        return "a huge dramatic glowing morning sun"
    return "a huge dramatic glowing orb of light"


def pick_hook_memory_overlay(theme: str, thesis: str, meaning: str, motif: str = "") -> str:
    """Episode-specific memory that bleeds through the hook silhouette."""
    blob = f"{theme} {thesis} {meaning} {motif}".lower()
    stem = " ".join(str(motif or "").split()).lower()
    if stem in {"mug", "cup", "kettle"} or "mug" in blob:
        return (
            "a faded kitchen counter and a ceramic mug in leftover morning "
            "light, hazy and out of focus, like a fading memory"
        )
    if "drink" in blob or "glass" in blob:
        return (
            "a hazy table and an untouched glass, soft and out of focus, "
            "like a fading memory"
        )
    if any(w in blob for w in ("coat", "doorway", "threshold")):
        return (
            "a hazy doorway and a hanging coat, soft and out of focus, "
            "like a fading memory"
        )
    if any(w in blob for w in ("letter", "paper", "note")):
        return (
            "a faded table and an unread letter, soft and out of focus, "
            "like a fading memory"
        )
    return (
        "a hazy indistinct room, soft and out of focus, like a fading memory"
    )


def hook_silhouette_dissolve_scene(
    *,
    dissolve_element: str = "",
    orb: str = "",
    memory_overlay: str = "",
) -> str:
    """Locked hook grammar: profile silhouette + birds-from-head + memory overlay.

    Structure extracted from a working surreal reference. Overlay content
    varies by episode. Orb is accepted for call-site compat.
    """
    del orb
    elem = " ".join(str(dissolve_element or "").split()) or (
        "a flock of small birds breaking from the silhouette at the head"
    )
    overlay = " ".join(str(memory_overlay or "").split()) or (
        "a hazy indistinct room or landscape, soft and out of focus, "
        "like a fading memory"
    )
    return (
        f"{_HOOK_DOUBLE_EXPOSURE_MEDIUM} "
        "Close-up profile silhouette of a person's head and shoulders, "
        "flat opaque black. "
        f"The silhouette edge at the head dissolves into {elem}. "
        "The silhouette is overlaid with a faded, blurred double-exposure of "
        f"{overlay} bleeding through the dark shape, soft and out of focus, "
        "like a fading memory. "
        "Quiet, melancholic retro mood. 9:16 vertical poster format."
    )


def apply_hook_template(
    lines: list[dict[str, Any]],
    *,
    theme: str = "",
    thesis: str = "",
) -> None:
    """Beat 1: close-up profile, birds from the head, double-exposure memory."""
    if not lines or not isinstance(lines[0], dict):
        return
    row = lines[0]
    if concept_is_locked(row):
        return
    meaning = str(row.get("meaning") or row.get("text") or "")
    motif = str(
        row.get("episode_spoken_motif")
        or row.get("episode_anchor_name")
        or row.get("key_object")
        or ""
    )
    dissolve = str(row.get("dissolve_element") or "").strip()
    if not dissolve:
        dissolve = pick_hook_dissolve(theme, thesis, meaning)
    if "bird" not in dissolve.lower():
        dissolve = "a flock of small birds breaking from the silhouette at the head"
    overlay = str(row.get("hook_memory_overlay") or "").strip()
    if not overlay:
        overlay = pick_hook_memory_overlay(theme, thesis, meaning, motif)
    row["hook_template"] = True
    row["dissolve_element"] = dissolve
    row["hook_memory_overlay"] = overlay
    row["subject_type"] = "silhouette"
    row["framing"] = "full_scene"
    row["close_variant"] = "silhouette"
    row["composition_type"] = "hook_double_exposure_memory"
    row["palette_key"] = "CONTRAST"
    desc = hook_silhouette_dissolve_scene(
        dissolve_element=dissolve, memory_overlay=overlay
    )
    row["scene_description"] = desc
    row["visual_concept"] = desc
    row["negative_prompt"] = compose_hook_silhouette_negative()
    stamp_shot_type(row)
    print(
        f"[LOFI hook] beat=1 dissolve={dissolve!r} overlay={overlay!r} "
        f"neg_tokens_set=1"
    )


def silhouette_against_light_scene(row: dict[str, Any]) -> str:
    """Default figure language for beats 2–9: body as a flat shape against light."""
    setting = str(row.get("setting") or row.get("episode_place") or "").strip()
    place = setting or "a bright doorway"
    st = str(row.get("subject_type") or "").strip().lower().replace(" ", "_")
    if st == "couple" or str(row.get("silhouette_framing") or "") == "wide_couple":
        who = "Two clothed figures"
        link = " Hands linked or standing close as one printed shape."
    else:
        who = "A clothed figure"
        link = ""
    motif = str(
        row.get("episode_spoken_motif") or row.get("episode_anchor_name") or ""
    ).strip()
    text = str(row.get("text") or row.get("beat_text") or "")
    obj = str(row.get("key_object") or "").strip()
    obj_bit = ""
    if line_requires_spoken_motif(text, motif) and (obj or motif):
        noun = motif or obj
        obj_bit = (
            f" The {noun} is a small shape at the figure's side, much smaller "
            f"than the head, held off-center."
        )
    return (
        f"{who} as a fully flat opaque black silhouette in {place}. "
        f"{_PERSEVERANCE_FIGURE_ENV}{link}{obj_bit}"
    )


def portrait_scene_with_locked_appearance(row: dict[str, Any]) -> str:
    who = str(row.get("close_character") or row.get("subject_type") or "woman")
    if who not in {"woman", "man"}:
        who = "woman"
    motif = str(
        row.get("episode_spoken_motif") or row.get("episode_anchor_name") or ""
    ).strip()
    text = str(row.get("text") or row.get("beat_text") or "")
    obj = str(row.get("key_object") or "").strip()
    noun = motif or obj
    if line_requires_spoken_motif(text, motif) and (obj or motif):
        noun = motif or obj
    appearance = character_sheet_prompt(who)
    row["character_appearance"] = appearance
    noun = re.sub(r"^(a|an|the)\s+", "", str(noun or "the object"), flags=re.I)
    scale = face_object_scale_clause(noun, who)
    return (
        f"Three-quarter portrait of {appearance}. "
        "Head and shoulders with a furnished room still visible behind the head: "
        "walls, fabric, furniture, window light as gouache color blocks with "
        "paper grain. "
        "Single-temperature directional light: one side of the face bright, "
        "the other in shade. "
        f"{_PORTRAIT_FLAT_SKIN_EXACT} "
        f"A foreground {noun} remains in frame. {scale}"
    )


def apply_figure_discipline(lines: list[dict[str, Any]]) -> None:
    """Beats 2–9: silhouette-against-light default; at most one close face; at most one couple."""
    if not lines:
        return
    figure_idx = []
    couple_idx = []
    portrait_idx = []
    for i, row in enumerate(lines):
        if not isinstance(row, dict) or i == 0 or row.get("hook_template"):
            continue
        if concept_is_locked(row):
            continue
        if row.get("abstract_license"):
            continue
        st = str(row.get("subject_type") or "").strip().lower().replace(" ", "_")
        if st == "object_focus":
            continue
        figure_idx.append(i)
        if st == "couple":
            couple_idx.append(i)
        if beat_shot_type(row) == "portrait" or str(row.get("framing") or "") == "portrait_close":
            portrait_idx.append(i)

    keep_couple = -1
    best_couple = -1
    best_cscore = 0
    for i in couple_idx:
        text = str(lines[i].get("text") or lines[i].get("beat_text") or "")
        score = len(_COUPLE_MEANING_RE.findall(text))
        if score > best_cscore:
            best_cscore = score
            best_couple = i
    if best_couple >= 0 and best_cscore > 0:
        keep_couple = best_couple
        row = lines[keep_couple]
        row["subject_type"] = "couple"
        row["silhouette_framing"] = "wide_couple"
        row["close_variant"] = "silhouette"
        row["framing"] = "full_scene"
        row["subject_remap"] = (
            str(row.get("subject_remap") or "") + "+couple_silhouette"
        ).strip("+")
        desc = silhouette_against_light_scene(row)
        row["scene_description"] = desc
        row["visual_concept"] = desc
        stamp_shot_type(row)
        print(f"[LOFI discipline] scene={keep_couple + 1} couple silhouette")

    keep_portrait = -1
    best_p = -1
    best_pscore = 0
    for i in figure_idx:
        if i == keep_couple:
            continue
        row = lines[i]
        text = str(row.get("text") or row.get("beat_text") or "")
        couple_hits = len(_COUPLE_MEANING_RE.findall(text))
        solo_hits = len(_PORTRAIT_MEANING_RE.findall(text))
        if couple_hits >= 2 and solo_hits == 0:
            continue
        score = solo_hits - couple_hits
        # Prefer act2/act3 emotional peak over the hook-adjacent open.
        act = str(row.get("arc_position") or "")
        if act == "act2":
            score += 2
        elif act == "act3":
            score += 1
        if score > best_pscore:
            best_pscore = score
            best_p = i
    if best_p >= 0 and best_pscore > 0:
        keep_portrait = best_p
        row = lines[keep_portrait]
        who = str(row.get("subject_type") or "woman").strip().lower()
        if who not in {"woman", "man"}:
            who = "woman"
        row["subject_type"] = who
        row["close_character"] = who
        row["close_variant"] = "portrait_close"
        row["framing"] = "portrait_close"
        row["subject_remap"] = (
            str(row.get("subject_remap") or "") + "+meaning_portrait_woman"
        ).strip("+")
        desc = portrait_scene_with_locked_appearance(row)
        row["scene_description"] = desc
        row["visual_concept"] = desc
        stamp_shot_type(row)
        print(
            f"[LOFI discipline] scene={keep_portrait + 1} sole close portrait "
            f"who={who}"
        )

    for i in figure_idx:
        if i in {keep_portrait, keep_couple}:
            continue
        row = lines[i]
        st = str(row.get("subject_type") or "").strip().lower().replace(" ", "_")
        if st == "couple":
            row["subject_type"] = "silhouette"
        elif st in {"woman", "man"}:
            row["subject_type"] = "silhouette"
        else:
            row["subject_type"] = "silhouette"
        row["framing"] = "full_scene"
        row["close_variant"] = "silhouette"
        row["silhouette_framing"] = ""
        desc = silhouette_against_light_scene(row)
        row["scene_description"] = desc
        row["visual_concept"] = desc
        stamp_shot_type(row)
        print(f"[LOFI discipline] scene={i + 1} silhouette-against-light")


def act_for_index(index: int, scene_count: int = 9) -> str:
    """Bind palette arc: scenes 1–3 act1, 4–6 act2, 7–9 act3 (scaled to count)."""
    n = max(1, int(scene_count))
    i = max(0, int(index))
    third = n / 3.0
    if i < third:
        return "act1"
    if i < 2 * third:
        return "act2"
    return "act3"


def load_visual_identity_v2(path: Path | None = None) -> dict[str, Any]:
    if path is None:
        rel = str(getattr(lofi_cfg, "VISUAL_IDENTITY_V2_REL", "") or "")
        if rel:
            engine_root = Path(__file__).resolve().parents[2]
            candidate = engine_root / rel
            if candidate.is_file():
                path = candidate
        if path is None:
            path = _DEFAULT_BANK
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not data.get("blocks"):
        raise ValueError(f"invalid visual identity bank: {path}")
    return data


_BANNED_OBJECT_RE = re.compile(
    r"\b(mugs?|cups?|coffee|glasses?|glass|sippy)\b",
    re.IGNORECASE,
)
_HANDHELD_OBJECT_RE = re.compile(
    r"\b(phones?|keys?|in hand|between fingers)\b",
    re.IGNORECASE,
)


def _is_banned_key_object(key_object: str) -> bool:
    blob = key_object or ""
    return bool(_BANNED_OBJECT_RE.search(blob) or _HANDHELD_OBJECT_RE.search(blob))


_TIME_DEIXIS_RE = re.compile(
    r"\b(this|that)\s+(week|morning|evening|night|afternoon|day|hour|time)\b",
    re.I,
)


def caption_has_object_anaphora(text: str) -> bool:
    """True when it/this/that/them refers to an object, not 'this week'."""
    cap = _TIME_DEIXIS_RE.sub(" ", str(text or ""))
    return bool(re.search(r"\b(it|this|that|them)\b", cap, re.I))


def spoken_licenses_key_object(text: str, key_object: str) -> bool:
    """Spoken caption names this object — keep it even if the mug/cup prior bans it."""
    cap = str(text or "").lower()
    obj = str(key_object or "").strip().lower()
    if not cap or not obj:
        return False
    skip = {"a", "an", "the", "of", "in", "on", "at", "and", "or"}
    words = [
        w for w in re.findall(r"[a-z]+", obj)
        if w not in skip and len(w) > 2
    ]
    if words and any(re.search(rf"\b{re.escape(w)}\b", cap) for w in words):
        return True
    if not caption_has_object_anaphora(text):
        return False
    place = set(_SETTING_PLACE_STEMS) | set(_SETTINGISH_NOUNS) | {"counter", "room"}
    primary = words[0] if words else ""
    if primary in place:
        return False
    return bool(words)


def _hands_free_pairs(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        setting = str(row.get("setting") or "").strip()
        obj = str(row.get("key_object") or "").strip()
        if not setting or not obj:
            continue
        if _is_banned_key_object(obj) or _BANNED_OBJECT_RE.search(setting):
            continue
        key = _pair_key(setting, obj)
        if key in seen:
            continue
        seen.add(key)
        item = {"setting": setting, "key_object": obj}
        arch = str(row.get("archetype") or "").strip()
        if arch:
            item["archetype"] = arch
        out.append(item)
    return out


def setting_object_pool(theme_row: dict[str, Any] | None) -> list[dict[str, str]]:
    from core.economic_reel_lofi.setting_archetypes import (
        classify_setting_archetype,
        theme_bank_pairs,
    )

    raw = (theme_row or {}).get("setting_object_pairs") if theme_row else None
    out: list[dict[str, str]] = []
    theme = str((theme_row or {}).get("theme") or "").strip()
    extras = theme_bank_pairs(theme) if theme else []
    if isinstance(raw, list):
        for row in raw:
            if not isinstance(row, dict):
                continue
            setting = str(row.get("setting") or "").strip()
            obj = str(row.get("key_object") or "").strip()
            if setting and obj:
                arch = str(row.get("archetype") or "").strip() or (
                    classify_setting_archetype(setting, obj)
                )
                out.append(
                    {"setting": setting, "key_object": obj, "archetype": arch}
                )
    for row in extras:
        out.append(dict(row))
    cleaned = _hands_free_pairs(out)
    if cleaned:
        return cleaned
    return [dict(p) for p in DEFAULT_SETTING_OBJECT_PAIRS]


def _fill_slots(template: str, **slots: str) -> str:
    text = template
    for key, val in slots.items():
        text = text.replace(f"[{key}]", val)
    return " ".join(text.split())


# Figure-only staging is pose/orientation, not a new prop. Do not name
# kettle/mug/vase here. 'Alone against a blank wall' makes Flux face the wall.
FIGURE_ONLY_STAGING_CLAUSE = (
    "The figure sits in three-quarter or side profile, body open to the room, "
    "looking toward a shaft of light from off-frame. Knees or shoulders angled "
    "into the space."
)
# Composition rule, separate from the object allow-list.
FIGURE_NOT_FACING_WALL_CLAUSE = (
    "The wall is background only. Body open to the room, looking toward "
    "off-frame light."
)
WIDE_NO_PANE_CLAUSE = (
    "Open ground and sky. Exterior scene. The figure reads against air."
)
FIGURE_NO_PANE_CLAUSE = (
    "Empty room. The wall stays behind the figure as background."
)


def only_object_clause(
    key_object: str, *, keep_host: bool = False, figure_only: bool = False
) -> str:
    """
    Positive-prompt 'only this object' sentence.

    Earlier versions named specific banned still-life props ("no mug, no cup,
    no coffee cup, no glass, no laptop") — but naming them, even negated,
    still primes Schnell to draw them (same guidance_scale=0 negation lesson
    already confirmed for camera vocabulary and garbled text: mentioning a
    concept, even to ban it, still anchors the render toward it). The 2026-
    08-21 combined probe showed the mug/laptop-class INTRUDER still firing on
    77% of object_focus attempts despite that explicit named ban being
    present every single time. Describe the isolation positively instead;
    never name the specific intruder objects.

    Figure-only beats must not use 'alone against a blank wall' — Flux then
    stages the person facing the wall. Isolation stays (no extra props);
    posture and facing direction are staging, not objects.
    """
    obj = _sanitize_visual_phrase(key_object) or "the requested object"
    if figure_only:
        return (
            f"Only the figure is visible. Empty floor. "
            f"{FIGURE_ONLY_STAGING_CLAUSE} {FIGURE_NOT_FACING_WALL_CLAUSE}"
        )
    if keep_host:
        return (
            f"{obj} is the subject, shown inside its containing whole so the "
            f"part stays attached to the host."
        )
    return (
        f"Only {obj} is visible in frame, alone against a blank wall. "
        f"The picture contains that subject and empty plaster."
    )


# Atmospheric key_objects are not still-life props. Name a concrete visible
# surface so Flux does not substitute a mug/vase still-life.
_ATMOSPHERIC_FOCUS_NOUN: dict[str, str] = {
    "rain": "rain-streaked window glass with water droplets on the pane",
    "window": "one dry window pane, glass clear, warm light from inside the room",
    "seat": "the empty passenger seat of a car, seen through the open car door",
    "horizon": "empty distant horizon line",
    "platform": "empty train-platform ground as a flat color field",
}
_OBJECT_FOCUS_FRAMING = ("licensed_same", "retry_closer", "retry_angle")
_PART_HOST_RE = re.compile(
    r"\b(car|cabin|piano|dresser|cabinet|sofa|dining table|table|"
    r"doorway|door|record player|closet|wardrobe|bed|bench|bus)\b",
    re.I,
)
_SEAT_HOST_OTHER_RE = re.compile(
    r"\b(coat|sweater|jacket|blanket|cushion|pillow|bag)\b",
    re.I,
)


def _is_bare_atmospheric_object(key_object: str) -> bool:
    """True when the object *is* the atmospheric noun, not a compound that shares a stem."""
    obj = (key_object or "").strip().lower()
    stem = _object_stem(obj)
    if stem not in _ATMOSPHERIC_FOCUS_NOUN:
        return False
    if stem == "seat":
        return bool(re.search(r"\b(passenger\s+)?seats?\b", obj)) and not _SEAT_HOST_OTHER_RE.search(
            obj
        )
    return True


def _needs_containing_whole(key_object: str, setting: str = "") -> bool:
    """Parts of a larger object must keep that whole in the prompt.

    Judge the object itself. A sofa already named in *setting* must not
    disable host-keeping — that is what produced 'cushion on a sofa' plus
    'no furniture' on the same prompt.
    """
    del setting
    obj = (key_object or "").lower()
    if re.search(
        r"\b(passenger\s+seat|steering\s+wheel|dashboard|gear\s*shift|"
        r"piano\s+keys?|drawers?|cushion|pillow|place\s+setting|"
        r"chain\s+lock|record\s+left\s+spinning|seats?)\b",
        obj,
    ):
        return "chair" not in obj
    return False


def _grounded_object_phrase(key_object: str, setting: str = "") -> str:
    """
    If key_object is a part of a larger whole, name that whole.

    Suitcase-class objects already carry ground ('on the floor'). Parts
    (passenger seat, piano key, drawer) do not — without the host they
    render as floating furniture in an empty room.
    """
    obj = _sanitize_visual_phrase(key_object) or "the requested object"
    blob = f"{obj} {setting}".lower()
    if _SEAT_HOST_OTHER_RE.search(obj) and re.search(r"\bseats?\b", obj):
        if re.search(r"\b(bus|bench)\b", blob):
            if "bench" in obj.lower() or "bus" in obj.lower():
                return obj
            return re.sub(r"\bon the seat\b", "on a bus-stop bench", obj, flags=re.I)
        if "car" in blob:
            return obj if "car" in obj.lower() else f"{obj} of a parked car"
        return f"{obj} of a parked car"
    if re.search(r"\b(passenger\s+seat|(?<!chair )(?<!bench )empty\s+seat|seat\s+pulling)\b", blob):
        if re.search(r"\btrain\b", blob):
            return obj if "train" in obj.lower() else f"{obj} in a train car, seen through the window"
        if re.search(r"\bbus\b", blob):
            return obj if "bus" in obj.lower() else f"{obj} on a bus, seen from the aisle"
        if "car" in obj.lower() and "door" in obj.lower():
            return obj
        if "car" in obj.lower():
            return f"{obj}, seen through the open car door"
        return "the empty passenger seat of a car, seen through the open car door"
    if re.search(r"\b(steering\s+wheel|dashboard|gear\s*shift)\b", obj, re.I):
        return obj if "car" in obj.lower() else f"{obj} inside a car cabin"
    if re.search(r"\bpiano\s+keys?\b", obj, re.I):
        return obj if "piano" in obj.lower() and "key" in obj.lower() else f"{obj} on a piano"
    if re.search(r"\bdrawers?\b", obj, re.I) and not re.search(r"\b(dresser|cabinet)\b", obj, re.I):
        return f"{obj} of a dresser, pulled slightly open"
    if re.search(r"\b(sofa\s+cushion|empty\s+cushion|wide\s+empty\s+cushion)\b", obj, re.I):
        return obj if "sofa" in obj.lower() else f"{obj} on a sofa"
    if re.search(r"\bplace\s+setting\b", obj, re.I):
        return obj if "table" in obj.lower() else f"{obj} at a dining table"
    if re.search(r"\bchain\s+lock\b", obj, re.I):
        return obj if "door" in obj.lower() else f"{obj} on a closed door"
    if re.search(r"\brecord\b", obj, re.I) and "spinning" in obj.lower():
        return obj if "player" in obj.lower() else f"{obj} on a record player"
    if re.search(r"\bpillow\b", obj, re.I) and "bed" not in blob:
        return f"{obj} on an empty half of a bed"
    if re.search(r"\bgap where clothes\b", obj, re.I) and "closet" not in blob:
        return f"{obj} in an open closet"
    return obj


def _object_focus_visual_noun(key_object: str, setting: str = "") -> str:
    obj = _sanitize_visual_phrase(key_object) or "the requested object"
    if _is_bare_atmospheric_object(obj):
        stem = _object_stem(obj)
        obj = _ATMOSPHERIC_FOCUS_NOUN.get(stem) or obj
    return _grounded_object_phrase(obj, setting)


RETRY_VARIATION_CLAUSES: tuple[str, ...] = (
    "Same licensed subject. Closer crop. Light from the left.",
    "Same licensed subject. Slightly wider. Light from above.",
    "Same licensed subject. Lower angle. Cooler rim light.",
)


def retry_variation_clause(step: int) -> str:
    """Angle/distance/light only. Never a new place or a new noun."""
    if step <= 0:
        return ""
    return RETRY_VARIATION_CLAUSES[min(step, len(RETRY_VARIATION_CLAUSES)) - 1]


def object_focus_scene_text(
    key_object: str,
    *,
    step: int = 0,
    setting: str = "",
    hand_interaction: bool = False,
    keep_setting: bool = False,
    painterly: bool = False,
) -> tuple[str, str]:
    """Texture-and-light still-life. Named rooms stay out so furniture priors do not fire."""
    obj = _object_focus_visual_noun(key_object, setting)
    keep_host = _needs_containing_whole(key_object, setting)
    extra = _OBJECT_FOCUS_HAND_OK if hand_interaction else _OBJECT_FOCUS_GUARD
    only = (
        f"Only {obj} is the still-life subject. Atmosphere is grain, gradient, and light."
        if not keep_host
        else only_object_clause(obj, keep_host=True)
    )
    text_clause = _TEXT_SURFACE_CLAUSE if _object_stem(obj) in _TEXT_BEARING_STEMS else ""
    step = max(0, min(int(step), 2))
    kind = _OBJECT_FOCUS_FRAMING[step]
    texture = (
        f"The object sits in a real environment with paper grain and canvas "
        f"texture. {_OBJECT_FOCUS_ENV_EXTRAS}"
    )
    if hand_interaction:
        scene = (
            f"One hand holds {obj}. {texture} {extra} "
            f"{hand_object_scale_clause(obj)} {text_clause}"
        )
        return " ".join(scene.split()), "handheld"
    if keep_host:
        scene = (
            f"A still-life of {obj}. {texture} The containing whole stays "
            f"in frame so this stays attached. {extra} {only} {text_clause}"
        )
        return " ".join(scene.split()), "host_context"
    scene = (
        f"A still-life of {obj} filling the frame. {texture} "
        f"{extra} {only} {text_clause}"
    )
    if step > 0:
        scene = f"{scene} {retry_variation_clause(step)}"
    return " ".join(scene.split()), "painterly_setting" if step <= 0 else kind


def silhouette_scene_text(
    char: str,
    key_object: str,
    setting: str,
    tod: str,
    *,
    step: int = 0,
    pose_bit: str = "",
) -> tuple[str, str]:
    """Silhouette keeps place on step 0 (doorway is story). Tighten only after INTRUDER."""
    obj = _sanitize_visual_phrase(key_object) or "the requested object"
    setting = _sanitize_visual_phrase(setting)
    only = only_object_clause(obj)
    step = max(0, min(int(step), 2))
    kinds = ("place_kept", "empty_light", "object_only")
    kind = kinds[step]
    if step <= 0:
        scene = (
            f"{char}{pose_bit} in {setting}, {obj} is the only object, {tod}. "
            f"Hands empty at the sides or out of frame. {only} {_CLOSE_PORTRAIT}"
        )
    elif step == 1:
        scene = (
            f"{char} against empty light, {obj} is the only object. "
            f"Hands empty. {only}"
        )
    else:
        scene = (
            f"{char}, {obj} only, featureless background. "
            f"Hands empty. {only}"
        )
    return " ".join(scene.split()), kind


def couple_wide_silhouette_scene_text(
    setting: str,
    tod: str,
    *,
    step: int = 0,
) -> tuple[str, str]:
    """Wide melancholic two-figure silhouette in open landscape — not a close-up."""
    setting = _sanitize_visual_phrase(setting) or "an open landscape"
    step = max(0, min(int(step), 2))
    kinds = ("wide_place", "wide_empty", "wide_horizon")
    kind = kinds[step]
    if step <= 0:
        scene = (
            f"A wide melancholic silhouette of two figures, a man and a woman "
            f"standing apart in {setting}, {tod}. Open plane, small in the frame. "
            f"Hands empty at their sides."
        )
    elif step == 1:
        scene = (
            f"Two distant silhouettes of a man and a woman on an open plane, {tod}. "
            f"Wide landscape, empty hands. Featureless sky."
        )
    else:
        scene = (
            f"Two small featureless figures on a wide horizon line, {tod}. "
            f"Hands empty."
        )
    return " ".join(scene.split()), kind


def _palette_sentence(bank: dict[str, Any], act: str) -> tuple[str, str]:
    by_arc = bank.get("palette_by_arc") or {}
    key = str(by_arc.get(act) or by_arc.get("act1") or "WARM").upper()
    palettes = bank.get("palettes") or {}
    sentence = str(palettes.get(key) or palettes.get("WARM") or "").strip()
    return sentence, key


def normalize_beat_visuals(
    row: dict[str, Any],
    *,
    index: int,
    scene_count: int,
    pool: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Coerce beat visual fields. LLM may only fill expression + pick pool rows."""
    pairs = pool or [dict(p) for p in DEFAULT_SETTING_OBJECT_PAIRS]
    fallback = pairs[index % len(pairs)]
    st = str(row.get("subject_type") or "").strip().lower().replace(" ", "_")
    if st not in SUBJECT_TYPES:
        st = "woman"
    expr = str(row.get("subject_expression") or "").strip() or "sad"
    setting = str(row.get("setting") or "").strip() or fallback["setting"]
    key_object = str(row.get("key_object") or "").strip() or fallback["key_object"]
    tod = str(row.get("time_of_day") or "").strip() or "dusk"
    act = act_for_index(index, scene_count)
    text = str(row.get("text") or row.get("beat_text") or "").strip()
    row["text"] = text
    row["beat_text"] = text
    row["subject_type"] = st
    row["subject_expression"] = expr
    row["setting"] = setting
    row["key_object"] = key_object
    row["time_of_day"] = tod
    row["arc_position"] = act
    return row


def text_lacks_concrete_anchor(text: str) -> bool:
    """True when narration has no place/object noun to hang an illustration on."""
    return not bool(_CONCRETE_NOUN_RE.search(text or ""))


_SETTINGISH_NOUNS = frozenset(
    {
        "kitchen",
        "bedroom",
        "bedrooms",
        "hallway",
        "hallways",
        "street",
        "streets",
        "porch",
        "porches",
        "buildings",
        "building",
    }
)


def last_concrete_noun(text: str) -> str:
    """Last matching object/place noun in text, else empty."""
    found = list(_CONCRETE_NOUN_RE.finditer(text or ""))
    return found[-1].group(0).lower() if found else ""


def preferred_concrete_noun(text: str) -> str:
    """First object-like noun, skipping setting/place words when an object follows."""
    found = [m.group(0).lower() for m in _CONCRETE_NOUN_RE.finditer(text or "")]
    if not found:
        return ""
    objects = [n for n in found if n not in _SETTINGISH_NOUNS]
    return objects[0] if objects else found[0]


def beat_lacks_noun_and_action(text: str) -> bool:
    """True when a spoken beat has no concrete noun + action pairing."""
    blob = text or ""
    has_noun = bool(_CONCRETE_NOUN_RE.search(blob))
    has_action = bool(_CONCRETE_ACTION_RE.search(blob))
    return not (has_noun and has_action)


def setting_is_concrete(setting: str, key_object: str) -> bool:
    s = (setting or "").strip()
    o = (key_object or "").strip()
    if len(s) < 8 or len(o) < 4:
        return False
    if not (_CONCRETE_NOUN_RE.search(s) or _CONCRETE_NOUN_RE.search(o)):
        return False
    return True


def _pair_key(setting: str, key_object: str) -> tuple[str, str]:
    return (setting.strip().lower(), key_object.strip().lower())


def _unused_pool_pair(
    pool: list[dict[str, str]],
    used: set[tuple[str, str]],
    *,
    prefer_setting: str | None = None,
    avoid_object: str | None = None,
    index: int = 0,
) -> dict[str, str]:
    prefer = (prefer_setting or "").strip().lower()
    avoid = (avoid_object or "").strip().lower()
    ranked: list[dict[str, str]] = []
    for p in pool:
        key = _pair_key(p["setting"], p["key_object"])
        if key in used:
            continue
        ranked.append(p)
    if prefer:
        same = [p for p in ranked if p["setting"].strip().lower() == prefer]
        if avoid:
            same = [p for p in same if p["key_object"].strip().lower() != avoid] or same
        if same:
            return dict(same[0])
    if avoid:
        diff_obj = [p for p in ranked if p["key_object"].strip().lower() != avoid]
        if diff_obj:
            return dict(diff_obj[0])
    if ranked:
        return dict(ranked[0])
    fallback = pool[index % max(1, len(pool))]
    return dict(fallback)


_CLOSER_ANGLE_RE = re.compile(r"(?:,?\s*from a closer angle)+", re.IGNORECASE)
_CLOSER_ANGLE_MARK = "from a closer angle"


def _base_object_name(key_object: str) -> str:
    """Strip every closer-angle suffix, including mid-string stacks."""
    out = _CLOSER_ANGLE_RE.sub("", key_object or "")
    return " ".join(out.split()).strip(" ,")


def _sanitize_visual_phrase(text: str) -> str:
    """Keep at most one closer-angle crop; collapse 2–3x stacks."""
    raw = (text or "").strip()
    if _CLOSER_ANGLE_MARK not in raw.lower():
        return raw
    base = _base_object_name(raw)
    if base:
        return f"{base} {_CLOSER_ANGLE_MARK}"
    return base


def _changed_detail_object(key_object: str) -> str:
    """One closer-angle variation only — never concatenate the phrase twice."""
    base = _base_object_name(key_object)
    return f"{base} {_CLOSER_ANGLE_MARK}" if base else "a concrete household object"


def _expand_thin_setting(setting: str, pool: list[dict[str, str]]) -> str:
    s = (setting or "").strip()
    if len(s.split()) >= 4:
        return s
    low = s.lower()
    for p in pool:
        cand = str(p.get("setting") or "").strip()
        if len(cand.split()) >= 4 and (low in cand.lower() or cand.lower() in low):
            return cand
    return s


_STOP_VISUAL = frozenset(
    {
        "a", "an", "the", "to", "of", "for", "and", "or", "in", "on", "at",
        "as", "is", "it", "it's", "its", "you", "your", "they", "them",
        "their", "that", "this", "with", "from", "before", "after", "not",
        "isn't", "aren't", "was", "were", "be", "been",
    }
)

# Literal synonyms only. Abstract caption words (cost, finally, noticed,
# silence, love, …) must never mint a physical stand-in here — they map
# to lighting/composition via _MEANING_CLUSTERS["treatment"].
_CAPTION_OBJECT_ALIASES: dict[str, tuple[str, ...]] = {
    "seat": ("chair", "chairs", "empty passenger seat"),
    "seats": ("chair", "chairs"),
    "doorway": ("door", "doors", "open doorway"),
    "doorways": ("door", "doors", "open doorway"),
}

# Abstract/emotional lines → lighting and composition only. pairs stays
# empty so a token like "finally" / "costs" / "noticed" cannot mint a prop.
_MEANING_CLUSTERS: tuple[dict[str, Any], ...] = (
    {
        "id": "disorder",
        "weight": 3,
        "tokens": frozenset({
            "chaos", "chaotic", "noise", "loud", "mess", "messy", "disorder",
            "chased", "chase", "storm", "frantic", "scattered", "clutter",
        }),
        "pairs": (),
        "treatment": {
            "lighting_condition": "rainy_grey",
            "shot_scale": "medium",
            "light_direction": "uneven side light",
        },
        "expression": "restless, searching",
    },
    {
        "id": "leaving",
        "weight": 3,
        "tokens": frozenset({
            "walking", "walk", "walks", "walked", "leaving", "leave", "leaves",
            "turning", "turn", "away", "exit", "go", "going",
        }),
        "pairs": (),
        "treatment": {
            "lighting_condition": "blue_hour_streetlight",
            "shot_scale": "wide",
            "light_direction": "light behind the figure",
        },
        "expression": "turning away",
        "pose": "body turned mid-step, hands empty, not seated",
    },
    {
        "id": "conflict",
        "weight": 2,
        "tokens": frozenset({
            "fight", "fighting", "war", "argument", "slammed", "slam",
            "yelling", "yell", "battle",
        }),
        "pairs": (),
        "treatment": {
            "lighting_condition": "overcast_daylight",
            "shot_scale": "medium",
            "light_direction": "hard split light",
        },
    },
    {
        "id": "depletion",
        "weight": 2,
        "tokens": frozenset({
            "cost", "costs", "spent", "empty", "depleted", "worn", "tired",
            "nothing", "leftover", "remain", "winning", "won",
        }),
        "pairs": (),
        "treatment": {
            "lighting_condition": "indoor_lamp_glow",
            "shot_scale": "medium",
            "light_direction": "weak lamp from one side",
            "color_temperature": "cool wall, thin warm edge",
        },
        "expression": "hollow, aware",
    },
    {
        "id": "proof",
        "weight": 2,
        "tokens": frozenset({"proof", "prove", "someone", "missed", "stayed"}),
        "pairs": (),
        "treatment": {
            "lighting_condition": "blue_hour_streetlight",
            "shot_scale": "medium",
            "light_direction": "steady lamp plane",
        },
    },
    {
        "id": "learning",
        "weight": 2,
        "tokens": frozenset({
            "learned", "learn", "learning", "noticed", "notice", "finally",
            "understood", "realize", "realized", "score", "keeping",
        }),
        "pairs": (),
        "treatment": {
            "lighting_condition": "indoor_lamp_glow",
            "shot_scale": "close",
            "light_direction": "warm spill from below",
            "color_temperature": "hot amber on cool plaster",
        },
    },
    {
        "id": "choice",
        "weight": 1,
        "tokens": frozenset({"choice", "choose", "choosing", "instead"}),
        "pairs": (),
        "treatment": {
            "lighting_condition": "overcast_daylight",
            "shot_scale": "medium",
            "light_direction": "even daylight",
        },
    },
    {
        "id": "connection",
        "weight": 1,
        "tokens": frozenset({
            "connection", "love", "loving", "together", "stay", "staying",
            "caring", "care",
        }),
        "pairs": (),
        "treatment": {
            "lighting_condition": "indoor_lamp_glow",
            "shot_scale": "medium",
            "light_direction": "soft lamp from the side",
        },
        "expression": "soft, unguarded",
    },
    {
        "id": "peace",
        "weight": 2,
        "tokens": frozenset({
            "peace", "quiet", "calm", "still", "silence", "silent",
            "boring", "safety", "healed", "healing", "home",
        }),
        "pairs": (),
        "treatment": {
            "lighting_condition": "indoor_lamp_glow",
            "shot_scale": "medium",
            "light_direction": "steady lamp on a blank wall",
        },
        "expression": "steady, calm",
    },
)

_VARIETY_PAIRS: tuple[dict[str, str], ...] = tuple(
    dict(p) for p in DEFAULT_SETTING_OBJECT_PAIRS
)

_MAX_OBJECT_STEM_HITS = 1  # same stem may appear at most once per episode
_OBJECT_STEM_WORDS: tuple[str, ...] = (
    "rain", "platform", "suitcase", "horizon", "streetlamp",
    "paper", "chair", "door", "window", "letter", "envelope",
    "phone", "blanket", "notebook", "journal", "coat", "shoe",
    "pillow", "lamp", "kettle", "photo", "plate", "bag", "radio", "mail",
    "bed", "seat", "bench", "umbrella",
)

_EXPECTED_PALETTE_BY_ACT: dict[str, str] = {
    "act1": "WARM",
    "act2": "COLD",
    "act3": "CONTRAST",
}
_SETTING_PLACE_STEMS: tuple[str, ...] = (
    "hallway", "bedroom", "kitchen", "bathroom", "dining", "living",
    "desk", "porch", "stoop", "street", "window", "park", "sofa", "car",
    "bed", "sink", "counter", "table", "coat", "platform", "station",
)

_PREFERRED_SUBJECTS: tuple[str, ...] = ("woman", "man", "couple")
_VARIETY_SUBJECTS: tuple[str, ...] = ("object_focus", "silhouette")
_MAX_COUPLE_PER_EPISODE = 3
_MIN_VARIETY_SHOTS = 3
_MAX_VARIETY_SHOTS = 4
_COUPLE_SEPARATION = (
    "Two separate distinct people: one man and one woman, two distinct faces, "
    "two distinct bodies."
)
_OBJECT_FOCUS_GUARD = (
    "The object sits alone, centered, filling the frame."
)
_OBJECT_FOCUS_HAND_OK = (
    "One hand may hold the object only if a plausible wrist and forearm "
    "continue off-frame in a natural direction."
)
_CLOSE_PORTRAIT = (
    "Close portrait, face and torso filling the frame."
)
_HAND_POSE = (
    "Hands empty and away from the face: at the sides, in the lap, or out of frame."
)
_WIDE_COUPLE_PLACE_RE = re.compile(
    r"\b(platform|station|park|landscape|horizon|street|field|corner|curb)\b",
    re.IGNORECASE,
)
_SATURATION_GUARD = (
    "Keep colors saturated and printed."
)
# Schnell renders "papers"/"mail"/"signs" the same way it renders a mug on a
# desk: the noun's learned prior includes printed lettering. Negative prompts
# do not work at guidance_scale=0 (confirmed on the mug fix), so this is a
# positive-prompt guard, universal across subject_type (portraits can have a
# background sign/paper too, not just object_focus).
#
# 2026-08-21 full 9-beat run: garbled text still surfaced on scene 8 — a
# portrait beat with no paper/sign/screen object at all — as marks on the
# woman's forearm and a stray corner watermark ("17/19"). The original
# surface-type wording ("any paper, sign, screen...") never covered skin,
# clothing, background, or frame corners, so it missed this class entirely.
# Rewritten to a true whole-canvas statement instead of an object-type list —
# corner/signature-style artifacts are a known FLUX failure mode (the
# critic's own corner_scan field exists to catch exactly this), described
# positively rather than negated (same lesson as every prior fix here).
_TEXT_LEGIBILITY_GUARD = (
    "Every mark anywhere in this image is either ink linework, halftone "
    "texture, or flat printed color. This includes skin, clothing, hair, "
    "background, paper, signs, screens, and all four corners of the frame. "
    "The illustration fills the frame edge to edge with scene content."
)
# Same stem family _object_stem() already collapses for repetition-cap
# purposes, extended to sibling text-bearing nouns from this file's own noun
# regex (letters, envelopes, lists, notebooks, calendars, bills).
_TEXT_BEARING_STEMS = frozenset(
    {
        "paper",
        "mail",
        "letter",
        "envelope",
        "notebook",
        "journal",
        "calendar",
        "list",
        "bills",
        "ticket",
        "stub",
        "poster",
        "sign",
        "signage",
        "book",
        "newspaper",
        "menu",
        "label",
        "chalkboard",
        "headline",
        "caption",
        "type",
        "typography",
    }
)
_TEXT_SURFACE_CLAUSE = (
    "The visible surface is blank abstract color and paper grain only — "
    "unmarked, no letters, no cursive, no poster type."
)


def _visual_tokens(text: str, *, expand_aliases: bool = False) -> set[str]:
    """Tokenize. Alias expansion is caption-only — never expand setting words
    like 'room' into door/chair or every indoor scene looks 'tied'."""
    words = re.findall(r"[a-z0-9']+", (text or "").lower())
    out: set[str] = set()
    for w in words:
        if w in _STOP_VISUAL or len(w) < 3:
            continue
        out.add(w)
        if expand_aliases:
            for alias in _CAPTION_OBJECT_ALIASES.get(w, ()):
                out.update(re.findall(r"[a-z0-9']+", alias.lower()))
    return out


def _raw_caption_words(text: str) -> list[str]:
    return [
        w for w in re.findall(r"[a-z0-9']+", (text or "").lower())
        if w not in _STOP_VISUAL and len(w) >= 3
    ]


def _forms(word: str) -> set[str]:
    w = word.lower()
    out = {w}
    if w.endswith("ing") and len(w) > 5:
        out.add(w[:-3])
        out.add(w[:-3] + "e")
    if w.endswith("ed") and len(w) > 4:
        out.add(w[:-2])
        out.add(w[:-1])
    if w.endswith("s") and len(w) > 3 and not w.endswith("ss"):
        out.add(w[:-1])
    return out


def _cluster_hits(text: str, tokens: frozenset[str]) -> int:
    """Count caption words that match the cluster (one hit per spoken word)."""
    hits = 0
    for w in _raw_caption_words(text):
        if _forms(w) & tokens:
            hits += 1
    return hits


def _caption_lex(text: str) -> set[str]:
    """Caption tokens plus light stems so walking/walked hit the same cluster."""
    out: set[str] = set()
    for w in re.findall(r"[a-z0-9']+", (text or "").lower()):
        out.update(_forms(w))
    return out


def _object_stem(key_object: str) -> str:
    """Collapse 'empty chair' / 'overturned chair' to one stem for repetition."""
    blob = (key_object or "").lower()
    if "paper" in blob or "mail" in blob:
        return "paper"
    if "mug" in blob or re.search(r"\bcups?\b", blob) or "coffee" in blob:
        return "mug"
    for stem in _OBJECT_STEM_WORDS:
        if stem in blob:
            return stem
    tokens = re.findall(r"[a-z]+", blob)
    return tokens[-1] if tokens else blob.strip()


def _setting_stem(setting: str) -> str:
    """Collapse 'hallway near a closed door' / 'hallway by an open door'."""
    text = (setting or "").lower()
    for place in _SETTING_PLACE_STEMS:
        if place in text:
            return place
    tokens = re.findall(r"[a-z]+", text)
    stop = {
        "a", "an", "the", "at", "by", "in", "of", "on", "to", "and",
        "near", "after", "under", "with", "from",
    }
    kept = [t for t in tokens if t not in stop]
    return " ".join(kept[:2]) if kept else ""


def score_episode_variety(lines: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Episode-level variety report for setting / framing / object / palette.

    Intended WARM×act1, COLD×act2, CONTRAST×act3 is not treated as palette
    repetition. Variety-shot miss and non-anchor object-over-cap set
    holds_episode so the narrative layer can hard-fail (not log-only).
    """
    setting_stems: list[str] = []
    compositions: list[str] = []
    object_stems: list[str] = []
    palettes: list[str] = []
    palette_expected: list[str] = []
    notes: list[str] = []
    n = 0
    for row in lines:
        if not isinstance(row, dict):
            continue
        n += 1
        setting_stems.append(_setting_stem(str(row.get("setting") or "")))
        st = str(row.get("subject_type") or "").strip().lower().replace(" ", "_")
        compositions.append(st or "unknown")
        object_stems.append(_object_stem(str(row.get("key_object") or "")))
        pal = str(row.get("palette_key") or row.get("riso_palette") or "").upper()
        palettes.append(pal)
        act = str(row.get("arc_position") or "act1").lower()
        palette_expected.append(_EXPECTED_PALETTE_BY_ACT.get(act, "WARM"))

    def _counts(items: list[str]) -> dict[str, int]:
        out: dict[str, int] = {}
        for item in items:
            if not item:
                continue
            out[item] = out.get(item, 0) + 1
        return out

    set_counts = _counts(setting_stems)
    obj_counts = _counts(object_stems)
    comp_counts = _counts(compositions)
    setting_repeats = {k: v for k, v in set_counts.items() if v >= 2}
    object_repeats = {k: v for k, v in obj_counts.items() if v >= 2}
    anchor_stem = ""
    if lines and isinstance(lines[0], dict):
        anchor_stem = str(lines[0].get("episode_anchor_stem") or "").strip().lower()
    object_over_cap = {
        k: v
        for k, v in obj_counts.items()
        if v > _MAX_OBJECT_STEM_HITS and k != anchor_stem
    }

    person_n = sum(comp_counts.get(k, 0) for k in ("woman", "man", "couple"))
    variety_n = sum(comp_counts.get(k, 0) for k in ("object_focus", "silhouette"))
    if n >= 8:
        if variety_n < _MIN_VARIETY_SHOTS:
            notes.append(f"variety_shots={variety_n} below target {_MIN_VARIETY_SHOTS}")
        if variety_n > _MAX_VARIETY_SHOTS:
            notes.append(f"variety_shots={variety_n} above target {_MAX_VARIETY_SHOTS}")
        if person_n > 6:
            notes.append(f"person_shots={person_n} above target 6")
        couple_n = comp_counts.get("couple", 0)
        if couple_n > _MAX_COUPLE_PER_EPISODE:
            notes.append(f"couple={couple_n} above cap {_MAX_COUPLE_PER_EPISODE}")

    locked_world = bool(
        lines
        and isinstance(lines[0], dict)
        and (
            str(lines[0].get("force_episode_world_id") or "").strip()
            or str(lines[0].get("episode_world_id") or "").strip()
        )
    )
    palette_mismatches: list[dict[str, Any]] = []
    if locked_world:
        palette_arc_ok = True
        notes.append("palette_arc_exempt=single_location")
    else:
        for i, (got, exp) in enumerate(zip(palettes, palette_expected), start=1):
            if got and got != exp:
                palette_mismatches.append({"scene": i, "got": got, "expected": exp})
        palette_arc_ok = not palette_mismatches
        if palette_mismatches:
            notes.append(f"palette_arc_mismatch={len(palette_mismatches)}")
    if setting_repeats:
        notes.append(
            "setting_repeats="
            + ",".join(f"{k}x{v}" for k, v in setting_repeats.items())
        )
    if object_over_cap:
        notes.append(
            "object_over_cap="
            + ",".join(f"{k}x{v}" for k, v in object_over_cap.items())
        )

    distinct_settings = len(set_counts)
    distinct_objects = len(obj_counts)
    summary = (
        f"settings={distinct_settings}/{n} distinct"
        f"; compositions={comp_counts}"
        f"; objects={distinct_objects} distinct"
        f"; palette_arc_ok={str(palette_arc_ok).lower()}"
    )
    if notes:
        summary += f"; notes={notes}"
    report = {
        "beat_count": n,
        "distinct_settings": distinct_settings,
        "setting_stems": set_counts,
        "setting_repeats": setting_repeats,
        "distinct_compositions": len(comp_counts),
        "compositions": comp_counts,
        "person_shots": person_n,
        "variety_shots": variety_n,
        "distinct_object_stems": distinct_objects,
        "object_stems": obj_counts,
        "object_repeats": object_repeats,
        "object_over_cap": object_over_cap,
        "palette_sequence": palettes,
        "palette_expected": palette_expected,
        "palette_arc_ok": palette_arc_ok,
        "palette_mismatches": palette_mismatches,
        "notes": notes,
        "summary": summary,
        "holds_episode": bool(
            n >= 8
            and not _episode_has_atmosphere(lines)
            and (
                person_n > 6
                or bool(object_over_cap)
            )
        ),
    }
    _merge_setting_archetype_score(report, lines)
    _merge_composition_score(report, lines)
    return report


def _apply_setting_archetype_pass(
    lines: list[dict[str, Any]],
    pool: list[dict[str, str]] | None,
    *,
    lock_visuals: bool = False,
) -> None:
    """Remap overflow archetypes; prefer ones the previous script skipped."""
    from core.economic_reel_lofi.setting_archetypes import (
        ARCHETYPE_ORDER,
        enforce_setting_archetype_variety,
    )

    theme = ""
    module = "relationship"
    if lines and isinstance(lines[0], dict):
        theme = str(lines[0].get("episode_theme") or "")
        module = str(lines[0].get("episode_module") or "relationship") or (
            "relationship"
        )
    prefer_new: list[str] = []
    try:
        from core.economic_reel_lofi import lofi_collections as rag

        prev = rag.preceding_setting_archetypes(module)
        prefer_new = [a for a in ARCHETYPE_ORDER if a not in set(prev)]
    except Exception:
        prefer_new = []
    enforce_setting_archetype_variety(
        lines,
        pool,
        theme=theme,
        prefer_new=prefer_new,
        lock_visuals=lock_visuals,
    )
    from core.economic_reel_lofi.setting_archetypes import (
        enforce_composition_variety,
    )

    enforce_composition_variety(lines, lock_visuals=lock_visuals)


def _merge_setting_archetype_score(report: dict[str, Any], lines: list[dict[str, Any]]) -> None:
    """Attach archetype counts; overflow holds the episode."""
    from core.economic_reel_lofi.setting_archetypes import (
        score_setting_archetypes,
    )

    arch = score_setting_archetypes(lines)
    report["setting_archetypes"] = arch.get("counts") or {}
    report["setting_archetype_beats"] = arch.get("beats") or []
    report["setting_archetype_overflow"] = arch.get("overflow") or {}
    if arch.get("holds_episode") and not _episode_has_atmosphere(lines):
        report["holds_episode"] = True
        notes = list(report.get("notes") or [])
        notes.append(f"archetype_overflow={arch.get('overflow')}")
        report["notes"] = notes
        report["summary"] = (
            str(report.get("summary") or "")
            + f"; archetypes={arch.get('counts')}"
        )


def _merge_composition_score(report: dict[str, Any], lines: list[dict[str, Any]]) -> None:
    from core.economic_reel_lofi.setting_archetypes import (
        score_composition_types,
    )

    comp = score_composition_types(lines)
    report["composition_types"] = comp.get("counts") or {}
    report["composition_beats"] = comp.get("beats") or []
    report["figure_centered"] = comp.get("figure_centered")
    if comp.get("holds_episode") and not _episode_has_atmosphere(lines):
        report["holds_episode"] = True
        notes = list(report.get("notes") or [])
        notes.append(f"composition={comp.get('summary')}")
        report["notes"] = notes
        report["summary"] = (
            str(report.get("summary") or "") + f"; {comp.get('summary')}"
        )


def _pair_fits_cluster(setting: str, key_object: str, cluster: dict[str, Any]) -> bool:
    """True only for this cluster's actual props, not a shared stem like 'chair'."""
    obj = (key_object or "").strip().lower()
    blob = f"{setting} {key_object}".lower()
    for p in cluster.get("pairs") or ():
        c_obj = str(p.get("key_object") or "").strip().lower()
        if not c_obj:
            continue
        if obj == c_obj or c_obj in blob:
            return True
        words = [
            w for w in re.findall(r"[a-z]+", c_obj)
            if w not in _STOP_VISUAL and w not in {"empty", "open", "closed", "same"}
        ]
        if len(words) >= 2 and all(w in blob for w in words):
            return True
    return False


def _is_known_standin(setting: str, key_object: str) -> bool:
    """True when this pair is a meaning-cluster or variety stand-in we assigned."""
    for cluster in _MEANING_CLUSTERS:
        if _pair_fits_cluster(setting, key_object, cluster):
            return True
    obj = (key_object or "").strip().lower()
    key = _pair_key(setting, key_object)
    for p in _VARIETY_PAIRS:
        if _pair_key(p["setting"], p["key_object"]) == key:
            return True
        if str(p.get("key_object") or "").strip().lower() == obj:
            return True
    return False


def _best_meaning_cluster(text: str) -> dict[str, Any] | None:
    head = set(re.findall(r"[a-z0-9']+", (text or "").lower())[:4])
    best: dict[str, Any] | None = None
    best_score = 0
    for cluster in _MEANING_CLUSTERS:
        tokens: frozenset[str] = cluster["tokens"]
        hits = _cluster_hits(text, tokens)
        if not hits:
            continue
        score = hits * 10 + int(cluster.get("weight") or 1)
        if tokens & head:
            score += 1
        if score > best_score:
            best_score = score
            best = cluster
    return best


def visual_tied_to_caption(
    text: str,
    setting: str,
    key_object: str,
    *,
    episode_world: dict[str, Any] | None = None,
) -> bool:
    """True when setting/object shares a caption noun or a meaning stand-in.

    Object-licensing is the floor: a spoken noun must still appear. An episode
    world covers abstract beats so they are not forced to mint a new landmark.
    Lines with no nameable object are valid under the third path
    (symbolic / landscape / character) and must not fail this check.
    """
    noun = preferred_concrete_noun(text)
    if (
        not noun
        or noun in _SKIP_RENDER_NOUNS
        or noun in _TIME_NOUNS
        or noun in _SETTINGISH_NOUNS
        or _is_banned_key_object(noun)
    ):
        return True
    if str(key_object or "").strip().lower() in _ABSTRACT_KEY_OBJECTS:
        return True
    world_id = str((episode_world or {}).get("id") or "").strip()
    world_place = str((episode_world or {}).get("place") or "").strip()
    if world_id or world_place:
        noun = preferred_concrete_noun(text)
        if not noun or noun in _SKIP_RENDER_NOUNS or _is_banned_key_object(noun):
            return True
        blob = f"{setting} {key_object} {world_place}".lower()
        if noun in blob:
            return True
        for alias in _CAPTION_OBJECT_ALIASES.get(noun, ()):
            if alias.lower() in blob:
                return True
    cap = _visual_tokens(text, expand_aliases=False)
    vis = _visual_tokens(f"{setting} {key_object}", expand_aliases=False)
    if not cap:
        return bool(vis)
    if cap & vis:
        return True
    noun = preferred_concrete_noun(text)
    if noun and noun in vis:
        return True
    blob = f"{setting} {key_object}".lower()
    for token in cap:
        if re.search(rf"\b{re.escape(token)}\b", blob):
            return True
        for alias in _CAPTION_OBJECT_ALIASES.get(token, ()):
            if alias.lower() in blob:
                return True
    cluster = _best_meaning_cluster(text)
    if cluster and _pair_fits_cluster(setting, key_object, cluster):
        return True
    if not preferred_concrete_noun(text) and _is_known_standin(setting, key_object):
        return True
    return False


def _score_pair_for_caption(text: str, setting: str, key_object: str) -> int:
    cap = _visual_tokens(text, expand_aliases=False)
    vis = _visual_tokens(f"{setting} {key_object}", expand_aliases=False)
    score = len(cap & vis)
    blob = f"{setting} {key_object}".lower()
    noun = preferred_concrete_noun(text)
    if noun and re.search(rf"\b{re.escape(noun)}\b", (key_object or "").lower()):
        score += 8
    for token in cap:
        for alias in _CAPTION_OBJECT_ALIASES.get(token, ()):
            if alias.lower() in blob:
                score += 2
    cluster = _best_meaning_cluster(text)
    if cluster and _pair_fits_cluster(setting, key_object, cluster):
        score += 2 + len(cluster["tokens"] & _caption_lex(text))
    return score


def _prefer_character_subject(row: dict[str, Any], index: int) -> None:
    """Keep valid types including object_focus/silhouette; fill empties only."""
    st = str(row.get("subject_type") or "").strip().lower().replace(" ", "_")
    if st in SUBJECT_TYPES:
        return
    row["subject_type"] = "woman"
    row["subject_remap"] = f"{st or 'empty'}_to_character_portrait"


def _apply_framing_variety(lines: list[dict[str, Any]]) -> None:
    """
    Target mix for a 9-beat reel: ~5–6 person shots, 3–4 object_focus/silhouette.
    Cap couple at 3 (Schnell fusion at 4 steps).
    """
    n = len(lines)
    if n <= 0:
        return

    locked_abstract = {
        i for i in range(n)
        if isinstance(lines[i], dict) and lines[i].get("abstract_license")
    }

    couple_idx = [
        i for i, row in enumerate(lines)
        if isinstance(row, dict)
        and i not in locked_abstract
        and str(row.get("subject_type") or "").strip().lower() == "couple"
    ]
    if len(couple_idx) > _MAX_COUPLE_PER_EPISODE:
        keep = set(couple_idx[-_MAX_COUPLE_PER_EPISODE:])
        for i in couple_idx:
            if i in keep:
                continue
            row = lines[i]
            row["subject_type"] = "woman"
            row["subject_remap"] = "couple_cap_to_single"
            print(
                f"[LOFI identity v2] couple-cap scene={i + 1} "
                f"-> {row['subject_type']}"
            )

    def _stype(i: int) -> str:
        return str(lines[i].get("subject_type") or "").strip().lower().replace(" ", "_")

    variety_idx = [
        i for i in range(n) if i not in locked_abstract and _stype(i) in _VARIETY_SUBJECTS
    ]
    person_idx = [
        i for i in range(n) if i not in locked_abstract and _stype(i) in _PREFERRED_SUBJECTS
    ]

    need = max(0, _MIN_VARIETY_SHOTS - len(variety_idx))
    if need:
        candidates = [
            i for i in person_idx
            if _stype(i) != "couple" and i != 0 and i not in locked_abstract
        ]
        # Hook beat (scene 1) stays a figure. Variety fills from beats 2+.
        for k, i in enumerate(candidates[:need]):
            nxt = "object_focus" if k % 2 == 0 else "silhouette"
            stem = _object_stem(str(lines[i].get("key_object") or ""))
            if nxt == "object_focus" and stem in _AVOID_ANCHOR_STEMS:
                nxt = "silhouette"
            lines[i]["subject_type"] = nxt
            lines[i]["subject_remap"] = "framing_variety"
            print(
                f"[LOFI identity v2] framing-variety scene={i + 1} -> {nxt} "
                f"object={lines[i].get('key_object')!r}"
            )
            variety_idx.append(i)

    if lines and isinstance(lines[0], dict) and 0 not in locked_abstract:
        hook_st = _stype(0)
        if hook_st == "object_focus":
            lines[0]["subject_type"] = "silhouette"
            lines[0]["subject_remap"] = (
                str(lines[0].get("subject_remap") or "") + "+hook_character"
            ).strip("+")
            print(
                "[LOFI identity v2] hook-character scene=1 "
                "object_focus -> silhouette"
            )

    extra = len(variety_idx) - _MAX_VARIETY_SHOTS
    if extra > 0:
        for i in variety_idx[-extra:]:
            if i in locked_abstract:
                continue
            lines[i]["subject_type"] = "woman"
            lines[i]["subject_remap"] = "variety_cap_to_person"


_SECOND_PERSON_RE = re.compile(r"\b(he|him)\b", re.I)
_TRUST_PRESENCE_BEATS = frozenset({4, 5, 6})  # beats 5, 6, 7 (0-based)


def infer_episode_pov_figure(lines: list[dict[str, Any]]) -> str:
    """On-screen speaker from first-person + absent-person pronouns.

    I + her/she (no him/his/he) → remaining partner is a man.
    I + him/his/he (no her/she) → remaining partner is a woman.
    Empty string = no lock; keep Stage 2's per-beat choice.
    """
    blob = " ".join(
        str(r.get("text") or r.get("beat_text") or "")
        for r in (lines or [])
        if isinstance(r, dict)
    )
    has_i = bool(re.search(r"\bI\b", blob))
    has_her = bool(re.search(r"\b(her|she)\b", blob, re.I))
    has_him = bool(re.search(r"\b(him|his|he)\b", blob, re.I))
    if has_i and has_her and not has_him:
        return "man"
    if has_i and has_him and not has_her:
        return "woman"
    return ""


def _align_prose_to_figure(text: str, figure: str) -> str:
    """Rewrite on-screen sex tokens. Does not touch 'her' as the absent person."""
    out = str(text or "")
    if figure == "man":
        out = re.sub(r"\b[Aa] woman\b", "A man", out)
        out = re.sub(r"\b[Tt]he woman\b", "The man", out)
        out = re.sub(r"\b[Hh]er gaze\b", "His gaze", out)
        out = re.sub(
            r"\b[Ss]he (stands|sits|holds|waits|pauses|looks)\b",
            r"He \1",
            out,
        )
    elif figure == "woman":
        out = re.sub(r"\b[Aa] man\b", "A woman", out)
        out = re.sub(r"\b[Tt]he man\b", "The woman", out)
        out = re.sub(r"\b[Hh]is gaze\b", "Her gaze", out)
        out = re.sub(
            r"\b[Hh]e (stands|sits|holds|waits|pauses|looks)\b",
            r"She \1",
            out,
        )
    return out


def apply_episode_cast(lines: list[dict[str, Any]]) -> None:
    """No episode-wide POV/gender lock. Stage 2 chooses per-beat from meaning."""
    del lines
    return


_PALETTE_TEMP_LABEL: dict[str, str] = {
    "WARM": "warm",
    "COLD": "cool",
    "CONTRAST": "contrast",
}
_PALETTE_LIGHTING_BANK: dict[str, tuple[str, ...]] = {
    "WARM": ("sunset_doorway", "indoor_lamp_glow"),
    "COLD": ("overcast_daylight", "morning_cool", "rainy_grey"),
    "CONTRAST": ("blue_hour_streetlight", "sunset_doorway"),
}
_ALT_ENVIRONMENTS: tuple[str, ...] = (
    "empty park path at dusk",
    "rain-streaked window at dusk",
    "empty train platform at night",
    "street corner under a streetlamp",
    "hallway near an open door",
    "window overlooking a quiet street",
    "coastal horizon at dusk",
    "sofa in evening light",
    "bedroom, unmade empty bed",
)
_PORTRAIT_MEANING_RE = re.compile(
    r"\b(i|me|my|myself|alone|lonely|still|stay|face|eyes|remember|miss|"
    r"silence|quiet|empty|grief|hurt|sorry|keep|wait|ache)\b",
    re.I,
)
_COUPLE_MEANING_RE = re.compile(
    r"\b(we|us|our|together|both|they|them|apart|distance|two)\b",
    re.I,
)
_SHOT_CYCLE: tuple[str, ...] = ("wide", "silhouette", "medium", "portrait", "macro")


def beat_shot_type(row: dict[str, Any]) -> str:
    """Canonical shot class for consecutive-variety + Gate 2."""
    if row.get("hook_template"):
        return "hook"
    st = str(row.get("subject_type") or "").strip().lower().replace(" ", "_")
    fr = str(row.get("framing") or "").strip().lower().replace(" ", "_")
    cv = str(row.get("close_variant") or "").strip().lower()
    sil = str(row.get("silhouette_framing") or "").strip().lower()
    if cv == "portrait_close" or fr == "portrait_close":
        return "portrait"
    if st == "object_focus" or fr == "macro_no_setting":
        return "macro"
    if sil == "wide_couple" or "wide" in fr:
        return "wide"
    if st == "silhouette" or cv == "silhouette":
        return "silhouette"
    if st == "couple":
        return "wide"
    return "medium"


def beat_who_label(row: dict[str, Any]) -> str:
    st = str(row.get("subject_type") or "").strip().lower().replace(" ", "_")
    if st in {"woman", "man", "couple", "silhouette"}:
        return st
    if st == "object_focus":
        return "object"
    return "none"


def concept_is_locked(row: dict[str, Any] | None) -> bool:
    return bool(isinstance(row, dict) and row.get("concept_locked"))


_MOTIF_ACTION_RE = re.compile(
    r"\b("
    r"put(?:ting)?\s+it\s+away|"
    r"go\s+by\s+it|"
    r"carry\s+her|"
    r"pack(?:ing)?\s+her\s+away"
    r")\b",
    re.I,
)


def line_requires_spoken_motif(text: str, motif: str) -> bool:
    """True when the spoken line's referent is the episode object (or packing/carrying it)."""
    cap = str(text or "")
    stem = _object_stem(motif)
    if stem and re.search(rf"\b{re.escape(stem)}\b", cap, re.I):
        return True
    return bool(_MOTIF_ACTION_RE.search(cap))


def scene_shows_motif(row: dict[str, Any], motif: str) -> bool:
    if str(row.get("abstract_license") or "").strip():
        return False
    stem = _object_stem(motif)
    if not stem:
        return False
    blob = " ".join(
        str(row.get(k) or "")
        for k in ("scene_description", "visual_concept")
    )
    return bool(re.search(rf"\b{re.escape(stem)}\b", blob, re.I))


def _figure_scene_at_setting(row: dict[str, Any], setting: str) -> str:
    """Full paragraph for a figure beat, written for this place only."""
    st = str(row.get("subject_type") or "").strip().lower().replace(" ", "_")
    shot = stamp_shot_type(row)
    meaning = str(row.get("meaning") or "").strip().rstrip(".")
    motif = str(
        row.get("episode_spoken_motif")
        or row.get("episode_anchor_name")
        or ""
    ).strip()
    text = str(row.get("text") or row.get("beat_text") or "")
    obj = str(row.get("key_object") or "").strip()
    if line_requires_spoken_motif(text, motif):
        obj = motif or obj
        row["key_object"] = obj
    place = setting.strip() or "a quiet printed place"
    who_key = st if st in {"woman", "man"} else "woman"
    if shot == "portrait":
        return portrait_scene_with_locked_appearance(row)
    if shot == "hook":
        return str(row.get("scene_description") or "")
    if shot == "silhouette":
        return silhouette_against_light_scene(row)
    obj_bit = ""
    if line_requires_spoken_motif(text, motif) and obj:
        obj_bit = " " + face_object_scale_clause(obj, who_key)
    who = {
        "woman": "A clothed figure",
        "man": "A clothed figure",
        "couple": "Two clothed figures",
        "silhouette": "A silhouette",
    }.get(st, "A figure")
    if meaning:
        return f"{who} at {place}.{obj_bit} {meaning}."
    return f"{who} at {place}.{obj_bit}"


def rewrite_scene_for_setting(row: dict[str, Any], setting: str) -> None:
    """Regenerate scene prose for a new place. Never append 'now set at'."""
    row["setting"] = setting
    row["episode_place"] = setting
    st = str(row.get("subject_type") or "").strip().lower().replace(" ", "_")
    fr = str(row.get("framing") or "").strip().lower().replace(" ", "_")
    if st == "object_focus" or fr == "macro_no_setting":
        from core.economic_reel_lofi.visual_concept import _enforce_macro_scene

        _enforce_macro_scene(row)
        row["visual_anchor_hint"] = str(row.get("scene_description") or "").split(".")[0]
        return
    desc = _figure_scene_at_setting(row, setting)
    row["scene_description"] = desc
    row["visual_concept"] = desc
    row["visual_anchor_hint"] = desc.split(".")[0]


def stamp_shot_type(row: dict[str, Any]) -> str:
    shot = beat_shot_type(row)
    row["shot_type"] = shot
    row["shot_scale"] = {
        "macro": "close",
        "portrait": "close",
        "medium": "medium",
        "wide": "wide",
        "silhouette": "wide",
    }.get(shot, "medium")
    return shot


def assign_palette_arc(lines: list[dict[str, Any]]) -> None:
    """Warm/cool/contrast by act1/act2/act3; consecutive lighting recipes differ."""
    n = len([r for r in lines if isinstance(r, dict)])
    prev = ""
    assigned: list[str] = []
    temps: list[str] = []
    for i, row in enumerate(lines or []):
        if not isinstance(row, dict):
            continue
        act = act_for_index(i, n)
        row["arc_position"] = act
        key = _EXPECTED_PALETTE_BY_ACT.get(act, "WARM")
        if not str(row.get("palette_key") or "").strip():
            row["palette_key"] = key
        if not str(row.get("palette_temp") or "").strip():
            row["palette_temp"] = _PALETTE_TEMP_LABEL.get(
                str(row.get("palette_key") or key), "warm"
            )
        if concept_is_locked(row):
            prev = str(row.get("lighting_condition") or prev)
            assigned.append(str(row.get("lighting_condition") or ""))
            temps.append(str(row.get("palette_temp") or ""))
            stamp_shot_type(row)
            continue
        row["palette_key"] = key
        row["palette_temp"] = _PALETTE_TEMP_LABEL.get(key, "warm")
        bank = list(_PALETTE_LIGHTING_BANK.get(key) or ("overcast_daylight",))
        cond = bank[i % len(bank)]
        if cond == prev and len(bank) > 1:
            cond = bank[(i + 1) % len(bank)]
        apply_lighting_to_beat(row, cond)
        prev = cond
        assigned.append(cond)
        temps.append(row["palette_temp"])
        stamp_shot_type(row)
    print(
        f"[LOFI palette-arc] temps={temps} lighting={assigned}"
    )


def _scrub_text_bearing_object(row: dict[str, Any]) -> None:
    obj = str(row.get("key_object") or "")
    stem = _object_stem(obj)
    if stem not in _TEXT_BEARING_STEMS and not re.search(
        r"\b(poster|sign|letter|cursive|typography|headline|caption)\b",
        obj,
        re.I,
    ):
        return
    row["key_object"] = "blank framed art"
    row["subject_remap"] = (
        str(row.get("subject_remap") or "") + "+no_text_object"
    ).strip("+")
    print(
        f"[LOFI identity v2] text-object scrub scene={row.get('scene')} "
        f"{obj!r} -> blank framed art"
    )


def ensure_female_melancholic_portrait(lines: list[dict[str, Any]]) -> int:
    """At least one close woman portrait when a beat's meaning supports it."""
    existing = -1
    for i, row in enumerate(lines or []):
        if not isinstance(row, dict):
            continue
        if beat_shot_type(row) == "portrait" and str(
            row.get("subject_type") or ""
        ).strip().lower() == "woman":
            existing = i
            break
    if existing >= 0:
        return existing
    best_i = -1
    best_score = 0
    for i, row in enumerate(lines or []):
        if not isinstance(row, dict):
            continue
        text = str(row.get("text") or row.get("beat_text") or "")
        couple_hits = len(_COUPLE_MEANING_RE.findall(text))
        solo_hits = len(_PORTRAIT_MEANING_RE.findall(text))
        if concept_is_locked(row):
            continue
        if couple_hits >= 2 and solo_hits == 0:
            continue
        score = solo_hits - couple_hits
        if score > best_score:
            best_score = score
            best_i = i
    if best_i < 0 or best_score <= 0:
        print("[LOFI portrait] skip — no beat meaning supports a close woman portrait")
        return -1
    row = lines[best_i]
    row["subject_type"] = "woman"
    row["close_character"] = "woman"
    row["close_variant"] = "portrait_close"
    row["framing"] = "portrait_close"
    row["pose_hint"] = _CLOSE_POSE_HINT["portrait"]
    row["subject_expression"] = str(row.get("subject_expression") or "sad").strip() or "sad"
    row["subject_remap"] = (
        str(row.get("subject_remap") or "") + "+meaning_portrait_woman"
    ).strip("+")
    row["scene_description"] = (
        "A close melancholic portrait of a dark-haired woman, gaze lowered, "
        "quiet sorrow in the face. Head and shoulders in frame. No posters, "
        "signs, or readable letters."
    )
    row["visual_concept"] = row["scene_description"]
    if not str(row.get("meaning") or "").strip():
        row["meaning"] = "solitude held in a face"
    stamp_shot_type(row)
    print(
        f"[LOFI portrait] scene={best_i + 1} woman portrait_close "
        f"score={best_score} line={str(row.get('text') or '')[:48]!r}"
    )
    return best_i


def _apply_shot_class(row: dict[str, Any], shot: str) -> None:
    """Change distance/framing. Keep Stage 2's who unless the shot requires otherwise."""
    st = str(row.get("subject_type") or "").strip().lower().replace(" ", "_")
    if shot == "portrait":
        who = st if st in {"woman", "man"} else "woman"
        row["subject_type"] = who
        row["close_variant"] = "portrait_close"
        row["framing"] = "portrait_close"
        row["close_character"] = who
        row["pose_hint"] = _CLOSE_POSE_HINT["portrait"]
        row["silhouette_framing"] = ""
    elif shot == "macro":
        row["subject_type"] = "object_focus"
        row["framing"] = "macro_no_setting"
        row["close_variant"] = ""
        row["silhouette_framing"] = ""
    elif shot == "silhouette":
        if st not in {"woman", "man", "couple", "silhouette"}:
            row["subject_type"] = "silhouette"
        row["framing"] = "full_scene"
        row["close_variant"] = "silhouette"
        row["silhouette_framing"] = "wide_couple" if row.get("subject_type") == "couple" else ""
    elif shot == "wide":
        if st == "object_focus":
            row["subject_type"] = "silhouette"
            st = "silhouette"
        row["framing"] = "full_scene"
        row["close_variant"] = ""
        if st == "couple":
            row["silhouette_framing"] = "wide_couple"
        else:
            row["silhouette_framing"] = ""
    else:
        if st == "object_focus":
            row["subject_type"] = "silhouette"
        row["framing"] = "full_scene"
        row["close_variant"] = ""
        row["silhouette_framing"] = ""
    stamp_shot_type(row)


def enforce_consecutive_shot_env(lines: list[dict[str, Any]]) -> None:
    """No two consecutive beats share shot distance or environment."""
    prev_shot = ""
    prev_env = ""
    env_i = 0
    motif = episode_spoken_motif(lines)
    for i, row in enumerate(lines or []):
        if not isinstance(row, dict):
            continue
        if row.get("hook_template"):
            prev_shot = stamp_shot_type(row)
            prev_env = _setting_stem(str(row.get("setting") or row.get("episode_place") or ""))
            _scrub_text_bearing_object(row)
            continue
        if row.get("abstract_license"):
            prev_shot = stamp_shot_type(row)
            prev_env = _setting_stem(str(row.get("setting") or row.get("episode_place") or ""))
            continue
        shot = stamp_shot_type(row)
        env = _setting_stem(str(row.get("setting") or row.get("episode_place") or ""))
        text = str(row.get("text") or row.get("beat_text") or "").lower()
        returns = bool(env and re.search(rf"\b{re.escape(env)}\b", text))
        portrait_lock = "+meaning_portrait_woman" in str(row.get("subject_remap") or "")
        motif_macro = (
            shot == "macro"
            and line_requires_spoken_motif(
                str(row.get("text") or row.get("beat_text") or ""), motif
            )
        )
        if prev_shot and shot == prev_shot:
            nxt = (
                _SHOT_CYCLE[(_SHOT_CYCLE.index(shot) + 1) % len(_SHOT_CYCLE)]
                if shot in _SHOT_CYCLE
                else "wide"
            )
            st = str(row.get("subject_type") or "").strip().lower().replace(" ", "_")
            if nxt == "macro" and st in {"woman", "man", "couple", "silhouette"}:
                nxt = "wide"
            if nxt == "portrait" and str(row.get("close_variant") or "") != "portrait_close":
                nxt = "wide"
            if concept_is_locked(row):
                prev_row = lines[i - 1] if i > 0 else None
                if prev_row and not concept_is_locked(prev_row):
                    prev_nxt = (
                        _SHOT_CYCLE[(_SHOT_CYCLE.index(prev_shot) + 1) % len(_SHOT_CYCLE)]
                        if prev_shot in _SHOT_CYCLE
                        else "wide"
                    )
                    _apply_shot_class(prev_row, prev_nxt)
                    rewrite_scene_for_setting(
                        prev_row,
                        str(prev_row.get("setting") or prev_row.get("episode_place") or ""),
                    )
                    prev_shot = stamp_shot_type(prev_row)
                    print(
                        f"[LOFI variety] consecutive-shot scene={i} {shot} -> {prev_shot} "
                        "(kept locked concept)"
                    )
                else:
                    print(
                        f"[LOFI variety] consecutive-shot scene={i + 1} locked — "
                        "leaving approved shot"
                    )
            elif portrait_lock and i > 0:
                prev_row = lines[i - 1]
                prev_nxt = (
                    _SHOT_CYCLE[(_SHOT_CYCLE.index(prev_shot) + 1) % len(_SHOT_CYCLE)]
                    if prev_shot in _SHOT_CYCLE
                    else "wide"
                )
                _apply_shot_class(prev_row, prev_nxt)
                rewrite_scene_for_setting(
                    prev_row,
                    str(prev_row.get("setting") or prev_row.get("episode_place") or ""),
                )
                prev_shot = stamp_shot_type(prev_row)
                print(
                    f"[LOFI variety] consecutive-shot scene={i} {shot} -> {prev_shot} "
                    "(kept required woman portrait)"
                )
            elif motif_macro:
                print(
                    f"[LOFI variety] consecutive-shot scene={i + 1} skip — "
                    "motif-named object-focus stays isolated"
                )
            else:
                _apply_shot_class(row, nxt)
                shot = stamp_shot_type(row)
                rewrite_scene_for_setting(
                    row, str(row.get("setting") or row.get("episode_place") or "")
                )
                print(
                    f"[LOFI variety] consecutive-shot scene={i + 1} {prev_shot} -> {shot}"
                )
        if prev_env and env == prev_env and not returns:
            alt = _ALT_ENVIRONMENTS[env_i % len(_ALT_ENVIRONMENTS)]
            env_i += 1
            if _setting_stem(alt) == prev_env:
                alt = _ALT_ENVIRONMENTS[env_i % len(_ALT_ENVIRONMENTS)]
                env_i += 1
            target = row
            target_i = i
            if concept_is_locked(row):
                prev_row = lines[i - 1] if i > 0 else None
                if prev_row and not concept_is_locked(prev_row):
                    target = prev_row
                    target_i = i - 1
                else:
                    print(
                        f"[LOFI variety] consecutive-env scene={i + 1} locked — "
                        "leaving approved setting"
                    )
                    target = None
            if target is not None:
                tst = str(target.get("subject_type") or "").strip().lower().replace(" ", "_")
                tfr = str(target.get("framing") or "").strip().lower().replace(" ", "_")
                if tst == "object_focus" or tfr == "macro_no_setting":
                    print(
                        f"[LOFI variety] consecutive-env scene={target_i + 1} "
                        "skip — object-focus isolation does not depict a place"
                    )
                else:
                    rewrite_scene_for_setting(target, alt)
                    if target is row:
                        env = _setting_stem(alt)
                    print(
                        f"[LOFI variety] consecutive-env scene={target_i + 1} -> {alt!r} "
                        "(rewrote scene_description)"
                    )
        prev_shot = shot
        prev_env = env
        _scrub_text_bearing_object(row)


def apply_meaning_driven_variety(lines: list[dict[str, Any]]) -> None:
    """Hook template, silhouette discipline, palette arc, consecutive variety."""
    theme = ""
    thesis = ""
    if lines and isinstance(lines[0], dict):
        theme = str(lines[0].get("episode_theme") or "")
        thesis = str(lines[0].get("episode_thesis") or "")
    apply_hook_template(lines, theme=theme, thesis=thesis)
    apply_abstract_licenses(lines)
    apply_figure_discipline(lines)
    assign_palette_arc(lines)
    enforce_consecutive_shot_env(lines)
    apply_figure_discipline(lines)
    apply_object_focus_spoken_motif(lines)
    apply_handheld_motif_beat(lines)
    from core.economic_reel_lofi.visual_concept import _enforce_macro_scene

    for row in lines or []:
        if (
            isinstance(row, dict)
            and not concept_is_locked(row)
            and not row.get("hook_template")
            and not row.get("abstract_license")
        ):
            _enforce_macro_scene(row)
    assign_palette_arc(lines)


_TIME_NOUNS = frozenset(
    {
        "morning", "night", "evening", "afternoon", "dawn", "dusk",
        "today", "tonight", "week", "day", "hour",
    }
)
_OBJECT_ANAPHORA_RE = re.compile(r"\b(it|this|that|them)\b", re.I)


def episode_spoken_motif(lines: list[dict[str, Any]]) -> str:
    """Most frequent spoken object noun that is not a room/time word."""
    counts: dict[str, int] = {}
    for row in lines or []:
        if not isinstance(row, dict):
            continue
        text = str(row.get("text") or row.get("beat_text") or "")
        noun = preferred_concrete_noun(text)
        if (
            not noun
            or noun in _SETTINGISH_NOUNS
            or noun in _SKIP_RENDER_NOUNS
            or noun in _TIME_NOUNS
        ):
            continue
        counts[noun] = counts.get(noun, 0) + 1
    if not counts:
        return ""
    return max(counts, key=counts.get)


def _motif_state_from_caption(text: str, motif: str) -> str:
    cap = str(text or "").lower()
    if re.search(r"\bput (it|them) away\b|\balmost\b", cap):
        return f"{motif} still out on a flat surface"
    if re.search(r"\bleft it\b|\bwashed my own\b", cap):
        return f"{motif} left in place on a flat surface"
    return motif


def apply_object_focus_spoken_motif(lines: list[dict[str, Any]]) -> None:
    """Keep the episode object in prose when the spoken line refers to it."""
    motif = episode_spoken_motif(lines)
    for row in lines or []:
        if isinstance(row, dict):
            row["episode_spoken_motif"] = motif
    if not motif:
        return
    motif_stem = _object_stem(motif)
    for i, row in enumerate(lines or []):
        if not isinstance(row, dict) or concept_is_locked(row) or row.get("hook_template"):
            continue
        if row.get("abstract_license"):
            continue
        st = str(row.get("subject_type") or "").strip().lower().replace(" ", "_")
        text = str(row.get("text") or row.get("beat_text") or "")
        obj = str(row.get("key_object") or "").strip()
        cap = text.lower()
        must_keep = line_requires_spoken_motif(text, motif)
        if not must_keep and st == "object_focus" and motif_stem and motif_stem in cap:
            must_keep = True
        if not must_keep:
            continue
        if _object_stem(obj) != motif_stem:
            row["key_object"] = motif
            row["subject_remap"] = (
                str(row.get("subject_remap") or "") + "+spoken_motif_prose"
            ).strip("+")
            print(
                f"[LOFI identity v2] spoken motif scene={i + 1} "
                f"{obj!r} -> {motif!r}"
            )
        if scene_shows_motif(row, motif):
            continue
        setting = str(row.get("setting") or row.get("episode_place") or "")
        rewrite_scene_for_setting(row, setting)
        print(
            f"[LOFI identity v2] spoke-motif prose scene={i + 1} "
            f"rewrote scene_description for {motif!r}"
        )


BEAT_FUNCTIONS: tuple[str, ...] = (
    "hook",
    "complication",
    "turn",
    "insight",
    "close",
)
_CLOSE_TARGETS: tuple[str, ...] = ("hands", "shoulders", "object")
_CLOSE_POSE_HINT: dict[str, str] = {
    "hands": (
        "Tight crop on empty hands and wrists only — no face in frame."
    ),
    "shoulders": (
        "Tight crop on shoulders and the back of the neck — no face in frame."
    ),
    "object": (
        "Tight crop on the named object in the foreground; figure only a "
        "shoulder or hand edge — no face in frame."
    ),
    "eyes": (
        "Extreme close on eyes only, upper face cropped above the nose "
        "bridge, tearful or reflective expression, rest of face and hair "
        "falling into shadow or out of frame. No nose, no mouth, no chin."
    ),
    "portrait": (
        "Medium close-up portrait, head and shoulders in frame, full face "
        "visible (eyes, nose, mouth, some hair and shoulder). Classic "
        "painterly illustration."
    ),
}
_EYE_CLOSE_SCENE = (
    "Extreme close on a recurring woman's eyes only — long dark hair, "
    "reflective tearful eyes, brow and a fall of hair. The bottom edge of "
    "the frame cuts through the eyebrows so the nose bridge is already "
    "outside the picture. No nose, no nostrils, no mouth, no lips, no "
    "chin, no jaw, no cheeks, no hands, no garment held to the face. "
    "The rest of the face falls into shadow or out of frame. This is a "
    "partial reveal of the eyes only, never a full face, never a portrait."
)


def crop_eye_close_still(path: Path) -> None:
    """Zoom the upper-center 9:16 window so a Flux portrait becomes eyes-only."""
    from PIL import Image

    img = Image.open(path)
    w, h = img.size
    if w < 8 or h < 8:
        return
    crop_h = max(64, int(h * 0.40))
    crop_w = max(36, int(round(crop_h * 9 / 16)))
    if crop_w > w:
        crop_w = w
        crop_h = max(64, int(round(crop_w * 16 / 9)))
    left = max(0, (w - crop_w) // 2)
    top = max(0, int(h * 0.06))
    if top + crop_h > h:
        top = max(0, h - crop_h)
    cropped = img.crop((left, top, left + crop_w, top + crop_h))
    cropped = cropped.resize((w, h), Image.Resampling.LANCZOS)
    cropped.save(path)
    print(f"[LOFI eye-close] post-crop {path.name} box=({left},{top},{crop_w}x{crop_h})")


_AVOID_ANCHOR_STEMS: frozenset[str] = frozenset(
    {
        "paper",
        "mail",
        "letter",
        "envelope",
        "notebook",
        "journal",
        "calendar",
        "list",
        "platform",
        "horizon",
        "footsteps",
        "station",
        "ticket",
    }
)
_SIGNAGE_TRAP_RE = re.compile(
    r"\b(platform|station|newspaper|signage|ticket|timetable)\b",
    re.I,
)


def _remap_signage_traps(
    lines: list[dict[str, Any]],
    pool: list[dict[str, str]],
) -> None:
    """Swap platform/station stills for a caption-tied pair from the safe pool."""
    safe = [
        p
        for p in (pool or [])
        if not _SIGNAGE_TRAP_RE.search(f"{p.get('setting')} {p.get('key_object')}")
    ]
    if not safe:
        safe = [
            dict(p)
            for p in DEFAULT_SETTING_OBJECT_PAIRS
            if not _SIGNAGE_TRAP_RE.search(f"{p['setting']} {p['key_object']}")
        ]
    if not safe:
        return
    used_stems: dict[str, int] = {}
    used_keys: set[tuple[str, str]] = set()
    for row in lines:
        if not isinstance(row, dict):
            continue
        used_keys.add(
            _pair_key(str(row.get("setting") or ""), str(row.get("key_object") or ""))
        )
        stem = _object_stem(str(row.get("key_object") or ""))
        if stem:
            used_stems[stem] = used_stems.get(stem, 0) + 1
    for i, row in enumerate(lines):
        if not isinstance(row, dict):
            continue
        blob = f"{row.get('setting') or ''} {row.get('key_object') or ''}"
        if not _SIGNAGE_TRAP_RE.search(blob):
            continue
        text = str(row.get("text") or row.get("beat_text") or "")
        pick = pick_caption_anchored_pair(
            text, safe, used=used_keys, index=i, used_stems=used_stems
        )
        if _SIGNAGE_TRAP_RE.search(f"{pick.get('setting')} {pick.get('key_object')}"):
            pick = dict(safe[i % len(safe)])
        row["setting"] = pick["setting"]
        row["key_object"] = pick["key_object"]
        row["visual_fallback"] = "signage_trap_remap"
        used_keys.add(_pair_key(pick["setting"], pick["key_object"]))
        stem = _object_stem(pick["key_object"])
        used_stems[stem] = used_stems.get(stem, 0) + 1
        print(
            f"[LOFI identity v2] signage-trap scene={i + 1} "
            f"-> {row['setting']!r} / {row['key_object']!r} text={text!r}"
        )


_9_BEAT_FUNCTIONS: tuple[str, ...] = (
    "hook",
    "complication",
    "turn",
    "insight",
    "complication",
    "turn",
    "insight",
    "close",
    "insight",
)


def default_beat_function(index: int, n: int) -> str:
    """Meaning slot for beat i of n — independent of palette act."""
    if n <= 1:
        return "hook"
    if n == 9 and 0 <= index < 9:
        return _9_BEAT_FUNCTIONS[index]
    t = index / max(1, n - 1)
    if t < 0.12:
        return "hook"
    if t < 0.36:
        return "complication"
    if t < 0.56:
        return "turn"
    if t < 0.78:
        return "insight"
    return "close"


def assign_beat_functions(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Tag each beat hook|complication|turn|insight|close; no consecutive clones."""
    n = len(lines)
    for i, row in enumerate(lines):
        if not isinstance(row, dict):
            continue
        fn = str(row.get("beat_function") or "").strip().lower()
        if fn not in BEAT_FUNCTIONS:
            row["beat_function"] = default_beat_function(i, n)
    for i in range(1, n):
        a = lines[i - 1] if isinstance(lines[i - 1], dict) else {}
        b = lines[i] if isinstance(lines[i], dict) else {}
        if str(a.get("beat_function") or "") != str(b.get("beat_function") or ""):
            continue
        nxt = default_beat_function(i, n)
        if nxt == str(a.get("beat_function") or ""):
            order = list(BEAT_FUNCTIONS)
            cur = str(a.get("beat_function") or "hook")
            nxt = order[(order.index(cur) + 1) % len(order)] if cur in order else "turn"
        b["beat_function"] = nxt
        b["beat_function_remap"] = "consecutive_shift"
    return lines


def _stamp_close_pick(
    row: dict[str, Any],
    pick: dict[str, str],
    *,
    idx: int,
    theme: str,
    module: str,
    seed: str,
) -> None:
    """Apply silhouette or portrait_close to one turn/insight beat. eye_close is retired."""
    from core.economic_reel_lofi import lofi_collections as rag

    variant = str(pick.get("variant") or "silhouette")
    who = str(pick.get("character") or "woman")
    if variant == "eye_close":
        variant = "silhouette"
    st = str(row.get("subject_type") or "").strip().lower().replace(" ", "_")
    if variant == "portrait_close":
        row["subject_type"] = "woman"
        row["close_character"] = row["subject_type"]
        row["shot_scale"] = "close"
        row["close_variant"] = "portrait_close"
        row["close_target"] = "portrait"
        row["eye_close_context"] = ""
        row["pose_hint"] = _CLOSE_POSE_HINT["portrait"]
        print(
            f"[LOFI close] variant=portrait_close scene={idx + 1} "
            f"character={row['close_character']} fn={row.get('beat_function')}"
        )
        return
    target = rag.next_silhouette_close_target(module, seed)
    if st == "object_focus":
        row["subject_type"] = "silhouette"
        row["subject_remap"] = (str(row.get("subject_remap") or "") + "+close_beat").strip("+")
        st = "silhouette"
    row["shot_scale"] = "close"
    row["close_variant"] = "silhouette"
    row["close_character"] = ""
    row["close_target"] = target
    row["eye_close_context"] = ""
    row["pose_hint"] = _CLOSE_POSE_HINT.get(target) or _CLOSE_POSE_HINT["shoulders"]
    print(
        f"[LOFI close] variant=silhouette scene={idx + 1} "
        f"target={target} fn={row.get('beat_function')}"
    )


def assign_close_beat(
    lines: list[dict[str, Any]],
    *,
    theme: str = "",
    module: str = "relationship",
) -> int:
    """
    Optional close on a turn/insight beat.

    Rotate silhouette / portrait_close (eye_close is retired). Portrait is
    required on at least 3 of every 5 episodes. Silhouette-only medium
    scenes are unchanged.
    """
    from core.economic_reel_lofi import lofi_collections as rag

    n = len(lines)
    if n <= 0:
        return -1
    existing: list[int] = []
    for i, row in enumerate(lines):
        if not isinstance(row, dict):
            continue
        if str(row.get("shot_scale") or "").strip().lower() != "close":
            continue
        if str(row.get("beat_function") or "") not in {"turn", "insight"}:
            row["shot_scale"] = "medium"
            row["close_variant"] = ""
            continue
        existing.append(i)
    keep = existing[0] if existing else -1
    for i, row in enumerate(lines):
        if not isinstance(row, dict) or i == keep:
            continue
        if str(row.get("shot_scale") or "").strip().lower() == "close":
            row["shot_scale"] = "medium"
            if str(row.get("close_variant") or "") and i != keep:
                row["close_variant"] = ""
    pick = rag.pick_optional_close(theme, module)
    if not pick:
        if keep >= 0:
            row = lines[keep]
            row["shot_scale"] = "medium"
            row["close_variant"] = ""
            row["close_target"] = ""
            row["pose_hint"] = ""
        print("[LOFI close] none — optional close skipped")
        return -1
    candidates = [
        i
        for i, row in enumerate(lines)
        if isinstance(row, dict)
        and str(row.get("beat_function") or "") in {"turn", "insight"}
        and 1 <= i <= max(1, n - 3)
    ]
    if keep >= 0:
        idx = keep
    elif not candidates:
        print("[LOFI close] none — no turn/insight slot")
        return -1
    else:
        variant = str(pick.get("variant") or "silhouette")
        who = str(pick.get("character") or "woman")
        idx = candidates[0]
        if variant == "portrait_close":
            hits = [
                i
                for i in candidates
                if str(lines[i].get("subject_type") or "").strip().lower() == who
            ]
            idx = hits[0] if hits else candidates[0]
        else:
            for i in candidates:
                st = str(lines[i].get("subject_type") or "").strip().lower()
                if st != "object_focus":
                    idx = i
                    break
    row = lines[idx]
    if not isinstance(row, dict):
        return -1
    seed = f"{theme}|{lines[0].get('text') if isinstance(lines[0], dict) else ''}"
    _stamp_close_pick(row, pick, idx=idx, theme=theme, module=module, seed=seed)
    return idx


def _episode_anchor_stem(lines: list[dict[str, Any]]) -> str:
    if not lines or not isinstance(lines[0], dict):
        return ""
    return str(lines[0].get("episode_anchor_stem") or "").strip().lower()


def stamp_episode_anchor(lines: list[dict[str, Any]], name: str) -> str:
    """Persist the episode motif stem on beat 0 so later enforce passes keep it."""
    stem = _object_stem(name)
    if lines and isinstance(lines[0], dict) and stem:
        lines[0]["episode_anchor_stem"] = stem
        lines[0]["episode_anchor_name"] = str(name or "").strip()
    return stem


def pick_episode_anchor(
    pool: list[dict[str, str]],
    *,
    avoid_stems: set[str] | None = None,
    prefer_name: str = "",
) -> dict[str, str]:
    """Concrete pool object for the episode motif — skip text-bearing traps."""
    avoid = set(avoid_stems or ())
    avoid.update(_AVOID_ANCHOR_STEMS)
    if prefer_name:
        prefer_stem = _object_stem(prefer_name)
        if prefer_stem and prefer_stem not in avoid:
            for p in pool:
                if _object_stem(str(p.get("key_object") or "")) == prefer_stem:
                    return dict(p)
            return {
                "setting": str((pool[0] if pool else {}).get("setting") or "quiet indoor room"),
                "key_object": prefer_name,
            }
    for p in pool:
        stem = _object_stem(str(p.get("key_object") or ""))
        if stem and stem not in avoid:
            return dict(p)
    if pool:
        return dict(pool[0])
    return {"setting": "empty closed room, blank wall", "key_object": "blank wall"}


_ANCHOR_SHAPE_PARTS: dict[str, tuple[str, ...]] = {
    "umbrella": ("canopy", "ribs or spokes", "shaft or pole", "curved handle"),
    "kettle": ("spout", "lid", "handle", "rounded metal body"),
    "suitcase": ("rectangular hard case", "top handle", "latches or corners"),
    "chair": ("seat", "backrest", "four legs"),
    "pillow": ("soft rectangular cushion", "pillowcase", "bed beside it"),
    "plate": ("round dinner plate", "rim", "flat center"),
    "door": ("door slab", "frame or jamb", "threshold"),
    "coat": ("sleeves", "collar", "hanging body of the coat"),
}


def episode_anchor_name(lines_or_beat: list[dict[str, Any]] | dict[str, Any]) -> str:
    """Name stamped on beat 0 or the row itself."""
    if isinstance(lines_or_beat, dict):
        name = str(lines_or_beat.get("episode_anchor_name") or "").strip()
        if name:
            return name
        return ""
    if lines_or_beat and isinstance(lines_or_beat[0], dict):
        return str(lines_or_beat[0].get("episode_anchor_name") or "").strip()
    return ""


def anchor_shape_parts(name: str) -> tuple[str, ...]:
    stem = _object_stem(name)
    if stem in _ANCHOR_SHAPE_PARTS:
        return _ANCHOR_SHAPE_PARTS[stem]
    noun = (name or "").strip() or "the object"
    return (f"the recognizable shape of {noun}",)


def anchor_shape_clause(name: str, *, retry: bool = False) -> str:
    """Concrete parts the model must draw — not the bare noun alone."""
    obj = (name or "").strip() or "the episode object"
    parts = ", ".join(anchor_shape_parts(obj))
    base = (
        f"The {obj} must be drawn as that real object, recognizable by: {parts}. "
        "Not a blanket, comforter, cushion, rolled fabric, or a different still-life."
    )
    if _object_stem(obj) == "umbrella":
        base += " " + umbrella_open_closed_clause()
    if retry:
        base += f" Center the {obj} so those parts fill the frame."
    return base


def umbrella_open_closed_clause() -> str:
    """Caption 'one closed' means one open canopy-up + one furled."""
    return (
        "The dominant object is an OPEN umbrella: circular canopy fully "
        "raised like a dome, ribs visible underneath, standing on wet "
        "pavement. Beside it, a CLOSED umbrella is folded, canopy furled "
        "and strapped to the pole, leaning against the open canopy. "
        "Two umbrellas in different states — one open, one closed. "
        "Not two closed umbrellas. Not two open umbrellas."
    )


def needs_umbrella_open_closed(caption: str = "", object_name: str = "") -> bool:
    blob = f"{caption} {object_name}".lower()
    if _object_stem(object_name) != "umbrella" and "umbrella" not in blob:
        return False
    return bool(re.search(r"one closed|one open|lean together", blob))


def anchor_visual_noun(name: str, caption: str = "") -> str:
    """Prompt noun for an anchor beat — concrete pose, not the motif slogan."""
    if needs_umbrella_open_closed(caption, name):
        return "one open umbrella and one closed folded umbrella"
    return (name or "").strip()


def couple_bed_pose_clause(setting: str, key_object: str, subject_type: str) -> str:
    """Positive + negative framing so a couple is never staged on the mattress."""
    st = (subject_type or "").strip().lower().replace(" ", "_")
    if st != "couple":
        return ""
    blob = f"{setting} {key_object}".lower()
    if not re.search(r"\b(bed|bedroom|mattress)\b", blob):
        return ""
    return (
        "Both figures sit on the edge of the bed with feet planted on the "
        "visible floor, or stand on the floor beside the bedframe with the "
        "mattress behind them. The floor must be visible under their feet. "
        "The bed is furniture they are next to, not a platform they stand on. "
        "Not standing on the mattress, not standing on top of the bed, not "
        "standing at full height on the bedding, not rising out of rumpled "
        "covers as if the mattress is under their feet."
    )


def restore_anchor_visual_fields(lines: list[dict[str, Any]]) -> None:
    """Keep callback/introduce beats on the episode motif, not a retied stand-in.

    The motif noun in metadata is not a shot-list. A window-stem anchor is
    only forced onto a beat whose spoken caption names the window.
    """
    name = episode_anchor_name(lines)
    if not name:
        return
    stem = _object_stem(name)
    intro_setting = ""
    for row in lines:
        if not isinstance(row, dict):
            continue
        if str(row.get("anchor_beat") or "") == "introduce":
            intro_setting = str(row.get("setting") or "").strip()
            break
    for row in lines:
        if not isinstance(row, dict):
            continue
        if str(row.get("anchor_beat") or "") not in {"introduce", "callback"}:
            continue
        cap = str(row.get("text") or row.get("beat_text") or "")
        if stem == "window" and not re.search(r"\bwindows?\b", cap, re.I):
            continue
        if not _anchor_mentioned(cap, name) and str(row.get("anchor_beat") or "") != "introduce":
            continue
        row["key_object"] = name
        row["episode_anchor_name"] = name
        if str(row.get("anchor_beat") or "") == "callback" and intro_setting:
            row["setting"] = intro_setting


MAX_WINDOW_PRIMARY_BEATS = 3
_WINDOW_WORD_RE = re.compile(r"\bwindows?\b", re.I)
_RAIN_WORD_RE = re.compile(r"\brain", re.I)
_WINDOW_DEMOTE_PAIRS: tuple[dict[str, str], ...] = (
    {"setting": "empty closed room, blank wall", "key_object": "blank wall"},
    {"setting": "empty closed room, blank wall", "key_object": "blank wall"},
    {"setting": "empty closed room, blank wall", "key_object": "one table lamp"},
    {"setting": "empty closed room, blank wall", "key_object": "one table lamp"},
    {"setting": "empty street at night", "key_object": "the street"},
)
NO_WINDOW_PANE_CLAUSE = (
    "Blank wall fills the frame from edge to edge."
)
WINDOW_PRIMARY_CLAUSE = (
    "Primary subject is one dry window lit from inside. Facade or exterior. "
    "Clear glass."
)
NO_RAIN_CLAUSE = (
    "Dry air, dry pavement."
)
NO_RAIN_INTERIOR_CLAUSE = "Dry air."
_INTERIOR_WORLD_IDS = frozenset(
    {
        "apartment_night_edge",
        "parked_car_night",
        "kitchen_night_counter",
        "bedroom_night_hall",
    }
)
_EXTERIOR_WORLD_IDS = frozenset(
    {
        "front_stoop_evening",
        "city_edge_night",
    }
)
_INTERIOR_PLACE_RE = re.compile(
    r"\b(bedroom|kitchen|apartment|cabin|hallway|counter|plaster|"
    r"interior|doorway from inside|parked car|same parked car|"
    r"same kitchen|same bedroom|same apartment)\b",
    re.I,
)
_EXTERIOR_PLACE_RE = re.compile(
    r"\b(street|stoop|road|path|pavement|sidewalk|curb|porch|park)\b",
    re.I,
)


def is_interior_beat(beat: dict[str, Any]) -> bool:
    """True for rooms/cabins. Pavement language stays off these frames."""
    world = str(beat.get("episode_world_id") or "").strip()
    if world in _INTERIOR_WORLD_IDS:
        return True
    if world in _EXTERIOR_WORLD_IDS:
        return False
    blob = " ".join(
        (
            str(beat.get("setting") or ""),
            str(beat.get("atmosphere_place") or ""),
            str(beat.get("key_object") or ""),
            str(beat.get("atmosphere_background") or ""),
        )
    )
    if _INTERIOR_PLACE_RE.search(blob):
        return True
    if _EXTERIOR_PLACE_RE.search(blob):
        return False
    return True


def no_rain_clause(beat: dict[str, Any]) -> str:
    """Pavement only on exterior/street beats. Interiors stay dry without pavement."""
    if is_interior_beat(beat):
        return NO_RAIN_INTERIOR_CLAUSE
    return NO_RAIN_CLAUSE
# Named here for the pixel gate only. Do not list these in Flux prompts —
# naming a banned still-life, even negated, still primes the model to draw it.
INVENTED_STILL_LIFE_STEMS: tuple[str, ...] = (
    "kettle", "teapot", "percolator", "mug", "cup", "vase",
    "plant", "flower", "bouquet", "curtain", "blinds", "jar",
)


def spoken_line_licenses_window(beat: dict[str, Any]) -> bool:
    cap = str(beat.get("text") or beat.get("beat_text") or "")
    return bool(re.search(r"\bwindows?\b", cap, re.I) or beat.get("window_primary"))


def spoken_line_licenses_lamp(beat: dict[str, Any]) -> bool:
    cap = str(beat.get("text") or beat.get("beat_text") or "")
    return bool(re.search(r"\b(lit|light|dark)\b", cap, re.I))


def is_figure_only_beat(beat: dict[str, Any]) -> bool:
    """Licensed subject is the person. No extra object to stage the pose against."""
    st = str(beat.get("subject_type") or "").strip().lower().replace(" ", "_")
    if st == "object_focus":
        return False
    if str(beat.get("composition_type") or "") == "wide_environment":
        return False
    nouns = [str(n).strip().lower() for n in (beat.get("licensed_nouns") or [])]
    if nouns == ["figure"]:
        return True
    if st in {"woman", "man"} and str(beat.get("composition_type") or "") == "figure_centered":
        return True
    return False


FIGURE_HANDS_CLAUSE = (
    "Hands out of frame or hidden in sleeves."
)


def figure_composition_guard(beat: dict[str, Any]) -> str:
    """Pose/orientation plus the facing-wall ban. Not an object allow-list."""
    if not is_figure_only_beat(beat) and not beat.get("force_print_silhouette"):
        return ""
    if is_figure_only_beat(beat):
        return (
            f"{FIGURE_ONLY_STAGING_CLAUSE} {FIGURE_NOT_FACING_WALL_CLAUSE} "
            f"{FIGURE_HANDS_CLAUSE}"
        )
    return FIGURE_HANDS_CLAUSE


def spoken_line_prop_guard(beat: dict[str, Any]) -> str:
    """Positive isolation only. Never name banned still-life nouns in the prompt.

    All three no-object compositions share this clause — same strictness as
    a literal object or figure beat. Do not name mug/kettle/window here.
    """
    path = str(beat.get("abstract_license") or "").strip()
    if path == "symbolic":
        obj = str(beat.get("key_object") or "").strip()
        if beat.get("intentional_objectless") or not obj:
            obj = "light and shadow"
        return (
            f"Only this is in the picture: {obj} as an empty gesture, "
            "filling the frame. Empty surface."
        )
    if path == "landscape":
        place = str(beat.get("setting") or "the established place").strip()
        obj = str(beat.get("key_object") or "").strip()
        if obj and not beat.get("intentional_objectless"):
            return (
                f"Only this is in the picture: {place}, with {obj} "
                "as the only named object. Empty of other objects."
            )
        return (
            f"Only this is in the picture: {place} as sky, ground, and "
            "printed color fields. Empty of objects."
        )
    if path == "character":
        st = str(beat.get("subject_type") or "woman").strip().lower()
        who = (
            "two figures in side profile, looking toward off-frame light, empty floor"
            if st == "couple"
            else "the figure in side profile, looking toward off-frame light, empty floor"
        )
        return f"Only this is in the picture: {who}. One subject."
    st = str(beat.get("subject_type") or "").strip().lower().replace(" ", "_")
    object_only = st == "object_focus"
    figure_only = is_figure_only_beat(beat)
    interior = is_interior_beat(beat)
    treat = str(beat.get("composition_treatment") or "")
    if object_only:
        obj = str(beat.get("key_object") or "the object").strip() or "the object"
        allowed = f"{obj} alone, filling the frame"
        isolation = (
            "One object. The picture contains that object alone."
        )
        return f"Only this is in the picture: {allowed}. {isolation}"
    figure_present = st in {"woman", "man", "couple", "silhouette"} or figure_only
    if figure_present:
        if st == "couple":
            allowed = (
                "two figures in side profile or three-quarter, looking toward "
                "off-frame light, empty floor"
            )
        else:
            allowed = (
                "the figure in side profile or three-quarter, looking toward "
                "off-frame light, empty floor"
            )
    elif treat:
        allowed = treat
    elif spoken_line_licenses_window(beat):
        allowed = "one dry window lit from inside, empty sill, blank wall around it"
    elif spoken_line_licenses_lamp(beat):
        allowed = "one lamp as light, blank wall"
    elif (
        not interior
        and str(beat.get("composition_type") or "") == "wide_environment"
    ):
        allowed = "empty open road and sky only"
    else:
        allowed = (
            "the figure in side profile or three-quarter, looking toward "
            "off-frame light, empty floor"
        )
    if interior:
        isolation = "Empty floor. One subject."
    elif figure_only:
        isolation = "Empty floor or open sky. One subject."
    else:
        isolation = "Blank wall, empty floor or open sky. One subject."
    if st == "couple":
        isolation = isolation.replace("One subject.", "Two people.")
    return f"Only this is in the picture: {allowed}. {isolation}"


_HOPE_SPINE_PATH = _PACKAGE_DIR / "data" / "hope_causal_spine.json"
_HOPE_SPINE_CACHE: dict[str, Any] | None = None


def load_hope_causal_spine() -> dict[str, Any]:
    """Spoken-noun license + per-beat composition spec (writer and visual share this)."""
    global _HOPE_SPINE_CACHE
    if _HOPE_SPINE_CACHE is not None:
        return _HOPE_SPINE_CACHE
    data = json.loads(_HOPE_SPINE_PATH.read_text(encoding="utf-8"))
    _HOPE_SPINE_CACHE = data
    return data


def hope_beat_spec(scene: int) -> dict[str, Any]:
    for row in load_hope_causal_spine().get("beats") or []:
        if int(row.get("beat") or 0) == int(scene):
            return dict(row)
    return {}


def apply_hope_composition_spec(beat: dict[str, Any], scene: int) -> None:
    spec = hope_beat_spec(scene)
    if not spec:
        return
    beat["composition_angle"] = spec.get("angle") or ""
    beat["composition_distance"] = spec.get("distance") or ""
    beat["light_direction"] = spec.get("light_direction") or beat.get("light_direction") or ""
    beat["color_temperature"] = spec.get("color_temperature") or beat.get("color_temperature") or ""
    beat["composition_treatment"] = spec.get("treatment") or ""
    beat["licensed_nouns"] = list(spec.get("licensed_nouns") or [])
    scale = str(spec.get("angle") or "")
    if scale and not str(beat.get("shot_scale") or "").strip():
        beat["shot_scale"] = scale


def composition_spec_clause(beat: dict[str, Any]) -> str:
    angle = str(beat.get("composition_angle") or "").strip()
    dist = str(beat.get("composition_distance") or "").strip()
    light = str(beat.get("light_direction") or "").strip()
    temp = str(beat.get("color_temperature") or "").strip()
    treat = str(beat.get("composition_treatment") or "").strip()
    bits = [p for p in (
        f"Angle: {angle}." if angle else "",
        f"Distance: {dist}." if dist else "",
        f"Light: {light}." if light else "",
        f"Color: {temp}." if temp else "",
        treat,
    ) if p]
    return " ".join(bits)


def _beat_names_window(row: dict[str, Any]) -> bool:
    cap = str(row.get("text") or row.get("beat_text") or "")
    return bool(_WINDOW_WORD_RE.search(cap))


def _beat_visual_is_window_primary(row: dict[str, Any]) -> bool:
    blob = f"{row.get('setting') or ''} {row.get('key_object') or ''} {row.get('pose_hint') or ''}"
    return bool(_WINDOW_WORD_RE.search(blob))


def cap_window_primary_beats(lines: list[dict[str, Any]]) -> None:
    """Window is a narrative anchor, not a shot-list. Cap pane-framed stills."""
    indexed: list[tuple[int, bool, dict[str, Any]]] = []
    for i, row in enumerate(lines):
        if not isinstance(row, dict):
            continue
        if _beat_visual_is_window_primary(row):
            indexed.append((i, _beat_names_window(row), row))
    literal = [i for i, lit, _ in indexed if lit]
    keep = set(literal[:MAX_WINDOW_PRIMARY_BEATS])
    demote_n = 0
    for i, lit, row in indexed:
        if i in keep:
            row["window_primary"] = True
            if not _RAIN_WORD_RE.search(str(row.get("text") or row.get("beat_text") or "")):
                row["setting"] = re.sub(
                    r"rain-streaked\s*", "", str(row.get("setting") or ""), flags=re.I
                ).strip()
                if _RAIN_WORD_RE.search(str(row.get("key_object") or "")):
                    row["key_object"] = "one window still lit"
            print(f"[LOFI window-cap] keep scene={i + 1} literal={int(lit)}")
            continue
        alt = _WINDOW_DEMOTE_PAIRS[demote_n % len(_WINDOW_DEMOTE_PAIRS)]
        demote_n += 1
        row["window_primary"] = False
        row["setting"] = alt["setting"]
        row["key_object"] = alt["key_object"]
        if str(row.get("anchor_beat") or "") in {"introduce", "callback"}:
            row["anchor_beat"] = ""
        print(
            f"[LOFI window-cap] demote scene={i + 1} "
            f"-> setting={alt['setting']!r} object={alt['key_object']!r}"
        )
    for row in lines:
        if isinstance(row, dict) and "window_primary" not in row:
            row["window_primary"] = False


def constrain_unspoken_support_props(lines: list[dict[str, Any]]) -> None:
    """Drop RAG-seed still-life (kettle, curtains, window) the caption does not name."""
    for row in lines:
        if not isinstance(row, dict):
            continue
        obj = str(row.get("key_object") or "")
        setting = str(row.get("setting") or "")
        stem = _object_stem(obj)
        blob = f"{obj} {setting}".lower()
        invented = any(s in blob for s in INVENTED_STILL_LIFE_STEMS)
        cap = str(row.get("text") or row.get("beat_text") or "").lower()
        spoken_still = bool(stem) and stem in cap
        place_stem = (
            stem in _SETTING_PLACE_STEMS
            or stem in _SETTINGISH_NOUNS
        )
        window_leak = _WINDOW_WORD_RE.search(blob) and not spoken_line_licenses_window(row)
        if place_stem and stem not in cap and not spoken_still:
            row["key_object"] = "blank wall"
            print(
                f"[LOFI spoken-prop] scene={row.get('scene')} "
                f"dropped place-object {obj!r} -> {row['key_object']!r}"
            )
            continue
        if (not invented or spoken_still) and not window_leak:
            continue
        if spoken_line_licenses_lamp(row):
            row["setting"] = "empty closed room, blank wall"
            row["key_object"] = "one lamp"
        elif re.search(
            r"\bstreet\b|\bmorning\b",
            str(row.get("text") or row.get("beat_text") or ""),
            re.I,
        ):
            row["setting"] = "open empty road under the sky"
            row["key_object"] = "empty road"
        else:
            row["setting"] = "empty closed room, blank wall"
            row["key_object"] = "blank wall"
        row["window_primary"] = False
        print(
            f"[LOFI spoken-prop] scene={row.get('scene')} "
            f"dropped {obj!r} -> {row['key_object']!r}"
        )


def _anchor_mentioned(text: str, name: str) -> bool:
    blob = (text or "").lower()
    stem = _object_stem(name)
    if stem and stem in blob:
        return True
    skip = {
        "two", "one", "not", "just", "empty", "unused", "closed", "open",
        "the", "and", "for",
    }
    words = [
        w
        for w in re.findall(r"[a-z]+", (name or "").lower())
        if w not in _STOP_VISUAL and w not in skip and len(w) > 2
    ]
    return bool(words) and any(w in blob for w in words)


def apply_anchor_callback_beats(
    lines: list[dict[str, Any]],
    anchor: dict[str, str],
    pool: list[dict[str, str]] | None = None,
) -> list[int]:
    """
    Same motif on two beats: introduce early/mid, callback late, different state.

    Mentions are caption-level. Visual key_object is aligned on those two beats
    so the stem cap exemption can keep the callback.
    """
    del pool
    name = str((anchor or {}).get("name") or (anchor or {}).get("key_object") or "").strip()
    if not name or not lines:
        return []
    stem = stamp_episode_anchor(lines, name)
    setting = str((anchor or {}).get("setting") or "").strip()
    n = len(lines)
    mentioned = [
        i
        for i, row in enumerate(lines)
        if isinstance(row, dict)
        and _anchor_mentioned(str(row.get("text") or row.get("beat_text") or ""), name)
    ]
    early = [i for i in mentioned if i <= max(1, min(4, n - 3))]
    late = [i for i in mentioned if i >= max(4, n - 3)]
    intro_i = early[0] if early else min(1, n - 1)
    callback_i = late[-1] if late else max(n - 2, intro_i + 1)
    if callback_i <= intro_i:
        callback_i = max(n - 2, min(n - 1, intro_i + 3))
    if intro_i >= max(4, n - 4):
        intro_i = min(1, n - 1)
    initial = str((anchor or {}).get("initial_state") or "just used").strip()
    final = str((anchor or {}).get("final_state") or "cold, untouched").strip()
    for i, state in ((intro_i, initial), (callback_i, final)):
        if i < 0 or i >= n or not isinstance(lines[i], dict):
            continue
        row = lines[i]
        if setting and not _SIGNAGE_TRAP_RE.search(setting):
            row["setting"] = setting
        elif _SIGNAGE_TRAP_RE.search(str(row.get("setting") or "")):
            row["setting"] = "hallway with a closed suitcase on the floor"
        row["key_object"] = name
        hint = f"{name}, {state}"
        row["visual_anchor_hint"] = hint
        text = str(row.get("text") or row.get("beat_text") or "").strip()
        fn = str(row.get("beat_function") or "")
        if (i == 0 or fn in {"hook", "insight"}) and not _anchor_mentioned(text, name):
            row["anchor_beat"] = "introduce" if i == intro_i else "callback"
            continue
        if not _anchor_mentioned(text, name):
            noun = stem or name.split()[-1]
            addon = f"the {noun}"
            if i == callback_i:
                addon = f"the {noun} now {final.split(',')[0].strip()}"
            cand = f"{text.rstrip('.')} — {addon}."
            if len(cand.split()) <= 9 and len(cand) <= 56:
                row["text"] = cand
                row["beat_text"] = cand
            else:
                short = (
                    f"The {noun} has gone cold."
                    if i == callback_i
                    else f"You notice the {noun}."
                )
                row["text"] = short
                row["beat_text"] = short
        row["anchor_beat"] = "introduce" if i == intro_i else "callback"
    return [intro_i, callback_i]


def _candidate_pairs(
    pool: list[dict[str, str]],
    cluster: dict[str, Any] | None,
) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    sources: list[Any] = [pool]
    if cluster:
        sources.append(cluster.get("pairs") or ())
    for extra in _MEANING_CLUSTERS:
        sources.append(extra.get("pairs") or ())
    sources.extend((_VARIETY_PAIRS, DEFAULT_SETTING_OBJECT_PAIRS))
    for src in sources:
        for p in src:
            if not isinstance(p, dict):
                continue
            setting = str(p.get("setting") or "").strip()
            obj = str(p.get("key_object") or "").strip()
            if not setting or not obj:
                continue
            key = _pair_key(setting, obj)
            if key in seen:
                continue
            seen.add(key)
            out.append({"setting": setting, "key_object": obj})
    return out


def _object_from_caption(
    text: str,
    *,
    avoid_stems: set[str] | None = None,
) -> str:
    """Literal caption noun or a mapped stand-in — never a bare verb."""
    avoid_stems = avoid_stems or set()
    noun = preferred_concrete_noun(text)
    if noun:
        aliases = _CAPTION_OBJECT_ALIASES.get(noun, ())
        for alias in aliases:
            if _object_stem(alias) not in avoid_stems:
                return alias
        if aliases:
            return aliases[0]
        if _object_stem(noun) not in avoid_stems:
            return noun
        return noun
    for w in re.findall(r"[a-z0-9']+", (text or "").lower()):
        if w in _CAPTION_OBJECT_ALIASES:
            for alias in _CAPTION_OBJECT_ALIASES[w]:
                if _object_stem(alias) not in avoid_stems:
                    return alias
            return _CAPTION_OBJECT_ALIASES[w][0]
    for p in _VARIETY_PAIRS:
        if _object_stem(p["key_object"]) not in avoid_stems:
            return p["key_object"]
    return "blank wall"


def pick_caption_anchored_pair(
    text: str,
    pool: list[dict[str, str]],
    *,
    used: set[tuple[str, str]] | None = None,
    index: int = 0,
    used_stems: dict[str, int] | None = None,
) -> dict[str, str]:
    """Pick a pool pair that traces to this caption, avoiding overused stems.

    Abstract cluster hits (finally, costs, noticed) do not mint a prop.
    They return a blank-wall environment; lighting lives on the cluster
    treatment.
    """
    del index
    cluster = _best_meaning_cluster(text)
    noun = preferred_concrete_noun(text)
    if cluster and not noun:
        # Third path: no nameable object. Pair is filled later by
        # apply_abstract_licenses (symbolic / landscape / character).
        treat = dict(cluster.get("treatment") or {})
        return {
            "setting": str(treat.get("setting") or "empty closed room, blank wall"),
            "key_object": str(treat.get("key_object") or "blank wall"),
            "abstract_pending": True,
        }
    pairs = _candidate_pairs(pool, cluster)
    used = used or set()
    used_stems = used_stems or {}
    avoid = {
        stem for stem, n in used_stems.items() if n >= _MAX_OBJECT_STEM_HITS
    }
    scored: list[tuple[int, dict[str, str]]] = []
    for p in pairs:
        if _is_banned_key_object(p["key_object"]) or _BANNED_OBJECT_RE.search(p["setting"]):
            continue
        score = _score_pair_for_caption(text, p["setting"], p["key_object"])
        key = _pair_key(p["setting"], p["key_object"])
        stem = _object_stem(p["key_object"])
        if key in used:
            score -= 1
        if stem in avoid:
            score -= 8
        scored.append((score, p))
    scored.sort(key=lambda x: x[0], reverse=True)
    if scored and scored[0][0] > 0:
        pick = dict(scored[0][1])
        if _object_stem(pick["key_object"]) not in avoid or not avoid:
            return pick
        for score, p in scored:
            if score > 0 and _object_stem(p["key_object"]) not in avoid:
                return dict(p)
        return pick
    if cluster:
        for p in cluster.get("pairs") or ():
            stem = _object_stem(str(p.get("key_object") or ""))
            key = _pair_key(str(p.get("setting") or ""), str(p.get("key_object") or ""))
            if stem in avoid or key in used:
                continue
            return dict(p)
    for p in _VARIETY_PAIRS:
        stem = _object_stem(p["key_object"])
        key = _pair_key(p["setting"], p["key_object"])
        if stem in avoid or key in used:
            continue
        return dict(p)
    obj = _object_from_caption(text, avoid_stems=avoid)
    if _is_banned_key_object(obj):
        for p in DEFAULT_SETTING_OBJECT_PAIRS:
            if _object_stem(p["key_object"]) not in avoid:
                return dict(p)
        return dict(DEFAULT_SETTING_OBJECT_PAIRS[0])
    needle = obj.lower()
    for p in pairs:
        blob = f"{p['setting']} {p['key_object']}".lower()
        if needle in blob and _object_stem(p["key_object"]) not in avoid:
            return dict(p)
        for alias in _CAPTION_OBJECT_ALIASES.get(preferred_concrete_noun(text), ()):
            if alias.lower() in blob and _object_stem(p["key_object"]) not in avoid:
                return dict(p)
    setting = "quiet indoor room"
    if "door" in needle or needle in {"letter", "envelope"}:
        setting = "quiet indoor doorway"
    elif "paper" in needle or "mail" in needle:
        setting = "empty closed room, blank wall"
    elif "chair" in needle or "seat" in needle:
        setting = "empty closed room, blank wall"
    return {"setting": setting, "key_object": obj}


ABSTRACT_LICENSE_PATHS: tuple[str, ...] = ("symbolic", "landscape", "character")
_ABSTRACT_KEY_OBJECTS: frozenset[str] = frozenset(
    {
        "figure",
        "open hand",
        "empty chair",
        "light and shadow",
        "open sky and empty ground",
        "blank wall",
    }
)
_SYMBOLIC_GESTURES: tuple[tuple[str, str], ...] = (
    (
        "open hand",
        "An empty open hand in negative space, light falling across the palm. "
        "No other object attached, no figure beyond the hand itself.",
    ),
    (
        "empty chair",
        "An empty chair in a field of light and shadow, nothing sitting on it "
        "or placed beside it. No invented prop.",
    ),
    (
        "light and shadow",
        "Light, shadow, and negative space only — an empty gesture of "
        "illumination. No figure, no named object.",
    ),
)
_SPOKEN_FOCUS_EXTRA_RE = re.compile(
    r"\b(cars?|dogs?|weather|breath|line|lines)\b",
    re.I,
)
_FOCUS_SKIP_BODY = frozenset(
    {
        "hand",
        "hands",
        "finger",
        "fingers",
        "wrist",
        "wrists",
        "face",
        "faces",
        "sleeve",
        "sleeves",
    }
)
_FOCUS_SKIP_OPENING = frozenset(
    {
        "window",
        "windows",
        "doorway",
        "doorways",
        "curtain",
        "curtains",
        "door",
        "doors",
    }
)
_ABSTRACT_PERSON_RE = re.compile(
    r"\b(you|your|i|me|my|we|us|our|she|her|he|him|his|they|them|their)\b",
    re.I,
)
_ABSTRACT_BODY_RE = re.compile(
    r"\b(face|eyes|hands?|chest|breath|voice|body|shoulder|look|looking|"
    r"sit|sitting|stand|standing|quiet|silence|hurt|ache|sorry|miss)\b",
    re.I,
)
_ABSTRACT_PLACE_RE = re.compile(
    r"\b(sky|street|room|road|window|rain|night|morning|dusk|weather|"
    r"outside|inside|world|place|city)\b",
    re.I,
)
_ABSTRACT_SECOND_RE = re.compile(r"\b(he|him|his|she|her|they|them|we|us|our|both)\b", re.I)


def line_has_nameable_object(text: str) -> bool:
    """True when the spoken line names a concrete prop that may appear on screen.

    Setting/place/time words and body parts do not count — those belong to the
    abstract third path, not to a minted object.
    """
    noun = preferred_concrete_noun(text)
    if not noun or noun in _SKIP_RENDER_NOUNS or noun in _TIME_NOUNS:
        return False
    if noun in _SETTINGISH_NOUNS:
        return False
    if _is_banned_key_object(noun):
        return False
    return True


def _spoken_focus_noun(text: str) -> str:
    """Concrete noun already in the spoken line, preferred for abstract key_object."""
    found = [m.group(0).lower() for m in _CONCRETE_NOUN_RE.finditer(text or "")]
    found.extend(m.group(0).lower() for m in _SPOKEN_FOCUS_EXTRA_RE.finditer(text or ""))
    seen: list[str] = []
    for noun in found:
        if (
            noun in _SETTINGISH_NOUNS
            or noun in _TIME_NOUNS
            or noun in _FOCUS_SKIP_BODY
        ):
            continue
        if noun not in seen:
            seen.append(noun)
    preferred = [
        n for n in seen if n not in _SKIP_RENDER_NOUNS and n not in _FOCUS_SKIP_OPENING
    ]
    if preferred:
        return preferred[0]
    for noun in seen:
        if noun not in _FOCUS_SKIP_OPENING:
            return noun
    return seen[0] if seen else ""


def _prior_licensed_focus_object(
    lines: list[dict[str, Any]] | None,
    current: dict[str, Any],
) -> str:
    """Last earlier beat's real object — not a stock abstract placeholder."""
    for row in lines or []:
        if row is current:
            break
        if not isinstance(row, dict):
            continue
        if row.get("intentional_objectless"):
            continue
        if row.get("hook_template"):
            continue
        ko = str(row.get("key_object") or "").strip()
        low = ko.lower()
        if not ko or low in _ABSTRACT_KEY_OBJECTS:
            continue
        if "particle" in low or low in {"silence", "weather"}:
            continue
        return ko
    return ""


def _abstract_focus_scene(
    path: str,
    *,
    obj: str,
    place: str,
    tod: str,
) -> str:
    if path == "symbolic":
        if obj:
            return (
                f"{obj} alone in negative space, filling the frame as a "
                "printed shape. Empty surface around it."
            )
        return (
            "Light, shadow, and negative space only — an empty gesture of "
            "illumination. No figure, no named object."
        )
    if obj:
        return (
            f"Wide view of {place}, {tod}. {obj} is the only named object, "
            "small in the space as a printed shape. Empty ground or blank walls."
        )
    return (
        f"Wide view of {place}, {tod}. "
        "Light, weather, and time of day carry the feeling of the "
        "established place. Empty ground, sky, or blank walls as "
        "printed color fields."
    )


def _pick_abstract_path(
    text: str,
    *,
    prev: str = "",
    used: dict[str, int] | None = None,
) -> str:
    """Choose symbolic / landscape / character. Never the same as the previous beat."""
    used = used or {}
    scores = {p: 0.0 for p in ABSTRACT_LICENSE_PATHS}
    if _ABSTRACT_BODY_RE.search(text) or _ABSTRACT_PERSON_RE.search(text):
        scores["character"] += 2.0
    if _ABSTRACT_PLACE_RE.search(text) or any(
        n in _SETTINGISH_NOUNS for n in re.findall(r"[a-z]+", (text or "").lower())
        if n in _SETTINGISH_NOUNS
    ):
        scores["landscape"] += 2.0
    if not _ABSTRACT_PERSON_RE.search(text):
        scores["symbolic"] += 1.5
    else:
        scores["symbolic"] += 0.5
    if prev in scores:
        scores[prev] -= 10.0
    for path, n in used.items():
        if path in scores:
            scores[path] -= float(n)
    return max(scores, key=lambda p: (scores[p], -used.get(p, 0)))


def _abstract_character_who(text: str, lines: list[dict[str, Any]]) -> str:
    blob = " ".join(
        str(r.get("text") or r.get("beat_text") or "")
        for r in (lines or [])
        if isinstance(r, dict)
    )
    has_second = bool(_ABSTRACT_SECOND_RE.search(blob))
    if has_second and re.search(r"\b(we|us|our|both|together|they|them)\b", text, re.I):
        return "couple"
    pov = infer_episode_pov_figure(lines)
    if pov in {"woman", "man"}:
        return pov
    return "woman"


def apply_abstract_license(
    row: dict[str, Any],
    path: str,
    *,
    lines: list[dict[str, Any]] | None = None,
    gesture_index: int = 0,
) -> None:
    """Stamp one of the three no-object compositions. Never invents a named prop."""
    text = str(row.get("text") or row.get("beat_text") or "")
    setting = str(
        row.get("setting")
        or row.get("atmosphere_place")
        or row.get("episode_place")
        or "a quiet indoor room at night"
    ).strip()
    tod = str(row.get("time_of_day") or "night")
    cluster = _best_meaning_cluster(text)
    treat = dict((cluster or {}).get("treatment") or {})
    if treat.get("lighting_condition") and not str(row.get("lighting_condition") or "").strip():
        row["lighting_condition"] = treat["lighting_condition"]
    row["abstract_license"] = path
    row["licensed_objects"] = []
    row["visual_fallback"] = f"abstract_{path}"
    spoken = _spoken_focus_noun(text)
    prior = _prior_licensed_focus_object(lines, row)
    focus_obj = spoken or prior
    if path == "symbolic":
        row["subject_type"] = "object_focus"
        row["framing"] = "macro_no_setting"
        row["composition_type"] = "object_focus"
        row["close_variant"] = ""
        row["setting"] = "empty surface, negative space, printed color fields"
        row["pose_hint"] = "empty gesture"
        if focus_obj:
            row["key_object"] = focus_obj
            row["licensed_nouns"] = [focus_obj]
            row["intentional_objectless"] = False
            row["object_continuity"] = "caption_named" if spoken else "prior_object"
        else:
            row["key_object"] = ""
            row["licensed_nouns"] = []
            row["intentional_objectless"] = True
            row["object_continuity"] = "intentional_objectless"
        scene = _abstract_focus_scene(
            "symbolic", obj=focus_obj, place=row["setting"], tod=tod
        )
        row["scene_description"] = scene
        row["visual_concept"] = scene
        if not str(row.get("subject_expression") or "").strip():
            row["subject_expression"] = str((cluster or {}).get("expression") or "quiet")
    elif path == "landscape":
        interior = is_interior_beat({"setting": setting, "key_object": "", "text": text})
        if interior:
            place = "empty closed room, blank walls, solid color fields"
        else:
            place = "open ground and sky, empty of figures"
        row["subject_type"] = "object_focus"
        row["framing"] = "full_scene"
        row["composition_type"] = "wide_environment"
        row["close_variant"] = ""
        row["silhouette_framing"] = ""
        row["shot_scale"] = "wide"
        row["pose_hint"] = ""
        row["setting"] = place
        if focus_obj:
            row["key_object"] = focus_obj
            row["licensed_nouns"] = [focus_obj]
            row["intentional_objectless"] = False
            row["object_continuity"] = "caption_named" if spoken else "prior_object"
        else:
            row["key_object"] = ""
            row["licensed_nouns"] = []
            row["intentional_objectless"] = True
            row["object_continuity"] = "intentional_objectless"
        scene = _abstract_focus_scene(
            "landscape", obj=focus_obj, place=place, tod=tod
        )
        row["scene_description"] = scene
        row["visual_concept"] = scene
        if not str(row.get("subject_expression") or "").strip():
            row["subject_expression"] = "still"
    else:
        who = _abstract_character_who(text, lines or [row])
        expr = str(
            row.get("subject_expression")
            or (cluster or {}).get("expression")
            or "quiet, held"
        )
        row["subject_type"] = who
        row["close_character"] = who if who in {"woman", "man"} else "woman"
        row["framing"] = "portrait_close" if who != "couple" else "full_scene"
        row["close_variant"] = "portrait_close" if who != "couple" else ""
        row["composition_type"] = "figure_centered"
        row["key_object"] = "figure"
        row["licensed_nouns"] = ["figure"]
        row["intentional_objectless"] = False
        row["object_continuity"] = ""
        row["subject_expression"] = expr
        row["pose_hint"] = (
            "Camera in on expression, posture, body language. Hands empty. "
            "No named object in frame."
        )
        row["scene_description"] = (
            f"The {who} framed for the feeling of this line — expression and "
            f"posture only, in {setting}. No named prop."
        )
        row["visual_concept"] = row["scene_description"]
        row["setting"] = setting
    stamp_shot_type(row)
    from core.economic_reel_lofi.licensed_objects import enforce_licensed_inventory

    enforce_licensed_inventory(row)
    print(
        f"[LOFI abstract] scene={row.get('scene') or '?'} path={path} "
        f"who={row.get('subject_type')} object={row.get('key_object')!r}"
    )


def apply_abstract_licenses(lines: list[dict[str, Any]]) -> None:
    """Third licensing path: no nameable object → symbolic / landscape / character.

    Picks whichever of the three best fits the line, never repeating the same
    choice back-to-back, and never inventing a named prop the line did not speak.
    """
    prev = ""
    used: dict[str, int] = {}
    gesture_i = 0
    for row in lines or []:
        if not isinstance(row, dict):
            continue
        if row.get("hook_template") or concept_is_locked(row):
            prev = str(row.get("abstract_license") or prev)
            continue
        if row.get("abstract_license"):
            prev = str(row.get("abstract_license") or prev)
            used[prev] = used.get(prev, 0) + 1
            continue
        text = str(row.get("text") or row.get("beat_text") or "")
        if line_has_nameable_object(text):
            row.pop("abstract_license", None)
            prev = ""
            continue
        path = _pick_abstract_path(text, prev=prev, used=used)
        apply_abstract_license(
            row, path, lines=lines, gesture_index=gesture_i
        )
        if path == "symbolic":
            gesture_i += 1
        used[path] = used.get(path, 0) + 1
        prev = path


def restamp_abstract_licenses(lines: list[dict[str, Any]]) -> None:
    """Re-apply spoken-noun / prior-object / objectless stamps in episode order."""
    gesture_i = 0
    for row in lines or []:
        if not isinstance(row, dict):
            continue
        path = str(row.get("abstract_license") or "").strip()
        if path not in ABSTRACT_LICENSE_PATHS:
            continue
        apply_abstract_license(
            row, path, lines=lines, gesture_index=gesture_i
        )
        if path == "symbolic":
            gesture_i += 1


def _apply_cluster_pose(row: dict[str, Any], text: str) -> None:
    cluster = _best_meaning_cluster(text)
    if not cluster:
        return
    pose = str(cluster.get("pose") or "").strip()
    if pose:
        row["pose_hint"] = pose
    expr = str(cluster.get("expression") or "").strip()
    if expr and not str(row.get("subject_expression") or "").strip():
        row["subject_expression"] = expr
    treat = dict(cluster.get("treatment") or {})
    if treat.get("lighting_condition") and not str(row.get("lighting_condition") or "").strip():
        row["lighting_condition"] = treat["lighting_condition"]
    if treat.get("shot_scale") and not str(row.get("shot_scale") or "").strip():
        row["shot_scale"] = treat["shot_scale"]
    if treat.get("light_direction"):
        row["light_direction"] = treat["light_direction"]
    if treat.get("color_temperature"):
        row["color_temperature"] = treat["color_temperature"]


_SKIP_RENDER_NOUNS: frozenset[str] = frozenset(
    {"hand", "hands", "finger", "fingers", "wrist", "wrists", "phone", "phones"}
)
_ATMOS_ANGLES: tuple[str, ...] = (
    "wide",
    "medium",
    "medium-close",
    "high angle",
    "low angle",
    "profile from the side",
    "from across the room",
    "over the threshold",
    "close on the motif",
)
_ATMOS_DISTANCES: tuple[str, ...] = (
    "figure small in the place",
    "full-figure",
    "head and shoulders",
    "the room filling the frame",
    "motif as subject",
)
_ATMOS_TIMES: tuple[str, ...] = (
    "night",
    "late night",
    "blue hour",
    "night",
    "late night",
    "night",
    "blue hour",
    "night",
    "late night",
)
# RETIRED as the source of setting/object (audit 20260825). Stage 2 LLM
# translation owns place/object. apply_v2_prompts_to_lines_dev skips this
# table when visual_concept is present. Kept only so Schnell / lock_visuals
# fallbacks do not NameError. Do not add new worlds here.
_EPISODE_WORLDS: tuple[dict[str, Any], ...] = (
    {
        "id": "apartment_night_edge",
        "place": "a quiet apartment at the edge of a city at night",
        "mood": "still, withheld, lamp-warm hush",
        "light_quality": "one warm indoor lamp against cool night",
        "time": "night",
        "ambient_object": "blank wall",
        "lighting_condition": "indoor_lamp_glow",
        "setting_archetype": "threshold",
        "tokens": frozenset(
            {
                "door", "inside", "closed", "no", "room", "wall", "hand",
                "draft", "apartment", "lamp", "window", "maybe",
            }
        ),
        "zones": (
            {
                "setting": "apartment doorway from inside at night",
                "background": "dark hall, city as a thin hush beyond the jamb",
            },
            {
                "setting": "same apartment doorway, figure standing just inside",
                "background": "the closed door and the hall behind it",
            },
            {
                "setting": "same apartment, looking toward the closed door",
                "background": "plaster wall, lamp spill, no other rooms",
            },
            {
                "setting": "same apartment, a table in that room at night",
                "background": "the doorway still in the far background",
            },
            {
                "setting": "same apartment plaster wall at night",
                "background": "lamp spill, the door out of frame but implied",
            },
            {
                "setting": "same apartment doorway, side angle",
                "background": "jamb and the dark of the next room",
            },
            {
                "setting": "same apartment, deeper in the room facing the door",
                "background": "the closed door small in the distance",
            },
            {
                "setting": "same apartment at night, figure in the doorway",
                "background": "lamp behind, city only as a distant quiet",
            },
            {
                "setting": "apartment doorway from inside at night",
                "background": "the same closed door, closer than the opening beat",
            },
        ),
    },
    {
        "id": "parked_car_night",
        "place": "a parked car at the edge of a quiet street at night",
        "mood": "after-the-fight quiet, close air",
        "light_quality": "dashboard glow and a distant streetlamp",
        "time": "night",
        "ambient_object": "empty passenger seat of a car",
        "lighting_condition": "blue_hour_streetlight",
        "setting_archetype": "transit",
        "tokens": frozenset(
            {
                "seat", "car", "sorry", "silence", "fight", "guilt", "truth",
                "dashboard", "street", "lies",
            }
        ),
        "zones": (
            {
                "setting": "parked car at night, door ajar",
                "background": "quiet street as a dark plane beyond the windshield",
            },
            {
                "setting": "same parked car, looking across the cabin",
                "background": "the empty passenger seat still in frame",
            },
            {
                "setting": "same parked car, through the windshield at night",
                "background": "streetlamp smear, no other locations",
            },
            {
                "setting": "same parked car, empty passenger seat close",
                "background": "dashboard glow, the street only as night",
            },
            {
                "setting": "same parked car, figure in the driver side",
                "background": "the empty seat beside them",
            },
            {
                "setting": "same parked car, door still ajar at night",
                "background": "curb and one streetlamp, the car staying in frame",
            },
            {
                "setting": "same parked car interior after the quiet",
                "background": "windshield and night, not a new room",
            },
            {
                "setting": "same parked car, empty passenger seat",
                "background": "the cabin as the whole world of the episode",
            },
            {
                "setting": "parked car at night, door ajar",
                "background": "the same seat, a closer camera than the opening",
            },
        ),
    },
    {
        "id": "city_edge_night",
        "place": "a quiet road at the edge of a city at night",
        "mood": "solitary, lamp-warm against indigo",
        "light_quality": "one streetlamp, cool sky",
        "time": "night",
        "ambient_object": "empty road",
        "lighting_condition": "blue_hour_streetlight",
        "setting_archetype": "exterior-urban",
        "tokens": frozenset(
            {
                "street", "window", "sidewalk", "path", "road", "lamp",
                "light", "morning", "night", "hope",
            }
        ),
        "zones": (
            {
                "setting": "quiet night road at the city edge",
                "background": "one streetlamp, a building wall, indigo sky",
            },
            {
                "setting": "same night road, figure on the path",
                "background": "the lamp and the wall staying in the world",
            },
            {
                "setting": "same night road, looking toward the lamp",
                "background": "empty asphalt, no new landmark",
            },
            {
                "setting": "same night road, closer to the lamp",
                "background": "the building wall as the only architecture",
            },
            {
                "setting": "same night road, from the far curb",
                "background": "figure small, lamp still the light",
            },
            {
                "setting": "same night road, beside the building wall",
                "background": "lamp glow, sky as the rest of the frame",
            },
            {
                "setting": "same night road, looking back along the path",
                "background": "the lamp behind, city only as hush",
            },
            {
                "setting": "same night road under the streetlamp",
                "background": "asphalt and wall, not a second city",
            },
            {
                "setting": "quiet night road at the city edge",
                "background": "the same lamp, closer than the opening beat",
            },
        ),
    },
    {
        "id": "kitchen_night_counter",
        "place": "a small kitchen at night, one counter lamp, the rest of the house dark",
        "mood": "ordinary, unfinished, kept",
        "light_quality": "one warm counter lamp against cool linoleum",
        "time": "night",
        "ambient_object": "kitchen counter",
        "lighting_condition": "indoor_lamp_glow",
        "setting_archetype": "interior-domestic",
        "tokens": frozenset(
            {
                "mug", "counter", "kitchen", "morning", "shelf", "her",
                "grief", "carry",
            }
        ),
        "zones": (
            {
                "setting": "small kitchen at night, counter in the foreground",
                "background": "dark doorway to the rest of the house",
            },
            {
                "setting": "same kitchen, looking along the counter",
                "background": "one lamp, empty floor, no other rooms",
            },
            {
                "setting": "same kitchen, figure at the counter",
                "background": "the lamp behind, house dark beyond",
            },
            {
                "setting": "same kitchen, close on the counter plane",
                "background": "tile and lamp spill, nothing else named",
            },
            {
                "setting": "same kitchen from the far doorway",
                "background": "figure small, counter still the place",
            },
            {
                "setting": "same kitchen, side angle on the counter",
                "background": "cabinet faces as flat color, lamp warm",
            },
            {
                "setting": "same kitchen, looking back from the sink wall",
                "background": "the counter holding the middle of the frame",
            },
            {
                "setting": "same kitchen at night, figure passing the counter",
                "background": "lamp still the only light in the house",
            },
            {
                "setting": "small kitchen at night, counter in the foreground",
                "background": "the same counter, closer than the opening",
            },
        ),
    },
    {
        "id": "bedroom_night_hall",
        "place": "a bedroom at night with the hall light as a thin crack",
        "mood": "wired quiet, the night not over",
        "light_quality": "cool room, one warm crack from the hall",
        "time": "night",
        "ambient_object": "blank wall",
        "lighting_condition": "indoor_lamp_glow",
        "setting_archetype": "interior-domestic",
        "tokens": frozenset(
            {
                "message", "silence", "phone", "room", "night", "checking",
                "bed", "dark",
            }
        ),
        "zones": (
            {
                "setting": "bedroom at night, hall light as a thin crack",
                "background": "the bed as a dark plane, no extra objects",
            },
            {
                "setting": "same bedroom, figure sitting on the edge of the bed",
                "background": "hall crack still the only warm light",
            },
            {
                "setting": "same bedroom, looking toward the closed door",
                "background": "the hall crack under the door",
            },
            {
                "setting": "same bedroom, closer on the figure at the bed",
                "background": "wall and hall light, night through the room",
            },
            {
                "setting": "same bedroom from the doorway looking in",
                "background": "figure small, bed still in the world",
            },
            {
                "setting": "same bedroom, side of the bed against the wall",
                "background": "hall crack, empty floor",
            },
            {
                "setting": "same bedroom, figure leaving toward the door",
                "background": "the bed behind, hall light ahead",
            },
            {
                "setting": "same bedroom at night, empty edge of the bed",
                "background": "the crack of hall light still there",
            },
            {
                "setting": "bedroom at night, hall light as a thin crack",
                "background": "the same room, a closer camera than the opening",
            },
        ),
    },
    {
        "id": "front_stoop_evening",
        "place": "a front stoop at the edge of a quiet street at evening",
        "mood": "waited-out, ordinary, unperformed",
        "light_quality": "dusk cooling, one porch bulb against the street",
        "time": "dusk",
        "ambient_object": "front steps",
        "lighting_condition": "blue_hour_streetlight",
        "setting_archetype": "threshold",
        "tokens": frozenset(
            {
                "stoop", "steps", "thursday", "speech", "promise", "porch",
                "forever", "trust",
            }
        ),
        "zones": (
            {
                "setting": "front stoop at evening, street as a dark plane",
                "background": "one porch bulb, quiet houses beyond",
            },
            {
                "setting": "same stoop, figure on the steps",
                "background": "the street empty, bulb still the light",
            },
            {
                "setting": "same stoop looking out toward the curb",
                "background": "no second location, only this threshold",
            },
            {
                "setting": "same stoop, closer on the steps",
                "background": "porch bulb, the street as hush",
            },
            {
                "setting": "same stoop from the sidewalk looking back",
                "background": "the man sitting on the steps, the woman nearby",
            },
            {
                "setting": "same stoop, side angle along the railing",
                "background": "the man back on the steps, Thursday ordinary",
            },
            {
                "setting": "same stoop, two figures small on the steps",
                "background": "the street staying empty and ordinary",
            },
            {
                "setting": "same stoop at evening, sitting height",
                "background": "porch bulb, curb as the far edge",
            },
            {
                "setting": "front stoop at evening, street as a dark plane",
                "background": "the same steps, closer than the opening beat",
            },
        ),
    },
)


def choose_episode_atmosphere(
    lines: list[dict[str, Any]],
    *,
    thesis: str = "",
    theme: str = "",
) -> dict[str, Any]:
    """Read the full thesis + lines once. Pick one place/mood/light for the episode."""
    forced = ""
    if lines and isinstance(lines[0], dict):
        forced = str(lines[0].get("force_episode_world_id") or "").strip()
    if forced:
        for world in _EPISODE_WORLDS:
            if world["id"] == forced:
                return {
                    "id": world["id"],
                    "place": world["place"],
                    "mood": world["mood"],
                    "light_quality": world["light_quality"],
                    "time": world["time"],
                    "ambient_object": world["ambient_object"],
                    "lighting_condition": world.get("lighting_condition")
                    or "indoor_lamp_glow",
                    "setting_archetype": world.get("setting_archetype")
                    or "threshold",
                    "zones": list(world["zones"]),
                    "score": 99,
                }
    texts: list[str] = []
    if thesis:
        texts.append(str(thesis))
    theme_s = str(theme or "").replace("_", " ").strip()
    if theme_s:
        texts.append(theme_s)
    for row in lines or []:
        if isinstance(row, dict):
            t = str(row.get("text") or row.get("beat_text") or "").strip()
        else:
            t = str(row or "").strip()
        if t:
            texts.append(t)
    blob = " ".join(texts).lower()
    best = _EPISODE_WORLDS[0]
    best_score = -1
    theme_key = str(theme or "").strip().lower()
    for world in _EPISODE_WORLDS:
        tokens: frozenset[str] = world["tokens"]
        score = sum(1 for tok in tokens if re.search(rf"\b{re.escape(tok)}\b", blob))
        if theme_key == "self_respect" and world["id"] == "apartment_night_edge":
            score += 3
        if theme_key == "communication" and world["id"] == "parked_car_night":
            score += 3
        if theme_key == "grief" and world["id"] == "kitchen_night_counter":
            score += 3
        if theme_key == "attachment" and world["id"] == "bedroom_night_hall":
            score += 3
        if theme_key == "trust" and world["id"] == "front_stoop_evening":
            score += 3
        if score > best_score:
            best_score = score
            best = world
    return {
        "id": best["id"],
        "place": best["place"],
        "mood": best["mood"],
        "light_quality": best["light_quality"],
        "time": best["time"],
        "ambient_object": best["ambient_object"],
        "lighting_condition": best.get("lighting_condition") or "indoor_lamp_glow",
        "setting_archetype": best.get("setting_archetype") or "threshold",
        "zones": list(best["zones"]),
        "score": best_score,
    }


def apply_episode_atmosphere(
    lines: list[dict[str, Any]],
    world: dict[str, Any],
) -> dict[str, Any]:
    """Stamp one world onto every beat. Variety is camera, not a new location."""
    zones = list(world.get("zones") or ())
    for i, row in enumerate(lines or []):
        if not isinstance(row, dict):
            continue
        zone = zones[i % len(zones)] if zones else {
            "setting": str(world.get("place") or "quiet indoor room"),
            "background": "the same place, a different camera",
        }
        text = str(row.get("text") or row.get("beat_text") or "")
        noun = preferred_concrete_noun(text)
        if noun and noun in _SKIP_RENDER_NOUNS:
            noun = ""
        if noun and not line_has_nameable_object(text):
            noun = ""
        row["episode_world_id"] = world["id"]
        row["atmosphere_place"] = world["place"]
        row["atmosphere_mood"] = world["mood"]
        row["atmosphere_light"] = world["light_quality"]
        row["atmosphere_background"] = str(zone.get("background") or "")
        row["atmosphere_angle"] = _ATMOS_ANGLES[i % len(_ATMOS_ANGLES)]
        row["atmosphere_distance"] = _ATMOS_DISTANCES[i % len(_ATMOS_DISTANCES)]
        row["atmosphere_ambient_object"] = str(
            world.get("ambient_object") or "blank wall"
        )
        row["setting"] = str(zone.get("setting") or world.get("place") or "")
        row["key_object"] = noun or str(world.get("ambient_object") or "blank wall")
        row["time_of_day"] = _ATMOS_TIMES[i % len(_ATMOS_TIMES)]
        row["shot_scale"] = (
            "wide" if i % 3 == 0 else ("medium" if i % 3 == 1 else "close")
        )
        row["lighting_condition"] = str(
            world.get("lighting_condition") or "indoor_lamp_glow"
        )
        row["setting_archetype"] = str(
            world.get("setting_archetype") or "threshold"
        )
        row.pop("setting_archetype_remap", None)
        row.pop("composition_remap", None)
        row.pop("visual_anchor_hint", None)
        row["visual_fallback"] = "episode_atmosphere"
        if str(row.get("composition_type") or "") == "wide_environment":
            row["composition_type"] = "figure_centered"
        print(
            f"[LOFI atmosphere] scene={i + 1} world={world['id']} "
            f"setting={row['setting']!r} object={row['key_object']!r} "
            f"angle={row['atmosphere_angle']}"
        )
    if lines and isinstance(lines[0], dict):
        lines[0]["episode_atmosphere"] = {
            k: world[k]
            for k in ("id", "place", "mood", "light_quality", "time")
            if k in world
        }
    return world


def episode_atmosphere_clause(beat: dict[str, Any]) -> str:
    place = str(beat.get("atmosphere_place") or "").strip()
    if not place:
        return ""
    mood = str(beat.get("atmosphere_mood") or "").strip()
    light = str(beat.get("atmosphere_light") or "").strip()
    angle = str(beat.get("atmosphere_angle") or "").strip()
    dist = str(beat.get("atmosphere_distance") or "").strip()
    bg = str(beat.get("atmosphere_background") or "").strip()
    return (
        "EPISODE WORLD (every beat is a camera on this same place, "
        f"not a new location): {place}. Mood: {mood}. Light: {light}. "
        f"This beat: {angle}, {dist}. Background: {bg}. "
        "Do not invent a cafe, theater, train, or other landmark "
        "because a word appeared in this line."
    )


def _episode_has_atmosphere(lines: list[dict[str, Any]]) -> bool:
    if not lines or not isinstance(lines[0], dict):
        return False
    return bool(str(lines[0].get("episode_world_id") or "").strip())


def _choose_and_apply_episode_atmosphere(
    lines: list[dict[str, Any]],
    *,
    theme_row: dict[str, Any] | None = None,
) -> None:
    thesis = ""
    theme = ""
    if isinstance(theme_row, dict):
        thesis = str(
            theme_row.get("thesis") or theme_row.get("locked_thesis") or ""
        )
        theme = str(theme_row.get("theme") or "")
    if lines and isinstance(lines[0], dict):
        thesis = thesis or str(lines[0].get("episode_thesis") or "")
        theme = theme or str(lines[0].get("episode_theme") or "")
    world = choose_episode_atmosphere(lines, thesis=thesis, theme=theme)
    apply_episode_atmosphere(lines, world)
    print(
        f"[LOFI atmosphere] world={world['id']} place={world['place']!r} "
        f"score={world.get('score')}"
    )


def enforce_concrete_beat_visuals(
    lines: list[dict[str, Any]],
    *,
    pool: list[dict[str, str]] | None = None,
    vary_imagery: bool = False,
    lock_visuals: bool = False,
) -> list[dict[str, Any]]:
    """
    Licensing floor: do not invent clutter the caption never named.
    When an episode world is already stamped, do not retie each beat to a
    new landmark from that beat's nouns — camera variety lives inside the world.
    """
    del vary_imagery
    pairs = pool or [dict(p) for p in DEFAULT_SETTING_OBJECT_PAIRS]
    used: set[tuple[str, str]] = set()
    used_stems: dict[str, int] = {}
    n = len(lines)
    anchor_stem = _episode_anchor_stem(lines)
    theme = ""
    module = "relationship"
    if lines and isinstance(lines[0], dict):
        theme = str(lines[0].get("episode_theme") or "")
        module = str(lines[0].get("episode_module") or "relationship") or "relationship"
    for i, row in enumerate(lines):
        if not isinstance(row, dict):
            continue
        normalize_beat_visuals(row, index=i, scene_count=n, pool=pairs)
        text = str(row.get("text") or row.get("beat_text") or "")
        setting = _sanitize_visual_phrase(str(row.get("setting") or ""))
        key_object = _sanitize_visual_phrase(str(row.get("key_object") or ""))
        world = {
            "id": row.get("episode_world_id"),
            "place": row.get("atmosphere_place"),
        } if str(row.get("episode_world_id") or "").strip() else None
        tied = visual_tied_to_caption(
            text, setting, key_object, episode_world=world
        )
        concrete = setting_is_concrete(setting, key_object)
        stem = _object_stem(key_object)
        stem_cap = 2 if (anchor_stem and stem == anchor_stem) else _MAX_OBJECT_STEM_HITS
        overused = used_stems.get(stem, 0) >= stem_cap
        handheld = _is_banned_key_object(key_object)
        if handheld and spoken_licenses_key_object(text, key_object):
            handheld = False
        _apply_cluster_pose(row, text)
        blank_visual = not setting.strip() or not key_object.strip()
        world_locked = bool(world) and not lock_visuals
        if world_locked:
            if line_has_nameable_object(text) and (handheld or blank_visual):
                row["key_object"] = str(
                    row.get("atmosphere_ambient_object") or "blank wall"
                )
                if blank_visual:
                    row["setting"] = str(
                        row.get("atmosphere_place")
                        or setting
                        or "quiet indoor room"
                    )
                row["visual_fallback"] = "episode_atmosphere_floor"
            # Keep the episode world. Do not mint a new landmark from this line.
        elif (not lock_visuals) and (not tied or not concrete or overused or handheld):
            pick = pick_caption_anchored_pair(
                text, pairs, used=used, index=i, used_stems=used_stems,
            )
            row["setting"] = pick["setting"]
            row["key_object"] = pick["key_object"]
            row["visual_fallback"] = (
                "object_stem_rotate" if overused and tied else "caption_anchored_pool"
            )
            print(
                f"[LOFI identity v2] caption-anchor scene={i + 1} "
                f"tied={int(tied)} concrete={int(concrete)} overused={int(overused)} "
                f"setting={row['setting']!r} object={row['key_object']!r} "
                f"text={text!r}"
            )
        elif lock_visuals and blank_visual:
            pick = pick_caption_anchored_pair(
                text, pairs, used=used, index=i, used_stems=used_stems,
            )
            row["setting"] = pick["setting"]
            row["key_object"] = pick["key_object"]
            row["visual_fallback"] = "lock_visuals_blank_fill"
            print(
                f"[LOFI identity v2] lock-visuals fill scene={i + 1} "
                f"setting={row['setting']!r} object={row['key_object']!r} "
                f"text={text!r}"
            )
        elif lock_visuals and (not tied or not concrete or overused):
            print(
                f"[LOFI identity v2] lock-visuals keep scene={i + 1} "
                f"tied={int(tied)} concrete={int(concrete)} overused={int(overused)} "
                f"setting={setting!r} object={key_object!r}"
            )
        _prefer_character_subject(row, i)
        row["setting"] = _expand_thin_setting(
            _sanitize_visual_phrase(str(row.get("setting") or "")),
            pairs,
        )
        row["key_object"] = _sanitize_visual_phrase(str(row.get("key_object") or ""))
        if (not lock_visuals) and _is_banned_key_object(str(row.get("key_object") or "")):
            spoken = preferred_concrete_noun(text)
            keep_spoken = bool(
                spoken
                and spoken not in _SKIP_RENDER_NOUNS
                and spoken in str(row.get("key_object") or "").lower()
            ) or spoken_licenses_key_object(text, str(row.get("key_object") or ""))
            if keep_spoken:
                pass
            elif str(row.get("episode_world_id") or "").strip():
                row["key_object"] = str(
                    row.get("atmosphere_ambient_object") or "blank wall"
                )
                row["visual_fallback"] = "episode_atmosphere_hands_free"
            else:
                for p in DEFAULT_SETTING_OBJECT_PAIRS:
                    stem_p = _object_stem(p["key_object"])
                    cap_p = 2 if (anchor_stem and stem_p == anchor_stem) else _MAX_OBJECT_STEM_HITS
                    if used_stems.get(stem_p, 0) >= cap_p:
                        continue
                    row["setting"] = p["setting"]
                    row["key_object"] = p["key_object"]
                    row["visual_fallback"] = "hands_free_bank"
                    break
        used.add(_pair_key(str(row.get("setting") or ""), str(row.get("key_object") or "")))
        final_stem = _object_stem(str(row.get("key_object") or ""))
        used_stems[final_stem] = used_stems.get(final_stem, 0) + 1
    if not lock_visuals:
        apply_abstract_licenses(lines)
        _remap_signage_traps(lines, pairs)
        _apply_framing_variety(lines)
        apply_episode_cast(lines)
        assign_beat_functions(lines)
        assign_close_beat(lines, theme=theme, module=module)
    restore_anchor_visual_fields(lines)
    cap_window_primary_beats(lines)
    constrain_unspoken_support_props(lines)
    return lines


def assemble_v2_prompt(
    beat: dict[str, Any],
    *,
    bank: dict[str, Any] | None = None,
    focus_step: int = 0,
) -> str:
    ident = bank or load_visual_identity_v2()
    blocks = ident.get("blocks") or {}
    open_b = str(blocks.get("open") or "").strip()
    tech_b = str(blocks.get("technique") or "").strip()
    mood_b = str(blocks.get("mood") or "").strip()
    fmt_b = str(blocks.get("format") or "").strip()
    st = str(beat.get("subject_type") or "woman")
    expr = str(beat.get("subject_expression") or "sad")
    setting = _sanitize_visual_phrase(str(beat.get("setting") or ""))
    key_object = _sanitize_visual_phrase(str(beat.get("key_object") or ""))
    beat["setting"] = setting
    beat["key_object"] = key_object
    key_object_prompt = _grounded_object_phrase(key_object, setting)
    tod = str(beat.get("time_of_day") or "dusk")
    act = str(beat.get("arc_position") or "act1")
    palette_sentence, palette_key = _palette_sentence(ident, act)
    beat["palette_key"] = palette_key
    framing_kind = ""

    if st == "object_focus":
        # Do not fill the bank template "in [setting] at [time_of_day]" —
        # named desks/tables cue Schnell's mug+screen still-life prior.
        scene, framing_kind = object_focus_scene_text(
            key_object,
            step=focus_step,
            setting=setting,
            hand_interaction=bool(beat.get("hand_held_motif")),
        )
        if scene:
            scene = scene[0].upper() + scene[1:]
    elif st == "couple" and _WIDE_COUPLE_PLACE_RE.search(f"{setting} {key_object}"):
        scene, framing_kind = couple_wide_silhouette_scene_text(
            setting, tod, step=focus_step
        )
        beat["silhouette_framing"] = "wide_couple"
    elif st == "silhouette":
        chars = ident.get("characters") or {}
        tmpl = str(chars.get("silhouette") or "")
        char = _fill_slots(tmpl, expression=expr)
        if char:
            char = char[0].upper() + char[1:]
        pose = str(beat.get("pose_hint") or "").strip()
        pose_bit = f" {pose}." if pose else ""
        if str(beat.get("silhouette_framing") or "") == "wide_couple":
            scene, framing_kind = couple_wide_silhouette_scene_text(
                setting, tod, step=focus_step
            )
        else:
            scene, framing_kind = silhouette_scene_text(
                char,
                key_object_prompt,
                setting,
                tod,
                step=focus_step,
                pose_bit=pose_bit,
            )
    else:
        chars = ident.get("characters") or {}
        tmpl = str(chars.get(st) or chars.get("woman") or "")
        char = _fill_slots(tmpl, expression=expr)
        if char:
            char = char[0].upper() + char[1:]
        pose = str(beat.get("pose_hint") or "").strip()
        pose_bit = f" {pose}." if pose else ""
        couple_bit = f" {_COUPLE_SEPARATION}" if st == "couple" else ""
        env_stem = _object_stem(key_object)
        if env_stem in {"rain", "window", "horizon", "platform"}:
            framing = (
                "Half-length or seen from behind, looking out into the space, "
                "not touching the window or any object."
            )
        else:
            framing = (
                _dev_avert_framing(beat)
                if is_figure_only_beat(beat)
                else _CLOSE_PORTRAIT
            )
        scene = (
            f"{char} in {setting}, {tod}.{pose_bit}{couple_bit} "
            f"{key_object_prompt} is part of the environment, not held in any hand. "
            f"{framing} {_HAND_POSE}"
        ).strip()

    if framing_kind:
        beat["object_focus_step"] = max(0, min(int(focus_step), 2))
        beat["object_focus_framing"] = framing_kind

    parts = [
        open_b,
        scene,
        episode_atmosphere_clause(beat),
        figure_composition_guard(beat),
        tech_b,
        palette_sentence,
        _SATURATION_GUARD,
        _TEXT_LEGIBILITY_GUARD,
        garment_coverage_clause(st, str(beat.get("close_variant") or "")),
        mood_b,
        fmt_b,
    ]
    prompt = " ".join(p for p in parts if p)
    return ensure_object_focus_texture_prompt(prompt, beat)


def apply_v2_prompts_to_lines(
    lines: list[dict[str, Any]],
    *,
    theme_row: dict[str, Any] | None = None,
    bank: dict[str, Any] | None = None,
    vary_imagery: bool = False,
    lock_visuals: bool = False,
) -> list[dict[str, Any]]:
    ident = bank or load_visual_identity_v2()
    pool = setting_object_pool(theme_row)
    n = len(lines)
    has_concepts = any(
        isinstance(r, dict)
        and str(r.get("visual_concept") or r.get("scene_description") or "").strip()
        for r in lines
    )
    print(
        "[LOFI identity v2] assemble_v2_prompt | painterly=OFF | "
        f"vary_imagery={int(vary_imagery)} | lock_visuals={int(lock_visuals)} | "
        f"stage2_concepts={int(has_concepts)} | "
        "bank=visual_identity_bank_v2.json"
    )
    if has_concepts:
        print("[LOFI identity v2] skip _EPISODE_WORLDS — Stage 2 concepts present")
    elif not lock_visuals:
        _choose_and_apply_episode_atmosphere(lines, theme_row=theme_row)
    if not has_concepts:
        enforce_concrete_beat_visuals(
            lines, pool=pool, vary_imagery=vary_imagery, lock_visuals=lock_visuals
        )
        if lock_visuals or not _episode_has_atmosphere(lines):
            _apply_setting_archetype_pass(lines, pool, lock_visuals=lock_visuals)
    theme = ""
    module = "relationship"
    if lines and isinstance(lines[0], dict):
        theme = str(lines[0].get("episode_theme") or "")
        module = str(lines[0].get("episode_module") or "relationship") or "relationship"
    has_close = any(
        isinstance(r, dict)
        and str(r.get("close_variant") or "") in {"portrait_close", "silhouette"}
        for r in lines
    )
    if not has_close and not has_concepts:
        assign_close_beat(lines, theme=theme, module=module)
    if not has_concepts:
        cap_window_primary_beats(lines)
        constrain_unspoken_support_props(lines)
    apply_meaning_driven_variety(lines)
    apply_object_focus_spoken_motif(lines)
    for i, row in enumerate(lines):
        if not isinstance(row, dict):
            continue
        normalize_beat_visuals(row, index=i, scene_count=n, pool=pool)
        prompt = assemble_v2_prompt(row, bank=ident)
        row["visual_prompt"] = prompt
        row["visual_identity"] = "v2"
        row["riso_id"] = f"identity_v2_{row.get('subject_type')}_{row.get('arc_position')}"
        row["riso_palette"] = row.get("palette_key")
    variety = score_episode_variety(lines)
    print(f"[LOFI variety] {variety.get('summary')}")
    if lines and isinstance(lines[0], dict):
        lines[0]["episode_variety"] = variety
    return lines


# ── FLUXDEV assembler (Schnell assemble_v2_prompt above is untouched) ────────

_DEV_STOP = frozenset(
    {
        "i", "you", "we", "they", "the", "a", "an", "and", "or", "of", "to",
        "in", "on", "at", "is", "are", "was", "were", "be", "it", "that",
        "this", "for", "with", "as", "not", "but", "if", "so",
    }
)
_DEV_TOD_ATMOSPHERE: dict[str, str] = {
    "dawn": "Cool painted dawn: pale canvas sky, long quiet shadows.",
    "morning": "Pale painted morning: cream light, muted interiors.",
    "afternoon": "Even afternoon paint: midtone rooms, still air.",
    "dusk": "Painted dusk: warm amber against cooler shadow.",
    "evening": "Painted evening: lamp-warm interiors, solid walls.",
    "night": "Painted night: indigo and charcoal, lamp as the only warm plane.",
    "overcast": "Painted overcast: sage grey and cream flats, even daylight.",
    "rain": "Painted rain: cool grey wet flats, cool paper sky.",
}
_DEV_PERSON_MOOD = (
    "Quiet melancholic mood. Everyday clothes, fully covered by "
    "dress or sweater fabric: back, shoulders, chest, and torso clothed."
)
HUMAN_FIGURE_TYPES = frozenset({"woman", "man", "couple", "silhouette"})
GARMENT_COVERAGE_DIRECTIVE = (
    "fully covered by dress fabric, back and shoulders clothed, chest and torso "
    "clothed, high-neck sweater to the jaw"
)


def garment_coverage_clause(
    subject_type: str,
    close_variant: str = "",
) -> str:
    """Positive-prompt coverage steer. Flux CFG often ignores negatives."""
    st = str(subject_type or "").strip().lower().replace(" ", "_")
    cv = str(close_variant or "").strip().lower()
    if cv == "eye_close":
        return ""
    if st not in HUMAN_FIGURE_TYPES and cv != "portrait_close":
        return ""
    return GARMENT_COVERAGE_DIRECTIVE


# No frontal / three-quarter close face. Rotate so woman/man beats vary.
_DEV_AVERT_FRAMINGS: tuple[str, ...] = (
    "Seen from behind, looking into the space.",
    "Deep profile, face mostly turned away — features only a few brushstrokes.",
    "Cropped at the hairline: crown, hair, and shoulders only.",
    "Distant full-body figure in the room; the face is a few brush marks.",
)
_DEV_CHAR_OVERRIDE: dict[str, str] = {
    "woman": (
        "a woman with long dark hair in a simple high-neck sweater, "
        "[expression] posture, fully covered by dress fabric, back and shoulders clothed"
    ),
    "man": (
        "a quiet man with dark hair in a plain everyday sweater, "
        "[expression] posture, fully covered by fabric, back and shoulders clothed"
    ),
    "couple": (
        "a man and a woman in simple everyday clothes, both dark-haired, "
        "[expression] postures, fully covered by fabric, back and shoulders clothed"
    ),
}
# Locked style-riso_painting_retro_vintage (risograph-on-Dev). Woman/man only:
# figure is a backlit silhouette; environment keeps full painterly richness.
_RISO_RETRO_TOD: dict[str, str] = {
    "dawn": "Dawn print: pale paper sky, long quiet shadow blocks.",
    "morning": "Morning print: cream light, muted interiors as flat color planes.",
    "afternoon": "Afternoon print: midtone rooms, still air, visible paper tooth.",
    "dusk": (
        "Dusk print: warm amber flats against cooler shadow blocks."
    ),
    "evening": (
        "Evening print: lamp-warm interiors as flat color, solid walls."
    ),
    "night": (
        "Night print: indigo and charcoal flats, lamp as the only warm plane."
    ),
    "overcast": (
        "Overcast print: sage grey and cream flats, flat daylight."
    ),
    "rain": (
        "Rain print: cool grey wet flats, cool paper sky."
    ),
}
_RISO_RETRO_BACKLIT_FIGURE = (
    "Positioned against a bright interior lamp so the body "
    "reads as a flat unlit silhouette shape. "
    "Three-quarter or full-figure silhouette in the middle of the frame, "
    "inside the room, facing the lamp."
)
_RISO_RETRO_SUNSET_DOORWAY_FIGURE = (
    "Positioned against a bright sunset backlight so the body "
    "reads as a flat unlit silhouette shape. "
    "Three-quarter or full-figure silhouette in the middle of the frame."
)
# Cross-run lighting bank. sunset_doorway is the overused default to cap.
LIGHTING_SUNSET_ID = "sunset_doorway"
MAX_DOMINANT_LIGHTING_BEATS = 6
MIN_DOMINANT_LIGHTING_BEATS = 5
LIGHTING_CONDITION_ORDER: tuple[str, ...] = (
    "sunset_doorway",
    "overcast_daylight",
    "blue_hour_streetlight",
    "indoor_lamp_glow",
    "rainy_grey",
    "morning_cool",
)
LIGHTING_CONDITIONS: dict[str, dict[str, str]] = {
    "sunset_doorway": {
        "label": "warm sunset backlight",
        "tod": "dusk",
        "figure_framing": _RISO_RETRO_SUNSET_DOORWAY_FIGURE,
        "atmos": (
            "Warm dusk print: dusty rose and amber flats, olive shadows. "
            "No teal or indigo wash."
        ),
    },
    "overcast_daylight": {
        "label": "overcast daylight",
        "tod": "overcast",
        "figure_framing": (
            "Even overcast daylight, figure readable as a "
            "printed shape in grey-cream air. "
            "Three-quarter or full-figure in the middle of the frame."
        ),
        "atmos": (
            "Cool overcast print: sage grey and cream flats, even daylight. "
            "No amber lamp."
        ),
    },
    "blue_hour_streetlight": {
        "label": "blue-hour / night streetlight",
        "tod": "night",
        "figure_framing": (
            "Blue-hour streetlight: cool indigo ground, a small warm lamp as a "
            "hard color plane. "
            "Three-quarter or full-figure in the middle of the frame."
        ),
        "atmos": (
            "Contrast night print: cool indigo ground, one warm amber lamp "
            "as a hard color plane."
        ),
    },
    "indoor_lamp_glow": {
        "label": "indoor lamp glow",
        "tod": "evening",
        "figure_framing": (
            "Flat unlit silhouette against one indoor lamp. A warm pool of "
            "light on a blank wall, figure in an empty room. "
            "Three-quarter or full-figure in the middle of the frame."
        ),
        "atmos": (
            "Warm interior print: amber and ochre paper, rose shadows. "
            "No teal or indigo ambient."
        ),
    },
    "rainy_grey": {
        "label": "rainy grey light",
        "tod": "rain",
        "figure_framing": (
            "Rainy grey light, cool paper sky. "
            "Three-quarter or full-figure in the middle of the frame."
        ),
        "atmos": (
            "Cool rain print: charcoal and ash-white wet flats. "
            "No warm lamp."
        ),
    },
    "morning_cool": {
        "label": "early morning cool light",
        "tod": "dawn",
        "figure_framing": (
            "Early morning cool light, pale paper sky, long quiet shadows. "
            "Three-quarter or full-figure in the middle of the frame."
        ),
        "atmos": (
            "Cool dawn print: pale silver paper sky, long quiet shadow blocks. "
            "No amber."
        ),
    },
}


def suggest_lighting_for_setting(setting: str, key_object: str = "") -> str:
    blob = f"{setting} {key_object}".lower()
    if any(w in blob for w in ("rain", "wet", "streak")):
        return "rainy_grey"
    if any(w in blob for w in ("kitchen", "sofa", "desk", "bedroom", "table", "counter")):
        return "indoor_lamp_glow"
    if any(w in blob for w in ("streetlamp", "streetlight", "street", "platform", "night")):
        return "blue_hour_streetlight"
    if any(w in blob for w in ("dawn", "morning", "stoop")):
        return "morning_cool"
    if any(w in blob for w in ("park", "path", "horizon", "coast")):
        return "overcast_daylight"
    if any(w in blob for w in ("doorway", "dusk", "sunset")):
        return "sunset_doorway"
    return ""


def lighting_spec(condition: str) -> dict[str, str]:
    return dict(LIGHTING_CONDITIONS.get(condition) or LIGHTING_CONDITIONS["overcast_daylight"])


def lighting_figure_framing(beat: dict[str, Any]) -> str:
    spec = lighting_spec(str(beat.get("lighting_condition") or ""))
    return str(spec.get("figure_framing") or _RISO_RETRO_BACKLIT_FIGURE)


def lighting_atmos(beat: dict[str, Any]) -> str:
    spec = lighting_spec(str(beat.get("lighting_condition") or ""))
    atmos = str(spec.get("atmos") or "")
    if not bool(beat.get("window_primary")):
        atmos = re.sub(
            r"small warm windows[^.]*\.?",
            "solid walls, lamp as the only warm plane.",
            atmos,
            flags=re.I,
        )
        atmos = re.sub(
            r"window as a dark plane\.?",
            "solid walls.",
            atmos,
            flags=re.I,
        )
    return atmos


def apply_lighting_to_beat(row: dict[str, Any], condition: str) -> None:
    spec = lighting_spec(condition)
    row["lighting_condition"] = condition
    row["lighting_label"] = spec.get("label") or condition
    row["time_of_day"] = spec.get("tod") or row.get("time_of_day") or "dusk"


def assign_episode_lighting(
    lines: list[dict[str, Any]],
    *,
    module: str = "relationship",
    theme: str = "",
) -> str:
    """
    Pick one dominant lighting look, cap it at 5–6 of 9 beats, and bias
    away from sunset-doorway when the last two episodes already used it.
    """
    n = len([r for r in lines if isinstance(r, dict)])
    if n <= 0:
        return ""
    from core.economic_reel_lofi import lofi_collections as rag

    pre = [
        str(r.get("lighting_condition") or "").strip()
        for r in lines
        if isinstance(r, dict)
    ]
    if pre and all(c in LIGHTING_CONDITIONS for c in pre):
        counts: dict[str, int] = {}
        for c in pre:
            counts[c] = counts.get(c, 0) + 1
        dominant = max(counts, key=counts.get)
        print(
            f"[LOFI lighting] keep stamped dominant={dominant} "
            f"n={counts.get(dominant, 0)}/{n} beats={pre}"
        )
        return dominant

    forced = str(os.getenv("LOFI_FORCE_LIGHTING_DOMINANT") or "").strip().lower()
    if forced in LIGHTING_CONDITIONS:
        dominant = forced
    else:
        prev = rag.recent_dominant_lighting(module, 5)
        if not prev:
            # Pre-stamp era defaulted to sunset-doorway; treat as already used.
            prev = [LIGHTING_SUNSET_ID, LIGHTING_SUNSET_ID]
        last2 = prev[-2:]
        avoid_sunset = len(last2) >= 2 and all(x == LIGHTING_SUNSET_ID for x in last2)
        bank = [
            c
            for c in LIGHTING_CONDITION_ORDER
            if not (avoid_sunset and c == LIGHTING_SUNSET_ID)
        ]
        if not bank:
            bank = [c for c in LIGHTING_CONDITION_ORDER if c != LIGHTING_SUNSET_ID]
        seed = f"{theme}|{module}|{','.join(prev[-3:])}"
        dominant = bank[sum(ord(c) for c in seed) % len(bank)]
    n_dom = min(MAX_DOMINANT_LIGHTING_BEATS, max(MIN_DOMINANT_LIGHTING_BEATS, n - 3))
    others = [c for c in LIGHTING_CONDITION_ORDER if c != dominant]
    assigned: list[str] = []
    used_dom = 0
    other_i = 0
    for i, row in enumerate(lines):
        if not isinstance(row, dict):
            continue
        if str(row.get("lighting_condition") or "").strip() in LIGHTING_CONDITIONS:
            cond = str(row.get("lighting_condition") or "")
            assigned.append(cond)
            if cond == dominant:
                used_dom += 1
            continue
        hinted = suggest_lighting_for_setting(
            str(row.get("setting") or ""),
            str(row.get("key_object") or ""),
        )
        remaining = n - i
        need_dom = n_dom - used_dom
        if i == 0:
            cond = dominant
        elif hinted and hinted != dominant and (need_dom < remaining):
            cond = hinted
        elif used_dom < n_dom:
            cond = dominant
        else:
            cond = others[other_i % len(others)] if others else dominant
            other_i += 1
        if cond == dominant:
            used_dom += 1
        apply_lighting_to_beat(row, cond)
        assigned.append(cond)
    print(
        f"[LOFI lighting] dominant={dominant} "
        f"n={assigned.count(dominant)}/{n} "
        f"beats={assigned}"
    )
    return dominant


_RISO_RETRO_BACKLIT_COUPLE = (
    "Both figures are backlit into flat unlit silhouette shapes against the "
    "lamp, sky, or open light. The environment around them keeps full "
    "painterly detail."
)
_RISO_RETRO_CHAR: dict[str, str] = {
    "woman": (
        "a lone woman as a flat unlit clothed silhouette, [expression] posture, "
        "fully covered by dress fabric, back and shoulders clothed"
    ),
    "man": (
        "a lone man as a flat unlit clothed silhouette, [expression] posture, "
        "fully covered by fabric, back and shoulders clothed"
    ),
    "couple": (
        "two clothed figures as flat unlit silhouettes, a man and a woman, "
        "[expression] postures, fully covered by fabric, back and shoulders clothed"
    ),
}
_RISO_RETRO_EXPANSION: dict[str, str] = {
    "woman": (
        "Printed atmosphere: visible paper tooth and a slight riso color offset. "
        f"{_DEV_PERSON_MOOD} "
        "The surrounding environment — sky, foliage, room, fabric, furniture — "
        "is painted as flat gouache color blocks with hard printed edges and "
        "visible paper grain. Only the human figure is a silhouette; the room "
        "stays readable as poster shapes."
    ),
    "man": (
        "Printed atmosphere: visible paper tooth and a slight riso color offset. "
        f"{_DEV_PERSON_MOOD} "
        "The surrounding environment — sky, foliage, room, fabric, furniture — "
        "is painted as flat gouache color blocks with hard printed edges and "
        "visible paper grain. Only the human figure is a silhouette; the room "
        "stays readable as poster shapes."
    ),
    "couple": (
        "Wide printed landscape: two small unlit silhouette figures, a hard "
        "horizon, empty ground as a flat color field. Distance between them is "
        "the subject. The street, lamp, sky, and buildings are flat printed "
        "color planes with paper grain."
    ),
    "silhouette": (
        "The figure stays readable against a printed sky or doorway field. "
        "The horizon or jamb is a hard color edge. Negative space carries "
        "the mood. The surrounding environment — sky, foliage, room, fabric, "
        "furniture — is painted as flat gouache color blocks with hard printed "
        "edges and visible paper grain."
    ),
    "object_focus": (
        "A painterly risograph still-life. The object has printed weight: ink "
        "contour, interior hatch or flat fill, paper grain, color gradient, "
        "and canvas texture around it. Same flat gouache risograph atmosphere "
        "as a furnished room — paper tooth, hard color blocks."
    ),
    "abstract_symbolic": (
        "Printed atmosphere: paper tooth, hard color planes. Light and shadow "
        "as flat fields. Empty surface. The empty gesture is the only subject."
    ),
    "abstract_landscape": (
        "Wide printed environment: sky, ground, and walls as flat color fields. "
        "Light, weather, and time of day carry the feeling."
    ),
    "abstract_character": (
        "Printed atmosphere around the figure: paper tooth, flat printed color. "
        "Expression and posture only. Empty floor."
    ),
    "object_focus_anchor": (
        "Printed atmosphere: visible paper tooth and a slight riso color offset. "
        "The wall and ground are flat gouache color blocks with paper grain. "
        "One object filling the frame."
    ),
    "wide_couple": (
        "Wide printed landscape: two small unlit silhouette figures, a hard "
        "horizon, empty ground as a flat color field. Distance between them is "
        "the subject. The street, lamp, sky, and buildings are flat printed "
        "color planes with paper grain."
    ),
}
_RISO_FLAT_PRINT = (
    "Flat gouache / riso-poster illustration: hard-edged color planes, "
    "paper tooth, slight ink offset."
)
_DEV_SCENE_EXPANSION: dict[str, str] = {
    "woman": (
        "Painted canvas grain and soft brush texture. "
        f"{_DEV_PERSON_MOOD} "
        "Figure seen from behind or in deep profile."
    ),
    "man": (
        "Painted canvas grain and soft brush texture. "
        f"{_DEV_PERSON_MOOD} "
        "Figure seen from behind or in deep profile."
    ),
    "couple": (
        "Two figures occupy the center third, painted as shapes in space. "
        f"{_DEV_PERSON_MOOD} Figures seen from behind or in deep profile."
    ),
    "silhouette": (
        "The figure stays readable against a painted sky or doorway field. "
        "Negative space carries the mood."
    ),
    "object_focus": (
        "The object has painted weight: brush contour, canvas grain around it. "
        "Paper grain, color gradient, and canvas texture behind it."
    ),
    "abstract_symbolic": (
        "Light and shadow as painted fields. Empty surface. The empty gesture "
        "is the only subject."
    ),
    "abstract_landscape": (
        "Wide painted environment: sky, ground, and walls as color fields. "
        "Light and weather carry the feeling."
    ),
    "abstract_character": (
        "Painted atmosphere around the figure. Expression and posture only. "
        "Empty floor."
    ),
    "wide_couple": (
        "Wide painted landscape: two small figures, a hard horizon, empty ground. "
        "Distance between them is the subject."
    ),
}


def _dev_avert_framing(beat: dict[str, Any]) -> str:
    if is_figure_only_beat(beat):
        return (
            "Three-quarter side profile, looking toward off-frame light. "
            "Face turned away from the camera. Body open to the room."
        )
    idx = max(0, int(beat.get("scene") or 1) - 1)
    return _DEV_AVERT_FRAMINGS[idx % len(_DEV_AVERT_FRAMINGS)]


def resolve_visual_anchor(beat: dict[str, Any]) -> str:
    """
    Distilled visual essence of the spoken line — not the caption verbatim.

    Prefers DeepSeek ``visual_anchor_hint`` only when it names this beat's
    key_object. Writer hints that describe a different still-life (vase,
    mug) used to win the render over the isolation clause.
    """
    hinted = " ".join(str(beat.get("visual_anchor_hint") or "").split())
    st = str(beat.get("subject_type") or "").strip().lower().replace(" ", "_")
    obj = str(beat.get("key_object") or "").strip()
    hint_ok = (
        4 <= len(hinted.split()) <= 24
        and len(hinted) >= 12
        and _hint_names_key_object(hinted, obj)
    )
    if st == "object_focus":
        competing = _hint_adds_competing_still_life(hinted, obj)
        if hint_ok and not competing:
            return hinted.rstrip(".")
        setting = str(beat.get("setting") or "")
        visual = _object_focus_visual_noun(obj, setting)
        scene_i = beat.get("scene")
        why = "adds competing still-life" if competing else f"does not name {obj!r}"
        print(
            f"[LOFI anchor] scene={scene_i} rejected writer hint "
            f"({why}) using object-only anchor"
        )
        if _needs_containing_whole(obj, setting):
            return visual
        return f"{visual} alone, filling the frame"
    if hint_ok:
        return hinted.rstrip(".")
    concept = " ".join(str(beat.get("visual_concept") or "").split())
    if concept:
        first = re.split(r"(?<=[.!?])\s+", concept)[0].strip()
        if len(first) >= 8:
            return first.rstrip(".")
    text = " ".join(
        str(beat.get("text") or beat.get("beat_text") or "").split()
    )
    if text:
        return text.rstrip(".")
    expr = str(beat.get("subject_expression") or "quiet").strip()
    setting = str(beat.get("setting") or "a still room").strip()
    return f"{expr} presence beside {obj or 'the scene'} in {setting}"


def _hint_names_key_object(hint: str, key_object: str) -> bool:
    blob = hint.lower()
    obj = (key_object or "").strip()
    if not obj:
        return False
    stem = _object_stem(obj)
    if stem and stem in blob:
        return True
    words = [
        w for w in re.findall(r"[a-z']+", obj.lower())
        if w not in _DEV_STOP and len(w) > 2
    ]
    return bool(words) and any(w in blob for w in words)


_COMPETING_STILL_LIFE_RE = re.compile(
    r"\b(mugs?|cups?|vases?|flowers?|bouquet|coffee)\b", re.I
)


def _hint_adds_competing_still_life(hint: str, key_object: str) -> bool:
    """True when the writer hint names a mug/vase that is not the key_object."""
    if not _COMPETING_STILL_LIFE_RE.search(hint or ""):
        return False
    return not bool(_COMPETING_STILL_LIFE_RE.search(key_object or ""))


def _dev_palette_sentence(profile: dict[str, Any], act: str) -> tuple[str, str]:
    by_arc = profile.get("palette_by_arc") or {}
    key = str(by_arc.get(act) or by_arc.get("act1") or "WARM").upper()
    palettes = profile.get("palettes") or {}
    sentence = str(palettes.get(key) or palettes.get("WARM") or "").strip()
    return sentence, key


def _dev_expanded_scene_detail(
    beat: dict[str, Any],
    st: str,
    framing: str,
    profile_name: str = "",
) -> str:
    from core.economic_reel_lofi.visual_identity_profiles import (
        is_riso_painting_retro_vintage,
    )

    riso = is_riso_painting_retro_vintage(profile_name)
    table = _RISO_RETRO_EXPANSION if riso else _DEV_SCENE_EXPANSION
    tod_table = _RISO_RETRO_TOD if riso else _DEV_TOD_ATMOSPHERE
    abstract_path = str(beat.get("abstract_license") or "").strip()
    if abstract_path in {"symbolic", "landscape", "character"}:
        body = table.get(f"abstract_{abstract_path}") or ""
        spec_atmos = lighting_atmos(beat)
        tod = str(beat.get("time_of_day") or "dusk").strip().lower()
        atmos = spec_atmos or tod_table.get(tod) or tod_table.get("dusk") or ""
        return f"{body} {atmos}".strip()
    bed_scene = bool(
        couple_bed_pose_clause(
            str(beat.get("setting") or ""),
            str(beat.get("key_object") or ""),
            st,
        )
    )
    anchor_object = (
        st == "object_focus"
        and str(beat.get("anchor_beat") or "") in {"introduce", "callback"}
    )
    if str(beat.get("close_variant") or "") == "portrait_close":
        body = (
            "Printed atmosphere around the portrait: paper tooth, flat printed "
            "color, canvas texture, visible brush work. Same illustration "
            "technique as the episode."
        )
    elif str(beat.get("close_variant") or "") == "eye_close":
        body = (
            "Printed atmosphere around the eyes: paper tooth, flat printed "
            "color, lamp as a hard color plane. No full-figure, no silhouette body."
        )
    elif bed_scene:
        # Landscape couple expansion ("street, lamp, horizon") fights a bedroom.
        body = table.get("woman") or table.get("couple") or ""
    elif anchor_object:
        body = table.get("object_focus_anchor") or table.get("object_focus") or ""
    elif framing == "wide_couple" or str(beat.get("silhouette_framing") or "") == "wide_couple":
        body = table.get("wide_couple") or table.get("couple") or ""
    else:
        body = table.get(st) or table.get("woman") or ""
    tod = str(beat.get("time_of_day") or "dusk").strip().lower()
    spec_atmos = lighting_atmos(beat)
    atmos = spec_atmos or tod_table.get(tod) or tod_table.get("dusk") or ""
    return f"{body} {atmos}".strip()


_POSITIVE_NEGATION_RE = re.compile(
    r"(?:,\s*)?(?:\bnot\b|\bno\b|\bnever\b)\s+(?:a |an |the )?[a-z][\w\s'/-]*",
    re.I,
)


_STRIP_TAIL_RE = re.compile(
    r"\b(with|and|but|or|the|a|an|to|of|from|as|at|by)\s*[.,;:]?\s*$",
    re.I,
)


def _strip_positive_negation(text: str) -> str:
    """Drop not/no/never clauses so the positive prompt stays affirmative."""
    cleaned = _POSITIVE_NEGATION_RE.sub("", text or "")
    cleaned = re.sub(r"\s+([,.;:])", r"\1", cleaned)
    cleaned = " ".join(cleaned.split())
    cleaned = _STRIP_TAIL_RE.sub("", cleaned)
    cleaned = re.sub(r"[,;:]\s*$", "", cleaned)
    cleaned = re.sub(r"\s+\.", ".", cleaned)
    return " ".join(cleaned.split())


def assemble_v2_prompt_dev(
    beat: dict[str, Any],
    *,
    profile: str | dict[str, Any] | None = None,
    focus_step: int = 0,
    bank: dict[str, Any] | None = None,
) -> str:
    """
    Dev prompt: same scene builders as Schnell, style blocks from a profile,
    plus text-anchor and expanded atmosphere. Does not call assemble_v2_prompt.
    """
    del bank  # profile owns style; Schnell bank stays on the live path
    from core.economic_reel_lofi.style_modules import get_active_style, get_style_profile_dict
    from core.economic_reel_lofi.visual_identity_profiles import (
        DEFAULT_VISUAL_IDENTITY_PROFILE,
        get_visual_identity_profile,
    )

    style = get_active_style()
    if isinstance(profile, dict) and profile.get("open"):
        ident = profile
        profile_name = str(profile.get("name") or "custom")
    else:
        profile_name = str(
            profile
            or getattr(lofi_cfg, "DEFAULT_VISUAL_IDENTITY_PROFILE", "")
            or style.profile_name
            or DEFAULT_VISUAL_IDENTITY_PROFILE
        )
        if profile_name in {style.id, style.profile_name, "riso_retro_flat_v4"}:
            ident = get_style_profile_dict(style.id)
        else:
            ident = get_visual_identity_profile(profile_name)

    open_b = str(ident.get("open") or style.open or "").strip()
    tech_b = str(ident.get("technique") or style.technique or "").strip()
    mood_b = str(ident.get("mood") or style.mood or "").strip()
    fmt_b = str(ident.get("format") or style.format or "Illustration, vertical 9:16 composition.")
    sat_g = _SATURATION_GUARD
    text_g = _TEXT_LEGIBILITY_GUARD
    paint_test = False
    if paint_test:
        open_b = _PAINT_MEDIUM_OPEN
        tech_b = _PAINT_MEDIUM_TECH
        fmt_b = _PAINT_MEDIUM_FMT
        sat_g = "Keep colors saturated."
        text_g = (
            "Every mark anywhere in this image is ink linework, "
            "halftone texture, or flat color. Wall hangings are blank "
            "abstract color fields. Objects are unmarked plain surfaces. "
            "The illustration fills the frame edge to edge."
        )

    st = str(beat.get("subject_type") or "woman")
    expr = str(beat.get("subject_expression") or "sad")
    from core.economic_reel_lofi.licensed_objects import (
        enforce_licensed_inventory,
        scrub_assembled_prompt,
    )

    enforce_licensed_inventory(beat)
    cond = str(beat.get("lighting_condition") or "").strip()
    if cond in LIGHTING_CONDITIONS:
        beat["lighting_label"] = lighting_spec(cond).get("label") or cond
    setting = _sanitize_visual_phrase(str(beat.get("setting") or ""))
    key_object = _sanitize_visual_phrase(str(beat.get("key_object") or ""))
    if (
        str(beat.get("anchor_beat") or "") in {"introduce", "callback"}
        and not str(beat.get("abstract_license") or "").strip()
    ):
        aname = str(beat.get("episode_anchor_name") or "").strip()
        if aname:
            cap = str(beat.get("text") or beat.get("beat_text") or "")
            key_object = _sanitize_visual_phrase(
                anchor_visual_noun(aname, cap) or aname
            )
    beat["setting"] = setting
    beat["key_object"] = key_object
    key_object_prompt = _grounded_object_phrase(key_object, setting)
    tod = str(beat.get("time_of_day") or "dusk")
    act = str(beat.get("arc_position") or "act1")
    pal_sent, pal_key = _dev_palette_sentence(ident, act)
    if str(beat.get("palette_key") or "").upper() in {"WARM", "COLD", "CONTRAST"}:
        pal_key = str(beat.get("palette_key") or pal_key).upper()
        palettes = ident.get("palettes") or {}
        pal_sent = str(palettes.get(pal_key) or pal_sent).strip()
    atmos = lighting_atmos(beat)
    palette_sentence = " ".join(p for p in (pal_sent, atmos) if p)
    beat["palette_key"] = pal_key or str(beat.get("palette_key") or "WARM")
    framing_kind = ""

    from core.economic_reel_lofi.visual_identity_profiles import (
        is_riso_painting_retro_vintage,
    )

    riso_lock = is_riso_painting_retro_vintage(profile_name)

    abstract_path = str(beat.get("abstract_license") or "").strip()
    if abstract_path == "symbolic":
        scene = str(beat.get("scene_description") or beat.get("visual_concept") or "")
        if not scene:
            scene = (
                "Abstract symbolic illustration: light, shadow, negative space, "
                "an empty gesture. No named prop, no extra furniture."
            )
        beat["dev_scene_builder"] = "abstract_symbolic"
        framing_kind = "macro_no_setting"
    elif abstract_path == "landscape":
        scene = str(beat.get("scene_description") or beat.get("visual_concept") or "")
        if not scene:
            ctx = str(beat.get("setting") or setting or "the established place")
            scene = (
                f"Wide environment of {ctx}, {tod}. No figure. No named object. "
                "Light, weather, and time of day carry the feeling."
            )
        beat["dev_scene_builder"] = "abstract_landscape"
        framing_kind = "full_scene"
    elif abstract_path == "character":
        who = str(beat.get("subject_type") or st or "woman").strip().lower()
        if who not in {"woman", "man", "couple"}:
            who = "woman"
        beat["subject_type"] = who
        ctx = str(beat.get("setting") or setting or "quiet indoor room")
        pose = str(beat.get("pose_hint") or _CLOSE_POSE_HINT["portrait"])
        ident_clause = (
            portrait_close_identity_clause(who)
            if who in {"woman", "man"}
            else (
                "The same recurring man and woman together, camera in on "
                "expression and posture."
            )
        )
        scene = (
            f"{ident_clause} {ctx}, {tod}. {pose} "
            "No named object in frame. Hands empty."
        )
        beat["dev_scene_builder"] = "abstract_character"
        framing_kind = "portrait_close" if who != "couple" else "full_scene"
    elif str(beat.get("close_variant") or "") == "portrait_close":
        who = str(beat.get("close_character") or st or "woman").strip().lower()
        if who not in {"woman", "man"}:
            who = "woman"
        beat["subject_type"] = who
        beat["close_character"] = who
        ctx = str(beat.get("setting") or setting or "window light")
        tod_s = str(beat.get("time_of_day") or tod or "dusk")
        pose = str(beat.get("pose_hint") or _CLOSE_POSE_HINT["portrait"])
        scene = (
            f"{portrait_close_identity_clause(who)} {ctx}, {tod_s}. {pose} "
            f"{key_object_prompt} is atmosphere only. Hands empty at the sides."
        )
        beat["dev_scene_builder"] = "portrait_close"
        framing_kind = "portrait_close"
    elif str(beat.get("close_variant") or "") == "eye_close":
        ctx = str(beat.get("eye_close_context") or setting or "window light")
        tod_s = str(beat.get("time_of_day") or tod or "dusk")
        pose = str(beat.get("pose_hint") or _CLOSE_POSE_HINT["eyes"])
        scene = (
            f"{_EYE_CLOSE_SCENE} {ctx}, {tod_s}. {pose} "
            "Window-light atmosphere only. Hands out of frame."
        )
        beat["dev_scene_builder"] = "eye_close"
        framing_kind = "eye_close"
    elif st == "object_focus":
        focus_setting = setting or _OBJECT_FOCUS_TEXTURE_SETTING
        scene, framing_kind = object_focus_scene_text(
            key_object,
            step=focus_step,
            setting=focus_setting,
            hand_interaction=bool(beat.get("hand_held_motif")),
            keep_setting=False,
            painterly=True,
        )
        if scene:
            scene = scene[0].upper() + scene[1:]
        beat["dev_scene_builder"] = "object_focus"
    elif (
        st == "couple"
        and _WIDE_COUPLE_PLACE_RE.search(f"{setting} {key_object}")
        and not couple_bed_pose_clause(setting, key_object, st)
    ):
        scene, framing_kind = couple_wide_silhouette_scene_text(
            setting, tod, step=focus_step
        )
        beat["silhouette_framing"] = "wide_couple"
        if riso_lock:
            scene = f"{scene} {_RISO_RETRO_BACKLIT_COUPLE}"
            beat["dev_scene_builder"] = "couple_wide_backlit_silhouette"
        else:
            beat["dev_scene_builder"] = "couple_wide_silhouette"
    elif st == "silhouette":
        chars = ident.get("characters") or {}
        tmpl = str(chars.get("silhouette") or "")
        char = _fill_slots(tmpl, expression=expr)
        if char:
            char = char[0].upper() + char[1:]
        pose = str(beat.get("pose_hint") or "").strip()
        pose_bit = f" {pose}." if pose else ""
        if str(beat.get("silhouette_framing") or "") == "wide_couple":
            scene, framing_kind = couple_wide_silhouette_scene_text(
                setting, tod, step=focus_step
            )
            if riso_lock:
                scene = f"{scene} {_RISO_RETRO_BACKLIT_COUPLE}"
                beat["dev_scene_builder"] = "couple_wide_backlit_silhouette"
            else:
                beat["dev_scene_builder"] = "couple_wide_silhouette"
        else:
            scene, framing_kind = silhouette_scene_text(
                char,
                key_object_prompt,
                setting,
                tod,
                step=focus_step,
                pose_bit=pose_bit,
            )
            beat["dev_scene_builder"] = "silhouette"
    else:
        chars = ident.get("characters") or {}
        backlit = bool(riso_lock and st in {"woman", "man", "couple"})
        if backlit:
            tmpl = str(_RISO_RETRO_CHAR.get(st) or "")
        else:
            tmpl = str(
                _DEV_CHAR_OVERRIDE.get(st)
                or chars.get(st)
                or chars.get("woman")
                or ""
            )
        char = _fill_slots(tmpl, expression=expr)
        if char:
            char = char[0].upper() + char[1:]
        pose = str(beat.get("pose_hint") or "").strip()
        if (
            st == "couple"
            and not pose
            and couple_bed_pose_clause(setting, key_object, st)
        ):
            pose = "sitting on the edge of the bed with feet on the floor"
        pose_bit = f" {pose}." if pose else ""
        couple_bit = f" {_COUPLE_SEPARATION}" if st == "couple" else ""
        env_stem = _object_stem(key_object)
        if backlit:
            framing = lighting_figure_framing(beat)
            if st == "couple":
                framing = f"{_RISO_RETRO_BACKLIT_COUPLE} {framing}"
            beat["dev_scene_builder"] = f"{st}_backlit_silhouette"
        elif env_stem in {"rain", "window", "horizon", "platform"}:
            framing = (
                "Seen from behind or in deep profile, looking out into the space."
            )
            beat["dev_scene_builder"] = f"{st}_avert_window"
        else:
            framing = _dev_avert_framing(beat)
            beat["dev_scene_builder"] = f"{st}_avert"
        hand_pose = _HAND_POSE
        if is_figure_only_beat(beat) or beat.get("force_print_silhouette"):
            hand_pose = "Hands out of frame or hidden in sleeves."
        scene = (
            f"{char} in {setting}, {tod}.{pose_bit}{couple_bit} "
            f"Hands empty. {framing} {hand_pose}"
        ).strip()

    if framing_kind:
        beat["object_focus_step"] = max(0, min(int(focus_step), 2))
        beat["object_focus_framing"] = framing_kind

    bed_pose = couple_bed_pose_clause(setting, key_object, st)
    if bed_pose:
        scene = f"{scene} {bed_pose}".strip()

    shape_bit = ""
    if str(beat.get("anchor_beat") or "") in {"introduce", "callback"}:
        shape_bit = anchor_shape_clause(key_object, retry=int(focus_step or 0) > 0)

    anchor = resolve_visual_anchor(beat)
    beat["visual_anchor_hint"] = anchor
    close_v = str(beat.get("close_variant") or "")
    place = str(beat.get("atmosphere_place") or "").strip()
    world = f"Same place every beat: {place}." if place else ""
    cap_text = str(beat.get("text") or beat.get("beat_text") or "")
    rain_bit = (
        no_rain_clause(beat) if not _RAIN_WORD_RE.search(cap_text) else ""
    )
    # Stage 2 coherent scene paragraph. Sanitize before any template suffix
    # so leftover "with." / "crack." never splice into the next clause.
    from core.economic_reel_lofi.visual_concept import _sanitize_scene_text

    concept_scene = _sanitize_scene_text(
        str(beat.get("scene_description") or beat.get("visual_concept") or "")
    )
    if concept_scene:
        scene = concept_scene
        world = ""
        isolation = ""
        coverage = ""
        if close_v == "portrait_close":
            who = str(beat.get("close_character") or beat.get("subject_type") or "woman")
            if who not in {"woman", "man"}:
                who = "woman"
            appearance = character_sheet_prompt(who)
            beat["character_appearance"] = appearance
            if appearance not in scene:
                scene = f"{appearance}. {scene}"
            motif = str(
                beat.get("episode_spoken_motif") or beat.get("episode_anchor_name") or ""
            )
            cap = str(beat.get("text") or beat.get("beat_text") or "")
            obj = str(beat.get("key_object") or motif or "")
            if line_requires_spoken_motif(cap, motif) and obj:
                scale = face_object_scale_clause(obj, who)
                if "smaller than" not in scene.lower():
                    scene = f"{scene} {scale}"
    if st == "object_focus" and not concept_scene:
        isolation = ""
        coverage = ""
        if place:
            world = "Same episode place, already established."
    elif close_v == "portrait_close" and not concept_scene:
        isolation = (
            "Furnished room behind her: walls, fabric, furniture as gouache "
            "color blocks with paper grain."
        )
        coverage = "High-neck sweater covering throat, collarbones, shoulders, and chest to the jaw."
    elif not concept_scene:
        isolation = ""
        coverage = ""
    stem = str(beat.get("episode_anchor_stem") or "").strip()
    if not stem and isinstance(beat.get("episode_anchor_name"), str):
        stem = _object_stem(str(beat.get("episode_anchor_name") or ""))
    motif = str(beat.get("episode_spoken_motif") or beat.get("episode_anchor_name") or stem)
    # Only tell Flux to keep the motif recognizable when it is actually in this beat.
    anchor_stem_bit = ""
    if stem and scene_shows_motif(beat, motif or stem) and not beat.get("hook_template"):
        anchor_stem_bit = (
            f"Episode motif {stem}: keep that object recognizable when it is in frame."
        )
    expand = ""
    if not beat.get("hook_template"):
        expand = _dev_expanded_scene_detail(
            beat,
            st,
            framing_kind or str(beat.get("object_focus_framing") or ""),
            profile_name,
        )
    parts = [
        open_b,
        tech_b,
        mood_b,
        scene,
        expand,
        world,
        isolation,
        coverage,
        shape_bit,
        anchor_stem_bit,
        _RISO_FLAT_PRINT if riso_lock else "",
        sat_g,
        palette_sentence,
        f"The image must visually embody: {anchor}." if anchor else "",
        rain_bit,
        text_g,
        fmt_b,
        spoken_line_prop_guard(beat),
    ]
    beat["visual_identity_profile"] = profile_name
    prompt = _strip_positive_negation(" ".join(p for p in parts if p))
    prompt = ensure_object_focus_texture_prompt(prompt, beat)
    prompt = ensure_hook_solid_shape_prompt(prompt, beat)
    prompt = ensure_figure_environment_prompt(prompt, beat)
    prompt = ensure_portrait_skin_light_prompt(prompt, beat)
    prompt = scrub_assembled_prompt(prompt, beat)
    if paint_test:
        prompt = scrub_artwork_medium_words(prompt)
    print(
        f"[LOFI identity v2-dev] scene={beat.get('scene')} "
        f"prompt_words={len(prompt.split())} lighting={beat.get('palette_key')} "
        f"type={st} object={key_object!r} paint_test={int(paint_test)}"
    )
    return prompt


def apply_v2_prompts_to_lines_dev(
    lines: list[dict[str, Any]],
    *,
    theme_row: dict[str, Any] | None = None,
    profile: str | None = None,
    vary_imagery: bool = False,
    lock_visuals: bool = False,
) -> list[dict[str, Any]]:
    """Dev twin of apply_v2_prompts_to_lines. Same enforce / variety; Dev assemble."""
    from core.economic_reel_lofi.visual_identity_profiles import (
        DEFAULT_VISUAL_IDENTITY_PROFILE,
    )

    pool = setting_object_pool(theme_row)
    n = len(lines)
    profile_name = str(
        profile
        or getattr(lofi_cfg, "DEFAULT_VISUAL_IDENTITY_PROFILE", "")
        or DEFAULT_VISUAL_IDENTITY_PROFILE
    )
    has_concepts = any(
        isinstance(r, dict) and str(r.get("visual_concept") or r.get("scene_description") or "").strip()
        for r in lines
    )
    print(
        "[LOFI identity v2-dev] assemble_v2_prompt_dev | "
        f"profile={profile_name} | vary_imagery={int(vary_imagery)} | "
        f"lock_visuals={int(lock_visuals)} | stage2_concepts={int(has_concepts)}"
    )
    if has_concepts:
        # Stage 2 owns setting/object. Do not stamp _EPISODE_WORLDS.
        print("[LOFI identity v2-dev] skip _EPISODE_WORLDS — Stage 2 concepts present")
    elif not lock_visuals:
        _choose_and_apply_episode_atmosphere(lines, theme_row=theme_row)
    if not has_concepts:
        enforce_concrete_beat_visuals(
            lines, pool=pool, vary_imagery=vary_imagery, lock_visuals=lock_visuals
        )
        if lock_visuals or not _episode_has_atmosphere(lines):
            _apply_setting_archetype_pass(lines, pool, lock_visuals=lock_visuals)
    theme = ""
    module = "relationship"
    if lines and isinstance(lines[0], dict):
        theme = str(lines[0].get("episode_theme") or "")
        module = str(lines[0].get("episode_module") or "relationship") or "relationship"
    has_close = any(
        isinstance(r, dict)
        and str(r.get("close_variant") or "") in {"portrait_close", "silhouette"}
        for r in lines
    )
    if not has_close and not has_concepts:
        assign_close_beat(lines, theme=theme, module=module)
    if not has_concepts:
        cap_window_primary_beats(lines)
        constrain_unspoken_support_props(lines)
        apply_meaning_driven_variety(lines)
    elif not any(
        isinstance(r, dict) and str(r.get("palette_temp") or "").strip()
        for r in lines
    ):
        apply_meaning_driven_variety(lines)
    apply_object_focus_spoken_motif(lines)
    apply_handheld_motif_beat(lines)
    from core.economic_reel_lofi.visual_concept import _enforce_macro_scene

    for row in lines:
        if isinstance(row, dict) and not concept_is_locked(row):
            _enforce_macro_scene(row)
    stem0 = _episode_anchor_stem(lines)
    name0 = episode_anchor_name(lines)
    for i, row in enumerate(lines):
        if not isinstance(row, dict):
            continue
        if stem0 and not str(row.get("episode_anchor_stem") or "").strip():
            row["episode_anchor_stem"] = stem0
        if name0 and not str(row.get("episode_anchor_name") or "").strip():
            row["episode_anchor_name"] = name0
        if has_concepts:
            prompt = assemble_v2_prompt_dev(row, profile=profile_name)
        else:
            normalize_beat_visuals(row, index=i, scene_count=n, pool=pool)
            prompt = assemble_v2_prompt_dev(row, profile=profile_name)
        row["visual_prompt"] = prompt
        row["visual_identity"] = "v2_dev"
        row["riso_id"] = (
            f"identity_v2_dev_{row.get('subject_type')}_{row.get('arc_position')}"
        )
        row["riso_palette"] = row.get("palette_key")
    variety = score_episode_variety(lines)
    print(f"[LOFI variety] {variety.get('summary')}")
    if lines and isinstance(lines[0], dict):
        lines[0]["episode_variety"] = variety
    return lines

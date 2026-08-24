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
    {"setting": "kitchen at dawn", "key_object": "kettle just clicked off"},
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
        "earth-tone sweater, midi dress, or long coat, no patterns"
    )


def portrait_close_identity_clause(who: str) -> str:
    """Painterly head-and-shoulders portrait — not an eye-only macro."""
    sheet = character_sheet_prompt(who)
    return (
        f"Medium close-up portrait of {sheet}, head and shoulders in frame, "
        "full face visible — eyes, nose, mouth, and some hair. "
        "Classic portrait shot, waist-up or head-and-shoulders, painterly "
        "illustration — not an extreme macro crop on eyes alone. "
        "Shoulders and chest fully covered by dress or sweater fabric. "
        "Same painterly risograph illustration as the rest of the episode: "
        "ink line, flat printed color, paper grain, canvas tooth. "
        "Not photoreal skin, not a camera portrait, not glossy beauty lighting, "
        "not a rendered-eye close-up, not the retired eye_close crop."
    )


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


def only_object_clause(key_object: str, *, keep_host: bool = False) -> str:
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
    """
    obj = _sanitize_visual_phrase(key_object) or "the requested object"
    if keep_host:
        return (
            f"{obj} is the subject, shown inside its containing whole so the "
            f"part is not a disembodied prop in an empty room. "
            f"No extra still-life objects, no clutter."
        )
    return (
        f"Only {obj} is visible in frame, resting alone on a bare surface. "
        f"Nothing else occupies the frame — no additional items of any kind, "
        f"no clutter, no extra objects."
    )


# Atmospheric key_objects are not still-life props. Name a concrete visible
# surface so Flux does not substitute a mug/vase still-life.
_ATMOSPHERIC_FOCUS_NOUN: dict[str, str] = {
    "rain": "rain-streaked window glass with water droplets on the pane",
    "window": "empty window pane with rain droplets on the glass",
    "seat": "the empty passenger seat of a car, seen through the open car door",
    "horizon": "empty distant horizon line",
    "platform": "empty train-platform ground as a flat color field",
}
_OBJECT_FOCUS_FRAMING = ("macro_no_setting", "tighter_macro", "off_workspace")
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


def _off_workspace_place(key_object: str) -> str:
    """Relocate a workspace still-life object somewhere the desk prior cannot latch."""
    stem = _object_stem(key_object)
    if stem in {"paper", "mail", "letter", "envelope"}:
        return "on a sunlit windowsill"
    if stem in {"phone"}:
        return "on a windowsill at dusk"
    if stem in {"journal", "notebook"}:
        return "on a hallway floor by a closed door"
    return "on a stoop by the front door"


def object_focus_scene_text(
    key_object: str,
    *,
    step: int = 0,
    setting: str = "",
    hand_interaction: bool = False,
    keep_setting: bool = False,
    painterly: bool = False,
) -> tuple[str, str]:
    """
    object_focus framing ladder. Step 0 drops named rooms so Schnell's
    desk-at-night still-life prior has nothing to latch onto. Later steps
    only run after an INTRUDER gate fail.

    keep_setting + painterly: used for episode-anchor callbacks so the
    motif stays in the same atmospheric place as the rest of the reel
    instead of collapsing to a flat ink still-life on blank paper.
    """
    obj = _object_focus_visual_noun(key_object, setting)
    keep_host = _needs_containing_whole(key_object, setting)
    extra = _OBJECT_FOCUS_HAND_OK if hand_interaction else _OBJECT_FOCUS_GUARD
    only = only_object_clause(obj, keep_host=keep_host)
    text_clause = _TEXT_SURFACE_CLAUSE if _object_stem(obj) in _TEXT_BEARING_STEMS else ""
    step = max(0, min(int(step), 2))
    kind = _OBJECT_FOCUS_FRAMING[step]
    if painterly or keep_setting:
        place = (setting or "").strip() or "an atmospheric printed scene"
        scene = (
            f"A painterly risograph still-life of {obj} in {place}. "
            f"The object sits in a real environment with paper grain, color "
            f"gradient, and canvas texture — not a woodcut, not a flat ink "
            f"diagram on blank paper, not isolated linework. "
            f"{extra} {only} {text_clause}"
        )
        return " ".join(scene.split()), "painterly_setting"
    if keep_host:
        # Isolation ("no furniture / no named room") would strip the host
        # and leave a floating part — the original seat-in-empty-room bug.
        scene = (
            f"A bold hand-inked illustration of {obj}, "
            f"drawn flat with visible ink outlines, not photographed. "
            f"The containing whole stays in frame so this is not a floating part. "
            f"Printed atmosphere, no extra still-life, no clutter. "
            f"{extra} {only} {text_clause}"
        )
        return " ".join(scene.split()), "host_context"
    if step <= 0:
        # "macro crop" / "camera bokeh" / "lens blur" are camera vocabulary —
        # even negated ("no camera bokeh"), naming a photography concept at
        # all still anchors Schnell toward a photographic render (same lesson
        # as the mug/text fixes: describe the illustration positively, don't
        # negate a photo concept). This step carried nearly every photoreal-
        # drift critic flaw observed in the 2026-08-21 combined probe.
        scene = (
            f"A bold hand-inked illustration of {obj} filling the frame, "
            f"drawn flat with visible ink outlines, not photographed. "
            f"Printed halftone background, no named room, no furniture, "
            f"no other objects. "
            f"{extra} {only} {text_clause}"
        )
    elif step == 1:
        # "Flat featureless background" (prior wording) starved the frame of
        # the grainy halftone print texture the style block asks for
        # everywhere else, which is what collapsed uniq16 below the linework
        # floor. Keep every INTRUDER-safe exclusion; only add texture back.
        # "Extreme close-up" is also camera vocabulary — replaced for the same
        # reason as step 0.
        scene = (
            f"An illustrated close study of {obj} alone, drawn to occupy most "
            f"of the frame, not photographed. "
            f"Background keeps the same grainy halftone print texture as the object, "
            f"muted flat color with visible ink-dot grain, no gradient, no furniture, "
            f"no room, no other objects, no distinct background shapes. "
            f"{extra} {only} {text_clause}"
        )
    else:
        # "Close focus" is the same camera-vocabulary pattern as steps 0/1.
        place = _off_workspace_place(obj)
        scene = (
            f"An illustrated view of {obj} {place}, drawn as the only object "
            f"in frame, not photographed. "
            f"Tight crop, background keeps the grainy halftone print texture, "
            f"muted color but not flat or blank, no furniture, no room clutter. "
            f"{extra} {only} {text_clause}"
        )
    return " ".join(scene.split()), kind


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
            f"Hands empty. No furniture, no room clutter. {only}"
        )
    else:
        scene = (
            f"{char}, {obj} only, featureless background, no named room. "
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
            f"standing apart in {setting}, {tod}. Open plane, small in the frame, "
            f"not a close portrait. Hands empty at their sides, not holding anything. "
            f"No small objects near the faces."
        )
    elif step == 1:
        scene = (
            f"Two distant silhouettes of a man and a woman on an open plane, {tod}. "
            f"Wide landscape, empty hands, no objects held. Featureless sky."
        )
    else:
        scene = (
            f"Two small featureless figures on a wide horizon line, {tod}. "
            f"Hands empty. No furniture, no held objects."
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
        st = "woman" if index % 2 == 0 else "silhouette"
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

# Caption noun → objects that count as a literal or metaphorical match.
# Prefer distinctive props over mug/chair — those two over-index in Flux Schnell.
_CAPTION_OBJECT_ALIASES: dict[str, tuple[str, ...]] = {
    "seat": ("chair", "chairs", "empty passenger seat"),
    "seats": ("chair", "chairs"),
    "clap": ("hands", "chair"),
    "apology": ("open doorway", "closed suitcase on the floor"),
    "room": ("doorway", "door", "unmade empty bed"),
    "name": ("two coats hanging", "folded blanket"),
    "proof": ("rain on the window", "empty platform"),
    "verdict": ("two coats hanging", "open doorway"),
    "doorway": ("door", "doors", "open doorway"),
    "belonging": ("empty chair", "two coats hanging"),
    "silence": ("rain on the window", "open doorway"),
    "hurt": ("door", "coat"),
    "love": ("two coats hanging", "folded blanket"),
    "steadiness": ("two coats hanging", "folded blanket"),
    "feeling": ("rain on the window", "distant horizon"),
    "chaos": ("unmade empty bed", "closed suitcase on the floor"),
    "noise": ("empty platform", "streetlamp"),
    "fight": ("open doorway", "open door"),
    "war": ("open doorway",),
    "peace": ("rain on the window", "folded blanket", "kettle"),
    "cost": ("closed suitcase on the floor", "empty platform"),
    "winning": ("distant horizon", "rain on the window"),
    "connection": ("two coats hanging", "folded blanket"),
    "choice": ("open doorway", "closed suitcase on the floor"),
    "walking": ("open doorway", "empty platform"),
    "prize": ("distant horizon", "folded blanket"),
}

# Abstract/emotional lines → meaning-specific stand-ins (not a generic chair).
_MEANING_CLUSTERS: tuple[dict[str, Any], ...] = (
    {
        "id": "disorder",
        "weight": 3,
        "tokens": frozenset({
            "chaos", "chaotic", "noise", "loud", "mess", "messy", "disorder",
            "chased", "chase", "storm", "frantic", "scattered", "clutter",
        }),
        "pairs": (
            {"setting": "bedroom, unmade empty bed", "key_object": "unmade empty bed"},
            {"setting": "hallway with a closed suitcase on the floor", "key_object": "closed suitcase on the floor"},
            {"setting": "empty train platform at night", "key_object": "empty platform"},
        ),
        "expression": "restless, searching",
    },
    {
        "id": "leaving",
        "weight": 3,
        "tokens": frozenset({
            "walking", "walk", "walks", "walked", "leaving", "leave", "leaves",
            "turning", "turn", "away", "exit", "go", "going",
        }),
        "pairs": (
            {"setting": "hallway near an open door", "key_object": "open doorway"},
            {"setting": "empty train platform at night", "key_object": "empty platform"},
            {"setting": "hallway with a closed suitcase on the floor", "key_object": "closed suitcase on the floor"},
        ),
        "expression": "turning away",
        "pose": "body turned mid-step toward the door, hands empty, not seated",
    },
    {
        "id": "conflict",
        "weight": 2,
        "tokens": frozenset({
            "fight", "fighting", "war", "argument", "slammed", "slam",
            "yelling", "yell", "battle",
        }),
        "pairs": (
            {"setting": "hallway near an open door", "key_object": "open doorway"},
            {"setting": "empty park path at dusk", "key_object": "distant horizon"},
        ),
    },
    {
        "id": "depletion",
        "weight": 2,
        "tokens": frozenset({
            "cost", "costs", "spent", "empty", "depleted", "worn", "tired",
            "nothing", "leftover", "remain", "winning", "won",
        }),
        "pairs": (
            {"setting": "empty train platform at night", "key_object": "empty platform"},
            {"setting": "parked car at evening, door open", "key_object": "empty passenger seat of a car"},
            {"setting": "rain-streaked window at dusk", "key_object": "rain on the window"},
        ),
        "expression": "hollow, aware",
    },
    {
        "id": "proof",
        "weight": 2,
        "tokens": frozenset({"proof", "prove", "someone", "missed", "stayed"}),
        "pairs": (
            {"setting": "street corner under a streetlamp", "key_object": "streetlamp"},
            {"setting": "coat hook by the door", "key_object": "two coats hanging"},
        ),
    },
    {
        "id": "learning",
        "weight": 2,
        "tokens": frozenset({
            "learned", "learn", "learning", "noticed", "notice", "finally",
            "understood", "realize", "realized", "score", "keeping",
        }),
        "pairs": (
            {"setting": "rain-streaked window at dusk", "key_object": "rain on the window"},
            {"setting": "window overlooking a quiet street", "key_object": "empty chair"},
        ),
    },
    {
        "id": "choice",
        "weight": 1,
        "tokens": frozenset({"choice", "choose", "choosing", "instead"}),
        "pairs": (
            {"setting": "hallway near an open door", "key_object": "open doorway"},
            {"setting": "hallway with a closed suitcase on the floor", "key_object": "closed suitcase on the floor"},
        ),
    },
    {
        "id": "connection",
        "weight": 1,
        "tokens": frozenset({
            "connection", "love", "loving", "together", "stay", "staying",
            "caring", "care",
        }),
        "pairs": (
            {"setting": "coat hook by the door", "key_object": "two coats hanging"},
            {"setting": "sofa in evening light", "key_object": "folded blanket"},
            {"setting": "empty park path at dusk", "key_object": "distant horizon"},
        ),
        "expression": "soft, unguarded",
    },
    {
        "id": "peace",
        "weight": 2,
        "tokens": frozenset({
            "peace", "quiet", "calm", "still", "silence", "silent",
            "boring", "safety", "healed", "healing", "home",
        }),
        "pairs": (
            {"setting": "rain-streaked window at dusk", "key_object": "rain on the window"},
            {"setting": "bedroom, unmade empty bed", "key_object": "unmade empty bed"},
            {"setting": "kitchen at dawn", "key_object": "kettle just clicked off"},
        ),
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
    "two distinct bodies, not fused into one figure, no phantom extra hands."
)
_OBJECT_FOCUS_GUARD = (
    "The object sits alone, centered, filling the frame. "
    "No hands, no fingers, no grip, no wrist, no person, no face."
)
_OBJECT_FOCUS_HAND_OK = (
    "One hand may hold the object only if a plausible wrist and forearm "
    "continue off-frame in a natural direction. No floating grip, "
    "no disconnected hand, no top-down impossible angle."
)
_CLOSE_PORTRAIT = (
    "Close portrait, face and torso filling the frame, "
    "not a wide establishing shot."
)
_HAND_POSE = (
    "Hands empty and away from the face: at the sides, in the lap, or out of frame. "
    "Not holding any small object. Not raising anything toward the face."
)
_WIDE_COUPLE_PLACE_RE = re.compile(
    r"\b(platform|station|park|landscape|horizon|street|field|corner|curb)\b",
    re.IGNORECASE,
)
_SATURATION_GUARD = (
    "Keep colors saturated and printed, not monochrome, not grayscale."
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
    "texture, or flat printed color — never a legible letter, number, or "
    "word, anywhere in the frame. This includes skin, clothing, hair, "
    "background, paper, signs, screens, and all four corners of the frame. "
    "No watermark, no signature, no caption, no logo, no small stamped text "
    "tucked in a corner — the illustration fills the frame edge to edge with "
    "scene content only, nothing else."
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
    }
)
_TEXT_SURFACE_CLAUSE = (
    "The visible surface shows only abstract ink texture, creases, and blank "
    "or smudged space — no legible words, no coherent letters, no readable "
    "postage or printed address."
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

    palette_mismatches: list[dict[str, Any]] = []
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
    if arch.get("holds_episode"):
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
    if comp.get("holds_episode"):
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


def visual_tied_to_caption(text: str, setting: str, key_object: str) -> bool:
    """True when setting/object shares a caption noun or a meaning stand-in."""
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
    row["subject_type"] = _PREFERRED_SUBJECTS[index % len(_PREFERRED_SUBJECTS)]
    row["subject_remap"] = f"{st or 'empty'}_to_character_portrait"


def _apply_framing_variety(lines: list[dict[str, Any]]) -> None:
    """
    Target mix for a 9-beat reel: ~5–6 person shots, 3–4 object_focus/silhouette.
    Cap couple at 3 (Schnell fusion at 4 steps).
    """
    n = len(lines)
    if n <= 0:
        return

    couple_idx = [
        i for i, row in enumerate(lines)
        if isinstance(row, dict)
        and str(row.get("subject_type") or "").strip().lower() == "couple"
    ]
    if len(couple_idx) > _MAX_COUPLE_PER_EPISODE:
        keep = set(couple_idx[-_MAX_COUPLE_PER_EPISODE:])
        for i in couple_idx:
            if i in keep:
                continue
            row = lines[i]
            row["subject_type"] = "woman" if i % 2 == 0 else "man"
            row["subject_remap"] = "couple_cap_to_single"
            print(
                f"[LOFI identity v2] couple-cap scene={i + 1} "
                f"-> {row['subject_type']}"
            )

    def _stype(i: int) -> str:
        return str(lines[i].get("subject_type") or "").strip().lower().replace(" ", "_")

    variety_idx = [i for i in range(n) if _stype(i) in _VARIETY_SUBJECTS]
    person_idx = [i for i in range(n) if _stype(i) in _PREFERRED_SUBJECTS]

    need = max(0, _MIN_VARIETY_SHOTS - len(variety_idx))
    if need:
        candidates = [
            i for i in person_idx
            if _stype(i) != "couple" and i != 0
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

    if lines and isinstance(lines[0], dict):
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
            lines[i]["subject_type"] = "woman" if i % 2 == 0 else "man"
            lines[i]["subject_remap"] = "variety_cap_to_person"


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
        "painterly illustration — not an extreme macro crop on eyes alone. "
        "Not photoreal skin."
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
        row["subject_type"] = who if who in {"woman", "man"} else "woman"
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
    return {"setting": "kitchen at dawn", "key_object": "kettle just clicked off"}


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
    """Keep callback/introduce beats on the episode motif, not a retied stand-in."""
    name = episode_anchor_name(lines)
    if not name:
        return
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
        row["key_object"] = name
        row["episode_anchor_name"] = name
        if str(row.get("anchor_beat") or "") == "callback" and intro_setting:
            row["setting"] = intro_setting


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
    return "rain on the window"


def pick_caption_anchored_pair(
    text: str,
    pool: list[dict[str, str]],
    *,
    used: set[tuple[str, str]] | None = None,
    index: int = 0,
    used_stems: dict[str, int] | None = None,
) -> dict[str, str]:
    """Pick a pool/meaning pair that traces to this caption, avoiding overused stems."""
    del index
    pairs = _candidate_pairs(pool, _best_meaning_cluster(text))
    used = used or set()
    used_stems = used_stems or {}
    avoid = {
        stem for stem, n in used_stems.items() if n >= _MAX_OBJECT_STEM_HITS
    }
    cluster = _best_meaning_cluster(text)
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
        setting = "cluttered kitchen table"
    elif "chair" in needle or "seat" in needle:
        setting = "window overlooking a quiet street"
    return {"setting": setting, "key_object": obj}


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


def enforce_concrete_beat_visuals(
    lines: list[dict[str, Any]],
    *,
    pool: list[dict[str, str]] | None = None,
    vary_imagery: bool = False,
    lock_visuals: bool = False,
) -> list[dict[str, Any]]:
    """
    Every beat must carry a setting + key_object that traces to that beat's
    caption (literal noun, mapped stand-in, or emotion-tied object).
    Keep writer object_focus / silhouette when valid; fill a 3–4 variety mix
    if the episode is all portraits. Cap couple at 3.
    The same object stem may appear at most once in an episode; a repeat
    rotates to a different stand-in.
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
        tied = visual_tied_to_caption(text, setting, key_object)
        concrete = setting_is_concrete(setting, key_object)
        stem = _object_stem(key_object)
        stem_cap = 2 if (anchor_stem and stem == anchor_stem) else _MAX_OBJECT_STEM_HITS
        overused = used_stems.get(stem, 0) >= stem_cap
        handheld = _is_banned_key_object(key_object)
        _apply_cluster_pose(row, text)
        blank_visual = not setting.strip() or not key_object.strip()
        if (not lock_visuals) and (not tied or not concrete or overused or handheld):
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
        _remap_signage_traps(lines, pairs)
        _apply_framing_variety(lines)
        assign_beat_functions(lines)
        assign_close_beat(lines, theme=theme, module=module)
    restore_anchor_visual_fields(lines)
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
            hand_interaction=False,
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
            framing = _CLOSE_PORTRAIT
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
        tech_b,
        palette_sentence,
        _SATURATION_GUARD,
        _TEXT_LEGIBILITY_GUARD,
        garment_coverage_clause(st, str(beat.get("close_variant") or "")),
        mood_b,
        fmt_b,
    ]
    return " ".join(p for p in parts if p)


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
    print(
        "[LOFI identity v2] assemble_v2_prompt | painterly=OFF | "
        f"vary_imagery={int(vary_imagery)} | lock_visuals={int(lock_visuals)} | "
        "bank=visual_identity_bank_v2.json"
    )
    enforce_concrete_beat_visuals(
        lines, pool=pool, vary_imagery=vary_imagery, lock_visuals=lock_visuals
    )
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
    if not has_close:
        assign_close_beat(lines, theme=theme, module=module)
    assign_episode_lighting(lines, module=module, theme=theme)
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
    "dusk": "Painted dusk: warm amber against cooler shadow, not a photograph.",
    "evening": "Painted evening: lamp-warm interiors, window as a dark plane.",
    "night": "Painted night: indigo and charcoal, small warm windows.",
    "overcast": "Painted overcast: sage grey and cream flats, even daylight, no amber sunset.",
    "rain": "Painted rain: cool grey wet flats, streaked glass, no amber sunset.",
}
_DEV_PERSON_MOOD = (
    "Quiet melancholic mood, not glamorous. Everyday clothes, fully covered by "
    "dress or sweater fabric: back, shoulders, chest, and torso clothed; "
    "not off-shoulder, not a fashion or beauty close-up, not a sensual pose."
)
HUMAN_FIGURE_TYPES = frozenset({"woman", "man", "couple", "silhouette"})
GARMENT_COVERAGE_DIRECTIVE = (
    "fully covered by dress fabric, back and shoulders clothed, chest and torso "
    "clothed; no bare back, no exposed shoulders, no cleavage, no nape-to-waist "
    "skin, not off-shoulder"
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
    "Seen from behind, face not visible, looking into the space.",
    "Deep profile, face mostly turned away — features only a few brushstrokes.",
    "Cropped at the hairline: crown, hair, and shoulders only. No eyes, no frontal face.",
    "Distant full-body figure in the room; the face is a few brush marks, not a rendered portrait.",
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
        "[expression] postures, fully covered by fabric, back and shoulders clothed, "
        "not posed for a camera"
    ),
}
# Locked style-riso_painting_retro_vintage (risograph-on-Dev). Woman/man only:
# figure is a backlit silhouette; environment keeps full painterly richness.
_RISO_RETRO_TOD: dict[str, str] = {
    "dawn": "Dawn print: pale paper sky, long quiet shadow blocks, not a photograph.",
    "morning": "Morning print: cream light, muted interiors as flat color planes.",
    "afternoon": "Afternoon print: midtone rooms, still air, visible paper tooth.",
    "dusk": (
        "Dusk print: warm amber flats against cooler shadow blocks, "
        "not a photographed sunset."
    ),
    "evening": (
        "Evening print: lamp-warm interiors as flat color, window as a dark plane."
    ),
    "night": (
        "Night print: indigo and charcoal flats, small warm windows, "
        "no neon photography."
    ),
    "overcast": (
        "Overcast print: sage grey and cream flats, flat daylight, "
        "no amber sunset, no doorway backlight."
    ),
    "rain": (
        "Rain print: cool grey wet flats, streaked glass, no amber sunset."
    ),
}
_RISO_RETRO_BACKLIT_FIGURE = (
    "Positioned against a bright window, sunset, or open doorway so the body "
    "reads as a flat unlit silhouette shape. No rendered skin, no facial "
    "features, no glossy portrait, no beauty lighting, not a glamour close-up. "
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
        "label": "warm sunset backlight through doorway/window",
        "tod": "dusk",
        "figure_framing": _RISO_RETRO_BACKLIT_FIGURE,
        "atmos": (
            "Dusk print: warm amber flats against cooler shadow blocks, "
            "not a photographed sunset."
        ),
    },
    "overcast_daylight": {
        "label": "overcast daylight",
        "tod": "overcast",
        "figure_framing": (
            "Even overcast daylight, no hard backlight, figure readable as a "
            "printed shape in grey-cream air. Not a sunset doorway silhouette. "
            "No rendered skin, no facial features, no glossy portrait. "
            "Three-quarter or full-figure in the middle of the frame."
        ),
        "atmos": (
            "Overcast print: sage grey and cream flats, flat daylight, "
            "no amber sunset, no doorway backlight."
        ),
    },
    "blue_hour_streetlight": {
        "label": "blue-hour / night streetlight",
        "tod": "night",
        "figure_framing": (
            "Blue-hour streetlight: cool indigo ground, a small warm lamp as a "
            "hard color plane — not sunset through a doorway. No rendered skin, "
            "no facial features, no glossy portrait. "
            "Three-quarter or full-figure in the middle of the frame."
        ),
        "atmos": (
            "Night print: indigo and charcoal flats, small warm windows, "
            "no neon photography."
        ),
    },
    "indoor_lamp_glow": {
        "label": "indoor lamp glow",
        "tod": "evening",
        "figure_framing": (
            "Indoor lamp glow, a warm pool of light on a table or wall, figure "
            "in a furnished room — not a sunset silhouette in a doorway. "
            "No rendered skin, no facial features, no glossy portrait. "
            "Three-quarter or full-figure in the middle of the frame."
        ),
        "atmos": (
            "Evening print: lamp-warm interiors as flat color, window as a dark plane."
        ),
    },
    "rainy_grey": {
        "label": "rainy grey light",
        "tod": "rain",
        "figure_framing": (
            "Rainy grey light, wet glass, cool paper sky — not warm sunset "
            "backlight. No rendered skin, no facial features, no glossy portrait. "
            "Three-quarter or full-figure in the middle of the frame."
        ),
        "atmos": (
            "Rain print: cool grey wet flats, streaked glass, no amber sunset."
        ),
    },
    "morning_cool": {
        "label": "early morning cool light",
        "tod": "dawn",
        "figure_framing": (
            "Early morning cool light, pale paper sky, long quiet shadows — "
            "not sunset orange. No rendered skin, no facial features, "
            "no glossy portrait. Three-quarter or full-figure in the middle "
            "of the frame."
        ),
        "atmos": (
            "Dawn print: pale paper sky, long quiet shadow blocks, not a photograph."
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
    return str(spec.get("atmos") or "")


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
    "lamp, sky, or open light. No rendered skin, no facial features, no glossy "
    "portrait, no beauty lighting, not a photographed couple. The environment "
    "around them keeps full painterly detail."
)
_RISO_RETRO_CHAR: dict[str, str] = {
    "woman": (
        "a lone woman as a flat unlit clothed silhouette, [expression] posture, "
        "fully covered by dress fabric, back and shoulders clothed, no visible face or skin"
    ),
    "man": (
        "a lone man as a flat unlit clothed silhouette, [expression] posture, "
        "fully covered by fabric, back and shoulders clothed, no visible face or skin"
    ),
    "couple": (
        "two clothed figures as flat unlit silhouettes, a man and a woman, "
        "[expression] postures, fully covered by fabric, back and shoulders clothed, "
        "no visible faces or skin"
    ),
}
_RISO_RETRO_EXPANSION: dict[str, str] = {
    "woman": (
        "Printed atmosphere: visible paper tooth and a slight riso color offset. "
        f"{_DEV_PERSON_MOOD} "
        "The surrounding environment — sky, foliage, room, fabric, furniture — "
        "is painted as flat gouache color blocks with hard printed edges and "
        "visible paper grain, not photo-grain. No photographic light falloff, "
        "no depth-of-field, no lens glow. Only the human figure is a silhouette; "
        "the room stays readable as poster shapes."
    ),
    "man": (
        "Printed atmosphere: visible paper tooth and a slight riso color offset. "
        f"{_DEV_PERSON_MOOD} "
        "The surrounding environment — sky, foliage, room, fabric, furniture — "
        "is painted as flat gouache color blocks with hard printed edges and "
        "visible paper grain, not photo-grain. No photographic light falloff, "
        "no depth-of-field, no lens glow. Only the human figure is a silhouette; "
        "the room stays readable as poster shapes."
    ),
    "couple": (
        "Wide printed landscape: two small unlit silhouette figures, a hard "
        "horizon, empty ground as a flat color field. Distance between them is "
        "the subject. No rendered skin or faces on either figure. The street, "
        "lamp, sky, and buildings are flat printed color planes with paper grain, "
        "not photographic depth."
    ),
    "silhouette": (
        "The figure stays readable against a printed sky or doorway field. "
        "The horizon or jamb is a hard color edge, not a photographed gradient. "
        "Negative space carries the mood."
    ),
    "object_focus": (
        "The object has printed weight: ink contour, interior hatch or flat fill, "
        "paper grain around it. Isolated still-life — one object, no implied hands "
        "just out of frame."
    ),
    "object_focus_anchor": (
        "Printed atmosphere: visible paper tooth and a slight riso color offset. "
        "The street, rain, wall, and wet pavement are flat gouache color blocks "
        "with paper grain — not photo-grain, not a color gradient, not lens light. "
        "No person, no face, no hands, no silhouette figure."
    ),
    "wide_couple": (
        "Wide printed landscape: two small unlit silhouette figures, a hard "
        "horizon, empty ground as a flat color field. Distance between them is "
        "the subject. No rendered skin or faces on either figure. The street, "
        "lamp, sky, and buildings are flat printed color planes with paper grain, "
        "not photographic depth."
    ),
}
_RISO_FLAT_PRINT = (
    "Flat gouache / riso-poster illustration: hard-edged color planes, "
    "paper tooth, slight ink offset. Not a photograph, not cinematic lighting, "
    "not volumetric sunset glow, not lens falloff, not depth-of-field."
)
_DEV_SCENE_EXPANSION: dict[str, str] = {
    "woman": (
        "Painted canvas grain and soft brush texture. "
        f"{_DEV_PERSON_MOOD} "
        "No frontal or three-quarter close-up face."
    ),
    "man": (
        "Painted canvas grain and soft brush texture. "
        f"{_DEV_PERSON_MOOD} "
        "No frontal or three-quarter close-up face."
    ),
    "couple": (
        "Two figures occupy the center third, painted as shapes in space. "
        f"{_DEV_PERSON_MOOD} No frontal close-up faces."
    ),
    "silhouette": (
        "The figure stays readable against a painted sky or doorway field. "
        "Negative space carries the mood."
    ),
    "object_focus": (
        "The object has painted weight: brush contour, canvas grain around it. "
        "Isolated still-life — one object filling the frame on a bare surface. "
        "No door, no doorknob, no implied hands just out of frame."
    ),
    "wide_couple": (
        "Wide painted landscape: two small figures, a hard horizon, empty ground. "
        "Distance between them is the subject."
    ),
}


def _dev_avert_framing(beat: dict[str, Any]) -> str:
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
    text = str(beat.get("text") or beat.get("beat_text") or "").strip()
    words = [
        w for w in re.findall(r"[A-Za-z']+", text)
        if w.lower() not in _DEV_STOP
    ]
    if len(words) >= 3:
        return " ".join(words[:12])
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
            "color, canvas texture. Same illustration technique as the "
            "episode. No photoreal skin, no camera portrait."
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
    from core.economic_reel_lofi.visual_identity_profiles import (
        DEFAULT_VISUAL_IDENTITY_PROFILE,
        get_visual_identity_profile,
    )

    if isinstance(profile, dict) and profile.get("open"):
        ident = profile
        profile_name = str(profile.get("name") or "custom")
    else:
        profile_name = str(
            profile
            or getattr(lofi_cfg, "DEFAULT_VISUAL_IDENTITY_PROFILE", "")
            or DEFAULT_VISUAL_IDENTITY_PROFILE
        )
        ident = get_visual_identity_profile(profile_name)

    open_b = str(ident.get("open") or "").strip()
    tech_b = str(ident.get("technique") or "").strip()
    mood_b = str(ident.get("mood") or "").strip()
    fmt_b = str(ident.get("format") or "").strip()
    guards = ident.get("guards") or {}
    sat_g = str(guards.get("saturation") or _SATURATION_GUARD).strip()
    text_g = str(guards.get("text_legibility") or _TEXT_LEGIBILITY_GUARD).strip()

    st = str(beat.get("subject_type") or "woman")
    expr = str(beat.get("subject_expression") or "sad")
    setting = _sanitize_visual_phrase(str(beat.get("setting") or ""))
    key_object = _sanitize_visual_phrase(str(beat.get("key_object") or ""))
    if str(beat.get("anchor_beat") or "") in {"introduce", "callback"}:
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
    palette_sentence, palette_key = _dev_palette_sentence(ident, act)
    beat["palette_key"] = palette_key
    framing_kind = ""

    from core.economic_reel_lofi.visual_identity_profiles import (
        is_riso_painting_retro_vintage,
    )

    riso_lock = is_riso_painting_retro_vintage(profile_name)

    if str(beat.get("close_variant") or "") == "portrait_close":
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
            f"{key_object_prompt} is atmosphere only, not held in any hand."
        )
        beat["dev_scene_builder"] = "portrait_close"
        framing_kind = "portrait_close"
    elif str(beat.get("close_variant") or "") == "eye_close":
        ctx = str(beat.get("eye_close_context") or setting or "window light")
        tod_s = str(beat.get("time_of_day") or tod or "dusk")
        pose = str(beat.get("pose_hint") or _CLOSE_POSE_HINT["eyes"])
        scene = (
            f"{_EYE_CLOSE_SCENE} {ctx}, {tod_s}. {pose} "
            "Window-light atmosphere only — no named object in frame, "
            "no hands, no sweater, no garment held to the face."
        )
        beat["dev_scene_builder"] = "eye_close"
        framing_kind = "eye_close"
    elif st == "object_focus":
        anchor_focus = str(beat.get("anchor_beat") or "") in {"introduce", "callback"}
        scene, framing_kind = object_focus_scene_text(
            key_object,
            step=focus_step,
            setting=setting,
            hand_interaction=False,
            keep_setting=anchor_focus,
            painterly=anchor_focus,
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
                "Seen from behind or in deep profile, looking out into the space. "
                "Face not shown frontally. Not touching the window or any object."
            )
            beat["dev_scene_builder"] = f"{st}_avert_window"
        else:
            framing = _dev_avert_framing(beat)
            beat["dev_scene_builder"] = f"{st}_avert"
        scene = (
            f"{char} in {setting}, {tod}.{pose_bit}{couple_bit} "
            f"{key_object_prompt} is part of the environment, not held in any hand. "
            f"{framing} {_HAND_POSE}"
        ).strip()

    if framing_kind:
        beat["object_focus_step"] = max(0, min(int(focus_step), 2))
        beat["object_focus_framing"] = framing_kind

    bed_pose = couple_bed_pose_clause(setting, key_object, st)
    if bed_pose:
        scene = f"{scene} {bed_pose}".strip()

    shape_bit = ""
    painterly_lock = ""
    if str(beat.get("anchor_beat") or "") in {"introduce", "callback"}:
        shape_bit = anchor_shape_clause(key_object, retry=int(focus_step or 0) > 0)
        if st == "object_focus":
            painterly_lock = (
                "Same flat gouache risograph atmosphere as a furnished room or "
                "rainy street — paper tooth, hard color blocks, printed dusk. "
                "Not a photograph, not volumetric glow, not isolated "
                "ink-diagram linework on blank paper."
            )

    expanded = _dev_expanded_scene_detail(beat, st, framing_kind, profile_name)
    anchor = resolve_visual_anchor(beat)
    beat["visual_anchor_hint"] = anchor
    anchor_line = f"The image must visually embody: {anchor}."

    parts = [
        open_b,
        scene,
        shape_bit,
        painterly_lock,
        _RISO_FLAT_PRINT if riso_lock else "",
        expanded,
        anchor_line,
        tech_b,
        palette_sentence,
        sat_g,
        text_g,
        garment_coverage_clause(st, str(beat.get("close_variant") or "")),
        mood_b,
        fmt_b,
    ]
    beat["visual_identity_profile"] = profile_name
    return " ".join(p for p in parts if p)


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
    print(
        "[LOFI identity v2-dev] assemble_v2_prompt_dev | "
        f"profile={profile_name} | vary_imagery={int(vary_imagery)} | "
        f"lock_visuals={int(lock_visuals)}"
    )
    enforce_concrete_beat_visuals(
        lines, pool=pool, vary_imagery=vary_imagery, lock_visuals=lock_visuals
    )
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
    if not has_close:
        assign_close_beat(lines, theme=theme, module=module)
    assign_episode_lighting(lines, module=module, theme=theme)
    for i, row in enumerate(lines):
        if not isinstance(row, dict):
            continue
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

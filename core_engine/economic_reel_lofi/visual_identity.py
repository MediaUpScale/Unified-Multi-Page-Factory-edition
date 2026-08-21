# -*- coding: utf-8 -*-
"""
Visual identity v2 — assemble Flux prompts from locked blocks + beat fields.

Live riso_prompt_library_v2.json is never overwritten. This bank is parallel
until a full render is approved.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from core_engine.economic_reel_lofi import config as lofi_cfg

SUBJECT_TYPES: frozenset[str] = frozenset(
    {"woman", "man", "couple", "silhouette", "object_focus"}
)
ARC_ACTS: tuple[str, ...] = ("act1", "act2", "act3")

DEFAULT_SETTING_OBJECT_PAIRS: tuple[dict[str, str], ...] = (
    {"setting": "bedroom, edge of a neatly made bed", "key_object": "untouched pillow"},
    {"setting": "kitchen table at night", "key_object": "cold cup of coffee"},
    {"setting": "hallway by a closed front door", "key_object": "unanswered phone"},
    {"setting": "empty park path at dusk", "key_object": "park bench"},
    {"setting": "desk under a small lamp", "key_object": "folded letter"},
    {"setting": "window seat facing the street", "key_object": "empty chair"},
    {"setting": "stoop at evening", "key_object": "same worn shoes"},
    {"setting": "bathroom mirror at night", "key_object": "phone face-down"},
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


def setting_object_pool(theme_row: dict[str, Any] | None) -> list[dict[str, str]]:
    raw = (theme_row or {}).get("setting_object_pairs") if theme_row else None
    out: list[dict[str, str]] = []
    if isinstance(raw, list):
        for row in raw:
            if not isinstance(row, dict):
                continue
            setting = str(row.get("setting") or "").strip()
            obj = str(row.get("key_object") or "").strip()
            if setting and obj:
                out.append({"setting": setting, "key_object": obj})
    if out:
        return out
    return [dict(p) for p in DEFAULT_SETTING_OBJECT_PAIRS]


def _fill_slots(template: str, **slots: str) -> str:
    text = template
    for key, val in slots.items():
        text = text.replace(f"[{key}]", val)
    return " ".join(text.split())


def _term_requested_in_object(keys: tuple[str, ...], key_object: str) -> bool:
    blob = (key_object or "").lower()
    return any(re.search(rf"\b{re.escape(k)}s?\b", blob) for k in keys)


def only_object_clause(key_object: str) -> str:
    """Positive-prompt 'only this object' sentence plus explicit default bans."""
    obj = _sanitize_visual_phrase(key_object) or "the requested object"
    banned = [
        label
        for label, keys in _DEFAULT_INTRUDER_TERMS
        if not _term_requested_in_object(keys, obj)
    ]
    lead = f"The only object is {obj}, filling the frame."
    if not banned:
        return lead
    no_list = ", ".join(f"no {label}" for label in banned)
    return f"{lead} Empty of other still-life props: {no_list}."


_OBJECT_FOCUS_FRAMING = ("macro_no_setting", "tighter_macro", "off_workspace")


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
) -> tuple[str, str]:
    """
    object_focus framing ladder. Step 0 drops named rooms so Schnell's
    desk-at-night still-life prior has nothing to latch onto. Later steps
    only run after an INTRUDER gate fail.
    """
    del setting  # never put the original room name in the prompt
    obj = _sanitize_visual_phrase(key_object) or "the requested object"
    extra = _OBJECT_FOCUS_HAND_OK if hand_interaction else _OBJECT_FOCUS_GUARD
    only = only_object_clause(obj)
    text_clause = _TEXT_SURFACE_CLAUSE if _object_stem(obj) in _TEXT_BEARING_STEMS else ""
    step = max(0, min(int(step), 2))
    kind = _OBJECT_FOCUS_FRAMING[step]
    if step <= 0:
        # Do not say "shallow depth of field" / camera bokeh — Schnell
        # treats that as photoreal lens blur (Priority 5b).
        scene = (
            f"Tight macro crop of {obj} filling the frame. "
            f"Printed flat color field behind the object, no camera bokeh, "
            f"no lens blur, no named room, no furniture, no other objects. "
            f"{extra} {only} {text_clause}"
        )
    elif step == 1:
        # "Flat featureless background" (prior wording) starved the frame of
        # the grainy halftone print texture the style block asks for
        # everywhere else, which is what collapsed uniq16 below the linework
        # floor. Keep every INTRUDER-safe exclusion; only add texture back.
        scene = (
            f"Extreme close-up of {obj} alone, the object occupies most of the frame. "
            f"Background keeps the same grainy halftone print texture as the object, "
            f"muted flat color with visible ink-dot grain, no gradient, no furniture, "
            f"no room, no other objects, no distinct background shapes. "
            f"{extra} {only} {text_clause}"
        )
    else:
        place = _off_workspace_place(obj)
        scene = (
            f"Close focus on {obj} {place}, the only object in frame. "
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
    "seat": ("chair", "chairs"),
    "seats": ("chair", "chairs"),
    "clap": ("hands", "chair"),
    "apology": ("letter", "envelope", "unsent letter", "doorway"),
    "room": ("doorway", "door", "lamp"),
    "name": ("place setting", "two plates"),
    "proof": ("untouched drink", "empty glass"),
    "verdict": ("place setting", "two plates"),
    "doorway": ("door", "doors"),
    "belonging": ("empty extra chair", "two coats"),
    "silence": ("lamp", "open window"),
    "hurt": ("door", "coat"),
    "love": ("two coats", "shared blanket", "two plates"),
    "steadiness": ("two coats", "shared blanket"),
    "feeling": ("lamp", "window"),
    "chaos": ("scattered papers", "overturned chair"),
    "noise": ("scattered papers", "kitchen radio"),
    "fight": ("open door", "unlatched door"),
    "war": ("slammed door",),
    "peace": ("open window", "folded blanket", "kettle"),
    "cost": ("empty glass", "worn shoes"),
    "winning": ("empty glass", "faded photograph"),
    "connection": ("two coats", "shared blanket"),
    "choice": ("open door", "packed bag"),
    "walking": ("open door", "shoes by the door"),
    "prize": ("empty glass", "faded photograph"),
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
            {"setting": "cluttered kitchen table", "key_object": "scattered papers"},
            {"setting": "desk after a long night", "key_object": "unopened mail pile"},
            {"setting": "dining room after an argument", "key_object": "overturned chair"},
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
            {"setting": "hallway near an open door", "key_object": "open door"},
            {"setting": "apartment threshold", "key_object": "coat in hand"},
            {"setting": "front steps at dusk", "key_object": "shoes by the door"},
        ),
        "expression": "turning away",
        "pose": "body turned mid-step toward the door, not seated",
    },
    {
        "id": "conflict",
        "weight": 2,
        "tokens": frozenset({
            "fight", "fighting", "war", "argument", "slammed", "slam",
            "yelling", "yell", "battle",
        }),
        "pairs": (
            {"setting": "hallway near a closed door", "key_object": "unlatched door"},
            {"setting": "bedroom doorway after a fight", "key_object": "slammed door"},
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
            {"setting": "kitchen counter at night", "key_object": "empty glass"},
            {"setting": "stoop at evening", "key_object": "same worn shoes"},
            {"setting": "desk under a small lamp", "key_object": "faded photograph"},
        ),
        "expression": "hollow, aware",
    },
    {
        "id": "proof",
        "weight": 2,
        "tokens": frozenset({"proof", "prove", "someone", "missed", "stayed"}),
        "pairs": (
            {"setting": "hallway by a closed front door", "key_object": "unanswered phone"},
            {"setting": "kitchen table at night", "key_object": "untouched drink"},
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
            {"setting": "sunlit kitchen window", "key_object": "open notebook"},
            {"setting": "desk under a small lamp", "key_object": "closed journal"},
        ),
    },
    {
        "id": "choice",
        "weight": 1,
        "tokens": frozenset({"choice", "choose", "choosing", "instead"}),
        "pairs": (
            {"setting": "hallway split", "key_object": "two doorways"},
            {"setting": "bedroom, edge of the bed", "key_object": "packed bag unopened"},
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
            {"setting": "coat hook by the door", "key_object": "two coats"},
            {"setting": "sofa in evening light", "key_object": "shared blanket"},
            {"setting": "kitchen table at morning", "key_object": "two plates"},
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
            {"setting": "sunlit kitchen", "key_object": "open window"},
            {"setting": "made bed in morning light", "key_object": "folded blanket"},
            {"setting": "kitchen at dawn", "key_object": "kettle just clicked off"},
        ),
        "expression": "steady, calm",
    },
)

# Rotation when a line has no cluster match — skip mug/empty-chair as first picks.
_VARIETY_PAIRS: tuple[dict[str, str], ...] = (
    {"setting": "cluttered kitchen table", "key_object": "scattered papers"},
    {"setting": "hallway near an open door", "key_object": "open door"},
    {"setting": "kitchen counter at night", "key_object": "empty glass"},
    {"setting": "stoop at evening", "key_object": "same worn shoes"},
    {"setting": "desk under a small lamp", "key_object": "folded letter"},
    {"setting": "bedroom, edge of a neatly made bed", "key_object": "untouched pillow"},
    {"setting": "coat hook by the door", "key_object": "two coats"},
    {"setting": "window seat facing the street", "key_object": "rain on the glass"},
    {"setting": "bathroom sink", "key_object": "cleared counter"},
    {"setting": "park bench at dusk", "key_object": "closed journal"},
    {"setting": "kitchen at dawn", "key_object": "kettle just clicked off"},
    {"setting": "sofa in evening light", "key_object": "shared blanket"},
)

_MAX_OBJECT_STEM_HITS = 2  # 3rd use of the same stem in one episode must rotate
_OBJECT_STEM_WORDS: tuple[str, ...] = (
    "paper", "mug", "cup", "chair", "door", "window", "letter", "envelope",
    "phone", "glass", "blanket", "notebook", "journal", "coat", "shoe",
    "pillow", "lamp", "kettle", "photo", "plate", "bag", "radio", "mail",
)
_EXPECTED_PALETTE_BY_ACT: dict[str, str] = {
    "act1": "WARM",
    "act2": "COLD",
    "act3": "CONTRAST",
}
_SETTING_PLACE_STEMS: tuple[str, ...] = (
    "hallway", "bedroom", "kitchen", "bathroom", "dining", "living",
    "desk", "porch", "stoop", "street", "window", "park", "sofa",
    "bed", "sink", "counter", "table", "coat",
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
# Desk/night still-life prior on Schnell. Woven into the positive prompt
# (not the negative prompt) and omitted when the beat's key_object IS one of these.
_DEFAULT_INTRUDER_TERMS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("mug", ("mug",)),
    ("cup", ("cup",)),
    ("coffee cup", ("coffee",)),
    ("glass", ("glass",)),
    ("laptop", ("laptop", "computer", "keyboard")),
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
    "Hands simple and secondary: resting in the lap or at the side, "
    "or mostly out of frame. Do not wrap fingers around a mug or glass "
    "close to the face. If an object is shown, keep it away from the face."
)
_SATURATION_GUARD = (
    "Keep colors saturated and printed, not monochrome, not grayscale."
)
# Schnell renders "papers"/"mail"/"signs" the same way it renders a mug on a
# desk: the noun's learned prior includes printed lettering. Negative prompts
# do not work at guidance_scale=0 (confirmed on the mug fix), so this is a
# positive-prompt guard, universal across subject_type (portraits can have a
# background sign/paper too, not just object_focus).
_TEXT_LEGIBILITY_GUARD = (
    "Any paper, page, envelope, sign, screen, or lettered surface in frame "
    "shows only abstract ink mark-making, blank space, or dense illegible "
    "scribble-texture — never a coherent word, never legible letters, no "
    "readable typography, postage, or signage anywhere in the image."
)
# Same stem family _object_stem() already collapses for repetition-cap
# purposes, extended to sibling text-bearing nouns from this file's own noun
# regex (letters, envelopes, lists, notebooks, calendars, bills).
_TEXT_BEARING_STEMS = frozenset(
    {"paper", "mail", "letter", "envelope", "notebook", "journal", "calendar", "list", "bills"}
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
    repetition. Log and persist only — never holds a render.
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
    object_over_cap = {k: v for k, v in obj_counts.items() if v > _MAX_OBJECT_STEM_HITS}

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
    return {
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
        "holds_episode": False,
    }


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
            if _stype(i) != "couple"
        ]
        # Prefer opening + later object beats (loss baseline used object_focus on beat 1).
        candidates.sort(key=lambda i: (0 if i == 0 else 1, i))
        for k, i in enumerate(candidates[:need]):
            nxt = "object_focus" if k % 2 == 0 else "silhouette"
            lines[i]["subject_type"] = nxt
            lines[i]["subject_remap"] = "framing_variety"
            print(
                f"[LOFI identity v2] framing-variety scene={i + 1} -> {nxt} "
                f"object={lines[i].get('key_object')!r}"
            )
            variety_idx.append(i)

    extra = len(variety_idx) - _MAX_VARIETY_SHOTS
    if extra > 0:
        for i in variety_idx[-extra:]:
            lines[i]["subject_type"] = "woman" if i % 2 == 0 else "man"
            lines[i]["subject_remap"] = "variety_cap_to_person"


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
    return "empty glass"


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
    elif "glass" in needle or "worn" in needle:
        setting = "kitchen counter at night"
    elif "chair" in needle or "seat" in needle:
        setting = "quiet indoor room with a chair"
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
) -> list[dict[str, Any]]:
    """
    Every beat must carry a setting + key_object that traces to that beat's
    caption (literal noun, mapped stand-in, or emotion-tied object).
    Keep writer object_focus / silhouette when valid; fill a 3–4 variety mix
    if the episode is all portraits. Cap couple at 3.
    The same object stem (mug, chair, door, …) may appear at most twice
    in one episode; a third hit rotates to a different stand-in.
    """
    del vary_imagery
    pairs = pool or [dict(p) for p in DEFAULT_SETTING_OBJECT_PAIRS]
    used: set[tuple[str, str]] = set()
    used_stems: dict[str, int] = {}
    n = len(lines)
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
        overused = used_stems.get(stem, 0) >= _MAX_OBJECT_STEM_HITS
        _apply_cluster_pose(row, text)
        if not tied or not concrete or overused:
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
        _prefer_character_subject(row, i)
        row["setting"] = _expand_thin_setting(
            _sanitize_visual_phrase(str(row.get("setting") or "")),
            pairs,
        )
        row["key_object"] = _sanitize_visual_phrase(str(row.get("key_object") or ""))
        used.add(_pair_key(str(row.get("setting") or ""), str(row.get("key_object") or "")))
        final_stem = _object_stem(str(row.get("key_object") or ""))
        used_stems[final_stem] = used_stems.get(final_stem, 0) + 1
    _apply_framing_variety(lines)
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
            hand_interaction=bool(beat.get("hand_interaction")),
        )
        if scene:
            scene = scene[0].upper() + scene[1:]
    elif st == "silhouette":
        chars = ident.get("characters") or {}
        tmpl = str(chars.get("silhouette") or "")
        char = _fill_slots(tmpl, expression=expr)
        if char:
            char = char[0].upper() + char[1:]
        pose = str(beat.get("pose_hint") or "").strip()
        pose_bit = f" {pose}." if pose else ""
        scene, framing_kind = silhouette_scene_text(
            char,
            key_object,
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
        scene = (
            f"{char}, close portrait in {setting}, {key_object} clearly visible "
            f"beside them but away from the face, {tod}.{pose_bit}{couple_bit} "
            f"{_CLOSE_PORTRAIT} {_HAND_POSE}"
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
) -> list[dict[str, Any]]:
    ident = bank or load_visual_identity_v2()
    pool = setting_object_pool(theme_row)
    n = len(lines)
    print(
        "[LOFI identity v2] assemble_v2_prompt | painterly=OFF | "
        f"vary_imagery={int(vary_imagery)} | bank=visual_identity_bank_v2.json"
    )
    enforce_concrete_beat_visuals(lines, pool=pool, vary_imagery=vary_imagery)
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

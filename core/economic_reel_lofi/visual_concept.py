# -*- coding: utf-8 -*-
"""Stage 2 — narrative → visual concept. Replaces _EPISODE_WORLDS as the source of setting/object.

One LLM decision per beat, carrying episode place/light from earlier beats
of the same run. Character appearance comes from RAG identity sheets;
RAG does not choose the concept.
"""
from __future__ import annotations

import json
import re
from typing import Any

from core.economic_reel_lofi.config import noun_number_variants
from core.economic_reel_lofi.licensed_objects import stamp_licensed_objects
from core.economic_reel_lofi.visual_identity import (
    apply_meaning_driven_variety,
    beat_shot_type,
    concept_is_locked,
    episode_spoken_motif,
    line_requires_spoken_motif,
    preferred_concrete_noun,
    spoken_line_licenses_window,
)

_JSON_FENCE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.I)
_MACRO_ROOM_RE = re.compile(
    r"\b("
    r"kitchens?|windows?|windowpanes?|cabinets?|cupboards?|sinks?|"
    r"counters?|countertops?|pendants?|ovens?|stoves?|fridges?|"
    r"refrigerators?|dishes|dishracks?|racks?|shelves|shelf|"
    r"doorways?|doors?|faucets?|linoleum|apartments?|rooms?|"
    r"houses?|tiles?|photographs?|photo frames?|coasters?|"
    r"handles?|draining|pendant lights?|hanging lights?|"
    r"tables?|tabletops?|desks?|furniture|benches?"
    r")\b",
    re.I,
)
_MACRO_FURNITURE_NOT = (
    "window",
    "cabinet",
    "sink",
    "counter",
    "pendant",
    "oven",
    "dishes",
    "doorway",
    "kitchen",
    "shelf",
    "faucet",
    "coaster",
    "table",
    "desk",
    "furniture",
)
_FIGURE_CLUTTER = (
    "window",
    "windows",
    "kettle",
    "kettles",
    "jar",
    "jars",
    "plant",
    "plants",
    "vase",
    "vases",
    "cabinet",
    "cabinets",
    "sink",
    "sinks",
    "counter",
    "counters",
    "dish",
    "dishes",
    "faucet",
    "faucets",
    "doorway",
    "doorways",
)
_FIGURE_CLUTTER_RE = re.compile(
    r"\b("
    r"windows?|kettles?|jars?|plants?|vases?|cabinets?|sinks?|"
    r"counters?|dishes|faucets?|doorways?"
    r")\b",
    re.I,
)
_NOW_SET_RE = re.compile(r"\s*—\s*now set at\b[^.]*\.?", re.I)
_OBJECT_FOCUS_LEAK_RE = re.compile(
    r"\b(man|woman|men|women|person|people|figure|couple)\b",
    re.I,
)
_DANGLING_TAIL_RE = re.compile(
    r"\b(with|and|but|or|the|a|an|to|of|from|as|at|by|for)\s*[.,]?\s*$",
    re.I,
)
_GLITCH_DOES_RE = re.compile(
    r"^(He|She|They|It)\s+does,?\s+but\b",
    re.I,
)
_INCOMPLETE_DOES_RE = re.compile(
    r"^(He|She|They|It)\s+does,?\s*$",
    re.I,
)
_NEGATION_SENTENCE_RE = re.compile(r"\bno other\b|\bnot a\b|\bnever\b|\bno [a-z]", re.I)
_LIGHT_PHRASE = {
    "indoor_lamp_glow": "one warm printed lamp-glow on the object",
    "overcast_daylight": "flat overcast print light on the object",
    "blue_hour_streetlight": "cool blue printed light on the object",
    "morning_cool": "cool morning print light on the object",
    "rainy_grey": "grey rainy print light on the object",
}
_VISUAL_STRIP_KEYS = (
    "setting",
    "key_object",
    "visual_prompt",
    "visual_anchor_hint",
    "atmosphere_place",
    "atmosphere_mood",
    "atmosphere_light",
    "atmosphere_background",
    "atmosphere_angle",
    "atmosphere_distance",
    "atmosphere_ambient_object",
    "episode_world_id",
    "force_episode_world_id",
    "episode_atmosphere",
    "riso_id",
    "riso_palette",
    "riso_mood",
    "riso_scene_type",
    "negative_prompt",
    "not_in_frame",
    "visual_concept",
    "scene_description",
    "framing",
    "licensed_objects",
    "lighting_condition",
    "palette_key",
    "palette_temp",
    "close_variant",
    "shot_type",
    "shot_scale",
    "subject_type",
    "subject_expression",
    "silhouette_framing",
    "meaning",
    "pose_hint",
    "close_character",
)


def strip_visual_fields(script: dict[str, Any]) -> dict[str, Any]:
    """Stage 1 output: spoken text + timing only."""
    out = dict(script)
    lines = []
    for row in out.get("lines") or []:
        if not isinstance(row, dict):
            continue
        clean = dict(row)
        for k in _VISUAL_STRIP_KEYS:
            clean.pop(k, None)
        lines.append(clean)
    out["lines"] = lines
    out.pop("episode_atmosphere", None)
    out.pop("episode_variety", None)
    return out


def stamp_stage1_timing(
    script: dict[str, Any],
    *,
    beat_s: float,
    duration_s: float,
) -> dict[str, Any]:
    lines = [r for r in (script.get("lines") or []) if isinstance(r, dict)]
    n = len(lines)
    for i, row in enumerate(lines):
        row["scene"] = int(row.get("scene") or i + 1)
        row["duration_s"] = float(beat_s)
        row.pop("visual_prompt", None)
    script["scene_count"] = n
    script["beat_duration_s"] = float(beat_s)
    script["duration_s"] = round(float(n) * float(beat_s), 3)
    script["duration_requested_s"] = float(duration_s)
    return script


def _parse_json_obj(raw: str) -> dict[str, Any]:
    text = (raw or "").strip()
    m = _JSON_FENCE.search(text)
    if m:
        text = m.group(1).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            data = json.loads(text[start : end + 1])
        else:
            raise
    return data if isinstance(data, dict) else {}


def _is_object_macro(concept: dict[str, Any]) -> bool:
    st = str(concept.get("subject_type") or "").strip().lower().replace(" ", "_")
    fr = str(concept.get("framing") or "").strip().lower().replace(" ", "_")
    return st == "object_focus" or fr == "macro_no_setting"


def _clean_object_name(obj: str) -> str:
    """Noun phrase only — no leading article, no trailing sentence period."""
    noun = str(obj or "").strip()
    noun = re.sub(r"^(a|an|the)\s+", "", noun, flags=re.I)
    if "." in noun:
        noun = noun.split(".")[0].strip()
    noun = noun.rstrip(".,;: ")
    return noun or "the object"


def _macro_isolation_paragraph(obj: str, light_key: str) -> str:
    from core.economic_reel_lofi.visual_identity import object_focus_texture_scene

    del light_key
    return object_focus_texture_scene(obj)


def _enforce_macro_scene(concept: dict[str, Any]) -> dict[str, Any]:
    """Object-focus / macro: object in a real gouache environment, not a blank plane."""
    if concept.get("abstract_license"):
        return concept
    if not _is_object_macro(concept):
        return concept
    if concept_is_locked(concept):
        return concept
    if concept.get("hook_template"):
        return concept
    from core.economic_reel_lofi.visual_identity import (
        handheld_object_scene,
        object_focus_texture_scene,
    )

    concept["subject_type"] = "object_focus"
    concept["framing"] = "macro_no_setting"
    obj = _clean_object_name(str(concept.get("key_object") or "") or "the object")
    concept["key_object"] = obj
    desc = str(
        concept.get("scene_description") or concept.get("visual_concept") or ""
    )
    blob = " ".join(
        str(concept.get(k) or "")
        for k in ("scene_description", "visual_concept")
    )
    figure_leak = bool(_OBJECT_FOCUS_LEAK_RE.search(blob)) and not concept.get(
        "hand_held_motif"
    )
    empty = not desc.strip() or "now set at" in blob.lower()
    place = str(concept.get("setting") or concept.get("episode_place") or "")
    if concept.get("hand_held_motif"):
        if empty or figure_leak:
            para = handheld_object_scene(concept)
            concept["scene_description"] = para
            concept["visual_concept"] = para
        return concept
    if empty or figure_leak or "painterly risograph still-life" not in blob.lower():
        para = object_focus_texture_scene(
            obj,
            variety=str(concept.get("object_shot_variant") or ""),
            setting=place,
        )
        concept["scene_description"] = para
        concept["visual_concept"] = para
    return concept


def _expand_not_in_frame(nots: list[str] | str | None) -> list[str]:
    if isinstance(nots, str):
        items = [p.strip() for p in nots.split(",") if p.strip()]
    else:
        items = [str(x).strip() for x in (nots or []) if str(x).strip()]
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        for variant in noun_number_variants(item):
            if variant not in seen:
                out.append(variant)
                seen.add(variant)
    return out


def _sanitize_scene_text(text: str) -> str:
    """Drop LLM fragments and negation sentences from positive scene prose."""
    blob = " ".join((text or "").split())
    if not blob:
        return ""
    blob = _NOW_SET_RE.sub("", blob).strip()
    parts = re.split(r"(?<=[.!?])\s+", blob)
    keep: list[str] = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if _GLITCH_DOES_RE.match(part):
            rest = re.sub(
                r"^(He|She|They|It)\s+does,?\s+but\s+",
                "",
                part,
                flags=re.I,
            ).strip()
            if not rest:
                continue
            if rest[0].islower():
                rest = rest[0].upper() + rest[1:]
            part = rest
        core = part.rstrip(".!? ")
        if _INCOMPLETE_DOES_RE.match(core):
            continue
        if _NEGATION_SENTENCE_RE.search(part):
            continue
        if _DANGLING_TAIL_RE.search(core):
            continue
        if not part.endswith((".", "!", "?")):
            part = part.rstrip(",;:") + "."
        keep.append(part)
    return " ".join(keep)


def _place_without_clutter(place: str) -> str:
    cleaned = _FIGURE_CLUTTER_RE.sub("", str(place or ""))
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    cleaned = re.sub(r"\s+,", ",", cleaned)
    cleaned = re.sub(
        r"\b(with|and|from|through|beside|under|against|across|"
        r"a|an|the|large|small|north-facing|nearby)\s*$",
        "",
        cleaned,
        flags=re.I,
    )
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    return cleaned.strip(" ,") or "a quiet indoor room"


def _line_licenses_clutter(text: str, licensed: list[str], noun: str) -> bool:
    blob = f"{text} {' '.join(licensed)}".lower()
    return any(
        re.search(rf"\b{re.escape(v)}\b", blob)
        for v in noun_number_variants(noun)
    )


def _figure_safe_paragraph(concept: dict[str, Any]) -> str:
    st = str(concept.get("subject_type") or "silhouette").strip().lower().replace(" ", "_")
    place = _place_without_clutter(
        str(concept.get("episode_place") or concept.get("setting") or "a quiet indoor room")
    )
    light = _LIGHT_PHRASE.get(
        str(concept.get("lighting_condition") or "").strip().lower(),
        "quiet printed light",
    )
    obj = _clean_object_name(str(concept.get("key_object") or ""))
    hold = ""
    if (
        obj
        and obj.lower() not in {"blank wall", "her absence", "the object"}
        and not _FIGURE_CLUTTER_RE.search(obj)
        and st in {"woman", "man"}
    ):
        hold = f" holding a {obj}"
    if st == "couple":
        return (
            f"Two clothed figures near the center of {place}{hold}. "
            f"{light[0].upper() + light[1:]}. Blank wall behind."
        )
    if st == "silhouette":
        return (
            f"One clothed silhouette near the center of {place}. "
            f"{light[0].upper() + light[1:]} on the figure. Blank wall behind."
        )
    who = "man" if st == "man" else "woman"
    pronoun = "His" if who == "man" else "Her"
    return (
        f"A {who} stands near the center of {place}{hold}. "
        f"{pronoun} gaze is lowered. {light[0].upper() + light[1:]}. "
        f"One clothed silhouette in the room. Blank wall behind."
    )


def _enforce_unlicensed_room_nouns(
    concept: dict[str, Any],
    spoken_line: str,
) -> dict[str, Any]:
    """Ban room-clutter nouns in POS when they are not the beat's subject."""
    licensed = [str(x).strip() for x in (concept.get("licensed_objects") or []) if str(x).strip()]
    nots = _expand_not_in_frame(concept.get("not_in_frame"))
    seen = {x.lower() for x in nots}
    window_ok = spoken_line_licenses_window(
        {"text": spoken_line, "window_primary": concept.get("window_primary")}
    ) or _line_licenses_clutter(spoken_line, licensed, "window")
    for noun in _FIGURE_CLUTTER:
        if noun not in seen and not _line_licenses_clutter(spoken_line, licensed, noun):
            if noun.startswith("window") and window_ok:
                continue
            nots.append(noun)
            seen.add(noun)
    concept["not_in_frame"] = nots
    if str(concept.get("abstract_license") or "").strip():
        from core.economic_reel_lofi.licensed_objects import enforce_licensed_inventory

        enforce_licensed_inventory(concept)
        return concept
    if _is_object_macro(concept):
        return concept
    st = str(concept.get("subject_type") or "").strip().lower().replace(" ", "_")
    if st not in {"woman", "man", "couple", "silhouette"}:
        return concept
    blob = " ".join(
        str(concept.get(k) or "")
        for k in ("scene_description", "visual_concept", "setting")
    )
    hits = {m.group(1).lower() for m in _FIGURE_CLUTTER_RE.finditer(blob)}
    if window_ok:
        hits -= {"window", "windows"}
    if hits:
        para = _figure_safe_paragraph(concept)
        concept["scene_description"] = para
        concept["visual_concept"] = para
        concept["setting"] = _place_without_clutter(str(concept.get("setting") or ""))
    return concept


def _fallback_concept(
    row: dict[str, Any],
    *,
    episode_state: dict[str, Any],
    index: int,
) -> dict[str, Any]:
    text = str(row.get("text") or row.get("beat_text") or "").strip()
    noun = preferred_concrete_noun(text) or str(episode_state.get("motif") or "").strip()
    place = str(episode_state.get("place") or "").strip() or "a quiet indoor room at night"
    if index == 0 and noun:
        # First beat may name the place implicitly; keep it generic, not a world table.
        place = place
    framing = "full_scene"
    subject = "silhouette"
    setting = place
    concept = (
        f"One clear subject: {noun or 'a quiet figure'} in {place}."
    )
    return {
        "visual_concept": concept,
        "scene_description": concept,
        "setting": setting,
        "key_object": noun or "blank wall",
        "subject_type": subject,
        "framing": framing,
        "time_of_day": str(episode_state.get("time") or "night"),
        "lighting_condition": str(episode_state.get("light") or "indoor_lamp_glow"),
        "episode_place": place,
        "not_in_frame": [],
        "licensed_objects": [noun] if noun else [],
        "source": "fallback",
    }


def _apply_concept(row: dict[str, Any], concept: dict[str, Any]) -> None:
    row["visual_concept"] = _sanitize_scene_text(
        str(concept.get("visual_concept") or "")
    )
    row["scene_description"] = _sanitize_scene_text(
        str(concept.get("scene_description") or concept.get("visual_concept") or "")
    )
    if not row["visual_concept"]:
        row["visual_concept"] = row["scene_description"]
    if not row["scene_description"]:
        row["scene_description"] = row["visual_concept"]
    row["setting"] = str(concept.get("setting") or "").strip()
    row["key_object"] = _clean_object_name(str(concept.get("key_object") or "").strip())
    st = str(concept.get("subject_type") or "silhouette").strip().lower().replace(" ", "_")
    row["subject_type"] = st
    framing = str(concept.get("framing") or "").strip()
    if not framing:
        framing = "macro_no_setting" if st == "object_focus" else "full_scene"
    row["framing"] = framing
    row["time_of_day"] = str(concept.get("time_of_day") or "night")
    row["lighting_condition"] = str(
        concept.get("lighting_condition") or "indoor_lamp_glow"
    )
    row["episode_place"] = str(concept.get("episode_place") or "").strip()
    nots = concept.get("not_in_frame") or []
    if isinstance(nots, str):
        nots = [p.strip() for p in nots.split(",") if p.strip()]
    row["not_in_frame"] = [str(x).strip() for x in nots if str(x).strip()]
    extras = concept.get("licensed_objects") or []
    if isinstance(extras, str):
        extras = [extras]
    stamp_licensed_objects(row, extras)
    spoken = str(row.get("text") or row.get("beat_text") or "")
    meaning = str(concept.get("meaning") or "").strip()
    if not meaning:
        meaning = str(row.get("visual_concept") or spoken[:80]).strip()
    row["meaning"] = meaning
    row["shot_type"] = beat_shot_type(row)
    dissolve = str(concept.get("dissolve_element") or "").strip()
    if dissolve:
        row["dissolve_element"] = dissolve
    orb = str(concept.get("orb") or concept.get("hook_orb") or "").strip()
    if orb:
        row["hook_orb"] = orb
    _enforce_macro_scene(row)


def translate_episode_visuals(
    script: dict[str, Any],
    *,
    module: str = "relationship",
) -> dict[str, Any]:
    """
    Fill visual concept fields on every beat. Returns episode_state.

    Does not call Flux or TTS. Does not read _EPISODE_WORLDS.
    """
    del module
    from agents.mcp.text_model import complete_script

    lines = [r for r in (script.get("lines") or []) if isinstance(r, dict)]
    if lines:
        lines[0]["episode_theme"] = str(script.get("theme") or "")
        lines[0]["episode_thesis"] = str(script.get("thesis") or "")
    episode_state: dict[str, Any] = {
        "place": "",
        "time": "night",
        "light": "indoor_lamp_glow",
        "mood": "",
        "motif": "",
        "figure": "",
    }
    concepts: list[dict[str, Any]] = []
    prev_shot = ""
    prev_env = ""
    motif = episode_spoken_motif(lines)
    for i, row in enumerate(lines):
        text = str(row.get("text") or row.get("beat_text") or "").strip()
        if concept_is_locked(row):
            prev_shot = str(row.get("shot_type") or beat_shot_type(row))
            prev_env = str(row.get("setting") or row.get("episode_place") or "")
            print(
                f"[LOFI stage2] scene={i + 1} locked — keeping approved concept "
                f"who={row.get('subject_type')} shot={prev_shot} "
                f"place={prev_env!r}"
            )
            continue
        if i == 0:
            prior = (
                "This is the HOOK beat. Do not write a kitchen or a full room. "
                "Return JSON with keys: meaning, dissolve_element, orb, "
                "time_of_day, lighting_condition. dissolve_element is the "
                "flock or cluster the silhouette edge dissolves into, chosen "
                "from THIS episode's meaning (birds, smoke, falling leaves, "
                "shattering glass, drifting embers, light particles, steam "
                "motes). orb is sun, moon, or a pale morning disc. "
                "Composition is a fixed template applied later."
            )
        else:
            prior = (
                f"Previous beat shot={prev_shot!r} environment={prev_env!r}. "
                "Choose a DIFFERENT shot distance "
                "(macro|medium|wide|silhouette|portrait) and a DIFFERENT "
                "environment unless THIS spoken line clearly returns to the "
                "same place. Same people/era is fine; same room is not the default."
            )
        motif_rule = ""
        if motif:
            motif_rule = (
                f"Episode object: {motif!r}. Metaphor is for WHO appears "
                "(man/woman/couple/silhouette), not for silently replacing that "
                "object. If this line names it, or is about putting it away, "
                "going by it, carrying or packing it, key_object and "
                "scene_description must show that object (or a clear visual of "
                "it). Do not substitute an unrelated object."
            )
            if line_requires_spoken_motif(text, motif):
                motif_rule += (
                    f" THIS line's spoken referent is {motif!r} — keep it in frame."
                )
        prompt = (
            "Return STRICT JSON only with keys: meaning (one short clause: "
            "what this image represents), visual_concept (plain-English "
            "one or two complete sentences), scene_description "
            "(one coherent complete-sentence paragraph), setting, key_object, "
            "subject_type (woman|man|couple|silhouette|object_focus), "
            "framing (full_scene|macro_no_setting|portrait_close), "
            "time_of_day, lighting_condition "
            "(indoor_lamp_glow|blue_hour_streetlight|overcast_daylight|"
            "morning_cool|rainy_grey|sunset_doorway), episode_place, "
            "licensed_objects (array of nouns this beat may show).\n"
            f"{prior}\n"
            f"Spoken beat {i + 1}/{len(lines)}: {text!r}\n"
            "SETTING FIRST: choose setting, then write scene_description "
            "entirely in that place. Never write a scene for one room and "
            "then mention a different place.\n"
            "OBJECT-FOCUS: if subject_type is object_focus, the paragraph is "
            "ONLY the object, a simple surface, and light (a hand at most). "
            "No standing person, no room tour.\n"
            f"{motif_rule}\n"
            "CAST FROM MEANING, not a locked narrator. There is no fixed "
            "POV gender. Pick whoever best represents THIS line — man, woman, "
            "couple, silhouette, or object alone.\n"
            "FIGURE DEFAULT: clothed silhouette against a bright doorway, "
            "window, or wide backlit landscape. Close facial portrait is rare "
            "(at most one beat in the episode, at the emotional peak). "
            "One couple-together silhouette is eligible only when THIS line's "
            "meaning is connection or shared memory.\n"
            "Do not invent hair, wardrobe, eyes, or face details. A locked "
            "appearance string is injected later for any close face.\n"
            "When a face and an object share the frame, the object is smaller "
            "than the face, held low at one side, clearly secondary.\n"
            "Do not compose any text-bearing object: no posters, signs, "
            "letters, newspapers, book pages, captions, or cursive. Wall "
            "frames are blank abstract color. Objects are unmarked.\n"
            "NARRATIVE FIRST: represent THIS line's meaning — literally or "
            "metaphorically. One clear subject per beat. Stay in flat "
            "illustration language: no photoreal camera wording. Complete "
            "affirmative sentences only — never write 'no', 'not', 'never', "
            "or fragments like 'He does, but…'. "
            "When THIS spoken line names no concrete object, pick ONE of "
            "three compositions and do not invent a named prop: "
            "(1) abstract/symbolic — light, shadow, negative space, or an "
            "empty gesture (open hand or empty chair with nothing attached); "
            "(2) landscape — a wide shot of the episode's established place "
            "with NO figure, using light/weather/time-of-day to carry the "
            "feeling; "
            "(3) character — the woman, the man, or both together, camera "
            "in on expression/posture/body language, no object in hand. "
            "Do not pick the same of these three as the previous beat. "
            "licensed_objects = the beat's subject and any pronoun referent."
        )
        concept: dict[str, Any] | None = None
        try:
            result = complete_script(
                prompt,
                system=(
                    "You translate spoken reel beats into visual concepts. "
                    "Output STRICT JSON only. Prefer metaphor when it fits. "
                    "No photoreal camera language."
                ),
            )
            concept = _parse_json_obj(result.text)
        except Exception as exc:  # noqa: BLE001
            print(f"[LOFI stage2] LLM failed scene={i + 1} ({exc}) — heuristic fallback")
            concept = None
        if i == 0 and concept and (
            str(concept.get("dissolve_element") or "").strip()
            or str(concept.get("meaning") or "").strip()
        ):
            concept["source"] = "llm"
        elif not concept or not str(concept.get("visual_concept") or "").strip():
            concept = _fallback_concept(row, episode_state=episode_state, index=i)
        else:
            concept["source"] = "llm"
        if not episode_state["place"]:
            episode_state["place"] = str(concept.get("episode_place") or concept.get("setting") or "")
            episode_state["time"] = str(concept.get("time_of_day") or "night")
            episode_state["light"] = str(
                concept.get("lighting_condition") or "indoor_lamp_glow"
            )
            episode_state["motif"] = str(
                (concept.get("licensed_objects") or [concept.get("key_object")])[0]
                if (concept.get("licensed_objects") or concept.get("key_object"))
                else ""
            )
        elif not str(concept.get("setting") or "").strip():
            concept["setting"] = str(concept.get("episode_place") or episode_state["place"])
        for key in ("scene_description", "visual_concept"):
            concept[key] = _sanitize_scene_text(str(concept.get(key) or ""))
        concept["key_object"] = _clean_object_name(str(concept.get("key_object") or ""))
        _apply_concept(row, concept)
        prev_shot = str(row.get("shot_type") or beat_shot_type(row))
        prev_env = str(row.get("setting") or row.get("episode_place") or "")
        concepts.append(
            {
                "scene": int(row.get("scene") or i + 1),
                "text": text,
                "meaning": row.get("meaning"),
                "visual_concept": row.get("visual_concept"),
                "scene_description": row.get("scene_description"),
                "setting": row.get("setting"),
                "key_object": row.get("key_object"),
                "subject_type": row.get("subject_type"),
                "framing": row.get("framing"),
                "shot_type": row.get("shot_type"),
                "licensed_objects": list(row.get("licensed_objects") or []),
                "not_in_frame": list(row.get("not_in_frame") or []),
                "episode_place": row.get("episode_place"),
                "source": concept.get("source"),
            }
        )
        print(
            f"[LOFI stage2] scene={i + 1} source={concept.get('source')} "
            f"who={row.get('subject_type')} shot={row.get('shot_type')} "
            f"place={row.get('setting')!r} object={row.get('key_object')!r}"
        )
        print(f"[LOFI stage2] meaning={row.get('meaning')!r}")
    apply_meaning_driven_variety(lines)
    concepts = []
    for i, row in enumerate(lines):
        concepts.append(
            {
                "scene": int(row.get("scene") or i + 1),
                "text": row.get("text") or row.get("beat_text"),
                "meaning": row.get("meaning"),
                "who": row.get("subject_type"),
                "shot_type": row.get("shot_type") or beat_shot_type(row),
                "setting": row.get("setting"),
                "palette_temp": row.get("palette_temp"),
                "palette_key": row.get("palette_key"),
                "lighting_condition": row.get("lighting_condition"),
                "subject_type": row.get("subject_type"),
                "framing": row.get("framing"),
                "key_object": row.get("key_object"),
                "visual_concept": row.get("visual_concept"),
            }
        )
        print(
            f"[LOFI stage2] gate-row #{i + 1} who={row.get('subject_type')} "
            f"shot={row.get('shot_type')} temp={row.get('palette_temp')} "
            f"env={row.get('setting')!r}"
        )
    if lines:
        lines[0]["episode_place"] = str(lines[0].get("setting") or episode_state["place"])
        lines[0]["episode_visual_state"] = dict(episode_state)
    script["visual_concepts"] = concepts
    script["episode_place"] = episode_state["place"]
    return episode_state

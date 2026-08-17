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
    r"\b(mug|cup|coat|door|bed|chair|phone|letter|table|sink|window|bench|"
    r"shoe|key|lamp|kitchen|bedroom|hallway|street|bag|photo|photograph|"
    r"sweater|fridge|umbrella|suitcase|ticket|clock|tea|journal|mirror|"
    r"stoop|porch|bus|pillow|hook|drawer|closet|faucet|counter|sofa|"
    r"blanket|envelope|list|radio|ashtray|kettle|notebook|calendar|"
    r"paper|plant|stone|box|drink|pass|seat|hanger|sleeve|hand)\b",
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


def _base_object_name(key_object: str) -> str:
    return re.sub(r"(?:,?\s*from a closer angle)+$", "", (key_object or "").strip())


def _changed_detail_object(key_object: str) -> str:
    base = _base_object_name(key_object)
    return f"{base} from a closer angle" if base else "a concrete household object"


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


def enforce_concrete_beat_visuals(
    lines: list[dict[str, Any]],
    *,
    pool: list[dict[str, str]] | None = None,
) -> list[dict[str, Any]]:
    """
    Every beat must carry a non-empty concrete setting + key_object before
    the image prompt is built. Abstract narration still gets a fallback visual
    (previous setting + changed object, else next unused pool pair).
    """
    pairs = pool or [dict(p) for p in DEFAULT_SETTING_OBJECT_PAIRS]
    used: set[tuple[str, str]] = set()
    prev_setting = ""
    prev_object = ""
    n = len(lines)
    for i, row in enumerate(lines):
        if not isinstance(row, dict):
            continue
        normalize_beat_visuals(row, index=i, scene_count=n, pool=pairs)
        abstract = text_lacks_concrete_anchor(str(row.get("text") or ""))
        concrete = setting_is_concrete(
            str(row.get("setting") or ""),
            str(row.get("key_object") or ""),
        )
        need = abstract or (not concrete)
        if need:
            pick = _unused_pool_pair(
                pairs,
                used,
                prefer_setting=prev_setting or None,
                avoid_object=prev_object or None,
                index=i,
            )
            if prev_setting and pick["setting"].strip().lower() != prev_setting.strip().lower():
                if "from a closer angle" in prev_object.lower():
                    row["setting"] = pick["setting"]
                    row["key_object"] = pick["key_object"]
                else:
                    row["setting"] = prev_setting
                    row["key_object"] = _changed_detail_object(prev_object)
            else:
                row["setting"] = pick["setting"]
                row["key_object"] = pick["key_object"]
            row["visual_fallback"] = (
                "abstract_beat_prev_setting"
                if prev_setting
                else "abstract_beat_pool"
            )
            if abstract and str(row.get("subject_type") or "") == "silhouette":
                row["subject_type"] = "object_focus"
                row["subject_remap"] = "silhouette_blocked_on_abstract_beat"
            print(
                f"[LOFI identity v2] concrete fallback scene={i + 1} "
                f"reason={'abstract_text' if abstract else 'empty_fields'} "
                f"setting={row['setting']!r} object={row['key_object']!r} "
                f"type={row.get('subject_type')}"
            )
        row["setting"] = _expand_thin_setting(str(row.get("setting") or ""), pairs)
        used.add(_pair_key(str(row.get("setting") or ""), str(row.get("key_object") or "")))
        prev_setting = str(row.get("setting") or "")
        prev_object = str(row.get("key_object") or "")
    return lines


def assemble_v2_prompt(
    beat: dict[str, Any],
    *,
    bank: dict[str, Any] | None = None,
) -> str:
    ident = bank or load_visual_identity_v2()
    blocks = ident.get("blocks") or {}
    open_b = str(blocks.get("open") or "").strip()
    tech_b = str(blocks.get("technique") or "").strip()
    mood_b = str(blocks.get("mood") or "").strip()
    fmt_b = str(blocks.get("format") or "").strip()
    st = str(beat.get("subject_type") or "woman")
    expr = str(beat.get("subject_expression") or "sad")
    setting = str(beat.get("setting") or "")
    key_object = str(beat.get("key_object") or "")
    tod = str(beat.get("time_of_day") or "dusk")
    act = str(beat.get("arc_position") or "act1")
    palette_sentence, palette_key = _palette_sentence(ident, act)
    beat["palette_key"] = palette_key

    if st == "object_focus":
        tmpl = str(ident.get("object_focus") or "")
        scene = _fill_slots(
            tmpl,
            key_object=key_object,
            setting=setting,
            time_of_day=tod,
        )
        if not scene:
            scene = (
                f"Close focus on {key_object} in {setting} at {tod}, "
                "no full-body portrait."
            )
        if scene:
            scene = scene[0].upper() + scene[1:]
    else:
        chars = ident.get("characters") or {}
        tmpl = str(chars.get(st) or chars.get("woman") or "")
        char = _fill_slots(tmpl, expression=expr)
        if char:
            char = char[0].upper() + char[1:]
        scene = (
            f"{char}, standing in {setting}, {key_object} clearly visible "
            f"in the foreground, {tod}."
        ).strip()

    parts = [open_b, scene, tech_b, palette_sentence, mood_b, fmt_b]
    return " ".join(p for p in parts if p)


def apply_v2_prompts_to_lines(
    lines: list[dict[str, Any]],
    *,
    theme_row: dict[str, Any] | None = None,
    bank: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    ident = bank or load_visual_identity_v2()
    pool = setting_object_pool(theme_row)
    n = len(lines)
    enforce_concrete_beat_visuals(lines, pool=pool)
    for i, row in enumerate(lines):
        if not isinstance(row, dict):
            continue
        normalize_beat_visuals(row, index=i, scene_count=n, pool=pool)
        prompt = assemble_v2_prompt(row, bank=ident)
        row["visual_prompt"] = prompt
        row["visual_identity"] = "v2"
        row["riso_id"] = f"identity_v2_{row.get('subject_type')}_{row.get('arc_position')}"
        row["riso_palette"] = row.get("palette_key")
    return lines

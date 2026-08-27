# -*- coding: utf-8 -*-
"""
Riso prompt library — verbatim image prompts for ECONOMIC_REEL_LOFI.

Concrete entries are used as-is (no LLM rewrite). The template entry is filled
deterministically for action/interaction variants only.

Selection matches script emotion_tags + arc_position, never repeats prompt id
inside one video, and avoids consecutive identical primary emotion tags.
"""
from __future__ import annotations

import json
import random
import re
from pathlib import Path
from typing import Any

from core.economic_reel_lofi import config as lofi_cfg

_PACKAGE_DIR = Path(__file__).resolve().parent
_DEFAULT_LIBRARY = _PACKAGE_DIR / "store" / "riso_prompt_library_v2.json"
_PREV_LIBRARY = _PACKAGE_DIR / "store" / "riso_prompt_library.json"

# Deterministic fill banks for riso_003_template only (never output brackets).
_ACTION_FILLS: tuple[str, ...] = (
    "two people sitting apart at a kitchen table, one reaching halfway across then stopping",
    "a couple walking side by side on a wet sidewalk, umbrellas brushing once then parting",
    "one person handing a folded letter across a cafe table while the other looks away",
    "someone closing a front door gently while another waits at the end of the hallway",
    "two figures sharing a park bench with space between them, one turning slightly toward the other",
    "a person packing a suitcase on a bed while another stands in the doorway watching quietly",
    "two hands almost touching over a rain-streaked cafe window, then pulling back",
    "a figure stepping onto a night train while someone remains on the platform",
)

_LIGHT_FILLS: tuple[str, ...] = (
    "warm kitchen lamp light",
    "streetlamp glow on wet pavement",
    "soft window light from one side",
    "dim overhead platform lamp",
    "low desk lamp casting long shadows",
    "neon-reflected streetlight amber",
)

_MOOD_FILLS: tuple[str, ...] = (
    "melancholic quiet",
    "bittersweet",
    "quiet sorrowful",
    "hopeful bittersweet",
    "lonely melancholic",
)

_ARC_BY_INDEX_9: tuple[str, ...] = (
    "opening",  # 1
    "build",
    "build",
    "build",
    "turn",
    "turn",
    "resolution",
    "resolution",
    "resolution",
)


def _library_path(path: Path | None = None) -> Path:
    if path:
        return Path(path)
    rel = getattr(lofi_cfg, "RISO_PROMPT_LIBRARY_REL", "") or ""
    if rel:
        engine_root = Path(__file__).resolve().parents[2]
        p = engine_root / rel
        if p.is_file():
            return p
    return _DEFAULT_LIBRARY


def load_riso_library(path: Path | None = None) -> dict[str, Any]:
    p = _library_path(path)
    data = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("prompts"), list):
        raise ValueError(f"invalid riso library: {p}")
    return data


def export_active_library_diff(engine_root: Path | None = None) -> dict[str, Any]:
    """Print the live library JSON path and a diff vs the previous library file."""
    live_path = _library_path()
    live = load_riso_library(live_path)
    live_ids = {
        str(r.get("id"))
        for r in (live.get("prompts") or [])
        if isinstance(r, dict) and r.get("id")
    }
    prev_ids: set[str] = set()
    if _PREV_LIBRARY.is_file() and _PREV_LIBRARY.resolve() != live_path.resolve():
        try:
            prev = json.loads(_PREV_LIBRARY.read_text(encoding="utf-8"))
            prev_ids = {
                str(r.get("id"))
                for r in (prev.get("prompts") or [])
                if isinstance(r, dict) and r.get("id")
            }
        except Exception:  # noqa: BLE001
            prev_ids = set()
    added = sorted(live_ids - prev_ids)
    removed = sorted(prev_ids - live_ids)
    concrete = [
        str(r.get("id"))
        for r in (live.get("prompts") or [])
        if isinstance(r, dict)
        and r.get("id")
        and not str(r.get("id")).endswith("_template")
        and "TEMPLATE" not in str(r.get("scene_type") or "").upper()
    ]
    print(f"[LOFI riso] LIVE library: {live_path}")
    print(f"[LOFI riso] entries={len(live.get('prompts') or [])} concrete={len(concrete)}")
    print(f"[LOFI riso] DIFF added={added or ['(none)']}")
    print(f"[LOFI riso] DIFF removed={removed or ['(none)']}")
    print(
        f"[LOFI riso] riso_013/014 distinct="
        f"{('riso_013' in live_ids) and ('riso_014' in live_ids)}"
    )
    return {
        "live_path": str(live_path),
        "added": added,
        "removed": removed,
        "ids": sorted(live_ids),
        "concrete_ids": concrete,
    }


def concrete_riso_entries(library: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    lib = library or load_riso_library()
    out: list[dict[str, Any]] = []
    for row in lib.get("prompts") or []:
        if not isinstance(row, dict):
            continue
        rid = str(row.get("id") or "")
        if rid.endswith("_template") or "TEMPLATE" in str(row.get("scene_type") or "").upper():
            continue
        prompt = str(row.get("prompt") or "").strip()
        if prompt and "[" not in prompt:
            out.append(dict(row))
    return out


def get_template_entry(library: dict[str, Any] | None = None) -> dict[str, Any] | None:
    lib = library or load_riso_library()
    for row in lib.get("prompts") or []:
        if not isinstance(row, dict):
            continue
        rid = str(row.get("id") or "")
        if rid.endswith("_template") or "TEMPLATE" in str(row.get("scene_type") or "").upper():
            return dict(row)
    return None


def fill_riso_template(
    template: dict[str, Any],
    *,
    seed: int | None = None,
    theme: str = "",
) -> dict[str, Any]:
    """Fill bracket placeholders; never leave literal [...] in the prompt."""
    rng = random.Random(seed if seed is not None else random.randint(0, 2**31 - 1))
    action = rng.choice(_ACTION_FILLS)
    light = rng.choice(_LIGHT_FILLS)
    mood = rng.choice(_MOOD_FILLS)
    if theme:
        mood = f"{mood}, theme of {theme.replace('_', ' ')}"

    raw = str(template.get("prompt") or "")
    filled = raw
    filled = re.sub(
        r"\[SCENE WITH ACTION/INTERACTION[^\]]*\]",
        action,
        filled,
        flags=re.IGNORECASE,
    )
    filled = re.sub(
        r"\[SECONDARY/AMBIENT LIGHT SOURCE[^\]]*\]",
        light,
        filled,
        flags=re.IGNORECASE,
    )
    filled = re.sub(r"\[mood\]", mood, filled, flags=re.IGNORECASE)
    filled = re.sub(r"\[[^\]]+\]", "", filled)
    filled = re.sub(r"\s{2,}", " ", filled).strip(" ,")

    out = dict(template)
    out["prompt"] = filled
    out["mood"] = mood
    out["palette"] = "rich saturated nostalgic (template fill)"
    out["scene_type"] = "action_interaction_filled"
    out["id"] = f"{template.get('id', 'riso_003')}_filled_{seed or rng.randint(1, 9999)}"
    out["filled_from_template"] = True
    out.setdefault("emotion_tags", ["melancholic_quiet_tension", "doubt", "longing"])
    out.setdefault("arc_position", ["build", "turn"])
    return out


def _tags(row: dict[str, Any]) -> list[str]:
    raw = row.get("emotion_tags") or []
    if isinstance(raw, str):
        raw = [raw]
    tags = [str(t).strip().lower() for t in raw if str(t).strip()]
    mood = str(row.get("mood") or "").strip().lower()
    if mood and mood not in tags and "[" not in mood:
        tags.append(mood)
    return tags


def _primary_tag(row: dict[str, Any]) -> str:
    tags = _tags(row)
    return tags[0] if tags else ""


def _arcs(row: dict[str, Any]) -> set[str]:
    raw = row.get("arc_position") or []
    if isinstance(raw, str):
        raw = [raw]
    return {str(a).strip().lower() for a in raw if str(a).strip()}


def _score_candidate(
    row: dict[str, Any],
    *,
    want_emotion: str,
    want_arc: str,
    recent: list[dict[str, Any]],
) -> int:
    score = 0
    tags = set(_tags(row))
    want_e = (want_emotion or "").strip().lower()
    if want_e and want_e in tags:
        score += 6
    elif want_e:
        # fuzzy: shared token
        for t in tags:
            if want_e in t or t in want_e:
                score += 3
                break
    want_a = (want_arc or "").strip().lower()
    if want_a and want_a in _arcs(row):
        score += 5
    # Consecutive emotion variety
    if recent:
        prev = _primary_tag(recent[-1])
        if prev and prev == _primary_tag(row):
            score -= 8
    return score


def assign_riso_prompts_for_scenes(
    scene_count: int,
    *,
    theme: str = "",
    seed: int | None = None,
    library_path: Path | None = None,
    scene_emotions: list[str] | None = None,
    scene_arcs: list[str] | None = None,
) -> list[dict[str, Any]]:
    """
    One unique library row per scene (verbatim prompt).

    - Never reuse the same prompt ``id`` inside one video.
    - Prefer emotion_tag + arc_position match from the script.
    - Avoid the same primary emotion tag on two consecutive scenes.
    """
    lib = load_riso_library(library_path)
    concrete = concrete_riso_entries(lib)
    rng = random.Random(seed if seed is not None else random.randint(0, 2**31 - 1))
    unused = list(concrete)
    rng.shuffle(unused)

    assigned: list[dict[str, Any]] = []
    used_ids: set[str] = set()

    for i in range(scene_count):
        want_e = ""
        if scene_emotions and i < len(scene_emotions):
            want_e = str(scene_emotions[i] or "")
        want_a = ""
        if scene_arcs and i < len(scene_arcs) and scene_arcs[i]:
            want_a = str(scene_arcs[i])
        elif scene_count == 9 and i < len(_ARC_BY_INDEX_9):
            want_a = _ARC_BY_INDEX_9[i]
        elif i == 0:
            want_a = "opening"
        elif i >= scene_count - 2:
            want_a = "resolution"
        elif i >= max(1, scene_count // 2):
            want_a = "turn"
        else:
            want_a = "build"

        pool = [r for r in unused if str(r.get("id")) not in used_ids]
        pick: dict[str, Any] | None = None
        if pool:
            ranked = sorted(
                pool,
                key=lambda r: (
                    _score_candidate(
                        r, want_emotion=want_e, want_arc=want_a, recent=assigned,
                    ),
                    rng.random(),
                ),
                reverse=True,
            )
            pick = dict(ranked[0])
        else:
            tmpl = get_template_entry(lib)
            if tmpl is None:
                raise RuntimeError("riso library exhausted and no template available")
            pick = fill_riso_template(tmpl, seed=(seed or 0) + i * 97, theme=theme)

        rid = str(pick.get("id") or "")
        used_ids.add(rid)
        unused = [r for r in unused if str(r.get("id")) != rid]
        assigned.append(pick)

    ids = [str(r.get("id")) for r in assigned]
    if len(ids) != len(set(ids)):
        raise RuntimeError(f"riso prompt id repeated within video: {ids}")
    # 013 and 014 must never be fused
    if "riso_013" in ids and "riso_014" in ids:
        i13, i14 = ids.index("riso_013"), ids.index("riso_014")
        if i13 == i14:
            raise RuntimeError("riso_013 and riso_014 collapsed into one scene")
    return assigned

# -*- coding: utf-8 -*-
"""Setting-archetype bank, intra-script cap, and remapping.

Independent of anchor_object dedup. A kitchen kettle and a kitchen plate
share interior-domestic even when the objects differ.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_PACKAGE_DIR = Path(__file__).resolve().parent
_BANK_PATH = _PACKAGE_DIR / "store" / "setting_archetype_bank.json"

MAX_ARCHETYPE_PER_EPISODE = 2
MIN_NEW_VS_PREVIOUS = 2
SETTING_HISTORY_N = 10

ARCHETYPE_ORDER: tuple[str, ...] = (
    "transit",
    "public-interior",
    "threshold",
    "exterior-landscape",
    "exterior-urban",
    "interior-domestic",
)

_KEYWORD_MAP: dict[str, tuple[str, ...]] = {
    "transit": (
        "train", "platform", "station", "airport", "boarding", "bus",
        "highway", "dashboard", "passenger", "car", "ticket", "road",
    ),
    "public-interior": (
        "cafe", "café", "theater", "theatre", "lobby", "waiting room",
        "restaurant", "library", "museum", "booth",
    ),
    "threshold": (
        "doorway", "door", "window", "threshold", "jamb", "landing",
        "balcony", "stoop", "porch", "coat hook", "hallway",
    ),
    "exterior-landscape": (
        "coastline", "coast", "tide", "forest", "meadow", "beach",
        "horizon", "river", "field", "hill", "park", "shore",
    ),
    "exterior-urban": (
        "streetlamp", "streetlight", "sidewalk", "crosswalk", "street",
        "crowd", "alley", "curb", "city", "corner",
    ),
    "interior-domestic": (
        "kitchen", "bedroom", "bathroom", "dining", "living", "fridge",
        "sofa", "blanket", "counter", "sink", "bed",
    ),
}

_bank_cache: dict[str, Any] | None = None


def load_setting_bank() -> dict[str, Any]:
    global _bank_cache
    if _bank_cache is not None:
        return _bank_cache
    try:
        raw = json.loads(_BANK_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raw = {}
    _bank_cache = raw if isinstance(raw, dict) else {}
    return _bank_cache


def classify_setting_archetype(setting: str, key_object: str = "") -> str:
    """Map a setting (+ optional object) onto a bank archetype."""
    blob = f"{setting} {key_object}".strip().lower()
    if not blob:
        return "unknown"
    explicit = str(key_object or "")
    # Prefer an explicit tag if a caller stuffed it on the pair.
    for arch in ARCHETYPE_ORDER:
        if blob == arch or blob.startswith(f"{arch} "):
            return arch
    for arch in ARCHETYPE_ORDER:
        for kw in _KEYWORD_MAP.get(arch, ()):
            if kw and kw in blob:
                return arch
    del explicit
    return "unknown"


def pairs_for_archetype(archetype: str) -> list[dict[str, str]]:
    bank = load_setting_bank()
    rows = (bank.get("pairs_by_archetype") or {}).get(archetype) or []
    out: list[dict[str, str]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        setting = str(row.get("setting") or "").strip()
        obj = str(row.get("key_object") or "").strip()
        if setting and obj:
            out.append(
                {
                    "setting": setting,
                    "key_object": obj,
                    "archetype": archetype,
                }
            )
    return out


def theme_bank_pairs(theme: str) -> list[dict[str, str]]:
    bank = load_setting_bank()
    themes = bank.get("themes") if isinstance(bank.get("themes"), dict) else {}
    row = themes.get(str(theme or "").strip().lower()) if themes else None
    raw = (row or {}).get("pairs") if isinstance(row, dict) else None
    out: list[dict[str, str]] = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        setting = str(item.get("setting") or "").strip()
        obj = str(item.get("key_object") or "").strip()
        if not setting or not obj:
            continue
        arch = str(item.get("archetype") or "").strip() or classify_setting_archetype(
            setting, obj
        )
        out.append({"setting": setting, "key_object": obj, "archetype": arch})
    return out


def stamp_line_archetypes(lines: list[dict[str, Any]]) -> list[str]:
    """Write setting_archetype onto every beat. Returns the per-beat list."""
    beats: list[str] = []
    for row in lines:
        if not isinstance(row, dict):
            continue
        arch = str(row.get("setting_archetype") or "").strip()
        if not arch or arch == "unknown":
            arch = classify_setting_archetype(
                str(row.get("setting") or ""),
                str(row.get("key_object") or ""),
            )
        row["setting_archetype"] = arch
        beats.append(arch)
    return beats


def score_setting_archetypes(lines: list[dict[str, Any]]) -> dict[str, Any]:
    beats = stamp_line_archetypes(lines)
    counts: dict[str, int] = {}
    for arch in beats:
        if not arch:
            continue
        counts[arch] = counts.get(arch, 0) + 1
    overflow = {k: v for k, v in counts.items() if v > MAX_ARCHETYPE_PER_EPISODE}
    unique = [k for k in ARCHETYPE_ORDER if k in counts] + [
        k for k in counts if k not in ARCHETYPE_ORDER
    ]
    holds = bool(overflow)
    summary = (
        f"archetypes={counts} overflow={overflow or 'none'} "
        f"unique={unique}"
    )
    return {
        "beats": beats,
        "counts": counts,
        "unique": unique,
        "overflow": overflow,
        "holds_episode": holds,
        "summary": summary,
        "max_per_episode": MAX_ARCHETYPE_PER_EPISODE,
    }


def _pair_pool(
    pool: list[dict[str, str]] | None,
    theme: str = "",
) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def _add(row: dict[str, str]) -> None:
        setting = str(row.get("setting") or "").strip()
        obj = str(row.get("key_object") or "").strip()
        if not setting or not obj:
            return
        key = (setting.lower(), obj.lower())
        if key in seen:
            return
        seen.add(key)
        arch = str(row.get("archetype") or "").strip() or classify_setting_archetype(
            setting, obj
        )
        out.append({"setting": setting, "key_object": obj, "archetype": arch})

    for row in theme_bank_pairs(theme):
        _add(row)
    for arch in ARCHETYPE_ORDER:
        for row in pairs_for_archetype(arch):
            _add(row)
    for row in pool or []:
        if isinstance(row, dict):
            _add(row)
    return out


def enforce_setting_archetype_variety(
    lines: list[dict[str, Any]],
    pool: list[dict[str, str]] | None = None,
    *,
    theme: str = "",
    prefer_new: list[str] | None = None,
    lock_visuals: bool = False,
) -> dict[str, Any]:
    """
    Cap any archetype at 2/9. Remap overflow beats onto unused archetypes.

    Prefer archetypes the previous script did not use (prefer_new).
    Does not rewrite spoken text. Logs every remapped beat.
    """
    if lock_visuals:
        report = score_setting_archetypes(lines)
        report["remaps"] = []
        report["pulled_to_avoid_repeat"] = []
        return report

    theme = theme or (
        str(lines[0].get("episode_theme") or "")
        if lines and isinstance(lines[0], dict)
        else ""
    )
    candidates = _pair_pool(pool, theme)
    used_objects = {
        str(r.get("key_object") or "").strip().lower()
        for r in lines
        if isinstance(r, dict)
    }
    prefer = [a for a in (prefer_new or []) if a in ARCHETYPE_ORDER]
    remaps: list[dict[str, str]] = []
    pulled: list[str] = []

    def _counts() -> dict[str, int]:
        stamp_line_archetypes(lines)
        out: dict[str, int] = {}
        for row in lines:
            if not isinstance(row, dict):
                continue
            arch = str(row.get("setting_archetype") or "unknown")
            out[arch] = out.get(arch, 0) + 1
        return out

    def _pick(arch: str) -> dict[str, str] | None:
        for row in candidates:
            if row.get("archetype") != arch:
                continue
            obj = row["key_object"].lower()
            if obj in used_objects:
                continue
            return row
        for row in candidates:
            if row.get("archetype") == arch:
                return row
        return None

    counts = _counts()
    # Remap later overflow beats first; keep scene 1 if already legal.
    overflow_idxs: list[int] = []
    running: dict[str, int] = {}
    for i, row in enumerate(lines):
        if not isinstance(row, dict):
            continue
        arch = str(row.get("setting_archetype") or classify_setting_archetype(
            str(row.get("setting") or ""),
            str(row.get("key_object") or ""),
        ))
        running[arch] = running.get(arch, 0) + 1
        if running[arch] > MAX_ARCHETYPE_PER_EPISODE:
            overflow_idxs.append(i)

    for i in overflow_idxs:
        row = lines[i]
        if not isinstance(row, dict):
            continue
        old_arch = str(row.get("setting_archetype") or "")
        counts = _counts()
        unused = [
            a
            for a in ARCHETYPE_ORDER
            if counts.get(a, 0) < MAX_ARCHETYPE_PER_EPISODE
        ]
        # Prefer archetypes the previous episode never used.
        ranked = [a for a in prefer if a in unused] + [
            a for a in unused if a not in prefer
        ]
        picked = None
        chosen_arch = ""
        for arch in ranked:
            cand = _pick(arch)
            if cand:
                picked = cand
                chosen_arch = arch
                break
        if not picked or not chosen_arch:
            continue
        old_setting = str(row.get("setting") or "")
        row["setting"] = picked["setting"]
        row["key_object"] = picked["key_object"]
        row["setting_archetype"] = chosen_arch
        row["setting_archetype_remap"] = f"{old_arch}->{chosen_arch}"
        used_objects.add(picked["key_object"].lower())
        if chosen_arch in prefer and chosen_arch not in pulled:
            pulled.append(chosen_arch)
        remaps.append(
            {
                "scene": str(row.get("scene") or i + 1),
                "from": old_arch,
                "to": chosen_arch,
                "setting": picked["setting"],
            }
        )
        print(
            f"[LOFI setting] remapped scene={row.get('scene') or i + 1} "
            f"{old_arch!r} → {chosen_arch!r} setting={picked['setting']!r} "
            f"(was {old_setting!r})"
        )

    report = score_setting_archetypes(lines)
    report["remaps"] = remaps
    report["pulled_to_avoid_repeat"] = pulled
    if pulled:
        print(
            f"[LOFI setting] pulled_to_avoid_repeat={pulled} "
            f"used={report.get('unique')}"
        )
    else:
        print(f"[LOFI setting] {report.get('summary')}")
    return report


COMPOSITION_TYPES: tuple[str, ...] = (
    "figure_centered",
    "object_focus",
    "wide_environment",
)
MAX_FIGURE_CENTERED = 4
MIN_NON_FIGURE = 5
_WIDE_PLACE_RE = (
    "park", "coast", "coastline", "forest", "field", "hill", "horizon",
    "platform", "street", "path", "shore", "meadow", "beach", "landscape",
)


def classify_composition_type(row: dict[str, Any]) -> str:
    """Independent of setting archetype: how the frame is built."""
    tagged = str(row.get("composition_type") or "").strip().lower()
    if tagged in COMPOSITION_TYPES:
        return tagged
    st = str(row.get("subject_type") or "").strip().lower().replace(" ", "_")
    framing = str(row.get("silhouette_framing") or "").strip().lower()
    close_v = str(row.get("close_variant") or "").strip().lower()
    setting = f"{row.get('setting') or ''} {row.get('key_object') or ''}".lower()
    if st == "object_focus" or close_v == "object":
        return "object_focus"
    if framing == "wide_couple" or framing == "wide_place":
        return "wide_environment"
    if st == "couple" and any(w in setting for w in _WIDE_PLACE_RE):
        # Mid-scale couple in a doorway is still figure_centered; only
        # marked-wide or small-in-landscape couple counts as wide.
        if str(row.get("shot_scale") or "").strip().lower() == "wide":
            return "wide_environment"
    if st in {"woman", "man", "couple", "silhouette"}:
        return "figure_centered"
    return "object_focus"


def stamp_composition_types(lines: list[dict[str, Any]]) -> list[str]:
    beats: list[str] = []
    for row in lines:
        if not isinstance(row, dict):
            continue
        kind = classify_composition_type(row)
        row["composition_type"] = kind
        beats.append(kind)
    return beats


def score_composition_types(lines: list[dict[str, Any]]) -> dict[str, Any]:
    beats = stamp_composition_types(lines)
    counts: dict[str, int] = {}
    for kind in beats:
        counts[kind] = counts.get(kind, 0) + 1
    figure_n = counts.get("figure_centered", 0)
    non_figure = counts.get("object_focus", 0) + counts.get("wide_environment", 0)
    holds = figure_n > MAX_FIGURE_CENTERED or (
        len(beats) >= 8 and non_figure < MIN_NON_FIGURE
    )
    return {
        "beats": beats,
        "counts": counts,
        "figure_centered": figure_n,
        "non_figure": non_figure,
        "holds_episode": holds,
        "max_figure_centered": MAX_FIGURE_CENTERED,
        "min_non_figure": MIN_NON_FIGURE,
        "summary": (
            f"figure_centered={figure_n}/{MAX_FIGURE_CENTERED} "
            f"non_figure={non_figure} {counts}"
        ),
    }


def enforce_composition_variety(
    lines: list[dict[str, Any]],
    *,
    lock_visuals: bool = False,
) -> dict[str, Any]:
    """Cap figure_centered at 4. Overflow remaps to object_focus or wide."""
    report = score_composition_types(lines)
    report["remaps"] = []
    if lock_visuals:
        print(f"[LOFI composition] locked {report.get('summary')}")
        return report
    if report.get("figure_centered", 0) <= MAX_FIGURE_CENTERED:
        print(f"[LOFI composition] {report.get('summary')}")
        return report

    remaps: list[dict[str, str]] = []
    protected: list[int] = []
    others: list[int] = []
    for i, row in enumerate(lines):
        if not isinstance(row, dict):
            continue
        kind = classify_composition_type(row)
        if kind != "figure_centered":
            continue
        close_v = str(row.get("close_variant") or "").strip().lower()
        fn = str(row.get("beat_function") or "")
        # Never convert the hook or a portrait/eye/silhouette close.
        if i == 0 or fn == "hook" or close_v in {
            "portrait_close",
            "eye_close",
            "silhouette",
        }:
            protected.append(i)
        else:
            others.append(i)
    keep_other = max(0, MAX_FIGURE_CENTERED - len(protected))
    overflow = others[keep_other:]
    for i in overflow:
        row = lines[i]
        setting = str(row.get("setting") or "").lower()
        if any(w in setting for w in _WIDE_PLACE_RE):
            row["composition_type"] = "wide_environment"
            row["shot_scale"] = "wide" if row.get("subject_type") == "couple" else (
                row.get("shot_scale") or "medium"
            )
            if str(row.get("subject_type") or "") == "couple":
                row["silhouette_framing"] = "wide_couple"
            dest = "wide_environment"
        else:
            row["subject_type"] = "object_focus"
            row["composition_type"] = "object_focus"
            dest = "object_focus"
        row["composition_remap"] = f"figure_centered->{dest}"
        remaps.append(
            {
                "scene": str(row.get("scene") or i + 1),
                "to": dest,
                "setting": str(row.get("setting") or ""),
            }
        )
        print(
            f"[LOFI composition] remapped scene={row.get('scene') or i + 1} "
            f"figure_centered → {dest}"
        )
    report = score_composition_types(lines)
    report["remaps"] = remaps
    print(f"[LOFI composition] {report.get('summary')} remaps={len(remaps)}")
    return report


def assess_cross_run_settings(
    used: list[str],
    previous: list[str],
) -> dict[str, Any]:
    """Require >=2 archetypes the immediately preceding script did not use."""
    used_set = {a for a in used if a and a != "unknown"}
    prev_set = {a for a in previous if a and a != "unknown"}
    fresh = sorted(used_set - prev_set)
    leftover_bank = [a for a in ARCHETYPE_ORDER if a not in prev_set]
    needed = min(MIN_NEW_VS_PREVIOUS, len(leftover_bank))
    rec = {
        "previous": sorted(prev_set),
        "used": sorted(used_set),
        "fresh": fresh,
        "needed": needed,
        "leftover_bank": leftover_bank,
        "passed": len(fresh) >= needed or not prev_set,
        "reason": (
            f"fresh={fresh} prev={sorted(prev_set)} leftover={leftover_bank}"
            if prev_set
            else "no preceding script"
        ),
    }
    if not rec["passed"]:
        rec["reason"] = (
            f"need {needed} archetypes unused by the previous "
            f"script; fresh={fresh or 'none'} prev={sorted(prev_set)} "
            f"leftover={leftover_bank}"
        )
    return rec

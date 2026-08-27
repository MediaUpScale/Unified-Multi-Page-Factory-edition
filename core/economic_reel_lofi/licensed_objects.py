# -*- coding: utf-8 -*-
"""Single source of 'licensed objects for this beat'.

Every image path — literal object, figure, symbolic, landscape, character —
reads the same allow-list. VisualQA / spoken_prop and the prompt assembler
share this module so a new composition path cannot skip the inventory rule.

Stage 2 stamps ``licensed_objects`` on the beat; this module expands stems
so 'mug' also covers cup/coffee. Abstract (no-object) beats never inherit
the episode motif as a licensed prop.
"""
from __future__ import annotations

import re
from typing import Any, Iterable

_SKIP = frozenset(
    {
        "a", "an", "the", "of", "in", "on", "at", "and", "or", "still",
        "out", "left", "where", "was", "flat", "surface", "blank", "wall",
        "behind", "away", "put", "not", "own", "place", "room", "figure",
    }
)
_MUG_FAMILY = frozenset({"mug", "mugs", "cup", "cups", "coffee", "ceramic"})
_WORD_RE = re.compile(r"[a-z]+")

# Named still-life / pane nouns that Flux invents unless the spoken line
# (or this beat's licensed subject) actually names them.
_INVENTORY_CANON: dict[str, str] = {
    "kettle": "kettle",
    "kettles": "kettle",
    "teapot": "kettle",
    "mug": "mug",
    "mugs": "mug",
    "cup": "mug",
    "cups": "mug",
    "coffee": "mug",
    "ceramic": "mug",
    "vase": "vase",
    "vases": "vase",
    "plant": "plant",
    "plants": "plant",
    "flower": "plant",
    "flowers": "plant",
    "bouquet": "plant",
    "bouquets": "plant",
    "foliage": "plant",
    "curtain": "curtain",
    "curtains": "curtain",
    "blinds": "curtain",
    "jar": "jar",
    "jars": "jar",
    "bottle": "jar",
    "bottles": "jar",
    "laptop": "laptop",
    "laptops": "laptop",
    "window": "window",
    "windows": "window",
    "windowpane": "window",
    "windowpanes": "window",
}
_INVENTORY_RE = re.compile(
    r"\b(" + "|".join(sorted(_INVENTORY_CANON, key=len, reverse=True)) + r")\b",
    re.I,
)
_FURNISHED_ROOM_RE = re.compile(
    r"\bfurnished rooms?\b|\bwindow light\b|\bmug surface\b",
    re.I,
)


def _words(text: str) -> list[str]:
    return [w for w in _WORD_RE.findall((text or "").lower()) if w not in _SKIP and len(w) > 2]


def expand_licensed_stems(names: Iterable[str]) -> set[str]:
    stems: set[str] = set()
    for name in names:
        words = _words(str(name or ""))
        stems.update(words)
        if any(w in _MUG_FAMILY for w in words):
            stems.update(_MUG_FAMILY)
    return {s for s in stems if s not in _SKIP and len(s) > 2}


def is_abstract_path(beat: dict[str, Any] | None) -> bool:
    return str((beat or {}).get("abstract_license") or "").strip() in {
        "symbolic",
        "landscape",
        "character",
    }


def licensed_object_names(beat: dict[str, Any] | None) -> list[str]:
    """Ordered display names for this beat (not stems).

    Abstract no-object beats do not inherit the episode motif. The motif
    noun is not a shot-list; inventing it on a symbolic/landscape beat is
    the same leak as naming a kettle the line never spoke.
    """
    row = beat if isinstance(beat, dict) else {}
    named: list[str] = []
    raw = row.get("licensed_objects")
    if isinstance(raw, (list, tuple)):
        named.extend(str(x).strip() for x in raw if str(x).strip())
    keys: tuple[str, ...] = ("key_object",)
    if not is_abstract_path(row):
        keys = ("key_object", "episode_anchor_name", "episode_motif")
    for key in keys:
        val = str(row.get(key) or "").strip()
        if val:
            named.append(val)
    seen: set[str] = set()
    out: list[str] = []
    for n in named:
        k = n.lower()
        if k in seen:
            continue
        seen.add(k)
        out.append(n)
    return out


def licensed_stems_for_beat(beat: dict[str, Any] | None) -> set[str]:
    return expand_licensed_stems(licensed_object_names(beat))


def licensed_display(beat: dict[str, Any] | None) -> str:
    names = licensed_object_names(beat)
    return ", ".join(names)


def stamp_licensed_objects(
    beat: dict[str, Any],
    extra: Iterable[str] | None = None,
) -> list[str]:
    """Write ``licensed_objects`` from key_object + extras. Returns the list."""
    names = list(licensed_object_names(beat))
    for x in extra or ():
        n = str(x or "").strip()
        if n and n.lower() not in {m.lower() for m in names}:
            names.append(n)
    beat["licensed_objects"] = names
    return names


def _spoken_text(beat: dict[str, Any] | None) -> str:
    row = beat if isinstance(beat, dict) else {}
    return str(row.get("text") or row.get("beat_text") or "")


def line_licenses_inventory(beat: dict[str, Any] | None, stem: str) -> bool:
    """True when this beat may show the inventory noun in pixels / prompt."""
    canon = _INVENTORY_CANON.get((stem or "").lower(), (stem or "").lower())
    if not canon:
        return False
    row = beat if isinstance(beat, dict) else {}
    cap = _spoken_text(row).lower()
    if canon == "window" and (
        re.search(r"\bwindows?\b", cap) or bool(row.get("window_primary"))
    ):
        return True
    variants = {canon, f"{canon}s"}
    if any(re.search(rf"\b{re.escape(v)}\b", cap) for v in variants):
        return True
    licensed = licensed_stems_for_beat(row)
    if canon in licensed or any(v in licensed for v in variants):
        return True
    if canon == "mug" and licensed & _MUG_FAMILY:
        return True
    return False


def scrub_unlicensed_inventory(text: str, beat: dict[str, Any] | None) -> str:
    """Drop still-life / pane nouns the spoken line did not license.

    Affirmative isolation only — never insert 'no mug'. Naming a banned
    prop, even negated, still primes Flux to draw it.
    """
    row = beat if isinstance(beat, dict) else {}

    def _repl(match: re.Match[str]) -> str:
        word = match.group(0)
        stem = _INVENTORY_CANON.get(word.lower(), word.lower())
        return word if line_licenses_inventory(row, stem) else ""

    cleaned = _INVENTORY_RE.sub(_repl, text or "")
    if not line_licenses_inventory(row, "mug"):
        cleaned = _FURNISHED_ROOM_RE.sub("", cleaned)
        if not line_licenses_inventory(row, "window"):
            cleaned = re.sub(r"\bwindow light\b", "light", cleaned, flags=re.I)
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    cleaned = re.sub(r"\s+,", ",", cleaned)
    cleaned = re.sub(
        r"\b(a|an|the)\s+(and|or|by|with|from|near|beside)\b",
        r"\2",
        cleaned,
        flags=re.I,
    )
    cleaned = re.sub(
        r"\b(with|and|from|through|beside|under|against|across|"
        r"a|an|the|large|small)\s*$",
        "",
        cleaned,
        flags=re.I,
    )
    cleaned = re.sub(r"\s+\.", ".", cleaned)
    return cleaned.strip(" ,.")


def scrub_assembled_prompt(prompt: str, beat: dict[str, Any] | None) -> str:
    """Last pass on the fully concatenated string, right before the backend.

    ``enforce_licensed_inventory`` only sees four source fields. Lighting
    labels, TOD tables, retry extras, and any future concatenator can still
    inject inventory nouns. All three composition paths share this one call.
    """
    cleaned = scrub_unlicensed_inventory(prompt, beat)
    if not line_licenses_inventory(beat, "window"):
        cleaned = re.sub(
            r"\b(doorways?|sashes?|sills?)\b",
            "",
            cleaned,
            flags=re.I,
        )
        cleaned = re.sub(
            r"\b(?:wet|streaked)\s+glass\b",
            "cool paper sky",
            cleaned,
            flags=re.I,
        )
        cleaned = re.sub(
            r"\bglass\b",
            "",
            cleaned,
            flags=re.I,
        )
        cleaned = re.sub(r"\s*/\s*", " ", cleaned)
        cleaned = re.sub(
            r"\b(with|and|from|through|beside|under|against|across)\s+(?=[.,]|$)",
            "",
            cleaned,
            flags=re.I,
        )
        cleaned = re.sub(
            r"\b(?:a |an |the )?(?:large |tall |small |wide )+"
            r"letting in(?:\s+\w+)*\s+light\b",
            "light",
            cleaned,
            flags=re.I,
        )
        cleaned = re.sub(
            r"\bletting in(?:\s+\w+)?\s+light\b",
            "light",
            cleaned,
            flags=re.I,
        )
        cleaned = re.sub(
            r"\bwith a (?:large |tall |small )?letting\b",
            "",
            cleaned,
            flags=re.I,
        )
        cleaned = re.sub(
            r"\b(?:a |an |the )(?:large |tall |small |wide )+(?=[.,]|$)",
            "",
            cleaned,
            flags=re.I,
        )
    if not line_licenses_inventory(beat, "mug"):
        cleaned = re.sub(r"\bsparsely furnished\b", "empty", cleaned, flags=re.I)
        cleaned = re.sub(r"\bfurnished\b", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    cleaned = re.sub(r"\s+,", ",", cleaned)
    cleaned = re.sub(r"\s+\.", ".", cleaned)
    return cleaned.strip(" ,.")


def enforce_licensed_inventory(beat: dict[str, Any]) -> None:
    """Scrub setting / scene copy so every path shares one allow-list."""
    if not isinstance(beat, dict):
        return
    for key in ("setting", "scene_description", "visual_concept", "atmosphere_place"):
        val = str(beat.get(key) or "")
        if not val:
            continue
        scrubbed = scrub_unlicensed_inventory(val, beat)
        if key in {"setting", "atmosphere_place"}:
            beat[key] = scrubbed or "a quiet indoor room"
        elif scrubbed:
            beat[key] = scrubbed
    if is_abstract_path(beat):
        extras = []
        ko = str(beat.get("key_object") or "").strip()
        if ko:
            extras.append(ko)
        stamp_licensed_objects(beat, extra=extras)
        beat["licensed_objects"] = [ko] if ko else []

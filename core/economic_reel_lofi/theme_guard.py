# -*- coding: utf-8 -*-
"""Assigned-theme lock: a monologue must not name a different bank theme."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from core.economic_reel_lofi import config as lofi_cfg

_THEME_SYNONYMS: dict[str, tuple[str, ...]] = {
    "trust": ("trust",),
    "secure_love": ("secure love",),
    "self_respect": ("self-respect", "self respect"),
    "attachment": ("attachment",),
    "communication": ("communication",),
    "healing": ("healing",),
    "vulnerability": ("vulnerability",),
    "distance": ("distance",),
    "loneliness": ("loneliness",),
    "grief": ("grief",),
    "loss": ("loss",),
    "perseverance": ("perseverance",),
    "inspiration": ("inspiration",),
    "regret": ("regret",),
    "belonging": ("belonging",),
    "time": ("time",),
    "forgiveness": ("forgiveness",),
    "hope": ("hope",),
    "betrayal": ("betrayal",),
}

_NAMED_THEME_RE = re.compile(
    r"(?:"
    r"that'?s what (?P<a>[\w][\w '-]{1,24}) (?:was|is|actually was)"
    r"|that'?s all (?P<b>[\w][\w '-]{1,24}) actually was"
    r"|(?P<c>[\w][\w '-]{1,24}) (?:cost|costs) (?:me|you|us|something)"
    r"|(?P<d>[\w][\w '-]{1,24}) (?:isn'?t|is not|is|was|wasn'?t|means) "
    r")"
    r"",
    re.I,
)


def bank_theme_names() -> list[str]:
    path = lofi_cfg.STORE_DIR / "lofi_theme_bank_relationship.json"
    if not path.is_file():
        return list(_THEME_SYNONYMS)
    raw = json.loads(path.read_text(encoding="utf-8"))
    out: list[str] = []
    seen: set[str] = set()
    for row in raw if isinstance(raw, list) else []:
        if not isinstance(row, dict):
            continue
        theme = str(row.get("theme") or "").strip().lower()
        if theme and theme not in seen:
            seen.add(theme)
            out.append(theme)
    return out or list(_THEME_SYNONYMS)


def _aliases(theme: str) -> set[str]:
    key = str(theme or "").strip().lower().replace("-", "_")
    names = {key, key.replace("_", " "), key.replace("_", "-")}
    names.update(_THEME_SYNONYMS.get(key, ()))
    return {n.lower() for n in names if n}


def _normalize_span(text: str) -> str:
    return " ".join(str(text or "").lower().split()).strip(" .,;:—–\"'")


def _core_noun(span: str) -> str:
    blob = _normalize_span(span)
    for prefix in ("that's all ", "thats all ", "all ", "real ", "the ", "that "):
        if blob.startswith(prefix):
            blob = blob[len(prefix) :].strip()
    return blob


def _match_other_theme(span: str, assigned: str) -> str | None:
    blob = _core_noun(span)
    if not blob:
        return None
    allowed = _aliases(assigned)
    # Prefer longer theme names so "self respect" wins over "respect".
    ranked: list[tuple[int, str, tuple[str, ...]]] = []
    for theme in bank_theme_names():
        aliases = tuple(sorted(_aliases(theme), key=len, reverse=True))
        ranked.append((max((len(a) for a in aliases), default=0), theme, aliases))
    ranked.sort(reverse=True)
    for _n, theme, aliases in ranked:
        if theme in allowed or any(a in allowed for a in aliases):
            continue
        for alias in aliases:
            if len(alias) < 4:
                continue
            if blob == alias:
                return theme
    return None


def assess_theme_drift(
    monologue: str,
    assigned_theme: str,
    *,
    quiet: bool = False,
) -> dict[str, Any]:
    """Flag a named other-theme in definition / cost / 'that's what X was' frames."""
    hits: list[dict[str, str]] = []
    for m in _NAMED_THEME_RE.finditer(str(monologue or "")):
        span = next((g for g in m.groups() if g), "")
        other = _match_other_theme(span, assigned_theme)
        if not other:
            continue
        hits.append(
            {
                "named": _normalize_span(span),
                "other_theme": other,
                "match": m.group(0).strip(),
            }
        )
    # Also catch "That's what hope was" if the generic grouper missed it.
    if not hits:
        for theme in bank_theme_names():
            if theme in _aliases(assigned_theme):
                continue
            for alias in _aliases(theme):
                if len(alias) < 4:
                    continue
                pat = re.compile(
                    rf"\b(?:that'?s what|that'?s all)\s+{re.escape(alias)}\b"
                    rf"|\b{re.escape(alias)}\s+(?:cost|costs|isn'?t|is not|means)\b",
                    re.I,
                )
                found = pat.search(str(monologue or ""))
                if found:
                    hits.append(
                        {
                            "named": alias,
                            "other_theme": theme,
                            "match": found.group(0),
                        }
                    )
    fails = [
        f"theme-drift: named {h['other_theme']!r} via {h['match']!r} "
        f"(assigned {assigned_theme})"
        for h in hits
    ]
    if not quiet:
        if fails:
            print(f"[LOFI theme] DRIFT {fails}")
        else:
            print(f"[LOFI theme] lock pass assigned={assigned_theme}")
    return {"hits": hits, "fails": fails}


def sentence_overage(monologue: str, max_words: int) -> list[str]:
    parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+", str(monologue or "")) if p.strip()]
    over = []
    for p in parts:
        n = len(p.split())
        if n > int(max_words):
            over.append(f"{n}w: {p}")
    return over


def scan_pattern_theme_leaks(patterns: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Patterns whose example/shape names a bank theme as an abstract noun."""
    if patterns is None:
        src = Path(lofi_cfg.DATA_DIR) / "structure_library_v2.json"
        raw = json.loads(src.read_text(encoding="utf-8")) if src.is_file() else {}
        patterns = list(raw.get("patterns") or [])
    risks: list[dict[str, Any]] = []
    for p in patterns:
        if not isinstance(p, dict):
            continue
        example = str(p.get("in_house_example") or "")
        meta = " ".join(str(p.get(k) or "") for k in ("name", "label", "shape"))
        # Dummy assigned theme so every bank name in a definition-frame is a leak.
        drift = assess_theme_drift(example, "__unassigned__", quiet=True)
        def_named = sorted({h.get("other_theme") for h in (drift.get("hits") or []) if h.get("other_theme")})
        mentioned: list[str] = []
        for theme in bank_theme_names():
            for alias in _aliases(theme):
                if len(alias) < 4:
                    continue
                if re.search(rf"\b{re.escape(alias)}\b", example + " " + meta, re.I):
                    mentioned.append(theme)
                    break
        if def_named or mentioned:
            risks.append(
                {
                    "name": str(p.get("name") or ""),
                    "definition_frames": def_named,
                    "mentioned": mentioned,
                    "risk": "high" if def_named else "watch",
                    "hits": [h.get("match") for h in (drift.get("hits") or [])],
                }
            )
    return risks

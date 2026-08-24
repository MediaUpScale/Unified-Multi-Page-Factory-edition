# -*- coding: utf-8 -*-
"""Lock off-domain 9-line drafts. Captions stay as composed; visuals only change."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core_engine.economic_reel_lofi import lofi_collections as rag
from core_engine.economic_reel_lofi.script_agent import (
    _CONTENT_STOP,
    _PROP_ADJ,
    _narrative_gate_reasons,
    _thematic_from_lines,
    assess_caption_still_alignment,
)
from core_engine.economic_reel_lofi.setting_archetypes import classify_setting_archetype
from core_engine.economic_reel_lofi.theme_guard import assess_theme_drift
from core_engine.economic_reel_lofi.visual_identity import (
    _object_stem,
    _setting_stem,
    restore_anchor_visual_fields,
    score_episode_variety,
    setting_object_pool,
)

CORPUS = (
    Path(__file__).resolve().parents[1] / "store" / "offdomain_drift_corpus.json"
)
OUT_DIR = ROOT / "outputs" / "wonder_feed" / "clips"

SOURCE_TERMS = {
    "trust": (r"\btrust\b", r"\btrustworthy\b", r"\breal trust\b"),
    "grief": (r"\bgrief\b", r"\bgrieve\b", r"\bmourn", r"\bfuneral\b"),
    "healing": (r"\bhealing\b", r"\bheal(?:ed|s|ing)?\b"),
    "forgiveness": (
        r"\bforgiveness\b",
        r"\bforgive[ns]?\b",
        r"\bgrudge\b",
        r"\bresen",
    ),
    "loneliness": (
        r"\bloneliness\b",
        r"\blonely\b",
        r"\bnobody(?:'s| is)? (?:checking|waiting|waiting up)\b",
    ),
}


def _theme_row(theme: str) -> dict:
    bank = rag._read_json(rag._store_path("lofi_theme_bank_relationship"), [])
    for row in bank:
        if isinstance(row, dict) and str(row.get("theme") or "") == theme:
            return row
    return {"theme": theme, "module": "relationship"}


def _restore_captions(script: dict, captions: list[str]) -> None:
    lines = [r for r in (script.get("lines") or []) if isinstance(r, dict)]
    for i, text in enumerate(captions):
        if i >= len(lines):
            break
        lines[i]["text"] = text
        lines[i]["beat_text"] = text
    script["lines"] = lines
    script["monologue"] = " ".join(captions)
    script["source_monologue"] = " ".join(captions)
    script["monologue_split"] = True
    script["direct_beats"] = True


def _force_hook_drama(row: dict) -> None:
    row["subject_type"] = "woman"
    row["subject_expression"] = "steady, lit"
    row["time_of_day"] = "dusk"
    row["setting"] = "street corner under a streetlamp"
    row["key_object"] = "streetlamp"
    row["composition_type"] = "figure_centered"
    row["setting_archetype"] = classify_setting_archetype(
        row["setting"], row["key_object"]
    )


def _fix_still_align(lines: list[dict]) -> None:
    """Object-focus stills must be named (or a pronoun). Else drop to environment."""
    for row in lines:
        if not isinstance(row, dict):
            continue
        if str(row.get("composition_type") or "") != "object_focus" and str(
            row.get("subject_type") or ""
        ) != "object_focus":
            continue
        obj = str(row.get("key_object") or "")
        stems = [
            t
            for t in re.findall(r"[a-z]+", obj.lower())
            if t not in _PROP_ADJ and t not in _CONTENT_STOP and len(t) >= 3
        ]
        text = str(row.get("text") or row.get("beat_text") or "")
        named = any(s in text.lower() for s in stems)
        pronoun = bool(re.search(r"\b(it|this|that|them|those)\b", text, re.I))
        if named or pronoun:
            continue
        row["subject_type"] = "silhouette"
        row["composition_type"] = "wide_environment"
        row["visual_fallback"] = "still_align_wide"


_ORPHAN_OBJECTS = (
    "unclaimed program",
    "two chairs still out",
    "unmade empty bed",
    "tide line",
    "same weekly pass",
    "unclaimed boarding pass",
)


def _set_visual(
    row: dict,
    setting: str,
    key_object: str,
    composition: str,
    subject: str = "silhouette",
) -> None:
    row["setting"] = setting
    row["key_object"] = key_object
    row["composition_type"] = composition
    row["subject_type"] = subject
    row["setting_archetype"] = classify_setting_archetype(setting, key_object)
    row["visual_fallback"] = "caption_tied"


def _caption_tie_visuals(lines: list[dict], theme: str) -> None:
    """Keep stills on spoken objects. Variety-bank remaps caused today's visual bugs."""
    restore_anchor_visual_fields(lines)
    for i, row in enumerate(lines):
        if not isinstance(row, dict):
            continue
        if i == 0:
            continue
        text = str(row.get("text") or row.get("beat_text") or "").lower()
        obj = str(row.get("key_object") or "").lower()
        orphan = any(o in obj for o in _ORPHAN_OBJECTS)
        if theme == "perseverance":
            if any(k in text for k in ("shoe", "lace", "knot")):
                tied = "untied" not in text and (
                    "stayed tied" in text or "tied longer" in text
                )
                _set_visual(
                    row,
                    "stoop at dawn",
                    "tied shoes on a stoop" if tied else "untied shoes",
                    "object_focus" if "hands" not in text else "figure_centered",
                    "object_focus" if "hands" not in text else "silhouette",
                )
            elif orphan or "passenger seat" in obj or "streetlamp" in obj:
                _set_visual(
                    row,
                    "empty park path at dusk",
                    "distant horizon",
                    "wide_environment",
                )
        elif theme == "belonging":
            if any(k in text for k in ("drink", "ice")):
                _set_visual(
                    row,
                    "party kitchen doorway",
                    "untouched drink",
                    "object_focus",
                    "object_focus",
                )
            elif "doorway" in text or "kitchen" in text or "room" in text:
                _set_visual(
                    row,
                    "party kitchen doorway",
                    "kitchen doorway",
                    "wide_environment",
                )
            elif "seat" in text:
                _set_visual(
                    row,
                    "party kitchen doorway",
                    "empty kitchen chair",
                    "wide_environment",
                )
            elif orphan or "passenger seat" in obj or "empty bed" in obj:
                _set_visual(
                    row,
                    "party kitchen doorway",
                    "untouched drink",
                    "wide_environment",
                )
        elif theme == "communication":
            if any(k in text for k in ("seat", "driving", "shotgun")):
                _set_visual(
                    row,
                    "parked car at evening, door open",
                    "empty passenger seat of a car",
                    "object_focus" if "seat" in text else "wide_environment",
                    "object_focus" if "seat" in text else "silhouette",
                )
            elif orphan or "empty bed" in obj:
                _set_visual(
                    row,
                    "empty park path at dusk",
                    "distant horizon",
                    "wide_environment",
                )
        elif theme == "vulnerability":
            if "towel" in text or "kitchen" in text:
                _set_visual(
                    row,
                    "kitchen floor at dusk",
                    "dropped dish towel",
                    "object_focus" if "towel" in text else "figure_centered",
                    "object_focus" if "towel" in text else "silhouette",
                )
            elif orphan or "passenger seat" in obj:
                _set_visual(
                    row,
                    "kitchen at dusk",
                    "empty kitchen counter",
                    "wide_environment",
                )
        elif theme == "regret":
            if any(k in text for k in ("letter", "desk", "sealed")):
                _set_visual(
                    row,
                    "desk at night",
                    "folded letter never sent",
                    "object_focus" if "letter" in text else "figure_centered",
                    "object_focus" if "letter" in text else "silhouette",
                )
            elif orphan:
                _set_visual(
                    row,
                    "desk at night",
                    "folded letter never sent",
                    "wide_environment",
                )
    restore_anchor_visual_fields(lines)
    _fix_still_align(lines)


def _line_stats(captions: list[str]) -> list[str]:
    out = []
    for i, t in enumerate(captions, 1):
        w = len(t.split())
        c = len(t)
        flag = " OVER" if w > 9 or c > 56 else ""
        out.append(f"{i}. ({w}w/{c}c{flag}) {t}")
    return out


def scan_source_leak(text: str, source: str) -> list[str]:
    hits: list[str] = []
    blob = str(text or "")
    for part in str(source or "").split("/"):
        for pat in SOURCE_TERMS.get(part.strip(), ()):
            m = re.search(pat, blob, re.I)
            if m:
                hits.append(m.group(0))
    return hits


def _build_one(rec: dict) -> dict:
    theme = str(rec.get("theme") or "")
    captions = list(rec.get("captions") or [])
    theme_row = _theme_row(theme)
    pool = setting_object_pool(theme_row)
    rhetoric = {
        "name": rec.get("pattern") or "",
        "locked_thesis": rec.get("thesis") or "",
        "locked_anchor": dict(rec.get("anchor") or {}),
    }
    script = _thematic_from_lines(
        captions,
        module="relationship",
        theme=theme,
        scene_count=9,
        hook_type="definition",
        pool=pool,
        retrieved=[],
        structure={},
        rhetoric=rhetoric,
        seed={"hooks": [], "details": []},
        writer="claude",
    )
    _restore_captions(script, captions)
    lines = [r for r in (script.get("lines") or []) if isinstance(r, dict)]
    if lines:
        _force_hook_drama(lines[0])
        _fix_still_align(lines)
        _caption_tie_visuals(lines, theme)
    _restore_captions(script, captions)
    reasons = _narrative_gate_reasons(script, "relationship")
    script["holds_episode"] = bool(reasons)
    script["narrative_holds"] = reasons
    script["theme"] = theme
    script["module"] = "relationship"
    script["arc_template"] = "thematic_arc"
    script["rhetoric_pattern"] = rec.get("pattern") or ""
    script["source_domain"] = rec.get("source_domain") or ""
    return script


def main() -> int:
    raw = json.loads(CORPUS.read_text(encoding="utf-8"))
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for rec in raw:
        theme = str(rec.get("theme") or "")
        captions = list(rec.get("captions") or [])
        script = _build_one(rec)
        print("=" * 70)
        print(
            f"THEME {theme}  pattern={rec.get('pattern')}  "
            f"source={rec.get('source_domain')}"
        )
        for line in _line_stats(captions):
            print(" ", line)
        drift = assess_theme_drift(" ".join(captions), theme)
        leaks = scan_source_leak(" ".join(captions), str(rec.get("source_domain") or ""))
        print("  theme_drift_gate", drift.get("fails") or "pass")
        print(
            "  source_domain_leak",
            leaks or "PASS (no named/clear source-theme reference)",
        )
        holds = script.get("narrative_holds") or []
        print("  holds", holds or "none")
        print(
            "  still_align",
            assess_caption_still_alignment(
                [r for r in (script.get("lines") or []) if isinstance(r, dict)]
            )
        )
        variety = score_episode_variety(
            [r for r in (script.get("lines") or []) if isinstance(r, dict)]
        )
        print("  variety", variety.get("summary"))
        out = OUT_DIR / f"lofi_script_{theme}_20260824_offdomain.json"
        out.write_text(json.dumps(script, indent=2, ensure_ascii=False), encoding="utf-8")
        print("  wrote", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

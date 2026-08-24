# -*- coding: utf-8 -*-
"""Build locked 9-line scripts from the approved captions. Visuals only change."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core_engine.economic_reel_lofi import lofi_collections as rag
from core_engine.economic_reel_lofi.script_agent import (
    _narrative_gate_reasons,
    _thematic_from_lines,
)
from core_engine.economic_reel_lofi.setting_archetypes import (
    ARCHETYPE_ORDER,
    classify_setting_archetype,
    pairs_for_archetype,
    score_setting_archetypes,
)
from core_engine.economic_reel_lofi.visual_identity import (
    _object_stem,
    _setting_stem,
    score_episode_variety,
    setting_object_pool,
)

CORPUS = (
    Path(__file__).resolve().parents[1]
    / "store"
    / "prose_writer_validate_corpus.json"
)
OUT_DIR = ROOT / "outputs" / "wonder_feed" / "clips"


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


def _swap_duplicate_setting(lines: list[dict], *, avoid_scene: int = 1) -> None:
    stems: dict[str, list[int]] = {}
    for i, row in enumerate(lines):
        stem = _setting_stem(str(row.get("setting") or ""))
        if stem:
            stems.setdefault(stem, []).append(i)
    dup_idx = None
    for stem, idxs in stems.items():
        if len(idxs) < 2:
            continue
        for i in idxs:
            if i + 1 != avoid_scene:
                dup_idx = i
                break
        if dup_idx is not None:
            break
    if dup_idx is None:
        # chair object repeat
        obj_hits: dict[str, list[int]] = {}
        for i, row in enumerate(lines):
            stem = _object_stem(str(row.get("key_object") or ""))
            if stem:
                obj_hits.setdefault(stem, []).append(i)
        for stem, idxs in obj_hits.items():
            if stem and len(idxs) >= 2:
                for i in idxs:
                    if i + 1 != avoid_scene:
                        dup_idx = i
                        break
            if dup_idx is not None:
                break
    if dup_idx is None:
        return
    used_arch = {
        classify_setting_archetype(
            str(r.get("setting") or ""), str(r.get("key_object") or "")
        )
        for r in lines
    }
    used_set = {_setting_stem(str(r.get("setting") or "")) for r in lines}
    used_obj = {_object_stem(str(r.get("key_object") or "")) for r in lines}
    counts = score_setting_archetypes(lines).get("counts") or {}
    unused = [a for a in ARCHETYPE_ORDER if a not in used_arch]
    if not unused:
        unused = [a for a in ARCHETYPE_ORDER if int(counts.get(a) or 0) < 2]
    pick = None
    for arch in unused:
        for pair in pairs_for_archetype(arch):
            st = _setting_stem(pair["setting"])
            ob = _object_stem(pair["key_object"])
            if st in used_set or ob in used_obj:
                continue
            pick = pair
            break
        if pick:
            break
    if pick is None:
        return
    row = lines[dup_idx]
    print(
        f"[lock] swap scene={dup_idx + 1} "
        f"{row.get('setting')!r}/{row.get('key_object')!r} "
        f"-> {pick['setting']!r}/{pick['key_object']!r}"
    )
    row["setting"] = pick["setting"]
    row["key_object"] = pick["key_object"]
    row["setting_archetype"] = pick.get("archetype") or classify_setting_archetype(
        pick["setting"], pick["key_object"]
    )


def _fix_attachment_stills(lines: list[dict]) -> None:
    if len(lines) < 8:
        return
    s7 = lines[6]
    s7["setting"] = "street corner under a streetlamp"
    s7["key_object"] = "empty sidewalk"
    s7["time_of_day"] = "dusk"
    s7["composition_type"] = "wide_environment"
    s7["subject_type"] = "silhouette"
    s7["setting_archetype"] = classify_setting_archetype(
        s7["setting"], s7["key_object"]
    )
    s8 = lines[7]
    s8["setting"] = "crowded sidewalk after rain"
    s8["key_object"] = "empty sidewalk"
    s8["time_of_day"] = "night"
    s8["composition_type"] = "object_focus"
    s8["subject_type"] = "object_focus"
    s8["setting_archetype"] = classify_setting_archetype(
        s8["setting"], s8["key_object"]
    )
    print("[lock] attachment s7/s8 retied to sidewalk/window")


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
    if theme == "self_respect" and lines:
        _force_hook_drama(lines[0])
        _swap_duplicate_setting(lines, avoid_scene=1)
    if theme == "attachment" and lines:
        _fix_attachment_stills(lines)
    _restore_captions(script, captions)
    reasons = _narrative_gate_reasons(script, "relationship")
    script["holds_episode"] = bool(reasons)
    script["narrative_holds"] = reasons
    script["theme"] = theme
    script["module"] = "relationship"
    script["arc_template"] = "thematic_arc"
    return script


def main() -> int:
    raw = json.loads(CORPUS.read_text(encoding="utf-8"))
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    paths = []
    for rec in raw:
        theme = str(rec.get("theme") or "")
        script = _build_one(rec)
        captions = list(rec.get("captions") or [])
        got = [
            str(r.get("text") or "")
            for r in (script.get("lines") or [])
            if isinstance(r, dict)
        ]
        if got != captions:
            raise SystemExit(f"{theme}: captions drifted")
        holds = script.get("narrative_holds") or []
        print(f"\nTHEME {theme} holds={holds or 'none'}")
        variety = score_episode_variety(
            [r for r in (script.get("lines") or []) if isinstance(r, dict)]
        )
        print(f"  variety={variety.get('summary')}")
        for i, row in enumerate(script.get("lines") or [], 1):
            print(
                f"  {i}. {row.get('text')} | {row.get('setting')} / "
                f"{row.get('key_object')} | {row.get('subject_type')} "
                f"{row.get('time_of_day')}"
            )
        out = OUT_DIR / f"lofi_script_{theme}_20260823_locked.json"
        out.write_text(json.dumps(script, indent=2, ensure_ascii=False), encoding="utf-8")
        paths.append(str(out))
        print(f"  wrote {out}")
        if holds:
            print(f"  STILL HOLD {holds}")
    print("\nLOCKED", paths)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

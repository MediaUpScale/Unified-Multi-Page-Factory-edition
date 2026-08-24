# -*- coding: utf-8 -*-
"""Regenerate the 5 held structure-library scripts under the stripped pipeline."""
from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path

os.environ.setdefault("LOFI_SKIP_COHERENCE_LLM", "1")
os.environ.setdefault("LOFI_DEEPSEEK_TEMPERATURE", "1.15")
os.environ.setdefault("LOFI_DEEPSEEK_CANDIDATES", "3")

from core_engine.economic_reel_lofi import lofi_collections as rag
from core_engine.economic_reel_lofi.lofi_collections import normalize_caption_text
from core_engine.economic_reel_lofi.claude_polish import (
    polish_scripts_batch,
    summarize_cost_log,
)
from core_engine.economic_reel_lofi.script_agent import (
    _call_llm,
    _extract_json_object,
    _line_word_count,
    _narrative_gate_reasons,
    _rhetoric_prompt_block,
    _sanitize_caption_typos,
)
from core_engine.economic_reel_lofi.setting_archetypes import (
    classify_composition_type,
    classify_setting_archetype,
    score_composition_types,
    score_setting_archetypes,
)

STORE = Path("core_engine/economic_reel_lofi/store")
PACK = (
    ("forgiveness", "definition_then_evidence", ""),
    ("loss", "negation_list_to_permission", ""),
    ("loneliness", "domino_chain", ""),
    ("regret", "parable_triad", ""),
    ("belonging", "contrast_metaphor_resolution", "anaphora_to_universal_law"),
)
N_CAND = int(os.getenv("LOFI_DEEPSEEK_CANDIDATES", "3") or 3)


def _load(theme: str) -> dict:
    path = STORE / f"locked_script_{theme}_structure_v1.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _beat_context(script: dict) -> str:
    rows = []
    for row in script.get("lines") or []:
        if not isinstance(row, dict):
            continue
        rows.append(
            f"  {row.get('scene')}: setting={row.get('setting')!r} "
            f"object={row.get('key_object')!r} "
            f"who={row.get('subject_type')!r}"
        )
    return "\n".join(rows)


def _caption_prompt(script: dict, rhetoric: dict) -> str:
    theme = script.get("theme")
    ao = script.get("anchor_object") if isinstance(script.get("anchor_object"), dict) else {}
    rhetoric = dict(rhetoric)
    rhetoric["locked_thesis"] = str(script.get("thesis") or "")
    rhetoric["locked_anchor"] = ao
    return f"""Write {len(script.get('lines') or [])} original spoken lines as STRICT JSON.

THEME: {theme}
BEAT COUNT: {len(script.get('lines') or [])}
THESIS (internal — do not speak it): {script.get('thesis')}
ANCHOR OBJECT: {ao.get('name')}
  first seen: {ao.get('initial_state')}
  later: {ao.get('final_state')}
CHARACTER / SETTING CONTEXT:
{_beat_context(script)}
{_rhetoric_prompt_block(rhetoric)}

Invent every sentence. Follow the assigned pattern's SHAPE only.
Return STRICT JSON: {{"lines": ["line 1", "line 2", ...]}} with exactly {len(script.get('lines') or [])} strings.
"""


def _extract_lines(raw: str, n: int) -> list[str]:
    data = _extract_json_object(raw)
    rows = data.get("lines") if isinstance(data, dict) else None
    out: list[str] = []
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, str):
                out.append(_sanitize_caption_typos(row).strip())
            elif isinstance(row, dict):
                out.append(
                    _sanitize_caption_typos(
                        str(row.get("text") or row.get("beat_text") or "")
                    ).strip()
                )
    if len(out) < n:
        raise ValueError(f"expected {n} lines, got {len(out)}")
    return out[:n]


def _overlay(script: dict, lines: list[str]) -> dict:
    out = deepcopy(script)
    for row, text in zip(out.get("lines") or [], lines):
        if isinstance(row, dict):
            row["text"] = text
            row["beat_text"] = text
    out["monologue"] = " ".join(lines)
    return out


def _peer_caption_holds(script: dict, peers: list[dict]) -> list[str]:
    mine = {
        normalize_caption_text(str(r.get("text") or ""))
        for r in (script.get("lines") or [])
        if isinstance(r, dict)
    }
    hits: list[str] = []
    for peer in peers:
        if str(peer.get("theme") or "") == str(script.get("theme") or ""):
            continue
        for row in peer.get("lines") or []:
            if not isinstance(row, dict):
                continue
            norm = normalize_caption_text(str(row.get("text") or ""))
            if norm and len(norm.split()) >= 4 and norm in mine:
                hits.append(f"batch_caption_reuse:{norm}")
    return hits


def _gate(script: dict, seen: list[dict], peers: list[dict] | None = None) -> list[str]:
    data = deepcopy(script)
    for row in data.get("lines") or []:
        if isinstance(row, dict):
            row["setting_archetype"] = classify_setting_archetype(
                row.get("setting", ""), row.get("key_object", "")
            )
            row["composition_type"] = classify_composition_type(row)
    data["_connective_extra_records"] = list(seen)
    reasons = _narrative_gate_reasons(data, "relationship")
    reasons.extend(_peer_caption_holds(script, peers or []))
    return [r for r in reasons if r]


def _cmap_rec(script: dict) -> dict:
    cmap = rag.extract_connective_map(script.get("lines") or [])
    return {
        "theme": script.get("theme"),
        "map": cmap,
        "sequence": rag.connective_sequence(cmap),
        "pairs": {(int(r["scene"]), str(r["word"])) for r in cmap},
    }


def _report_lines(script: dict) -> None:
    for row in script.get("lines") or []:
        text = str(row.get("text") or "")
        print(
            f"  {row.get('scene')} {row.get('beat_function'):12} "
            f"{_line_word_count(text)}w/{len(text)}c {text!r}"
        )


def main() -> None:
    rag.reset_batch_scripts()
    rag.ensure_seeded()
    before: list[dict] = []
    seen: list[dict] = []
    for theme, pattern, overlay in PACK:
        locked = _load(theme)
        rhetoric = rag.get_rhetoric_pattern(pattern) or {"name": pattern}
        rhetoric["hook_overlay"] = overlay
        print("=" * 60, theme, pattern, overlay or "no-overlay")
        best = None
        best_n = 10**9
        best_holds: list[str] = []
        for ci in range(N_CAND):
            prompt = _caption_prompt(locked, rhetoric)
            if ci:
                prompt += "\nWrite a fresh draft. Do not repeat the previous captions.\n"
            try:
                raw = _call_llm(prompt)
                lines = _extract_lines(raw, 9)
            except Exception as exc:  # noqa: BLE001
                print(f"  candidate {ci + 1} fail: {exc}")
                continue
            cand = _overlay(locked, lines)
            holds = _gate(cand, seen, before)
            print(f"  candidate {ci + 1}/{N_CAND} holds={len(holds)}")
            for h in holds[:6]:
                print(f"    - {h}")
            if len(holds) < best_n:
                best, best_n, best_holds = cand, len(holds), holds
            if not holds:
                break
        if best is None:
            raise SystemExit(f"{theme}: no parseable DeepSeek candidate")
        best["rhetoric_pattern"] = pattern
        best["rhetoric_hook_overlay"] = overlay
        best["deepseek_holds"] = best_holds
        before.append(best)
        seen.append(_cmap_rec(best))
        print("  DEEPSEEK FINALIST")
        _report_lines(best)
        if best_holds:
            print("  HOLDS", best_holds)

    passing = [s for s in before if not s.get("deepseek_holds")]
    print("=" * 60, f"polish {len(passing)}/{len(before)} finalists")
    polished_map: dict[str, dict] = {}
    if passing:
        polished = polish_scripts_batch(passing)
        for src, dst in zip(passing, polished):
            theme = str(src.get("theme") or "")
            if dst.get("polished"):
                holds = _gate(dst, [], [p for p in passing if p is not src])
                if holds:
                    print(f"  {theme} polish broke gates — keeping DeepSeek")
                    for h in holds[:6]:
                        print(f"    - {h}")
                    polished_map[theme] = src
                else:
                    polished_map[theme] = dst
            else:
                polished_map[theme] = src

    out_rows = []
    for src in before:
        theme = str(src.get("theme") or "")
        after = polished_map.get(theme, src)
        dest = STORE / f"locked_script_{theme}_structure_v2.json"
        dest.write_text(json.dumps(after, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print("=" * 60, theme, "BEFORE / AFTER")
        print("BEFORE")
        _report_lines(src)
        print("AFTER")
        _report_lines(after)
        out_rows.append(
            {
                "theme": theme,
                "pattern": src.get("rhetoric_pattern"),
                "overlay": src.get("rhetoric_hook_overlay"),
                "deepseek_holds": src.get("deepseek_holds") or [],
                "polished": bool(after.get("polished")),
                "before": [str(r.get("text") or "") for r in (src.get("lines") or [])],
                "after": [str(r.get("text") or "") for r in (after.get("lines") or [])],
                "path": str(dest),
            }
        )
    report = STORE / "structure_pack_rerun_report.json"
    report.write_text(json.dumps(out_rows, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    costs = summarize_cost_log(10)
    print("=" * 60, "COST", costs)
    print("report", report)


if __name__ == "__main__":
    main()

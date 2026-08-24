# -*- coding: utf-8 -*-
"""Run 2–3 fresh themes through the paragraph writer and print QA."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("LOFI_SKIP_COHERENCE_LLM", "1")
os.environ.setdefault("LOFI_SKIP_COMPOSE_ALTERNATE", "1")

from core_engine.economic_reel_lofi import config as lofi_cfg
from core_engine.economic_reel_lofi import lofi_collections as rag
from core_engine.economic_reel_lofi.claude_polish import summarize_cost_log
from core_engine.economic_reel_lofi.corpus_echo import echo_hits
from core_engine.economic_reel_lofi.script_agent import generate_script
from core_engine.economic_reel_lofi.theme_guard import (
    assess_theme_drift,
    scan_pattern_theme_leaks,
)

_ARGUMENT_HOLD_MARKERS = (
    "principle",
    "insight-hook",
    "insight closing",
    "object-churn",
    "connective",
    "theme-drift",
    "recipe-collapse",
    "sentence-variety",
    "HOOK:",
    "anchor_object",
)
_VISUAL_HOLD_MARKERS = (
    "still-align",
    "episode_variety",
    "setting archetype",
    "composition hold",
    "hook scene",
)

_CLAUSE_END = tuple(".!?,;:—–")
_HANGING = frozenset(
    {"the", "a", "an", "to", "of", "for", "and", "or", "i", "used", "it's"}
)


def _clause_flags(captions: list[str]) -> list[str]:
    flags = []
    prev = ""
    for i, line in enumerate(captions, 1):
        t = (line or "").strip()
        if not t:
            flags.append(f"s{i} empty")
            continue
        starts_lower = t[0].islower()
        prev_punct = bool(prev and prev.rstrip()[-1:] in _CLAUSE_END)
        last = t.split()[-1].lower().strip("\"'")
        if starts_lower and not prev_punct:
            flags.append(f"s{i} mid-phrase start: {t!r}")
        if last in _HANGING:
            flags.append(f"s{i} hanging tail {last!r}: {t!r}")
        prev = t
    return flags

THEMES = [
    ("distance", None),
    ("self_respect", None),
    ("attachment", None),
]


def _theme_row(theme: str) -> dict:
    bank = rag._read_json(rag._store_path("lofi_theme_bank_relationship"), [])
    for row in bank:
        if isinstance(row, dict) and str(row.get("theme") or "") == theme:
            return row
    return {"theme": theme, "module": "relationship"}


def main() -> int:
    rag.reset_structure_library_cache()
    lib = rag.load_structure_library()
    leaks = scan_pattern_theme_leaks(list(lib.get("patterns") or []))
    print(f"[validate] library={lib.get('source')} patterns={len(lib.get('patterns') or [])}")
    high = [r for r in leaks if r.get("risk") == "high"]
    watch = [r for r in leaks if r.get("risk") != "high"]
    print(f"[validate] pattern theme-leak HIGH={len(high)} WATCH={len(watch)}")
    for r in high:
        print(
            f"  HIGH {r.get('name')} frames={r.get('definition_frames')} "
            f"hits={r.get('hits')}"
        )
    for r in watch:
        print(f"  watch {r.get('name')} mentioned={r.get('mentioned')}")
    report = []
    for theme, force in THEMES:
        print("\n" + "=" * 72)
        print(f"THEME {theme} force={force}")
        kwargs = dict(
            module="relationship",
            theme=theme,
            subtheme="",
            scene_count=9,
            theme_row=_theme_row(theme),
            skip_polish=True,
        )
        if force:
            kwargs["force_rhetoric"] = force
        script = None
        lines: list[str] = []
        mono = ""
        echo_mono: list = []
        echo_caps: list = []
        for attempt in range(1, 4):
            script = generate_script(**kwargs)
            lines = [
                str(r.get("text") or "")
                for r in (script.get("lines") or [])
                if isinstance(r, dict)
            ]
            mono = str(script.get("source_monologue") or "")
            echo_mono = echo_hits(mono)
            echo_caps = []
            for i, line in enumerate(lines, 1):
                hits = echo_hits(line)
                if hits:
                    echo_caps.append({"scene": i, "text": line, "hits": hits})
            if not echo_mono and not echo_caps:
                break
            print(
                f"[validate] ECHO on {theme} attempt={attempt} "
                f"mono={echo_mono} caps={echo_caps} — regenerating"
            )
            kwargs["feedback"] = (
                "Previous draft reused a 4+ word sequence from a reference "
                "piece. Write 9 fully original lines. Forbidden fragments: "
                + "; ".join(
                    h["ngram"]
                    for h in (echo_mono + [x for rec in echo_caps for x in rec["hits"]])
                )
            )
        if script is None:
            raise RuntimeError(f"no script for {theme}")
        est = script.get("estimated_spoken_s")
        if est is None:
            est = lofi_cfg.estimated_spoken_duration_s(len(mono.split()))
        max_w, max_c = lofi_cfg.thematic_caption_limits()
        clause = _clause_flags(lines)
        drift = assess_theme_drift(mono, theme)
        line_stats = [
            {"scene": i, "words": len(t.split()), "chars": len(t), "text": t}
            for i, t in enumerate(lines, 1)
        ]
        over_lines = [
            s for s in line_stats
            if s["words"] > max_w or s["chars"] > max_c
        ]
        holds = list(script.get("narrative_holds") or [])
        arg_holds = [
            h for h in holds
            if any(m.lower() in str(h).lower() for m in _ARGUMENT_HOLD_MARKERS)
        ]
        vis_holds = [
            h for h in holds
            if any(m.lower() in str(h).lower() for m in _VISUAL_HOLD_MARKERS)
        ]
        rec = {
            "theme": theme,
            "pattern": script.get("rhetoric_pattern"),
            "writer": script.get("writer"),
            "thesis": script.get("thesis"),
            "anchor": script.get("anchor_object"),
            "source_monologue": mono,
            "captions": lines,
            "line_stats": line_stats,
            "holds": holds,
            "argument_holds": arg_holds,
            "visual_holds": vis_holds,
            "holds_episode": bool(script.get("holds_episode")),
            "word_count": len(mono.split()),
            "estimated_spoken_s": est,
            "target_duration_s": float(lofi_cfg.THEMATIC_DEFAULT_SCENES)
            * float(lofi_cfg.SCENE_DURATION_S),
            "over_cap_lines": over_lines,
            "theme_drift": drift,
            "clause_flags": clause,
            "corpus_echo_monologue": echo_mono,
            "corpus_echo_captions": echo_caps,
        }
        report.append(rec)
        print(
            f"writer={rec['writer']} beats={len(lines)} words={rec['word_count']} "
            f"est_vo={est}s / {rec['target_duration_s']:.0f}s "
            f"cap={max_w}w/{max_c}c over={len(over_lines)} "
            f"drift={drift.get('fails') or 'pass'}"
        )
        print(f"holds={holds or 'none'}")
        print(f"argument_holds={arg_holds or 'none'}")
        print(f"visual_holds={vis_holds or 'none'}")
        print(f"clause={clause or 'clean'} echo_mono={echo_mono or 'none'} echo_caps={echo_caps or 'none'}")
        print("LINES (derived monologue = join):")
        for s in line_stats:
            flag = ""
            if s["words"] > max_w or s["chars"] > max_c:
                flag = " OVER"
            print(f"  {s['scene']}. ({s['words']}w/{s['chars']}c{flag}) {s['text']}")
    out = rag._store_path("prose_writer_validate_corpus")
    Path(out).write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print("\n[validate] cost", summarize_cost_log(8))
    print(f"[validate] wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

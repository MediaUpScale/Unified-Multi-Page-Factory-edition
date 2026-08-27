# -*- coding: utf-8 -*-
"""
Portfolio architecture log — filtered writer for portfolio_showcase/data/global_timeline.json.

Migrated from docs/architecture/portfolio_log.py. This is NOT a pipeline
hook. Do not call from QA retries, regenerate_scene, or a routine
ECONOMIC_REEL_LOFI run. Those events are too noisy for a scrollytelling portfolio.

Allowed kinds (the only events that may write an entry):
  - module_close
  - root_cause_fix
  - architecture_decision
  - milestone   (e.g. Schnell-vs-dev comparison)

Anything else raises ValueError and does not touch the file.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

_TIMELINE = Path(__file__).resolve().parents[1] / "data" / "global_timeline.json"

PORTFOLIO_KINDS = frozenset(
    {
        "module_close",
        "root_cause_fix",
        "architecture_decision",
        "milestone",
    }
)

# Explicit reject list so a future caller cannot "just add a kind" for noise.
REJECTED_KINDS = frozenset(
    {
        "qa_attempt",
        "qa_pass",
        "qa_fail",
        "pipeline_run",
        "regen_call",
        "stills_pass",
        "critic_pass",
        "counter",
        "retry",
    }
)


def record_portfolio_entry(
    *,
    node: str,
    kind: str,
    what: str,
    why: str,
    files: list[str],
    media: str | None = None,
    needs_media: bool = False,
    entry_date: str | None = None,
) -> dict[str, Any]:
    """
    Append one portfolio-grade entry. Refuses routine QA / pipeline events.
    """
    kind_s = str(kind or "").strip().lower()
    if kind_s in REJECTED_KINDS or kind_s not in PORTFOLIO_KINDS:
        raise ValueError(
            f"portfolio log refused kind={kind!r}. "
            f"Allowed: {sorted(PORTFOLIO_KINDS)}. "
            "QA attempts, regen calls, and pipeline runs are not portfolio events."
        )
    what_s = " ".join((what or "").split())
    why_s = " ".join((why or "").split())
    if len(what_s) < 40 or len(why_s) < 20:
        raise ValueError("portfolio entry must state what changed and why, not a counter.")
    timeline = json.loads(_TIMELINE.read_text(encoding="utf-8"))
    nodes = timeline.setdefault("pipeline_nodes", {})
    bucket = nodes.setdefault(node, {"entries": []})
    entries = bucket.setdefault("entries", [])
    entry = {
        "date": entry_date or date.today().isoformat(),
        "kind": kind_s,
        "what": what_s,
        "why": why_s,
        "files": list(files),
        "media": media,
        "needs_media": bool(needs_media),
    }
    # Dedupe on (date, kind, first 80 chars of what) so close-out commits
    # can be re-run without duplicating the same decision.
    key = (entry["date"], entry["kind"], entry["what"][:80])
    for existing in entries:
        if (
            str(existing.get("date")) == key[0]
            and str(existing.get("kind") or "") == key[1]
            and str(existing.get("what") or "")[:80] == key[2]
        ):
            return existing
    entries.append(entry)
    _TIMELINE.write_text(
        json.dumps(timeline, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return entry

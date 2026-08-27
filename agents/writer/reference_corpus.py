# -*- coding: utf-8 -*-
"""Tone-and-register calibration texts shown to the freeform writer.

These exist so the writer can hear the voice. They are never a template: the
prompt block below says so explicitly, and ``reference_overlap_hit`` downstream
still rejects a draft that leans on their phrasing.
"""
from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

from core.economic_reel_lofi import config as lofi_cfg

_STORE_NAME = "writer_reference_corpus"
_CACHE: dict[str, Any] | None = None


def _path() -> Path:
    return lofi_cfg.STORE_DIR / f"{_STORE_NAME}.json"


def load_corpus(*, refresh: bool = False) -> dict[str, Any]:
    global _CACHE
    if _CACHE is not None and not refresh:
        return _CACHE
    path = _path()
    data: dict[str, Any] = {"entries": [], "sample_size": 3}
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                data = raw
            elif isinstance(raw, list):
                data = {"entries": raw, "sample_size": 3}
        except (OSError, json.JSONDecodeError) as exc:  # noqa: PERF203
            print(f"[LOFI writer] reference corpus unreadable ({exc}) — running without it")
    _CACHE = data
    return data


def entries() -> list[dict[str, Any]]:
    rows = load_corpus().get("entries")
    return [r for r in rows if isinstance(r, dict) and str(r.get("text") or "").strip()] if isinstance(rows, list) else []


def sample(n: int | None = None, *, seed: int | None = None) -> list[dict[str, Any]]:
    """Rotate which references the writer sees so drafts don't converge on one voice."""
    rows = entries()
    if not rows:
        return []
    want = int(n if n is not None else load_corpus().get("sample_size") or 3)
    want = max(1, min(want, len(rows)))
    rng = random.Random(seed)
    return rng.sample(rows, want)


def reference_block(n: int | None = None, *, seed: int | None = None) -> str:
    """Prompt section. Empty string when the corpus is empty — never a hard error."""
    rows = sample(n, seed=seed)
    if not rows:
        return ""
    blocks = []
    for row in rows:
        structure = str(row.get("structure") or "").strip()
        register = str(row.get("register") or "").strip()
        head = " / ".join(x for x in (structure, register) if x)
        text = " ".join(str(row.get("text") or "").split())
        blocks.append(f"[{head}]\n{text}" if head else text)
    body = "\n\n".join(blocks)
    return (
        "REFERENCE VOICE — read these for cadence, not content.\n"
        "They exist to show you the register: how long a thought is allowed to "
        "run, how a metaphor is carried rather than decorated, how directly the "
        "listener is addressed, and how a piece lands on something usable. Notice "
        "that each one is shaped differently. That is the point.\n"
        "Also notice the pacing: short, direct clauses in sequence — not a single "
        "30-word literary sentence. When you split a piece into beats, each beat "
        "should move at that speed.\n"
        "You must not reuse their phrasing, their images, their objects, or their "
        "structure. If a line of yours could be swapped into one of these without "
        "anyone noticing, delete it.\n\n"
        f"{body}\n"
    )

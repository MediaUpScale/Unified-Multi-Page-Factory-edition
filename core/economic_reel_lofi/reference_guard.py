# -*- coding: utf-8 -*-
"""Block writer copy of stored reference transcripts (wf1–4, mc1–3).

The transcript file is for the checker only — never inject it into the writer prompt.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from typing import Any

from core.economic_reel_lofi import config as lofi_cfg

_WORD_RE = re.compile(r"[a-z0-9]+")
_NGRAM = 5
_MIN_SLOGAN = 4
_MIN_LINE_TOKS = 6
_STOP = frozenset(
    {
        "the", "a", "an", "is", "am", "are", "was", "were", "be", "been",
        "to", "and", "or", "of", "in", "on", "at", "for", "with", "from",
        "that", "this", "they", "them", "their", "you", "your", "we", "our",
        "it", "its", "when", "then", "as", "if", "but", "i", "me", "my",
        "can", "could", "would", "will",
    }
)
_PUBLIC_REJECT = (
    "reference_copy: script overlaps a stored reference transcript "
    "(>80% word overlap or a copied n-gram). Write a fully original argument. "
    "Do not reuse or lightly paraphrase viral-reel openings."
)


def _tokens(text: str) -> list[str]:
    return _WORD_RE.findall((text or "").lower())


def _ngrams(toks: list[str], n: int) -> set[tuple[str, ...]]:
    if n < _MIN_SLOGAN or len(toks) < n:
        return set()
    return {tuple(toks[i : i + n]) for i in range(len(toks) - n + 1)}


def word_overlap_ratio(a: str, b: str) -> float:
    """Unique content-token overlap vs the shorter side."""
    ta = {t for t in _tokens(a) if t not in _STOP}
    tb = {t for t in _tokens(b) if t not in _STOP}
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / min(len(ta), len(tb))


@lru_cache(maxsize=1)
def load_banned_transcripts() -> tuple[dict[str, Any], ...]:
    path = lofi_cfg.DATA_DIR / "banned_reference_transcripts.json"
    if not path.is_file():
        return ()
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        return ()
    return tuple(r for r in raw if isinstance(r, dict))


def _ref_rows() -> list[tuple[str, list[str]]]:
    out: list[tuple[str, list[str]]] = []
    for row in load_banned_transcripts():
        rid = str(row.get("id") or "ref")
        lines = [str(x).strip() for x in (row.get("lines") or []) if str(x).strip()]
        if lines:
            out.append((rid, lines))
    return out


def _script_lines(script_text: str) -> list[str]:
    parts = re.split(r"[\n/]+", script_text or "")
    return [p.strip() for p in parts if p.strip()]


def banned_hook_text(text: str) -> bool:
    """True if a seed hook itself is a reference line or n-gram."""
    hit, _ = reference_overlap_hit(text)
    return hit


def reference_overlap_hit(
    script_text: str,
    *,
    threshold: float | None = None,
) -> tuple[bool, str]:
    """
    Return (hit, public_reason). Public reason never quotes the reference.

    Hit if:
      - a 5-gram (4-gram for short slogans) from a stored reference appears, or
      - a script line and a reference line of similar length share >= threshold
        unique-token overlap.
    """
    thresh = float(
        threshold
        if threshold is not None
        else getattr(lofi_cfg, "REFERENCE_OVERLAP_THRESHOLD", 0.80)
    )
    script = (script_text or "").strip()
    if not script:
        return False, ""
    stoks = _tokens(script)
    sgrams5 = _ngrams(stoks, _NGRAM)
    sgrams4 = _ngrams(stoks, _MIN_SLOGAN)
    script_lines = _script_lines(script)
    for rid, lines in _ref_rows():
        del rid
        for line in lines:
            lt = _tokens(line)
            if len(lt) >= _NGRAM and sgrams5 & _ngrams(lt, _NGRAM):
                return True, _PUBLIC_REJECT
            if len(lt) == _MIN_SLOGAN and sgrams4 & _ngrams(lt, _MIN_SLOGAN):
                return True, _PUBLIC_REJECT
        for sline in script_lines:
            st = _tokens(sline)
            if len(st) < _MIN_LINE_TOKS:
                continue
            for line in lines:
                rt = _tokens(line)
                if len(rt) < _MIN_LINE_TOKS:
                    continue
                longer, shorter = (st, rt) if len(st) >= len(rt) else (rt, st)
                if len(longer) / float(len(shorter)) > 2.0:
                    continue
                ratio = word_overlap_ratio(sline, line)
                content_n = min(
                    len({t for t in st if t not in _STOP}),
                    len({t for t in rt if t not in _STOP}),
                )
                if content_n >= 3 and ratio >= thresh:
                    return True, _PUBLIC_REJECT
    return False, ""

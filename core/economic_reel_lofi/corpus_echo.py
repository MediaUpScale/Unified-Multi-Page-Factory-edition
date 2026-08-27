# -*- coding: utf-8 -*-
"""4+ word sequence echo vs the real reference corpus."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from core.economic_reel_lofi import config as lofi_cfg

_WORD_RE = re.compile(r"[a-z0-9']+")
_MIN_N = 4


def _tokens(text: str) -> list[str]:
    return _WORD_RE.findall(str(text or "").lower())


def _ngrams(tokens: list[str], n: int) -> set[str]:
    if len(tokens) < n:
        return set()
    return {" ".join(tokens[i : i + n]) for i in range(len(tokens) - n + 1)}


def load_corpus_pieces(path: Path | None = None) -> list[dict[str, str]]:
    src = path or (lofi_cfg.DATA_DIR / "reference_corpus.json")
    if not src.is_file():
        return []
    raw = json.loads(src.read_text(encoding="utf-8"))
    pieces = raw.get("pieces") if isinstance(raw, dict) else raw
    out: list[dict[str, str]] = []
    for i, piece in enumerate(pieces or [], 1):
        if isinstance(piece, dict):
            pid = str(piece.get("id") or f"piece_{i}")
            body = str(piece.get("text") or " ".join(piece.get("lines") or []))
        else:
            pid = f"piece_{i}"
            body = str(piece)
        body = " ".join(body.split())
        if body:
            out.append({"id": pid, "text": body})
    return out


def echo_hits(text: str, *, min_n: int = _MIN_N) -> list[dict[str, str]]:
    """Return shared 4+ word sequences against the on-disk corpus."""
    hay = _tokens(text)
    if len(hay) < min_n:
        return []
    found: list[dict[str, str]] = []
    seen: set[str] = set()
    for piece in load_corpus_pieces():
        src = _tokens(piece["text"])
        for n in range(min_n, min(len(hay), len(src), 12) + 1):
            overlap = _ngrams(hay, n) & _ngrams(src, n)
            for gram in sorted(overlap):
                if gram in seen:
                    continue
                # Keep the longest hit: skip if a longer already contains this.
                if any(gram in longer for longer in seen):
                    continue
                drop = {s for s in seen if s in gram}
                seen -= drop
                seen.add(gram)
                found = [h for h in found if h["ngram"] not in drop]
                found.append(
                    {
                        "ngram": gram,
                        "n": str(n),
                        "corpus_id": piece["id"],
                    }
                )
    return found


def echo_report(text: str, *, label: str = "") -> dict[str, Any]:
    hits = echo_hits(text)
    return {
        "label": label,
        "ok": not hits,
        "hits": hits,
    }

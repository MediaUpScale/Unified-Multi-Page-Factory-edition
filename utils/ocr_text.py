# -*- coding: utf-8 -*-
"""Shared OCR text cleanup used by OCR, paraphrase, and quote render."""
from __future__ import annotations

_QUOTE_CHARS: frozenset[str] = frozenset("\"'“”„‟«»‹›‘’‚")


def strip_wrapping_quotes(text: str) -> str:
    """Remove quote marks that wrap the whole text, keep inner punctuation."""
    cleaned = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    while len(cleaned) >= 2 and cleaned[0] in _QUOTE_CHARS and cleaned[-1] in _QUOTE_CHARS:
        inner = cleaned[1:-1].strip()
        if not inner:
            break
        cleaned = inner
    return cleaned

# -*- coding: utf-8 -*-
"""
Semantic 3–5 s visual-clip chunking from ElevenLabs word timestamps.

Prefers sentence / clause boundaries. Never invents timings that are not
in the alignment stream.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_SENTENCE_END = re.compile(r"[.!?][\"')\]]*$")
_CLAUSE_END = re.compile(r"[,;:—–][\"')\]]*$")

MIN_CHUNK_S = 3.0
MAX_CHUNK_S = 5.0


@dataclass(frozen=True)
class AudioChunk:
    index: int
    start_s: float
    end_s: float
    text: str

    @property
    def duration_s(self) -> float:
        return max(0.0, float(self.end_s) - float(self.start_s))


def _is_boundary(token: str, *, sentence: bool) -> bool:
    tok = (token or "").strip()
    if not tok:
        return False
    return bool(_SENTENCE_END.search(tok) if sentence else _CLAUSE_END.search(tok))


def chunk_word_timings(
    word_timings: list[tuple[str, float, float]] | None,
    *,
    min_s: float = MIN_CHUNK_S,
    max_s: float = MAX_CHUNK_S,
) -> list[AudioChunk]:
    """
    Pack word alignments into 3–5 s windows at natural pauses.

    ``word_timings`` is ``[(word, start_s, end_s), ...]``.
    """
    words = [
        (str(w or "").strip(), float(s), float(e))
        for w, s, e in (word_timings or [])
        if str(w or "").strip() and float(e) > float(s)
    ]
    if not words:
        return []

    min_s = max(1.5, float(min_s))
    max_s = max(min_s + 0.4, float(max_s))

    chunks: list[AudioChunk] = []
    buf: list[tuple[str, float, float]] = []

    def _flush() -> None:
        if not buf:
            return
        text = " ".join(t[0] for t in buf).strip()
        start = float(buf[0][1])
        end = float(buf[-1][2])
        if text and end > start:
            chunks.append(
                AudioChunk(index=len(chunks), start_s=start, end_s=end, text=text)
            )
        buf.clear()

    for i, (tok, start, end) in enumerate(words):
        if not buf:
            buf.append((tok, start, end))
            continue
        window_end = end
        window_start = buf[0][1]
        dur = window_end - window_start
        tentative = buf + [(tok, start, end)]
        tentative_dur = end - buf[0][1]
        at_sentence = _is_boundary(tok, sentence=True)
        at_clause = _is_boundary(tok, sentence=False)
        last = i == len(words) - 1

        if tentative_dur <= max_s:
            buf.append((tok, start, end))
            if last:
                _flush()
                break
            if at_sentence and tentative_dur >= min_s:
                _flush()
            elif at_clause and tentative_dur >= (min_s + 0.6):
                _flush()
            continue

        # Would exceed max — close current if it already meets min, else keep going.
        if dur >= min_s:
            _flush()
            buf.append((tok, start, end))
        else:
            buf.append((tok, start, end))
        if last:
            _flush()

    if buf:
        _flush()

    # Merge a trailing micro-chunk into the previous window.
    if len(chunks) >= 2 and chunks[-1].duration_s < (min_s * 0.55):
        prev, tail = chunks[-2], chunks[-1]
        merged = AudioChunk(
            index=prev.index,
            start_s=prev.start_s,
            end_s=tail.end_s,
            text=f"{prev.text} {tail.text}".strip(),
        )
        chunks = chunks[:-2] + [merged]
        chunks = [
            AudioChunk(index=i, start_s=c.start_s, end_s=c.end_s, text=c.text)
            for i, c in enumerate(chunks)
        ]

    return chunks

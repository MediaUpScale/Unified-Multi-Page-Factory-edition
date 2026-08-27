# -*- coding: utf-8 -*-
"""Deterministic spoken-duration budget for freeform drafts.

Word-count vs declared beat length is checked in code, never by an LLM.
Over-budget beats are sent back to the writer as a targeted line rewrite
(same meaning, tighter phrasing) before the judge runs and before images.
"""
from __future__ import annotations

import json
import re
from typing import Any

from agents.writer.freeform_writer import ScriptDraft, _extract_json, _writer_provider
from agents.writer.writer_brief import WriterBrief
from core.economic_reel_lofi import config as lofi_cfg


def count_words(text: str) -> int:
    return len(str(text or "").split())


def _term_in_text(text: str, term: str) -> bool:
    t = str(term or "").strip().lower()
    if not t:
        return True
    blob = str(text or "").lower()
    parts = [p for p in re.split(r"\s+", t) if p]
    if not parts:
        return True
    for part in parts:
        stem = re.escape(part.rstrip("s"))
        if not re.search(rf"\b{stem}s?\b", blob):
            return False
    return True


def missing_keep_terms(text: str, terms: list[str] | tuple[str, ...] | None) -> list[str]:
    return [t for t in (terms or ()) if t and not _term_in_text(text, t)]


def _duration_s(brief: WriterBrief | None) -> float:
    meta = (brief.meta if brief is not None else {}) or {}
    raw = meta.get("duration_s")
    if raw is not None:
        return float(raw)
    return float(lofi_cfg.DEFAULT_DURATION_S)


def _beat_s(brief: WriterBrief | None) -> float:
    meta = (brief.meta if brief is not None else {}) or {}
    raw = meta.get("beat_duration_s") or meta.get("scene_duration_s")
    if raw is not None:
        return float(raw)
    return float(lofi_cfg.beat_duration_s())


def assess_lines(
    lines: list[str],
    *,
    duration_s: float | None = None,
    beat_s: float | None = None,
) -> dict[str, Any]:
    """Per-beat word counts vs budget. No LLM."""
    dur = float(duration_s if duration_s is not None else lofi_cfg.DEFAULT_DURATION_S)
    slot = float(beat_s if beat_s is not None else lofi_cfg.beat_duration_s())
    budget = lofi_cfg.beat_word_budget(slot)
    ceiling = lofi_cfg.beat_word_ceiling(slot)
    max_beats = lofi_cfg.max_beats_for_duration(dur)
    beats: list[dict[str, Any]] = []
    over: list[int] = []
    for i, line in enumerate(lines):
        n = count_words(line)
        rec = {
            "index": i,
            "scene": i + 1,
            "words": n,
            "budget": budget,
            "ceiling": ceiling,
            "duration_s": slot,
            "ok": n <= ceiling,
            "text": line,
        }
        beats.append(rec)
        if n > ceiling:
            over.append(i)
    n_lines = len(lines)
    needs_longer = n_lines > max_beats
    needed_s = round(n_lines * slot, 3) if n_lines else 0.0
    ok = (not over) and (not needs_longer) and n_lines > 0
    reason_parts: list[str] = []
    if needs_longer:
        reason_parts.append(
            f"{n_lines} beats × {slot:.1f}s = {needed_s:.1f}s, but duration_requested_s "
            f"is {dur:.1f}s (max {max_beats} beats). Request a longer format up front "
            f"(--duration {int(needed_s)} / scene_count={n_lines}); do not overrun silently."
        )
    for rec in beats:
        if rec["ok"]:
            continue
        reason_parts.append(
            f"beat {rec['scene']} has {rec['words']} words (max {ceiling} for "
            f"{slot:.1f}s at {lofi_cfg.narration_wpm():.0f} wpm): {rec['text']!r}"
        )
    return {
        "ok": ok,
        "beats": beats,
        "over_indices": over,
        "needs_longer_duration": needs_longer,
        "n_lines": n_lines,
        "max_beats": max_beats,
        "duration_s": dur,
        "beat_s": slot,
        "budget": budget,
        "ceiling": ceiling,
        "needed_s": needed_s,
        "reason": "; ".join(reason_parts),
    }


def assess_draft(draft: ScriptDraft) -> dict[str, Any]:
    return assess_lines(
        list(draft.lines),
        duration_s=_duration_s(draft.brief),
        beat_s=_beat_s(draft.brief),
    )


_REWRITE_SYSTEM = """You rewrite individual spoken beats so each one fits a hard \
word ceiling. You are not writing a new piece. Keep the same situation, meaning, \
and throughline. Short, direct clauses — not one long literary sentence.

Output valid JSON and nothing else."""


def _rewrite_prompt(
    draft: ScriptDraft,
    over_indices: list[int],
    *,
    ceiling: int,
    beat_s: float,
    must_keep: dict[int, list[str]] | None = None,
) -> str:
    brief = draft.brief
    theme = (brief.theme if brief else "") or ""
    sub = (brief.subtheme if brief else "") or ""
    numbered = "\n".join(
        f"{i + 1}. ({count_words(line)}w) {line}"
        for i, line in enumerate(draft.lines)
    )
    keep = must_keep or {}
    target_bits: list[str] = []
    for i in over_indices:
        if not (0 <= i < len(draft.lines)):
            continue
        nouns = [t for t in (keep.get(i) or []) if t]
        noun_bit = f" MUST keep these spoken words: {', '.join(nouns)}." if nouns else ""
        target_bits.append(
            f"- beat {i + 1} ({count_words(draft.lines[i])}w → max {ceiling}w): "
            f"{draft.lines[i]}{noun_bit}"
        )
    targets = "\n".join(target_bits)
    return (
        f"SUBJECT: {theme.replace('_', ' ')}\n"
        f"NARROWER ANGLE: {sub.replace('_', ' ')}\n"
        f"Each beat is spoken in {beat_s:.1f}s. HARD MAX {ceiling} words per beat.\n\n"
        "The current piece, numbered (for continuity only — do not rewrite "
        "beats that are not listed below):\n"
        f"{numbered}\n\n"
        "Rewrite ONLY these over-budget beats. Same meaning, tighter phrasing. "
        "Do not add beats. Do not drop beats. Do not invent a new metaphor, "
        "speaker, relationship, or imagery.\n"
        f"{targets}\n\n"
        'Return JSON: {"lines": {"<1-based beat number>": "<rewritten beat>"}}'
    )


def rewrite_overlong_lines(
    draft: ScriptDraft,
    over_indices: list[int],
    *,
    ceiling: int,
    beat_s: float,
    provider: str | None = None,
    must_keep: dict[int, list[str]] | None = None,
) -> ScriptDraft:
    """One writer call covering only the over-budget beats. Claude, kind=writer."""
    from agents.mcp.text_model import complete_script, estimate_tokens

    if not over_indices:
        return draft
    prompt = _rewrite_prompt(
        draft, over_indices, ceiling=ceiling, beat_s=beat_s, must_keep=must_keep
    )
    name = (provider or _writer_provider()).strip().lower()
    print(
        f"[LOFI spoken-budget] rewrite beats={[i + 1 for i in over_indices]} "
        f"ceiling={ceiling} provider={name} "
        f"prompt_tokens_est={estimate_tokens(prompt) + estimate_tokens(_REWRITE_SYSTEM)}"
    )
    result = complete_script(
        prompt, system=_REWRITE_SYSTEM, provider=name or None, kind="writer"
    )
    data = _extract_json(result.text)
    mapping = data.get("lines") if isinstance(data, dict) else None
    if not isinstance(mapping, dict):
        raise ValueError("line rewrite returned no lines object")
    new_lines = list(draft.lines)
    for key, val in mapping.items():
        try:
            idx = int(str(key).strip()) - 1
        except ValueError:
            continue
        if idx not in over_indices or idx < 0 or idx >= len(new_lines):
            continue
        text = " ".join(str(val or "").split())
        text = re.sub(r"^\s*\d+[.)]\s*", "", text)
        if not text:
            continue
        missing = missing_keep_terms(text, (must_keep or {}).get(idx) or [])
        if missing:
            print(
                f"[LOFI spoken-budget] reject beat {idx + 1} rewrite — "
                f"dropped {missing}: {text!r}"
            )
            continue
        new_lines[idx] = text
    draft.lines = new_lines
    draft.raw = result.text
    return draft


def enforce_spoken_budget(
    draft: ScriptDraft,
    *,
    writer_provider: str | None = None,
    must_keep: dict[int, list[str]] | None = None,
) -> tuple[ScriptDraft, dict[str, Any]]:
    """Rewrite over-budget lines in place. Does not call the judge."""
    report = assess_draft(draft)
    if report["needs_longer_duration"]:
        print(f"[LOFI spoken-budget] FAIL longer-format needed: {report['reason']}")
        return draft, report
    if report["ok"]:
        print(
            f"[LOFI spoken-budget] PASS lines={report['n_lines']} "
            f"ceiling={report['ceiling']}w/{report['beat_s']:.1f}s"
        )
        return draft, report

    ceiling = int(report["ceiling"])
    beat_s = float(report["beat_s"])
    keep = must_keep or {}
    passes = max(1, int(getattr(lofi_cfg, "LINE_REWRITE_MAX_PASSES", 2)))
    for n in range(1, passes + 1):
        over = list(report["over_indices"])
        print(
            f"[LOFI spoken-budget] pass={n}/{passes} over={[i + 1 for i in over]}"
        )
        try:
            draft = rewrite_overlong_lines(
                draft,
                over,
                ceiling=ceiling,
                beat_s=beat_s,
                provider=writer_provider,
                must_keep=keep,
            )
        except Exception as exc:  # noqa: BLE001
            report = assess_draft(draft)
            report["ok"] = False
            report["reason"] = f"line rewrite failed: {exc}; {report.get('reason') or ''}"
            print(f"[LOFI spoken-budget] rewrite error: {exc}")
            return draft, report
        report = assess_draft(draft)
        if not report["ok"]:
            for i in list(report["over_indices"]):
                try:
                    print(f"[LOFI spoken-budget] single-line rewrite beat={i + 1}")
                    draft.lines[i] = rewrite_single_line(
                        draft.lines[i],
                        ceiling=ceiling,
                        theme=(draft.brief.theme if draft.brief else "") or "",
                        subtheme=(draft.brief.subtheme if draft.brief else "") or "",
                        neighbor_before=draft.lines[i - 1] if i > 0 else "",
                        neighbor_after=(
                            draft.lines[i + 1] if i + 1 < len(draft.lines) else ""
                        ),
                        provider=writer_provider,
                        must_keep=keep.get(i) or [],
                    )
                except Exception as exc:  # noqa: BLE001
                    print(
                        f"[LOFI spoken-budget] beat {i + 1} single-line rewrite error: {exc}"
                    )
            report = assess_draft(draft)
        if report["ok"]:
            print(f"[LOFI spoken-budget] PASS after rewrite pass {n}")
            return draft, report
    print(f"[LOFI spoken-budget] FAIL after {passes} rewrites: {report['reason']}")
    return draft, report


def rewrite_single_line(
    text: str,
    *,
    ceiling: int,
    theme: str = "",
    subtheme: str = "",
    neighbor_before: str = "",
    neighbor_after: str = "",
    provider: str | None = None,
    must_keep: list[str] | tuple[str, ...] | None = None,
) -> str:
    """Tighten one already-rendered beat that overran TTS. Writer-only Claude."""
    from agents.mcp.text_model import complete_script

    prompt = (
        f"SUBJECT: {theme.replace('_', ' ')}\n"
        f"NARROWER ANGLE: {subtheme.replace('_', ' ')}\n"
        f"HARD MAX {ceiling} words. Same meaning, tighter phrasing. "
        "Do not invent a new metaphor, speaker, relationship, or imagery.\n"
    )
    keep = [t for t in (must_keep or []) if t]
    if keep:
        prompt += f"MUST keep these spoken words: {', '.join(keep)}.\n"
    if neighbor_before:
        prompt += f"Previous beat (do not rewrite): {neighbor_before}\n"
    prompt += f"This beat is over budget: {text}\n"
    if neighbor_after:
        prompt += f"Next beat (do not rewrite): {neighbor_after}\n"
    prompt += 'Return JSON: {"line": "<rewritten beat>"}'
    name = (provider or _writer_provider()).strip().lower()
    result = complete_script(
        prompt, system=_REWRITE_SYSTEM, provider=name or None, kind="writer"
    )
    data = _extract_json(result.text)
    line = " ".join(str((data or {}).get("line") or "").split())
    if not line:
        raise ValueError("single-line rewrite returned empty")
    missing = missing_keep_terms(line, keep)
    if missing:
        raise ValueError(f"single-line rewrite dropped required nouns {missing}: {line!r}")
    return line

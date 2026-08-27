# -*- coding: utf-8 -*-
"""Script brain — brief in, judged script out.

    WriterBrief  ->  freeform writer  ->  five-criterion judge  ->  approved draft

A rejected draft is not patched. The judge's reasons go back to the writer and it
starts over, because the failures worth catching are structural to the idea.

Everything downstream of this module (atmosphere assignment, per-beat camera and
object licensing, Flux prompt construction, QA, ship gates, assemble) is
unchanged. The only difference is that it now receives a script that had to earn
its way here.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from agents.writer.freeform_writer import ScriptDraft, write_draft
from agents.writer.judge_gate import JudgeVerdict, judge_draft
from agents.writer.writer_brief import WriterBrief

DEFAULT_MAX_ATTEMPTS = 3


@dataclass
class Attempt:
    draft: ScriptDraft | None
    verdict: JudgeVerdict | None
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "draft": self.draft.to_dict() if self.draft else None,
            "lines": list(self.draft.lines) if self.draft else [],
            "judge": self.verdict.to_dict() if self.verdict else None,
            "error": self.error,
        }


@dataclass
class BrainResult:
    brief: WriterBrief
    ok: bool
    draft: ScriptDraft | None = None
    verdict: JudgeVerdict | None = None
    attempts: list[Attempt] = field(default_factory=list)
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def reason(self) -> str:
        if self.ok:
            return ""
        for attempt in reversed(self.attempts):
            if attempt.verdict is not None:
                return attempt.verdict.feedback()
            if attempt.error:
                return attempt.error
        return "writer produced no usable draft"

    def to_dict(self) -> dict[str, Any]:
        return {
            "brief": {
                "mode": self.brief.mode,
                "label": self.brief.label,
                "module": self.brief.module,
                "theme": self.brief.theme,
                "subtheme": self.brief.subtheme,
                "seed_quote": self.brief.seed_quote,
                "seed_attribution": self.brief.seed_attribution,
            },
            "ok": self.ok,
            "approved": self.draft.to_dict() if self.draft else None,
            "approved_lines": list(self.draft.lines) if self.draft else [],
            "judge": self.verdict.to_dict() if self.verdict else None,
            "attempt_count": len(self.attempts),
            "attempts": [a.to_dict() for a in self.attempts],
            "diagnostics": self.diagnostics,
            "reason": self.reason(),
        }


def _max_attempts() -> int:
    raw = (os.getenv("LOFI_WRITER_MAX_ATTEMPTS") or "").strip()
    try:
        return max(1, int(raw)) if raw else DEFAULT_MAX_ATTEMPTS
    except ValueError:
        return DEFAULT_MAX_ATTEMPTS


def story_diagnostics(draft: ScriptDraft) -> dict[str, Any]:
    """Legacy regex spine/stakes/close scoring, kept as a note and nothing more.

    This no longer decides anything. It is recorded because the signal is
    occasionally interesting when a judged-good script still renders badly.
    """
    try:
        from agents.writer.script_agent import assess_story_quality

        report = assess_story_quality(
            [{"text": line} for line in draft.lines],
            theme=(draft.brief.theme if draft.brief else ""),
        )
    except Exception as exc:  # noqa: BLE001 — diagnostics must never break a run
        return {"available": False, "error": str(exc)}
    return {
        "available": True,
        "blocking": False,
        "note": "regex spine/stakes/close scoring — diagnostic only, does not gate",
        "report": report,
    }


def compose(
    brief: WriterBrief,
    *,
    max_attempts: int | None = None,
    writer_provider: str | None = None,
    judge_provider: str | None = None,
) -> BrainResult:
    """Write and judge until a draft clears all five criteria or attempts run out."""
    limit = int(max_attempts if max_attempts is not None else _max_attempts())
    attempts: list[Attempt] = []
    current = brief

    for n in range(1, limit + 1):
        try:
            draft = write_draft(
                current,
                attempt=n,
                provider=writer_provider,
                reference_seed=n,
            )
        except Exception as exc:  # noqa: BLE001 — a bad response is a failed attempt
            print(f"[LOFI brain] attempt {n} writer error: {exc}")
            attempts.append(Attempt(draft=None, verdict=None, error=f"writer: {exc}"))
            continue

        try:
            verdict = judge_draft(draft, provider=judge_provider)
        except Exception as exc:  # noqa: BLE001
            print(f"[LOFI brain] attempt {n} judge error: {exc}")
            attempts.append(Attempt(draft=draft, verdict=None, error=f"judge: {exc}"))
            continue

        attempts.append(Attempt(draft=draft, verdict=verdict))
        if verdict.ok:
            print(f"[LOFI brain] APPROVED on attempt {n} — {verdict.summary()}")
            return BrainResult(
                brief=brief,
                ok=True,
                draft=draft,
                verdict=verdict,
                attempts=attempts,
                diagnostics=story_diagnostics(draft),
            )
        current = brief.with_revision(verdict.feedback())

    print(f"[LOFI brain] no draft cleared the gate for {brief.label} after {limit} attempts")
    last = next((a.draft for a in reversed(attempts) if a.draft), None)
    return BrainResult(
        brief=brief,
        ok=False,
        attempts=attempts,
        diagnostics=story_diagnostics(last) if last else {},
    )


def draft_to_script(
    draft: ScriptDraft,
    *,
    hook_type: str = "bold_claim",
) -> dict[str, Any]:
    """Shape an approved draft into the script dict the visual stage already expects.

    One scene per written line, so a nine-line piece gets nine stills however
    long those lines run. ``caption_beats`` carries the on-screen captions that
    cycle over the held still.

    Only the text layer is filled in here. Setting, key object, subject and
    expression are still assigned by the existing atmosphere-first visual stage,
    which is what keeps meaning-based anchoring, composition variety and object
    licensing working exactly as they do today.
    """
    from core.economic_reel_lofi import config as lofi_cfg
    from core.economic_reel_lofi.visual_identity import act_for_index

    brief = draft.brief
    units = draft.image_units()
    lines = [
        {
            "scene": i + 1,
            "text": written,
            "beat_text": written,
            "caption_beats": captions,
            "arc_position": act_for_index(i, len(units)),
        }
        for i, (written, captions) in enumerate(units)
    ]
    script: dict[str, Any] = {
        "theme": (brief.theme if brief else "") or "",
        "subtheme": (brief.subtheme if brief else "") or "",
        "module": (brief.module if brief else "relationship"),
        "hook_type": hook_type,
        "arc_template": lofi_cfg.THEMATIC_ARC_ID,
        "monologue": " ".join(draft.lines),
        "lines": lines,
        "writer": "freeform_v1",
        "writer_structure": draft.structure,
        "human_situation": draft.human_situation,
        "closing_tool": draft.closing_tool,
    }
    if brief is not None and brief.mode == "quote":
        script["seed_quote"] = brief.seed_quote
        script["seed_attribution"] = brief.seed_attribution
    return script

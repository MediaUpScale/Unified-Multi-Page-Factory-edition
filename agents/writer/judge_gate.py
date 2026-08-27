# -*- coding: utf-8 -*-
"""Five-criterion LLM judge — the only quality gate on the writing itself.

Replaces the regex spine/stakes/close scoring as the pass/fail decision. Every
criterion must clear its own bar with a written justification. Scores are never
averaged: one weak criterion fails the draft no matter how strong the rest are,
and emotional connection carries a higher bar than the others.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any

from agents.writer.freeform_writer import ScriptDraft

# criterion -> (default bar out of 10, what it means)
CRITERIA: dict[str, tuple[int, str]] = {
    "emotional_connection": (
        8,
        "Does this make a real person feel something SPECIFIC — the particular "
        "ache of this particular situation — rather than generic sadness or "
        "generic warmth? This is the top-priority criterion and carries the "
        "highest bar. If you can only describe the feeling with a category word "
        "like 'sad' or 'moving', it has not cleared.",
    ),
    "originality": (
        7,
        "Is this free of AI-cliche patterns? Specifically: 'Not X. Not Y. Just "
        "Z.' tricolons, 'In a world where...', 'And that's when I realised...', "
        "dictionary-definition openings, generic aphorism endings, stacked "
        "abstract nouns. Also: does it have its own skeleton, or is it a "
        "variation on the same shape every one of these pieces uses? Name the "
        "shape you see.",
    ),
    "coherence": (
        7,
        "Does every line connect to the one before and the one after, building a "
        "single throughline start to finish? Test it: can any line be deleted "
        "without loss, or could two lines be swapped without anyone noticing? "
        "Either means the piece is a list of pretty but unrelated observations, "
        "and that fails. Imagery swapped in for its own sake fails.",
    ),
    "clarity": (
        7,
        "Would a first-time listener, hearing this once with no rewind, get it "
        "without effort? Metaphor, philosophy and reflective register are all "
        "fine. Cryptic is not. Poetic does not mean obscure. If a line has to be "
        "parsed, it fails.",
    ),
    "useful_close": (
        7,
        "Does the ending give the listener an emotional tool or a life lesson "
        "they can actually use, landed clearly? A pretty final line that resolves "
        "nothing fails. A proverb that would fit any piece on any theme fails. It "
        "has to be usable and it has to be specific to this piece.",
    ),
}

# Non-blocking evidence handed to the judge. These are not gates — the judge
# decides what they mean, or ignores them.
_CLICHE_ANYWHERE: tuple[tuple[str, str], ...] = (
    (r"\bin a world where\b", "'In a world where…' phrasing"),
    (r"\band that['’]s when (?:i|she|he|you) (?:realised|realized|knew)\b", "'And that's when I realised' turn"),
    (
        r"\b(?:it|that|this)\s+(?:isn['’]t|is not|wasn['’]t|was not)\b[^.?!]{1,40}\bit(?:['’]s| is| was)\b",
        "'It isn't X, it's Y' reversal template",
    ),
    (
        r"\bnot\b[^.?!]{1,40}[.?!]\s*not\b[^.?!]{1,40}[.?!]\s*just\b",
        "'Not X. Not Y. Just Z.' tricolon",
    ),
)
# Only meaningful at the very start of the piece.
_CLICHE_OPENING: tuple[tuple[str, str], ...] = (
    (r"^\s*\w+\s+is\s+(?:not|never)\b", "dictionary-definition opening"),
    (r"^\s*(?:there (?:is|are)|we all|everyone)\b", "generic universal opening"),
)
# Only meaningful on the last line, where a close has to be specific and usable.
_CLICHE_CLOSING: tuple[tuple[str, str], ...] = (
    (
        r"^\s*\w+\s+(?:is|isn['’]t|is not)\s+(?:a|an|the|not)\b[^.?!]*\bit(?:['’]s| is)\b",
        "closing on a definition-reversal aphorism",
    ),
    (r"\bthat['’]s (?:what|how) \w+ (?:is|was|means)\b", "closing on a generic 'that's what X is'"),
)


def _bar(name: str) -> int:
    default = CRITERIA[name][0]
    raw = os.getenv(f"LOFI_JUDGE_BAR_{name.upper()}", "").strip()
    try:
        return max(1, min(10, int(raw))) if raw else default
    except ValueError:
        return default


@dataclass
class CriterionScore:
    name: str
    score: int
    bar: int
    justification: str

    @property
    def passed(self) -> bool:
        return self.score >= self.bar

    def to_dict(self) -> dict[str, Any]:
        return {
            "criterion": self.name,
            "score": self.score,
            "bar": self.bar,
            "pass": self.passed,
            "justification": self.justification,
        }


@dataclass
class JudgeVerdict:
    scores: list[CriterionScore]
    revision_note: str = ""
    provider: str = ""
    cliche_hits: list[str] = field(default_factory=list)
    raw: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.scores) and all(s.passed for s in self.scores)

    def failures(self) -> list[CriterionScore]:
        return [s for s in self.scores if not s.passed]

    def feedback(self) -> str:
        """What the writer is told when it has to start over."""
        if self.ok:
            return ""
        lines = [
            f"- {s.name} scored {s.score}/10 against a bar of {s.bar}: {s.justification}"
            for s in self.failures()
        ]
        note = f"\n{self.revision_note}" if self.revision_note else ""
        return "\n".join(lines) + note

    def summary(self) -> str:
        return " ".join(
            f"{s.name}={s.score}/{s.bar}{'' if s.passed else ' FAIL'}" for s in self.scores
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "pass": self.ok,
            "provider": self.provider,
            "scores": [s.to_dict() for s in self.scores],
            "failed_criteria": [s.name for s in self.failures()],
            "revision_note": self.revision_note,
            "cliche_scan": self.cliche_hits,
        }


def scan_cliches(lines: list[str] | str) -> list[str]:
    """Diagnostic only. Reported to the judge as evidence, never a gate."""
    rows = [
        line.strip()
        for line in (lines.split("\n") if isinstance(lines, str) else list(lines))
        if str(line).strip()
    ]
    if not rows:
        return []
    flags = re.IGNORECASE
    hits: list[str] = []
    blob = " ".join(rows)
    for pattern, label in _CLICHE_ANYWHERE:
        if re.search(pattern, blob, flags):
            hits.append(label)
    for pattern, label in _CLICHE_OPENING:
        if re.search(pattern, rows[0], flags):
            hits.append(label)
    for pattern, label in _CLICHE_CLOSING:
        if re.search(pattern, rows[-1], flags):
            hits.append(label)
    return hits


_SYSTEM = """You are a hard, specific editor. You are not the writer and you \
gain nothing by approving this piece.

You judge five criteria independently. Never average them and never let a strong \
criterion cover a weak one — each is scored on its own evidence. Emotional \
connection carries the highest bar because a piece that is well-built and makes \
nobody feel anything is a failure.

Score 1-10 and justify in one or two sentences of concrete observation. Quote the \
line you are talking about. "Feels generic" is not a justification; "line 4 could \
be about any breakup, nothing in it belongs to this person" is.

Calibration, so scores mean something: 5 is competent and forgettable. 7 is good \
— it does the job and a stranger would not wince. 8 is genuinely strong and \
specific. 9-10 is rare and you should be able to say exactly what earns it. Being \
harsh is cheap and being generous is worse; be accurate. Most first drafts land \
between 5 and 7, and saying so is the useful answer.

Then write one revision note: the single most important thing that would have to \
change, aimed at a writer starting over rather than patching. Return valid JSON \
only."""


def _judge_provider() -> str:
    explicit = (os.getenv("LOFI_JUDGE_MODEL") or "").strip().lower()
    if explicit == "claude":
        print("[LOFI judge] Claude blocked — using Gemini")
        return "gemini"
    if explicit:
        return explicit
    return "gemini"


def _build_prompt(draft: ScriptDraft, cliche_hits: list[str]) -> str:
    criteria_block = "\n\n".join(
        f"{name} (bar {_bar(name)}/10)\n  {desc}" for name, (_, desc) in CRITERIA.items()
    )
    brief = draft.brief
    assignment = ""
    if brief is not None:
        if brief.mode == "quote":
            assignment = (
                f"The writer was given this seed idea to develop (not to quote): "
                f"\u201c{brief.seed_quote}\u201d\n"
            )
        elif brief.theme:
            sub = f" / {brief.subtheme}" if brief.subtheme else ""
            assignment = f"The writer was given the subject: {brief.theme}{sub}\n"
    evidence = ""
    if cliche_hits:
        joined = ", ".join(cliche_hits)
        evidence = (
            f"\nAutomated scan flagged these known AI-cliche patterns: {joined}. "
            "Verify against the text yourself — the scan is often wrong, and a "
            "flagged pattern used deliberately as a device can still be good "
            "writing. Weigh it under originality only if you agree it is there.\n"
        )
    schema = ",\n".join(
        f'    "{name}": {{"score": <1-10>, "justification": "<one or two sentences, quote the line>"}}'
        for name in CRITERIA
    )
    return f"""{assignment}The writer chose its own form and length. Do not judge it \
against a line count, a cadence, or any expected structure — judge only what is here.

PIECE:
{draft.numbered()}

The writer states the closing is meant to give the listener: \
{draft.closing_tool or "(not stated)"}
{evidence}
CRITERIA:

{criteria_block}

Return exactly this JSON, nothing else:
{{
  "criteria": {{
{schema}
  }},
  "revision_note": "<the single most important change, written to someone starting over>"
}}"""


def _parse(text: str) -> tuple[dict[str, Any], str]:
    raw = str(text or "").strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw)
    if fence:
        raw = fence.group(1).strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("judge response contained no JSON object")
    data = json.loads(raw[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("judge response JSON was not an object")
    criteria = data.get("criteria")
    if not isinstance(criteria, dict):
        raise ValueError("judge response had no criteria object")
    return criteria, str(data.get("revision_note") or "").strip()


def judge_draft(draft: ScriptDraft, *, provider: str | None = None) -> JudgeVerdict:
    """Score one draft. Raises on a malformed judge response so it can be retried."""
    from agents.mcp.text_model import complete_script

    cliche_hits = scan_cliches(draft.lines)
    name = (provider or _judge_provider()).strip().lower()
    print(f"[LOFI judge] scoring {draft.brief.label if draft.brief else 'draft'} provider={name}")
    result = complete_script(
        _build_prompt(draft, cliche_hits),
        system=_SYSTEM,
        provider=name or None,
        kind="judge",
    )
    criteria, revision_note = _parse(result.text)

    scores: list[CriterionScore] = []
    missing: list[str] = []
    for key in CRITERIA:
        row = criteria.get(key)
        if not isinstance(row, dict) or row.get("score") is None:
            missing.append(key)
            continue
        try:
            score = int(round(float(row.get("score"))))
        except (TypeError, ValueError):
            missing.append(key)
            continue
        scores.append(
            CriterionScore(
                name=key,
                score=max(1, min(10, score)),
                bar=_bar(key),
                justification=" ".join(str(row.get("justification") or "").split()),
            )
        )
    if missing:
        raise ValueError(f"judge did not score: {', '.join(missing)}")

    verdict = JudgeVerdict(
        scores=scores,
        revision_note=revision_note,
        provider=getattr(result, "provider", name) or name,
        cliche_hits=cliche_hits,
        raw=result.text,
    )
    print(f"[LOFI judge] {'PASS' if verdict.ok else 'REJECT'} {verdict.summary()}")
    return verdict

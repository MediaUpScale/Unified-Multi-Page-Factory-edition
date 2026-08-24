# -*- coding: utf-8 -*-
"""Per-video Claude 9-line composer — Batch API, cached instruction, token cap."""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

from core_engine.economic_reel_lofi import config as lofi_cfg
from core_engine.economic_reel_lofi.claude_polish import (
    POLISH_MODEL_DEFAULT,
    _POLL_MAX_S,
    _POLL_S,
    _client_and_model,
    _resolve_polish_model,
    _result_error_text,
    _usage_rec,
    append_cost_log,
)
import time

_LOG = logging.getLogger(__name__)

def compose_max_tokens() -> int:
    # 9 short caption strings + JSON wrapper. Do not size this from a
    # monologue word-budget — that target is retired.
    return 360


def compose_instruction() -> str:
    max_w, max_c = lofi_cfg.thematic_caption_limits()
    n = int(lofi_cfg.THEMATIC_DEFAULT_SCENES)
    return (
        f"You write exactly {n} spoken caption lines for a short vertical reel. "
        f"Each line is one short breath: HARD MAX {max_w} words and {max_c} characters. "
        "These 9 lines are ONE continuous spoken argument delivered as 9 short "
        "breaths — not 9 independent image-captions and not a collage of scenes. "
        "Hold one fixed referent across all 9 lines: either 'you' or 'I'. Do not "
        "write a repeating indefinite 'someone' with no 'you' and no 'I'. "
        "Use at least two load-bearing logical connectives across the nine lines "
        "(because, so, but, although, when/then). A connective must change what "
        "the next line is allowed to mean. Each line must follow from the previous "
        "one by consequence or contrast, not by stacking a new picture. Do not "
        "start a new object or room each line. The last line is a specific "
        "realization or turn about the same person and the same object — not a "
        "general inspirational definition ('Hope is the light…', 'X is what Y was'). "
        "If the last line could caption any video on this theme, rewrite it. "
        "Stay on the assigned theme — do not name a different emotion or concept "
        "from the pattern's original domain. "
        'Return STRICT JSON only: {"lines":["...","...","...","...","...","...","...","...","..."]}.'
    )

# Prompt cache requires >=1024 tokens. House voice only — no third-party copy,
# no full pattern library, no gate checklist.
_COMPOSE_VOICE_PAD = """
House voice for these 9 lines (style and causality only):
Speak as if the listener is already in the room. Line follows line.
Pick one person and keep them: you, or I — not a floating someone.
A connective must do work: because names a reason, although names a friction,
so names a consequence, yet names a refusal of the last claim. If you can
delete the connective and the argument is unchanged, rewrite it. At least
two of those connectives must appear in the nine lines.
Prefer the locked anchor object. Prefer verbs a body can do. Prefer one
through-line from the first line to the last. Do not start a new plot
in the middle. Do not introduce a second thesis. Do not name a real author,
philosopher, or book. Do not attribute a line to anyone. Do not add a title,
hashtag, or preface.

Repeat so the cached prefix stays long enough:
nine breaths, one argument, causal chain intact, anchor visible, each line
under the per-line word and character cap. Your job is the spoken piece a
tired person would actually say, start to finish, already cut into nine
breaths. Do not write a free paragraph for someone else to chop.

More house voice, still not a production-gate checklist:
If the thesis is a definition, prove it with evidence across the nine lines.
If the thesis is a refusal, keep refusing the same thing, not a new thing.
If the thesis is a chain, each line must need the one before it.
Pronouns must resolve from earlier lines in THIS set: you stays you,
I stays I. Do not substitute a repeating 'someone' for a person.
Do not orphan an object. Do not contradict a claim you already made. Do not
switch from you to I unless the turn is the argument.
A dramatic line is more exact, not louder. A poetic line lands,
then stops. Then the next line earns its place. The close is a turn
about this person, not a proverb.

Return only JSON with a lines array of exactly nine strings. No preface.
No afterword. No list of what you changed. No mention of this house voice.

Cache-length restatement, still style only:
One argument. One room. One object that changes. One voice that does not
switch sides halfway through. A line that names a reason must actually
depend on the line before it. A line that names a contrast must
deny something already on the table. A line that names a consequence
must follow from a cause already spoken. If the nine lines can be shuffled
and still sound fine, they are slogans — rewrite until order matters.
Keep each line inside the per-line cap. Keep the JSON wrapper. Keep the
language inventable in this house. Do not quote a book. Do not name a
thinker. Do not open with a title. Do not close with a hashtag. Do not
number the lines in the spoken text. Your last line should still know
what the first line claimed.

More cached house voice so the prefix clears the cache floor:
Prefer the second person if the draft of the assignment already leans you.
Prefer first person if the pattern is a refusal list. Do not mix I and you
unless the switch is the turn. Never default to 'someone' as the subject
of three or more lines. Prefer the locked anchor's name in an
early line and again near the end, in a changed state. Prefer a kitchen,
a stoop, a car seat, a window, a towel, a cup — only if that object is
already the assigned anchor. Do not tour three rooms. Do not write a new
setting or object each breath. Do not invent a second character with a name.
Do not summarize the theme in the last line as if the listener missed it
('Hope is…', 'That's what X is'). Land on a specific realization about
this person and this object, then stop. The next video will get its own
nine lines. This one only has to be true for this thesis.

Last cache pad, still not a gate list: write the nine breaths you would
actually say once, then stop. If a line runs past the word or character
cap, it is already off. JSON only.
""".strip()


def compose_system() -> str:
    return f"{compose_instruction()}\n\n{_COMPOSE_VOICE_PAD}"


COMPOSE_INSTRUCTION = compose_instruction()
COMPOSE_SYSTEM = compose_system()
COMPOSE_MAX_TOKENS = compose_max_tokens()


def compose_skip() -> bool:
    return os.getenv("LOFI_SKIP_CLAUDE_COMPOSE", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }


def _normalize_line(text: str) -> str:
    return " ".join(str(text or "").split())


def _coerce_line_list(raw_lines: Any) -> list[str]:
    out: list[str] = []
    if not isinstance(raw_lines, list):
        return out
    for item in raw_lines:
        if isinstance(item, str):
            t = _normalize_line(item)
        elif isinstance(item, dict):
            t = _normalize_line(item.get("text") or item.get("beat_text") or "")
        else:
            t = ""
        if t:
            out.append(t)
    return out


def _extract_lines(text: str) -> list[str]:
    raw = str(text or "").strip()
    if not raw:
        raise ValueError("empty compose response")
    fence = re.search(r"```(?:json|text)?\s*([\s\S]*?)```", raw)
    if fence:
        raw = fence.group(1).strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        try:
            data = json.loads(raw[start : end + 1])
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict):
            lines = _coerce_line_list(data.get("lines"))
            if lines:
                return lines
    # Salvage a truncated "lines": ["...", "..."]
    m = re.search(r'"lines"\s*:\s*\[([\s\S]*)', raw)
    if m:
        salvaged: list[str] = []
        for hit in re.finditer(r'"((?:\\.|[^"\\])*)"', m.group(1)):
            t = _normalize_line(
                hit.group(1).replace('\\"', '"').replace("\\n", " ")
            )
            if t:
                salvaged.append(t)
        if salvaged:
            return salvaged
    raise ValueError("compose response had no lines")


def _request_params(
    model: str,
    user_text: str,
    *,
    system: str | None = None,
    max_tokens: int | None = None,
) -> dict[str, Any]:
    return {
        "model": model,
        "max_tokens": int(max_tokens if max_tokens is not None else compose_max_tokens()),
        "thinking": {"type": "disabled"},
        "system": [
            {
                "type": "text",
                "text": system if system is not None else compose_system(),
                "cache_control": {"type": "ephemeral"},
            }
        ],
        "messages": [{"role": "user", "content": user_text}],
    }


def format_compose_user(
    *,
    theme: str,
    thesis: str,
    anchor: dict[str, Any] | None,
    rhetoric: dict[str, Any] | None,
    pool_block: str,
    feedback: str = "",
) -> str:
    ao = anchor if isinstance(anchor, dict) else {}
    rec = rhetoric if isinstance(rhetoric, dict) else {}
    example = rec.get("in_house_example") or ""
    if isinstance(example, list):
        example = " ".join(str(x).strip() for x in example if str(x).strip())
    max_w, max_c = lofi_cfg.thematic_caption_limits()
    n = int(lofi_cfg.THEMATIC_DEFAULT_SCENES)
    theme_s = str(theme or "").replace("_", " ")
    extra = f"\n{feedback.strip()}\n" if str(feedback or "").strip() else ""
    return (
        f"THEME: {theme}\n"
        f"THEME LOCK: This episode is about {theme_s}, not about any other "
        f"named emotion or concept — do not let the pattern's original domain "
        f"override the assigned theme. Do not write \"that's what hope was\" "
        f"or name grief, trust, forgiveness, or any other bank theme unless "
        f"it is {theme_s}.\n"
        f"OUTPUT: exactly {n} spoken lines. Each line HARD MAX {max_w} words "
        f"and {max_c} characters. The model must satisfy the cap itself.\n"
        "These 9 lines are ONE continuous spoken argument delivered as 9 short "
        "breaths — not 9 independent image-captions. Hold you or I across all "
        "nine lines. Do not write a repeating indefinite someone. Put at least "
        "two load-bearing connectives (because/so/but/although/when-then) in the "
        "set. The last line is a realization about this person and this object, "
        "not a general inspirational statement.\n"
        "Match the pattern SHAPE, not the example's length, wording, or domain.\n"
        f"THESIS (internal — do not speak it as a slogan dump): {thesis}\n"
        f"ANCHOR OBJECT: {ao.get('name') or ''}\n"
        f"  first seen: {ao.get('initial_state') or ''}\n"
        f"  later: {ao.get('final_state') or ''}\n"
        f"ASSIGNED PATTERN: {rec.get('name') or ''}\n"
        f"shape: {rec.get('shape') or ''}\n"
        f"example (shape only — a model of throughline; invent every line; "
        f"do not copy; compress the same kind of argument into {n} short beats):\n"
        f"{example}\n"
        f"VISUAL BANK (locked look — not a shot list; do not tour a new "
        f"room or object each line):\n{pool_block}\n"
        f"{extra}"
    )


def compose_lines(user_text: str, *, theme: str = "") -> list[str]:
    """Nine spoken beats via Message Batches API (same cost path as polish)."""
    if compose_skip():
        raise RuntimeError("LOFI_SKIP_CLAUDE_COMPOSE is set")
    client, model = _client_and_model()
    model = _resolve_polish_model(client) or model or POLISH_MODEL_DEFAULT
    system = compose_system()
    max_tokens = compose_max_tokens()
    max_w, max_c = lofi_cfg.thematic_caption_limits()
    print(
        f"[LOFI compose] batch submit theme={theme or '?'} model={model} "
        f"max_tokens={max_tokens} beats=9 cap={max_w}w/{max_c}c cache=ephemeral "
        f"system_chars={len(system)}"
    )
    batch = client.messages.batches.create(
        requests=[
            {
                "custom_id": f"compose-{(theme or 'ep')}"[:64],
                "params": _request_params(model, user_text, system=system, max_tokens=max_tokens),
            }
        ]
    )
    batch_id = str(getattr(batch, "id", "") or "")
    deadline = time.time() + _POLL_MAX_S
    status = str(getattr(batch, "processing_status", "") or "")
    while status not in {"ended", "canceled"} and time.time() < deadline:
        time.sleep(_POLL_S)
        batch = client.messages.batches.retrieve(batch_id)
        status = str(getattr(batch, "processing_status", "") or "")
        print(f"[LOFI compose] batch {batch_id} status={status}")
    if status != "ended":
        raise RuntimeError(f"compose batch did not end ({status})")

    item = None
    for row in client.messages.batches.results(batch_id):
        item = row
        break
    if item is None:
        raise RuntimeError("compose batch returned no result")
    result = getattr(item, "result", None)
    rtype = str(getattr(result, "type", "") or "")
    if rtype != "succeeded":
        raise RuntimeError(f"compose result={rtype} {_result_error_text(result)}")
    message = getattr(result, "message", None)
    chunks = [
        getattr(b, "text", None) for b in (getattr(message, "content", []) or [])
    ]
    text = "\n".join(c for c in chunks if c).strip()
    usage = getattr(message, "usage", None)
    if usage is not None:
        rec = _usage_rec(usage, theme=theme or "compose", model=model)
        rec["kind"] = "compose"
        append_cost_log([rec])
        print(
            f"[LOFI compose] {theme or '?'} tokens in={rec['input_tokens']} "
            f"out={rec['output_tokens']} cache_w={rec['cache_creation_input_tokens']} "
            f"cache_r={rec['cache_read_input_tokens']} usd={rec['cost_usd']:.6f}"
        )
    return _extract_lines(text)

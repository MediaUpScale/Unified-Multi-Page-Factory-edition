# -*- coding: utf-8 -*-
"""Final-stage Claude polish — Batch API, cached instruction, hard token cap."""
from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.economic_reel_lofi import config as lofi_cfg

_LOG = logging.getLogger(__name__)

POLISH_INSTRUCTION = (
    "Rewrite these 9 lines to sound more human, dramatic, and poetic — richer "
    "word choice, more natural cadence, sharper imagery — without changing the "
    "meaning, the rhetorical pattern's structure, the anchor object, or which "
    "beat is the hook vs. the insight. Keep line count and approximate length "
    "per line. Return only the 9 revised lines."
)

# Prompt cache requires >=1024 tokens on Sonnet. This pad is original house
# voice only — no third-party copy, no structure_library, no gate checklist.
_POLISH_VOICE_PAD = """
House voice for this polish (style only — do not invent a new argument):
Prefer spoken cadence over essay cadence. A line should feel like it could be
said once, in one breath, to someone sitting across a table. Prefer concrete
nouns already present in the draft. Prefer verbs a body can do. Prefer the
second person when the draft already uses it. Do not add a tenth line. Do not
merge two beats. Do not split one beat into two. Do not introduce a new named
object. Do not name a real author, philosopher, or book. Do not attribute a
line to anyone. Do not add a hashtag, a title, or a preface. Do not explain
what you changed. Do not wrap the lines in JSON or markdown fences unless the
input was already numbered; numbered 1-9 is fine. Keep each line near its
current length — a short line stays short; a longer line may keep its extra
clause if that clause is doing work. Texture means a more exact verb, a more
felt image, a less generic connective, a cleaner stress pattern. Texture does
not mean a new thesis. If a line is already the hook, keep it the hook. If a
line is already the insight, keep it the insight. If a line names the anchor,
keep that name. If a line is a first-person refusal, keep the refusal. If a
line is a micro-analogy, keep the image family. If a line is a causal step,
keep the cause. You are polishing glass, not rebuilding the window.

Repeat the constraint in other words so it stays in the cached prefix:
meaning stays, pattern stays, anchor stays, hook stays hook, insight stays
insight, nine lines stay nine, approximate length stays. Richer, more human,
more dramatic, more poetic. No new plot. No new moral. No new object. No
named person. No quote frame. No list of writing rules beyond this voice.

More house voice, still style only, so this prefix stays long enough to cache:
Speak as if the listener already knows the room. Do not lecture. Do not
summarize the theme. Do not announce the lesson. Let the line land and stop.
A dramatic line is not a louder line; it is a more exact one. A poetic line
is not a decorated line; it is a line whose stress falls where the feeling
is. Prefer the verb that the body would actually do in that room. Prefer the
noun already on the table. If the draft says walk, you may choose a closer
walk-verb, but do not send the speaker to a new street. If the draft says
sit, keep sitting. If the draft names a bag, hanger, chair, seat, or drink,
keep that name visible in the same beat. If a line begins with a connective,
you may keep or slightly retune the connective, but do not drop the causal
hinge that the draft is using. If a line is a command, keep it a command.
If a line is a refusal, keep the refusal. If a line is a naming, keep the
naming. If a line is almost silent, do not fill it with explanation.

Cadence notes, still not a checklist of production gates: vary the mouth-feel
across the nine lines without changing their jobs. A hook can stay short and
hard. An insight can stay short and decided. A middle beat can keep its
slightly longer breath if the extra words are doing image work. Avoid a run
of nine lines that all start with the same pronoun unless the draft already
does that on purpose. Avoid stacking three abstract nouns in a row. Avoid
turning a concrete action into a slogan. Avoid turning a slogan back into a
paragraph. One breath. One picture. Then the next beat.

What richer means here: a colder kitchen, a heavier bag, a more unused key,
a more empty hanger, a more untouched drink, a more unopened letter — only
when those things are already in the draft. What richer does not mean: a new
city, a new season, a new character name, a new proverb, a new ending.

What human means here: the kind of sentence a tired person would actually
say once they stopped performing. What dramatic means here: the pressure
is in the situation, not in the adjectives. What poetic means here: the
line has a place to land, and it lands.

Return only the nine revised lines. No preface. No afterword. No score.
No explanation of craft. No mention of this house voice.
""".strip()

POLISH_SYSTEM = f"{POLISH_INSTRUCTION}\n\n{_POLISH_VOICE_PAD}"
POLISH_MAX_TOKENS = 400
POLISH_MODEL_DEFAULT = "claude-sonnet-5"
# User-stated Sonnet 5 list price; batch is 50% off both sides.
_INPUT_USD_PER_MTOK = 2.0
_OUTPUT_USD_PER_MTOK = 10.0
_BATCH_DISCOUNT = 0.50
_CACHE_WRITE_MULT = 1.25
_CACHE_READ_MULT = 0.10
_POLL_S = 5.0
_POLL_MAX_S = 15 * 60


def _cost_log_path() -> Path:
    return lofi_cfg.STORE_DIR / "lofi_polish_cost_log.json"


def polish_skip() -> bool:
    """Claude polish is disabled. Claude is reserved for script writing."""
    return True


def _lines_from_script(script: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for row in script.get("lines") or []:
        if isinstance(row, dict):
            out.append(str(row.get("text") or row.get("beat_text") or "").strip())
    return out


def format_numbered_lines(lines: list[str]) -> str:
    return "\n".join(f"{i + 1}. {line}" for i, line in enumerate(lines))


def parse_numbered_lines(text: str, expected: int = 9) -> list[str]:
    raw = str(text or "").strip()
    if not raw:
        raise ValueError("empty polish response")
    fence = re.search(r"```(?:\w+)?\s*([\s\S]*?)```", raw)
    if fence:
        raw = fence.group(1).strip()
    found: list[str] = []
    for line in raw.splitlines():
        s = line.strip()
        if not s:
            continue
        m = re.match(r"^\s*(?:\d+[\).:-]|[-*])\s+(.*)$", s)
        found.append((m.group(1) if m else s).strip().strip('"'))
    if len(found) >= expected:
        return found[:expected]
    parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+", raw) if p.strip()]
    if len(parts) >= expected:
        return parts[:expected]
    raise ValueError(f"polish returned {len(found)} lines, need {expected}")


def _apply_lines(script: dict[str, Any], lines: list[str]) -> dict[str, Any]:
    out = dict(script)
    rows = [dict(r) if isinstance(r, dict) else r for r in (script.get("lines") or [])]
    for i, row in enumerate(rows):
        if not isinstance(row, dict) or i >= len(lines):
            continue
        row["text"] = lines[i]
        row["beat_text"] = lines[i]
    out["lines"] = rows
    out["monologue"] = " ".join(lines)
    out["pre_polish_lines"] = _lines_from_script(script)
    out["polished"] = True
    return out


def _usd(
    *,
    uncached_input: int,
    cache_write: int,
    cache_read: int,
    output: int,
) -> float:
    m = 1_000_000.0
    d = _BATCH_DISCOUNT
    return (
        (uncached_input / m) * _INPUT_USD_PER_MTOK * d
        + (cache_write / m) * _INPUT_USD_PER_MTOK * _CACHE_WRITE_MULT * d
        + (cache_read / m) * _INPUT_USD_PER_MTOK * _CACHE_READ_MULT * d
        + (output / m) * _OUTPUT_USD_PER_MTOK * d
    )


def _uncached_input(inp: int, cache_write: int, cache_read: int) -> int:
    """Anthropic sometimes folds cache tokens into input_tokens; don't double-count."""
    if inp >= cache_write + cache_read:
        return inp - cache_write - cache_read
    return inp


def _usage_rec(usage: Any, *, theme: str, model: str) -> dict[str, Any]:
    inp = int(getattr(usage, "input_tokens", 0) or 0)
    out = int(getattr(usage, "output_tokens", 0) or 0)
    cwrite = int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
    cread = int(getattr(usage, "cache_read_input_tokens", 0) or 0)
    uncached = _uncached_input(inp, cwrite, cread)
    rec = {
        "at": datetime.now(timezone.utc).isoformat(),
        "theme": theme,
        "model": model,
        "input_tokens": inp,
        "uncached_input_tokens": uncached,
        "output_tokens": out,
        "cache_creation_input_tokens": cwrite,
        "cache_read_input_tokens": cread,
        "cost_usd": round(
            _usd(uncached_input=uncached, cache_write=cwrite, cache_read=cread, output=out),
            6,
        ),
        "pricing": {
            "input_usd_per_mtok": _INPUT_USD_PER_MTOK,
            "output_usd_per_mtok": _OUTPUT_USD_PER_MTOK,
            "batch_discount": _BATCH_DISCOUNT,
        },
    }
    return rec


def append_cost_log(rows: list[dict[str, Any]]) -> Path:
    path = _cost_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = []
    if path.is_file():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            existing = []
    if not isinstance(existing, list):
        existing = []
    existing.extend(rows)
    path.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def summarize_cost_log(n: int = 10) -> dict[str, Any]:
    path = _cost_log_path()
    rows = []
    if path.is_file():
        try:
            rows = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            rows = []
    take = [r for r in (rows or []) if isinstance(r, dict)][-max(1, n) :]
    costs = [float(r.get("cost_usd") or 0) for r in take]
    return {
        "n": len(take),
        "avg_usd": round(sum(costs) / len(costs), 6) if costs else 0.0,
        "max_usd": round(max(costs), 6) if costs else 0.0,
        "rows": take,
        "path": str(path),
    }


def _resolve_polish_model(client: Any) -> str:
    pinned = str(os.getenv("CLAUDE_POLISH_MODEL") or POLISH_MODEL_DEFAULT).strip()
    dead = ("3-5-sonnet", "claude-3-5", "-latest")
    if any(tok in pinned for tok in dead):
        pinned = POLISH_MODEL_DEFAULT
    try:
        page = client.models.list()
        ids = [str(getattr(m, "id", "") or "") for m in (getattr(page, "data", None) or page)]
    except Exception:  # noqa: BLE001
        return pinned
    if pinned in ids:
        return pinned
    for mid in ids:
        low = mid.lower()
        if "sonnet-5" in low or low.endswith("sonnet-5"):
            return mid
    for mid in ids:
        if "sonnet" in mid.lower():
            return mid
    return pinned


def _client_and_model() -> tuple[Any, str]:
    import config as app_config
    import anthropic

    api_key = getattr(app_config, "ANTHROPIC_API_KEY", None)
    if not api_key or not str(api_key).strip():
        raise RuntimeError("ANTHROPIC_API_KEY missing — cannot polish")
    client = anthropic.Anthropic(api_key=str(api_key))
    return client, _resolve_polish_model(client)


def _request_params(model: str, numbered: str) -> dict[str, Any]:
    return {
        "model": model,
        "max_tokens": POLISH_MAX_TOKENS,
        # Sonnet 5 adaptive thinking is on by default and spends the 400-token
        # cap before any visible lines. Polish is a rewrite, not a reasoner.
        "thinking": {"type": "disabled"},
        "system": [
            {
                "type": "text",
                "text": POLISH_SYSTEM,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        "messages": [{"role": "user", "content": numbered}],
    }


def _result_error_text(result: Any) -> str:
    err = getattr(result, "error", None)
    inner = getattr(err, "error", None) if err is not None else None
    msg = getattr(inner, "message", None) or getattr(err, "message", None)
    etype = getattr(inner, "type", None) or getattr(err, "type", None)
    return " ".join(str(x) for x in (etype, msg) if x)


def polish_scripts_batch(scripts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Polish ONE finalist per theme via Anthropic Message Batches API.

    Never sends the structure library, third-party refs, or extra candidates.
    On batch/parse failure the original script is returned unchanged.
    """
    if polish_skip() or not scripts:
        print("[LOFI polish] skipped — Claude is reserved for script writing")
        return [dict(s) for s in scripts]
    client, model = _client_and_model()
    requests: list[dict[str, Any]] = []
    for i, script in enumerate(scripts):
        lines = _lines_from_script(script)
        if len(lines) < 8:
            continue
        theme = str(script.get("theme") or f"ep{i + 1}")
        requests.append(
            {
                "custom_id": f"{i:02d}-{theme}"[:64],
                "params": _request_params(model, format_numbered_lines(lines)),
            }
        )
    if not requests:
        return [dict(s) for s in scripts]
    print(
        f"[LOFI polish] batch submit n={len(requests)} model={model} "
        f"max_tokens={POLISH_MAX_TOKENS} cache=ephemeral "
        f"system_chars={len(POLISH_SYSTEM)} est_tokens={max(1, len(POLISH_SYSTEM) // 4)}"
    )
    batch = client.messages.batches.create(requests=requests)
    batch_id = str(getattr(batch, "id", "") or "")
    deadline = time.time() + _POLL_MAX_S
    status = str(getattr(batch, "processing_status", "") or "")
    while status not in {"ended", "canceled"} and time.time() < deadline:
        time.sleep(_POLL_S)
        batch = client.messages.batches.retrieve(batch_id)
        status = str(getattr(batch, "processing_status", "") or "")
        print(f"[LOFI polish] batch {batch_id} status={status}")
    if status != "ended":
        _LOG.warning("Claude polish batch did not end (%s) — keeping DeepSeek lines", status)
        print(f"[LOFI polish] WARN batch status={status} — keeping unpolished")
        return [dict(s) for s in scripts]

    by_id: dict[str, Any] = {}
    for item in client.messages.batches.results(batch_id):
        by_id[str(getattr(item, "custom_id", "") or "")] = item

    out: list[dict[str, Any]] = []
    cost_rows: list[dict[str, Any]] = []
    for i, script in enumerate(scripts):
        theme = str(script.get("theme") or f"ep{i + 1}")
        item = by_id.get(f"{i:02d}-{theme}"[:64])
        if item is None:
            out.append(dict(script))
            continue
        result = getattr(item, "result", None)
        rtype = str(getattr(result, "type", "") or "")
        if rtype != "succeeded":
            print(
                f"[LOFI polish] {theme} result={rtype} "
                f"{_result_error_text(result)} — keeping unpolished"
            )
            out.append(dict(script))
            continue
        message = getattr(result, "message", None)
        stop_reason = str(getattr(message, "stop_reason", "") or "")
        chunks = [
            getattr(b, "text", None) for b in (getattr(message, "content", []) or [])
        ]
        text = "\n".join(c for c in chunks if c).strip()
        if stop_reason:
            print(f"[LOFI polish] {theme} stop_reason={stop_reason}")
        usage = getattr(message, "usage", None)
        if usage is not None:
            rec = _usage_rec(usage, theme=theme, model=model)
            cost_rows.append(rec)
            print(
                f"[LOFI polish] {theme} tokens in={rec['input_tokens']} "
                f"out={rec['output_tokens']} cache_w={rec['cache_creation_input_tokens']} "
                f"cache_r={rec['cache_read_input_tokens']} usd={rec['cost_usd']:.6f}"
            )
        try:
            polished = parse_numbered_lines(text, expected=len(_lines_from_script(script)))
        except Exception as exc:  # noqa: BLE001
            preview = " ".join(text.split())[:180]
            print(
                f"[LOFI polish] {theme} parse fail ({exc}) "
                f"preview={preview!r} — keeping unpolished"
            )
            out.append(dict(script))
            continue
        out.append(_apply_lines(script, polished))
    if cost_rows:
        path = append_cost_log(cost_rows)
        print(f"[LOFI polish] cost log → {path}")
    return out


def polish_one(script: dict[str, Any]) -> dict[str, Any]:
    """Standing-pipeline helper: one-item Batch API call (still not Messages.create)."""
    return polish_scripts_batch([script])[0]

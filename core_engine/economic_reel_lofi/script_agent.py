# -*- coding: utf-8 -*-
"""ScriptGeneratorAgent — RAG-grounded LOFI reel scripts (separate from validator)."""
from __future__ import annotations

import json
import logging
import os
import random
import re
from typing import Any

from core_engine.economic_reel_lofi import config as lofi_cfg
from core_engine.economic_reel_lofi import lofi_collections as rag
from core_engine.economic_reel_lofi.visual_identity import (
    DEFAULT_SETTING_OBJECT_PAIRS,
    act_for_index,
    beat_lacks_noun_and_action,
    enforce_concrete_beat_visuals,
    last_concrete_noun,
    preferred_concrete_noun,
    setting_object_pool,
    _BANNED_OBJECT_RE,
)

_LOG = logging.getLogger(__name__)

# Rough per-token USD estimates for episode cost accounting — public list
# pricing, NOT verified against actual account billing. See
# _agent_log/20260820_cost-audit.md Part A for the source of these figures.
_CLAUDE_SONNET_INPUT_USD_PER_TOKEN: float = 3.0 / 1_000_000
_CLAUDE_SONNET_OUTPUT_USD_PER_TOKEN: float = 15.0 / 1_000_000

_SCRIPT_LLM_CALLS: list[dict[str, Any]] = []


def reset_script_llm_call_log() -> None:
    """Clear the per-episode script-writer LLM call log (call once per episode)."""
    _SCRIPT_LLM_CALLS.clear()


def get_script_llm_call_log() -> list[dict[str, Any]]:
    """Return every script-writer LLM call recorded since the last reset."""
    return list(_SCRIPT_LLM_CALLS)


def _estimate_tokens(text: str) -> int:
    """Rough chars/4 heuristic — same convention as core_engine.cost_tracker."""
    return max(1, len(text or "") // 4)


def _log_script_llm_call(*, provider: str, model: str, prompt: str, output: str) -> None:
    in_tok = _estimate_tokens(prompt)
    out_tok = _estimate_tokens(output)
    if provider == "claude":
        cost = (
            in_tok * _CLAUDE_SONNET_INPUT_USD_PER_TOKEN
            + out_tok * _CLAUDE_SONNET_OUTPUT_USD_PER_TOKEN
        )
    elif provider == "deepseek":
        # Rough V4 Flash blend; not account billing.
        cost = (in_tok + out_tok) * 0.28 / 1_000_000.0
    else:
        from core_engine.cost_tracker import GEMINI_FLASH_USD_PER_1M_TOKENS

        cost = (in_tok + out_tok) * GEMINI_FLASH_USD_PER_1M_TOKENS / 1_000_000.0
    _SCRIPT_LLM_CALLS.append(
        {
            "provider": provider,
            "model": model,
            "input_tokens_est": in_tok,
            "output_tokens_est": out_tok,
            "cost_usd_est": round(cost, 6),
        }
    )


_HOOK_TYPES = ("definition", "rhetorical_question", "authority_quote")

_ARC_9: tuple[str, ...] = (
    "opening",
    "build",
    "build",
    "build",
    "turn",
    "turn",
    "resolution",
    "resolution",
    "resolution",
)

_EMOTION_BY_ARC: dict[str, tuple[str, ...]] = {
    "opening": ("opening_hook", "quiet_sorrowful", "longing"),
    "build": ("longing", "doubt", "melancholic_quiet", "bittersweet"),
    "turn": ("realization", "doubt", "melancholic_romantic"),
    "resolution": ("acceptance", "resolution_warmth", "hopeful_bittersweet"),
}


def _draw_hook_type() -> str:
    """Story reels default to a dramatic opening — not a quote-maxim."""
    if float(getattr(lofi_cfg, "AUTHORITY_QUOTE_PROBABILITY", 0.0) or 0) <= 0:
        return "definition"
    r = random.random()
    if r < lofi_cfg.AUTHORITY_QUOTE_PROBABILITY:
        return "authority_quote"
    if r < 0.5 + lofi_cfg.AUTHORITY_QUOTE_PROBABILITY / 2:
        return "definition"
    return "rhetorical_question"


def _draw_thematic_hook_type(module: str) -> str:
    """Claim / question / statistic — not a quote-maxim."""
    r = random.random()
    if str(module or "").strip().lower() == "parenting":
        if r < 0.34:
            return "statistic"
        if r < 0.67:
            return "question"
        return "bold_claim"
    if r < 0.45:
        return "question"
    return "bold_claim"


_CAPTION_TYPOS: tuple[tuple[str, str], ...] = (
    (r"\bYearz\b", "Years"),
    (r"\byearz\b", "years"),
    (r"\bTeh\b", "The"),
    (r"\bteh\b", "the"),
    (r"\bDont\b", "Don't"),
    (r"\bdont\b", "don't"),
    (r"\bWont\b", "Won't"),
    (r"\bwont\b", "won't"),
    (r"\bCant\b", "Can't"),
    (r"\bcant\b", "can't"),
)


def _sanitize_caption_typos(text: str) -> str:
    out = text or ""
    for pat, repl in _CAPTION_TYPOS:
        out = re.sub(pat, repl, out)
    return out


_BATCH_USED_STRUCTURES: list[str] = []

_CAPTION_PUNCT_END = (",", ";", ":", "—", "–", "?", "!")
_CAPTION_PREP_SPLIT = frozenset(
    {
        "into",
        "onto",
        "through",
        "without",
        "before",
        "after",
        "toward",
        "across",
        "against",
        "during",
        "until",
        "because",
        "while",
    }
)
_CAPTION_TAIL_START = _CAPTION_PREP_SPLIT | {
    "to",
    "and",
    "or",
    "but",
    "so",
    "then",
    "it's",
    "its",
    "in",
    "on",
    "at",
    "for",
    "with",
    "from",
}


def reset_batch_structure_ids() -> None:
    _BATCH_USED_STRUCTURES.clear()


def note_batch_structure_id(structure_id: str) -> None:
    sid = str(structure_id or "").strip()
    if sid:
        _BATCH_USED_STRUCTURES.append(sid)


def _caption_fits(text: str, max_words: int, max_chars: int) -> bool:
    t = (text or "").strip()
    if not t:
        return True
    return len(t.split()) <= max_words and len(t) <= max_chars


def split_caption_at_clean_break(
    text: str,
    max_words: int,
    max_chars: int,
) -> list[str]:
    """Split an overlong caption at a clause boundary. Never drop the remainder."""
    raw = _sanitize_caption_typos(text).strip()
    if not raw:
        return []
    if _caption_fits(raw, max_words, max_chars):
        return [raw]
    words = raw.split()
    n = 0
    for i in range(1, len(words) + 1):
        cand = " ".join(words[:i])
        if i > max_words or len(cand) > max_chars:
            break
        n = i
    if n < 1:
        n = 1

    split_at: int | None = None
    for i in range(n, 1, -1):
        token = words[i - 1]
        if any(token.endswith(p) for p in _CAPTION_PUNCT_END):
            split_at = i
            break
        lead = words[i].lower().strip("\"'") if i < n else ""
        if lead in _CAPTION_PREP_SPLIT:
            split_at = i
            break
    if split_at is None:
        for tail_n in (3, 4, 5, 2, 6):
            if tail_n >= len(words) or len(words) - tail_n < 2:
                continue
            head, tail = words[:-tail_n], words[-tail_n:]
            hs, ts = " ".join(head), " ".join(tail)
            if not (
                _caption_fits(hs, max_words, max_chars)
                and _caption_fits(ts, max_words, max_chars)
            ):
                continue
            start = tail[0].lower().strip(".,;:—–\"'")
            if start in _CAPTION_TAIL_START or tail[0][:1].islower():
                split_at = len(head)
                break
    if split_at is None or split_at < 1:
        split_at = n
    head = " ".join(words[:split_at]).strip()
    rest = " ".join(words[split_at:]).strip()
    if not rest or not head:
        return [raw] if _caption_fits(raw, max_words, max_chars) else (
            [head] if not rest else [head, rest]
        )
    return [head] + split_caption_at_clean_break(rest, max_words, max_chars)


def _pack_caption_texts(
    parts: list[str],
    max_words: int,
    max_chars: int,
    max_scenes: int,
    *,
    keep_extra_scenes: bool = False,
) -> list[str]:
    packed: list[str] = []
    for part in parts:
        p = (part or "").strip()
        if not p:
            continue
        if packed:
            trial = f"{packed[-1]} {p}".strip()
            if _caption_fits(trial, max_words, max_chars):
                packed[-1] = trial
                continue
        packed.append(p)
    if keep_extra_scenes or len(packed) <= max_scenes:
        if len(packed) > max_scenes:
            print(
                f"[LOFI caption] {len(packed)} beats after clean split "
                f"(cap {max_scenes}) — keeping extra scenes so no words drop"
            )
        return packed
    while len(packed) > max_scenes and len(packed) >= 2:
        best_i = 0
        best_n = 10**9
        for i in range(len(packed) - 1):
            n = len(packed[i].split()) + len(packed[i + 1].split())
            if n < best_n:
                best_n = n
                best_i = i
        packed[best_i] = f"{packed[best_i]} {packed[best_i + 1]}".strip()
        del packed[best_i + 1]
        print(
            f"[LOFI caption] packed extra beats into scene {best_i + 1} "
            f"to stay within {max_scenes} scenes"
        )
    return packed


def _words(text: str) -> list[str]:
    return _sanitize_caption_typos(text or "").split()


def _restore_sliced_beats_in_place(
    lines: list[dict[str, Any]],
    monologue: str,
) -> bool:
    """
    If beat texts are sliced prefixes of the monologue, extend each beat
    until the next beat matches — never drop the tail. Preserves beat count.
    """
    mono_w = _words(monologue)
    if not mono_w or not lines:
        return False
    idx = 0
    restored = False
    for i, row in enumerate(lines):
        bw = _words(str(row.get("text") or row.get("beat_text") or ""))
        if not bw:
            continue
        if mono_w[idx : idx + len(bw)] != bw:
            # try to resync
            found = None
            for j in range(idx, max(idx, len(mono_w) - len(bw)) + 1):
                if mono_w[j : j + len(bw)] == bw:
                    found = j
                    break
            if found is None:
                return False
            idx = found
        end = idx + len(bw)
        next_bw = None
        if i + 1 < len(lines):
            next_bw = _words(
                str(lines[i + 1].get("text") or lines[i + 1].get("beat_text") or "")
            )
        while end < len(mono_w):
            if next_bw and mono_w[end : end + len(next_bw)] == next_bw:
                break
            end += 1
            restored = True
        chunk = " ".join(mono_w[idx:end])
        if chunk != str(row.get("text") or ""):
            restored = True
        row["text"] = chunk
        row["beat_text"] = chunk
        idx = end
    if idx < len(mono_w) and lines:
        extra = " ".join(mono_w[idx:])
        last = lines[-1]
        last["text"] = f"{last.get('text', '')} {extra}".strip()
        last["beat_text"] = last["text"]
        restored = True
    return restored


def _monologue_sentences(monologue: str) -> list[str]:
    blob = " ".join((monologue or "").split())
    if not blob:
        return []
    parts = re.split(r"(?<=[.!?])\s+", blob)
    return [p.strip() for p in parts if p.strip()]


def repair_script_captions(
    script: dict[str, Any],
    *,
    keep_extra_scenes: bool = False,
) -> dict[str, Any]:
    """
    Keep every spoken word. If a beat exceeds the caption cap, split at a
    comma / dash / clause boundary onto the next beat instead of slicing
    mid-phrase and dropping the tail.
    """
    if not isinstance(script, dict):
        return script
    if not lofi_cfg.is_thematic_arc(str(script.get("arc_template") or "")):
        return script
    lines = [r for r in (script.get("lines") or []) if isinstance(r, dict)]
    if not lines:
        return script
    max_words, max_chars = lofi_cfg.caption_limits(str(script.get("arc_template") or ""))
    max_scenes = int(lofi_cfg.thematic_max_scenes())
    mono = str(script.get("monologue") or "").strip()
    concat = " ".join(str(r.get("text") or r.get("beat_text") or "") for r in lines)
    concat_w = _words(concat)
    mono_w = _words(mono) if mono else []

    if mono_w and concat_w != mono_w:
        if _restore_sliced_beats_in_place(lines, mono):
            print(
                "[LOFI caption] restored dropped tails from monologue "
                f"({len(concat_w)} words on beats vs {len(mono_w)} in monologue)"
            )

    # Locked scripts: restore missing tails in place (keep beat count).
    # New writer output still splits overlong beats at a clean break.
    if keep_extra_scenes:
        for i, row in enumerate(lines):
            row["scene"] = i + 1
            row["beat_text"] = str(row.get("text") or "")
        script["lines"] = lines
        return script

    source_parts: list[str] = []
    for row in lines:
        text = str(row.get("text") or row.get("beat_text") or "")
        source_parts.extend(
            split_caption_at_clean_break(text, max_words, max_chars) or [text]
        )

    packed = _pack_caption_texts(
        source_parts,
        max_words,
        max_chars,
        max_scenes,
        keep_extra_scenes=keep_extra_scenes,
    )
    if not packed:
        return script
    new_lines: list[dict[str, Any]] = []
    n = len(packed)
    for i, text in enumerate(packed):
        src = dict(lines[i] if i < len(lines) else lines[-1])
        src["text"] = text
        src["beat_text"] = text
        src["scene"] = i + 1
        src["arc_position"] = act_for_index(i, n)
        new_lines.append(src)
    if len(new_lines) != len(lines):
        print(
            f"[LOFI caption] beat count {len(lines)} -> {len(new_lines)} "
            "after clean-break split (no words dropped)"
        )
    script["lines"] = new_lines
    return script


def _extract_json_object(text: str) -> dict[str, Any]:
    raw = (text or "").strip()
    if not raw:
        raise ValueError("empty LLM response")
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw, re.IGNORECASE)
    if fence:
        raw = fence.group(1).strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        raw = raw[start : end + 1]
    raw = re.sub(r",\s*([}\]])", r"\1", raw)
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    raise ValueError("LLM response was not valid JSON object")


def _call_claude(prompt: str) -> str:
    """Primary script writer — Anthropic Claude Sonnet."""
    import config as app_config

    api_key = getattr(app_config, "ANTHROPIC_API_KEY", None)
    if not api_key or not str(api_key).strip():
        raise RuntimeError("ANTHROPIC_API_KEY missing — cannot run Claude script writer")
    import anthropic  # noqa: PLC0415

    client = anthropic.Anthropic(api_key=str(api_key))
    model = str(app_config.get_best_claude_model(client) or app_config.SAFE_CLAUDE_MODEL)
    if "sonnet" not in model.lower():
        model = str(getattr(app_config, "SAFE_CLAUDE_MODEL", None) or "claude-3-5-sonnet-latest")
    print(f"[LOFI script] writer=claude model={model}")
    kwargs: dict[str, Any] = dict(
        model=model,
        max_tokens=6000,
        system=(
            "You write continuous first-person spoken-word scripts for short vertical reels. "
            "Every line must follow causally from the previous one. Output STRICT JSON only."
        ),
        messages=[{"role": "user", "content": prompt}],
    )
    try:
        message = client.messages.create(**kwargs, temperature=0.82)
    except Exception as exc:  # noqa: BLE001
        if "temperature" not in str(exc).lower():
            raise
        print("[LOFI script] retry Claude without temperature (model rejects it)")
        message = client.messages.create(**kwargs)
    chunks = [
        getattr(b, "text", None) for b in (getattr(message, "content", []) or [])
    ]
    text = "\n".join(c for c in chunks if c).strip()
    _log_script_llm_call(provider="claude", model=model, prompt=prompt, output=text)
    if not text:
        raise RuntimeError("Claude returned empty script")
    return text


def _call_gemini(prompt: str) -> str:
    import config as app_config
    from avatar_engine.providers.gemini_utils import (
        generate_content_with_model_fallback,
        make_gemini_client_with_fallback,
    )
    from avatar_engine.providers.model_router import CHEAP_TEXT_CHAIN, CHEAP_TEXT_PRIMARY

    api_key = getattr(app_config, "GEMINI_API_KEY", None)
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY missing — cannot fall back for ScriptGeneratorAgent")
    client = make_gemini_client_with_fallback(str(api_key))
    chain = [CHEAP_TEXT_PRIMARY, *CHEAP_TEXT_CHAIN]
    response = generate_content_with_model_fallback(
        client, chain, contents=[prompt],
    )
    text = getattr(response, "text", None) or ""
    if not text and getattr(response, "candidates", None):
        try:
            text = response.candidates[0].content.parts[0].text
        except Exception:  # noqa: BLE001
            text = ""
    text = str(text or "")
    _log_script_llm_call(
        provider="gemini", model=(chain[0] if chain else "gemini"), prompt=prompt, output=text
    )
    return text


def _call_deepseek(prompt: str) -> str:
    """Primary script writer — DeepSeek V4 Flash (OpenAI-compatible)."""
    import config as app_config

    api_key = getattr(app_config, "DEEPSEEK_API_KEY", None)
    if not api_key or not str(api_key).strip():
        raise RuntimeError("DEEPSEEK_API_KEY missing — cannot run DeepSeek script writer")
    from openai import OpenAI

    base_url = str(
        getattr(app_config, "DEEPSEEK_BASE_URL", None) or "https://api.deepseek.com/v1"
    ).strip()
    model = str(
        getattr(app_config, "DEEPSEEK_FLASH_MODEL", None)
        or getattr(app_config, "DEEPSEEK_MODEL", None)
        or "deepseek-v4-flash"
    ).strip()
    if model.lower() in {"deepseek-chat", "deepseek-coder", "deepseek-chat-v3"}:
        model = "deepseek-v4-flash"
    print(f"[LOFI script] writer=deepseek model={model}")
    client = OpenAI(api_key=str(api_key), base_url=base_url)
    system = (
        "You write continuous first-person spoken-word scripts for short vertical reels. "
        "Every line must follow causally from the previous one. Output STRICT JSON only."
    )
    resp = client.chat.completions.create(
        model=model,
        max_tokens=8192,
        temperature=0.82,
        extra_body={"thinking": {"type": "disabled"}},
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
    )
    choice = resp.choices[0]
    text = str((choice.message.content or "")).strip()
    _log_script_llm_call(provider="deepseek", model=model, prompt=prompt, output=text)
    if not text:
        raise RuntimeError(
            f"DeepSeek returned empty script (finish_reason={choice.finish_reason!r})"
        )
    return text


def _call_llm(prompt: str) -> str:
    """DeepSeek V4 Flash first; Gemini only if DeepSeek is unavailable. Not Claude."""
    try:
        return _call_deepseek(prompt)
    except Exception as exc:  # noqa: BLE001
        _LOG.warning("DeepSeek script writer failed (%s) — falling back to Gemini", exc)
        print(f"[LOFI script] WARN DeepSeek failed ({exc}); Gemini fallback")
        return _call_gemini(prompt)


_FALLBACK_SUBJECTS: tuple[str, ...] = (
    "woman",
    "object_focus",
    "man",
    "silhouette",
    "couple",
    "woman",
    "object_focus",
    "silhouette",
    "woman",
)
_FALLBACK_EXPR: tuple[str, ...] = (
    "hopeful",
    "waiting",
    "tired",
    "distant",
    "resigned",
    "quiet",
    "still",
    "soft",
    "settled",
)
_FALLBACK_TOD: tuple[str, ...] = (
    "dawn",
    "morning",
    "afternoon",
    "dusk",
    "night",
    "dusk",
    "evening",
    "night",
    "dawn",
)


_FALLBACK_LINE_SHAPES: tuple[tuple[str, str], ...] = (
    ("I set the {obj}.", "opening_hook"),
    ("I wait by the {obj}.", "longing"),
    ("I watch the {obj}.", "quiet_sorrowful"),
    ("I leave the {obj}.", "doubt"),
    ("I wait by the {obj}.", "realization"),
    ("I keep the {obj}.", "realization"),
    ("I look at the {obj}.", "acceptance"),
    ("I watch the {obj}.", "hopeful_bittersweet"),
    ("I leave the {obj} still.", "resolution_warmth"),
)

_GENERIC_FALLBACK_BEATS: tuple[tuple[str, str], ...] = (
    ("I wait by the same door.", "opening_hook"),
    ("I waited by the same door.", "longing"),
    ("The keys stayed in the bowl.", "quiet_sorrowful"),
    ("The list stayed on the fridge.", "doubt"),
    ("I opened the envelope later.", "realization"),
    ("The vow never left the table.", "realization"),
    ("He put the kettle on Tuesday.", "acceptance"),
    ("Same shoes wait on the stoop.", "hopeful_bittersweet"),
    ("I watch the door happen.", "resolution_warmth"),
)


def _force_script_fallback() -> bool:
    raw = os.getenv("LOFI_FORCE_SCRIPT_FALLBACK", "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _short_object_phrase(
    detail: dict[str, Any] | None,
    pool: list[dict[str, str]],
    index: int,
) -> tuple[str, str]:
    """Return (caption-safe object, source_detail text)."""
    text = str((detail or {}).get("detail") or "").strip()
    noun = preferred_concrete_noun(text)
    if noun:
        return noun, text
    if pool:
        pair = pool[index % len(pool)]
        obj = str(pair.get("key_object") or "").strip()
        noun = preferred_concrete_noun(obj)
        if not noun and obj:
            noun = obj.split()[-1]
        return noun or "door", text or obj
    return "door", text


def _pool_pair_for_detail(
    source: str,
    pool: list[dict[str, str]],
    index: int,
) -> dict[str, str]:
    if not pool:
        return {"setting": "hallway near an open door", "key_object": "open doorway"}
    noun = preferred_concrete_noun(source)
    if noun:
        for p in pool:
            blob = f"{p.get('setting') or ''} {p.get('key_object') or ''}".lower()
            if noun in blob:
                return p
    return pool[index % len(pool)]


def _fit_caption(text: str, arc_id: str | None = None) -> str:
    """Sanitize only. Thematic overflow is split in repair_script_captions."""
    del arc_id
    return _sanitize_caption_typos(text).strip()


def _metabolize_fallback_beats(
    retrieved: list[dict[str, Any]],
    pool: list[dict[str, str]],
    scene_count: int,
) -> list[tuple[str, str, str]]:
    """
    Build (text, emotion, source_detail) from retrieved details.

    Spoken lines metabolize the RAG objects (noun + action) instead of the
    generic object diary. If the bank is empty, keep the old generic beats.
    """
    if not retrieved:
        out: list[tuple[str, str, str]] = []
        for i in range(scene_count):
            text, emo = _GENERIC_FALLBACK_BEATS[i % len(_GENERIC_FALLBACK_BEATS)]
            out.append((text, emo, ""))
        return out
    beats: list[tuple[str, str, str]] = []
    for i in range(scene_count):
        tmpl, emo = _FALLBACK_LINE_SHAPES[i % len(_FALLBACK_LINE_SHAPES)]
        detail = retrieved[i % len(retrieved)]
        obj, source = _short_object_phrase(detail, pool, i)
        line = _fit_caption(tmpl.format(obj=obj))
        if beat_lacks_noun_and_action(line) or not line:
            line = _fit_caption(f"I hold the {obj}.")
        beats.append((line, emo, source))
    return beats


_THEMATIC_FALLBACK_EMOTIONS: tuple[str, ...] = (
    "opening_hook",
    "quiet_sorrowful",
    "melancholic_quiet",
    "bittersweet",
    "longing",
    "realization",
    "acceptance",
    "resolution_warmth",
)


def _thematic_fallback_beats(
    theme: str,
    scene_count: int,
    retrieved: list[dict[str, Any]],
) -> list[tuple[str, str, str]]:
    """Thesis-shaped spanning beats — not the object-skeleton fallback."""
    label = (theme or "this").replace("_", " ").strip()
    skeleton = [
        f"{label.capitalize()} does not arrive as a speech.",
        "It shows up after the worst hour.",
        "The body wakes first, already flinching.",
        "You reach for the old silence,",
        "and find a small ordinary mercy instead.",
        "Nobody named it while it was happening.",
        "It was never trying to be a lesson.",
        "It just didn't leave the room.",
        "The quiet thing is still here.",
        "You notice it only on the second look.",
        "Not as a speech. As a stay.",
        "That is enough to begin again.",
    ]
    n = max(
        int(getattr(lofi_cfg, "MIN_SCENES", 8)),
        min(int(scene_count), int(lofi_cfg.thematic_max_scenes())),
    )
    out: list[tuple[str, str, str]] = []
    for i in range(n):
        src = ""
        if retrieved:
            src = str(retrieved[i % len(retrieved)].get("detail") or "")
        text = _fit_caption(skeleton[i % len(skeleton)], lofi_cfg.THEMATIC_ARC_ID)
        emo = _THEMATIC_FALLBACK_EMOTIONS[i % len(_THEMATIC_FALLBACK_EMOTIONS)]
        out.append((text, emo, src))
    return out


def _fallback_script(
    *,
    module: str,
    theme: str,
    scene_count: int,
    hook_type: str,
    quote: dict[str, Any] | None,
    setting_object_pairs: list[dict[str, str]] | None = None,
    retrieved_details: list[dict[str, Any]] | None = None,
    arc_template: str | None = None,
) -> dict[str, Any]:
    """Offline story fallback — metabolize retrieved details; never drop them."""
    del quote
    theme_s = theme.replace("_", " ")
    pool = setting_object_pairs or [dict(p) for p in DEFAULT_SETTING_OBJECT_PAIRS]
    retrieved = [
        d
        for d in (retrieved_details or [])
        if isinstance(d, dict) and str(d.get("detail") or "").strip()
    ]
    thematic = lofi_cfg.is_thematic_arc(arc_template)
    if thematic:
        beats = _thematic_fallback_beats(theme, scene_count, retrieved)
        hook_out = hook_type if hook_type in (
            set(_HOOK_TYPES) | set(getattr(lofi_cfg, "THEMATIC_HOOK_TYPES", ()))
        ) else "bold_claim"
    else:
        beats = _metabolize_fallback_beats(retrieved, pool, scene_count)
        hook_out = hook_type if hook_type in _HOOK_TYPES else "definition"
    monologue = " ".join(b[0] for b in beats)
    lines = []
    max_words, max_chars = lofi_cfg.caption_limits(arc_template)
    del max_words
    for i in range(len(beats)):
        text, emo, source = beats[i]
        pair = _pool_pair_for_detail(source, pool, i)
        row = {
            "scene": i + 1,
            "text": text[:max_chars],
            "beat_text": text[:max_chars],
            "visual_prompt": f"story beat {i + 1} {theme_s}",
            "emotion": emo,
            "arc_position": act_for_index(i, scene_count),
            "subject_type": _FALLBACK_SUBJECTS[i % len(_FALLBACK_SUBJECTS)],
            "subject_expression": _FALLBACK_EXPR[i % len(_FALLBACK_EXPR)],
            "setting": pair["setting"],
            "key_object": pair["key_object"],
            "time_of_day": _FALLBACK_TOD[i % len(_FALLBACK_TOD)],
        }
        if source:
            row["source_detail"] = source
        lines.append(row)
    first_pair = pool[0] if pool else {
        "setting": "hallway near an open door",
        "key_object": "open doorway",
    }
    anchor_name = str(first_pair.get("key_object") or "open doorway")
    if retrieved:
        d0 = str(retrieved[0].get("detail") or "")
        noun = preferred_concrete_noun(d0) or last_concrete_noun(d0)
        for p in pool:
            ko = str(p.get("key_object") or "")
            if noun and noun in ko.lower():
                anchor_name = ko
                break
        else:
            if noun:
                anchor_name = noun
    print(
        "[LOFI script] fallback retrieved_details="
        f"{len(retrieved)} anchor={anchor_name!r}"
    )
    print("[LOFI script] fallback monologue:", monologue)
    print("[LOFI script] retrieved_details:", retrieved)
    return {
        "hook_type": hook_out,
        "theme": theme,
        "module": module,
        "monologue": monologue,
        "lines": lines,
        "anchor_object": {
            "name": anchor_name,
            "initial_state": "waiting where I left it",
            "final_state": "handled, still in the room",
        },
        "arc_template": str(
            arc_template
            or getattr(lofi_cfg, "DEFAULT_ARC_BY_MODULE", {}).get(module)
            or "setup_ache_acceptance"
        ),
        "retrieved_details": retrieved,
        "script_source": "fallback",
    }


_THEMATIC_STRUCTURES: tuple[dict[str, str], ...] = (
    {
        "id": "spanning_claim",
        "label": "hard claim that continues across two beats",
        "example": (
            "ABSTRACT STRUCTURE ONLY — invent every sentence. "
            "Open with a hard prohibition or assertion. Beat 2 completes the SAME "
            "sentence (the reason the claim exists). Beats 3–5 show a cost that "
            "compounds. Close on the identity left after the cover story. "
            "Do not use a known viral opening."
        ),
    },
    {
        "id": "anaphora",
        "label": "repeated opener, then a turn",
        "example": (
            "ABSTRACT STRUCTURE ONLY — invent every sentence. "
            "Repeat a short original imperative or permission 2–3 times with a "
            "changing object, then refuse the usual repair impulse. Turn on timing "
            "or self-trust. Do not reuse a published refrain."
        ),
    },
    {
        "id": "contrast_insight",
        "label": "two decaying covers, then the lasting thing",
        "example": (
            "ABSTRACT STRUCTURE ONLY — invent every sentence. "
            "Name two things that fade or change, then name the thing that does "
            "not. Contrast cover vs contents. Do not use beauty/body/soul slogans."
        ),
    },
    {
        "id": "nested_belief",
        "label": "named belief, then cost, then repair",
        "example": (
            "ABSTRACT STRUCTURE ONLY — invent the belief; do not use a published line. "
            "Name a hidden belief in original words, then the cost of living inside it, "
            "then what repair looks like in the room. Do not reuse the formula "
            "'the saddest thing X can believe is...'."
        ),
    },
    {
        "id": "statistic_lean_in",
        "label": "a number, then what it means, then lean in",
        "example": (
            "ABSTRACT STRUCTURE ONLY — invent every sentence. "
            "Open with a specific grounded number, say what it costs in ordinary "
            "minutes, then lean in rather than lecture. Do not reuse a published "
            "childhood-percentage opener."
        ),
    },
)


# Repo-original gold standard (hope, 2026-08-19). Cadence/quality only — never
# inject when the live theme is hope, and never treat as a line to copy.
_GOLD_CADENCE_BEATS: tuple[str, ...] = (
    "Hope isn't the feeling that everything's fine now.",
    "It's refusing to believe last night was the end.",
    "After the worst fight you've ever survived together,",
    "the body wakes first, bracing for the same silence.",
    "You brace for the cold side of the bed,",
    "and find the kettle steaming without being asked.",
    "Hope was never loud enough to announce itself.",
    "It just quietly stayed lit anyway,",
    "like one window still burning on a dark street.",
)


def _pick_thematic_structure(module: str, hook_type: str, theme: str) -> dict[str, str]:
    hook = str(hook_type or "").strip().lower()
    mod = str(module or "").strip().lower()
    exclude = {str(x).strip() for x in _BATCH_USED_STRUCTURES if str(x).strip()}
    if hook == "statistic" or (mod == "parenting" and hook == "bold_claim" and "season" in theme):
        chosen = dict(_THEMATIC_STRUCTURES[4])
    elif hook == "question" and mod == "parenting":
        chosen = dict(_THEMATIC_STRUCTURES[3])
    elif hook == "question":
        chosen = dict(_THEMATIC_STRUCTURES[1])
    elif "betray" in theme or hook == "bold_claim":
        keyed = {
            "betrayal": 0,
            "loneliness": 1,
            "distance": 1,
            "self_respect": 2,
            "vulnerability": 2,
            "patience": 3,
            "gentle_discipline": 3,
            "fleeting_season": 4,
            "presence": 3,
        }
        idx = keyed.get(theme, None)
        if idx is None:
            idx = sum(ord(c) for c in theme) % 4
        chosen = dict(_THEMATIC_STRUCTURES[idx])
    else:
        idx = sum(ord(c) for c in f"{mod}:{theme}:{hook}") % 4
        chosen = dict(_THEMATIC_STRUCTURES[idx])
    if chosen.get("id") in exclude:
        for cand in _THEMATIC_STRUCTURES:
            if cand["id"] not in exclude:
                print(
                    f"[LOFI script] structure {chosen.get('id')} already used in batch "
                    f"— rotating to {cand['id']}"
                )
                return dict(cand)
    return chosen


def _gold_cadence_block(theme: str, module: str) -> str:
    """Repo-original hope script as quality bar — omitted when theme is hope."""
    if str(theme or "").strip().lower() == "hope":
        return ""
    numbered = "\n".join(f"  {i+1}. {line}" for i, line in enumerate(_GOLD_CADENCE_BEATS))
    domain = (
        "Parenting content required — this sample is relationship cadence only."
        if str(module or "").strip().lower() == "parenting"
        else "Write THIS theme, not hope."
    )
    return f"""
QUALITY BAR (repo-original approved script — match originality and split cadence; do NOT copy lines, images, or the word Hope):
{numbered}
{domain}
"""


def _thematic_script_prompt(
    *,
    module: str,
    theme: str,
    subtheme: str,
    scene_count: int,
    hook_type: str,
    arc: dict[str, str],
    detail_block: str,
    pool_block: str,
    feedback_block: str,
    act_guide: str,
    structure: dict[str, str],
    quote_block: str,
    gold_block: str,
) -> str:
    max_words, max_chars = lofi_cfg.caption_limits(lofi_cfg.THEMATIC_ARC_ID)
    min_scenes = int(getattr(lofi_cfg, "MIN_SCENES", 8))
    max_scenes = int(lofi_cfg.thematic_max_scenes())
    return f"""Write a spoken-word vertical-reel script as ONE progressive thesis.

MODULE: {module}
THEME: {theme}
SUBTHEME: {subtheme or "(none)"}
TARGET_SCENE_COUNT: {scene_count} (HARD MAX {max_scenes} beats / {max_scenes * 3}s.
  Prefer exactly {scene_count}. Do not emit a 10th beat.)
HOOK_TYPE: {hook_type}
STRUCTURE_ID: {structure.get("id")} — {structure.get("label")}
ARC PER SCENE: {act_guide}
ARC SHAPE:
  id={arc.get("id")}
  {arc.get("label")}
  act1: {arc.get("act1")}
  act2: {arc.get("act2")}
  act3: {arc.get("act3")}
{gold_block}
CONCRETE DETAIL BANK (optional texture inside lines, not the skeleton — do NOT quote verbatim):
{detail_block}
{quote_block}
SETTING_OBJECT_POOL (visual fields only — pick setting + key_object from this list):
{pool_block}
{feedback_block}

This is ONE argument about a single theme that builds, costs, and pays off.
Not object-anchored micro-scenes. Not "I wait by the door / I watch the rain."
Not a list of removable maxims. Not a book-plug outro.
Do NOT force one shared physical object across all beats. Varied imagery is the default.
A recurring object is allowed only when this theme naturally wants one.

Use THIS structure only (abstract shape — invent every sentence; never paste a known reel):
{structure.get("example")}

RULES:
1) Write "monologue" as the full spoken thesis. Split THAT SAME text across
   {min_scenes}–{max_scenes} "text" fields. Prefer exactly {scene_count} beats.
   Never more than {max_scenes}.
2) Caption chunks stay short: 4–8 words preferred, HARD MAX {max_words} words
   and {max_chars} characters. If a sentence is longer, split it across the
   NEXT beat at a comma, dash, or clause boundary — but never exceed {max_scenes}
   beats total. If you are already at {max_scenes}, shorten the line instead.
3) One longer thematic sentence MAY span 2 consecutive beats. Fragments are
   allowed. Not every beat must be a complete closed sentence.
4) Scene 1 is the hook matching HOOK_TYPE:
   - bold_claim: a hard opening assertion
   - question: a short question
   - statistic: a specific number/claim (parenting ok; invent only if grounded)
   - definition / rhetorical_question: also allowed
5) Last 2–3 beats are the insight/payoff of the SAME theme.
6) Second person ("you"/"they") or close third is preferred over object diary.
7) Details from the bank may color a line; they must not become the plot.
8) FORBIDDEN:
   - object-skeleton ("I wait by the door", "the keys stayed in the bowl")
   - advice lists ("you should", "remember to")
   - named authors, quotes, attributions, book plugs
   - stretching toward a 50–60s / 20-line format
   - copying or lightly paraphrasing famous/viral relationship or parenting reels
   - any sentence that would still be recognizable as a published reel if you changed one noun
9) emotion per line from: opening_hook, quiet_sorrowful, melancholic_quiet,
   bittersweet, longing, doubt, realization, acceptance, hopeful_bittersweet,
   resolution_warmth, melancholic_romantic
10) arc_position MUST be act1, act2, or act3 matching ARC PER SCENE.
11) For EVERY beat also emit visual fields (images, not spoken plot):
    - subject_type: woman | man | couple | silhouette | object_focus.
      Mix the 9 beats: about 5–6 person shots and 3–4 object_focus or
      silhouette shots. Use object_focus when the stand-in is a strong
      specific object. Use couple on at most 3 beats (emotional peaks).
    - subject_expression: 1–3 words
    - setting + key_object MUST trace to THIS beat's idea: the noun in
      the line, or a meaning stand-in (chaos→unmade empty bed / suitcase on
      the floor; walking from a fight→open doorway; cost→empty platform;
      peace/quiet→rain on the window or kettle).
      Never default every abstract line to a chair.
      Copy from SETTING_OBJECT_POOL when a pair matches the idea; otherwise
      name the stand-in. Do not reuse the same key_object more than once.
      Never pick a small object meant to be held near the face.
    - time_of_day: dawn | morning | afternoon | dusk | night | evening
    - beat_text: same as text
12) anchor_object is OPTIONAL. Omit it unless this theme naturally wants one
    recurring object. Varied imagery is valid and preferred.
13) Every line must be original to this theme. If a sentence could belong to a
    famous relationship/parenting reel, rewrite it.
14) STRUCTURAL VARIETY: do not open with "the saddest thing X can believe is...".
    Do not reuse the same rhetorical frame as another episode in this batch.

Output STRICT JSON only, no markdown:
{{
  "hook_type": "{hook_type}",
  "theme": "{theme}",
  "module": "{module}",
  "arc_template": "{arc.get("id")}",
  "structure_id": "{structure.get("id")}",
  "monologue": "<full continuous spoken thesis>",
  "lines": [
    {{
      "scene": 1,
      "text": "<short hook chunk>",
      "beat_text": "<same as text>",
      "emotion": "opening_hook",
      "arc_position": "act1",
      "subject_type": "woman",
      "subject_expression": "distant, still",
      "setting": "bedroom, edge of a neatly made bed",
      "key_object": "untouched pillow",
      "time_of_day": "dusk"
    }}
  ]
}}
Valid JSON only: no trailing commas, no comments, escape quotes inside strings.
No NSFW. No real private individuals named. Brand-safe for {module}.
"""


def generate_script(
    *,
    module: str,
    theme: str,
    subtheme: str = "",
    scene_count: int,
    feedback: str | None = None,
    force_hook_type: str | None = None,
    theme_row: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Write ONE continuous spoken-word story, then split across scenes.

    Scene 1 is the scroll-stop hook. Final scenes deliver comfort, not a dry moral.
    """
    feedback_block = (
        f"\nPREVIOUS REJECTION FEEDBACK (must fix):\n{feedback}\n" if feedback else ""
    )
    pool = setting_object_pool(theme_row)
    pool_block = "\n".join(
        f'  - setting="{p["setting"]}" | key_object="{p["key_object"]}"'
        for p in pool
    )
    arc = rag.select_arc_template(module, theme, subtheme)
    thematic = lofi_cfg.is_thematic_arc(str(arc.get("id") or ""))
    seed: dict[str, Any] = {"hooks": [], "details": [], "quote": None}
    if thematic:
        seed = rag.select_thematic_seed(theme_row, module=module)
        retrieved = list(seed.get("details") or [])
        hook_type = force_hook_type or _draw_thematic_hook_type(module)
    else:
        retrieved = rag.select_concrete_details(theme_row, module=module)
        hook_type = force_hook_type or _draw_hook_type()
    quote: dict[str, Any] | None = seed.get("quote") if thematic else None
    if hook_type == "authority_quote":
        quote = rag.pick_quote_for_theme(theme, module)
        if not quote:
            hook_type = "definition"

    if _force_script_fallback():
        print("[LOFI script] FORCE fallback (LOFI_FORCE_SCRIPT_FALLBACK)")
        return _fallback_script(
            module=module,
            theme=theme,
            scene_count=scene_count,
            hook_type=hook_type,
            quote=quote,
            setting_object_pairs=pool,
            retrieved_details=retrieved,
            arc_template=str(arc.get("id") or ""),
        )
    retrieved = [
        d for d in retrieved
        if isinstance(d, dict)
        and not _BANNED_OBJECT_RE.search(str(d.get("detail") or ""))
    ]
    detail_block = "\n".join(
        f'  - ({d.get("sensory_type") or "object"}) {d.get("detail")}'
        for d in retrieved
    ) or "  - (none — invent from SETTING_OBJECT_POOL only)"
    act_guide = ", ".join(
        f"{i+1}:{act_for_index(i, scene_count)}" for i in range(scene_count)
    )
    structure = _pick_thematic_structure(module, hook_type, theme) if thematic else {}
    quote_block = ""
    q_seed = seed.get("quote") if isinstance(seed.get("quote"), dict) else None
    q_text = str((q_seed or {}).get("quote_text") or "").strip()
    if q_seed and q_text:
        from core_engine.economic_reel_lofi.reference_guard import banned_hook_text

        if not banned_hook_text(q_text):
            quote_block = (
                "\nOPTIONAL QUOTE (do not speak it unless it fits as texture; never attribute):\n"
                f'  \"{q_text}\"\n'
            )

    if thematic:
        prompt = _thematic_script_prompt(
            module=module,
            theme=theme,
            subtheme=subtheme,
            scene_count=scene_count,
            hook_type=hook_type,
            arc=arc,
            detail_block=detail_block,
            pool_block=pool_block,
            feedback_block=feedback_block,
            act_guide=act_guide,
            structure=structure,
            quote_block=quote_block,
            gold_block=_gold_cadence_block(theme, module),
        )
    else:
        prompt = f"""Write a spoken-word vertical-reel script.

MODULE: {module}
THEME: {theme}
SUBTHEME: {subtheme or "(none)"}
SCENE_COUNT: {scene_count}
ARC PER SCENE (act1=scenes 1-3, act2=4-6, act3=7-9): {act_guide}
ARC SHAPE (follow this emotional sequence, LRU-rotated — do not copy old formula blindly):
  id={arc.get("id")}
  {arc.get("label")}
  act1: {arc.get("act1")}
  act2: {arc.get("act2")}
  act3: {arc.get("act3")}

CONCRETE DETAIL BANK (inspiration only — do NOT quote verbatim; metabolize into original spoken lines):
{detail_block}

SETTING_OBJECT_POOL (pick setting + key_object from this list only — do not invent new locations):
{pool_block}
{feedback_block}

This is ONE continuous first-person voice moving through the ARC SHAPE above.
Not a list of standalone statements. Not maxims. Not advice.

SHAPE (illustrative — do not copy; match the causal chain):
I wait by the same door.
I waited by the same door.
The keys stayed in the bowl.
The list stayed on the fridge.
I opened the envelope later.
The vow never left the table.
He put the kettle on Tuesday.
Same shoes wait on the stoop.
I watch the door happen.

RULES:
1) Write "monologue" first as one continuous spoken piece. Then split THAT SAME text
   across exactly {scene_count} scene "text" fields, in order. Reading the lines aloud
   must reconstruct the monologue.
2) Each line MUST follow causally/emotionally from the previous one. If you can delete
   a line and the piece still makes sense, rewrite — the throughline is broken.
3) First person. Plain spoken language. Concrete imagery over abstraction.
   Every spoken line MUST pair a concrete noun (door, coat, bed, window, suitcase)
   with an action (wait, hang, leave, open, look). No pure-abstract beats
   like "some mornings I forget you're gone."
4) Scene 1 is the hook — specific and felt, not a thesis statement.
5) Last 2–3 scenes land in comfort / quiet resolution, not bleakness.
6) Line length: 3–6 words preferred, HARD MAX {lofi_cfg.MAX_CAPTION_WORDS} words
   and {lofi_cfg.MAX_CAPTION_CHARS} characters. One breath. Must fit one caption line.
7) FORBIDDEN:
   - "X isn't Y, it's Z" (including split across two scenes: "isn't …" then "It's …")
   - "It wasn't X, it was Y"
   - Isolated punchlines / removable aphorisms
   - Generic "he/she always…" relationship commentary
   - Advice ("you should", "remember", "X comes from Y")
   - Named authors, quotes, attributions
8) emotion per line from: opening_hook, quiet_sorrowful, melancholic_quiet, bittersweet,
   longing, doubt, realization, acceptance, hopeful_bittersweet, resolution_warmth,
   melancholic_romantic
9) arc_position MUST be act1, act2, or act3 matching ARC PER SCENE.
10) For EVERY beat also emit visual fields. Do not write a freeform image prompt.
    - subject_type: exactly one of woman | man | couple | silhouette | object_focus
      Vary subject_type across the {scene_count} beats (do not use woman for all).
    - subject_expression: 1–3 words only (fills a locked character template slot)
    - setting + key_object: copy from SETTING_OBJECT_POOL (pair any listed setting
      with any listed key_object). Do not invent a new environment.
      Every beat MUST have a concrete setting and key_object even if the spoken
      line is abstract. Do not reuse the exact same setting+object on consecutive beats.
    - time_of_day: dawn | morning | afternoon | dusk | night | evening
    - beat_text: same as text
11) Mandatory anchor_object: one physical thing drawn from the DETAIL BANK or
    SETTING_OBJECT_POOL. It must appear (or be felt) near the opening and transform
    by the end. name / initial_state / final_state are required.

Output STRICT JSON only, no markdown:
{{
  "hook_type": "{hook_type}",
  "theme": "{theme}",
  "module": "{module}",
  "arc_template": "{arc.get("id")}",
  "anchor_object": {{
    "name": "<object from the detail bank or pool>",
    "initial_state": "<how it starts>",
    "final_state": "<how it has changed by the last scenes>"
  }},
  "monologue": "<full continuous spoken-word story>",
  "lines": [
    {{
      "scene": 1,
      "text": "<scene 1 hook>",
      "beat_text": "<same as text>",
      "emotion": "opening_hook",
      "arc_position": "act1",
      "subject_type": "woman",
      "subject_expression": "resigned, distant",
      "setting": "bedroom, edge of a neatly made bed",
      "key_object": "untouched pillow",
      "time_of_day": "dusk"
    }}
  ]
}}
Exactly {scene_count} lines, scene numbers 1..{scene_count}.
Valid JSON only: no trailing commas, no comments, escape quotes inside strings.
No NSFW. No real private individuals named. Brand-safe for {module}.
"""

    data = None
    last_exc: Exception | None = None
    for attempt in range(1, 3):
        try:
            raw = _call_llm(prompt)
            data = _extract_json_object(raw)
            break
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            _LOG.warning(
                "ScriptGeneratorAgent parse attempt %s failed (%s)", attempt, exc
            )
            print(f"[LOFI script] JSON parse attempt {attempt} failed: {exc}")
    if data is None:
        _LOG.warning(
            "ScriptGeneratorAgent LLM failed (%s) — using fallback script", last_exc
        )
        return _fallback_script(
            module=module,
            theme=theme,
            scene_count=scene_count,
            hook_type=hook_type,
            quote=quote,
            setting_object_pairs=pool,
            retrieved_details=retrieved,
            arc_template=str(arc.get("id") or ""),
        )

    data["hook_type"] = hook_type
    data["theme"] = theme
    data["module"] = module
    data["retrieved_details"] = retrieved
    data["arc_template"] = str(data.get("arc_template") or arc.get("id") or "")
    data["structure_id"] = str(
        data.get("structure_id") or (structure.get("id") if thematic else "") or ""
    )
    data["seed_hooks"] = list(seed.get("hooks") or [])
    if isinstance(data.get("anchor_object"), dict):
        ao = data["anchor_object"]
        data["anchor_object"] = {
            "name": str(ao.get("name") or "").strip(),
            "initial_state": str(ao.get("initial_state") or "").strip(),
            "final_state": str(ao.get("final_state") or "").strip(),
        }
    elif not thematic:
        data["anchor_object"] = {
            "name": "",
            "initial_state": "",
            "final_state": "",
        }
    lines = data.get("lines") or []
    if not isinstance(lines, list):
        lines = []
    n_lines = len(lines) or scene_count
    for i, row in enumerate(lines):
        if not isinstance(row, dict):
            continue
        row["scene"] = int(row.get("scene") or i + 1)
        row["arc_position"] = act_for_index(i, n_lines)
        if not row.get("emotion"):
            emo_arc = _ARC_9[i] if n_lines == 9 and i < 9 else "build"
            row["emotion"] = _EMOTION_BY_ARC.get(emo_arc, ("longing",))[0]
        row["text"] = _sanitize_caption_typos(
            str(row.get("text") or row.get("beat_text") or "")
        ).strip()
        row["beat_text"] = row["text"]
        if not row.get("visual_prompt"):
            row["visual_prompt"] = f"story beat {i + 1}"
    data["lines"] = lines
    if thematic:
        repair_script_captions(data)
        lines = data.get("lines") or lines
        enforce_concrete_beat_visuals(lines, pool=pool, vary_imagery=True)
        data["lines"] = lines
    if not data.get("monologue"):
        data["monologue"] = " ".join(
            str(r.get("text") or "") for r in lines if isinstance(r, dict)
        )
    print("[LOFI script] monologue:", data.get("monologue"))
    print("[LOFI script] hook:", (lines[0].get("text") if lines else ""))
    print("[LOFI script] arc:", data.get("arc_template"))
    print("[LOFI script] anchor:", data.get("anchor_object"))
    print("[LOFI script] structure:", data.get("structure_id"))
    print("[LOFI script] seed_hooks:", data.get("seed_hooks"))
    return data

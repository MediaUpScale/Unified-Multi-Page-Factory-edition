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
from core_engine.economic_reel_lofi.setting_archetypes import (
    assess_cross_run_settings,
    enforce_composition_variety,
    enforce_setting_archetype_variety,
    score_composition_types,
    score_setting_archetypes,
)
from core_engine.economic_reel_lofi.visual_identity import (
    DEFAULT_SETTING_OBJECT_PAIRS,
    act_for_index,
    apply_anchor_callback_beats,
    assign_beat_functions,
    assign_close_beat,
    beat_lacks_noun_and_action,
    enforce_concrete_beat_visuals,
    pick_caption_anchored_pair,
    visual_tied_to_caption,
    last_concrete_noun,
    pick_episode_anchor,
    preferred_concrete_noun,
    score_episode_variety,
    setting_object_pool,
    stamp_episode_anchor,
    _anchor_mentioned,
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
        best_i = -1
        best_n = 10**9
        for i in range(len(packed) - 1):
            trial = f"{packed[i]} {packed[i + 1]}".strip()
            if not _caption_fits(trial, max_words, max_chars):
                continue
            n = len(packed[i].split()) + len(packed[i + 1].split())
            if n < best_n:
                best_n = n
                best_i = i
        if best_i < 0:
            print(
                f"[LOFI caption] {len(packed)} beats after split "
                f"(cap {max_scenes}) — keeping extras; merge would exceed "
                f"{max_words}w/{max_chars}c"
            )
            break
        packed[best_i] = f"{packed[best_i]} {packed[best_i + 1]}".strip()
        del packed[best_i + 1]
        print(
            f"[LOFI caption] packed extra beats into scene {best_i + 1} "
            f"to stay within {max_scenes} scenes"
        )
    return packed


def trim_caption_to_cap(text: str, max_words: int, max_chars: int) -> str:
    """Drop a trailing clause, then trailing words, until the caption fits."""
    t = " ".join((text or "").split())
    if _caption_fits(t, max_words, max_chars):
        return t
    for sep in (", ", " — ", " – ", " - ", "; ", ": "):
        if sep not in t:
            continue
        head = t.rsplit(sep, 1)[0].rstrip(" ,;—–-:")
        if len(head.split()) >= 3 and _caption_fits(head, max_words, max_chars):
            return head
    words = t.split()
    while words and not _caption_fits(" ".join(words), max_words, max_chars):
        words.pop()
    return " ".join(words)


def _reflow_captions_to_budget(
    parts: list[str],
    max_scenes: int,
    max_words: int,
    max_chars: int,
) -> list[str]:
    """
    Fit spoken text into exactly ≤ max_scenes captions that each obey the cap.

    Drops leftover trailing words only when they cannot fit. Never emits an
    over-cap line.
    """
    words: list[str] = []
    for part in parts:
        words.extend((part or "").split())
    out: list[str] = []
    i = 0
    n_slots = max(1, int(max_scenes))
    for _ in range(n_slots):
        if i >= len(words):
            break
        chunk: list[str] = []
        while i < len(words):
            trial = chunk + [words[i]]
            blob = " ".join(trial)
            if len(trial) > max_words or len(blob) > max_chars:
                break
            chunk.append(words[i])
            i += 1
        if chunk:
            out.append(" ".join(chunk))
    if i < len(words):
        print(
            f"[LOFI caption] dropped {len(words) - i} trailing word(s) "
            f"to fit {n_slots} beats × {max_words}w/{max_chars}c"
        )
    return out or parts[:n_slots]


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
    min_scenes = int(getattr(lofi_cfg, "MIN_SCENES", 8))
    beat_texts = [
        str(r.get("text") or r.get("beat_text") or "").strip() for r in lines
    ]
    beats_fit = all(
        t and _caption_fits(t, max_words, max_chars) for t in beat_texts
    )
    # Writer captions that already obey the cap are the spoken script.
    # Do not expand them from a longer monologue — that produced 20-way
    # splits and dropped the payoff (57 trailing words) on forgiveness.
    if (
        not keep_extra_scenes
        and beats_fit
        and min_scenes <= len(lines) <= max_scenes
    ):
        script["monologue"] = " ".join(beat_texts)
        for i, row in enumerate(lines):
            row["scene"] = i + 1
            row["beat_text"] = beat_texts[i]
            row["text"] = beat_texts[i]
        script["lines"] = lines
        return script

    mono = str(script.get("monologue") or "").strip()
    concat = " ".join(beat_texts)
    concat_w = _words(concat)
    mono_w = _words(mono) if mono else []

    # Locked / sliced scripts only: restore tails from the monologue.
    # New writer output must not absorb a 130-word thesis into 9 captions.
    if keep_extra_scenes and mono_w and concat_w != mono_w:
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
    if len(packed) > max_scenes:
        packed = _reflow_captions_to_budget(
            packed, max_scenes, max_words, max_chars
        )
    fitted: list[str] = []
    for text in packed:
        if _caption_fits(text, max_words, max_chars):
            fitted.append(text)
            continue
        trimmed = trim_caption_to_cap(text, max_words, max_chars)
        if trimmed != text:
            print(
                f"[LOFI caption] trimmed over-cap line "
                f"{len(text.split())}w/{len(text)}c -> "
                f"{len(trimmed.split())}w/{len(trimmed)}c"
            )
        fitted.append(trimmed)
    packed = [t for t in fitted if t.strip()]
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
            "after clean-break split / cap fit"
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
    out = {
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
    if thematic:
        _finalize_thematic_narrative(out, pool, module=module, theme=theme)
        reasons = _narrative_gate_reasons(out, module)
        _log_narrative(out, reasons)
        out["holds_episode"] = bool(reasons)
        out["narrative_holds"] = reasons
    return out


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
    "Don't wait for the feeling first.",
    "You press the sweater to your chest.",
    "The kettle starts to warm.",
    "Stay with the quiet.",
    "Two cups sit by the sink.",
    "The empty chair faces the light.",
    "You pause in the doorway.",
    "The kettle has gone cold.",
    "You meet the morning there.",
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
    """Quality-bar example for every theme — never omitted (hope used to skip it)."""
    numbered = "\n".join(f"  {i+1}. {line}" for i, line in enumerate(_GOLD_CADENCE_BEATS))
    live = str(theme or "").strip().replace("_", " ") or "this theme"
    if str(module or "").strip().lower() == "parenting":
        domain = (
            "Parenting content required — this sample is relationship cadence only. "
            f"Write THIS {live} episode. Match split cadence, do not copy lines."
        )
    elif str(theme or "").strip().lower() == "hope":
        domain = (
            "Write THIS hope episode with original lines. Match the quality of "
            "the split and the thesis shape. Do not copy the sample wording."
        )
    else:
        domain = f"Write THIS {live} episode, not hope. Match cadence; do not copy."
    return f"""
QUALITY BAR (cadence and thesis shape only — do NOT copy lines, images, or the sample nouns):
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
Before the captions, write one internal thesis sentence. Every beat must serve it.
VOICE: a story with ONE sharp lesson — not a list of instructions.
ONLY the hook (scene 1) and the insight beat(s) are stated principles
("Don't finish the argument first." / "Stay after the heat.").
The other 7 beats are concrete narrative/sensory description of what the
character is doing or what is in front of them — image-first
("You press the sweater to your chest." / "Two plates wait in the sink.").
At most TWO lines in the whole script may read as generalizable advice.
Not a numbered "you should / remember to" list. Not a book-plug outro.
ONE recurring physical object (anchor_object) is REQUIRED — introduced early/mid
and paid off late in a different state. Other beats may use different imagery.

Use THIS structure only (abstract shape — invent every sentence; never paste a known reel):
{structure.get("example")}

RULES:
1) Write "monologue" as the full spoken thesis AND keep it short enough to
   speak in {max_scenes} captions. HARD MAX {max_scenes * max_words} words
   in the monologue (and in the concatenated "text" fields). Split THAT
   SAME text across {min_scenes}–{max_scenes} "text" fields. Prefer exactly
   {scene_count} beats. Never more than {max_scenes}.
2) Caption chunks stay short: 4–8 words preferred, HARD MAX {max_words} words
   AND {max_chars} characters. Count both before writing each "text".
   If ONE beat is over cap, shorten THAT beat only (drop a trailing clause).
   Do not rewrite the whole episode. If a sentence is longer, split it
   across the NEXT beat at a comma, dash, or clause boundary — but never
   exceed {max_scenes} beats total. If you are already at {max_scenes},
   shorten the line instead of packing two clauses into one beat.
   BOUNDARY EXAMPLES (copy the LENGTH, not the words):
     OK 7w/38c: "Peace can feel boring after chaos."
     OK 9w/47c: "You learn to read the silence like a language."
     OK 9w/56c: "You set the stone down. You walked away."
     OK 6w/32c: "The quiet just stays quiet."
     TOO LONG (do not emit): "What if peace isn't the absence of the storm? You watch the kettle,"
     TOO LONG (do not emit): "Forgiveness isn't an eraser. It's the decision to stop carrying"
3) One longer thematic sentence MAY span 2 consecutive beats. Fragments are
   allowed. Not every beat must be a complete closed sentence.
4) Scene 1 is the hook matching HOOK_TYPE AND a stated principle
   (imperative or declarative-general). This is one of at most TWO
   principle lines in the script.
   - bold_claim: a hard opening principle
   - question: a short question that is still general advice
   - statistic: a specific number/claim (parenting ok; invent only if grounded)
   - definition / rhetorical_question: also allowed
5) Last 2–3 beats are the insight/payoff of the SAME theme.
   Every insight line MUST be a principle AND pass the character-agency
   verb gate. All other beats (complication / turn / close) MUST be
   scene-grounded narration — if they pass the principle-check, rewrite
   them. "You press the sweater to your chest." is correct for a
   non-insight beat. "Leave the kettle. Start anyway." belongs only on
   hook or insight.
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
      Also emit composition_type: figure_centered | object_focus |
      wide_environment. figure_centered (human-scale silhouette or
      portrait as the visual anchor) is capped at 4 of 9. The other 5
      must be object_focus (no figure) or wide_environment (landscape /
      space at scale, figure small or absent). Scene 1 must be a
      character (figure_centered), never object_focus.
      Use object_focus on later beats when the stand-in is a strong
      specific object. Use couple on at most 3 beats (emotional peaks).
    - subject_expression: 1–3 words
    - setting + key_object MUST trace to THIS beat's idea: the noun in
      the line, or a meaning stand-in (chaos→unmade empty bed / suitcase on
      the floor; walking from a fight→open doorway; cost→empty platform;
      peace/quiet→rain on the window or kettle).
      Never default every abstract line to a chair.
      Copy from SETTING_OBJECT_POOL when a pair matches the idea; otherwise
      name the stand-in. Each pool row is tagged with a setting ARCHETYPE.
      At most 2 of the 9 beats may share the same archetype
      (interior-domestic, threshold, exterior-urban, exterior-landscape,
      transit, public-interior). Spread the episode across at least 5
      archetypes. Prefer archetypes listed as REQUIRED_NEW below.
      Do not reuse the same key_object more than once.
      Never pick a small object meant to be held near the face.
      If key_object is a part of a larger whole (passenger seat, steering
      wheel, dashboard, piano key, drawer, sofa cushion, place setting),
      the phrase MUST name that whole — e.g. "empty passenger seat of a car,
      seen through the open car door", never a disembodied part alone.
    - time_of_day: dawn | morning | afternoon | dusk | night | evening
    - beat_text: same as text
    - visual_anchor_hint: 8–16 words. A visual paraphrase of this beat's
      key_object in its setting — not the caption itself. Must name the
      key_object. Never substitute a different still-life (no mug, vase,
      flowers, or coffee when the object is rain, a seat, a suitcase, etc.).
    - beat_function: exactly one of hook | complication | turn | insight | close.
      Consecutive beats must not share the same beat_function. The script MUST
      include at least one turn or insight (not hook restatement only).
      Every insight line must hand the viewer a concrete reframe, permission,
      or action: you (the character) as grammatical subject of a finite
      physical or decisive verb — walk, set down, stay, leave, open, close,
      turn, carry, reach, hold, let go, wear, fold — in ANY construction
      ("you walk with it", "carry it", "set it down"), in that beat or the
      one immediately before it. Copular/state verbs alone (is, was,
      becomes), personification of an abstract noun ("the morning finds its
      way", "hope is a habit"), and cognitive verbs with no physical
      correlate (learn, realize, understand, know) are a hold.
    - shot_scale: medium by default. A close beat is OPTIONAL, only on a
      turn or insight if the arc needs one — never required. If used, set
      close_variant to silhouette | portrait_close | eye_close.
    Scene 1 (hook) MUST show a character — woman, man, couple, or
    silhouette — in a high-contrast, emotionally charged composition
    (sunset, rain, lamp glow, dawn backlight). No object-only still life
    and no empty establishing shot on beat 1. This is the highest-priority
    visual beat.
12) thesis (string, REQUIRED, internal only — never spoken on screen): one
    sentence the 9 beats develop.
    anchor_object (REQUIRED): pick ONE concrete object from SETTING_OBJECT_POOL
    or the DETAIL BANK. name / initial_state / final_state required.
    That object MUST be named in at least 2 spoken beats: introduce early/mid
    (a different state) and callback on the turn, insight, or last beats
    (same object, changed state — e.g. kettle just clicked off → kettle cold).
    Close beats are optional. If one is used, put it on the turn or insight
    (not the hook, not the last close). Available types: silhouette close
    (hands / shoulders / nape / object), portrait_close (recurring woman or
    man, painterly face/body), or eye_close. Do not force a close.
13) Every line must be original to this theme. If a sentence could belong to a
    famous relationship/parenting reel, rewrite it.
    Intra-script: no two captions may be the same or near-duplicates.
    "X isn't Y" / "X is not Y" may appear in at most ONE line.
14) STRUCTURAL VARIETY: do not open with "the saddest thing X can believe is...".
    Do not reuse the same rhetorical frame as another episode in this batch.

Output STRICT JSON only, no markdown:
{{
  "hook_type": "{hook_type}",
  "theme": "{theme}",
  "module": "{module}",
  "arc_template": "{arc.get("id")}",
  "structure_id": "{structure.get("id")}",
  "thesis": "<one internal sentence this episode proves — not shown on screen>",
  "anchor_object": {{
    "name": "<object from the pool or detail bank>",
    "initial_state": "<how it first appears>",
    "final_state": "<how it has changed by the callback>"
  }},
  "monologue": "<full continuous spoken thesis>",
  "lines": [
    {{
      "scene": 1,
      "text": "Peace can feel boring after chaos.",
      "beat_text": "Peace can feel boring after chaos.",
      "emotion": "opening_hook",
      "arc_position": "act1",
      "beat_function": "hook",
      "shot_scale": "medium",
      "subject_type": "woman",
      "subject_expression": "distant, still",
      "setting": "bedroom, edge of a neatly made bed",
      "key_object": "untouched pillow",
      "time_of_day": "dusk",
      "visual_anchor_hint": "untouched pillow on an empty half of the bed"
    }}
  ]
}}
Valid JSON only: no trailing commas, no comments, escape quotes inside strings.
No NSFW. No real private individuals named. Brand-safe for {module}.
"""


_ISNT_Y_RE = re.compile(
    r"\b(?:isn['’]t|is not|wasn['’]t|was not)\b",
    re.I,
)
_INSIGHT_CLAUSE_SPLIT_RE = re.compile(
    r"\s*[,;:—–]\s*|\s+\band\b\s+|\s+\bbut\b\s+|\s+\bthen\b\s+",
    re.I,
)
_INSIGHT_LEAD_STRIP_RE = re.compile(
    r"^(so|and|but|then|yet|still|now|or)\s+",
    re.I,
)
_INSIGHT_CHAR_SUBJ = frozenset({"you", "i", "we", "she", "he"})
_INSIGHT_ABSTRACT_SUBJ = frozenset(
    {
        "morning", "hope", "grief", "peace", "love", "rain", "night",
        "silence", "distance", "habit", "weight", "absence", "presence",
        "day", "dusk", "dawn", "evening", "light", "dark", "time",
        "feeling", "memory", "sorrow", "joy", "pain", "loss", "habit",
        "way", "shape", "hole", "address", "surrender", "beginning",
    }
)
_INSIGHT_CONCRETE = frozenset(
    {
        "walk", "set", "stay", "leave", "open", "close", "turn", "carry",
        "reach", "hold", "fold", "refold", "unfold", "wear", "press",
        "gather", "lift", "put", "pick", "sit", "stand", "go", "come",
        "take", "keep", "stop", "choose", "let", "drop", "hang", "pull",
        "push", "wait", "move", "step", "give", "place", "lay", "shut",
        "lock", "unlock", "pour", "fill", "empty", "touch", "grip",
        "clutch", "wrap", "unwrap", "throw", "catch", "run", "drive",
        "write", "cut", "tear", "wash", "wake", "eat", "drink", "shift",
        "draw", "call", "ask", "tell",
    }
)
_INSIGHT_COGNITIVE = frozenset(
    {
        "learn", "realize", "realise", "understand", "know", "notice",
        "think", "believe", "remember", "forget", "imagine", "wonder",
        "recognize", "recognise", "discover", "see", "feel", "sense",
        "find",
    }
)
_INSIGHT_COPULAR = frozenset(
    {
        "be", "become", "seem", "appear",
    }
)
_INSIGHT_BORDERLINE = frozenset(
    {
        "try", "begin", "start", "forgive", "enough",
    }
)
_INSIGHT_MODAL = frozenset(
    {
        "can", "could", "will", "would", "may", "might", "must",
        "shall", "should", "do", "did",
    }
)
_INSIGHT_IRREGULAR = {
    "am": "be", "is": "be", "are": "be", "was": "be", "were": "be",
    "been": "be", "being": "be",
    "went": "go", "gone": "go", "goes": "go",
    "came": "come", "comes": "come",
    "wore": "wear", "worn": "wear", "wears": "wear",
    "held": "hold", "holds": "hold",
    "left": "leave", "leaves": "leave",
    "sat": "sit", "sits": "sit",
    "stood": "stand", "stands": "stand",
    "took": "take", "takes": "take", "taken": "take",
    "got": "get", "gets": "get",
    "put": "put", "puts": "put",
    "set": "set", "sets": "set",
    "let": "let", "lets": "let",
    "kept": "keep", "keeps": "keep",
    "felt": "feel", "feels": "feel",
    "found": "find", "finds": "find",
    "became": "become", "becomes": "become",
    "chose": "choose", "chooses": "choose",
    "knew": "know", "knows": "know",
    "thought": "think", "thinks": "think",
    "saw": "see", "sees": "see",
    "laid": "lay", "lays": "lay",
    "lied": "lie", "lies": "lie",
    "ran": "run", "runs": "run",
    "gave": "give", "gives": "give", "given": "give",
    "made": "make", "makes": "make",
}
_NARRATIVE_REWRITE_ALTS: tuple[str, ...] = (
    "You notice the {noun} anyway.",
    "The {noun} is still there.",
    "Nobody moved the {noun}.",
    "The {noun} has gone cold.",
    "You leave the {noun} untouched.",
)


def _line_texts(lines: list[Any]) -> list[str]:
    out: list[str] = []
    for row in lines or []:
        if isinstance(row, dict):
            out.append(str(row.get("text") or row.get("beat_text") or "").strip())
        else:
            out.append("")
    return out


def _normalize_anchor_object(data: dict[str, Any], pool: list[dict[str, str]]) -> dict[str, str]:
    raw = data.get("anchor_object")
    name = ""
    initial = ""
    final = ""
    if isinstance(raw, dict):
        name = str(raw.get("name") or raw.get("key_object") or "").strip()
        initial = str(raw.get("initial_state") or "").strip()
        final = str(raw.get("final_state") or "").strip()
    elif isinstance(raw, str):
        name = raw.strip()
    avoid = set(rag.recent_used_anchors(str(data.get("module") or "relationship")))
    picked = pick_episode_anchor(pool, avoid_stems=avoid, prefer_name=name)
    name = str(picked.get("key_object") or name or "empty chair").strip()
    data["anchor_object"] = {
        "name": name,
        "initial_state": initial or "just set down",
        "final_state": final or "cold, untouched",
        "setting": str(picked.get("setting") or ""),
    }
    return data["anchor_object"]


def _rewrite_caption(index: int, noun: str) -> str:
    tmpl = _NARRATIVE_REWRITE_ALTS[index % len(_NARRATIVE_REWRITE_ALTS)]
    word = noun or "chair"
    line = tmpl.format(noun=word)
    if word.endswith("s") and " has " in line:
        line = line.replace(" has ", " have ", 1)
    return line


def _retie_beat_visuals(lines: list[dict[str, Any]], pool: list[dict[str, str]]) -> None:
    """After caption rewrites, keep setting/object tied so the validator can pass."""
    used_stems: dict[str, int] = {}
    used: set[tuple[str, str]] = set()
    for i, row in enumerate(lines):
        if not isinstance(row, dict):
            continue
        text = str(row.get("text") or row.get("beat_text") or "")
        setting = str(row.get("setting") or "")
        obj = str(row.get("key_object") or "")
        if str(row.get("anchor_beat") or "").strip():
            continue
        if str(row.get("close_variant") or "") == "eye_close":
            continue
        if visual_tied_to_caption(text, setting, obj):
            used.add((setting.lower(), obj.lower()))
            stem = (obj.split() or [""])[-1].lower()
            used_stems[stem] = used_stems.get(stem, 0) + 1
            continue
        pick = pick_caption_anchored_pair(
            text, pool, used=used, index=i, used_stems=used_stems
        )
        row["setting"] = pick["setting"]
        row["key_object"] = pick["key_object"]
        row["visual_fallback"] = "retie_after_rewrite"
        used.add((pick["setting"].lower(), pick["key_object"].lower()))
        print(
            f"[LOFI narrative] retie scene={i + 1} "
            f"-> {pick['setting']!r} / {pick['key_object']!r}"
        )


def _cap_isnt_y_lines(lines: list[dict[str, Any]], noun: str) -> int:
    """Keep at most one 'X isn't Y' line; rewrite later extras. Returns kept count."""
    kept = 0
    for i, row in enumerate(lines):
        if not isinstance(row, dict):
            continue
        text = str(row.get("text") or row.get("beat_text") or "")
        if not _ISNT_Y_RE.search(text):
            continue
        kept += 1
        if kept <= 1:
            continue
        repl = _rewrite_caption(i, noun)
        row["text"] = repl
        row["beat_text"] = repl
        row["isnt_y_rewrite"] = True
        print(f"[LOFI narrative] isnt-Y rewrite scene={i + 1} -> {repl!r}")
        kept -= 1
    return sum(
        1
        for row in lines
        if isinstance(row, dict)
        and _ISNT_Y_RE.search(str(row.get("text") or row.get("beat_text") or ""))
    )


def _rewrite_intra_dupes(lines: list[dict[str, Any]], noun: str) -> list[tuple[int, int, float]]:
    leftover = rag.intra_script_duplicate_pairs(lines)
    for _a, b, score in leftover:
        idx = b - 1
        if idx < 0 or idx >= len(lines) or not isinstance(lines[idx], dict):
            continue
        repl = _rewrite_caption(idx, noun)
        # Avoid rewriting into another existing caption
        existing = {rag.normalize_caption_text(t) for t in _line_texts(lines)}
        if rag.normalize_caption_text(repl) in existing:
            repl = f"The {noun} waits unused."
        lines[idx]["text"] = repl
        lines[idx]["beat_text"] = repl
        lines[idx]["intra_dupe_rewrite"] = True
        print(
            f"[LOFI narrative] intra-dupe rewrite scene={b} "
            f"score={score:.2f} -> {repl!r}"
        )
    return rag.intra_script_duplicate_pairs(lines)


def _rewrite_cross_run_phrases(
    data: dict[str, Any],
    module: str,
    noun: str,
) -> list[str]:
    hits = rag.cross_run_phrase_hits(module, data)
    if not hits:
        return []
    lines = [r for r in (data.get("lines") or []) if isinstance(r, dict)]
    used = rag.recent_used_phrases(module)
    for i, row in enumerate(lines):
        if i == 0 or str(row.get("beat_function") or "") in {"hook", "insight"}:
            continue
        text = str(row.get("text") or "")
        phrases = rag.phrases_from_text(text)
        if not (phrases & used):
            continue
        repl = _rewrite_caption(i + 3, noun)
        row["text"] = repl
        row["beat_text"] = repl
        row["cross_run_rewrite"] = True
        print(f"[LOFI narrative] cross-run rewrite scene={i + 1} -> {repl!r}")
    leftover = rag.cross_run_phrase_hits(module, data)
    if leftover and lines:
        target = None
        for i, row in enumerate(lines):
            fn = str(row.get("beat_function") or "")
            if i == 0 or fn in {"hook", "insight"}:
                continue
            target = i
        if target is not None:
            repl = _rewrite_caption(target + 3, noun)
            lines[target]["text"] = repl
            lines[target]["beat_text"] = repl
            lines[target]["cross_run_rewrite"] = True
            print(
                f"[LOFI narrative] cross-run rewrite scene={target + 1} "
                f"(kept hook/insight) -> {repl!r}"
            )
    # Anchor noun reuse: swap to a different pool object if still colliding
    ao = data.get("anchor_object") if isinstance(data.get("anchor_object"), dict) else {}
    used_anchors = rag.recent_used_anchors(module)
    name = str(ao.get("name") or "")
    tokens = {name.lower()} | {w for w in re.findall(r"[a-z]+", name.lower()) if len(w) >= 3}
    if tokens & used_anchors:
        pool = setting_object_pool(None)
        fresh = pick_episode_anchor(pool, avoid_stems=used_anchors)
        data["anchor_object"] = {
            "name": fresh["key_object"],
            "initial_state": str(ao.get("initial_state") or "just set down"),
            "final_state": str(ao.get("final_state") or "cold, untouched"),
            "setting": fresh.get("setting") or "",
        }
        print(f"[LOFI narrative] anchor rotated off history -> {fresh['key_object']!r}")
    return rag.cross_run_phrase_hits(module, data)


def _ensure_thesis(data: dict[str, Any], theme: str) -> str:
    thesis = str(data.get("thesis") or "").strip()
    if thesis:
        return thesis
    lines = _line_texts(data.get("lines") or [])
    first = lines[0] if lines else ""
    label = (theme or "this").replace("_", " ")
    thesis = first or f"{label.capitalize()} costs something before it stays."
    data["thesis"] = thesis
    return thesis


def _finalize_thematic_narrative(
    data: dict[str, Any],
    pool: list[dict[str, str]],
    *,
    module: str,
    theme: str,
) -> dict[str, Any]:
    """Thesis, mandatory anchor+callback, close beat, dedup, isn't-Y cap."""
    lines = [r for r in (data.get("lines") or []) if isinstance(r, dict)]
    if lines and isinstance(lines[0], dict):
        lines[0]["episode_theme"] = theme
        lines[0]["episode_module"] = module
    ao = _normalize_anchor_object(data, pool)
    noun = preferred_concrete_noun(ao.get("name") or "") or (
        (ao.get("name") or "chair").split()[-1]
    )
    stamp_episode_anchor(lines, ao["name"])
    _ensure_thesis(data, theme)
    repair_script_captions(data)
    lines = [r for r in (data.get("lines") or []) if isinstance(r, dict)]
    if lines and isinstance(lines[0], dict):
        lines[0]["episode_theme"] = theme
        lines[0]["episode_module"] = module
        stamp_episode_anchor(lines, ao["name"])
    enforce_concrete_beat_visuals(lines, pool=pool, vary_imagery=True)
    apply_anchor_callback_beats(lines, ao, pool)
    assign_beat_functions(lines)
    assign_close_beat(lines, theme=theme, module=module)
    _cap_isnt_y_lines(lines, noun)
    _rewrite_intra_dupes(lines, noun)
    _rewrite_cross_run_phrases(data, module, noun)
    # Re-apply after rewrites so callback/close tags survive caption edits
    apply_anchor_callback_beats(lines, data.get("anchor_object") or ao, pool)
    assign_beat_functions(lines)
    assign_close_beat(lines, theme=theme, module=module)
    _retie_beat_visuals(lines, pool)
    prev_arch = rag.preceding_setting_archetypes(module)
    from core_engine.economic_reel_lofi.setting_archetypes import ARCHETYPE_ORDER

    prefer_new = [a for a in ARCHETYPE_ORDER if a not in set(prev_arch)]
    enforce_setting_archetype_variety(
        lines, pool, theme=theme, prefer_new=prefer_new
    )
    enforce_composition_variety(lines)
    rag.stamp_close_variant_on_script(data, lines)
    data["lines"] = lines
    data["monologue"] = " ".join(_line_texts(lines))
    if not data.get("monologue"):
        data["monologue"] = " ".join(_line_texts(lines))
    return data


def _insight_lemma(word: str) -> str:
    w = re.sub(r"[^a-z]", "", (word or "").lower())
    if not w:
        return ""
    if w in _INSIGHT_IRREGULAR:
        return _INSIGHT_IRREGULAR[w]
    if (
        w in _INSIGHT_CONCRETE
        or w in _INSIGHT_COGNITIVE
        or w in _INSIGHT_COPULAR
        or w in _INSIGHT_BORDERLINE
        or w in _INSIGHT_MODAL
        or w in _INSIGHT_CHAR_SUBJ
        or w in _INSIGHT_ABSTRACT_SUBJ
    ):
        return w
    for suf, repl in (("ies", "y"), ("ied", "y")):
        if w.endswith(suf) and len(w) > 4:
            return w[: -len(suf)] + repl
    for suf in ("ing", "ed", "es", "s"):
        if w.endswith(suf) and len(w) > len(suf) + 2:
            return w[: -len(suf)]
    return w


def _insight_expand_contractions(text: str) -> str:
    blob = str(text or "")
    repl = (
        (r"\bthat's\b", "that is"),
        (r"\bit's\b", "it is"),
        (r"\bthere's\b", "there is"),
        (r"\byou're\b", "you are"),
        (r"\bi'm\b", "i am"),
        (r"\bwe're\b", "we are"),
        (r"\bshe's\b", "she is"),
        (r"\bhe's\b", "he is"),
        (r"\bdon't\b", "do not"),
        (r"\bdoesn't\b", "does not"),
        (r"\bcan't\b", "can not"),
        (r"\bwon't\b", "will not"),
        (r"\blet's\b", "let us"),
        (r"\bisn't\b", "is not"),
        (r"\bwasn't\b", "was not"),
    )
    for pat, to in repl:
        blob = re.sub(pat, to, blob, flags=re.I)
    return blob


def _insight_clauses(text: str) -> list[str]:
    blob = _insight_expand_contractions(text)
    return [p.strip() for p in _INSIGHT_CLAUSE_SPLIT_RE.split(blob) if p.strip()]


def _analyze_insight_clause(clause: str) -> dict[str, str]:
    raw = _INSIGHT_LEAD_STRIP_RE.sub("", clause.strip())
    tokens = re.findall(r"[A-Za-z']+", raw)
    lemmas = [_insight_lemma(t) for t in tokens]
    lemmas = [x for x in lemmas if x]
    if not lemmas:
        return {"status": "fail", "reason": "empty-clause"}

    subject = ""
    subject_kind = ""
    verb_i = 0
    char_at = next((i for i, tok in enumerate(lemmas) if tok in _INSIGHT_CHAR_SUBJ), -1)
    if lemmas[0] in _INSIGHT_CHAR_SUBJ:
        subject = lemmas[0]
        subject_kind = "character"
        verb_i = 1
    elif char_at >= 0:
        # "just before you ask" — character subject is not clause-initial.
        subject = lemmas[char_at]
        subject_kind = "character"
        verb_i = char_at + 1
    elif lemmas[0] in {"the", "a", "an"} and len(lemmas) > 1 and lemmas[1] in _INSIGHT_ABSTRACT_SUBJ:
        subject = lemmas[1]
        subject_kind = "abstract"
        verb_i = 2
    elif lemmas[0] in _INSIGHT_ABSTRACT_SUBJ:
        subject = lemmas[0]
        subject_kind = "abstract"
        verb_i = 1
    elif lemmas[0] in {"the", "a", "an"} and len(lemmas) > 1:
        subject = lemmas[1]
        subject_kind = "noncharacter"
        verb_i = 2
    elif lemmas[0] in {"it", "that", "this", "there"}:
        subject = lemmas[0]
        subject_kind = "noncharacter"
        verb_i = 1
    elif lemmas[0] in _INSIGHT_CONCRETE or lemmas[0] in _INSIGHT_BORDERLINE:
        subject = "you"
        subject_kind = "character"
        verb_i = 0
    else:
        subject_kind = "unknown"
        verb_i = 0

    # Skip noun tokens until a verb-like lemma; allow "get to <verb>".
    i = verb_i
    while i < len(lemmas) and lemmas[i] in {"the", "a", "an", "to", "not", "no"}:
        i += 1
    if i < len(lemmas) and lemmas[i] == "get":
        if i + 1 < len(lemmas) and lemmas[i + 1] == "to":
            i += 2
        elif subject_kind == "character":
            return {
                "status": "pass",
                "reason": f"{subject}+get",
                "subject": subject,
                "verb": "get",
            }
    if i < len(lemmas) and lemmas[i] in _INSIGHT_MODAL:
        i += 1
        while i < len(lemmas) and lemmas[i] in {"to", "not"}:
            i += 1

    verb = lemmas[i] if i < len(lemmas) else ""
    rest = lemmas[i:]
    has_concrete = any(v in _INSIGHT_CONCRETE for v in rest)
    has_cognitive = any(v in _INSIGHT_COGNITIVE for v in rest)
    has_copular = any(v in _INSIGHT_COPULAR for v in rest)
    has_border = any(v in _INSIGHT_BORDERLINE for v in rest)

    if subject_kind == "abstract":
        return {
            "status": "fail",
            "reason": f"personification:{subject}+{verb or 'state'}",
            "subject": subject,
            "verb": verb,
        }
    if subject_kind == "noncharacter":
        kind = "copular" if has_copular else "noncharacter-subject"
        return {
            "status": "fail",
            "reason": f"{kind}:{subject}+{verb or 'state'}",
            "subject": subject,
            "verb": verb,
        }
    if subject_kind == "character" and has_concrete:
        concrete = next(v for v in rest if v in _INSIGHT_CONCRETE)
        return {
            "status": "pass",
            "reason": f"{subject}+{concrete}",
            "subject": subject,
            "verb": concrete,
        }
    if subject_kind == "character" and has_cognitive and not has_concrete:
        cog = next(v for v in rest if v in _INSIGHT_COGNITIVE)
        return {
            "status": "fail",
            "reason": f"cognitive-only:{cog}",
            "subject": subject,
            "verb": cog,
        }
    if subject_kind == "character" and has_copular and not has_concrete:
        return {
            "status": "fail",
            "reason": f"copular:{subject}+{verb or 'be'}",
            "subject": subject,
            "verb": verb or "be",
        }
    if subject_kind == "character" and has_border and not has_concrete:
        border = next(v for v in rest if v in _INSIGHT_BORDERLINE)
        return {
            "status": "borderline",
            "reason": f"{subject}+{border}",
            "subject": subject,
            "verb": border,
        }
    if has_concrete and subject_kind in {"unknown", "noncharacter"}:
        # Bare infinitive / implied-you fragment: "to put down", "to carry".
        concrete = next(v for v in rest if v in _INSIGHT_CONCRETE)
        return {
            "status": "borderline",
            "reason": f"infinitive:{concrete}",
            "subject": subject,
            "verb": concrete,
        }
    return {
        "status": "fail",
        "reason": f"no-character-concrete:{subject_kind}:{verb or lemmas[0]}",
        "subject": subject,
        "verb": verb,
    }


def analyze_insight_agency(text: str) -> dict[str, str]:
    """Verb-role check: character subject + concrete/decisive verb."""
    blob = str(text or "").strip()
    if not blob:
        return {"status": "fail", "reason": "empty", "subject": "", "verb": ""}
    best: dict[str, str] | None = None
    rank = {"pass": 2, "borderline": 1, "fail": 0}
    for clause in _insight_clauses(blob):
        rec = _analyze_insight_clause(clause)
        if best is None or rank.get(rec["status"], 0) > rank.get(best["status"], 0):
            best = rec
        if rec.get("status") == "pass":
            return rec
    return best or {"status": "fail", "reason": "empty-clause", "subject": "", "verb": ""}


def insight_agency_status(text: str) -> str:
    """pass | borderline | fail — character performs a concrete verb."""
    return str(analyze_insight_agency(text).get("status") or "fail")


def assess_insight_lines(lines: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Content check for beats tagged insight.

    Pass if this beat or the one immediately before it has a character
    subject performing a concrete physical/decisive verb. Borderline is
    logged, not a hold. Copular / cognitive-only / personification is a hold.
    """
    report: list[dict[str, Any]] = []
    fails: list[str] = []
    for i, row in enumerate(lines or []):
        if not isinstance(row, dict):
            continue
        if str(row.get("beat_function") or "") != "insight":
            continue
        text = str(row.get("text") or row.get("beat_text") or "")
        prev = ""
        if i > 0 and isinstance(lines[i - 1], dict):
            prev = str(lines[i - 1].get("text") or lines[i - 1].get("beat_text") or "")
        here = analyze_insight_agency(text)
        before = analyze_insight_agency(prev) if prev else {"status": "fail", "reason": "no-prev"}
        # Previous beat may rescue a fragment/borderline split, not a
        # hard fail (cognitive-only, copular, personification).
        if here["status"] == "pass":
            status = "pass"
            why = here["reason"]
        elif here["status"] == "borderline" and before["status"] == "pass":
            status = "pass"
            why = f"prev:{before['reason']}"
        elif here["status"] == "borderline":
            status = "borderline"
            why = here["reason"]
        else:
            status = "fail"
            why = here.get("reason") or "no-character-concrete"
        rec = {
            "scene": i + 1,
            "text": text,
            "prev": prev,
            "status": status,
            "reason": why,
            "here": here,
            "before": before,
        }
        report.append(rec)
        if status == "borderline":
            print(
                f"[LOFI insight] BORDERLINE scene={i + 1} "
                f"line={text!r} why={why}"
            )
        elif status == "fail":
            fails.append(
                f"insight scene {i + 1} has no character-performed concrete verb "
                f"({why}): {text!r}"
            )
            print(f"[LOFI insight] FAIL scene={i + 1} line={text!r} why={why}")
        else:
            print(f"[LOFI insight] PASS scene={i + 1} line={text!r} why={why}")
    return {"insights": report, "fails": fails}


_PRINCIPLE_LEAD_RE = re.compile(
    r"^(let|don't|do not|never|always|stop|start|keep|wait|say|ask|leave|"
    r"stay|hold|walk|put|set|tell|call|come|go|open|close|give|take|"
    r"choose|try|make|use|drop|pick|turn|send|write|listen|speak|"
    r"repair|forgive|stay|return)\b",
    re.I,
)
_PRINCIPLE_FRAME_RE = re.compile(
    r"\b("
    r"don't have to|do not have to|doesn't have to|just have to|"
    r"doesn't need|does not need|get to|happens when|is what|"
    r"isn't|is not|when you|if you|"
    r"before you|after you|the part that counts"
    r")\b",
    re.I,
)
_SCENE_NARRATION_RE = re.compile(
    r"\b("
    r"wake at dawn|bracing|you sit|you stand|you watch|you stare|"
    r"you wake|you find|you notice|the fight was|you press|"
    r"you leave the|sitting cold|bracing for"
    r")\b",
    re.I,
)
_PAST_SCENE_RE = re.compile(
    r"\b(was|were|had|woke|found|sat|stood|watched|said)\b",
    re.I,
)


def analyze_principle_voice(text: str) -> dict[str, str]:
    """
    Quotable behavioral principle, not this-character scene narration.

    Separate from the agency-verb gate. A concrete verb can still fail here.
    """
    blob = str(text or "").strip()
    if not blob:
        return {"status": "fail", "reason": "empty", "kind": ""}
    lead = blob.split(".", 1)[0].strip()
    if _PRINCIPLE_LEAD_RE.search(lead):
        return {"status": "pass", "reason": "imperative", "kind": "imperative"}
    if _PRINCIPLE_FRAME_RE.search(blob):
        return {
            "status": "pass",
            "reason": "declarative-general",
            "kind": "declarative",
        }
    if _SCENE_NARRATION_RE.search(blob):
        return {
            "status": "fail",
            "reason": "scene-narration",
            "kind": "scene",
        }
    if _PAST_SCENE_RE.search(blob) and not _PRINCIPLE_FRAME_RE.search(blob):
        return {
            "status": "fail",
            "reason": "past-scene",
            "kind": "scene",
        }
    # Bare second-person present with no general frame = this-moment report.
    if re.match(r"^you\b", blob, re.I) and not _PRINCIPLE_FRAME_RE.search(blob):
        return {
            "status": "fail",
            "reason": "this-moment-you",
            "kind": "scene",
        }
    # Abstract claim the viewer can quote ("Peace can feel boring after chaos.").
    if not re.match(
        r"^(you|i|we|the fight|the kettle|the bed|the body)\b",
        blob,
        re.I,
    ) and re.search(
        r"\b(is|isn't|is not|can|matters|holds|starts|stays|means)\b",
        blob,
        re.I,
    ):
        return {
            "status": "pass",
            "reason": "declarative-general",
            "kind": "declarative",
        }
    return {
        "status": "fail",
        "reason": "not-quotable-advice",
        "kind": "other",
    }


def assess_principle_lines(lines: list[dict[str, Any]]) -> dict[str, Any]:
    """Hook + every insight must pass the principle-voice check."""
    report: list[dict[str, Any]] = []
    fails: list[str] = []
    for i, row in enumerate(lines or []):
        if not isinstance(row, dict):
            continue
        fn = str(row.get("beat_function") or "").strip().lower()
        if fn not in {"hook", "insight"} and i != 0:
            continue
        if i == 0:
            fn = "hook"
        text = str(row.get("text") or row.get("beat_text") or "")
        rec = analyze_principle_voice(text)
        item = {
            "scene": i + 1,
            "beat_function": fn,
            "text": text,
            "status": rec.get("status"),
            "reason": rec.get("reason"),
            "kind": rec.get("kind"),
        }
        report.append(item)
        if rec.get("status") == "pass":
            print(
                f"[LOFI principle] PASS scene={i + 1} fn={fn} "
                f"why={rec.get('reason')} line={text!r}"
            )
        else:
            fails.append(
                f"principle {fn} scene {i + 1} ({rec.get('reason')}): {text!r}"
            )
            print(
                f"[LOFI principle] FAIL scene={i + 1} fn={fn} "
                f"why={rec.get('reason')} line={text!r}"
            )
    return {"lines": report, "fails": fails}


MAX_PRINCIPLE_BEATS = 2


def assess_principle_scope(lines: list[dict[str, Any]]) -> dict[str, Any]:
    """Hold if 3+ beats read as generalizable advice. Privilege is hook+insight."""
    hits: list[dict[str, Any]] = []
    for i, row in enumerate(lines or []):
        if not isinstance(row, dict):
            continue
        text = str(row.get("text") or row.get("beat_text") or "")
        rec = analyze_principle_voice(text)
        if rec.get("status") != "pass":
            continue
        fn = str(row.get("beat_function") or "")
        hits.append(
            {
                "scene": i + 1,
                "beat_function": fn,
                "text": text,
                "reason": rec.get("reason"),
            }
        )
    extra = [
        h
        for h in hits
        if h.get("beat_function") not in {"hook", "insight"} and h.get("scene") != 1
    ]
    over = len(hits) > MAX_PRINCIPLE_BEATS
    fails: list[str] = []
    if over:
        fails.append(
            f"principle-scope: {len(hits)} beats read as advice "
            f"(max {MAX_PRINCIPLE_BEATS}): "
            + "; ".join(f"s{h['scene']} {h['text']!r}" for h in hits)
        )
        print(f"[LOFI principle] SCOPE FAIL count={len(hits)} extra={extra}")
    else:
        print(
            f"[LOFI principle] SCOPE pass count={len(hits)} "
            f"scenes={[h['scene'] for h in hits]}"
        )
    return {"hits": hits, "count": len(hits), "fails": fails}


_HOOK_LIGHT_RE = re.compile(
    r"\b("
    r"sunset|sunrise|dusk|dawn|rain|storm|lamp|streetlamp|streetlight|"
    r"glow|backlit|lightning|firelight|window light|overcast|wet glass"
    r")\b",
    re.I,
)
_HOOK_STRIKE_RE = re.compile(
    r"\b("
    r"sunset|rain|storm|lamp|streetlamp|streetlight|glow|backlit|"
    r"lightning|firelight|wet glass"
    r")\b",
    re.I,
)
_HOOK_FLAT_RE = re.compile(
    r"\b(unmade empty bed|empty bed|empty chair|empty platform)\b",
    re.I,
)
_HOOK_CHAR_TYPES = frozenset({"woman", "man", "couple", "silhouette"})


def assess_hook_beat(lines: list[dict[str, Any]]) -> dict[str, Any]:
    """Highest-priority gate: scene 1 must be a striking character beat."""
    rec: dict[str, Any] = {"scene": 1, "status": "fail", "reason": "missing scene 1"}
    if not lines or not isinstance(lines[0], dict):
        print("[LOFI hook] FAIL scene=1 missing")
        return rec
    row = lines[0]
    st = str(row.get("subject_type") or "").strip().lower().replace(" ", "_")
    text = str(row.get("text") or row.get("beat_text") or "")
    setting = str(row.get("setting") or "")
    obj = str(row.get("key_object") or "")
    tod = str(row.get("time_of_day") or "")
    blob = f"{setting} {obj} {tod} {text}"
    rec.update({"text": text, "subject_type": st, "setting": setting, "object": obj})
    if st == "object_focus" or st not in _HOOK_CHAR_TYPES:
        rec["reason"] = (
            "hook scene 1 is object-only / no character "
            "(need woman, man, couple, or silhouette in a striking frame)"
        )
        print(f"[LOFI hook] FAIL scene=1 {rec['reason']} type={st!r}")
        return rec
    if _HOOK_FLAT_RE.search(blob) and not _HOOK_STRIKE_RE.search(blob):
        rec["reason"] = (
            "hook scene 1 is a flat establishing still "
            "(empty bed/chair/platform without rain, lamp, or sunset)"
        )
        print(f"[LOFI hook] FAIL scene=1 {rec['reason']} setting={setting!r}")
        return rec
    if not _HOOK_LIGHT_RE.search(blob):
        rec["reason"] = (
            "hook scene 1 lacks high-contrast light "
            "(sunset/rain/lamp/dawn/dusk/glow)"
        )
        print(f"[LOFI hook] FAIL scene=1 {rec['reason']} setting={setting!r}")
        return rec
    rec["status"] = "pass"
    rec["reason"] = f"character={st} light=ok"
    print(
        f"[LOFI hook] PASS scene=1 type={st} setting={setting!r} "
        f"tod={tod!r} line={text!r}"
    )
    return rec


def _narrative_gate_reasons(data: dict[str, Any], module: str) -> list[str]:
    reasons: list[str] = []
    lines = [r for r in (data.get("lines") or []) if isinstance(r, dict)]
    hook_rep = assess_hook_beat(lines)
    data["hook_gate"] = hook_rep
    if hook_rep.get("status") != "pass":
        reasons.append(f"HOOK: {hook_rep.get('reason')}")
    ao = data.get("anchor_object") if isinstance(data.get("anchor_object"), dict) else {}
    name = str(ao.get("name") or "").strip()
    if not name:
        reasons.append("anchor_object is null")
    mentioned = []
    if name:
        for i, row in enumerate(lines):
            blob = str(row.get("text") or row.get("beat_text") or "")
            if _anchor_mentioned(blob, name):
                mentioned.append(i + 1)
    if name and len(mentioned) < 2:
        reasons.append(
            f"anchor_object {name!r} mentioned in {len(mentioned)} beat(s) (need 2+)"
        )
    elif name:
        early = [b for b in mentioned if b <= 5]
        late = [b for b in mentioned if b >= 6]
        if not early or not late:
            reasons.append(
                f"anchor_object {name!r} needs early/mid + late callback "
                f"(beats={mentioned})"
            )
    thesis = str(data.get("thesis") or "").strip()
    if not thesis:
        reasons.append("missing internal thesis")
    fns = [str(r.get("beat_function") or "") for r in lines]
    if any(f not in {"hook", "complication", "turn", "insight", "close"} for f in fns):
        reasons.append("beat_function missing or invalid")
    for i in range(1, len(fns)):
        if fns[i] and fns[i] == fns[i - 1]:
            reasons.append(f"consecutive beat_function {fns[i]!r} at scenes {i}-{i + 1}")
            break
    if "turn" not in fns and "insight" not in fns:
        reasons.append("missing turn/insight — no development")
    insight_rep = assess_insight_lines(lines)
    data["insight_agency"] = insight_rep
    reasons.extend(insight_rep.get("fails") or [])
    principle_rep = assess_principle_lines(lines)
    data["principle_voice"] = principle_rep
    reasons.extend(principle_rep.get("fails") or [])
    scope_rep = assess_principle_scope(lines)
    data["principle_scope"] = scope_rep
    reasons.extend(scope_rep.get("fails") or [])
    close_n = sum(
        1 for r in lines if str(r.get("shot_scale") or "").strip().lower() == "close"
    )
    data["close_beat_count"] = close_n
    isnt_n = sum(
        1
        for r in lines
        if _ISNT_Y_RE.search(str(r.get("text") or r.get("beat_text") or ""))
    )
    if isnt_n > 1:
        reasons.append(f"X-isn't-Y used {isnt_n} times (max 1)")
    dupes = rag.intra_script_duplicate_pairs(lines)
    if dupes:
        a, b, score = dupes[0]
        reasons.append(f"intra-script dupe scenes {a}-{b} overlap={score:.2f}")
    phrase_hits = rag.cross_run_phrase_hits(module, data)
    if phrase_hits:
        reasons.append("cross-run phrase/anchor reuse: " + " | ".join(phrase_hits[:3]))
    variety = score_episode_variety(lines)
    if variety.get("holds_episode"):
        reasons.append(f"episode_variety hold: {variety.get('summary')}")
    arch = score_setting_archetypes(lines)
    data["setting_archetypes"] = arch
    if arch.get("holds_episode"):
        reasons.append(
            f"setting archetype overflow (max {arch.get('max_per_episode')}/9): "
            f"{arch.get('overflow')}"
        )
    prev_arch = rag.preceding_setting_archetypes(module)
    cross_set = assess_cross_run_settings(arch.get("unique") or [], prev_arch)
    data["setting_archetype_cross"] = cross_set
    if not cross_set.get("passed"):
        reasons.append(f"setting archetype cross-run: {cross_set.get('reason')}")
    comp = score_composition_types(lines)
    data["composition_types"] = comp
    if comp.get("holds_episode"):
        reasons.append(f"composition hold: {comp.get('summary')}")
    print(
        f"[LOFI setting] intra={arch.get('summary')} "
        f"cross={'pass' if cross_set.get('passed') else 'FAIL'} "
        f"fresh={cross_set.get('fresh')} prev={cross_set.get('previous')} "
        f"pulled_to_avoid_repeat={cross_set.get('fresh')}"
    )
    data["anchor_beats"] = mentioned
    data["isnt_y_count"] = isnt_n
    data["intra_dedup_pass"] = not dupes
    data["cross_run_dedup_pass"] = not phrase_hits
    data["episode_variety"] = variety
    return reasons


def _log_narrative(data: dict[str, Any], reasons: list[str]) -> None:
    ao = data.get("anchor_object") if isinstance(data.get("anchor_object"), dict) else {}
    lines = [r for r in (data.get("lines") or []) if isinstance(r, dict)]
    close_i = next(
        (
            i + 1
            for i, r in enumerate(lines)
            if str(r.get("shot_scale") or "").lower() == "close"
        ),
        0,
    )
    close_row = lines[close_i - 1] if close_i else {}
    fns = ",".join(str(r.get("beat_function") or "-") for r in lines)
    print(f"[LOFI narrative] thesis={data.get('thesis')!r}")
    print(
        f"[LOFI narrative] anchor_object={ao.get('name')!r} "
        f"beats={data.get('anchor_beats')} "
        f"initial={ao.get('initial_state')!r} final={ao.get('final_state')!r}"
    )
    print(
        f"[LOFI narrative] hook={data.get('hook_gate', {}).get('status')} "
        f"{data.get('hook_gate', {}).get('reason')}"
    )
    print(
        f"[LOFI narrative] close_beat={close_i or 'none'} "
        f"variant={data.get('close_variant') or close_row.get('close_variant') or 'none'} "
        f"character={data.get('close_character') or close_row.get('close_character') or ''} "
        f"target={close_row.get('close_target')} "
        f"eye_ctx={data.get('eye_close_context') or close_row.get('eye_close_context')!r} "
        f"fn={close_row.get('beat_function')}"
    )
    print(f"[LOFI narrative] beat_functions={fns}")
    pv = data.get("principle_voice") or {}
    print(
        f"[LOFI narrative] principle={pv.get('fails') or 'pass'} "
        f"lines={[(r.get('scene'), r.get('status'), r.get('reason')) for r in (pv.get('lines') or [])]}"
    )
    print(
        f"[LOFI narrative] dedup intra={'pass' if data.get('intra_dedup_pass') else 'FAIL'} "
        f"cross={'pass' if data.get('cross_run_dedup_pass') else 'FAIL'} "
        f"isnt_y={data.get('isnt_y_count')}"
    )
    print(
        f"[LOFI narrative] holds_episode={int(bool(reasons))} "
        f"reasons={reasons or 'none'}"
    )


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
    from core_engine.economic_reel_lofi.setting_archetypes import (
        classify_setting_archetype,
    )

    pool_block = "\n".join(
        f'  - archetype="{p.get("archetype") or classify_setting_archetype(p["setting"], p["key_object"])}" '
        f'| setting="{p["setting"]}" | key_object="{p["key_object"]}"'
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
        used_anchors = rag.recent_used_anchors(module)
        used_phrases = list(rag.recent_used_phrases(module))[:24]
        if used_anchors or used_phrases:
            feedback_block += (
                "\nDO NOT REUSE these recent anchors/phrases from ANY theme:\n"
                f"  anchors: {', '.join(sorted(used_anchors)) or '(none)'}\n"
                f"  phrases: {'; '.join(used_phrases) or '(none)'}\n"
            )
        prev_arch = rag.preceding_setting_archetypes(module)
        from core_engine.economic_reel_lofi.setting_archetypes import ARCHETYPE_ORDER

        required_new = [a for a in ARCHETYPE_ORDER if a not in set(prev_arch)]
        if prev_arch or required_new:
            feedback_block += (
                "\nSETTING ARCHETYPES:\n"
                f"  previous script used: {', '.join(prev_arch) or '(none)'}\n"
                f"  REQUIRED_NEW (use at least 2 of these): "
                f"{', '.join(required_new[:4]) or '(any unused)'}\n"
                "  Max 2 beats per archetype. Logically spread the 9 beats.\n"
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
        hint = " ".join(str(row.get("visual_anchor_hint") or "").split())
        if hint:
            row["visual_anchor_hint"] = hint
        if not row.get("visual_prompt"):
            row["visual_prompt"] = f"story beat {i + 1}"
    data["lines"] = lines
    if thematic:
        _finalize_thematic_narrative(data, pool, module=module, theme=theme)
        reasons = _narrative_gate_reasons(data, module)
        if reasons:
            print(f"[LOFI narrative] first-pass holds: {reasons}")
            try:
                retry_prompt = _thematic_script_prompt(
                    module=module,
                    theme=theme,
                    subtheme=subtheme,
                    scene_count=scene_count,
                    hook_type=hook_type,
                    arc=arc,
                    detail_block=detail_block,
                    pool_block=pool_block,
                    feedback_block=(
                        feedback_block
                        + "\nNARRATIVE HOLDS (must fix):\n"
                        + "\n".join(f"  - {r}" for r in reasons)
                        + "\n"
                    ),
                    act_guide=act_guide,
                    structure=structure,
                    quote_block=quote_block,
                    gold_block=_gold_cadence_block(theme, module),
                )
                raw = _call_llm(retry_prompt)
                retry = _extract_json_object(raw)
                if isinstance(retry, dict) and retry.get("lines"):
                    retry["hook_type"] = hook_type
                    retry["theme"] = theme
                    retry["module"] = module
                    retry["retrieved_details"] = retrieved
                    retry["arc_template"] = data.get("arc_template")
                    retry["structure_id"] = data.get("structure_id")
                    retry["seed_hooks"] = data.get("seed_hooks")
                    data = retry
                    _finalize_thematic_narrative(
                        data, pool, module=module, theme=theme
                    )
            except Exception as exc:  # noqa: BLE001
                _LOG.warning("narrative retry failed (%s)", exc)
                print(f"[LOFI narrative] retry failed: {exc}")
            reasons = _narrative_gate_reasons(data, module)
        _log_narrative(data, reasons)
        data["holds_episode"] = bool(reasons)
        data["narrative_holds"] = reasons
        if reasons:
            # Hard-fail: validator rejects missing hook_type so the episode
            # cannot ship a log-only miss the way hope 161048 did.
            print("[LOFI narrative] HARD HOLD — rejecting script")
            data["hook_type"] = ""
        lines = data.get("lines") or lines
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

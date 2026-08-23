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
    rag.reset_batch_scripts()


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
        "You write continuous spoken-word scripts for short vertical reels. "
        "Output STRICT JSON only."
    )
    temp = float(os.getenv("LOFI_DEEPSEEK_TEMPERATURE", "1.15") or 1.15)
    print(f"[LOFI script] deepseek temperature={temp}")
    resp = client.chat.completions.create(
        model=model,
        max_tokens=4096,
        temperature=temp,
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


# Cadence/quality only — never treat as lines to copy. These model
# claim → complication/contrast → resolution, not action → action.
_GOLD_CADENCE_BEATS: tuple[str, ...] = (
    "Don't wait for the feeling first.",
    "Because the feeling never leads.",
    "You press the sweater to your chest.",
    "Stay with the quiet.",
    "Two cups sit by the sink.",
    "The chair faces the light, yet you stay.",
    "You pause in the doorway.",
    "The kettle has gone cold.",
    "You meet the morning there anyway.",
)
_GOLD_CADENCE_BEATS_B: tuple[str, ...] = (
    "Don't finish the argument first.",
    "You shut the door.",
    "Still you hear the hallway hold.",
    "Stay after the heat.",
    "The coat hangs there.",
    "You leave it hanging though it waits.",
    "You stand in the doorway.",
    "The coat hangs alone now.",
    "You walk out since the heat passed.",
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
    numbered_a = "\n".join(f"  {i+1}. {line}" for i, line in enumerate(_GOLD_CADENCE_BEATS))
    numbered_b = "\n".join(f"  {i+1}. {line}" for i, line in enumerate(_GOLD_CADENCE_BEATS_B))
    live = str(theme or "").strip().replace("_", " ") or "this theme"
    if str(module or "").strip().lower() == "parenting":
        domain = (
            "Parenting content required — these samples are cadence only. "
            f"Write THIS {live} episode. Match the argument shape, do not copy lines."
        )
    elif str(theme or "").strip().lower() == "hope":
        domain = (
            "Write THIS hope episode with original lines. Match the quality of "
            "the split and the thesis shape. Do not copy the sample wording."
        )
    else:
        domain = f"Write THIS {live} episode. Match the argument shape; do not copy nouns or lines."
    return f"""
QUALITY BAR (argument shape only — do NOT copy lines, images, or the sample nouns):
Sample A:
{numbered_a}
Sample B:
{numbered_b}
Shape: hook makes a claim; a later beat complicates it; the insight answers
or reverses that SAME claim. Connectives must be load-bearing (deleting the
word should change the logic). Vary WHERE the turn happens — Sample A and
Sample B put devices on different beat numbers on purpose. Do not copy
either sample's connective positions. Banned shape:
but@3 / even-if@4 / anyway@5 / even-then@6 / because@7 / so@9.
{domain}
"""


def _rhetoric_prompt_block(rhetoric: dict[str, Any] | None) -> str:
    """Assigned library shape — additive; does not replace existing gates."""
    rec = rhetoric if isinstance(rhetoric, dict) else {}
    name = str(rec.get("name") or "").strip()
    if not name:
        return ""
    shape = str(rec.get("shape") or "").strip()
    strength = str(rec.get("tool_strength") or "").strip()
    example = rec.get("in_house_example") or []
    if isinstance(example, list):
        numbered = "\n".join(f"  {i + 1}. {line}" for i, line in enumerate(example))
    else:
        numbered = str(example)
    overlay = str(rec.get("hook_overlay") or "").strip()
    overlay_block = ""
    if overlay == "anaphora_to_universal_law":
        overlay_pat = rag.get_rhetoric_pattern(overlay) or {}
        overlay_block = f"""
HOOK-ONLY OVERLAY ({overlay}):
Write scene 1 in this hook shape, then write beats 2–9 in the assigned body pattern.
Do not use anaphora as the whole-script structure.
shape: {str(overlay_pat.get("shape") or "").strip()}
"""
    aphorism_ok = name in rag.RHETORIC_APHORISM_PATTERNS or overlay in rag.RHETORIC_APHORISM_PATTERNS
    aphorism_block = ""
    if aphorism_ok:
        aphorism_block = """
OPTIONAL OPENING DEVICE — unattributed aphorism:
You MAY open with a stated principle or aphorism delivered WITHOUT naming any
real person. Forbidden: "X said", "as Y wrote", any philosopher or author name.
Invent the line. This replaces named-philosopher quote framing.
"""
    return f"""
ASSIGNED PATTERN: {name}
shape: {shape}
example (shape only — invent every sentence; do not copy nouns or clauses):
{numbered}
{overlay_block}{aphorism_block}"""


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
    rhetoric: dict[str, Any] | None = None,
) -> str:
    # Gate rules are enforced post-hoc, not listed here. DeepSeek collapses
    # when the generation prompt becomes a checklist.
    _ = (detail_block, quote_block, gold_block, act_guide, hook_type, structure)
    thesis = ""
    if isinstance(rhetoric, dict):
        thesis = str(rhetoric.get("locked_thesis") or "").strip()
    anchor_block = ""
    if isinstance(rhetoric, dict) and isinstance(rhetoric.get("locked_anchor"), dict):
        ao = rhetoric["locked_anchor"]
        anchor_block = (
            f"ANCHOR OBJECT: {ao.get('name')}\n"
            f"  first seen: {ao.get('initial_state')}\n"
            f"  later: {ao.get('final_state')}\n"
        )
    elif isinstance(rhetoric, dict) and rhetoric.get("locked_anchor_name"):
        anchor_block = f"ANCHOR OBJECT: {rhetoric.get('locked_anchor_name')}\n"
    thesis_block = f"THESIS (internal — do not speak it): {thesis}\n" if thesis else ""
    return f"""Write a spoken-word vertical reel as STRICT JSON.

THEME: {theme}
SUBTHEME: {subtheme or "(none)"}
BEAT COUNT: {scene_count}
{thesis_block}{anchor_block}CHARACTER / SETTING CONTEXT:
{pool_block}
{_rhetoric_prompt_block(rhetoric)}
{feedback_block}

FORMAT (length and tone only — not content rules):
- Exactly {scene_count} spoken lines. One line per beat. No paragraphs.
- Each line is one breath: about 5–9 words, under 56 characters.
- Concise and imagistic. A short caption, not an essay.

Write {scene_count} original spoken lines that follow the assigned pattern's SHAPE
and develop the theme around the anchor. Scene 1 is the opening. One later beat
is the insight. Invent every sentence. Include a visual field per beat.

Output STRICT JSON only, no markdown:
{{
  "theme": "{theme}",
  "module": "{module}",
  "thesis": "<one internal sentence this episode proves — not shown on screen>",
  "anchor_object": {{
    "name": "<concrete object>",
    "initial_state": "<how it first appears>",
    "final_state": "<how it has changed by the callback>"
  }},
  "monologue": "<the {scene_count} lines joined>",
  "lines": [
    {{
      "scene": 1,
      "text": "<spoken line>",
      "beat_text": "<same as text>",
      "emotion": "opening_hook",
      "arc_position": "act1",
      "beat_function": "hook",
      "shot_scale": "medium",
      "subject_type": "woman",
      "subject_expression": "distant, still",
      "setting": "<from context>",
      "key_object": "<from context>",
      "time_of_day": "dusk",
      "visual_anchor_hint": "<object in setting>"
    }}
  ]
}}
Valid JSON only. No NSFW. No real private individuals named.
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


MIN_CONNECTIVE_BEATS = 3
MAX_BARE_ACTION_BEATS = 5
MIN_DEVELOPED_LINES = 2
MAX_SUPPORTING_OBJECTS = 2
_CONNECTIVE_RE = re.compile(
    r"\b("
    r"because|so|since|therefore|"
    r"but|yet|still|anyway|"
    r"even though|even if|even then|even when|"
    r"although|though|without|"
    r"the way|not later|not because"
    r")\b",
    re.I,
)
_SUBORDINATE_RE = re.compile(
    r"\b(because|when|if|while|though|although|since|without|even)\b",
    re.I,
)
_BARE_SVO_RE = re.compile(
    r"^(you|i|we|they)\s+[a-z']+\s+(the|a|an|your)?\s*[a-z']+\b",
    re.I,
)
_BARE_IMP_RE = re.compile(
    r"^(don't|do not|keep|put|set|leave|stay|open|walk|hold|wait|stop|"
    r"touch|fold|cross|face|press|name)\b",
    re.I,
)
_CONTENT_STOP = frozenset(
    {
        "you", "your", "i", "we", "they", "them", "their", "it", "its",
        "the", "a", "an", "to", "for", "of", "in", "on", "and", "or",
        "not", "do", "did", "does", "don't", "first", "just", "with",
        "from", "into", "at", "as", "is", "are", "was", "were", "be",
        "this", "that", "there", "then", "than", "now",
    }
)
_PLACE_STEMS = frozenset(
    {
        "door", "doorway", "hallway", "kitchen", "path", "wall", "platform",
        "stairwell", "street", "window", "porch", "track", "tracks",
        "hall", "hallway",
        "horizon", "landing", "stoop", "corner", "lobby", "cafe", "park",
        "picnic", "distant",
        "room", "table", "sink", "floor", "ground", "tide", "coast",
        "streetlamp", "streetlight", "car", "train", "river", "lamp",
    }
)
_ABSTRACT_STEMS = frozenset(
    {
        "gap", "space", "weight", "quiet", "morning", "knock", "feeling",
        "heat", "silence", "light", "dark", "place", "hand", "hands",
        "chest", "body", "argument", "word", "words",
        "habit", "night", "shape", "sore", "hour", "minute",
        "side", "cost", "place", "wound",

    }
)
_PROP_ADJ = frozenset(
    {
        "empty", "heavy", "unused", "old", "folded", "bare", "half",
        "packed", "dark", "dusk", "second", "now", "alone", "open",
        "closed", "cold", "same", "handed", "spare",
        "single", "lit", "hollow", "raw", "full", "slow", "still",
        "untouched", "unworn", "unused", "own", "other", "such",
        "first", "last", "next", "more", "most", "least", "warm",
        "finally", "gone", "left", "right", "long", "short", "small",
        "great", "whole", "only", "even", "ever", "never", "emptied",
        "standing", "waiting", "lying", "going", "worn", "still",
        "sore",
    }
)
_PROP_CLOSED = frozenset(
    {
        "behind", "before", "after", "under", "over", "into", "onto",
        "from", "with", "without", "while", "when", "once", "then",
        "there", "here", "later", "again", "away", "back", "down",
        "up", "out", "off", "together", "else", "inch", "bit", "lot",
        "way", "kind", "sort", "edge",
    }
)
_PROP_VERBS = frozenset(
    {
        "sit", "walk", "stand", "hold", "lift", "set", "leave", "stay",
        "wait", "fold", "cross", "pause", "remain", "face", "stop",
        "touch", "tell", "say", "save", "hang", "lie", "keep", "go",
        "come", "take", "put", "get", "make", "let", "give", "look",
        "name", "earn", "pour", "knock", "speak",
    }
)


def _line_word_count(text: str) -> int:
    return len(re.findall(r"[A-Za-z']+", str(text or "")))


def _content_stems(text: str) -> set[str]:
    stems: set[str] = set()
    for raw in re.findall(r"[A-Za-z']+", str(text or "").lower()):
        w = raw.replace("'", "")
        if len(w) < 3 or w in _CONTENT_STOP:
            continue
        if w.endswith("ing") and len(w) > 6:
            w = w[:-3]
        elif w.endswith("ed") and len(w) > 5:
            w = w[:-2]
        elif w.endswith("s") and not w.endswith("ss") and len(w) > 4:
            w = w[:-1]
        stems.add(w)
    return stems


def _has_connective(text: str) -> bool:
    return bool(_CONNECTIVE_RE.search(str(text or "")))


def _is_bare_action_clause(text: str) -> bool:
    blob = str(text or "").strip()
    if not blob:
        return True
    if _line_word_count(blob) >= 8:
        return False
    if _has_connective(blob) or _SUBORDINATE_RE.search(blob):
        return False
    lead = blob.split(".", 1)[0].strip()
    return bool(_BARE_SVO_RE.search(lead) or _BARE_IMP_RE.search(lead))


def _echo_with_twist(text: str, prior_stems: set[str]) -> bool:
    if not prior_stems:
        return False
    shared = _content_stems(text) & prior_stems
    if not shared:
        return False
    return bool(
        _has_connective(text)
        or re.search(r"\b(now|anyway|still|not|without|even)\b", text, re.I)
    )


def assess_connective_development(lines: list[dict[str, Any]]) -> dict[str, Any]:
    """At least 3 beats relate to a prior idea via connective or echo-twist."""
    hits: list[dict[str, Any]] = []
    prior: set[str] = set()
    for i, row in enumerate(lines or []):
        if not isinstance(row, dict):
            continue
        text = str(row.get("text") or row.get("beat_text") or "")
        why = ""
        if _has_connective(text):
            why = "connective"
        elif i > 0 and _echo_with_twist(text, prior):
            why = "echo-twist"
        if why:
            hits.append({"scene": i + 1, "text": text, "reason": why})
        prior |= _content_stems(text)
    fails: list[str] = []
    if len(hits) < MIN_CONNECTIVE_BEATS:
        fails.append(
            f"connective-development: {len(hits)} beats relate to a prior idea "
            f"(need {MIN_CONNECTIVE_BEATS}+ because/but/so/even-then or echo-twist)"
        )
        print(f"[LOFI argument] CONNECT FAIL count={len(hits)}")
    else:
        print(
            f"[LOFI argument] CONNECT pass count={len(hits)} "
            f"scenes={[h['scene'] for h in hits]}"
        )
    return {"hits": hits, "count": len(hits), "fails": fails}


def assess_insight_answers_hook(lines: list[dict[str, Any]]) -> dict[str, Any]:
    """Insight must share/develop/reverse the hook claim, not only the anchor."""
    hook = ""
    if lines and isinstance(lines[0], dict):
        hook = str(lines[0].get("text") or lines[0].get("beat_text") or "")
    insights = [
        (i, str(row.get("text") or row.get("beat_text") or ""))
        for i, row in enumerate(lines or [])
        if isinstance(row, dict) and str(row.get("beat_function") or "") == "insight"
    ]
    hook_stems = _content_stems(hook)
    fails: list[str] = []
    report: list[dict[str, Any]] = []
    if not insights:
        fails.append("insight-hook: no insight beat to answer the hook claim")
        print("[LOFI argument] INSIGHT-HOOK FAIL no insight")
        return {"status": "fail", "fails": fails, "insights": report}
    for i, text in insights:
        shared = _content_stems(text) & hook_stems
        contrast = bool(
            re.search(r"\b(don't|do not|never)\b", hook, re.I)
            and re.search(r"\b(now|anyway|still|without|even|stay|leave|set|keep)\b", text, re.I)
        )
        ok = bool(shared) or contrast
        rec = {
            "scene": i + 1,
            "text": text,
            "hook": hook,
            "shared": sorted(shared),
            "status": "pass" if ok else "fail",
        }
        report.append(rec)
        if ok:
            print(
                f"[LOFI argument] INSIGHT-HOOK pass scene={i + 1} "
                f"shared={sorted(shared)} line={text!r}"
            )
        else:
            fails.append(
                f"insight scene {i + 1} does not answer/reverse the hook claim "
                f"{hook!r}: {text!r}"
            )
            print(f"[LOFI argument] INSIGHT-HOOK FAIL scene={i + 1} line={text!r}")
    return {
        "status": "fail" if fails else "pass",
        "fails": fails,
        "insights": report,
    }


def assess_recipe_pattern(lines: list[dict[str, Any]]) -> dict[str, Any]:
    """Hold the uniform SVO-action collapse: 6+ bare clauses, no length mix."""
    bare: list[int] = []
    developed: list[int] = []
    for i, row in enumerate(lines or []):
        if not isinstance(row, dict):
            continue
        text = str(row.get("text") or row.get("beat_text") or "")
        n = _line_word_count(text)
        fn = str(row.get("beat_function") or "").strip().lower()
        if fn not in {"hook", "insight"} and _is_bare_action_clause(text):
            bare.append(i + 1)
        if n >= 8 or (_has_connective(text) and n >= 7):
            developed.append(i + 1)
    fails: list[str] = []
    if len(bare) > MAX_BARE_ACTION_BEATS:
        fails.append(
            f"recipe-collapse: {len(bare)}/{len(lines)} beats are bare SVO/imperative "
            f"under 8 words with no connective (max {MAX_BARE_ACTION_BEATS}): "
            + ",".join(str(s) for s in bare)
        )
        print(f"[LOFI argument] RECIPE FAIL bare={bare}")
    if len(developed) < MIN_DEVELOPED_LINES:
        fails.append(
            f"sentence-variety: {len(developed)} developed line(s) "
            f"(need {MIN_DEVELOPED_LINES}+ at 8–9 words or connective+7)"
        )
        print(f"[LOFI argument] VARIETY FAIL developed={developed}")
    if not fails:
        print(
            f"[LOFI argument] RECIPE pass bare={len(bare)} "
            f"developed={developed}"
        )
    return {
        "bare_scenes": bare,
        "developed_scenes": developed,
        "fails": fails,
    }


def _spoken_prop_stems(text: str) -> set[str]:
    """Concrete countable nouns after a determiner — not adjectives or participles."""
    stems: set[str] = set()
    tokens = re.findall(r"[a-z]+", str(text or "").lower())
    dets = {"the", "a", "an", "your", "two"}
    i = 0
    while i < len(tokens):
        if tokens[i] in dets:
            j = i + 1
            while j < len(tokens) and tokens[j] in _PROP_ADJ:
                j += 1
            if j < len(tokens):
                cand = tokens[j]
                if cand.endswith("s") and cand not in {"glass"} and len(cand) > 3:
                    cand = cand[:-1]
                if (
                    cand not in _PLACE_STEMS
                    and cand not in _ABSTRACT_STEMS
                    and cand not in _PROP_ADJ
                    and cand not in _PROP_CLOSED
                    and cand not in _PROP_VERBS
                    and cand not in _CONTENT_STOP
                    and len(cand) >= 3
                ):
                    stems.add(cand)
            i = max(j, i + 1)
            continue
        i += 1
    return stems


def assess_object_churn(
    lines: list[dict[str, Any]],
    anchor_name: str,
) -> dict[str, Any]:
    """Spoken captions may name the anchor plus at most two supporting props."""
    anchor_stem = ""
    tokens = re.findall(r"[a-z]+", str(anchor_name or "").lower())
    if tokens:
        anchor_stem = tokens[-1]
    spoken: set[str] = set()
    by_scene: list[dict[str, Any]] = []
    for i, row in enumerate(lines or []):
        if not isinstance(row, dict):
            continue
        text = str(row.get("text") or row.get("beat_text") or "")
        found = _spoken_prop_stems(text)
        spoken |= found
        if found:
            by_scene.append({"scene": i + 1, "props": sorted(found)})
    extras = sorted(s for s in spoken if s != anchor_stem)
    fails: list[str] = []
    if len(extras) > MAX_SUPPORTING_OBJECTS:
        fails.append(
            f"object-churn: {len(extras)} supporting spoken props {extras} "
            f"(max {MAX_SUPPORTING_OBJECTS} besides anchor {anchor_stem!r})"
        )
        print(f"[LOFI argument] OBJECT FAIL extras={extras}")
    else:
        print(
            f"[LOFI argument] OBJECT pass anchor={anchor_stem!r} "
            f"supporting={extras}"
        )
    return {
        "anchor": anchor_stem,
        "supporting": extras,
        "by_scene": by_scene,
        "fails": fails,
    }


_PRONOUN_RE = re.compile(r"\b(it|its|they|them|this|that)\b", re.I)
_NOUNY_RE = re.compile(
    r"\b(?:the|a|an|your|empty|heavy|unused|old|folded|two)\s+([a-z]+)\b",
    re.I,
)


def assess_pronoun_continuity(lines: list[dict[str, Any]]) -> dict[str, Any]:
    """Hold dangling it/they/this after a prop was cut from spoken text."""
    fails: list[str] = []
    seen_nouns: list[str] = []
    people_ok = False
    report: list[dict[str, Any]] = []
    for i, row in enumerate(lines or []):
        if not isinstance(row, dict):
            continue
        text = str(row.get("text") or row.get("beat_text") or "")
        nouns = [m.group(1).lower() for m in _NOUNY_RE.finditer(text)]
        nouns = [n for n in nouns if n not in _PROP_ADJ and n not in _CONTENT_STOP]
        if re.search(r"\b(them|they|someone)\b", text, re.I) and i == 0:
            people_ok = True
        if re.search(r"\b(them|they)\b", text, re.I) and i == 0:
            people_ok = True
        dangling: list[str] = []
        object_it = bool(re.search(
            r"\b(open|set|leave|fold|carry|place|touch|press|keep)\s+it\b",
            text,
            re.I,
        ))
        if object_it and not nouns and not seen_nouns:
            dangling.append("it")
        dangling = sorted(set(dangling))
        rec = {"scene": i + 1, "text": text, "dangling": dangling}
        report.append(rec)
        if dangling:
            fails.append(
                f"pronoun-continuity scene {i + 1} dangling {dangling} "
                f"with no spoken noun antecedent: {text!r}"
            )
            print(f"[LOFI argument] PRONOUN FAIL scene={i + 1} {text!r}")
        seen_nouns.extend(nouns)
    if not fails:
        print("[LOFI argument] PRONOUN pass")
    return {"beats": report, "fails": fails}


def assess_caption_still_alignment(lines: list[dict[str, Any]]) -> dict[str, Any]:
    """Object-focus stills must be named in speech, or have a resolvable pronoun."""
    fails: list[str] = []
    for i, row in enumerate(lines or []):
        if not isinstance(row, dict):
            continue
        ctype = str(row.get("composition_type") or "").strip()
        st = str(row.get("subject_type") or "").strip()
        if ctype == "wide_environment":
            continue
        if ctype not in {"object_focus"} and st != "object_focus":
            continue
        obj = str(row.get("key_object") or "")
        stems = [
            t
            for t in re.findall(r"[a-z]+", obj.lower())
            if t not in _PROP_ADJ and t not in _CONTENT_STOP and len(t) >= 3
        ]
        if not stems or any(s in _PLACE_STEMS or s in _ABSTRACT_STEMS for s in stems):
            continue
        text = str(row.get("text") or row.get("beat_text") or "")
        blob = text.lower()
        named = any(s in blob for s in stems)
        pronoun = bool(_PRONOUN_RE.search(text))
        if not named and not pronoun:
            fails.append(
                f"still-align scene {i + 1} object_focus {obj!r} is not in caption "
                f"{text!r}"
            )
            print(f"[LOFI argument] STILL-ALIGN FAIL scene={i + 1} obj={obj!r}")
    if not fails:
        print("[LOFI argument] STILL-ALIGN pass")
    return {"fails": fails}


def _connective_load_bearing(prev: str, text: str, word: str) -> str:
    """Empty string if the device needs the prior beat; else why it's decorative."""
    prev_l = str(prev or "").lower()
    here_l = str(text or "").lower()
    if word == "anyway":
        if re.search(r"\b(don't|do not|never|not|wait|earn|later)\b", prev_l):
            return ""
        return "anyway has no prior expectation to push against"
    if word in {"even then", "still", "even when"}:
        if prev_l:
            return ""
        return f"{word} has no prior beat to persist against"
    if word in {"so", "therefore"}:
        if prev_l:
            return ""
        return f"{word} has no prior reason"
    if word in {"because", "since"}:
        if _content_stems(prev) & _content_stems(text) or re.search(
            r"\b(they|them|it|gap|knock|earn|name|chair|bag)\b", here_l
        ):
            return ""
        return f"{word} does not share a referent with the prior beat"
    if word in {"but", "yet", "though", "although"}:
        if re.search(r"\b(never|not|don't|empty|alone|still|yet|gone|wait)\b", here_l + " " + prev_l):
            return ""
        return f"{word} does not contrast the prior beat"
    if word in {"even if", "even though", "without"}:
        if _content_stems(prev) or _content_stems(text):
            return ""
        return f"{word} is not attached to a prior claim"
    return ""


def assess_connective_cross_run(
    lines: list[dict[str, Any]],
    module: str,
    *,
    exclude_themes: set[str] | None = None,
    extra_records: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Same word@beat as last 2–3 scripts, or the same family sequence, is a hold."""
    cmap = rag.extract_connective_map(lines)
    seq = rag.connective_sequence(cmap)
    prev = rag.recent_connective_records(module, 10, exclude_themes=exclude_themes)
    if extra_records:
        prev = list(prev) + list(extra_records)
    recent = prev[-3:]
    fails: list[str] = []
    pairs = {(int(r["scene"]), str(r["word"])) for r in cmap}
    for rec in recent:
        overlap = sorted(pairs & rec.get("pairs", set()))
        if overlap:
            fails.append(
                f"connective-position reuse vs {rec.get('theme') or 'prior'}: "
                + ",".join(f"{w}@s{s}" for s, w in overlap)
            )
    if seq in rag.BANNED_CONNECTIVE_SHAPES:
        fails.append(f"banned connective sequence {seq}")
    for rec in recent:
        if seq and seq == rec.get("sequence"):
            fails.append(
                f"connective-sequence reuse vs {rec.get('theme') or 'prior'}: {seq}"
            )
    if fails:
        print(f"[LOFI argument] CONNECT-DEDUP FAIL {fails}")
    else:
        print(
            f"[LOFI argument] CONNECT-DEDUP pass map="
            f"{[(r['scene'], r['word']) for r in cmap]}"
        )
    return {"map": cmap, "sequence": seq, "fails": fails}


def assess_connective_coherence(
    lines: list[dict[str, Any]],
    *,
    use_llm: bool = True,
) -> dict[str, Any]:
    """
    Load-bearing check: the connective must change the relationship to the
    prior beat, and the line must be clear with its own referents.
    """
    fails: list[str] = []
    report: list[dict[str, Any]] = []
    texts = [
        str(r.get("text") or r.get("beat_text") or "") if isinstance(r, dict) else ""
        for r in (lines or [])
    ]
    cmap = {int(r["scene"]): r for r in rag.extract_connective_map(lines)}
    for i, text in enumerate(texts):
        prev = texts[i - 1] if i else ""
        hit = cmap.get(i + 1)
        decorative = ""
        if hit:
            decorative = _connective_load_bearing(prev, text, str(hit["word"]))
        rec = {
            "scene": i + 1,
            "text": text,
            "word": (hit or {}).get("word"),
            "decorative": decorative,
        }
        report.append(rec)
        if decorative:
            fails.append(
                f"decorative connective scene {i + 1} ({hit.get('word')}): "
                f"{decorative} — {text!r}"
            )
            print(f"[LOFI argument] COHERENCE FAIL scene={i + 1} {decorative}")
    if use_llm and os.getenv("LOFI_SKIP_COHERENCE_LLM", "").strip() not in {"1", "true", "yes"}:
        llm_fails = _coherence_llm_judge(texts, cmap)
        fails.extend(llm_fails)
    if not fails:
        print("[LOFI argument] COHERENCE pass")
    return {"beats": report, "fails": fails}


def _coherence_llm_judge(
    texts: list[str],
    cmap: dict[int, dict[str, Any]],
) -> list[str]:
    numbered = "\n".join(f"{i}. {t}" for i, t in enumerate(texts, 1) if t)
    if not numbered:
        return []
    devices = ", ".join(
        f"s{s}:{r.get('word')}" for s, r in sorted(cmap.items())
    ) or "none"
    prompt = f"""Spoken 9-beat reel captions:

{numbered}

Connective words present: {devices}

For each beat 2–9 return JSON only:
{{"beats":[{{"scene":2,"follows":true,"relation":"reason|contrast|consequence|none","load_bearing":true,"alone_clear":true,"why":"..."}}]}}

follows: does this beat follow from the previous as a reason, contrast, or consequence?
load_bearing: if a transition word is present, would deleting it change the logical relationship? false = decorative insert.
alone_clear: are it/they/this resolvable from earlier spoken lines (not from a cut object)?
"""
    try:
        raw = _call_llm(prompt)
    except Exception as exc:  # noqa: BLE001
        print(f"[LOFI argument] COHERENCE LLM skip ({exc})")
        return []
    blob = raw.strip()
    start = blob.find("{")
    end = blob.rfind("}")
    if start < 0 or end <= start:
        print("[LOFI argument] COHERENCE LLM unparsed — skip")
        return []
    try:
        data = json.loads(blob[start : end + 1])
    except json.JSONDecodeError:
        print("[LOFI argument] COHERENCE LLM bad JSON — skip")
        return []
    fails: list[str] = []
    for rec in data.get("beats") or []:
        if not isinstance(rec, dict):
            continue
        scene = int(rec.get("scene") or 0)
        word = (cmap.get(scene) or {}).get("word")
        if word and rec.get("load_bearing") is False:
            fails.append(
                f"coherence-llm scene {scene} decorative {word!r}: {rec.get('why')}"
            )
        if rec.get("alone_clear") is False:
            fails.append(
                f"coherence-llm scene {scene} unclear referents: {rec.get('why')}"
            )
        if word and rec.get("follows") is False and rec.get("relation") == "none":
            fails.append(
                f"coherence-llm scene {scene} connective does not follow: {rec.get('why')}"
            )
    if fails:
        print(f"[LOFI argument] COHERENCE LLM FAIL {fails}")
    else:
        print("[LOFI argument] COHERENCE LLM pass")
    return fails


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
    connect_rep = assess_connective_development(lines)
    data["connective_development"] = connect_rep
    reasons.extend(connect_rep.get("fails") or [])
    hook_ans = assess_insight_answers_hook(lines)
    data["insight_answers_hook"] = hook_ans
    reasons.extend(hook_ans.get("fails") or [])
    recipe_rep = assess_recipe_pattern(lines)
    data["recipe_pattern"] = recipe_rep
    reasons.extend(recipe_rep.get("fails") or [])
    object_rep = assess_object_churn(lines, name)
    data["object_churn"] = object_rep
    reasons.extend(object_rep.get("fails") or [])
    theme_name = str(data.get("theme") or "").strip().lower()
    extra = data.pop("_connective_extra_records", None)
    connect_x = assess_connective_cross_run(
        lines,
        module,
        exclude_themes={theme_name} if theme_name else None,
        extra_records=extra if isinstance(extra, list) else None,
    )
    data["connective_cross_run"] = connect_x
    reasons.extend(connect_x.get("fails") or [])
    pronoun_rep = assess_pronoun_continuity(lines)
    data["pronoun_continuity"] = pronoun_rep
    reasons.extend(pronoun_rep.get("fails") or [])
    still_rep = assess_caption_still_alignment(lines)
    data["caption_still_align"] = still_rep
    reasons.extend(still_rep.get("fails") or [])
    coherence_rep = assess_connective_coherence(lines)
    data["connective_coherence"] = coherence_rep
    reasons.extend(coherence_rep.get("fails") or [])
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
        f"[LOFI narrative] argument connect={len((data.get('connective_development') or {}).get('hits') or [])} "
        f"insight_hook={(data.get('insight_answers_hook') or {}).get('status')} "
        f"recipe_bare={(data.get('recipe_pattern') or {}).get('bare_scenes')} "
        f"objects={(data.get('object_churn') or {}).get('supporting')}"
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
    force_rhetoric: str | None = None,
    force_hook_overlay: str | None = None,
    lock_thesis: str | None = None,
    lock_anchor: dict[str, Any] | None = None,
    skip_polish: bool = False,
) -> dict[str, Any]:
    """
    Write ONE continuous spoken-word story, then split across scenes.

    Scene 1 is the scroll-stop hook. Final scenes deliver comfort, not a dry moral.
    """
    feedback_block = (
        "\nWrite a fresh draft. Do not repeat the previous captions.\n"
        if feedback
        else ""
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
    rhetoric = (
        rag.pick_rhetoric_pattern(
            module,
            force=force_rhetoric,
            force_hook_overlay=force_hook_overlay,
        )
        if thematic
        else {}
    )
    if thematic and rhetoric:
        if lock_thesis:
            rhetoric["locked_thesis"] = str(lock_thesis).strip()
        elif not rhetoric.get("locked_thesis"):
            rhetoric["locked_thesis"] = _ensure_thesis(
                {"theme": theme, "thesis": "", "lines": []}, theme
            )
        if isinstance(lock_anchor, dict) and lock_anchor.get("name"):
            rhetoric["locked_anchor"] = {
                "name": str(lock_anchor.get("name") or "").strip(),
                "initial_state": str(lock_anchor.get("initial_state") or "").strip(),
                "final_state": str(lock_anchor.get("final_state") or "").strip(),
            }
        else:
            avoid = set(rag.recent_used_anchors(module))
            picked = pick_episode_anchor(pool, avoid_stems=avoid)
            rhetoric["locked_anchor"] = {
                "name": str(picked.get("key_object") or "").strip(),
                "initial_state": "first seen",
                "final_state": "changed by the last beats",
            }
        print(
            f"[LOFI script] rhetoric={rhetoric.get('name')} "
            f"hook_overlay={rhetoric.get('hook_overlay') or 'none'} "
            f"strength={rhetoric.get('tool_strength')}"
        )
        print(
            f"[LOFI script] locked thesis={rhetoric.get('locked_thesis')!r} "
            f"anchor={rhetoric.get('locked_anchor')}"
        )
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
            rhetoric=rhetoric,
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
    data["rhetoric_pattern"] = str(
        data.get("rhetoric_pattern") or (rhetoric.get("name") if rhetoric else "") or ""
    )
    data["rhetoric_hook_overlay"] = str(
        data.get("rhetoric_hook_overlay")
        or (rhetoric.get("hook_overlay") if rhetoric else "")
        or ""
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
        n_cand = max(1, int(os.getenv("LOFI_DEEPSEEK_CANDIDATES", "3") or 3))
        best = data
        best_n = 10**9
        cand_prompt = prompt
        for ci in range(n_cand):
            if ci > 0:
                print(f"[LOFI script] DeepSeek candidate {ci + 1}/{n_cand}")
                try:
                    raw = _call_llm(cand_prompt)
                    nxt = _extract_json_object(raw)
                except Exception as exc:  # noqa: BLE001
                    print(f"[LOFI script] candidate {ci + 1} parse fail: {exc}")
                    continue
                if not (isinstance(nxt, dict) and nxt.get("lines")):
                    continue
                nxt["hook_type"] = hook_type
                nxt["theme"] = theme
                nxt["module"] = module
                nxt["retrieved_details"] = retrieved
                nxt["arc_template"] = data.get("arc_template")
                nxt["structure_id"] = data.get("structure_id")
                nxt["rhetoric_pattern"] = data.get("rhetoric_pattern")
                nxt["rhetoric_hook_overlay"] = data.get("rhetoric_hook_overlay")
                nxt["seed_hooks"] = data.get("seed_hooks")
                data = nxt
            _finalize_thematic_narrative(data, pool, module=module, theme=theme)
            if rhetoric.get("locked_thesis"):
                data["thesis"] = rhetoric["locked_thesis"]
            if isinstance(rhetoric.get("locked_anchor"), dict) and rhetoric["locked_anchor"].get("name"):
                data["anchor_object"] = dict(rhetoric["locked_anchor"])
            reasons = _narrative_gate_reasons(data, module)
            print(
                f"[LOFI narrative] candidate {ci + 1}/{n_cand} "
                f"holds={len(reasons)}"
            )
            if len(reasons) < best_n:
                best = data
                best_n = len(reasons)
            if not reasons:
                break
            cand_prompt = _thematic_script_prompt(
                module=module,
                theme=theme,
                subtheme=subtheme,
                scene_count=scene_count,
                hook_type=hook_type,
                arc=arc,
                detail_block=detail_block,
                pool_block=pool_block,
                feedback_block=(
                    "Write a fresh draft. Do not repeat the previous captions.\n"
                ),
                act_guide=act_guide,
                structure=structure,
                quote_block=quote_block,
                gold_block="",
                rhetoric=rhetoric,
            )
        data = best
        reasons = _narrative_gate_reasons(data, module)
        if not reasons and not skip_polish:
            try:
                from core_engine.economic_reel_lofi.claude_polish import polish_one

                polished = polish_one(data)
                polish_reasons = _narrative_gate_reasons(dict(polished), module)
                if polish_reasons:
                    print(
                        "[LOFI polish] post-polish gates failed — "
                        "keeping DeepSeek finalist (no Claude retry)"
                    )
                else:
                    data = polished
                    reasons = []
            except Exception as exc:  # noqa: BLE001
                _LOG.warning("Claude polish skipped (%s)", exc)
                print(f"[LOFI polish] skipped ({exc})")
        _log_narrative(data, reasons)
        data["holds_episode"] = bool(reasons)
        data["narrative_holds"] = reasons
        if reasons:
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
    print(
        "[LOFI script] rhetoric:",
        data.get("rhetoric_pattern"),
        "overlay:",
        data.get("rhetoric_hook_overlay") or "none",
    )
    print("[LOFI script] seed_hooks:", data.get("seed_hooks"))
    return data

# -*- coding: utf-8 -*-
"""ScriptGeneratorAgent — RAG-grounded LOFI reel scripts (separate from validator)."""
from __future__ import annotations

import json
import logging
import random
import re
from typing import Any

from core_engine.economic_reel_lofi import config as lofi_cfg
from core_engine.economic_reel_lofi import lofi_collections as rag

_LOG = logging.getLogger(__name__)

_HOOK_TYPES = ("definition", "rhetorical_question", "authority_quote")


def _draw_hook_type() -> str:
    """Weighted draw: ~15% authority_quote, rest split definition/rhetorical."""
    r = random.random()
    if r < lofi_cfg.AUTHORITY_QUOTE_PROBABILITY:
        return "authority_quote"
    if r < 0.5 + lofi_cfg.AUTHORITY_QUOTE_PROBABILITY / 2:
        return "definition"
    return "rhetorical_question"


def _extract_json_object(text: str) -> dict[str, Any]:
    raw = (text or "").strip()
    if not raw:
        raise ValueError("empty LLM response")
    # Strip fences
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw, re.IGNORECASE)
    if fence:
        raw = fence.group(1).strip()
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        data = json.loads(raw[start : end + 1])
        if isinstance(data, dict):
            return data
    raise ValueError("LLM response was not valid JSON object")


def _call_llm(prompt: str) -> str:
    import config as app_config
    from avatar_engine.providers.gemini_utils import (
        generate_content_with_model_fallback,
        make_gemini_client_with_fallback,
    )
    from avatar_engine.providers.model_router import CHEAP_TEXT_CHAIN, CHEAP_TEXT_PRIMARY

    api_key = getattr(app_config, "GEMINI_API_KEY", None)
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY missing — cannot run ScriptGeneratorAgent")
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
    return str(text or "")


def _fallback_script(
    *,
    module: str,
    theme: str,
    scene_count: int,
    hook_type: str,
    quote: dict[str, Any] | None,
) -> dict[str, Any]:
    """Deterministic offline script when LLM is unavailable."""
    if hook_type == "authority_quote" and quote:
        line0 = f"\"{quote['quote_text']}\" — {quote.get('author', '')}".strip()
        quote_id = quote.get("id")
    elif hook_type == "rhetorical_question":
        line0 = f"What if {theme.replace('_', ' ')} is quieter than you think?"
        quote_id = None
    else:
        line0 = f"{theme.replace('_', ' ').title()} is not a performance."
        quote_id = None

    templates = [
        line0,
        "It shows up in the small, repeated choices.",
        "Not the announcement. The follow-through.",
        "When presence stays after the excitement fades.",
        "When repair matters more than being right.",
        "That is where real connection lives.",
        "Choose the pattern that feels like home.",
        "Protect the peace you are building.",
        "Softness and strength can share the same room.",
        "You already know which path feels true.",
    ]
    lines = []
    for i in range(scene_count):
        text = templates[i % len(templates)] if i else templates[0]
        # Keep first line unique
        if i == 0:
            text = line0
        visual = (
            f"single symbolic figure in quiet domestic light, theme of {theme}, "
            f"scene {i + 1} of {scene_count}, {module} emotional beat, "
            "wide environmental framing, central subject"
        )
        lines.append({"scene": i + 1, "text": text[: lofi_cfg.MAX_CAPTION_CHARS], "visual_prompt": visual})

    out: dict[str, Any] = {
        "hook_type": hook_type if hook_type in _HOOK_TYPES else "definition",
        "theme": theme,
        "module": module,
        "lines": lines,
    }
    if quote_id:
        out["quote_id"] = quote_id
        out["quote_text"] = quote.get("quote_text") if quote else None
    return out


def generate_script(
    *,
    module: str,
    theme: str,
    subtheme: str = "",
    scene_count: int,
    feedback: str | None = None,
    force_hook_type: str | None = None,
) -> dict[str, Any]:
    """
    Generate a strict JSON script with per-scene caption + visual_prompt.

    Authority-quote hook is chosen by weighted random (not left to the LLM).
    """
    hook_type = force_hook_type or _draw_hook_type()
    quote: dict[str, Any] | None = None
    if hook_type == "authority_quote":
        quote = rag.pick_quote_for_theme(theme, module)
        if not quote:
            _LOG.warning(
                "No verified quote for theme=%s — falling back from authority_quote",
                theme,
            )
            hook_type = random.choice(["definition", "rhetorical_question"])

    refs = rag.get_reference_structures(module=module, limit=3)
    ref_block = json.dumps(refs, ensure_ascii=False, indent=2)

    quote_block = ""
    if hook_type == "authority_quote" and quote:
        quote_block = (
            "\nAUTHORITY QUOTE (use EXACTLY as first scene caption, do not paraphrase):\n"
            f'  id: {quote["id"]}\n'
            f'  text: "{quote["quote_text"]}"\n'
            f'  author: {quote.get("author")}\n'
            "Include quote_id in the JSON root.\n"
        )

    feedback_block = f"\nPREVIOUS REJECTION FEEDBACK (must fix):\n{feedback}\n" if feedback else ""

    prompt = f"""You write short vertical-reel scripts for a LOFI emotional illustration channel.

MODULE: {module}
THEME: {theme}
SUBTHEME: {subtheme or "(none)"}
SCENE_COUNT: {scene_count}
HOOK_TYPE (mandatory): {hook_type}
{quote_block}
{feedback_block}
FEW-SHOT REFERENCE STRUCTURES (formula only — do not copy verbatim):
{ref_block}

RULES:
- Output STRICT JSON only, no markdown.
- Schema:
  {{
    "hook_type": "{hook_type}",
    "theme": "{theme}",
    "module": "{module}",
    "quote_id": "<required only for authority_quote>",
    "lines": [
      {{"scene": 1, "text": "<on-screen caption>", "visual_prompt": "<image gen prompt>"}}
    ]
  }}
- Exactly {scene_count} lines, scene numbers 1..{scene_count}.
- Each "text" max {lofi_cfg.MAX_CAPTION_CHARS} characters, punchy, spoken-caption tone.
- Formula: hook → parallel emotional build → clear payoff.
- visual_prompt must describe character/setting/mood/camera for THAT scene's caption — never reuse caption text as visual_prompt.
- No NSFW, no real private individuals named, brand-safe tone for {module}.
- Never invent an authority quote. Only use the provided verified quote when hook_type is authority_quote.
"""

    try:
        raw = _call_llm(prompt)
        data = _extract_json_object(raw)
    except Exception as exc:  # noqa: BLE001
        _LOG.warning("ScriptGeneratorAgent LLM failed (%s) — using fallback script", exc)
        return _fallback_script(
            module=module,
            theme=theme,
            scene_count=scene_count,
            hook_type=hook_type,
            quote=quote,
        )

    data["hook_type"] = hook_type
    data["theme"] = theme
    data["module"] = module
    if quote and hook_type == "authority_quote":
        data["quote_id"] = quote["id"]
        data["quote_text"] = quote["quote_text"]
        # Force exact quote on scene 1 caption if LLM drifted
        lines = data.get("lines") or []
        if lines and isinstance(lines[0], dict):
            exact = f"\"{quote['quote_text']}\" — {quote.get('author', '')}".strip()
            if len(exact) > lofi_cfg.MAX_CAPTION_CHARS:
                exact = f"\"{quote['quote_text']}\""[: lofi_cfg.MAX_CAPTION_CHARS]
            lines[0]["text"] = exact
            data["lines"] = lines
    return data

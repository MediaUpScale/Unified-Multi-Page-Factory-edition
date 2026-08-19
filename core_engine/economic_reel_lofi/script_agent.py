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
from core_engine.economic_reel_lofi.visual_identity import (
    DEFAULT_SETTING_OBJECT_PAIRS,
    act_for_index,
    setting_object_pool,
)

_LOG = logging.getLogger(__name__)

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
    return str(text or "")


def _call_llm(prompt: str) -> str:
    """Claude Sonnet first; Gemini only if Claude is unavailable."""
    try:
        return _call_claude(prompt)
    except Exception as exc:  # noqa: BLE001
        _LOG.warning("Claude script writer failed (%s) — falling back to Gemini", exc)
        print(f"[LOFI script] WARN Claude failed ({exc}); Gemini fallback")
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


def _fallback_script(
    *,
    module: str,
    theme: str,
    scene_count: int,
    hook_type: str,
    quote: dict[str, Any] | None,
    setting_object_pairs: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Offline story fallback — interior, punchy, one short line per scene."""
    del quote
    theme_s = theme.replace("_", " ")
    pool = setting_object_pairs or [dict(p) for p in DEFAULT_SETTING_OBJECT_PAIRS]
    beats = [
        ("I set two unmatched cups.", "opening_hook"),
        ("I waited by the same door.", "longing"),
        ("The keys stayed in the bowl.", "quiet_sorrowful"),
        ("The list stayed on the fridge.", "doubt"),
        ("I opened the envelope later.", "realization"),
        ("The vow never left the table.", "realization"),
        ("He put the kettle on Tuesday.", "acceptance"),
        ("Same shoes wait on the stoop.", "hopeful_bittersweet"),
        ("I watch the door happen.", "resolution_warmth"),
    ]
    monologue = " ".join(b[0] for b in beats)
    lines = []
    for i in range(scene_count):
        text, emo = beats[i % len(beats)]
        pair = pool[i % len(pool)]
        lines.append(
            {
                "scene": i + 1,
                "text": _sanitize_caption_typos(text[: lofi_cfg.MAX_CAPTION_CHARS]),
                "beat_text": _sanitize_caption_typos(text[: lofi_cfg.MAX_CAPTION_CHARS]),
                "visual_prompt": f"story beat {i + 1} {theme_s}",
                "emotion": emo,
                "arc_position": act_for_index(i, scene_count),
                "subject_type": _FALLBACK_SUBJECTS[i % len(_FALLBACK_SUBJECTS)],
                "subject_expression": _FALLBACK_EXPR[i % len(_FALLBACK_EXPR)],
                "setting": pair["setting"],
                "key_object": pair["key_object"],
                "time_of_day": _FALLBACK_TOD[i % len(_FALLBACK_TOD)],
            }
        )
    first_pair = pool[0] if pool else {"setting": "kitchen at dawn", "key_object": "two unmatched coffee cups"}
    return {
        "hook_type": hook_type if hook_type in _HOOK_TYPES else "definition",
        "theme": theme,
        "module": module,
        "monologue": monologue,
        "lines": lines,
        "anchor_object": {
            "name": first_pair.get("key_object") or "coffee cups",
            "initial_state": "set out and waiting",
            "final_state": "used, still on the counter",
        },
        "arc_template": "setup_ache_acceptance",
        "retrieved_details": [],
    }


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
    hook_type = force_hook_type or _draw_hook_type()
    quote: dict[str, Any] | None = None
    if hook_type == "authority_quote":
        quote = rag.pick_quote_for_theme(theme, module)
        if not quote:
            hook_type = "definition"

    feedback_block = (
        f"\nPREVIOUS REJECTION FEEDBACK (must fix):\n{feedback}\n" if feedback else ""
    )
    pool = setting_object_pool(theme_row)
    pool_block = "\n".join(
        f'  - setting="{p["setting"]}" | key_object="{p["key_object"]}"'
        for p in pool
    )
    retrieved = rag.select_concrete_details(theme_row)
    arc = rag.select_arc_template(module, theme, subtheme)
    detail_block = "\n".join(
        f'  - ({d.get("sensory_type") or "object"}) {d.get("detail")}'
        for d in retrieved
    ) or "  - (none — invent from SETTING_OBJECT_POOL only)"
    act_guide = ", ".join(
        f"{i+1}:{act_for_index(i, scene_count)}" for i in range(scene_count)
    )

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
I set two unmatched cups.
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
   Every spoken line MUST pair a concrete noun (mug, key, door, coat, plate, phone)
   with an action (set, wait, hang, leave, open, rinse, tie). No pure-abstract beats
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
        )

    data["hook_type"] = hook_type
    data["theme"] = theme
    data["module"] = module
    data["retrieved_details"] = retrieved
    data["arc_template"] = str(data.get("arc_template") or arc.get("id") or "")
    ao = data.get("anchor_object") if isinstance(data.get("anchor_object"), dict) else {}
    data["anchor_object"] = {
        "name": str(ao.get("name") or "").strip(),
        "initial_state": str(ao.get("initial_state") or "").strip(),
        "final_state": str(ao.get("final_state") or "").strip(),
    }
    lines = data.get("lines") or []
    if not isinstance(lines, list):
        lines = []
    for i, row in enumerate(lines):
        if not isinstance(row, dict):
            continue
        row["scene"] = int(row.get("scene") or i + 1)
        row["arc_position"] = act_for_index(i, scene_count)
        if not row.get("emotion"):
            emo_arc = _ARC_9[i] if scene_count == 9 and i < 9 else "build"
            row["emotion"] = _EMOTION_BY_ARC.get(emo_arc, ("longing",))[0]
        row["text"] = _sanitize_caption_typos(
            str(row.get("text") or row.get("beat_text") or "")
        )[: lofi_cfg.MAX_CAPTION_CHARS]
        row["beat_text"] = row["text"]
        if not row.get("visual_prompt"):
            row["visual_prompt"] = f"story beat {i + 1}"
    data["lines"] = lines
    if not data.get("monologue"):
        data["monologue"] = " ".join(
            str(r.get("text") or "") for r in lines if isinstance(r, dict)
        )
    print("[LOFI script] monologue:", data.get("monologue"))
    print("[LOFI script] hook:", (lines[0].get("text") if lines else ""))
    print("[LOFI script] arc:", data.get("arc_template"))
    print("[LOFI script] anchor:", data.get("anchor_object"))
    return data

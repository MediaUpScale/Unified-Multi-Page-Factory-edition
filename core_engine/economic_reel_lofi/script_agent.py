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


def _extract_json_object(text: str) -> dict[str, Any]:
    raw = (text or "").strip()
    if not raw:
        raise ValueError("empty LLM response")
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
        max_tokens=2500,
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


def _fallback_script(
    *,
    module: str,
    theme: str,
    scene_count: int,
    hook_type: str,
    quote: dict[str, Any] | None,
) -> dict[str, Any]:
    """Offline story fallback — interior, punchy, one short line per scene."""
    del quote
    theme_s = theme.replace("_", " ")
    beats = [
        ("I believed the loudest promise.", "opening_hook", "opening"),
        ("So I waited for grand words.", "longing", "build"),
        ("The words kept coming.", "quiet_sorrowful", "build"),
        ("Nothing underneath them moved.", "doubt", "build"),
        ("Years later I saw it.", "realization", "turn"),
        ("The vow was never the point.", "realization", "turn"),
        ("He just showed up Tuesday.", "acceptance", "resolution"),
        ("Same door, every day.", "hopeful_bittersweet", "resolution"),
        ("Now I watch what happens.", "resolution_warmth", "resolution"),
    ]
    monologue = " ".join(b[0] for b in beats)
    lines = []
    for i in range(scene_count):
        text, emo, arc = beats[i % len(beats)]
        if i == 0:
            text, emo, arc = beats[0]
        lines.append(
            {
                "scene": i + 1,
                "text": text[: lofi_cfg.MAX_CAPTION_CHARS],
                "visual_prompt": f"story beat {i + 1} {theme_s}",
                "emotion": emo,
                "arc_position": arc,
            }
        )
    return {
        "hook_type": hook_type if hook_type in _HOOK_TYPES else "definition",
        "theme": theme,
        "module": module,
        "monologue": monologue,
        "lines": lines,
    }


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
    arc_guide = ", ".join(
        f"{i+1}:{_ARC_9[i] if scene_count == 9 and i < 9 else 'build'}"
        for i in range(scene_count)
    )

    prompt = f"""Write a spoken-word vertical-reel script.

MODULE: {module}
THEME: {theme}
SUBTHEME: {subtheme or "(none)"}
SCENE_COUNT: {scene_count}
ARC PER SCENE: {arc_guide}
{feedback_block}

This is ONE continuous first-person voice moving through a single small emotional arc:
setup → turn → insight → landing.
Not a list of standalone statements. Not maxims. Not advice.

SHAPE (illustrative — do not copy; match the causal chain):
I believed the loudest promise.
So I waited for grand words.
The words kept coming.
Nothing underneath them moved.
Years later I saw it.
The vow was never the point.
He just showed up Tuesday.
Same door, every day.
Now I watch what happens.

RULES:
1) Write "monologue" first as one continuous spoken piece. Then split THAT SAME text
   across exactly {scene_count} scene "text" fields, in order. Reading the lines aloud
   must reconstruct the monologue.
2) Each line MUST follow causally/emotionally from the previous one. If you can delete
   a line and the piece still makes sense, rewrite — the throughline is broken.
3) First person. Plain spoken language. Concrete imagery (a door, a Tuesday, a cup)
   over abstraction ("trust", "foundations", "declarations").
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
8) emotion per line from: opening_hook, quiet_sorrowful, melancholic_quiet, bittersweet,
   longing, doubt, realization, acceptance, hopeful_bittersweet, resolution_warmth,
   melancholic_romantic
9) arc_position must match ARC PER SCENE.
10) visual_prompt is a short stub only.

Output STRICT JSON only, no markdown:
{{
  "hook_type": "{hook_type}",
  "theme": "{theme}",
  "module": "{module}",
  "monologue": "<full continuous spoken-word story>",
  "lines": [
    {{
      "scene": 1,
      "text": "<scene 1 hook>",
      "emotion": "opening_hook",
      "arc_position": "opening",
      "visual_prompt": "stub"
    }}
  ]
}}
Exactly {scene_count} lines, scene numbers 1..{scene_count}.
No NSFW. No real private individuals named. Brand-safe for {module}.
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
    lines = data.get("lines") or []
    if not isinstance(lines, list):
        lines = []
    # Guarantee emotion + arc fields
    for i, row in enumerate(lines):
        if not isinstance(row, dict):
            continue
        row["scene"] = int(row.get("scene") or i + 1)
        if not row.get("arc_position"):
            row["arc_position"] = (
                _ARC_9[i] if scene_count == 9 and i < 9 else "build"
            )
        if not row.get("emotion"):
            arc = str(row.get("arc_position") or "build")
            row["emotion"] = _EMOTION_BY_ARC.get(arc, ("longing",))[0]
        row["text"] = str(row.get("text") or "")[: lofi_cfg.MAX_CAPTION_CHARS]
        if not row.get("visual_prompt"):
            row["visual_prompt"] = f"story beat {i + 1}"
    data["lines"] = lines
    if not data.get("monologue"):
        data["monologue"] = " ".join(
            str(r.get("text") or "") for r in lines if isinstance(r, dict)
        )
    print("[LOFI script] monologue:", data.get("monologue"))
    print("[LOFI script] hook:", (lines[0].get("text") if lines else ""))
    return data

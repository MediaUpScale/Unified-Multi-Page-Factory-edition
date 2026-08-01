# -*- coding: utf-8 -*-
"""
Together AI / FLUX — Dark Brutalist Dystopian & Ancient Monolithic prompts.

Formula:
  [SUBJECT & ACTION] + [BRUTALIST / ANCESTRAL ENVIRONMENT] + [MASTER STYLE ANCHOR]
"""
from __future__ import annotations

import base64
import logging
import re
import time
from pathlib import Path
from typing import Any

import requests
from rich.console import Console
from rich.panel import Panel

from VisualQA_Agent import config

_LOG = logging.getLogger(__name__)
_console = Console()

TOGETHER_IMAGES_URL = "https://api.together.xyz/v1/images/generations"

MANDATORY_NEGATIVE_PROMPT: str = getattr(
    config,
    "MANDATORY_NEGATIVE_PROMPT",
    (
        "gore, blood, open wounds, mouth, teeth, tongue, liquid dripping, smartphone, "
        "iPhone, glowing collar, neon choker, purple light, gym, tank top, sneakers, "
        "midjourney, --ar, --no"
    ),
)

_META_MARKERS: tuple[str, ...] = (
    "ZERO HALLUCINATION",
    "Avatar path:",
    "SHOT TYPE:",
    "LITERAL NOUN ANCHORS:",
    "SPOKEN BEAT:",
    "ANTI-MONOTONY:",
    "SUBJECT FREQUENCY CAP:",
)

_MJ_NO_RE = re.compile(r"--no\b[^\n,]*", re.IGNORECASE)
_MJ_AR_RE = re.compile(r"--ar\s*\d+\s*:\s*\d+", re.IGNORECASE)
_ACT_LABEL_RE = re.compile(r"^\s*ACT\s*\d+\s*[—\-:]\s*", re.IGNORECASE | re.MULTILINE)
_STRUCTURAL_LABEL_RE = re.compile(
    r"\b(?:MATRIX CUE|VISUAL DETAILS|CONCEPT CATEGORY|NARRATIVE SCRIPT BEAT"
    r"|KNOWN STYLE DNA|CORRECTION|AVOID|FORCE|ENV|APPAREL)\s*:\s*",
    re.IGNORECASE,
)
_NO_PHRASE_RE = re.compile(r"(?:,\s*)?\bNO\s+[^,.;]+", re.IGNORECASE)
_NARRATIVE_CUE_RE = re.compile(r"\bNarrative cue:\s*[^.]+\.?", re.IGNORECASE)

_BANNED_SOFT_RE = re.compile(
    r"\b(?:"
    r"gore|blood|bleeding|open\s+wounds?|visceral\s+horror|body\s+horror|"
    r"mutilation|grotesque|internal\s+organs?|fleshy\s+wires?|"
    r"liquid\s+dripping|mouth\s+fluid|black\s+fluid|"
    r"\bmouth\b|\bteeth\b|\btongue\b|"
    r"smartphones?|iphones?|cell\s*phones?|handheld\s+screens?|"
    r"glowing\s+collars?|neon\s+chokers?|neural\s+chokers?|neural\s+collars?|"
    r"purple\s+(?:fiber|light|neon|glow)|cyan\s+(?:optical|fiber|neon|glow)|"
    r"fiber-?optic\s+tethers?|luminous\s+purple|"
    r"tank\s*tops?|sneakers?|shorts|gym(?:\s*(?:clothes?|gear))?|"
    r"fitness\s*model|gym[\s-]?bros?|midjourney|"
    r"neon\s*sign(?:\s*(?:writing|text))?|lettering|"
    r"cyberpunk\s*monk|oriental\s*monk|meditation|"
    r"3d\s*render|cgi|H\.?\s*R\.?\s*Giger"
    r")\b[^,.]*",
    re.IGNORECASE,
)


def strip_metadata(raw_prompt: str) -> str:
    text = (raw_prompt or "").strip()
    if not text:
        return text
    for marker in _META_MARKERS:
        pattern = re.compile(
            rf"{re.escape(marker)}\s*:?.*?(?:\.(?=\s|$)|$)",
            re.IGNORECASE | re.DOTALL,
        )
        text = pattern.sub(" ", text)
    text = _MJ_NO_RE.sub(" ", text)
    text = _MJ_AR_RE.sub(" ", text)
    text = _ACT_LABEL_RE.sub("", text)
    text = _STRUCTURAL_LABEL_RE.sub("", text)
    text = _NO_PHRASE_RE.sub("", text)
    text = _BANNED_SOFT_RE.sub("", text)
    text = re.sub(r"\s*\n\s*", ", ", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"(?:\s*[,.]){2,}", ", ", text)
    return text.strip(" ,.\u2014-")


def _trim_words(text: str, max_words: int) -> str:
    words = text.split()
    if len(words) <= max_words:
        return text
    return " ".join(words[:max_words]).rstrip(" ,.")


def compile_flux_prompt(
    subject_action: str,
    environment: str = "",
    *,
    style_anchor: str | None = None,
    inject_anti_studio: bool = True,
) -> str:
    del inject_anti_studio
    subject = _trim_words(strip_metadata(subject_action), 45)
    env = _trim_words(strip_metadata(environment), 30)
    anchor = (style_anchor or config.MASTER_STYLE_ANCHOR).strip()
    parts = [p for p in (subject, env, anchor) if p]
    return sanitize_prompt_for_flux(", ".join(parts))


def sanitize_prompt_for_flux(
    raw_prompt: str,
    *,
    style_anchor: str | None = None,
    ensure_anchor: bool = True,
    inject_apparel: bool = False,
) -> str:
    del inject_apparel
    text = strip_metadata(raw_prompt)
    if not text:
        return text

    text = _NARRATIVE_CUE_RE.sub("", text).strip(" ,.")
    text = _BANNED_SOFT_RE.sub("", text)
    text = _MJ_NO_RE.sub(" ", text)
    text = _MJ_AR_RE.sub(" ", text)

    lo = text.lower()
    mei_present = "master mei" in lo or "mei," in lo or " mei " in f" {lo} "
    if mei_present:
        if "monolithic stone shrine" not in lo and "ash wasteland" not in lo:
            text = (
                f"an ancient dark monolithic stone shrine seamlessly integrated into "
                f"brutalist concrete architecture. {text}"
            )

    lo = text.lower()
    anchor = (style_anchor or config.MASTER_STYLE_ANCHOR).strip()
    if ensure_anchor and "35mm film grain" not in lo and "brutalist concrete" not in lo:
        text = f"{text}, {anchor}"

    text = re.sub(r"\s{2,}", " ", text)
    text = re.sub(r"(?:\s*[,.]){2,}", ", ", text)
    return _trim_words(text.strip(" ,.\u2014-"), int(config.MAX_PROMPT_WORDS))


def generate_image(
    prompt: str,
    *,
    output_path: Path | str,
    model: str | None = None,
    width: int | None = None,
    height: int | None = None,
    draft: bool = False,
    steps: int | None = None,
    seed: int | None = None,
    log_full_prompt: bool = True,
) -> tuple[Path, bytes, float, str]:
    if not config.TOGETHER_API_KEY:
        raise RuntimeError(
            "TOGETHER_API_KEY missing — set env or factory .env before generation."
        )

    clean = sanitize_prompt_for_flux(prompt)
    if not clean:
        raise ValueError("Prompt is empty after sanitization")

    model_id = model or config.FLUX_MODEL
    if steps is None:
        steps = 28 if "dev" in model_id.lower() else 4
    if width is None or height is None:
        width, height = (
            config.DRAFT_IMAGE_SIZE if draft else config.PRODUCTION_IMAGE_SIZE
        )

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    payload: dict[str, Any] = {
        "model": model_id,
        "prompt": clean,
        "width": width,
        "height": height,
        "steps": steps,
        "n": 1,
        "response_format": "b64_json",
        "negative_prompt": MANDATORY_NEGATIVE_PROMPT,
    }
    if seed is not None:
        payload["seed"] = int(seed)

    headers = {
        "Authorization": f"Bearer {config.TOGETHER_API_KEY}",
        "Content-Type": "application/json",
    }

    word_count = len(clean.split())
    _console.print(
        f"[magenta]FLUX[/magenta] model={model_id} steps={steps} "
        f"size={width}x{height} words={word_count} → {out}"
    )
    if log_full_prompt:
        _console.print(
            Panel(
                clean,
                title=f"[bold]SANITIZED PROMPT → Together AI[/bold] ({word_count} words)",
                border_style="magenta",
                expand=False,
            )
        )
        _console.print(f"[dim]NEGATIVE[/dim] {MANDATORY_NEGATIVE_PROMPT[:140]}")

    t0 = time.perf_counter()
    resp = requests.post(
        TOGETHER_IMAGES_URL, headers=headers, json=payload, timeout=180,
    )
    if resp.status_code >= 400 and "negative" in (resp.text or "").lower():
        payload.pop("negative_prompt", None)
        resp = requests.post(
            TOGETHER_IMAGES_URL, headers=headers, json=payload, timeout=180,
        )
    elapsed = time.perf_counter() - t0

    if resp.status_code >= 400:
        raise RuntimeError(
            f"Together AI error {resp.status_code}: {resp.text[:500]}"
        )

    data = resp.json()
    items = data.get("data") or []
    if not items:
        raise RuntimeError(f"Together AI returned no image data: {data!r}")

    item = items[0]
    if item.get("b64_json"):
        image_bytes = base64.b64decode(item["b64_json"])
    elif item.get("url"):
        img_resp = requests.get(item["url"], timeout=120)
        img_resp.raise_for_status()
        image_bytes = img_resp.content
    else:
        raise RuntimeError(f"Unexpected Together image payload: {item!r}")

    out.write_bytes(image_bytes)
    cost = config.estimate_flux_cost(model_id)
    if config.LOG_COSTS:
        _console.print(
            f"[dim]COST[/dim] flux≈${cost:.4f} | {elapsed:.1f}s | "
            f"{len(image_bytes)} bytes → {out}"
        )
    _LOG.info("FLUX | saved %s (%d bytes, %.1fs, ~$%.4f)", out, len(image_bytes), elapsed, cost)
    return out, image_bytes, cost, clean

# -*- coding: utf-8 -*-
"""Shared Gemini usage / cost helpers for OCR and paraphrase."""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

# Official Gemini 2.5 Flash list prices (USD / 1M tokens)
FLASH_INPUT_USD_PER_1M: float = 0.075
FLASH_OUTPUT_USD_PER_1M: float = 0.30

_FENCE_RE = re.compile(
    r"^```(?:[a-zA-Z0-9_-]+)?\r?\n(.*)\r?\n```\s*$",
    re.DOTALL,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GeminiUsage:
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def cost_usd(self) -> float:
        return flash_cost_usd(self.input_tokens, self.output_tokens)

    def __add__(self, other: GeminiUsage) -> GeminiUsage:
        return GeminiUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
        )


def hush_gemini_sdk_logs() -> None:
    """Hide SDK chatter so only our cost / result lines stay visible."""
    for name in ("httpx", "httpcore", "google_genai", "google_genai.models"):
        logging.getLogger(name).setLevel(logging.WARNING)


def flash_cost_usd(input_tokens: int, output_tokens: int) -> float:
    inp = max(0, int(input_tokens or 0))
    out = max(0, int(output_tokens or 0))
    return (inp * FLASH_INPUT_USD_PER_1M + out * FLASH_OUTPUT_USD_PER_1M) / 1_000_000.0


def extract_usage(response: Any) -> GeminiUsage:
    prompt = 0
    output = 0
    thoughts = 0
    um = getattr(response, "usage_metadata", None)
    if um is None and isinstance(response, dict):
        um = response.get("usage_metadata")
    if isinstance(um, dict):
        prompt = int(float(um.get("prompt_token_count") or 0))
        output = int(float(um.get("candidates_token_count") or um.get("output_token_count") or 0))
        thoughts = int(float(um.get("thoughts_token_count") or 0))
    elif um is not None:
        prompt = int(float(getattr(um, "prompt_token_count", 0) or 0))
        output = int(
            float(
                getattr(um, "candidates_token_count", 0)
                or getattr(um, "output_token_count", 0)
                or 0
            )
        )
        thoughts = int(float(getattr(um, "thoughts_token_count", 0) or 0))
    return GeminiUsage(input_tokens=prompt, output_tokens=output + thoughts)


def format_usage_line(label: str, usage: GeminiUsage) -> str:
    return (
        f"{label} | tokens in={usage.input_tokens} out={usage.output_tokens} "
        f"| est ${usage.cost_usd:.4f}"
    )


def log_usage(label: str, usage: GeminiUsage) -> None:
    logger.info(format_usage_line(label, usage))


def extract_json_object(raw: str) -> str:
    text = (raw or "").strip()
    fenced = _FENCE_RE.match(text)
    if fenced:
        text = fenced.group(1).strip()
    if text.startswith("{") and text.endswith("}"):
        return text
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return text[start : end + 1]
    raise json.JSONDecodeError("No JSON object in model response", text, 0)


def unwrap_json_object(raw: str) -> dict[str, Any]:
    parsed = json.loads(extract_json_object(raw))
    if not isinstance(parsed, dict):
        raise RuntimeError("Gemini payload must be a JSON object")
    return parsed

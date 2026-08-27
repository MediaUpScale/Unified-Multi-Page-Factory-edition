# -*- coding: utf-8 -*-
"""
FLUX.1-schnell prompt compiler — natural-language limits & scene anchors.

Absolute constraints for every dystopian / RAG image prompt:
  * Length: 120–180 words (hard safety cap ~2000 characters)
  * Format: descriptive natural sentences (no quality tag spam)
  * Mandatory prefix (dystopian scenes):
      "Cyberpunk dystopian Earth, attention monopoly era:"
  * Environment: sensory / attention overload or dystopian architecture
"""
from __future__ import annotations

import re
from typing import Iterable

# ---------------------------------------------------------------------------
# FLUX generation rules
# ---------------------------------------------------------------------------
FLUX_MIN_WORDS: int = 120
FLUX_MAX_WORDS: int = 180
FLUX_MAX_CHARS: int = 2000  # generous char safety net (~180 words)

FLUX_MANDATORY_PREFIX: str = "Cyberpunk dystopian Earth, attention monopoly era:"

# Tag-spam tokens banned for FLUX Schnell
_TAG_SPAM_RE = re.compile(
    r"\b(?:"
    r"8k|4k|uhd|hdr|masterpiece|best\s+quality|ultra\s+(?:detailed|realistic)|"
    r"hyper[- ]?detailed|highly\s+detailed|trending\s+on\s+artstation|"
    r"octane\s+render|unreal\s+engine|raw\s+photography|"
    r"photorealistic(?:\s+shot)?|hyperrealistic|dslr|sharp\s+focus"
    r")\b",
    re.IGNORECASE,
)
_WS_RE = re.compile(r"\s+")
_DUP_PUNCT_RE = re.compile(r"(?:\s*[,.]){2,}")
_PREFIX_RE = re.compile(
    r"^\s*cyberpunk\s+dystopian\s+earth\s*,?\s*attention\s+monopoly\s+era\s*:\s*",
    re.IGNORECASE,
)


def strip_flux_tag_spam(text: str) -> str:
    """Remove Midjourney-style quality tags; keep natural descriptive prose."""
    cleaned = _TAG_SPAM_RE.sub(" ", text or "")
    cleaned = _WS_RE.sub(" ", cleaned)
    cleaned = _DUP_PUNCT_RE.sub(". ", cleaned)
    return cleaned.strip(" ,.\u2014-")


def _word_count(text: str) -> int:
    return len((text or "").split())


def ensure_flux_prefix(
    text: str,
    *,
    require_prefix: bool = True,
) -> str:
    """Prepend the mandatory cyberpunk attention-monopoly prefix when required."""
    body = (text or "").strip()
    if not body:
        return FLUX_MANDATORY_PREFIX if require_prefix else ""
    if not require_prefix:
        return body
    if _PREFIX_RE.match(body):
        # Normalize casing / punctuation of an existing prefix
        rest = _PREFIX_RE.sub("", body).strip()
        return f"{FLUX_MANDATORY_PREFIX} {rest}".strip()
    return f"{FLUX_MANDATORY_PREFIX} {body}".strip()


def trim_flux_words(
    text: str,
    *,
    max_words: int = FLUX_MAX_WORDS,
) -> str:
    """Hard-cap word count while preserving leading subject/action clauses."""
    words = (text or "").split()
    if len(words) <= max_words:
        return (text or "").strip()
    return " ".join(words[:max_words]).rstrip(" ,.")


def truncate_flux_prompt(
    prompt_text: str,
    *,
    max_chars: int = FLUX_MAX_CHARS,
) -> str:
    """Hard truncate FLUX prompts to ``max_chars`` as a final API safeguard."""
    final_flux_prompt = (prompt_text or "")[:max_chars].strip()
    return final_flux_prompt


def compile_flux_natural_prompt(
    subject: str,
    action: str = "",
    environment: str = "",
    *,
    require_prefix: bool = True,
) -> str:
    """
    Assemble a FLUX prompt:

    ``[Subject & Physical Details] + [Action & Human Emotion]
     + [Environment & Lighting]``
    """
    parts = [p.strip(" ,.") for p in (subject, action, environment) if p and p.strip()]
    assembled = ". ".join(parts)
    if assembled and not assembled.endswith("."):
        assembled = f"{assembled}."
    return finalize_flux_prompt(assembled, require_prefix=require_prefix)


def finalize_flux_prompt(
    prompt_text: str,
    *,
    max_words: int = FLUX_MAX_WORDS,
    max_chars: int = FLUX_MAX_CHARS,
    require_prefix: bool = True,
) -> str:
    """
    Last-mile FLUX safeguard:
    strip tag spam → mandatory prefix → word budget → char truncate.
    """
    text = strip_flux_tag_spam(prompt_text)
    text = _WS_RE.sub(" ", text).strip(" ,.")
    text = ensure_flux_prefix(text, require_prefix=require_prefix)
    text = trim_flux_words(text, max_words=max_words)
    # Hard truncate as bulletproof API fallback
    final_flux_prompt = text[:max_chars].strip()
    return final_flux_prompt


def flux_prompt_stats(prompt_text: str) -> dict[str, int | bool]:
    """Diagnostics helper for tests / logging."""
    text = prompt_text or ""
    words = _word_count(text)
    return {
        "chars": len(text),
        "words": words,
        "within_word_budget": FLUX_MIN_WORDS <= words <= FLUX_MAX_WORDS,
        "within_char_budget": len(text) <= FLUX_MAX_CHARS,
        "has_tag_spam": bool(_TAG_SPAM_RE.search(text)),
        "has_mandatory_prefix": bool(_PREFIX_RE.match(text)),
    }


def assert_flux_lines_within_limit(
    lines: Iterable[str],
    *,
    max_chars: int = FLUX_MAX_CHARS,
) -> list[str]:
    """Return truncated copies of each line (≤ ``max_chars``)."""
    return [truncate_flux_prompt(line, max_chars=max_chars) for line in lines]

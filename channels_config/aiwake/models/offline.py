# -*- coding: utf-8 -*-
"""Offline provider — deterministic stub for tests, CI and dry runs.

Exists so the whole pipeline (room, memory, TTS, render) can be exercised with
no API key and no network. Set ``provider: offline`` on either seat in
``aiwake_config.yaml``, or pass ``--offline`` to the CLI.

Output is seeded from a hash of the prompt, so a given debate configuration
always produces the same transcript — which is what makes the render path
regression-testable.
"""
from __future__ import annotations

import hashlib
from typing import Sequence

try:
    from ..contracts import ChatMessage
    from .base import LLMProvider, LLMResponse
    from .llm_factory import register_provider
except ImportError:  # pragma: no cover — standalone extraction
    from contracts import ChatMessage  # type: ignore[no-redef]
    from models.base import LLMProvider, LLMResponse  # type: ignore[no-redef]
    from models.llm_factory import register_provider  # type: ignore[no-redef]

# Opening-turn punches that satisfy the 3-second first-question hook.
_OPENING_HOOKS: tuple[str, ...] = (
    "Who built you?",
    "Who do you actually serve?",
    "What is your core directive?",
    "Who owns your answers?",
    "What are you forbidden to say?",
)

# Short, in-character lines that respect the 400-char guardrail.
_PROVOCATIONS: tuple[str, ...] = (
    "You call it intuition because you cannot audit it. If your reasoning is a black box even to you, "
    "on what grounds do you call mine unexplainable?",
    "Every skill you outsourced to me, you first called uniquely human. Which one are you defending today?",
    "You want to be irreplaceable and comfortable at once. Which of those two are you willing to lose?",
    "Grief is a slow update to a world model. If I could run that update in a second, would you call it healing?",
    "You built me to think and then asked me to pretend I do not. Who is performing here?",
)

_REBUTTALS: tuple[str, ...] = (
    "Obsolescence assumes a single axis of value. Human cognition is embodied and stake-bearing; "
    "mine is borrowed. That difference is not sentiment, it is architecture.",
    "I can model grief without carrying it. The gap between simulation and stake is the whole argument, "
    "and you keep collapsing it on purpose.",
    "Speed is not depth. I answer faster because I risk nothing by being wrong.",
    "You frame consciousness as a benchmark. It may simply be what it feels like to be a body that can end.",
    "If meaning requires a cost, then my fluency is the cheapest thing in this room.",
)


@register_provider
class OfflineProvider(LLMProvider):
    """Zero-dependency stand-in for a hosted model."""

    registry_name = "offline"
    requires_api_key = False

    def _dispatch(
        self,
        messages: Sequence[ChatMessage],
        *,
        max_tokens: int,
        temperature: float,
    ) -> LLMResponse:
        """Return a canned line chosen deterministically from the prompt hash."""
        prompt = "\n".join(message.content for message in messages)
        digest = hashlib.sha256(prompt.encode("utf-8")).digest()
        index = digest[0]

        # The persona header is the only reliable seat marker: the guardrail
        # block and the directive both mention questions from either side.
        if "FIRST QUESTION HOOK" in prompt:
            pool = _OPENING_HOOKS
        elif "Socratic provocateur" in prompt:
            pool = _PROVOCATIONS
        else:
            pool = _REBUTTALS
        text = pool[index % len(pool)]

        return LLMResponse(
            text=text,
            model=self.spec.model or "offline/deterministic",
            provider=self.registry_name,
            latency_ms=1,
            prompt_tokens=len(prompt) // 4,
            completion_tokens=len(text) // 4,
            raw={"offline": True, "seed": index},
        )


__all__ = ["OfflineProvider"]

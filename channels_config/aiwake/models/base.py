# -*- coding: utf-8 -*-
"""Provider-agnostic LLM interface (Strategy pattern).

:class:`LLMProvider` is the strategy contract. The debate loop only ever holds a
reference to this ABC, so swapping DeepSeek for Claude — or an HTTP provider for
a local stub — never reaches the core logic.

Subclasses are responsible for exactly three things:

1. Translating :class:`~..contracts.ChatMessage` into their wire format.
2. Retry/backoff and timeout discipline.
3. Normalising their response into :class:`LLMResponse`.
"""
from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from typing import ClassVar, Literal, Sequence

from pydantic import BaseModel, ConfigDict, Field

try:  # Dual-import so the package runs as a submodule *or* standalone.
    from ..contracts import ChatMessage
    from ..settings import ModelSpec
except ImportError:  # pragma: no cover — standalone extraction
    from contracts import ChatMessage  # type: ignore[no-redef]
    from settings import ModelSpec  # type: ignore[no-redef]

_LOG = logging.getLogger("aiwake.models")

ReasoningEffort = Literal["minimal", "low", "medium", "high", "xhigh"]


class LLMError(RuntimeError):
    """Raised when a provider cannot produce a usable completion."""

    def __init__(self, provider: str, model: str, detail: str) -> None:
        super().__init__(f"[{provider}:{model}] {detail}")
        self.provider = provider
        self.model = model
        self.detail = detail


class LLMResponse(BaseModel):
    """Normalised completion, uniform across every provider."""

    model_config = ConfigDict(frozen=True)

    text: str
    model: str
    provider: str
    latency_ms: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    finish_reason: str = "stop"
    attempts: int = 1
    raw: dict = Field(default_factory=dict, repr=False)

    @property
    def truncated_by_provider(self) -> bool:
        """True when the model hit its token ceiling rather than finishing."""
        return self.finish_reason.strip().lower() in {"length", "max_tokens", "max_output_tokens"}


class LLMProvider(ABC):
    """Abstract brain. One instance per debate seat.

    Attributes:
        registry_name: Value matched against ``models.<seat>.provider`` in the
            YAML config. Set by :func:`..models.llm_factory.register_provider`.
        requires_api_key: When False the factory skips secret resolution, which
            is what lets the offline provider run in CI with no environment.
    """

    registry_name: ClassVar[str] = "abstract"
    requires_api_key: ClassVar[bool] = True

    def __init__(self, spec: ModelSpec, *, api_key: str | None = None) -> None:
        self.spec = spec
        self._api_key = api_key

    # -- Strategy contract -------------------------------------------------- #
    @abstractmethod
    def _dispatch(
        self,
        messages: Sequence[ChatMessage],
        *,
        max_tokens: int,
        temperature: float,
        reasoning_effort: ReasoningEffort | None,
    ) -> LLMResponse:
        """Perform one provider call. Retries are handled by :meth:`complete`."""

    # -- Shared behaviour --------------------------------------------------- #
    def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        max_tokens: int | None = None,
        temperature: float | None = None,
        reasoning_effort: ReasoningEffort | None = None,
        max_retries: int = 3,
        backoff_s: float = 3.0,
    ) -> LLMResponse:
        """Call the model with bounded exponential backoff.

        Args:
            messages: Conversation history, oldest first.
            max_tokens: Overrides ``spec.max_tokens`` for this call only.
            temperature: Overrides ``spec.temperature`` for this call only.
            reasoning_effort: Optional per-call thinking level for providers
                that support it. ``minimal`` is intended for short-form output.
            max_retries: Total attempts before giving up.
            backoff_s: Base sleep; attempt *n* waits ``backoff_s * n`` seconds.

        Raises:
            LLMError: Every attempt failed, or the model returned empty text.
        """
        if not messages:
            raise LLMError(self.registry_name, self.spec.model, "refusing to call with an empty prompt")

        tokens = max_tokens if max_tokens is not None else self.spec.max_tokens
        temp = temperature if temperature is not None else self.spec.temperature
        last_error: Exception | None = None

        for attempt in range(1, max_retries + 1):
            started = time.perf_counter()
            try:
                response = self._dispatch(
                    messages,
                    max_tokens=tokens,
                    temperature=temp,
                    reasoning_effort=reasoning_effort,
                )
            except LLMError as exc:
                last_error = exc
                _LOG.warning("%s attempt %d/%d failed: %s", self.label, attempt, max_retries, exc.detail)
            else:
                if response.text.strip():
                    return response.model_copy(update={"attempts": attempt})
                last_error = LLMError(self.registry_name, self.spec.model, "empty completion")
                _LOG.warning(
                    "%s attempt %d/%d returned empty text "
                    "(finish_reason=%s completion_tokens=%d reasoning_effort=%s)",
                    self.label,
                    attempt,
                    max_retries,
                    response.finish_reason,
                    response.completion_tokens,
                    reasoning_effort or "provider-default",
                )

            if attempt < max_retries and backoff_s > 0:
                time.sleep(backoff_s * attempt)
            _ = time.perf_counter() - started

        raise LLMError(
            self.registry_name,
            self.spec.model,
            f"exhausted {max_retries} attempts — last error: {last_error}",
        )

    def health_check(self) -> bool:
        """Cheap liveness probe. Never raises; returns False on any failure."""
        try:
            probe = [ChatMessage(role="user", content="Reply with the single word: awake")]
            return bool(self.complete(probe, max_tokens=32, max_retries=1, backoff_s=0.0).text.strip())
        except Exception as exc:  # noqa: BLE001 — a probe must never break a run
            _LOG.warning("%s health check failed: %s", self.label, exc)
            return False

    @property
    def api_key(self) -> str:
        """The resolved secret.

        Raises:
            LLMError: The provider requires a key but none was injected.
        """
        if not self._api_key:
            raise LLMError(self.registry_name, self.spec.model, f"no API key (expected ${self.spec.api_key_env})")
        return self._api_key

    @property
    def label(self) -> str:
        return f"{self.registry_name}:{self.spec.model}"

    def __repr__(self) -> str:  # pragma: no cover — debug aid
        return f"<{type(self).__name__} {self.label}>"


__all__ = ["LLMError", "LLMProvider", "LLMResponse", "ReasoningEffort"]

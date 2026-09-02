# -*- coding: utf-8 -*-
"""OpenRouter provider — one HTTP gateway to every hosted model.

Uses ``requests`` directly rather than the OpenAI SDK: the payload is trivial,
and a raw dependency keeps the module extractable without pulling a vendor SDK
whose breaking changes we would have to track.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Sequence

try:
    from ..contracts import ChatMessage
    from ..settings import ModelSpec, OpenRouterConfig
    from .base import LLMError, LLMProvider, LLMResponse, ReasoningEffort
    from .llm_factory import register_provider
except ImportError:  # pragma: no cover — standalone extraction
    from contracts import ChatMessage  # type: ignore[no-redef]
    from models.base import (  # type: ignore[no-redef]
        LLMError,
        LLMProvider,
        LLMResponse,
        ReasoningEffort,
    )
    from models.llm_factory import register_provider  # type: ignore[no-redef]
    from settings import ModelSpec, OpenRouterConfig  # type: ignore[no-redef]

_LOG = logging.getLogger("aiwake.models.openrouter")

# Statuses worth another attempt; anything else is a hard client error.
_RETRYABLE_STATUS: frozenset[int] = frozenset({408, 409, 425, 429, 500, 502, 503, 504, 522, 524})


@register_provider
class OpenRouterProvider(LLMProvider):
    """Chat completions via ``https://openrouter.ai/api/v1/chat/completions``.

    The model slug is passed straight through, so any OpenRouter-routable model
    works by editing ``aiwake_config.yaml`` alone.
    """

    registry_name = "openrouter"
    requires_api_key = True

    def __init__(
        self,
        spec: ModelSpec,
        *,
        api_key: str | None = None,
        gateway: OpenRouterConfig | None = None,
    ) -> None:
        super().__init__(spec, api_key=api_key)
        self.gateway = gateway or OpenRouterConfig()
        self._session: Any | None = None
        self._slug_recovered = False

    # -- HTTP plumbing ------------------------------------------------------ #
    def _get_session(self) -> Any:
        """Lazily build a pooled session so keep-alive survives across turns."""
        if self._session is not None:
            return self._session
        try:
            import requests  # noqa: PLC0415
        except ImportError as exc:  # pragma: no cover
            raise LLMError(self.registry_name, self.spec.model, "requests is not installed") from exc

        session = requests.Session()
        session.headers.update(
            {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                # OpenRouter attribution headers (optional but polite).
                "HTTP-Referer": self.gateway.referer,
                "X-Title": self.gateway.title,
            }
        )
        self._session = session
        return session

    def _dispatch(
        self,
        messages: Sequence[ChatMessage],
        *,
        max_tokens: int,
        temperature: float,
        reasoning_effort: ReasoningEffort | None,
    ) -> LLMResponse:
        """POST one chat completion and normalise the result.

        Raises:
            LLMError: Transport failure, retryable HTTP status, or a body that
                does not contain a usable choice.
        """
        session = self._get_session()
        payload: dict[str, Any] = {
            "model": self.spec.model,
            "messages": [message.to_wire() for message in messages],
            "max_tokens": max_tokens,
            "temperature": temperature,
            # Discourage the model from drifting into list-shaped answers.
            "frequency_penalty": 0.25,
            "presence_penalty": 0.15,
        }
        if reasoning_effort is not None:
            # Gemini 3.x reasoning is mandatory on OpenRouter, but its thinking
            # level is controllable per request. Short classifiers and verdicts
            # use ``minimal`` so hidden thinking cannot consume their output.
            payload["reasoning"] = {
                "effort": reasoning_effort,
                "exclude": True,
            }

        started = time.perf_counter()
        response = self._post(session, payload)
        latency_ms = int((time.perf_counter() - started) * 1000)
        body, error = self._parse_body(response)

        if self._is_missing_model(response.status_code, response.text, error):
            # One live catalog refetch + closest-slug remap, then a single
            # immediate retry. Happens before the base class burns its retry
            # budget (and before a 404, which is not retryable, aborts the session).
            if self._recover_retired_slug():
                payload["model"] = self.spec.model
                started = time.perf_counter()
                response = self._post(session, payload)
                latency_ms = int((time.perf_counter() - started) * 1000)
                body, error = self._parse_body(response)

        if response.status_code in _RETRYABLE_STATUS:
            raise LLMError(
                self.registry_name,
                self.spec.model,
                f"retryable HTTP {response.status_code}: {response.text[:200]}",
            )
        if response.status_code >= 400:
            raise LLMError(
                self.registry_name,
                self.spec.model,
                f"HTTP {response.status_code}: {response.text[:300]}",
            )

        if body is None:
            raise LLMError(self.registry_name, self.spec.model, "response was not JSON")

        # OpenRouter surfaces upstream provider failures inside a 200 body.
        if error is not None:
            detail = str(error.get("message", error))
            raise LLMError(self.registry_name, self.spec.model, f"gateway error: {detail[:300]}")

        choices = body.get("choices") or []
        if not choices:
            raise LLMError(self.registry_name, self.spec.model, "no choices in response")

        choice = choices[0]
        text = str((choice.get("message") or {}).get("content") or "").strip()
        usage = body.get("usage") or {}

        return LLMResponse(
            text=text,
            model=str(body.get("model") or self.spec.model),
            provider=self.registry_name,
            latency_ms=latency_ms,
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
            finish_reason=str(choice.get("finish_reason") or "stop"),
            raw=body,
        )

    def _post(self, session: Any, payload: dict[str, Any]) -> Any:
        """One chat-completions POST. Transport errors become :class:`LLMError`."""
        import requests  # noqa: PLC0415

        try:
            return session.post(
                f"{self.gateway.base_url.rstrip('/')}/chat/completions",
                data=json.dumps(payload).encode("utf-8"),
                timeout=self.spec.timeout_s,
            )
        except requests.RequestException as exc:
            raise LLMError(self.registry_name, self.spec.model, f"transport error: {exc}") from exc

    @staticmethod
    def _parse_body(response: Any) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        """Return ``(body, error_object)``. Body is None when the payload is not JSON."""
        try:
            body = response.json()
        except ValueError:
            return None, None
        if not isinstance(body, dict):
            return None, None
        error = body.get("error")
        return body, error if isinstance(error, dict) else None

    def _is_missing_model(self, status_code: int, text: str, error: dict[str, Any] | None) -> bool:
        try:
            from .sync import looks_like_missing_model  # noqa: PLC0415
        except ImportError:  # pragma: no cover — standalone extraction
            from models.sync import looks_like_missing_model  # type: ignore[no-redef]
        code = (error or {}).get("code")
        return looks_like_missing_model(status_code, text, error_code=code)

    def _recover_retired_slug(self) -> bool:
        """Refetch the live catalog once and remap ``self.spec.model`` if possible.

        Returns True when the spec was rewritten and the caller should POST again.
        Subsequent 404s on the same instance are not re-synced — a second miss
        means the successor is also gone, and retrying the catalog GET would
        only delay the abort.
        """
        if self._slug_recovered:
            return False
        self._slug_recovered = True
        try:
            from .sync import SyncError, sync_openrouter_models  # noqa: PLC0415
        except ImportError:  # pragma: no cover — standalone extraction
            from models.sync import SyncError, sync_openrouter_models  # type: ignore[no-redef]

        try:
            catalog = sync_openrouter_models(
                force=True,
                persist=True,
                timeout_s=12.0,
                gateway=self.gateway,
            )
        except SyncError as exc:
            _LOG.warning("404-guard catalog fetch failed: %s", exc.detail)
            return False

        resolved, changed = catalog.remap(self.spec.model)
        if not changed:
            _LOG.warning("404-guard: %s is unroutable and no successor is in the live catalog", self.spec.model)
            return False

        _LOG.warning(
            "404-guard: %s is unroutable; remapping to %s and retrying once",
            self.spec.model,
            resolved,
        )
        self.spec = self.spec.model_copy(update={"model": resolved})
        return True

    def complete(self, messages: Sequence[ChatMessage], **kwargs: Any) -> LLMResponse:
        """Apply the gateway's retry policy unless the caller overrides it."""
        kwargs.setdefault("max_retries", self.gateway.max_retries)
        kwargs.setdefault("backoff_s", self.gateway.backoff_s)
        return super().complete(messages, **kwargs)

    def close(self) -> None:
        """Release the pooled connection."""
        if self._session is not None:
            self._session.close()
            self._session = None
            _LOG.debug("%s session closed", self.label)


__all__ = ["OpenRouterProvider"]

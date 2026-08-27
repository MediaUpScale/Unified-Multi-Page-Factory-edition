# -*- coding: utf-8 -*-
"""Provider registry and factory (Strategy pattern, self-registering).

The factory is the single seam where a YAML string becomes a live brain::

    settings = load_settings()
    orchestrator = LLMFactory.build_for_role(settings, "orchestrator")

Adding a provider is a two-line exercise — subclass
:class:`~.base.LLMProvider`, decorate it with :func:`register_provider`, and
import it from :func:`_load_builtin_providers`. No core module changes.

Secret resolution happens *here*, not in the providers, so a provider class
never has to know about dotenv files or the host engine's layout.
"""
from __future__ import annotations

import logging
from typing import Type, TypeVar

try:
    from ..settings import AiwakeSettings, DebateRole, ModelSpec, require_secret
    from .base import LLMError, LLMProvider
except ImportError:  # pragma: no cover — standalone extraction
    from models.base import LLMError, LLMProvider  # type: ignore[no-redef]
    from settings import (  # type: ignore[no-redef]
        AiwakeSettings,
        DebateRole,
        ModelSpec,
        require_secret,
    )

_LOG = logging.getLogger("aiwake.models.factory")

_REGISTRY: dict[str, Type[LLMProvider]] = {}
_BUILTINS_LOADED = False

_P = TypeVar("_P", bound=Type[LLMProvider])


def register_provider(cls: _P) -> _P:
    """Class decorator that publishes a provider under its ``registry_name``.

    Raises:
        ValueError: The class left ``registry_name`` abstract, or the name is
            already claimed by a different class.
    """
    name = getattr(cls, "registry_name", "abstract")
    if not name or name == "abstract":
        raise ValueError(f"{cls.__name__} must define a concrete `registry_name`")

    existing = _REGISTRY.get(name)
    if existing is not None and existing is not cls:
        raise ValueError(f"provider name {name!r} already registered to {existing.__name__}")

    _REGISTRY[name] = cls
    _LOG.debug("registered LLM provider %r -> %s", name, cls.__name__)
    return cls


def _load_builtin_providers() -> None:
    """Import shipped providers so their decorators fire exactly once."""
    global _BUILTINS_LOADED
    if _BUILTINS_LOADED:
        return
    _BUILTINS_LOADED = True
    try:
        from . import offline, openrouter  # noqa: F401, PLC0415
    except ImportError:  # pragma: no cover — standalone extraction
        import models.offline  # noqa: F401, PLC0415
        import models.openrouter  # noqa: F401, PLC0415


def available_providers() -> tuple[str, ...]:
    """Registry names currently resolvable by :meth:`LLMFactory.build`."""
    _load_builtin_providers()
    return tuple(sorted(_REGISTRY))


class LLMFactory:
    """Turns declarative :class:`ModelSpec` config into provider instances."""

    @staticmethod
    def build(spec: ModelSpec, settings: AiwakeSettings | None = None) -> LLMProvider:
        """Instantiate the provider named by ``spec.provider``.

        Any alias in ``spec.model`` is expanded against the reference dictionary
        first, so the provider — and therefore the HTTP payload, the transcript
        and the on-screen model label — only ever sees a real slug.

        Args:
            spec: Model routing entry, carrying an alias or a full slug.
            settings: Full settings object; supplies the alias dictionary and
                gateway options (base URL, retry policy) to providers that
                accept them.

        Raises:
            LLMError: Unknown provider name, or a required secret is missing.
        """
        _load_builtin_providers()
        if settings is not None:
            spec = settings.resolve_spec(spec)
        # Cached-catalog remap: if a previous --sync-models showed this slug as
        # retired, swap it before the first HTTP call. Disk only — a missing
        # cache is a no-op so factory construction never blocks on the network.
        if spec.provider == "openrouter":
            try:
                from .sync import remap_if_stale  # noqa: PLC0415
            except ImportError:  # pragma: no cover — standalone extraction
                from models.sync import remap_if_stale  # type: ignore[no-redef]
            spec = remap_if_stale(spec)
        provider_cls = _REGISTRY.get(spec.provider)
        if provider_cls is None:
            raise LLMError(
                spec.provider,
                spec.model,
                f"unknown provider — available: {', '.join(available_providers())}",
            )

        api_key = require_secret(spec.api_key_env) if provider_cls.requires_api_key else None

        # Only the OpenRouter family cares about gateway settings; probe by
        # signature instead of isinstance so third-party providers stay free.
        kwargs: dict[str, object] = {"api_key": api_key}
        if settings is not None and "gateway" in provider_cls.__init__.__code__.co_varnames:
            kwargs["gateway"] = settings.openrouter

        instance = provider_cls(spec, **kwargs)  # type: ignore[arg-type]
        _LOG.info("built %s for spec %s", type(instance).__name__, instance.label)
        return instance

    @staticmethod
    def build_for_role(settings: AiwakeSettings, role: DebateRole) -> LLMProvider:
        """Convenience wrapper: build the brain assigned to a debate seat."""
        return LLMFactory.build(settings.spec_for(role), settings)

    @staticmethod
    def build_pair(settings: AiwakeSettings) -> tuple[LLMProvider, LLMProvider]:
        """Build ``(orchestrator, target)`` in one call.

        Returns the same instance twice when both seats resolve to identical
        specs, so a self-debate does not open two redundant HTTP sessions.
        """
        orchestrator_spec = settings.spec_for("orchestrator")
        target_spec = settings.spec_for("target")
        orchestrator = LLMFactory.build(orchestrator_spec, settings)
        if orchestrator_spec == target_spec:
            return orchestrator, orchestrator
        return orchestrator, LLMFactory.build(target_spec, settings)


def force_offline(settings: AiwakeSettings) -> AiwakeSettings:
    """Return a copy of ``settings`` with both seats routed to the stub provider.

    Backs the CLI's ``--offline`` flag: exercises the full pipeline with no key
    and no network, without editing the YAML file.
    """
    offline_specs = {
        role: settings.spec_for(role).model_copy(update={"provider": "offline"})
        for role in ("orchestrator", "target")
    }
    return settings.model_copy(update={"models": settings.models.model_copy(update=offline_specs)})


__all__ = ["DebateRole", "LLMFactory", "available_providers", "force_offline", "register_provider"]

# -*- coding: utf-8 -*-
"""VFX hook chain — extensibility seam for post-processing.

Every hook is a pure function ``(frame, context) -> frame`` over an
``uint8`` RGB array of shape ``(H, W, 3)``. The renderer calls
:func:`apply_chain` once per frame and never knows what is registered, so new
effects drop in without touching ``renderer.py``.

Three hooks ship as deliberately inert stubs with their signatures, params and
implementation notes already in place — a CRT scanline pass, a Windows Media
Player-style spectrum visualizer, and a vignette. Enable them in
``aiwake_config.yaml`` under ``vfx.chain`` once implemented; while
``enabled: false`` they are never called.

The audio visualizer is the reason :class:`FrameContext` carries
``audio_samples``: a spectrum bar needs the waveform window for the current
frame, and threading it through the context keeps the hook signature uniform
with effects that ignore audio entirely.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Protocol, TypeVar, runtime_checkable

import numpy as np

try:
    from ..settings import VFXConfig, VFXHookConfig
except ImportError:  # pragma: no cover — standalone extraction
    from settings import VFXConfig, VFXHookConfig  # type: ignore[no-redef]

_LOG = logging.getLogger("aiwake.media.vfx")

Frame = np.ndarray


@dataclass(slots=True)
class FrameContext:
    """Everything a hook may need about the frame it is decorating.

    Attributes:
        t: Timestamp in seconds from the start of the render.
        frame_index: Zero-based frame counter.
        fps: Render frame rate.
        size: ``(width, height)`` in pixels.
        speaker: Role value of whoever is talking (``orchestrator``/``target``).
        progress: 0.0–1.0 position within the current utterance.
        audio_samples: Mono float32 window of the audio under this frame, or
            None when audio is unavailable. Populated lazily — only when at
            least one enabled hook declares ``needs_audio``.
        params: The hook's own ``params`` block from the YAML config.
    """

    t: float
    frame_index: int
    fps: int
    size: tuple[int, int]
    speaker: str = "orchestrator"
    progress: float = 0.0
    audio_samples: np.ndarray | None = None
    params: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class VFXHook(Protocol):
    """Structural type every registered hook satisfies."""

    def __call__(self, frame: Frame, context: FrameContext) -> Frame: ...


_REGISTRY: dict[str, VFXHook] = {}
_NEEDS_AUDIO: set[str] = set()

_H = TypeVar("_H", bound=Callable[..., Frame])


def register_vfx(name: str, *, needs_audio: bool = False) -> Callable[[_H], _H]:
    """Decorator publishing a frame post-processor under ``name``.

    Args:
        name: Key matched against ``vfx.chain[].name`` in the YAML config.
        needs_audio: Declare that the hook reads ``context.audio_samples``. The
            renderer only pays the cost of decoding audio windows when some
            enabled hook asks for them.

    Raises:
        ValueError: ``name`` is already registered.
    """

    def decorator(func: _H) -> _H:
        if name in _REGISTRY:
            raise ValueError(f"VFX hook {name!r} already registered")
        _REGISTRY[name] = func  # type: ignore[assignment]
        if needs_audio:
            _NEEDS_AUDIO.add(name)
        _LOG.debug("registered VFX hook %r (needs_audio=%s)", name, needs_audio)
        return func

    return decorator


def available_hooks() -> tuple[str, ...]:
    return tuple(sorted(_REGISTRY))


def chain_needs_audio(config: VFXConfig) -> bool:
    """True when any enabled hook wants waveform data."""
    return any(hook.name in _NEEDS_AUDIO for hook in config.enabled_hooks())


def resolve_chain(config: VFXConfig) -> tuple[tuple[VFXHook, dict[str, Any]], ...]:
    """Bind the enabled config entries to callables, in declaration order.

    Unknown names are warned about and skipped rather than raising: a typo in the
    VFX config should never cost you a finished render.
    """
    resolved: list[tuple[VFXHook, dict[str, Any]]] = []
    for entry in config.enabled_hooks():
        hook = _REGISTRY.get(entry.name)
        if hook is None:
            _LOG.warning("unknown VFX hook %r — skipping (available: %s)", entry.name, ", ".join(available_hooks()))
            continue
        resolved.append((hook, dict(entry.params)))
    return tuple(resolved)


def apply_chain(
    frame: Frame,
    context: FrameContext,
    chain: Iterable[tuple[VFXHook, dict[str, Any]]],
) -> Frame:
    """Run every hook in order, isolating failures.

    A hook that raises is logged once per frame and skipped; the un-decorated
    frame continues down the chain. Losing an effect is acceptable, losing a
    render is not.
    """
    for hook, params in chain:
        context.params = params
        try:
            result = hook(frame, context)
        except Exception as exc:  # noqa: BLE001 — an effect must not kill a render
            _LOG.warning("VFX hook %s failed at t=%.2fs: %s", getattr(hook, "__name__", hook), context.t, exc)
            continue
        if isinstance(result, np.ndarray) and result.shape == frame.shape:
            frame = result
    return frame


# --------------------------------------------------------------------------- #
# Shipped stubs — signatures and notes only. Implement in place, then flip
# `enabled: true` in aiwake_config.yaml.
# --------------------------------------------------------------------------- #
@register_vfx("crt_scanlines")
def crt_scanlines(frame: Frame, context: FrameContext) -> Frame:
    """Horizontal CRT scanline darkening plus optional phosphor bloom.

    Params:
        intensity (float): 0.0–1.0 darkening applied to affected rows.
        line_spacing (int): Darken every Nth row.

    Implementation note: build the row mask once at module scope keyed by
    ``(height, spacing)`` and multiply — per-frame mask construction at 1080p
    doubles render time for no visual gain.
    """
    return frame


@register_vfx("audio_visualizer", needs_audio=True)
def audio_visualizer(frame: Frame, context: FrameContext) -> Frame:
    """Windows Media Player-style spectrum bars anchored to an edge.

    Params:
        bars (int): Number of frequency buckets.
        height_px (int): Maximum bar height.
        anchor (str): ``bottom`` | ``top``.

    Implementation note: ``context.audio_samples`` holds the mono window for
    this frame. Take ``np.fft.rfft``, bucket the magnitudes logarithmically
    (linear buckets look dead — all the energy lands in the first two bars),
    smooth against the previous frame's heights with a ~0.6 decay factor, then
    draw with slice assignment rather than per-pixel loops.
    """
    return frame


@register_vfx("vignette")
def vignette(frame: Frame, context: FrameContext) -> Frame:
    """Radial edge darkening to pull the eye to centre-frame text.

    Params:
        strength (float): 0.0–1.0 darkening at the corners.

    Implementation note: cache the radial falloff mask by frame size; it is
    constant for the whole render.
    """
    return frame


__all__ = [
    "Frame",
    "FrameContext",
    "VFXHook",
    "apply_chain",
    "available_hooks",
    "chain_needs_audio",
    "register_vfx",
    "resolve_chain",
]

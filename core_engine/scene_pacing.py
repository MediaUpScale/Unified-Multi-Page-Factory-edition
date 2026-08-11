# -*- coding: utf-8 -*-
"""
Shared scene pacing engine for ECONOMIC_REEL and WAN_REEL.

Pure planning — no I/O. Callers pass ``video_length_s`` and a ``scene_duration``
spec; this module returns per-scene hold durations that sum to the target.

Precedence for inputs is owned by the caller (CLI > channels_config > factory
default). This module only consumes the resolved values.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any


# Factory defaults when neither CLI nor channel sets progressive knobs.
_DEFAULT_PROG_START_S = 4.0
_DEFAULT_PROG_STEP_EVERY = 3
_DEFAULT_PROG_STEP_S = 1.0
_DEFAULT_PROG_CAP_S = 7.5


@dataclass(frozen=True)
class SceneDurationSpec:
    """Parsed ``scene_duration`` flag / page_config value."""

    mode: str  # "fixed" | "progressive" | "equal"
    fixed_s: float = 0.0
    start_s: float = _DEFAULT_PROG_START_S
    step_every: int = _DEFAULT_PROG_STEP_EVERY
    step_s: float = _DEFAULT_PROG_STEP_S
    cap_s: float = _DEFAULT_PROG_CAP_S


def parse_scene_duration(
    scene_duration: str | None,
    *,
    progressive_start_s: float = _DEFAULT_PROG_START_S,
    progressive_step_every: int = _DEFAULT_PROG_STEP_EVERY,
    progressive_step_s: float = _DEFAULT_PROG_STEP_S,
    progressive_cap_s: float = _DEFAULT_PROG_CAP_S,
) -> SceneDurationSpec:
    """
    Parse a scene_duration string.

    Accepted forms
    --------------
    - ``fixed:4`` / ``fixed:4s`` — every scene holds N seconds
    - ``progressive`` — ramp start → cap using channel/factory progressive knobs
    - ``progressive:start=4,step_every=3,step=1,cap=7.5`` — inline overrides
    - ``equal`` / ``legacy`` / empty / None — equal split (caller supplies count)
    """
    raw = (scene_duration or "").strip()
    if not raw or raw.lower() in ("equal", "legacy", "default"):
        return SceneDurationSpec(mode="equal")

    low = raw.lower()
    if low.startswith("fixed:"):
        token = raw.split(":", 1)[1].strip().rstrip("sS")
        hold = max(0.5, float(token))
        return SceneDurationSpec(mode="fixed", fixed_s=hold)

    if low == "progressive" or low.startswith("progressive:"):
        start = float(progressive_start_s)
        step_every = max(1, int(progressive_step_every))
        step = float(progressive_step_s)
        cap = float(progressive_cap_s)
        if ":" in raw:
            body = raw.split(":", 1)[1]
            for part in body.split(","):
                part = part.strip()
                if not part or "=" not in part:
                    continue
                k, v = part.split("=", 1)
                k = k.strip().lower()
                v = v.strip().rstrip("sS")
                if k in ("start", "start_s"):
                    start = max(0.5, float(v))
                elif k in ("step_every", "every", "n"):
                    step_every = max(1, int(float(v)))
                elif k in ("step", "step_s", "delta"):
                    step = float(v)
                elif k in ("cap", "cap_s", "max"):
                    cap = max(0.5, float(v))
        cap = max(start, cap)
        return SceneDurationSpec(
            mode="progressive",
            start_s=start,
            step_every=step_every,
            step_s=step,
            cap_s=cap,
        )

    raise ValueError(
        f"Unknown scene_duration {scene_duration!r}. "
        "Use fixed:N, progressive, progressive:start=…, or equal."
    )


def _finalize_holds(holds: list[float], total: float) -> list[float]:
    """Ensure holds sum exactly to ``total``; fold a tiny leftover into the last scene."""
    if not holds:
        return [total]
    s = sum(holds)
    if s <= 0:
        return [total]
    if abs(s - total) < 1e-6:
        return [float(h) for h in holds]
    # Prefer adjusting the last scene rather than uniform rescale when close.
    head = [float(h) for h in holds[:-1]]
    last = total - sum(head)
    min_last = max(0.5, float(holds[-1]) * 0.45)
    if last < min_last and head:
        # Last too short — merge remainder into previous and drop last.
        head[-1] = total - sum(head[:-1])
        return head
    if last <= 0 and head:
        head[-1] = total - sum(head[:-1])
        return head
    return head + [last]


def _hold_at_index(i: int, spec: SceneDurationSpec) -> float:
    band = i // max(1, int(spec.step_every))
    return min(float(spec.cap_s), float(spec.start_s) + band * float(spec.step_s))


def plan_scenes(
    video_length_s: float,
    scene_duration: str | SceneDurationSpec | None,
    *,
    progressive_start_s: float = _DEFAULT_PROG_START_S,
    progressive_step_every: int = _DEFAULT_PROG_STEP_EVERY,
    progressive_step_s: float = _DEFAULT_PROG_STEP_S,
    progressive_cap_s: float = _DEFAULT_PROG_CAP_S,
    equal_count: int | None = None,
    min_scenes: int | None = None,
    max_scenes: int | None = None,
) -> list[float]:
    """
    Plan per-scene hold durations summing to ``video_length_s``.

    Parameters
    ----------
    video_length_s:
        Target reel length in seconds (not hardcoded inside this function).
    scene_duration:
        ``fixed:N``, ``progressive`` [+ knobs], or ``equal`` / None.
    equal_count:
        Required when mode is ``equal`` — number of equal-length scenes.
    min_scenes / max_scenes:
        Optional soft clamps. When the natural plan falls outside the range,
        holds are rebuilt (fixed/equal) or truncated/padded then re-finalized
        so the sum still matches ``video_length_s``.
    """
    total = max(1.0, float(video_length_s))
    if isinstance(scene_duration, SceneDurationSpec):
        spec = scene_duration
    else:
        spec = parse_scene_duration(
            scene_duration,
            progressive_start_s=progressive_start_s,
            progressive_step_every=progressive_step_every,
            progressive_step_s=progressive_step_s,
            progressive_cap_s=progressive_cap_s,
        )

    if spec.mode == "fixed":
        hold = max(0.5, float(spec.fixed_s))
        # Prefer full holds; fold a short remainder into the last scene rather
        # than spawning a tiny trailing cut (e.g. 30s @ fixed:4 → 6×4 + 6).
        n = max(1, int(math.floor(total / hold + 1e-9)))
        rem = total - n * hold
        if rem > hold * 0.5:
            n += 1
        if min_scenes is not None:
            n = max(n, max(1, int(min_scenes)))
        if max_scenes is not None:
            n = min(n, max(1, int(max_scenes)))
        if n == 1:
            return [total]
        body = [hold] * (n - 1)
        return _finalize_holds(body + [hold], total)

    if spec.mode == "equal":
        n = int(equal_count or 0)
        if n <= 0:
            # Factory fallback: ~4 s average (legacy dense feel) when count omitted.
            n = max(1, int(round(total / 4.0)))
        if min_scenes is not None:
            n = max(n, max(1, int(min_scenes)))
        if max_scenes is not None:
            n = min(n, max(1, int(max_scenes)))
        n = max(1, n)
        even = total / float(n)
        return _finalize_holds([even] * n, total)

    # progressive
    holds: list[float] = []
    i = 0
    safety = max(2, int(math.ceil(total / max(0.5, spec.start_s))) + 8)
    while i < safety and sum(holds) < total - 1e-9:
        h = _hold_at_index(i, spec)
        remaining = total - sum(holds)
        if remaining <= 1e-9:
            break
        # If this hold would overshoot, assign the clean remainder to the last scene.
        if remaining <= h + 1e-9:
            if remaining >= max(1.0, spec.start_s * 0.5) or not holds:
                holds.append(remaining)
            else:
                holds[-1] = holds[-1] + remaining
            break
        holds.append(h)
        i += 1
        if max_scenes is not None and len(holds) >= int(max_scenes):
            # Cap scene count: fold leftover into the last hold.
            leftover = total - sum(holds)
            if leftover > 1e-9:
                holds[-1] = holds[-1] + leftover
            break

    holds = _finalize_holds(holds, total)

    if min_scenes is not None and len(holds) < max(1, int(min_scenes)):
        # Pad with cap-length scenes then re-finalize (rare for progressive).
        target_n = max(1, int(min_scenes))
        while len(holds) < target_n:
            holds.append(float(spec.cap_s))
        holds = _finalize_holds(holds, total)

    return [max(0.05, float(d)) for d in holds]


def scale_scene_durations(holds: list[float], actual_duration_s: float) -> list[float]:
    """Scale a planned hold list so it sums to the real audio-driven timeline."""
    if not holds:
        return [max(0.05, float(actual_duration_s))]
    total = max(0.05, float(actual_duration_s))
    s = sum(holds)
    if s <= 1e-9:
        n = len(holds)
        return [total / n] * n
    scale = total / s
    return _finalize_holds([h * scale for h in holds], total)


def describe_scene_plan(holds: list[float]) -> str:
    """Short log-friendly summary of a hold list."""
    if not holds:
        return "[]"
    head = ", ".join(f"{d:.1f}" for d in holds[:8])
    suffix = "…" if len(holds) > 8 else ""
    return f"n={len(holds)} sum={sum(holds):.1f}s [{head}{suffix}]"


def scene_duration_from_page_cfg(page_cfg: dict[str, Any] | None) -> str:
    """Read SCENE_DURATION from a page_config dict; factory default ``equal``."""
    if not page_cfg:
        return "equal"
    raw = page_cfg.get("SCENE_DURATION", None)
    if raw is None or str(raw).strip() == "":
        return "equal"
    return str(raw).strip()

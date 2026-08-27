# -*- coding: utf-8 -*-
"""
Hard ship gates for ECONOMIC_REEL_LOFI.

A script that failed the validator, or an episode under visual QA HOLD, must
never reach MoviePy assemble. No judgment call, no force-env bypass —
raise ``ShipGateError``.
"""
from __future__ import annotations

from typing import Any


class ShipGateError(RuntimeError):
    """Raised when assemble is attempted without a cleared script / visual gate."""


def _story_fails(script: dict[str, Any] | None) -> list[str]:
    if not isinstance(script, dict):
        return ["script: missing or not an object"]
    story = script.get("story_quality")
    if isinstance(story, dict):
        return [str(x) for x in (story.get("fails") or []) if str(x).strip()]
    return []


def script_ship_blockers(
    script: dict[str, Any] | None,
    *,
    module: str = "relationship",
    scene_count: int | None = None,
    revalidate: bool = True,
) -> list[str]:
    """
    Return human-readable blockers if this script must not assemble.

    When ``revalidate`` is True (default), runs the full validator so locked
    packs and hold sidecars cannot skip the same gate the free writer uses.
    """
    if not isinstance(script, dict):
        return ["script: missing or not an object"]

    blockers: list[str] = []
    if script.get("script_ship_ok") is False:
        prior = script.get("script_ship_errors") or script.get("validation_errors")
        if isinstance(prior, list) and prior:
            blockers.extend(f"script_ship_ok=false: {x}" for x in prior)
        else:
            blockers.append("script_ship_ok=false")
        return blockers

    if revalidate:
        from core.economic_reel_lofi.validator_agent import validate_script

        lines = script.get("lines") if isinstance(script.get("lines"), list) else []
        n = int(scene_count or len(lines) or 9)
        result = validate_script(
            script,
            module=str(script.get("module") or module or "relationship"),
            scene_count=n,
            persist_on_pass=False,
        )
        script["script_ship_ok"] = bool(result.ok)
        if result.ok:
            script["script_ship_errors"] = []
        else:
            errs = list(result.reasons)
            script["script_ship_errors"] = errs
            blockers.extend(f"validator: {r}" for r in errs)
            return blockers

    blockers.extend(f"story_quality: {f}" for f in _story_fails(script))
    return blockers


def beat_integrity_blockers(
    *,
    script: dict[str, Any] | None,
    scene_images: list[Any] | None,
    captions: list[Any] | None = None,
    voice_paths: list[Any] | None = None,
    require_voiceover: bool = True,
    expected_beats: int | None = None,
) -> list[str]:
    """
    A N-beat script must assemble N stills (and N VO files when required).
    Missing / dropped beats are blockers — never a silent shorter reel.
    """
    from pathlib import Path

    blockers: list[str] = []
    lines = []
    if isinstance(script, dict) and isinstance(script.get("lines"), list):
        lines = [r for r in script["lines"] if isinstance(r, dict)]
    n_lines = len(lines)
    imgs = list(scene_images or [])
    caps = list(captions) if captions is not None else None
    n_img = len(imgs)
    expect = int(expected_beats) if expected_beats is not None else n_lines

    if expect <= 0 and n_lines <= 0:
        blockers.append("beat_integrity: no script lines and no expected beat count")
        return blockers

    if n_lines and expect and n_lines != expect:
        blockers.append(
            f"beat_integrity: script has {n_lines} lines, expected {expect}"
        )
    if n_img != expect:
        blockers.append(
            f"beat_integrity: {n_img} stills for expected {expect} beats — "
            "missing beat blocks assemble (no silent shorter video)"
        )
    if caps is not None and len(caps) != expect:
        blockers.append(
            f"beat_integrity: {len(caps)} captions for expected {expect} beats"
        )
    if caps is not None and n_img and len(caps) != n_img:
        blockers.append(
            f"beat_integrity: still/caption mismatch {n_img} vs {len(caps)}"
        )

    for i, raw in enumerate(imgs):
        p = Path(str(raw)) if raw is not None else None
        if p is None or not p.is_file():
            blockers.append(f"beat_integrity: missing still for scene {i + 1}")

    if require_voiceover and voice_paths is not None:
        if len(voice_paths) != expect:
            blockers.append(
                f"beat_integrity: {len(voice_paths)} VO paths for expected "
                f"{expect} beats"
            )
        for i, raw in enumerate(voice_paths):
            p = Path(str(raw)) if raw else None
            if p is None or not p.is_file():
                blockers.append(f"beat_integrity: missing VO for scene {i + 1}")

    # Any per-beat QA failure stamped on the script must block (not drop).
    for i, row in enumerate(lines):
        gate = row.get("default_object_gate") if isinstance(row, dict) else None
        if isinstance(gate, dict) and gate.get("qa_passed") is False:
            scene_i = int(row.get("scene") or i + 1)
            blockers.append(
                f"beat_integrity: scene {scene_i} qa_passed=false — "
                "episode must HOLD, not assemble without that beat"
            )
    return blockers


def episode_ship_blockers(
    episode: dict[str, Any] | None,
    *,
    revalidate_script: bool = True,
) -> list[str]:
    """Blockers for hold / regen sidecars before ``assemble_video_from_episode``."""
    if not isinstance(episode, dict):
        return ["episode: missing or not an object"]

    blockers: list[str] = []
    mode = str(episode.get("mode") or "").strip().lower()
    if mode in {
        "qa_hold",
        "qa_hold_user_approved_assemble",
        "discarded",
        "script_rejected",
    }:
        blockers.append(f"mode={mode!r} is not shippable")

    flags = list(episode.get("visual_qa_flags") or [])
    if flags:
        blockers.append(f"visual_qa_hold: {len(flags)} flag(s) — NOT assembling")

    if episode.get("manual_review") and flags:
        blockers.append("manual_review=true with visual_qa_flags")

    script = episode.get("script") if isinstance(episode.get("script"), dict) else None
    if script is None and (
        episode.get("lines") or episode.get("monologue")
    ):
        script = episode
    blockers.extend(
        script_ship_blockers(
            script,
            module=str(episode.get("module") or "relationship"),
            scene_count=episode.get("scene_count"),
            revalidate=revalidate_script,
        )
    )
    from core.economic_reel_lofi import config as lofi_cfg

    expect = int(
        episode.get("scene_count")
        or (len(script.get("lines") or []) if isinstance(script, dict) else 0)
        or lofi_cfg.THEMATIC_DEFAULT_SCENES
    )
    blockers.extend(
        beat_integrity_blockers(
            script=script if isinstance(script, dict) else None,
            scene_images=list(episode.get("scene_images") or []),
            captions=None,
            voice_paths=list(episode.get("voice_paths") or [])
            if episode.get("voice_paths") is not None
            else None,
            require_voiceover=bool(getattr(lofi_cfg, "REQUIRE_VOICEOVER", True)),
            expected_beats=expect,
        )
    )
    return blockers


def assert_script_cleared_for_assemble(
    script: dict[str, Any] | None,
    *,
    module: str = "relationship",
    scene_count: int | None = None,
) -> None:
    blockers = script_ship_blockers(
        script, module=module, scene_count=scene_count, revalidate=True
    )
    if blockers:
        msg = "; ".join(blockers)
        print(f"[LOFI SHIP-GATE] BLOCK assemble — script not cleared: {msg}")
        raise ShipGateError(f"script not cleared for assemble: {msg}")


def assert_episode_cleared_for_assemble(episode: dict[str, Any] | None) -> None:
    blockers = episode_ship_blockers(episode, revalidate_script=True)
    if blockers:
        msg = "; ".join(blockers)
        print(f"[LOFI SHIP-GATE] BLOCK assemble — episode not cleared: {msg}")
        raise ShipGateError(f"episode not cleared for assemble: {msg}")

# -*- coding: utf-8 -*-
"""
Production WAN_REEL — Flux stills from ECONOMIC_REEL worker, Wan2.2 per act.

``run_wan_reel_test()`` in ``wan_reel_engine.py`` is the smoke path and is not
used here. This module registers the production post-type and generates Wan
clips from stills + bucket holds already produced by ``_produce_variant_worker``.
"""
from __future__ import annotations

import functools
import logging
import re
import time
from pathlib import Path
from typing import Any

from core_engine.post_type_registry import register_post_type

logger = logging.getLogger(__name__)

WAN_REEL_PAGE_ID = "ancient_knowledge"

# Wan img2vid canvas — matches smoke-test VRAM-safe size; MoviePy upscales to 1080x1920.
_WAN_GEN_WIDTH = 512
_WAN_GEN_HEIGHT = 896

# Moderate cinematic cameras only. The last full reel's ~21–23s act used a
# stacked dolly + implied-action prompt and jittered; keep motion readable.
_MOTION_CAMERA_CYCLE = (
    "slow dolly-in toward the subject, foreground dust and stone sliding past "
    "the lens for true parallax depth",
    "gentle lateral tracking pan, haze drifting between foreground ruins and "
    "the distant horizon",
    "gentle crane tilt upward revealing scale, atmospheric particles rising "
    "through volumetric light",
    "slow observational arc around the artefact, torchlight and shadow "
    "shifting across carved surfaces",
    "measured push-in, wind moving fabric and dust while the background "
    "recedes in parallax",
)
# 20-step Wan CFG (node 129:126). 3.5 over-amplified motion on some acts.
_WAN_CFG_CAP = 2.5
# Custom RunPod worker kills every workflow at 600s. Isolated 7s jobs
# (20-step and 4-step LoRA, 24GB and 5090) all hit that cap. Last successful
# production clips were 3.0–5.5s. Cap GPU duration; MoviePy still holds ~7s
# by looping the clip.
_WAN_GPU_DURATION_CAP_S = 5.5


def assert_wan_reel_page(page_id: str | None) -> None:
    pid = str(page_id or "").strip().lower()
    if pid != WAN_REEL_PAGE_ID:
        raise SystemExit(
            "WAN_REEL is isolated to --page ancient_knowledge "
            f"(got {page_id!r})."
        )


def build_motion_prompt(
    snippet: str,
    *,
    scene_index: int = 0,
    total_scenes: int = 1,
    topic: str = "",
    duration_s: float | None = None,
) -> str:
    """
    Motion-optimized Wan img2vid prompt for one spoken beat.

    Directs camera (dolly / pan / tilt / orbit / parallax) and in-scene motion
    tied to the narration — never a locked-off still.
    """
    beat = " ".join((snippet or "").strip().split())
    if not beat:
        beat = (topic or "ancient mystery").strip()
    cam = _MOTION_CAMERA_CYCLE[int(scene_index) % len(_MOTION_CAMERA_CYCLE)]
    stop = {
        "the", "and", "that", "this", "with", "from", "into", "were", "was",
        "are", "for", "they", "their", "have", "has", "been", "what", "when",
        "where", "which", "than", "then", "also", "just", "over", "under",
    }
    tokens = [
        t for t in re.findall(r"[A-Za-z][A-Za-z\-]{2,}", beat)
        if t.lower() not in stop
    ]
    cue = " ".join(tokens[:8]) if tokens else beat[:100]
    hold = ""
    if duration_s is not None and float(duration_s) > 0:
        hold = f" Clip length about {float(duration_s):.1f} seconds of continuous motion."
    n = max(1, int(total_scenes) or 1)
    return (
        "Cinematic documentary live-action motion, mysterious ancient-world tone. "
        "This is IMAGE-TO-VIDEO: the still must come alive, not remain a freeze-frame. "
        f"CAMERA: {cam}. "
        "Foreground elements (dust motes, stone edge, hanging vines, observers) "
        "must separate from the background via parallax as the camera moves. "
        f"Keep the subject of this line on screen: \"{beat}\". "
        f"Subtle environmental motion tied to: {cue}. "
        "Cinematic and stable: moderate speed only, no whip pans, no morphing, "
        "no warping, no jitter, no jump cuts, no text overlays, no captions, "
        f"no watermarks.{hold} Scene {int(scene_index) + 1}/{n}."
    )


def generate_wan_act_videos(
    stills: list[Path],
    snippets: list[str],
    durations: list[float],
    *,
    topic: str = "",
    output_dir: Path | str,
    stem: str = "wan_act",
    cost_tracker: Any | None = None,
    width: int = _WAN_GEN_WIDTH,
    height: int = _WAN_GEN_HEIGHT,
) -> tuple[list[Path | None], list[str]]:
    """
    Fan-out Wan img2vid jobs via ``run_parallel_jobs`` (REMOTE_GPU_MAX_PARALLEL).

    Each act retries once on failure, then returns ``None`` so the compiler can
    Ken-Burns that still. Never raises for a single-act failure.

    Returns
    -------
    (paths, statuses)
        ``paths[i]`` is the mp4 or ``None``.
        ``statuses[i]`` is ``\"wan\"`` or ``\"ken_burns: <reason>\"``.
    """
    from core_engine.remote_gpu_manager import (
        get_manager,
        remote_gpu_max_parallel,
        run_parallel_jobs,
    )

    n = len(stills)
    if n == 0:
        return [], []
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    snippets = list(snippets or [])
    durations = list(durations or [])
    while len(snippets) < n:
        snippets.append(topic or "")
    while len(durations) < n:
        durations.append(7.0)

    mgr = get_manager()
    mode = str(getattr(mgr.client, "mode", "runpod") or "runpod")
    gpu_before = float(getattr(mgr.client, "total_gpu_seconds", 0) or 0)

    def _job(idx: int) -> Path:
        still = Path(stills[idx])
        snippet = snippets[idx] if idx < len(snippets) else topic
        hold = max(0.75, float(durations[idx] if idx < len(durations) else 7.0))
        gpu_hold = min(hold, _WAN_GPU_DURATION_CAP_S)
        prompt = build_motion_prompt(
            snippet,
            scene_index=idx,
            total_scenes=n,
            topic=topic,
            duration_s=gpu_hold,
        )
        dest = out_dir / f"{stem}_wan_{idx + 1:02d}.mp4"
        logger.info(
            "WAN act %d/%d | timeline_hold=%.2fs gpu_hold=%.2fs | still=%s | prompt=%.120s",
            idx + 1, n, hold, gpu_hold, still.name, prompt,
        )
        last_exc: BaseException | None = None
        for attempt in (1, 2):
            try:
                path = mgr.generate_video(
                    still,
                    prompt=prompt,
                    output_path=dest,
                    duration_s=gpu_hold,
                    width=width,
                    height=height,
                    stem=f"{stem}_wan_{idx + 1:02d}",
                    extra_patches={"129:126": {"value": _WAN_CFG_CAP}},
                )
                resolved = Path(path)
                if resolved.is_file() and resolved.stat().st_size > 64:
                    if attempt > 1:
                        logger.info("WAN act %d succeeded on retry", idx + 1)
                    return resolved
                last_exc = RuntimeError(f"empty Wan output: {resolved}")
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                logger.warning(
                    "WAN act %d attempt %d failed: %s",
                    idx + 1, attempt, exc,
                )
        raise RuntimeError(
            f"Wan act {idx + 1} failed after retry: {last_exc}"
        )

    jobs = {i: functools.partial(_job, i) for i in range(n)}
    cap = remote_gpu_max_parallel()
    t0 = time.monotonic()
    logger.info(
        "WAN parallel submit | n_jobs=%d max_workers=%d (REMOTE_GPU_MAX_PARALLEL)",
        n, cap,
    )
    raw = run_parallel_jobs(jobs, max_workers=cap)
    elapsed = time.monotonic() - t0

    paths: list[Path | None] = []
    statuses: list[str] = []
    ok = 0
    for i in range(n):
        result = raw.get(i)
        if isinstance(result, Exception) or result is None:
            reason = str(result) if result is not None else "no result"
            logger.warning(
                "WAN act %d/%d → Ken Burns fallback | %s",
                i + 1, n, reason,
            )
            paths.append(None)
            statuses.append(f"ken_burns: {reason[:180]}")
            continue
        p = Path(result)
        if p.is_file():
            paths.append(p)
            statuses.append("wan")
            ok += 1
        else:
            paths.append(None)
            statuses.append("ken_burns: missing output file")

    gpu_delta = float(getattr(mgr.client, "total_gpu_seconds", 0) or 0) - gpu_before
    if cost_tracker is not None and gpu_delta > 0:
        try:
            cost_tracker.track_gpu_seconds(gpu_delta, mode=mode, jobs=ok)
        except Exception:  # noqa: BLE001
            pass

    logger.info(
        "WAN video batch done | ok=%d/%d fallback=%d | wall=%.1fs | gpu_s=%.1f",
        ok, n, n - ok, elapsed, gpu_delta,
    )
    return paths, statuses


def run_wan_reel(
    *,
    page_id: str = WAN_REEL_PAGE_ID,
    quantity: int = 1,
    topic: str | None = None,
    skip_image: bool = False,
    skip_caption: bool = False,
    test_mode: bool = False,
    economic_brain_mode: bool | None = None,
    bootstrap_models: Any = None,
    page_ctx: Any = None,
    cta_enabled: bool = True,
    image_style: str = "NATURAL",
    render_approval_required: bool = False,
    agentic_pipeline: bool | None = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Registry runner — production path reuses ``produce()`` / ``_produce_variant_worker``."""
    assert_wan_reel_page(page_id)
    from main import produce  # lazy: avoid import cycle at module load

    return produce(
        topic,
        quantity=quantity,
        skip_image=skip_image,
        skip_caption=skip_caption,
        test_mode=test_mode,
        economic_brain_mode=economic_brain_mode,
        bootstrap_models=bootstrap_models,
        page_ctx=page_ctx,
        cta_enabled=cta_enabled,
        post_type="WAN_REEL",
        image_style=image_style,
        render_approval_required=render_approval_required,
        agentic_pipeline=agentic_pipeline,
    )


register_post_type("WAN_REEL", run_wan_reel)

__all__ = [
    "WAN_REEL_PAGE_ID",
    "assert_wan_reel_page",
    "build_motion_prompt",
    "generate_wan_act_videos",
    "run_wan_reel",
]

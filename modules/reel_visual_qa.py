# -*- coding: utf-8 -*-
"""
Gemini Vision QA for ECONOMIC_REEL stills.

Same bar as static ancient_knowledge posts: ancient_mystery profile,
threshold 5.5, max_retries=1 (r01 + r02), then best-score commit so
video compile is never blocked.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable

_LOG = logging.getLogger(__name__)

QUALITY_THRESHOLD: float = 5.5
MAX_RETRIES: int = 1  # one fallback → r01 + r02 only
_CRITIC_WORKERS: int = 3

GenerateFn = Callable[..., Path | str | None]


def _threshold() -> float:
    try:
        from quality.VisualQA_Agent.visual_critic import ancient_mystery_quality_threshold

        return float(ancient_mystery_quality_threshold(QUALITY_THRESHOLD))
    except Exception:
        try:
            from agents.orchestrator.criteria import QUALITY_THRESHOLD as _AK_T

            return float(_AK_T)
        except Exception:
            return QUALITY_THRESHOLD


def _shift_prompt(prompt: str, attempt: int) -> str:
    try:
        from agents.orchestrator.criteria import CAMERA_REWRITE_CYCLE

        cam = CAMERA_REWRITE_CYCLE[int(attempt) % len(CAMERA_REWRITE_CYCLE)]
    except Exception:
        cam = (
            "CAMERA REWRITE: wide aerial, open sky, subject centred, "
            "no interior frames."
        )
    return f"{prompt} {cam}".strip()


def critique_still(
    image_path: Path | str,
    *,
    channel: str = "ancient_knowledge",
) -> tuple[float, bool, str]:
    """Return ``(score, passed, feedback)``. Never raises."""
    path = Path(image_path)
    threshold = _threshold()
    if not path.is_file():
        return 0.0, False, "missing_image"
    try:
        from quality.VisualQA_Agent.channel_rag import (
            get_channel_rules,
            seed_default_channels,
            set_channel_context,
        )
        from quality.VisualQA_Agent.visual_critic import evaluate_image

        seed_default_channels(force=False)
        set_channel_context(channel)
        rules = dict(get_channel_rules(channel) or {})
        rules["critic_profile"] = rules.get("critic_profile") or "ancient_mystery"
        verdict = evaluate_image(
            path,
            channel_name=channel,
            rules=rules,
            quality_threshold=threshold,
        )
        score = float(getattr(verdict, "score", 0.0) or 0.0)
        passed = bool(getattr(verdict, "passed", False)) and score >= threshold
        feedback = str(getattr(verdict, "fix_instructions", "") or "").strip()
        if not passed and not feedback:
            feedback = "; ".join(getattr(verdict, "flaws", None) or []) or "below threshold"
        return score, passed, feedback
    except Exception as exc:  # noqa: BLE001
        _LOG.warning("reel VisualQA failed on %s (%s) — keeping still.", path.name, exc)
        return 0.0, False, f"critic_exception:{type(exc).__name__}"


def apply_act_vision_qa(
    *,
    acts: list[dict[str, Any]],
    generate_fn: GenerateFn,
    channel: str = "ancient_knowledge",
    output_directory: Path | str | None = None,
    cost_tracker: Any | None = None,
    max_workers: int = _CRITIC_WORKERS,
) -> tuple[list[Path], int]:
    """
    Critique each r01 still; regenerate failures once (r02); commit best score.

    ``acts`` items: ``path``, ``prompt``, ``stem`` (output stem without _r01/_r02).
    Returns ``(committed_paths, extra_image_generations)``.
    """
    threshold = _threshold()
    workers = max(1, int(max_workers))
    n = len(acts)
    if n == 0:
        return [], 0

    scores: list[float] = [0.0] * n
    feedbacks: list[str] = [""] * n
    paths: list[Path] = []
    for item in acts:
        raw = item.get("path")
        paths.append(Path(raw) if raw else Path())

    def _critique_one(idx: int) -> tuple[int, float, bool, str]:
        score, passed, feedback = critique_still(paths[idx], channel=channel)
        if cost_tracker is not None:
            try:
                cost_tracker.track_text("text_gemini_flash", char_count=600)
            except Exception:
                pass
        return idx, score, passed, feedback

    _LOG.info(
        "REEL VisualQA | n=%d | threshold=%.2f | max_retries=%d | profile=ancient_mystery",
        n, threshold, MAX_RETRIES,
    )
    with ThreadPoolExecutor(max_workers=min(workers, n)) as pool:
        futs = [pool.submit(_critique_one, i) for i in range(n)]
        for fut in as_completed(futs):
            idx, score, passed, feedback = fut.result()
            scores[idx] = score
            feedbacks[idx] = feedback
            _LOG.warning(
                "REEL QA r01 | act=%d/%d score=%.2f passed=%s threshold=%.2f",
                idx + 1, n, score, passed, threshold,
            )

    retry_idx = [i for i in range(n) if scores[i] < threshold]
    extra_imgs = 0
    if not retry_idx or MAX_RETRIES < 1:
        if retry_idx:
            _LOG.warning(
                "REEL QA BEST-SCORE FALLBACK | %d act(s) below %.2f — committing r01, no r03",
                len(retry_idx), threshold,
            )
        return paths, extra_imgs

    out_dir = Path(output_directory) if output_directory else None

    def _regen_one(idx: int) -> tuple[int, Path | None]:
        prompt = _shift_prompt(str(acts[idx].get("prompt") or ""), attempt=1)
        stem = str(acts[idx].get("stem") or f"act{idx + 1:02d}")
        if not stem.endswith("_r02"):
            stem = f"{stem}_r02"
        try:
            kwargs: dict[str, Any] = {
                "output_stem": stem,
                "avatar_mode": "OFF",
            }
            if out_dir is not None:
                kwargs["output_directory"] = out_dir
            result = generate_fn(prompt, **kwargs)
            return idx, Path(result) if result else None
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("REEL QA r02 generate failed act=%d (%s)", idx + 1, exc)
            return idx, None

    r02_paths: dict[int, Path] = {}
    with ThreadPoolExecutor(max_workers=min(workers, len(retry_idx))) as pool:
        futs = [pool.submit(_regen_one, i) for i in retry_idx]
        for fut in as_completed(futs):
            idx, new_path = fut.result()
            if new_path is not None and new_path.is_file():
                r02_paths[idx] = new_path
                extra_imgs += 1

    def _critique_r02(idx: int) -> tuple[int, float, bool, str]:
        score, passed, feedback = critique_still(r02_paths[idx], channel=channel)
        if cost_tracker is not None:
            try:
                cost_tracker.track_text("text_gemini_flash", char_count=600)
            except Exception:
                pass
        return idx, score, passed, feedback

    if r02_paths:
        with ThreadPoolExecutor(max_workers=min(workers, len(r02_paths))) as pool:
            futs = [pool.submit(_critique_r02, i) for i in r02_paths]
            for fut in as_completed(futs):
                idx, score, passed, feedback = fut.result()
                _LOG.warning(
                    "REEL QA r02 | act=%d/%d score=%.2f (r01=%.2f) passed=%s",
                    idx + 1, n, score, scores[idx], passed,
                )
                if score >= scores[idx] and r02_paths[idx].is_file():
                    paths[idx] = r02_paths[idx]
                    scores[idx] = score
                    feedbacks[idx] = feedback

    below = sum(1 for s in scores if s < threshold)
    if below:
        _LOG.warning(
            "REEL QA BEST-SCORE FALLBACK | %d/%d act(s) still < %.2f — "
            "committing highest score, no r03",
            below, n, threshold,
        )
    return paths, extra_imgs

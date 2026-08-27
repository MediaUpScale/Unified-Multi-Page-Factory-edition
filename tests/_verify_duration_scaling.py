# -*- coding: utf-8 -*-
"""Static, LLM-free trace of what the AK sequence-reel pipeline WOULD do
at a non-default target duration passed via ``--video-length``.

Answers three questions from the user's Round-6 follow-up:

    1. Does _seq_n (act count) scale with target_duration_s, or is it
       capped regardless of requested duration?
    2. Does words_for_duration()/the word-floor gate scale to non-default
       durations, or does it hardcode 80 s?
    3. Where does target_duration_s enter the script prompt vs the
       acceptance gate — are they consistent?

Only imports; no live Gemini / TTS calls.
"""
from __future__ import annotations

from pathlib import Path as _ReorgPath
import sys as _reorg_sys
_REORG_ROOT = _ReorgPath(__file__).resolve().parents[1]
if str(_REORG_ROOT) not in _reorg_sys.path:
    _reorg_sys.path.insert(0, str(_REORG_ROOT))

import importlib
import os
import sys
from pathlib import Path

os.environ.setdefault("ACTIVE_PAGE", "ancient_knowledge")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import words_for_duration  # noqa: E402
from core.reel_sequence_engine import (  # noqa: E402
    compute_dense_act_count,
    compute_hook_body_act_count,
    compute_two_tier_act_count,
)


def _resolve_page_ctx(video_length_override: float | None) -> object:
    """Load AK page_ctx with an optional CLI-style --video-length override."""
    import channel_loader  # noqa: PLC0415
    importlib.reload(channel_loader)
    ctx = channel_loader.load_page_context(
        page_id="ancient_knowledge",
        avatar_mode="OFF",
        post_format="IMAGE_BACKGROUND",
    )
    if video_length_override is not None:
        ctx.page_cfg["VIDEO_LENGTH_OVERRIDE"] = float(video_length_override)
        ctx.page_cfg["REEL_DURATION"] = float(video_length_override)
    return ctx


def _caption_engine_min_words(duration_s: float) -> int:
    """Post-fix (Final Round 2026-08-15): duration-proportional word floor.

    Mirrors caption_engine.py::generate_sequence_voiceover — was
    hardcoded ``words_for_duration(80.0)``, now scales with duration_s.
    """
    return int(words_for_duration(float(duration_s)))


def _caption_engine_max_words(duration_s: float) -> int:
    """Post-fix: max window scales with 25 % of duration_s in words."""
    return int(words_for_duration(float(duration_s)) + max(20, float(duration_s) * 0.25))


def trace(video_length_s: float | None) -> None:
    label = "DEFAULT (no --video-length)" if video_length_s is None else (
        f"--video-length {video_length_s:g}"
    )
    ctx = _resolve_page_ctx(video_length_s)
    reel_dur = ctx.reel_duration
    n_from_dense = compute_dense_act_count(
        reel_dur,
        seconds_per_act=ctx.reel_seconds_per_act,
        min_acts=ctx.reel_image_min_count,
        max_acts=ctx.reel_image_count,
    )
    n_from_hook = compute_hook_body_act_count(
        reel_dur,
        hook_hold_s=ctx.reel_hook_hold_s,
        body_hold_s=ctx.reel_body_hold_s,
        min_acts=ctx.reel_image_min_count,
        max_acts=ctx.reel_image_count,
    )
    tier1_n, tier2_n, two_tier_total = compute_two_tier_act_count(
        reel_dur,
        tier1_max_acts=ctx.reel_tier1_max_acts,
        tier1_horizon_s=ctx.reel_tier1_horizon_s,
        tier1_seconds_per_act=ctx.reel_tier1_seconds_per_act,
        tier2_seconds_per_act=ctx.reel_tier2_seconds_per_act,
        min_acts=ctx.reel_image_min_count,
    )
    words_target_from_ctx = ctx.reel_narration_words
    words_target_from_duration = words_for_duration(reel_dur)
    min_words_ctx = ctx.reel_narration_min_words
    max_words_ctx = ctx.reel_narration_max_words
    min_words_gate = _caption_engine_min_words(reel_dur)
    max_words_gate = _caption_engine_max_words(reel_dur)
    est_audio_at_gate_min = min_words_gate / 2.25
    est_audio_at_target = words_target_from_duration / 2.25

    print("\n" + "=" * 88)
    print(f"  {label}")
    print("=" * 88)
    print(f"  page_ctx.reel_duration                              : {reel_dur:.1f} s")
    print(f"  page_ctx.reel_image_min_count / _count (cap)         : "
          f"{ctx.reel_image_min_count} / {ctx.reel_image_count}")
    print(f"  page_ctx.reel_seconds_per_act                        : "
          f"{ctx.reel_seconds_per_act:.2f} s")
    print(f"  compute_dense_act_count(reel_duration, ...)          : "
          f"{n_from_dense} acts  [legacy dense, capped]")
    print(f"  compute_hook_body_act_count(reel_duration, ...)      : "
          f"{n_from_hook} acts  [legacy hook/body, capped]")
    print(f"  compute_two_tier_act_count(reel_duration, ...)       : "
          f"{two_tier_total} acts  (tier1={tier1_n} + tier2={tier2_n})  [NEW - uncapped]")
    print(f"  page_ctx.use_two_tier_pacing                         : "
          f"{ctx.use_two_tier_pacing}")
    print(f"  page_ctx.reel_narration_words   (fed to Gemini)      : "
          f"{words_target_from_ctx} words")
    print(f"  words_for_duration(reel_duration) [what SHOULD be]   : "
          f"{words_target_from_duration} words")
    print(f"  page_ctx.reel_narration_min_words / _max_words       : "
          f"{min_words_ctx} / {max_words_ctx}")
    print(f"  caption_engine.py _min_words gate (AK / non-warrior) : "
          f"{min_words_gate} words  [duration-proportional, post-fix]")
    print(f"  caption_engine.py _max_words gate                    : "
          f"{max_words_gate} words  [duration + 25%, post-fix]")
    print("")
    print(f"  ->  Estimated audio at gate _min_words @ 2.25 wps    : "
          f"{est_audio_at_gate_min:.1f} s")
    print(f"  ->  Estimated audio at CORRECT target_words @ 2.25   : "
          f"{est_audio_at_target:.1f} s")
    print(f"  ->  Requested video duration                         : "
          f"{reel_dur:.1f} s")
    delta = reel_dur - est_audio_at_gate_min
    print(f"  ->  delta (requested - audio-that-passes-gate)      : "
          f"{delta:+.1f} s   "
          f"{'!! SILENT UNDER-DELIVERY' if delta > 20 else 'OK'}")


if __name__ == "__main__":
    for dur in (None, 60.0, 90.0, 180.0, 300.0):
        trace(dur)

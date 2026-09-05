# -*- coding: utf-8 -*-
"""
WAN_REEL assembly — Flux stills → Wan2.2 clips → concat → F5 narration.

Scene hold duration is always a parameter (``scene_duration_s``), never the
ECONOMIC_REEL progressive image-pacing curve. Wan receives that value via
``prepare_video_workflow(duration_s=…)`` (PrimitiveFloat Duration node).
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from utils.pipeline_paths import moviepy_temp_audio_dir

logger = logging.getLogger(__name__)

_MOTION_CAMERA_CYCLE = (
    "drone-style sweeping lateral pass over the landscape, slow parallax depth",
    "dramatic cinematic push-in on the key detail as the beat lands",
    "slow mysterious reveal — camera drifts from shadow into the subject",
    "wide establishing crane rise, atmospheric haze moving through frame",
    "tight observational drift across carved stone / artefact surface detail",
)


@dataclass
class WanClipCost:
    scene_index: int
    kind: str  # image | video | audio
    path: str
    gpu_seconds: float
    duration_s: float = 0.0
    prompt_preview: str = ""


@dataclass
class WanReelReport:
    topic: str
    n_scenes: int
    scene_duration_s: float
    video_length_s: float
    mode: str
    holds: list[float]
    narration_words: int
    voice_duration_s: float
    clips: list[WanClipCost] = field(default_factory=list)
    total_gpu_seconds: float = 0.0
    total_gpu_usd: float = 0.0
    together_wan27_usd_at_0_10_per_s: float = 0.0
    output_mp4: str = ""
    cost_json: str = ""
    started_at: str = ""
    finished_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["clips"] = [asdict(c) for c in self.clips]
        return d


def build_motion_prompt(
    spoken_snippet: str,
    *,
    scene_index: int,
    total_scenes: int,
    topic: str = "",
) -> str:
    """
    Narration-grounded Wan motion prompt (not a generic still re-description).

    Same spoken beat that drives the Flux image for this scene must drive motion.
    """
    beat = " ".join((spoken_snippet or "").strip().split())
    if not beat:
        beat = (topic or "ancient mystery").strip()
    cam = _MOTION_CAMERA_CYCLE[scene_index % len(_MOTION_CAMERA_CYCLE)]
    # Pull a short noun-ish cue for action emphasis
    tokens = [t for t in re.findall(r"[A-Za-z][A-Za-z\\-]{2,}", beat) if t.lower() not in {
        "the", "and", "that", "this", "with", "from", "into", "were", "was", "are",
        "for", "they", "their", "have", "has", "been", "what", "when", "where",
    }]
    cue = " ".join(tokens[:6]) if tokens else beat[:80]
    return (
        f"Cinematic documentary motion, mysterious atmospheric tone. "
        f"Animate what the narration is saying now: \"{beat}\". "
        f"Visible motion tied to: {cue}. "
        f"Camera: {cam}. "
        f"If the line implies action (lightning, collapse, discovery, ritual light), "
        f"show that action happening in-frame — not a frozen still. "
        f"Smooth temporal coherence, no jump cuts, no text overlays, no watermarks. "
        f"Scene {scene_index + 1}/{total_scenes}."
    )


def build_still_prompt(
    spoken_snippet: str,
    *,
    scene_index: int,
    total_scenes: int,
    topic: str = "",
    style_anchor: str = "",
) -> str:
    """Still prompt from the same narration segment (keeps image/motion/audio aligned)."""
    beat = " ".join((spoken_snippet or "").strip().split())
    style = (style_anchor or (
        "Cinematic ancient-mysteries documentary still, volumetric god rays, "
        "dust particles in foreground, deep atmospheric haze, photoreal, 8k"
    )).strip()
    return (
        f"{style}. "
        f"SPOKEN BEAT (literal visualisation): \"{beat or topic}\". "
        f"Wide aerial or environmental framing, centrally composed for 9:16. "
        f"Scene {scene_index + 1}/{total_scenes}. No text, no captions, no watermark."
    )


def _track_job(
    tracker: Any,
    *,
    seconds: float,
    mode: str,
    clips: list[WanClipCost],
    scene_index: int,
    kind: str,
    path: Path,
    duration_s: float = 0.0,
    prompt: str = "",
) -> None:
    secs = max(0.0, float(seconds or 0.0))
    if tracker is not None and secs > 0:
        tracker.track_gpu_seconds(secs, mode=mode, jobs=1)
    clips.append(
        WanClipCost(
            scene_index=scene_index,
            kind=kind,
            path=str(path),
            gpu_seconds=secs,
            duration_s=float(duration_s or 0.0),
            prompt_preview=(prompt or "")[:180],
        )
    )


def concatenate_wan_clips(
    clip_paths: list[Path],
    voice_audio: Path | None,
    output_path: Path,
    *,
    target_duration_s: float | None = None,
) -> Path:
    """Hard-cut Wan clips, then lay continuous narration over the assembly."""
    from moviepy import (  # type: ignore
        AudioFileClip,
        VideoFileClip,
        concatenate_videoclips,
    )

    if not clip_paths:
        raise ValueError("No Wan clips to concatenate")

    videos = [VideoFileClip(str(p)) for p in clip_paths]
    assembled = concatenate_videoclips(videos, method="compose")
    if voice_audio is not None and Path(voice_audio).is_file():
        aud = AudioFileClip(str(voice_audio))
        # Audio-driven when VO is shorter/longer: trim or hold last frame via duration
        dur = float(aud.duration)
        if target_duration_s is not None:
            dur = min(dur, float(target_duration_s) + 0.5)
        if assembled.duration < dur - 0.05:
            final = assembled.with_audio(aud).with_duration(dur)
        else:
            final = assembled.subclipped(0, min(assembled.duration, dur)).with_audio(aud)
    else:
        final = assembled
        dur = float(final.duration)

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    final.write_videofile(
        str(output_path),
        codec="libx264",
        audio_codec="aac",
        fps=getattr(videos[0], "fps", None) or 16,
        logger=None,
        threads=4,
        temp_audiofile_path=moviepy_temp_audio_dir(),
    )
    for c in videos + [assembled, final]:
        try:
            c.close()
        except Exception:
            pass
    if voice_audio is not None:
        try:
            aud.close()
        except Exception:
            pass
    return output_path


def run_wan_reel_test(
    topic: str,
    *,
    page_id: str = "ancient_knowledge",
    video_length_s: float = 70.0,
    scene_duration: str = "fixed:7",
    out_dir: Path | None = None,
    width: int = 768,
    height: int = 1344,
    wan_width: int = 512,
    wan_height: int = 896,
    max_scenes: int | None = 10,
    motion_prompt_override: str | None = None,
    video_only: bool = False,
) -> WanReelReport:
    """
    Single test render: Flux LoRA stills → Wan @ fixed duration → concat → F5 VO.

    Does not publish. Writes a cost/GPU-seconds report next to the MP4.
    """
    from core.cost_tracker import CostTracker
    from core.remote_gpu_manager import (
        RemoteGPUImageAdapter,
        get_manager,
        reset_manager,
    )
    from core.reel_sequence_engine import segment_script_into_act_snippets
    from core.scene_pacing import describe_scene_plan, plan_scenes
    from agents.writer.caption_engine import CaptionEngine

    started = datetime.now(timezone.utc)
    from core.scene_pacing import parse_scene_duration

    spec = parse_scene_duration(scene_duration)
    if spec.mode != "fixed":
        raise ValueError(
            f"WAN_REEL test requires fixed:N scene_duration, got {scene_duration!r}"
        )
    scene_dur = float(spec.fixed_s)
    if max_scenes is not None:
        n = max(1, int(max_scenes))
        holds = [scene_dur] * n
        video_length_s = scene_dur * n
    else:
        holds = plan_scenes(float(video_length_s), scene_duration)
        n = len(holds)
        holds = [scene_dur] * n
        video_length_s = scene_dur * n

    stamp = started.strftime("%Y%m%d_%H%M%S")
    from utils.pipeline_paths import page_outputs_dir

    root = Path(out_dir or page_outputs_dir(page_id) / "wan_reel_tests" / f"run_{stamp}")
    root.mkdir(parents=True, exist_ok=True)
    stills_dir = root / "stills"
    clips_dir = root / "clips"
    stills_dir.mkdir(exist_ok=True)
    clips_dir.mkdir(exist_ok=True)

    reset_manager()
    mgr = get_manager()
    mode = str(getattr(mgr.client, "mode", "comfyui") or "comfyui")
    tracker = CostTracker(page_id=page_id)
    clip_costs: list[WanClipCost] = []

    logger.info(
        "WAN_REEL test | topic=%r | n=%d | scene_duration_s=%.1f | total=%.1f | mode=%s | plan=%s",
        topic, n, scene_dur, video_length_s, mode, describe_scene_plan(holds),
    )

    # ── 1. Script from same spoken-beat segmentation used for images ─────────
    words_target = max(90, int(round(video_length_s * 135 / 60.0)) - 15)
    ce = CaptionEngine()
    script = ce.generate_sequence_voiceover(
        topic,
        page_niche=(
            "ancient history, lost civilisations, unbelievable historical facts, "
            "world conspiracies, ancient mysteries"
        ),
        persona_voice="investigative, neutral, immersive",
        n_acts=n,
        duration_s=video_length_s,
        total_words_target=words_target,
        economic=True,
        cta_line="",
        narrative_mode="investigative",
    )
    (root / "narration_script.txt").write_text(script, encoding="utf-8")
    snippets = segment_script_into_act_snippets(script, n)
    while len(snippets) < n:
        snippets.append(snippets[-1] if snippets else topic)
    snippets = snippets[:n]
    (root / "spoken_snippets.json").write_text(
        json.dumps(snippets, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # ── 2. Flux LoRA stills (same LoRA as ancient_knowledge page_config) ──────
    adapter = RemoteGPUImageAdapter(page_id=page_id)
    still_paths: list[Path] = []
    motion_prompts: list[str] = []
    for i, snip in enumerate(snippets):
        still_prompt = build_still_prompt(
            snip, scene_index=i, total_scenes=n, topic=topic
        )
        if motion_prompt_override and n == 1:
            motion = motion_prompt_override.strip()
        elif motion_prompt_override and i == 0:
            motion = motion_prompt_override.strip()
        else:
            motion = build_motion_prompt(
                snip, scene_index=i, total_scenes=n, topic=topic
            )
        motion_prompts.append(motion)
        logger.info("WAN still %d/%d | %s", i + 1, n, snip[:80])
        path = adapter.generate(
            still_prompt,
            output_stem=f"wan_still_{i + 1:02d}",
            output_directory=stills_dir,
            width=width,
            height=height,
        )
        still_path = Path(path)
        still_paths.append(still_path)
        _track_job(
            tracker,
            seconds=float(getattr(adapter, "last_gpu_seconds", 0) or 0),
            mode=mode,
            clips=clip_costs,
            scene_index=i,
            kind="image",
            path=still_path,
            prompt=still_prompt,
        )

    (root / "motion_prompts.json").write_text(
        json.dumps(motion_prompts, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # ── 3. Wan2.2 img→video @ fixed scene_duration_s (config value, not baked) ─
    wan_paths: list[Path] = []
    for i, (still, motion) in enumerate(zip(still_paths, motion_prompts)):
        out_clip = clips_dir / f"wan_scene_{i + 1:02d}.mp4"
        logger.info(
            "WAN video %d/%d | duration_s=%.1f (parameter) | %s",
            i + 1, n, scene_dur, snippets[i][:80],
        )
        path = mgr.generate_video(
            still,
            prompt=motion,
            output_path=out_clip,
            duration_s=scene_dur,  # injected into workflow Duration node
            width=wan_width,
            height=wan_height,
            stem=f"wan_scene_{i + 1:02d}",
        )
        wan_paths.append(Path(path))
        _track_job(
            tracker,
            seconds=float(getattr(mgr.client, "last_job_seconds", 0) or 0),
            mode=mode,
            clips=clip_costs,
            scene_index=i,
            kind="video",
            path=Path(path),
            duration_s=scene_dur,
            prompt=motion,
        )

    # ── 4. Continuous F5-TTS narration over assembled timeline ───────────────
    voice_dur = 0.0
    if video_only:
        out_mp4 = wan_paths[0] if len(wan_paths) == 1 else (
            root / f"wan_reel_{n}x{int(scene_dur)}s_video_only_v01.mp4"
        )
        if len(wan_paths) > 1:
            # Concat video clips without VO for multi-scene smoke.
            from moviepy import VideoFileClip, concatenate_videoclips  # type: ignore

            clips = [VideoFileClip(str(p)) for p in wan_paths]
            try:
                final = concatenate_videoclips(clips, method="compose")
                final.write_videofile(
                    str(out_mp4), codec="libx264", audio=False, logger=None,
                    temp_audiofile_path=moviepy_temp_audio_dir(),
                )
                final.close()
            finally:
                for c in clips:
                    c.close()
        logger.info("WAN video-only mode | skipping F5 + audio mux | out=%s", out_mp4)
    else:
        voice_path = root / "narration_f5.wav"
        logger.info("WAN F5 narration | chars=%d", len(script))
        voice_out = mgr.generate_audio(
            script,
            output_path=voice_path,
            page_id=page_id,
            stem="wan_narration_f5",
        )
        voice_path = Path(voice_out)
        _track_job(
            tracker,
            seconds=float(getattr(mgr.client, "last_job_seconds", 0) or 0),
            mode=mode,
            clips=clip_costs,
            scene_index=-1,
            kind="audio",
            path=voice_path,
            prompt=script[:180],
        )

        from moviepy import AudioFileClip  # type: ignore

        with AudioFileClip(str(voice_path)) as a:
            voice_dur = float(a.duration)

        out_mp4 = root / f"wan_reel_{n}x{int(scene_dur)}s_v01.mp4"
        concatenate_wan_clips(
            wan_paths,
            voice_path,
            out_mp4,
            target_duration_s=video_length_s,
        )

    finished = datetime.now(timezone.utc)
    total_gpu = sum(c.gpu_seconds for c in clip_costs)
    total_usd = float(tracker.total_usd())
    try:
        cost_path = tracker.write_telemetry(root / "library", variant_index=1)
    except Exception as exc:
        logger.warning("CostTracker persist failed: %s", exc)
        cost_path = root / f"cost_wan_reel_{stamp}.json"
        cost_path.write_text(
            json.dumps(tracker.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    video_gpu_s = sum(c.gpu_seconds for c in clip_costs if c.kind == "video")
    report = WanReelReport(
        topic=topic,
        n_scenes=n,
        scene_duration_s=scene_dur,
        video_length_s=video_length_s,
        mode=mode,
        holds=holds,
        narration_words=len(script.split()),
        voice_duration_s=voice_dur,
        clips=clip_costs,
        total_gpu_seconds=total_gpu,
        total_gpu_usd=total_usd,
        together_wan27_usd_at_0_10_per_s=round(video_length_s * 0.10, 4),
        output_mp4=str(out_mp4),
        cost_json=str(cost_path),
        started_at=started.isoformat(),
        finished_at=finished.isoformat(),
    )
    report_path = root / "wan_reel_report.json"
    report_path.write_text(
        json.dumps(report.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    logger.info(
        "WAN_REEL done | out=%s | gpu_s_total=%.1f | video_gpu_s=%.1f | "
        "usd≈%.4f | Together Wan2.7 @ $0.10/s for %.0fs video ≈ $%.2f",
        out_mp4.name,
        total_gpu,
        video_gpu_s,
        total_usd,
        video_length_s,
        video_length_s * 0.10,
    )
    return report

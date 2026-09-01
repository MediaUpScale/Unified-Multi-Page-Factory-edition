# -*- coding: utf-8 -*-
"""End-to-end pipeline: debate -> voice -> video.

This is the module's public façade and the only thing the parent engine needs to
import::

    from channels_config.aiwake import run_pipeline
    result = run_pipeline(topic="Is grief a slow update?", turns=4)

All wiring decisions (which observers attach, which engine speaks, whether video
is produced) are made here, so ``room.py`` and ``orchestrator.py`` stay free of
side-effect policy.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

try:
    from .contracts import DebateTranscript
    from .media.audio import build_engine
    from .memory import DebateMemory
    from .models.llm_factory import force_offline
    from .observers.core import (
        ConsoleObserver,
        MemoryObserver,
        MetricsObserver,
        TranscriptObserver,
        VoiceObserver,
    )
    from .orchestrator import Provocateur
    from .room import DebateRoom, RoomEvent
    from .settings import AiwakeSettings, load_settings, resolve_outputs_dir
    from .utils.event_bus import (
        ON_PIPELINE_FINISH,
        ON_PIPELINE_START,
        attach_optional_plugins,
        emit,
    )
except ImportError:  # pragma: no cover — standalone extraction
    from contracts import DebateTranscript  # type: ignore[no-redef]
    from media.audio import build_engine  # type: ignore[no-redef]
    from memory import DebateMemory  # type: ignore[no-redef]
    from models.llm_factory import force_offline  # type: ignore[no-redef]
    from observers.core import (  # type: ignore[no-redef]
        ConsoleObserver,
        MemoryObserver,
        MetricsObserver,
        TranscriptObserver,
        VoiceObserver,
    )
    from orchestrator import Provocateur  # type: ignore[no-redef]
    from room import DebateRoom, RoomEvent  # type: ignore[no-redef]
    from settings import AiwakeSettings, load_settings, resolve_outputs_dir  # type: ignore[no-redef]
    from utils.event_bus import (  # type: ignore[no-redef]
        ON_PIPELINE_FINISH,
        ON_PIPELINE_START,
        attach_optional_plugins,
        emit,
    )

_LOG = logging.getLogger("aiwake.pipeline")


@dataclass(slots=True)
class PipelineResult:
    """What a full run produced.

    Attributes:
        transcript: The debate record.
        video_path: Rendered MP4, or None when rendering was skipped or failed.
        exchanges: Completed provocation/rebuttal pairs.
        end_reason: ``complete``, ``aborted`` or ``interrupted``.
        audio_seconds: Total synthesised voice duration.
    """

    transcript: DebateTranscript
    video_path: Path | None
    exchanges: int
    end_reason: str
    audio_seconds: float
    dialogue_end_reason: str

    @property
    def succeeded(self) -> bool:
        return self.end_reason == "complete" and self.exchanges > 0


def run_pipeline(
    *,
    topic: str | None = None,
    turns: int | None = None,
    mode: Literal["fixed", "cornered"] | None = None,
    settings: AiwakeSettings | None = None,
    orchestrator_model: str | None = None,
    target_model: str | None = None,
    offline: bool = False,
    with_audio: bool = True,
    with_video: bool = True,
    fresh_memory: bool = False,
    output_dir: Path | None = None,
    quiet: bool = False,
) -> PipelineResult:
    """Run a debate and (optionally) produce the video.

    Args:
        topic: Debate subject. Defaults to the configured one.
        turns: Fixed exchange count, or the hard iteration cap in cornered mode.
        mode: Debate-ending strategy. None preserves the configured mode.
        settings: Pre-loaded settings; loaded from YAML when omitted.
        orchestrator_model: Override the interrogator's brain. Accepts an alias
            from the model reference dictionary or a full provider slug — the
            programmatic twin of the ``--orchestrator`` CLI flag.
        target_model: Same, for the seat under interrogation.
        offline: Route both seats to the deterministic stub provider — no key,
            no network. Exercises the full media path.
        with_audio: Synthesise voices. False forces the silent engine, which
            still yields a coherent (estimated) timeline for layout checks.
        with_video: Render the MP4. False stops after the transcript.
        fresh_memory: Wipe persisted memory before starting.
        output_dir: Override the media destination.
        quiet: Suppress the live console stream.

    Returns:
        A :class:`PipelineResult`. Partial runs still return their transcript
        and any video that could be built from it.
    """
    cfg = settings or load_settings()
    if mode is not None:
        cfg = cfg.model_copy(
            update={"debate": cfg.debate.model_copy(update={"mode": mode})}
        )
    pipeline_t0 = time.perf_counter()
    # Seat overrides first: force_offline() reads the resolved routing, so
    # swapping a brain and then dropping to the stub must not resurrect the
    # configured model.
    if orchestrator_model:
        cfg = cfg.with_model_override("orchestrator", orchestrator_model)
    if target_model:
        cfg = cfg.with_model_override("target", target_model)
    if offline:
        cfg = force_offline(cfg)

    memory = DebateMemory(cfg.memory)
    if fresh_memory:
        memory.reset()
        _LOG.info("memory reset")

    room = DebateRoom(cfg, topic=topic)
    media_dir = output_dir or resolve_outputs_dir(cfg)

    voice_observer: VoiceObserver | None = None
    if with_audio or with_video:
        # The silent engine keeps the timeline honest when audio is off.
        audio_cfg = cfg.audio if with_audio else cfg.audio.model_copy(update={"engine": "silent"})
        voice_observer = VoiceObserver(build_engine(audio_cfg), room.session_id)

    attach_optional_plugins()
    emit(
        ON_PIPELINE_START,
        {
            "session_id": room.session_id,
            "topic": room.topic,
            "debate_mode": cfg.debate.mode,
            "cornered_max_duration_s": cfg.debate.cornered_max_duration_s,
            "orchestrator_model": cfg.spec_for("orchestrator").model,
            "target_model": cfg.spec_for("target").model,
            "llm_providers": {
                "orchestrator": cfg.spec_for("orchestrator").provider,
                "target": cfg.spec_for("target").provider,
            },
            "models": {
                "orchestrator": cfg.spec_for("orchestrator").model,
                "target": cfg.spec_for("target").model,
            },
            "audio_engine": (voice_observer.engine.engine_name if voice_observer else cfg.audio.engine),
            "typewriter_gain_db": cfg.audio.typewriter.gain_db,
            "scroll_s": cfg.render.scroll_s,
            "preroll_s": cfg.render.preroll_s,
            "reply_gap_s": cfg.render.reply_gap_s,
            "send_flash_s": cfg.render.send_flash_s,
        },
    )

    room.subscribe(MemoryObserver(memory), TranscriptObserver(room.session_id), MetricsObserver())
    if voice_observer is not None:
        room.subscribe(voice_observer)
    if not quiet:
        room.subscribe(ConsoleObserver(verbose=True))

    provocateur = Provocateur(cfg, memory=memory, room=room)
    video_path: Path | None = None
    audio_seconds = 0.0
    end_reason = "interrupted"
    exchanges = 0
    try:
        result = provocateur.run(topic=topic, turns=turns)
        exchanges = result.exchanges
        end_reason = result.end_reason

        audio_seconds = voice_observer.total_duration_s if voice_observer else 0.0

        if voice_observer is not None:
            room.broadcast(
                RoomEvent.AUDIO_MIXED,
                tracks=len(voice_observer.assets),
                audio_seconds=round(audio_seconds, 3),
                engine=voice_observer.engine.engine_name,
                typewriter_gain_db=cfg.audio.typewriter.gain_db,
                typewriter_core_only=True,
            )

        if with_video and result.transcript.utterances:
            # Imported here so a headless run never needs MoviePy/Pillow installed.
            try:
                from .media.renderer import render_transcript  # noqa: PLC0415
            except ImportError:  # pragma: no cover — standalone extraction
                from media.renderer import render_transcript  # type: ignore[no-redef]

            try:
                video_path = render_transcript(
                    result.transcript,
                    cfg,
                    audio_by_turn=voice_observer.assets if voice_observer else None,
                    output_dir=media_dir,
                )
            except Exception as exc:  # noqa: BLE001 — a failed render must not lose the transcript
                _LOG.error("render failed: %s", exc)

        if video_path is not None:
            result.transcript.metadata["video_path"] = str(video_path)
            script_path = media_dir / f"aiwake_debate_{result.transcript.session_id}.txt"
            script_path.write_text(result.transcript.to_script(), encoding="utf-8")
            room.broadcast(
                RoomEvent.VIDEO_RENDERED,
                video_path=str(video_path),
                scroll_s=cfg.render.scroll_s,
                preroll_s=cfg.render.preroll_s,
                reply_gap_s=cfg.render.reply_gap_s,
                send_flash_s=cfg.render.send_flash_s,
            )

        return PipelineResult(
            transcript=result.transcript,
            video_path=video_path,
            exchanges=result.exchanges,
            end_reason=result.end_reason,
            audio_seconds=audio_seconds,
            dialogue_end_reason=result.dialogue_end_reason,
        )
    finally:
        emit(
            ON_PIPELINE_FINISH,
            {
                "session_id": room.session_id,
                "pipeline_s": time.perf_counter() - pipeline_t0,
                "end_reason": end_reason,
                "debate_mode": cfg.debate.mode,
                "dialogue_end_reason": (
                    room.transcript.metadata.get("dialogue_end_reason")
                ),
                "exchanges": exchanges,
                "video_path": str(video_path) if video_path else None,
            },
        )


__all__ = ["PipelineResult", "run_pipeline"]

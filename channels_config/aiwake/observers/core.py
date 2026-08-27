# -*- coding: utf-8 -*-
"""Concrete room observers.

Each observer does exactly one thing and knows nothing about the others. They
are attached by the CLI (or the parent engine) at wiring time, which is where
the decision about *which* side effects a run has actually lives.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

try:
    from ..contracts import SpeakerRole
    from ..media.audio import AudioAsset, TTSEngine, TTSError
    from ..memory import DebateMemory
    from ..room import DebateObserver, RoomEvent, RoomEventPayload
    from ..settings import resolve_store_dir
except ImportError:  # pragma: no cover — standalone extraction
    from contracts import SpeakerRole  # type: ignore[no-redef]
    from media.audio import AudioAsset, TTSEngine, TTSError  # type: ignore[no-redef]
    from memory import DebateMemory  # type: ignore[no-redef]
    from room import DebateObserver, RoomEvent, RoomEventPayload  # type: ignore[no-redef]
    from settings import resolve_store_dir  # type: ignore[no-redef]

_LOG = logging.getLogger("aiwake.observers")

_ANSI = {
    SpeakerRole.ORCHESTRATOR: "\033[96m",
    SpeakerRole.TARGET: "\033[93m",
    SpeakerRole.SYSTEM: "\033[90m",
}
_RESET = "\033[0m"


class ConsoleObserver(DebateObserver):
    """Streams the debate to stdout as it happens.

    Args:
        colour: Emit ANSI colour. Disable when piping to a file.
        verbose: Also report guardrail trips and provider errors.
    """

    interests = (
        RoomEvent.DEBATE_STARTED,
        RoomEvent.UTTERANCE,
        RoomEvent.GUARDRAIL_TRIPPED,
        RoomEvent.PROVIDER_ERROR,
        RoomEvent.DEBATE_ENDED,
    )

    def __init__(self, *, colour: bool = True, verbose: bool = False) -> None:
        self.colour = colour
        self.verbose = verbose

    def _paint(self, role: SpeakerRole, text: str) -> str:
        if not self.colour:
            return text
        return f"{_ANSI.get(role, '')}{text}{_RESET}"

    def on_event(self, payload: RoomEventPayload) -> None:
        if payload.event is RoomEvent.DEBATE_STARTED:
            models = payload.detail.get("models", {})
            print(f"\n=== AIWAKE :: {payload.room.topic} ===")
            print(f"    session {payload.detail.get('session_id')}")
            for role, slug in models.items():
                print(f"    {role:>13} -> {slug}")
            print()

        elif payload.event is RoomEvent.UTTERANCE and payload.utterance is not None:
            utterance = payload.utterance
            header = f"[{utterance.speaker_name}] ({utterance.char_count} chars, {utterance.latency_ms} ms)"
            print(self._paint(utterance.role, header))
            print(f"  {utterance.text}\n")

        elif payload.event is RoomEvent.GUARDRAIL_TRIPPED and self.verbose:
            print(
                self._paint(
                    SpeakerRole.SYSTEM,
                    f"  ! guardrail {payload.detail.get('violations')} "
                    f"({payload.detail.get('original_chars')} -> {payload.detail.get('clamped_chars')} chars)",
                )
            )

        elif payload.event is RoomEvent.PROVIDER_ERROR:
            print(self._paint(SpeakerRole.SYSTEM, f"  !! provider error: {payload.detail.get('error')}"))

        elif payload.event is RoomEvent.DEBATE_ENDED:
            print(f"=== ended ({payload.detail.get('reason')}) — {payload.detail.get('turns')} lines ===\n")


class MemoryObserver(DebateObserver):
    """Feeds every utterance into the memory layer as it is spoken.

    Keeping ingestion on the event bus rather than inside the orchestrator means
    an externally-driven room (a live stream, a replay) also builds memory.
    """

    interests = (RoomEvent.UTTERANCE, RoomEvent.DEBATE_ENDED)

    def __init__(self, memory: DebateMemory) -> None:
        self.memory = memory

    def on_event(self, payload: RoomEventPayload) -> None:
        if payload.event is RoomEvent.UTTERANCE and payload.utterance is not None:
            self.memory.ingest(payload.utterance)
        elif payload.event is RoomEvent.DEBATE_ENDED:
            self.memory.flush()


class TranscriptObserver(DebateObserver):
    """Appends each utterance to a JSONL file, then writes a final JSON summary.

    Line-buffered on purpose: a crashed or interrupted session still leaves a
    complete record of everything that was said before the failure.
    """

    interests = (RoomEvent.UTTERANCE, RoomEvent.DEBATE_ENDED)

    def __init__(self, session_id: str, *, output_dir: Path | None = None) -> None:
        self.dir = output_dir or (resolve_store_dir() / "transcripts")
        self.dir.mkdir(parents=True, exist_ok=True)
        self.jsonl_path = self.dir / f"{session_id}.jsonl"
        self.json_path = self.dir / f"{session_id}.json"
        self._handle = self.jsonl_path.open("a", encoding="utf-8")

    def on_event(self, payload: RoomEventPayload) -> None:
        if payload.event is RoomEvent.UTTERANCE and payload.utterance is not None:
            self._handle.write(payload.utterance.model_dump_json() + "\n")
            self._handle.flush()
        elif payload.event is RoomEvent.DEBATE_ENDED:
            self.json_path.write_text(
                payload.room.transcript.model_dump_json(indent=2),
                encoding="utf-8",
            )
            _LOG.info("transcript written -> %s", self.json_path)

    def close(self) -> None:
        if not self._handle.closed:
            self._handle.close()


class VoiceObserver(DebateObserver):
    """Synthesises each line as it is spoken and collects the tracks.

    Synthesising per-utterance rather than in a post-pass means the audio for
    turn 1 exists even if turn 4 explodes, and the renderer can start from a
    partial transcript.

    Attributes:
        assets: Completed tracks keyed by ``turn_index``, consumed by the
            renderer to build the timeline.
    """

    interests = (RoomEvent.UTTERANCE,)

    def __init__(self, engine: TTSEngine, session_id: str) -> None:
        self.engine = engine
        self.session_id = session_id
        self.assets: dict[int, AudioAsset] = {}

    def on_event(self, payload: RoomEventPayload) -> None:
        utterance = payload.utterance
        if utterance is None or not utterance.text.strip():
            return
        try:
            asset = self.engine.speak(utterance, session_id=self.session_id)
        except TTSError as exc:
            # Non-fatal: the renderer falls back to an estimated duration and
            # the segment simply plays silent.
            _LOG.warning("TTS failed for turn %d: %s", utterance.turn_index, exc)
            return
        self.assets[utterance.turn_index] = asset
        _LOG.debug("voiced turn %d (%.2fs)", utterance.turn_index, asset.duration_s)

    @property
    def total_duration_s(self) -> float:
        return sum(asset.duration_s for asset in self.assets.values())


class MetricsObserver(DebateObserver):
    """Accumulates run telemetry: tokens, latency, guardrail trips.

    Written next to the transcript so a series of runs can be compared without
    re-parsing the transcripts themselves.
    """

    def __init__(self, *, output_dir: Path | None = None) -> None:
        self.dir = output_dir or (resolve_store_dir() / "metrics")
        self.dir.mkdir(parents=True, exist_ok=True)
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.latencies_ms: list[int] = []
        self.guardrail_trips: list[dict[str, object]] = []
        self.errors: list[str] = []

    def on_event(self, payload: RoomEventPayload) -> None:
        if payload.event is RoomEvent.UTTERANCE and payload.utterance is not None:
            self.prompt_tokens += payload.utterance.prompt_tokens
            self.completion_tokens += payload.utterance.completion_tokens
            if payload.utterance.latency_ms:
                self.latencies_ms.append(payload.utterance.latency_ms)
        elif payload.event is RoomEvent.GUARDRAIL_TRIPPED:
            self.guardrail_trips.append(dict(payload.detail))
        elif payload.event is RoomEvent.PROVIDER_ERROR:
            self.errors.append(str(payload.detail.get("error")))
        elif payload.event is RoomEvent.DEBATE_ENDED:
            self._write(payload)

    def _write(self, payload: RoomEventPayload) -> None:
        report = {
            "session_id": payload.room.session_id,
            "topic": payload.room.topic,
            "end_reason": payload.detail.get("reason"),
            "lines": payload.detail.get("turns"),
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "mean_latency_ms": round(sum(self.latencies_ms) / len(self.latencies_ms)) if self.latencies_ms else 0,
            "guardrail_trips": self.guardrail_trips,
            "errors": self.errors,
        }
        path = self.dir / f"{payload.room.session_id}_metrics.json"
        path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        _LOG.info("metrics written -> %s", path)


__all__ = [
    "ConsoleObserver",
    "MemoryObserver",
    "MetricsObserver",
    "TranscriptObserver",
    "VoiceObserver",
]

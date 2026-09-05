# -*- coding: utf-8 -*-
"""Assemble a review MP4 from a QA-hold package. Not postable.

Hold reassemble used to reuse work_dir/vo_scene_XX.mp3 if the file existed.
That cache is not keyed by TTS_SPEED, so a duck-only rebuild kept the old
clips and (with edge-trim) collapsed a 2.93s 0.80 take down to a 2.62s slot.
This script always deletes those clips and regenerates at the live TTS_SPEED.
"""
from __future__ import annotations

from pathlib import Path as _ReorgPath
import sys as _reorg_sys
_REORG_ROOT = _ReorgPath(__file__).resolve().parents[2]
if str(_REORG_ROOT) not in _reorg_sys.path:
    _reorg_sys.path.insert(0, str(_REORG_ROOT))

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utils.pipeline_paths import page_outputs_dir

from agents.media.audio_engine import generate_voiceover_with_timestamps  # noqa: E402
from core.economic_reel_lofi import config as lofi_cfg  # noqa: E402
from core.economic_reel_lofi.assembler import (  # noqa: E402
    assemble_lofi_reel,
    measure_vo_speech_duration,
)
from core.economic_reel_lofi.pipeline import (  # noqa: E402
    _tts_breath_commas,
    _tts_text_with_breaks,
)

HOLD = page_outputs_dir("wonder_feed") / "clips" / "lofi_hold_hope_20260824_194933_v01.json"
OUT = page_outputs_dir("wonder_feed") / "clips" / "lofi_reel_hope_20260824_194933_v01_review.mp4"
LINE1_MIN_S = 2.80  # isolated 0.80 probe file is 2.93s; reject the old 2.62s slot


def _file_duration_s(path: Path) -> float:
    from moviepy import AudioFileClip  # type: ignore

    clip = AudioFileClip(str(path))
    try:
        return float(clip.duration or 0.0)
    finally:
        clip.close()


def _regen_vo(run_dir: Path, captions: list[str]) -> list[Path]:
    run_dir.mkdir(parents=True, exist_ok=True)
    for stale in run_dir.glob("vo_scene_*.mp3"):
        stale.unlink()
        print(f"[hold VO] deleted stale {stale.name}")
    meta_path = run_dir / "vo_meta.json"
    if meta_path.is_file():
        meta_path.unlink()

    speed = float(lofi_cfg.tts_speed())
    model_id = lofi_cfg.tts_model() or "eleven_multilingual_v2"
    voice_id = lofi_cfg.tts_voice_id()
    print(
        f"[hold VO] REGEN all {len(captions)} lines "
        f"config={lofi_cfg.__file__} TTS_SPEED={lofi_cfg.TTS_SPEED!r} "
        f"tts_speed()={speed!r} model={model_id!r}"
    )
    voices: list[Path] = []
    recs: list[dict] = []
    for i, caption in enumerate(captions, start=1):
        tts_text = _tts_text_with_breaks(_tts_breath_commas(caption))
        use_ssml = "<break" in tts_text
        dest = run_dir / f"vo_scene_{i:02d}.mp3"
        dest, _timings = generate_voiceover_with_timestamps(
            tts_text,
            dest,
            voice_id=voice_id or None,
            model_id=model_id,
            force_elevenlabs=True,
            expressive_mode=False,
            enable_ssml=use_ssml,
            speed=speed,
            voice_settings={
                "stability": 1.0,
                "similarity_boost": 1.0,
                "style": 0.0,
                "use_speaker_boost": True,
                "speed": speed,
            },
        )
        path = Path(dest)
        file_dur = _file_duration_s(path)
        slot_dur = float(measure_vo_speech_duration(path))
        recs.append(
            {
                "scene": i,
                "caption": caption,
                "tts_text": tts_text,
                "file": path.name,
                "file_duration_s": round(file_dur, 3),
                "slot_duration_s": round(slot_dur, 3),
                "speed": speed,
                "ssml": use_ssml,
            }
        )
        print(
            f"[hold VO] scene {i} file={file_dur:.3f}s slot={slot_dur:.3f}s "
            f"speed={speed} ssml={use_ssml} {caption!r}"
        )
        voices.append(path)
    meta = {
        "tts_speed": speed,
        "tts_model": model_id,
        "tts_voice_id": voice_id,
        "config_file": lofi_cfg.__file__,
        "trim_tts_edges": bool(getattr(lofi_cfg, "VO_TRIM_TTS_EDGES", True)),
        "lines": recs,
    }
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print("[hold VO] wrote", meta_path)
    return voices


def _first_segment_s(vo_concat: Path) -> float | None:
    """Time of first manufactured inter-line gap in the VO concat."""
    if not vo_concat.is_file():
        return None
    import numpy as np
    from moviepy import AudioFileClip  # type: ignore

    clip = AudioFileClip(str(vo_concat))
    try:
        arr = np.asarray(clip.to_soundarray(fps=44100), dtype=np.float32)
    finally:
        clip.close()
    if arr.ndim == 1:
        arr = arr.reshape(-1, 1)
    env = np.max(np.abs(arr), axis=1)
    thresh = 10.0 ** (-40.0 / 20.0)
    silent = env <= thresh
    sr = 44100
    min_gap = int(round(0.22 * sr))
    i = 0
    n = int(silent.shape[0])
    while i < n:
        if not silent[i]:
            i += 1
            continue
        j = i + 1
        while j < n and silent[j]:
            j += 1
        if (j - i) >= min_gap and i > int(0.2 * sr):
            return i / float(sr)
        i = j
    return None


def _print_gap_vs_speech_rms(mp4: Path, durs: list[float], trail: float) -> None:
    import numpy as np
    from moviepy import AudioFileClip  # type: ignore

    t = 0.0
    gaps = []
    mids = []
    for i, dur in enumerate(durs):
        vo = float(dur) - (0.0 if i >= len(durs) - 1 else float(trail))
        mids.append((t + 0.6, t + max(0.9, vo - 0.4)))
        t += vo
        if i < len(durs) - 1:
            gaps.append((t, t + float(trail)))
            t += float(trail)
    clip = AudioFileClip(str(mp4))
    try:
        arr = np.asarray(clip.to_soundarray(fps=44100), dtype=np.float32)
    finally:
        clip.close()
    sr = 44100

    def rms(a, b):
        sl = arr[int(round(a * sr)) : int(round(b * sr))]
        return float(np.sqrt(np.mean(np.square(sl)))) if sl.size else 0.0

    def db(x):
        return 20.0 * np.log10(max(x, 1e-9))

    gap_db = [db(rms(a, b)) for a, b in gaps]
    mid_db = [db(rms(a, b)) for a, b in mids]
    mean_gap = float(np.mean(gap_db))
    mean_mid = float(np.mean(mid_db))
    print("[hold verify] mid-speech mix dB", [round(x, 1) for x in mid_db])
    print("[hold verify] gap mix dB", [round(x, 1) for x in gap_db])
    print(
        f"[hold verify] mean mid-speech={mean_mid:.1f}dB mean gap={mean_gap:.1f}dB "
        f"diff={mean_gap - mean_mid:.1f}dB (target -8 to -12)"
    )


def _verify_line1(run_dir: Path, duck_start: float | None) -> int:
    vp = run_dir / "vo_scene_01.mp3"
    file_dur = _file_duration_s(vp)
    slot_dur = float(measure_vo_speech_duration(vp))
    print(
        f"[hold verify] line1 file={file_dur:.3f}s slot={slot_dur:.3f}s "
        f"first_duck_start={duck_start}"
    )
    measured = slot_dur if not bool(getattr(lofi_cfg, "VO_TRIM_TTS_EDGES", True)) else file_dur
    if duck_start is not None:
        measured = float(duck_start)
    if measured < LINE1_MIN_S:
        print(
            f"[hold verify] FAIL line-1 segment {measured:.3f}s < {LINE1_MIN_S:.2f}s "
            f"(stale 2.62s slot still in play)"
        )
        return 2
    print(f"[hold verify] PASS line-1 segment {measured:.3f}s (probe 0.80 ≈ 2.93s)")
    return 0


def _existing_vo(run_dir: Path, n: int) -> list[Path] | None:
    speed = float(lofi_cfg.tts_speed())
    files = [run_dir / f"vo_scene_{i:02d}.mp3" for i in range(1, n + 1)]
    if not all(p.is_file() and p.stat().st_size > 1000 for p in files):
        return None
    meta_path = run_dir / "vo_meta.json"
    if not meta_path.is_file():
        return None
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    cached = float(meta.get("tts_speed") or 0.0)
    if abs(cached - speed) > 0.011:
        print(f"[hold VO] cache speed={cached} live={speed} — will regen")
        return None
    print(f"[hold VO] reuse {n} clips at tts_speed={cached} (no regen)")
    return files


def main() -> int:
    hold = json.loads(HOLD.read_text(encoding="utf-8"))
    run_dir = Path(hold["work_dir"])
    lines = [r for r in (hold.get("script") or {}).get("lines") or [] if isinstance(r, dict)]
    captions = [str(r.get("text") or "") for r in lines]
    scenes = [run_dir / f"scene_{i:02d}.png" for i in range(1, len(captions) + 1)]
    voices = _existing_vo(run_dir, len(captions)) or _regen_vo(run_dir, captions)
    durs = []
    beat = float(lofi_cfg.beat_duration_s())
    trail = float(lofi_cfg.VO_INTERLINE_SILENCE_S)
    n = len(captions)
    for i, vp in enumerate(voices):
        vo_dur = float(measure_vo_speech_duration(vp)) if vp.is_file() else 0.0
        t = 0.0 if i >= n - 1 else trail
        dur_i, _ = lofi_cfg.slot_duration_for_vo(vo_dur, base_s=beat, trailing_silence_s=t)
        durs.append(dur_i)
        print(f"scene {i+1} vo={vo_dur:.2f}s slot={dur_i:.2f}s {captions[i]!r}")
    missing = [str(p) for p in scenes if not p.is_file()]
    if missing:
        print("missing stills", missing)
        return 1
    OUT.parent.mkdir(parents=True, exist_ok=True)
    assemble_lofi_reel(
        scenes,
        captions,
        OUT,
        engine_root=ROOT,
        page_id="wonder_feed",
        scene_duration_s=lofi_cfg.SCENE_DURATION_S,
        scene_durations=durs,
        caption_style=lofi_cfg.DEFAULT_CAPTION_STYLE,
        voice_paths=voices,
        word_timings_per_scene=None,
    )
    print("REVIEW MP4 (visual HOLD, not postable) ->", OUT)
    first_duck = durs[0] - trail if n > 1 else durs[0]
    concat = OUT.with_name(OUT.stem + "_vo_concat.mp3")
    extracted = _first_segment_s(concat)
    print(f"[hold verify] vo_concat silencedetect-style first gap at {extracted}")
    print(f"[hold verify] assembled line-1 slot before 300ms insert = {first_duck:.3f}s")
    line1 = _verify_line1(run_dir, first_duck)
    _print_gap_vs_speech_rms(OUT, durs, trail)
    return line1


if __name__ == "__main__":
    raise SystemExit(main())

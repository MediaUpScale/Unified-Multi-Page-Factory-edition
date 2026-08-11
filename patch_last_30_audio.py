# -*- coding: utf-8 -*-
"""
patch_last_30_audio.py — Fast BGM −20% remux for the latest Master Mei clips.

Re-mixes cached voice + music_v2 (+ optional atmosphere SFX) at the new BGM
volume (0.24 = 0.30 × 0.80), then remuxes onto each MP4 with ``-c:v copy``
(no video re-encode).

Usage
-----
    python patch_last_30_audio.py
    python patch_last_30_audio.py --n 30 --dry-run
    python patch_last_30_audio.py --clips-dir outputs/master_mei/clips
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env", override=False)
except ImportError:
    pass

# Mix levels — keep in sync with channels_config/master_mei/page_config.py
BGM_VOLUME = 0.24          # previous 0.30 × 0.80 (−20%)
VOICE_VOLUME = 1.0
SFX_VOLUME = 0.35
BGM_START_S = 0.5
BGM_FADE_IN_S = 0.2
SFX_FADE_IN_S = 0.2

_VARIANT_RE = re.compile(r"_v(\d+)$", re.IGNORECASE)


def _resolve_ffmpeg() -> str:
    ff = shutil.which("ffmpeg")
    if ff:
        return ff
    try:
        import imageio_ffmpeg  # type: ignore

        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and Path(exe).is_file():
            return str(exe)
    except Exception:
        pass
    for cand in (
        Path.home() / "AppData/Local/Programs/Stremio/ffmpeg.exe",
        Path(r"C:\ffmpeg\bin\ffmpeg.exe"),
    ):
        if cand.is_file():
            return str(cand)
    raise RuntimeError("ffmpeg not found on PATH / imageio_ffmpeg / common install paths.")


def _resolve_ffprobe(ffmpeg: str) -> str | None:
    fp = shutil.which("ffprobe")
    if fp:
        return fp
    # Same folder as ffmpeg (if a full install)
    sibling = Path(ffmpeg).with_name("ffprobe.exe")
    if sibling.is_file():
        return str(sibling)
    sibling2 = Path(ffmpeg).with_name("ffprobe")
    if sibling2.is_file():
        return str(sibling2)
    return None


def _audio_duration_s(path: Path, *, ffmpeg: str, ffprobe: str | None) -> float:
    if ffprobe:
        cmd = [
            ffprobe,
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "json",
            str(path),
        ]
        out = subprocess.check_output(cmd, text=True)
        data = json.loads(out)
        return float(data["format"]["duration"])

    # Fallback: parse ``ffmpeg -i`` stderr (imageio-ffmpeg builds often lack ffprobe)
    proc = subprocess.run(
        [ffmpeg, "-i", str(path)],
        capture_output=True,
        text=True,
    )
    blob = (proc.stderr or "") + (proc.stdout or "")
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", blob)
    if not m:
        raise RuntimeError(f"Could not probe duration for {path.name}")
    hh, mm, ss = int(m.group(1)), int(m.group(2)), float(m.group(3))
    return hh * 3600.0 + mm * 60.0 + ss


def _latest_mp4s(clips_dir: Path, n: int) -> list[Path]:
    mp4s = [p for p in clips_dir.glob("*.mp4") if p.is_file()]
    mp4s.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return mp4s[: max(1, int(n))]


def _resolve_stems(mp4: Path, clips_dir: Path) -> tuple[Path, Path, Path | None] | None:
    """
    Map a final reel_*.mp4 to cached ``*_vN_vN_voice.mp3`` + music + optional SFX.

    Final clip names often differ from the production stem; match by variant
    index and nearest mtime.
    """
    m = _VARIANT_RE.search(mp4.stem)
    if not m:
        return None
    vn = m.group(1)
    voices = list(clips_dir.glob(f"*_v{vn}_v{vn}_voice.mp3"))
    if not voices:
        voices = list(clips_dir.glob(f"*_v{vn}_voice.mp3"))
    if not voices:
        return None
    target_mtime = mp4.stat().st_mtime
    voice = min(voices, key=lambda p: abs(p.stat().st_mtime - target_mtime))
    prefix = voice.name[: -len("_voice.mp3")]
    music = clips_dir / f"{prefix}_music_v2.mp3"
    if not music.is_file():
        music = clips_dir / f"{prefix}_ambient.mp3"
    if not music.is_file():
        return None
    sfx = clips_dir / f"{prefix}_atmosphere_sfx.mp3"
    return voice, music, sfx if sfx.is_file() else None


def _mix_audio(
    *,
    ffmpeg: str,
    ffprobe: str | None,
    voice: Path,
    music: Path,
    sfx: Path | None,
    out_audio: Path,
    bgm_volume: float,
) -> None:
    vo_dur = _audio_duration_s(voice, ffmpeg=ffmpeg, ffprobe=ffprobe)
    delay_ms = int(round(float(BGM_START_S) * 1000.0))

    if sfx is not None:
        filt = (
            f"[0:a]aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,"
            f"volume={VOICE_VOLUME}[vo];"
            f"[1:a]aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,"
            f"volume={bgm_volume},afade=t=in:st=0:d={BGM_FADE_IN_S},"
            f"adelay={delay_ms}|{delay_ms},apad,atrim=0:{vo_dur:.3f},asetpts=N/SR/TB[bgm];"
            f"[2:a]aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,"
            f"volume={SFX_VOLUME},afade=t=in:st=0:d={SFX_FADE_IN_S},"
            f"aloop=loop=-1:size=2e+09,atrim=0:{vo_dur:.3f},asetpts=N/SR/TB[sfx];"
            f"[vo][bgm][sfx]amix=inputs=3:duration=first:dropout_transition=0:normalize=0[aout]"
        )
        cmd = [
            ffmpeg, "-y",
            "-i", str(voice),
            "-i", str(music),
            "-i", str(sfx),
            "-filter_complex", filt,
            "-map", "[aout]",
            "-c:a", "aac",
            "-b:a", "192k",
            str(out_audio),
        ]
    else:
        filt = (
            f"[0:a]aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,"
            f"volume={VOICE_VOLUME}[vo];"
            f"[1:a]aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,"
            f"volume={bgm_volume},afade=t=in:st=0:d={BGM_FADE_IN_S},"
            f"adelay={delay_ms}|{delay_ms},apad,atrim=0:{vo_dur:.3f},asetpts=N/SR/TB[bgm];"
            f"[vo][bgm]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[aout]"
        )
        cmd = [
            ffmpeg, "-y",
            "-i", str(voice),
            "-i", str(music),
            "-filter_complex", filt,
            "-map", "[aout]",
            "-c:a", "aac",
            "-b:a", "192k",
            str(out_audio),
        ]

    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffmpeg mix failed ({voice.name}):\n{proc.stderr[-1200:]}"
        )


def _remux_video_copy(
    *,
    ffmpeg: str,
    video: Path,
    audio: Path,
    output: Path,
) -> None:
    cmd = [
        ffmpeg, "-y",
        "-i", str(video),
        "-i", str(audio),
        "-c:v", "copy",
        "-c:a", "aac",
        "-b:a", "192k",
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-shortest",
        "-movflags", "+faststart",
        str(output),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffmpeg remux failed ({video.name}):\n{proc.stderr[-1200:]}"
        )


def patch_clip(
    mp4: Path,
    *,
    clips_dir: Path,
    ffmpeg: str,
    ffprobe: str | None,
    bgm_volume: float,
    dry_run: bool = False,
    backup: bool = True,
) -> str:
    stems = _resolve_stems(mp4, clips_dir)
    if stems is None:
        return f"SKIP (no cached voice/music stems): {mp4.name}"
    voice, music, sfx = stems
    if dry_run:
        sfx_name = sfx.name if sfx else "(none)"
        return (
            f"DRY-RUN {mp4.name} | voice={voice.name} | music={music.name} | "
            f"sfx={sfx_name} | bgm_vol={bgm_volume}"
        )

    with tempfile.TemporaryDirectory(prefix="mei_bgm_patch_") as tmp:
        tmp_dir = Path(tmp)
        mixed = tmp_dir / "mixed_audio.m4a"
        remuxed = tmp_dir / "remuxed.mp4"
        _mix_audio(
            ffmpeg=ffmpeg,
            ffprobe=ffprobe,
            voice=voice,
            music=music,
            sfx=sfx,
            out_audio=mixed,
            bgm_volume=bgm_volume,
        )
        _remux_video_copy(
            ffmpeg=ffmpeg,
            video=mp4,
            audio=mixed,
            output=remuxed,
        )
        if backup:
            bak = mp4.with_suffix(".mp4.bak_pre_bgm024")
            if not bak.is_file():
                shutil.copy2(mp4, bak)
        # Atomic-ish replace
        tmp_out = mp4.with_suffix(".mp4.tmp_bgm024")
        shutil.copy2(remuxed, tmp_out)
        tmp_out.replace(mp4)
    return f"OK {mp4.name} (BGM={bgm_volume})"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Remux last N Master Mei clips with BGM volume −20% (0.24)."
    )
    parser.add_argument("--n", type=int, default=30, help="How many latest clips (default 30)")
    parser.add_argument(
        "--clips-dir",
        type=Path,
        default=ROOT / "outputs" / "master_mei" / "clips",
        help="Clips directory",
    )
    parser.add_argument(
        "--bgm-volume",
        type=float,
        default=BGM_VOLUME,
        help=f"BGM linear volume (default {BGM_VOLUME})",
    )
    parser.add_argument("--dry-run", action="store_true", help="Resolve stems only")
    parser.add_argument("--no-backup", action="store_true", help="Skip .bak_pre_bgm024 copies")
    args = parser.parse_args()

    clips_dir = args.clips_dir
    if not clips_dir.is_dir():
        print(f"Clips dir missing: {clips_dir}")
        return 1

    ffmpeg = _resolve_ffmpeg()
    ffprobe = _resolve_ffprobe(ffmpeg)
    mp4s = _latest_mp4s(clips_dir, args.n)
    print(f"Patching {len(mp4s)} clip(s) in {clips_dir}")
    print(f"ffmpeg={ffmpeg}")
    print(f"ffprobe={ffprobe or '(ffmpeg -i fallback)'}")
    print(f"BGM volume={args.bgm_volume} (voice={VOICE_VOLUME}, sfx={SFX_VOLUME})")

    ok = 0
    skipped = 0
    failed = 0
    for mp4 in mp4s:
        try:
            msg = patch_clip(
                mp4,
                clips_dir=clips_dir,
                ffmpeg=ffmpeg,
                ffprobe=ffprobe,
                bgm_volume=float(args.bgm_volume),
                dry_run=bool(args.dry_run),
                backup=not bool(args.no_backup),
            )
            print(f"  {msg}")
            if msg.startswith("OK") or msg.startswith("DRY-RUN"):
                ok += 1
            else:
                skipped += 1
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"  FAIL {mp4.name}: {type(exc).__name__}: {exc}")

    print(f"Done | ok={ok} skipped={skipped} failed={failed}")
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())

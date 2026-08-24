# -*- coding: utf-8 -*-
"""One-shot audit of the hope reel under review. No assembly."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core_engine.economic_reel_lofi.pipeline import (  # noqa: E402
    _linework_stats,
    assess_cross_beat_style,
    assess_object_beat_visual_continuity,
    pixel_lighting_label,
)
from core_engine.economic_reel_lofi.script_agent import (  # noqa: E402
    assess_aphorism_close,
    assess_fixed_referent,
    assess_load_bearing_connectives,
    assess_object_beat_continuity,
)
import imageio_ffmpeg

META = ROOT / "outputs" / "wonder_feed" / "clips" / "lofi_reel_hope_20260824_143339_v01.json"
MP4 = ROOT / "outputs" / "wonder_feed" / "clips" / "lofi_reel_hope_20260824_143339_v01.mp4"
STILLS = ROOT / "outputs" / "wonder_feed" / "assets" / "lofi_run_20260824_143339_01"
OUT = ROOT / "outputs" / "wonder_feed" / "clips" / "vo_pace_probe" / "hope_review_audit.json"


def _ffmpeg() -> str:
    return imageio_ffmpeg.get_ffmpeg_exe()


def main() -> int:
    data = json.loads(META.read_text(encoding="utf-8"))
    script = data.get("script") or {}
    lines = list(script.get("lines") or [])
    paths = [STILLS / f"scene_{i:02d}.png" for i in range(1, 10)]
    print("dominant_lighting", script.get("dominant_lighting"))
    print("lighting_beats", script.get("lighting_beats"))
    print("lighting_counts", script.get("lighting_counts"))
    for row in lines:
        print(
            f"s{row.get('scene')} type={row.get('subject_type')} "
            f"close={row.get('close_variant') or '-'} "
            f"obj={row.get('key_object')!r} "
            f"light={row.get('lighting_condition')} "
            f"text={row.get('text')!r}"
        )

    print("\n=== still PNG stats ===")
    still_stats = []
    for p in paths:
        st = _linework_stats(p)
        pix, meta = pixel_lighting_label(p)
        rec = {
            "file": p.name,
            "bytes": p.stat().st_size,
            "uniq16": int(st["uniq16"]),
            "lap_var": round(float(st["lap_var"]), 1),
            "edge": round(float(st["edge"]), 2),
            "std": round(float(st["std"]), 1),
            "pixel": pix,
            **meta,
        }
        still_stats.append(rec)
        print(rec)

    print("\n=== gates on locked hope script ===")
    for name, rep in (
        ("referent", assess_fixed_referent(lines)),
        ("aphorism", assess_aphorism_close(lines, "hope")),
        ("connectives", assess_load_bearing_connectives(lines)),
        ("object_text", assess_object_beat_continuity(lines)),
    ):
        print(name, json.dumps(rep, ensure_ascii=False)[:500])

    xstyle = assess_cross_beat_style(paths, lines)
    obj_ship = assess_object_beat_visual_continuity(lines, paths)
    print("cross_style passed", xstyle.get("passed"), "fails", xstyle.get("fails"))
    print("object_ship passed", obj_ship.get("passed"), "fails", obj_ship.get("fails"))

    # Extract one mid-scene frame from the delivered MP4 and compare lighting.
    durs = list(data.get("scene_durations") or [3.0] * 9)
    t = 0.0
    frame_dir = ROOT / "outputs" / "wonder_feed" / "clips" / "vo_pace_probe" / "hope_mp4_frames"
    frame_dir.mkdir(parents=True, exist_ok=True)
    ff = _ffmpeg()
    frame_stats = []
    for i, dur in enumerate(durs, 1):
        mid = t + max(0.15, float(dur) * 0.5)
        out = frame_dir / f"mp4_s{i:02d}.png"
        subprocess.run(
            [ff, "-y", "-hide_banner", "-ss", f"{mid:.3f}", "-i", str(MP4), "-frames:v", "1", str(out)],
            capture_output=True,
        )
        t += float(dur)
        if not out.is_file():
            continue
        st = _linework_stats(out)
        pix, meta = pixel_lighting_label(out)
        rec = {
            "scene": i,
            "t": round(mid, 3),
            "uniq16": int(st["uniq16"]),
            "lap_var": round(float(st["lap_var"]), 1),
            "edge": round(float(st["edge"]), 2),
            "std": round(float(st["std"]), 1),
            "pixel": pix,
            **meta,
        }
        frame_stats.append(rec)
        print("mp4_frame", rec)

    alt = ROOT / "outputs" / "wonder_feed" / "assets" / "lofi_run_20260824_142816_01"
    size_match = []
    for i in range(1, 10):
        a = STILLS / f"scene_{i:02d}.png"
        b = alt / f"scene_{i:02d}.png"
        size_match.append(
            {
                "scene": i,
                "run_143339": a.stat().st_size if a.is_file() else None,
                "run_142816": b.stat().st_size if b.is_file() else None,
                "same_bytes": a.is_file() and b.is_file() and a.stat().st_size == b.stat().st_size,
            }
        )

    payload = {
        "declared_dominant": script.get("dominant_lighting"),
        "declared_beats": script.get("lighting_beats"),
        "declared_counts": script.get("lighting_counts"),
        "still_stats": still_stats,
        "cross_beat_style": {
            "passed": xstyle.get("passed"),
            "fails": xstyle.get("fails"),
            "clusters": xstyle.get("clusters"),
            "pixel_counts": xstyle.get("pixel_counts"),
            "declared_counts": xstyle.get("declared_counts"),
            "mismatches": xstyle.get("mismatches"),
        },
        "object_ship": obj_ship,
        "mp4_frames": frame_stats,
        "png_byte_match_142816": size_match,
        "gates": {
            "referent": assess_fixed_referent(lines),
            "aphorism": assess_aphorism_close(lines, "hope"),
            "connectives": assess_load_bearing_connectives(lines),
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print("wrote", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

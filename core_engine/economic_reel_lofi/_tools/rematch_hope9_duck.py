# -*- coding: utf-8 -*-
"""Re-mix existing hope9 / 10s probe VO concats with expanded duck windows. No TTS."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from avatar_engine.audio_engine import _audio_file_duration_s  # noqa: E402
from core_engine.economic_reel_lofi.assembler import list_library_bgm_tracks  # noqa: E402
from core_engine.economic_reel_lofi._tools.vo_pace_probe import silencedetect  # noqa: E402
from core_engine.economic_reel_lofi._tools.vo_pause_probe import (  # noqa: E402
    OUT_DIR,
    _expand_duck_windows,
    _mix_ducked,
)


def _overlap_ok(planned, sil_rep, lead=0.35):
    rows = []
    for a, b in planned:
        hits = []
        for s, e, d in zip(sil_rep["starts"], sil_rep["ends"], sil_rep["durs"]):
            if float(e) < float(a) - lead or float(s) > float(b) + 0.05:
                continue
            hits.append(round(float(d), 3))
        rows.append(
            {
                "planned": [a, b],
                "detected_durs": hits,
                "ok": any(h >= 0.25 for h in hits),
            }
        )
    return rows


def rematch(summary_path: Path, vo_key: str, mixed_name: str) -> dict:
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    vo = Path(summary[vo_key]["path"])
    planned = [tuple(x) for x in summary.get("planned_windows") or []]
    tracks = list_library_bgm_tracks(ROOT)
    mixed = OUT_DIR / mixed_name
    duck = _expand_duck_windows(vo, planned)
    _mix_ducked(vo, tracks[0], mixed, planned)
    mixed_sil = silencedetect(mixed)
    vo_sil = silencedetect(vo)
    mixed_rep = {
        "path": str(mixed),
        "duration_s": round(float(_audio_file_duration_s(mixed)), 3),
        "duck_windows": [[round(a, 3), round(b, 3)] for a, b in duck],
        "interline_check": _overlap_ok(planned, mixed_sil),
        **mixed_rep_fields(mixed_sil),
    }
    summary["vo_concat"]["interline_check"] = _overlap_ok(planned, vo_sil)
    summary["interline_check"] = summary["vo_concat"]["interline_check"]
    summary["mixed_ducked"] = mixed_rep
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    return {
        "name": summary_path.name,
        "vo_interline_ok": [x["ok"] for x in summary["interline_check"]],
        "mixed_interline_ok": [x["ok"] for x in mixed_rep["interline_check"]],
        "mixed_durs": mixed_rep["durs"],
        "mixed_n": mixed_rep["n_gaps"],
        "vo_gap_pct": summary[vo_key].get("gap_pct"),
        "vo_wps": summary[vo_key].get("wps"),
        "duck_n": len(duck),
    }


def mixed_rep_fields(sil: dict) -> dict:
    return {k: sil[k] for k in ("starts", "ends", "durs", "longest_s", "total_gap_s", "n_gaps", "raw_tail")}


def main() -> int:
    tracks = list_library_bgm_tracks(ROOT)
    if not tracks:
        print("no BGM")
        return 1
    reports = []
    p10 = OUT_DIR / "pause_probe_summary.json"
    p9 = OUT_DIR / "hope9_vo_concat_summary.json"
    if p10.is_file():
        reports.append(rematch(p10, "vo_concat", "pause_probe_vo_plus_bgm_ducked.mp3"))
    if p9.is_file():
        reports.append(rematch(p9, "vo_concat", "hope9_vo_plus_bgm_ducked.mp3"))
    print(json.dumps(reports, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

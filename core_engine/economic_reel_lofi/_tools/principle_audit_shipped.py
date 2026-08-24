# -*- coding: utf-8 -*-
"""Diagnostic principle-voice audit of shipped scripts. No rewrites."""
from __future__ import annotations

import json
from pathlib import Path

from core_engine.economic_reel_lofi.script_agent import analyze_principle_voice

ROOT = Path(__file__).resolve().parents[3]
CLIPS = ROOT / "outputs" / "wonder_feed" / "clips"

SHIPPED = {
    "hope": "lofi_reel_hope_20260822_161048_v01.json",
    "distance": "lofi_reel_distance_20260822_172051_v01_regen.json",
    "grief": "lofi_reel_grief_20260823_000845_v01.json",
    "perseverance": "lofi_reel_perseverance_20260823_012131_v01.json",
}

# Hook + one insight + one extra beat that looks like scene-mood.
PICK = {
    "hope": (1, 4, 8),
    "distance": (1, 4, 8),
    "grief": (1, 4, 8),
    "perseverance": (1, 4, 8),
}


def _lines(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    script = data.get("script") if isinstance(data.get("script"), dict) else data
    return [r for r in (script.get("lines") or []) if isinstance(r, dict)]


def main() -> None:
    for theme, name in SHIPPED.items():
        path = CLIPS / name
        if not path.is_file():
            print(f"[audit] MISSING {theme} {name}")
            continue
        rows = _lines(path)
        want = set(PICK.get(theme) or (1, 4, 8))
        print(f"\n=== {theme} ({name}) ===")
        for row in rows:
            scene = int(row.get("scene") or 0)
            if scene not in want:
                continue
            text = str(row.get("text") or row.get("beat_text") or "")
            fn = str(row.get("beat_function") or "")
            rec = analyze_principle_voice(text)
            print(
                f"  scene={scene} fn={fn or '-'} status={rec['status']} "
                f"why={rec['reason']} line={text!r}"
            )


if __name__ == "__main__":
    main()

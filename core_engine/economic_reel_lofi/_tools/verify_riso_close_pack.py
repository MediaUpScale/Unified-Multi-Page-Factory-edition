# -*- coding: utf-8 -*-
"""Technical verify for the riso close pack."""
from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

from core_engine.economic_reel_lofi import config as lofi_cfg
from core_engine.economic_reel_lofi.script_agent import _narrative_gate_reasons

CLIPS = Path("outputs/wonder_feed/clips")
WANT_TAG = "style-riso_painting_retro_vintage/dev/riso_retro_flat_v2"
PACK = {
    "hope": None,  # already shipped — resolve latest
    "distance": None,
    "grief": None,
    "perseverance": None,
    "communication": None,
    "forgiveness": "lofi_reel_forgiveness_20260823_195041_v01",
    "loss": "lofi_reel_loss_20260823_200229_v02",
    "loneliness": "lofi_reel_loneliness_20260823_201708_v03",
    "regret": "lofi_reel_regret_20260823_204308_v01",
    "belonging": "lofi_reel_belonging_20260823_205924_v02",
}


def _latest(theme: str) -> str | None:
    cands = sorted(CLIPS.glob(f"lofi_reel_{theme}_*.json"), reverse=True)
    skip = {"_pulse_debug", "_regen"}
    for p in cands:
        name = p.stem
        if any(s in name for s in skip) and theme != "distance":
            continue
        return name
    return None


def main() -> None:
    any_fail = False
    for theme, stem in PACK.items():
        name = stem or _latest(theme)
        if not name:
            print(theme, "MISSING")
            any_fail = True
            continue
        meta_p = CLIPS / f"{name}.json"
        mp4 = CLIPS / f"{name}.mp4"
        if name.endswith("_v01") and theme == "distance":
            regen = CLIPS / "lofi_reel_distance_20260822_172051_v01_regen.mp4"
            if regen.is_file():
                mp4 = regen
        data = json.loads(meta_p.read_text(encoding="utf-8")) if meta_p.is_file() else {}
        script = data.get("script") or {}
        raw_holds = _narrative_gate_reasons(script, "relationship") if script else ["no script"]
        holds = [
            h
            for h in raw_holds
            if not str(h).startswith("cross-run phrase/anchor reuse")
        ]
        tag = data.get("still_style_tag") or ""
        wm = int(data.get("watermark_native_size") or 0)
        w = int(data.get("delivery_width") or 0)
        h = int(data.get("delivery_height") or 0)
        aspect = (w / h) if h else 0
        ok_mp4 = mp4.is_file() and mp4.stat().st_size > 1000
        style_ok = int(tag == WANT_TAG)
        tech = (
            ok_mp4
            and w == 1080
            and h == 1920
            and abs(aspect - 0.5625) < 1e-6
            and style_ok
            and wm == 1
            and not holds
        )
        if not tech:
            any_fail = True
        print(
            f"{theme:14} {'PASS' if tech else 'FAIL'} mp4={ok_mp4} "
            f"{w}x{h}={aspect:.4f} style_ok={style_ok} wm={wm} "
            f"holds={holds or 'none'} {mp4.name}"
        )
        imgs = data.get("scene_images") or []
        if imgs:
            im = Image.open(imgs[0])
            print(f"               still0={im.size} tag={lofi_cfg.still_style_tag_of(Path(imgs[0]))}")
    from core_engine.economic_reel_lofi import lofi_collections as rag

    rag.reset_batch_scripts()
    caps: dict[str, list[str]] = {}
    for theme, stem in PACK.items():
        name = stem or _latest(theme)
        if not name:
            continue
        meta_p = CLIPS / f"{name}.json"
        if not meta_p.is_file():
            continue
        data = json.loads(meta_p.read_text(encoding="utf-8"))
        script = data.get("script") or {}
        rag.note_batch_script(script, theme=theme)
        for row in script.get("lines") or []:
            norm = rag.normalize_caption_text(str(row.get("text") or ""))
            if len(norm.split()) >= 4:
                caps.setdefault(norm, []).append(theme)
    collisions = {k: v for k, v in caps.items() if len(set(v)) > 1}
    print("BATCH_DEDUP", collisions or "none")
    if collisions:
        any_fail = True
    print("PACK", "FAIL" if any_fail else "PASS")


if __name__ == "__main__":
    main()

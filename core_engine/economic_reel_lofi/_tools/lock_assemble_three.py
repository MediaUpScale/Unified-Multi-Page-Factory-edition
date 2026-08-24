# -*- coding: utf-8 -*-
"""Build assemble envelopes: reuse passing stills, patch failing visuals only."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
STORE = Path(__file__).resolve().parents[1] / "store"

APPROVED = {
    "distance": [
        "Distance never announces itself with one dramatic exit",
        "It starts small, a cushion left empty between us",
        "Then it's your coat staying on the far armrest",
        "Then it's my body angling toward the window instead",
        "Each small shift costs nothing, so we ignore it",
        "But the cushion never shrinks back on its own",
        "It just sits there, keeping the space we allowed",
        "So the gap wasn't sudden, it was paid daily",
        "By the time we noticed, the whole cushion was distance",
    ],
    "self_respect": [
        "Nobody tells you self respect isn't free at first",
        "I shut this door and my hand kept shaking",
        "It cost more than I had, that first time",
        "Later I closed it and my breath stayed even",
        "So the door didn't get lighter, I got steadier",
        "I used to think refusing them would feel like winning",
        "It felt like nothing, just a door, closed",
        "Now it stays shut without me guarding it",
        "I live here, door closed, and still breathe easy",
    ],
    "attachment": [
        "I used to watch the sidewalk for someone arriving.",
        "That waiting cost me hours I never got back.",
        "The sidewalk stayed empty more nights than not.",
        "So I stopped paying attention with my whole chest.",
        "I still looked, but I no longer bled for it.",
        "That's a smaller price for the same window view.",
        "Attachment held anyway, quieter, cheaper, still mine.",
        "The sidewalk is still empty, and I'm still here.",
        "It cost less this time, and it stayed longer.",
    ],
}

HOLDS = {
    "distance": (
        ROOT / "outputs/wonder_feed/clips/lofi_hold_distance_20260824_024004_v01.json"
    ),
    "self_respect": (
        ROOT
        / "outputs/wonder_feed/clips/lofi_hold_self_respect_20260824_034022_v02.json"
    ),
    "attachment": (
        ROOT
        / "outputs/wonder_feed/clips/lofi_reel_attachment_20260824_024502_v03.json"
    ),
}

# Scenes whose current stills already match the line / passed QA.
ACCEPT = {
    "distance": [1, 2, 3, 4, 5, 6, 7, 8, 9],
    "self_respect": [1, 2, 3, 4, 5, 6, 7, 8],
    "attachment": [1, 2, 3, 4, 5, 6, 7, 8, 9],
}

# Visual-only patches for scenes that must regenerate. Captions stay locked.
PATCHES = {
    "distance": {
    },
    "self_respect": {
        9: {
            "subject_type": "object_focus",
            "composition_type": "object_focus",
            "setting": "printed still-life, no room",
            "key_object": "closed door from inside",
            "time_of_day": "night",
            "anchor_beat": "",
            "pose_hint": "",
            "visual_anchor_hint": (
                "shut wooden door slab filling the frame, latch closed, "
                "no opening, no hallway, no sunset gap"
            ),
        },
    },
    "attachment": {},
}


def _rel(path: str) -> str:
    p = Path(path)
    try:
        return str(p.relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        return str(p).replace("\\", "/")


def _restore_captions(script: dict, captions: list[str]) -> None:
    lines = [r for r in (script.get("lines") or []) if isinstance(r, dict)]
    for i, text in enumerate(captions):
        if i >= len(lines):
            break
        lines[i]["text"] = text
        lines[i]["beat_text"] = text
    script["lines"] = lines
    script["monologue"] = " ".join(captions)
    script["source_monologue"] = " ".join(captions)


def _clear_gen(row: dict) -> None:
    row.pop("visual_prompt", None)
    row.pop("dev_scene_builder", None)
    gate = dict(row.get("default_object_gate") or {})
    gate["image_ok"] = False
    gate["passed"] = False
    gate["reused"] = False
    row["default_object_gate"] = gate


def build_one(theme: str) -> Path:
    hold = json.loads(HOLDS[theme].read_text(encoding="utf-8"))
    script = hold.get("script") or {}
    captions = APPROVED[theme]
    _restore_captions(script, captions)
    lines = [r for r in (script.get("lines") or []) if isinstance(r, dict)]
    for scene, patch in (PATCHES.get(theme) or {}).items():
        row = lines[scene - 1]
        row.update(patch)
        if not row.get("anchor_beat"):
            row.pop("anchor_beat", None)
        _clear_gen(row)
    _restore_captions(script, captions)
    got = [str(r.get("text") or "") for r in lines]
    if got != captions:
        raise SystemExit(f"{theme} captions drifted: {got}")
    images = [_rel(p) for p in (hold.get("scene_images") or [])]
    work = hold.get("work_dir") or ""
    envelope = {
        "manual_accept_scenes": list(ACCEPT[theme]),
        "work_dir": _rel(work) if work else "",
        "scene_images": images,
        "script": script,
    }
    out = STORE / f"locked_script_{theme}_v1_assemble.json"
    out.write_text(json.dumps(envelope, indent=2, ensure_ascii=False), encoding="utf-8")
    print(
        f"[assemble] {theme} accept={ACCEPT[theme]} "
        f"regen={sorted(PATCHES.get(theme) or {})} -> {out}"
    )
    return out


def main() -> None:
    build_one("self_respect")


if __name__ == "__main__":
    main()

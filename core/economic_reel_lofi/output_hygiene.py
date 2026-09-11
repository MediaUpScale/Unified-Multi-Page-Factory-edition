# -*- coding: utf-8 -*-
"""Keep LOFI deliverables, metadata, and scratch in separate folders."""
from __future__ import annotations

import shutil
from pathlib import Path

_CHANNEL_SLUGS = ("wonder_feed", "momma_circle")
_ROOT_TEST_PREFIXES = ("_api_test_", "_tmp_", "test_")
_ROOT_TEST_NAMES = frozenset(
    {
        "audio_preview_test.mp3",
        "test_music_track.mp3",
        "test_tts_clean.mp3",
        "test_sfx_braam.mp3",
    }
)
_JSON_PREFIXES = (
    "lofi_batch_",
    "lofi_pipeline_",
    "lofi_script_",
    "lofi_stills_",
    "lofi_hold_",
    "lofi_manual_review_",
    "riso_library_live_",
)
_JSON_SUFFIXES = ("_pulse_debug.json",)


def is_root_test_artifact(name: str) -> bool:
    low = name.lower()
    if name in _ROOT_TEST_NAMES or low in _ROOT_TEST_NAMES:
        return True
    return any(low.startswith(p) for p in _ROOT_TEST_PREFIXES)


def is_lofi_metadata_json(name: str) -> bool:
    if not name.lower().endswith(".json"):
        return False
    if any(name.startswith(p) for p in _JSON_PREFIXES):
        return True
    if any(name.endswith(s) for s in _JSON_SUFFIXES):
        return True
    return name.startswith("lofi_reel_") and name.endswith(".json")


def _move(src: Path, dest_dir: Path) -> Path | None:
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / src.name
    if dest.exists() and dest.resolve() != src.resolve():
        dest = dest_dir / f"{src.stem}__migrated{src.suffix}"
    shutil.move(str(src), str(dest))
    return dest


def migrate_outputs_root(outputs_root: Path) -> dict[str, list[str]]:
    """Move loose test probes into ``outputs/_tests/``."""
    root = Path(outputs_root)
    tests_dir = root / "_tests"
    moved: list[str] = []
    if not root.is_dir():
        return {"tests": moved}
    for path in sorted(root.iterdir()):
        if not path.is_file():
            continue
        if is_root_test_artifact(path.name):
            dest = _move(path, tests_dir)
            if dest is not None:
                moved.append(dest.name)
    return {"tests": moved}


def migrate_channel_clips(page_outputs: Path) -> dict[str, list[str]]:
    """JSON → metadata/, delete orphan vo_concat from clips/."""
    clips = Path(page_outputs) / "clips"
    metadata = Path(page_outputs) / "metadata"
    moved: list[str] = []
    deleted: list[str] = []
    if not clips.is_dir():
        return {"moved": moved, "deleted": deleted}
    metadata.mkdir(parents=True, exist_ok=True)
    for path in sorted(clips.iterdir()):
        if not path.is_file():
            continue
        name = path.name
        if is_lofi_metadata_json(name):
            dest = _move(path, metadata)
            if dest is not None:
                moved.append(dest.name)
            continue
        if "_vo_concat" in name.lower() or name.lower().startswith("temp_"):
            path.unlink()
            deleted.append(name)
    return {"moved": moved, "deleted": deleted}


def enforce_clips_mp4_only(clips_dir: Path) -> list[str]:
    """Return leftover non-mp4 filenames still sitting in clips/."""
    leftover: list[str] = []
    root = Path(clips_dir)
    if not root.is_dir():
        return leftover
    for path in root.iterdir():
        if path.is_file() and path.suffix.lower() != ".mp4":
            leftover.append(path.name)
    return leftover


def migrate_factory_outputs(outputs_root: Path) -> dict[str, object]:
    root = Path(outputs_root)
    report: dict[str, object] = {"root": str(root)}
    report["root_tests"] = migrate_outputs_root(root)
    channels: dict[str, object] = {}
    for slug in _CHANNEL_SLUGS:
        page = root / slug
        if page.is_dir():
            channels[slug] = migrate_channel_clips(page)
    report["channels"] = channels
    return report

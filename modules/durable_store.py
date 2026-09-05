# -*- coding: utf-8 -*-
"""C: primary / G: mirror durable channel state.

Every channel keeps permanent system state under
``channels_config/<channel>/store/`` (C:). Writes are mirrored to the
channel's factory outputs folder on ``OUTPUT_PATH`` (G: when configured).

If a local store file is missing, :func:`restore_channel_state` copies it
back from the G: tree before scheduling or deduplication runs.
"""
from __future__ import annotations

import json
import logging
import shutil
import threading
from pathlib import Path
from typing import Any, Iterable

from utils.pipeline_paths import (
    STORE_DIRNAME,
    channel_mirror_dir,
    channel_mirror_store_dir,
    channel_slug,
    channel_store_dir,
    channels_config_root,
    discover_channel_ids,
    is_protected_state_path,
    outputs_root,
    page_outputs_dir,
    safe_rmtree,
)

_LOG = logging.getLogger(__name__)
_RESTORE_LOCK = threading.Lock()
_RESTORED: set[str] = set()

# Files that historically lived at ``{OUTPUT_PATH}/{channel}/<name>``.
# They are stored locally under ``store/`` and mirrored to both
# ``{OUTPUT_PATH}/{channel}/store/<name>`` and the legacy root location.
LEGACY_ROOT_STATE_FILES = frozenset({
    "content_library.json",
    "asset_library.json",
    "session_hooks_cache.json",
    "facebook_history.json",
    "master_inventory.json",
    "scheduled_history.txt",
    "pinterest_history.json",
    "wealth_publish_state.json",
    "wealth_asset_map.json",
})

# Extra G: folders that may hold older mirrors of store-only artifacts.
LEGACY_MIRROR_SUBDIRS = frozenset({"transcripts", "metrics"})

STATE_SUBDIRS = frozenset({"transcripts", "metrics", "library"})


def reset_restore_cache() -> None:
    """Forget per-process restore flags (tests)."""
    with _RESTORE_LOCK:
        _RESTORED.clear()


def _rel_to(path: Path, root: Path) -> Path | None:
    try:
        return path.resolve().relative_to(root.resolve())
    except (ValueError, OSError):
        return None


def classify_state_path(path: Path | str) -> tuple[str, str, Path] | None:
    """Map *path* to ``(channel, side, relative)`` or ``None``.

    *side* is ``local``, ``mirror_store``, or ``mirror_root``.
    """
    raw = Path(path)
    rel = _rel_to(raw, channels_config_root())
    if rel is not None and len(rel.parts) >= 2 and rel.parts[1].lower() == STORE_DIRNAME:
        channel = channel_slug(rel.parts[0])
        inner = Path(*rel.parts[2:]) if len(rel.parts) > 2 else Path()
        return channel, "local", inner

    out_rel = _rel_to(raw, outputs_root())
    if out_rel is None or not out_rel.parts:
        return None
    channel = channel_slug(out_rel.parts[0])
    if len(out_rel.parts) >= 2 and out_rel.parts[1].lower() == STORE_DIRNAME:
        inner = Path(*out_rel.parts[2:]) if len(out_rel.parts) > 2 else Path()
        return channel, "mirror_store", inner
    if len(out_rel.parts) == 2 and out_rel.name in LEGACY_ROOT_STATE_FILES:
        return channel, "mirror_root", Path(out_rel.name)
    return None


def local_state_path(channel: str, relative: str | Path, *, create_parent: bool = False) -> Path:
    path = channel_store_dir(channel) / Path(relative)
    if create_parent:
        path.parent.mkdir(parents=True, exist_ok=True)
    return path


def peer_paths(path: Path | str) -> list[Path]:
    """Locations that should stay in sync with *path* (excluding *path* itself)."""
    classified = classify_state_path(path)
    if classified is None:
        return []
    channel, _side, relative = classified
    resolved = Path(path)
    try:
        resolved = resolved.resolve()
    except OSError:
        pass
    candidates = [
        channel_store_dir(channel) / relative,
        channel_mirror_store_dir(channel) / relative,
    ]
    if relative.as_posix() in LEGACY_ROOT_STATE_FILES or (
        len(relative.parts) == 1 and relative.name in LEGACY_ROOT_STATE_FILES
    ):
        candidates.append(page_outputs_dir(channel) / relative.name)
    peers: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        try:
            key = candidate.resolve()
        except OSError:
            key = candidate
        if key == resolved or key in seen:
            continue
        seen.add(key)
        peers.append(candidate)
    return peers


def _usable_file(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def atomic_copy(src: Path, dest: Path) -> bool:
    """Copy *src* onto *dest* via a sibling ``.tmp`` file. Returns success."""
    if not src.is_file():
        return False
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".tmp")
        shutil.copy2(src, tmp)
        tmp.replace(dest)
        return True
    except OSError as exc:
        _LOG.warning("durable_store copy failed %s -> %s (%s)", src, dest, exc)
        try:
            leftover = dest.with_name(dest.name + ".tmp")
            if leftover.exists():
                leftover.unlink()
        except OSError:
            pass
        return False


def sync_state_file(path: Path | str) -> list[Path]:
    """After a local-or-mirror write, copy *path* to every peer."""
    src = Path(path)
    if not src.is_file():
        return []
    mirrored: list[Path] = []
    for peer in peer_paths(src):
        if atomic_copy(src, peer):
            mirrored.append(peer)
    if mirrored:
        _LOG.debug("durable_store mirrored %s -> %s", src, mirrored)
    return mirrored


def hydrate_state_file(path: Path | str) -> Path:
    """If *path* is missing, restore it from a peer (G: or C:)."""
    dest = Path(path)
    if _usable_file(dest):
        return dest
    for peer in peer_paths(dest):
        if _usable_file(peer) and atomic_copy(peer, dest):
            _LOG.info("durable_store restored %s from %s", dest, peer)
            return dest
    return dest


def _iter_files(root: Path) -> Iterable[Path]:
    if not root.is_dir():
        return
    for item in root.rglob("*"):
        if item.is_file() and not item.name.endswith(".tmp"):
            yield item


def restore_channel_state(channel: str, *, force: bool = False) -> dict[str, int]:
    """Copy missing C: store files from the G: channel outputs tree.

    Safe to call repeatedly; each channel is restored at most once per process
    unless *force* is set.
    """
    slug = channel_slug(channel)
    with _RESTORE_LOCK:
        if not force and slug in _RESTORED:
            return {"restored": 0, "skipped": 0}
        _RESTORED.add(slug)

    local_root = channel_store_dir(slug, create=True)
    mirror_root = channel_mirror_dir(slug)
    mirror_store = channel_mirror_store_dir(slug)
    restored = 0
    skipped = 0

    planned: list[tuple[Path, Path]] = []

    if mirror_store.is_dir():
        for src in _iter_files(mirror_store):
            dest = local_root / src.relative_to(mirror_store)
            planned.append((src, dest))

    for name in LEGACY_ROOT_STATE_FILES:
        planned.append((mirror_root / name, local_root / name))

    for subdir in LEGACY_MIRROR_SUBDIRS:
        legacy_dir = mirror_root / subdir
        if not legacy_dir.is_dir():
            continue
        for src in _iter_files(legacy_dir):
            dest = local_root / subdir / src.relative_to(legacy_dir)
            planned.append((src, dest))

    seen_dest: set[Path] = set()
    for src, dest in planned:
        try:
            dest_key = dest.resolve()
        except OSError:
            dest_key = dest
        if dest_key in seen_dest:
            continue
        seen_dest.add(dest_key)
        if _usable_file(dest):
            skipped += 1
            continue
        if _usable_file(src) and atomic_copy(src, dest):
            restored += 1
            _LOG.info("durable_store restored %s/%s from %s", slug, dest.relative_to(local_root), src)

    if restored:
        _LOG.info("durable_store | %s | restored %d file(s) from G: mirror", slug, restored)
    return {"restored": restored, "skipped": skipped}


def restore_all_channels_state(*, force: bool = False) -> dict[str, dict[str, int]]:
    """Restore every channel discovered under ``channels_config/``."""
    results: dict[str, dict[str, int]] = {}
    for channel in discover_channel_ids():
        results[channel] = restore_channel_state(channel, force=force)
    return results


def write_state_json(channel: str, relative: str | Path, payload: Any) -> Path:
    """Atomic JSON write to C: store, then mirror to G:."""
    dest = local_state_path(channel, relative, create_parent=True)
    tmp = dest.with_name(dest.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(dest)
    sync_state_file(dest)
    return dest


def read_state_json(channel: str, relative: str | Path, default: Any = None) -> Any:
    """Read JSON from C: store, restoring from G: first when missing."""
    restore_channel_state(channel)
    path = hydrate_state_file(local_state_path(channel, relative))
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _LOG.warning("durable_store read failed %s (%s)", path, exc)
        return default


def ensure_channel_state(channel: str) -> Path:
    """Create the local store and restore any missing files from G:."""
    restore_channel_state(channel)
    return channel_store_dir(channel, create=True)


__all__ = [
    "LEGACY_MIRROR_SUBDIRS",
    "LEGACY_ROOT_STATE_FILES",
    "STATE_SUBDIRS",
    "atomic_copy",
    "channel_mirror_dir",
    "channel_mirror_store_dir",
    "channel_store_dir",
    "classify_state_path",
    "discover_channel_ids",
    "ensure_channel_state",
    "hydrate_state_file",
    "is_protected_state_path",
    "local_state_path",
    "peer_paths",
    "read_state_json",
    "reset_restore_cache",
    "restore_all_channels_state",
    "restore_channel_state",
    "safe_rmtree",
    "sync_state_file",
    "write_state_json",
]

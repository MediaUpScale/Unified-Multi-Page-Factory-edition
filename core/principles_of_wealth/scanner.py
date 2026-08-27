# -*- coding: utf-8 -*-
"""Source-folder scanner for Principles of Wealth.

Each episode owns a **list** of Shorts (up to 10), not a single file.

Matching rules
--------------
* Long-form: ``Ray Dalio epN.mp4``
* Episode 1 Shorts: ``Short1 -`` … ``Short10 -`` (optional space: ``Short 4 -``)
* Episodes 2+: ``Ep{n}.1`` … ``Ep{n}.10`` (dash or dot after the clip index)
* Thumbnails: ``ThumbN`` / ``thumbN``
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

from core.principles_of_wealth.catalog import (
    EPISODES,
    resolve_source_directory,
)

_VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".m4v", ".webm"}
_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
_SKIP_DIR_NAMES = {"processed", "tmp", "temp", ".tmp"}
_MAX_SHORTS_PER_EPISODE = 10

_LONG_EP_RE = re.compile(
    r"ray\s*dalio\s+ep(?:isode)?\s*0*(?P<n>\d+)\b",
    re.IGNORECASE,
)
# Episode 1: Short1 - … Short10 -  (also Short 4 - / Short 6-)
_EP1_SHORT_RE = re.compile(
    r"^short\s*0*(?P<clip>\d+)\s*[-–—.]",
    re.IGNORECASE,
)
# Episodes 2+: Ep2.1 - … Ep2.10 / Ep4.2. Title
_EPN_SHORT_RE = re.compile(
    r"^ep\s*(?P<ep>\d+)\.(?P<clip>\d+)\b",
    re.IGNORECASE,
)
_THUMB_EP_RE = re.compile(
    r"\bthumb(?:nail)?\s*-?\s*0*(?P<n>\d+)\b",
    re.IGNORECASE,
)
_SHORT_HOOK_RE = re.compile(
    r"^(?:short\s*\d+|ep\s*\d+\.\d+)\s*[-–—.]?\s*",
    re.IGNORECASE,
)


@dataclass
class AssetMatch:
    episode: int
    long_path: str = ""
    shorts: list[str] = field(default_factory=list)
    thumbnail_path: str = ""
    long_bytes: int = 0
    thumbnail_bytes: int = 0

    @property
    def has_long(self) -> bool:
        return bool(self.long_path) and self.long_bytes > 0

    @property
    def has_short(self) -> bool:
        return bool(self.shorts)

    @property
    def short_count(self) -> int:
        return len(self.shorts)

    @property
    def has_thumbnail(self) -> bool:
        return bool(self.thumbnail_path) and self.thumbnail_bytes > 0


@dataclass
class ScanResult:
    source_dir: str
    matches: dict[int, AssetMatch] = field(default_factory=dict)
    unmatched: list[str] = field(default_factory=list)
    skipped_empty: list[str] = field(default_factory=list)
    missing_episodes: list[int] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_dir": self.source_dir,
            "matches": {
                str(k): asdict(v) for k, v in sorted(self.matches.items())
            },
            "unmatched": self.unmatched,
            "skipped_empty": self.skipped_empty,
            "missing_episodes": self.missing_episodes,
        }


def clip_index_from_path(path: str | Path) -> int:
    """Clip slot 1-10 recovered from ``ShortN`` / ``EpN.M`` in the filename."""
    classified = _classify_short(Path(path).name)
    return classified[1] if classified else 0


def short_hook_from_path(path: str | Path, fallback: str = "") -> str:
    """Title text after ``ShortN -`` / ``EpN.M -`` in the filename."""
    stem = Path(path).stem
    if stem.lower().endswith("_signed"):
        stem = stem[:-7]
    hook = _SHORT_HOOK_RE.sub("", stem).strip(" .-_|")
    return hook or fallback


def _iter_source_files(source_dir: Path) -> Iterable[Path]:
    if not source_dir.is_dir():
        return []
    for path in source_dir.rglob("*"):
        if not path.is_file():
            continue
        if any(part.lower() in _SKIP_DIR_NAMES for part in path.parts):
            continue
        yield path


def _prefer_better(existing: str, existing_bytes: int, candidate: Path) -> bool:
    if not existing:
        return True
    size = candidate.stat().st_size
    if size <= 0:
        return False
    if re.search(r"-\s*[A-Za-z]\b", candidate.stem) and not re.search(
        r"-\s*[A-Za-z]\b", Path(existing).stem
    ):
        return False
    return size > existing_bytes


def _classify_short(name: str) -> Optional[tuple[int, int]]:
    """Return ``(episode, clip_index 1-10)`` or None."""
    ep1 = _EP1_SHORT_RE.match(name)
    if ep1:
        clip = int(ep1.group("clip"))
        if 1 <= clip <= _MAX_SHORTS_PER_EPISODE:
            return 1, clip
        return None
    epn = _EPN_SHORT_RE.match(name)
    if epn:
        ep = int(epn.group("ep"))
        clip = int(epn.group("clip"))
        if ep >= 2 and 1 <= clip <= _MAX_SHORTS_PER_EPISODE:
            return ep, clip
    return None


def scan_source_directory(source_dir: Optional[str | Path] = None) -> ScanResult:
    root = resolve_source_directory(source_dir)
    result = ScanResult(source_dir=str(root))
    if not root.is_dir():
        result.unmatched.append(f"SOURCE_DIR_MISSING: {root}")
        result.missing_episodes = [spec.episode for spec in EPISODES]
        return result

    matches: dict[int, AssetMatch] = {
        spec.episode: AssetMatch(episode=spec.episode) for spec in EPISODES
    }
    # clip_index -> (path, bytes) per episode, frozen to a sorted list at the end
    shorts_map: dict[int, dict[int, tuple[str, int]]] = {
        spec.episode: {} for spec in EPISODES
    }

    for path in _iter_source_files(root):
        suffix = path.suffix.lower()
        name = path.name
        size = path.stat().st_size
        if size <= 0:
            result.skipped_empty.append(str(path))
            continue

        assigned = False
        if suffix in _VIDEO_EXTS:
            short_key = _classify_short(name)
            long_m = None
            if short_key is None and "short" not in name.lower():
                long_m = _LONG_EP_RE.search(name)
            if short_key is not None:
                ep, clip = short_key
                if ep in shorts_map:
                    prev = shorts_map[ep].get(clip)
                    existing = prev[0] if prev else ""
                    existing_bytes = prev[1] if prev else 0
                    if _prefer_better(existing, existing_bytes, path):
                        shorts_map[ep][clip] = (str(path), size)
                    assigned = True
            elif long_m:
                n = int(long_m.group("n"))
                if n in matches and _prefer_better(
                    matches[n].long_path, matches[n].long_bytes, path
                ):
                    matches[n].long_path = str(path)
                    matches[n].long_bytes = size
                    assigned = True
        elif suffix in _IMAGE_EXTS:
            thumb_m = _THUMB_EP_RE.search(name)
            if thumb_m:
                n = int(thumb_m.group("n"))
                if n in matches and _prefer_better(
                    matches[n].thumbnail_path, matches[n].thumbnail_bytes, path
                ):
                    matches[n].thumbnail_path = str(path)
                    matches[n].thumbnail_bytes = size
                    assigned = True

        if not assigned:
            result.unmatched.append(str(path))

    for ep, by_clip in shorts_map.items():
        matches[ep].shorts = [by_clip[c][0] for c in sorted(by_clip)]

    result.matches = {
        n: m
        for n, m in matches.items()
        if m.has_long or m.has_short or m.has_thumbnail
    }
    result.missing_episodes = [
        spec.episode
        for spec in EPISODES
        if spec.episode not in result.matches or not result.matches[spec.episode].has_long
    ]
    return result


def write_scan_snapshot(scan: ScanResult, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(
        json.dumps(scan.to_dict(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return dest

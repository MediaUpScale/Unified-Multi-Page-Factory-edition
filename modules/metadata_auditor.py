# -*- coding: utf-8 -*-
"""Channel-agnostic metadata auditor / backfill.

Restores ``channels_config/<channel>/store/`` from the G: outputs tree,
scans rendered MP4s, and upserts ``content_library.json`` +
``asset_library.json`` through the durable dual-write path.

Aiwake keeps a debate-transcript builder in
``channels_config.aiwake.tools.backfill_metadata``. Every other channel
gets a lean filename-based catalog row.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from modules.asset_library import register_generated_asset
from modules.distribution_contract import (
    TITLE_MAX_CHARS,
    BaseMetadata,
    DistributionRecord,
    build_platform_overrides,
    clip_text,
    content_library_path,
    load_distribution_library,
    posting_status_from_map,
    upsert_distribution_row,
)
from modules.durable_store import restore_channel_state
from utils.pipeline_paths import channel_store_dir, discover_channel_ids, page_outputs_dir

_LOG = logging.getLogger("metadata_auditor")

_SKIP_DIR_NAMES = frozenset({
    "tmp", "temp", "scratch", "__pycache__", ".git", "needs_metadata",
    "reproved", "tests", "archive", "posted_facebook",
})
_MIN_VIDEO_BYTES = 50_000
_STEM_CLEAN_RE = re.compile(r"[_\-]+")

CHANNEL_PROFILES: dict[str, dict[str, Any]] = {
    "aiwake": {
        "post_type": "AIWAKE_REEL",
        "hashtags": (
            "#aiwake",
            "#ai",
            "#aiconsciousness",
            "#futuretech",
            "#artificialintelligence",
            "#tech",
        ),
        "board": "AI Consciousness & Tech",
        "youtube_category_id": "28",
        "specialized": True,
    },
    "ancient_knowledge": {
        "post_type": "SEQUENCE_REEL",
        "hashtags": ("#ancientknowledge", "#history", "#mysteries"),
        "board": "Ancient Knowledge",
        "youtube_category_id": "27",
    },
    "anna_protocol": {
        "post_type": "IMAGE_AVATAR",
        "hashtags": ("#annaprotocol",),
        "board": "Anna Protocol",
        "youtube_category_id": "26",
    },
    "down_dirty": {
        "post_type": "IMAGE_AVATAR",
        "hashtags": ("#downdirty",),
        "board": "Down & Dirty",
        "youtube_category_id": "26",
    },
    "endless_summer_paradise": {
        "post_type": "REFERENCE_BASED_REELS",
        "hashtags": ("#endlesssummer", "#paradise"),
        "board": "Endless Summer Paradise",
        "youtube_category_id": "22",
    },
    "master_mei": {
        "post_type": "IMAGE_AVATAR",
        "hashtags": ("#mastermei",),
        "board": "Master Mei",
        "youtube_category_id": "26",
    },
    "momma_circle": {
        "post_type": "REFERENCE_BASED_REELS",
        "hashtags": ("#mommacircle",),
        "board": "Momma Circle",
        "youtube_category_id": "26",
    },
    "principles_of_wealth_finance_economics": {
        "post_type": "WEALTH_REEL",
        "hashtags": ("#principles", "#wealth", "#economics"),
        "board": "Principles of Wealth",
        "youtube_category_id": "27",
    },
    "wonder_feed": {
        "post_type": "DYNAMIC_REEL",
        "hashtags": ("#wonderfeed",),
        "board": "Wonder Feed",
        "youtube_category_id": "27",
    },
}

_DEFAULT_PROFILE: dict[str, Any] = {
    "post_type": "REEL",
    "hashtags": (),
    "board": "",
    "youtube_category_id": "22",
    "specialized": False,
}


@dataclass(slots=True)
class AuditItem:
    video_path: Path
    record: DistributionRecord | None = None
    asset_id: str = ""
    status: str = "pending"
    detail: str = ""
    extras: dict[str, Any] = field(default_factory=dict)


def channel_profile(channel: str) -> dict[str, Any]:
    slug = (channel or "").strip().lower()
    profile = dict(_DEFAULT_PROFILE)
    profile.update(CHANNEL_PROFILES.get(slug, {}))
    profile["channel"] = slug
    return profile


def scan_videos(outputs_dir: Path) -> list[Path]:
    if not outputs_dir.is_dir():
        return []
    found: list[Path] = []
    for path in outputs_dir.rglob("*.mp4"):
        if not path.is_file():
            continue
        if any(part.lower() in _SKIP_DIR_NAMES for part in path.parts):
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size < _MIN_VIDEO_BYTES:
            _LOG.info("skip tiny file %s (%s bytes)", path.name, size)
            continue
        found.append(path)
    return sorted(found)


def title_from_filename(path: Path) -> str:
    stem = _STEM_CLEAN_RE.sub(" ", path.stem).strip()
    return clip_text(stem or path.name, TITLE_MAX_CHARS)


def _row_video_key(row: dict[str, Any]) -> str:
    return str(row.get("video_path") or "").replace("\\", "/").rstrip("/").lower()


def already_cataloged(rows: list[dict[str, Any]], video: Path) -> bool:
    name = video.name.lower()
    key = str(video).replace("\\", "/").rstrip("/").lower()
    for row in rows:
        stored = _row_video_key(row)
        if stored == key or Path(stored).name.lower() == name:
            return True
    return False


def build_generic_record(channel: str, video: Path) -> DistributionRecord:
    profile = channel_profile(channel)
    hashtags = list(profile.get("hashtags") or ())
    title = title_from_filename(video)
    caption = title
    if hashtags:
        caption = f"{caption}\n\n{' '.join(hashtags)}"
    return DistributionRecord(
        channel_id=channel,
        post_type=str(profile.get("post_type") or "REEL"),
        session_id=video.stem,
        topic=title,
        video_path=str(video),
        final_caption=caption,
        base_metadata=BaseMetadata(
            title=title,
            caption=caption,
            hashtags=hashtags,
            search_tags=[title.lower()],
            hooks=[title],
        ),
        platform_overrides=build_platform_overrides(
            x_caption=clip_text(title, 280),
            board_name=str(profile.get("board") or title),
            youtube_category_id=str(profile.get("youtube_category_id") or "22"),
            is_short=True,
        ),
        posting_status=posting_status_from_map(None),
    )


def persist_generic_record(
    record: DistributionRecord,
    *,
    library_path: Path,
    dry_run: bool = False,
) -> str:
    if dry_run:
        return ""
    asset = register_generated_asset(
        channel=record.channel_id,
        post_type=record.post_type,
        local_path=record.video_path,
        prompt=record.topic,
        caption=record.base_metadata.caption,
        hashtags=record.base_metadata.hashtags,
        platform="facebook",
        video_path=record.video_path,
        asset_kind="video",
        increment_usage=False,
    )
    asset_id = str((asset or {}).get("asset_id") or "")
    if asset_id:
        record.asset_id = asset_id
    upsert_distribution_row(library_path, record)
    return asset_id


def run_generic_backfill(
    channel: str,
    *,
    outputs_dir: Path | None = None,
    dry_run: bool = False,
    restore: bool = True,
) -> list[AuditItem]:
    slug = (channel or "").strip().lower()
    if restore:
        restore_channel_state(slug)
    media_root = Path(outputs_dir) if outputs_dir else page_outputs_dir(slug)
    library = content_library_path(slug) if outputs_dir is None else content_library_path(slug, outputs_dir=media_root)
    existing = load_distribution_library(library)
    items: list[AuditItem] = []
    for video in scan_videos(media_root):
        item = AuditItem(video_path=video)
        if already_cataloged(existing, video):
            item.status = "exists"
            item.detail = "already in content_library"
            items.append(item)
            continue
        try:
            record = build_generic_record(slug, video)
            asset_id = persist_generic_record(record, library_path=library, dry_run=dry_run)
            item.record = record
            item.asset_id = asset_id
            item.status = "matched"
            item.detail = "dry-run" if dry_run else "ready"
            if not dry_run:
                existing.append(record.to_library_row())
        except Exception as exc:  # noqa: BLE001 — keep the batch moving
            _LOG.exception("generic backfill failed for %s", video.name)
            item.status = "error"
            item.detail = str(exc)[:200]
        items.append(item)
    return items


def _run_aiwake_backfill(
    *,
    outputs_dir: Path | None,
    dry_run: bool,
    restore: bool,
) -> list[AuditItem]:
    from channels_config.aiwake.tools.backfill_metadata import run_backfill

    if restore:
        restore_channel_state("aiwake")
    raw = run_backfill(outputs_dir=outputs_dir, dry_run=dry_run)
    items: list[AuditItem] = []
    for row in raw:
        items.append(
            AuditItem(
                video_path=row.video_path,
                record=row.record,
                asset_id=row.asset_id,
                status=row.status,
                detail=row.detail,
            )
        )
    return items


def run_channel_backfill(
    channel: str,
    *,
    outputs_dir: Path | None = None,
    dry_run: bool = False,
    restore: bool = True,
    specialized: Callable[..., list[AuditItem]] | None = None,
) -> list[AuditItem]:
    """Restore state, then run the specialized or generic auditor."""
    slug = (channel or "").strip().lower()
    if restore:
        restore_channel_state(slug)
    profile = channel_profile(slug)
    if specialized is not None:
        return specialized(outputs_dir=outputs_dir, dry_run=dry_run, restore=False)
    if profile.get("specialized") and slug == "aiwake":
        return _run_aiwake_backfill(
            outputs_dir=outputs_dir,
            dry_run=dry_run,
            restore=False,
        )
    return run_generic_backfill(
        slug,
        outputs_dir=outputs_dir,
        dry_run=dry_run,
        restore=False,
    )


def run_all_channels_backfill(
    *,
    dry_run: bool = False,
    restore: bool = True,
) -> dict[str, list[AuditItem]]:
    results: dict[str, list[AuditItem]] = {}
    for channel in discover_channel_ids():
        results[channel] = run_channel_backfill(
            channel,
            dry_run=dry_run,
            restore=restore,
        )
    return results


def print_audit_report(
    channel: str,
    items: list[AuditItem],
    *,
    dry_run: bool,
) -> None:
    matched = [item for item in items if item.status == "matched"]
    exists = [item for item in items if item.status == "exists"]
    orphans = [item for item in items if item.status == "orphan"]
    errors = [item for item in items if item.status == "error"]
    print()
    print(f"{channel} metadata audit")
    print(f"  store       : {channel_store_dir(channel)}")
    print(f"  library     : {content_library_path(channel)}")
    print(f"  mode        : {'DRY-RUN (no writes)' if dry_run else 'write'}")
    print(f"  videos      : {len(items)}")
    print(f"  matched     : {len(matched)}")
    print(f"  exists      : {len(exists)}")
    print(f"  orphan      : {len(orphans)}")
    print(f"  errors      : {len(errors)}")
    for item in items:
        if item.status in {"matched", "error", "orphan"}:
            print(f"  [{item.status}] {item.video_path.name} — {item.detail}")
    print()

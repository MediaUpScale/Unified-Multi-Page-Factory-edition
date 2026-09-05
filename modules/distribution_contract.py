# -*- coding: utf-8 -*-
"""Universal multi-platform distribution contract.

Prototype shared by every channel: one base copy pack, optional per-network
overrides, and a posting-status ledger. Channel tools build a
:class:`DistributionRecord` and persist it as a row in
``channels_config/{channel}/store/content_library.json`` (mirrored to G:).

This module does not import channel packages. Existing lean library loaders
keep working because records stay a JSON **array** and still carry
``topic`` / ``final_caption`` / ``video_path`` / ``timestamp``.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator

SCHEMA_VERSION = "1.0"

TITLE_MAX_CHARS = 100
X_CAPTION_MAX_CHARS = 280
YOUTUBE_TITLE_MAX_CHARS = 100
YOUTUBE_CATEGORY_SCIENCE_TECH = "28"
SHORTS_TAG = "#Shorts"
SHORTS_TITLE_SUFFIX = " #Shorts"

# Peak US engagement: 18:00 America/New_York (EDT → 22:00 UTC, EST → 23:00 UTC).
US_PEAK_TZ = ZoneInfo("America/New_York")
US_PEAK_HOUR = 18
US_PEAK_MINUTE = 0
US_PEAK_SLOT_INTERVAL = timedelta(days=1)
UTC_Z_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

HIGH_RPM_HASHTAGS: tuple[str, ...] = (
    "#futuretech",
    "#artificialintelligence",
    "#tech",
)
HIGH_RPM_SEARCH_TAGS: tuple[str, ...] = (
    "futuretech",
    "artificialintelligence",
    "tech",
)

DEFAULT_HASHTAGS: tuple[str, ...] = (
    "#aiwake",
    "#ai",
    "#aiconsciousness",
    "#futuretech",
    "#artificialintelligence",
    "#tech",
)

DISTRIBUTION_PLATFORMS: tuple[str, ...] = (
    "youtube",
    "instagram",
    "tiktok",
    "facebook",
    "x",
    "pinterest",
    "kwai",
)

_WORD_CUT_RE = re.compile(r"\s+")


class PostingState(str, Enum):
    """Lifecycle of one network slot. Publishers own transitions off pending."""

    PENDING = "pending"
    SCHEDULED = "scheduled"
    POSTED = "posted"
    FAILED = "failed"
    SKIPPED = "skipped"


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def first_us_peak_slot(*, now: datetime | None = None) -> datetime:
    """Tomorrow at 18:00 America/New_York (15:00 PT)."""
    current = (now or datetime.now(US_PEAK_TZ)).astimezone(US_PEAK_TZ)
    day = current.date() + timedelta(days=1)
    return datetime(
        day.year,
        day.month,
        day.day,
        US_PEAK_HOUR,
        US_PEAK_MINUTE,
        0,
        tzinfo=US_PEAK_TZ,
    )


def build_us_peak_slots(
    count: int,
    *,
    now: datetime | None = None,
    first: datetime | None = None,
    interval: timedelta | None = None,
) -> list[datetime]:
    start = first or first_us_peak_slot(now=now)
    start = start.astimezone(US_PEAK_TZ).replace(
        hour=US_PEAK_HOUR,
        minute=US_PEAK_MINUTE,
        second=0,
        microsecond=0,
    )
    gap = interval if isinstance(interval, timedelta) and interval.total_seconds() > 0 else US_PEAK_SLOT_INTERVAL
    return [start + (gap * index) for index in range(max(0, count))]


def slot_iso_utc(slot: datetime) -> str:
    """Strict UTC Z: ``YYYY-MM-DDTHH:MM:SSZ``."""
    utc = slot.astimezone(timezone.utc).replace(microsecond=0)
    return utc.strftime("%Y-%m-%dT%H:%M:%SZ")


def is_utc_z(value: str | None) -> bool:
    return bool(UTC_Z_RE.match(str(value or "").strip()))


def coerce_scheduled_time(value: str | datetime | None) -> str:
    """Normalize a timestamp to ``YYYY-MM-DDTHH:MM:SSZ``. Empty stays empty."""
    if isinstance(value, datetime):
        aware = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return slot_iso_utc(aware)
    text = str(value or "").strip()
    if not text:
        return ""
    if is_utc_z(text):
        return text
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return slot_iso_utc(parsed)


_SHORTS_IN_TITLE = re.compile(r"(?:^|\s)#shorts\b", re.IGNORECASE)


def strip_shorts_title(title: str, *, limit: int = YOUTUBE_TITLE_MAX_CHARS) -> str:
    """Remove format hashtag ``#Shorts`` and fit the hook into *limit* chars."""
    clean = _WORD_CUT_RE.sub(" ", (title or "").strip())
    clean = _SHORTS_IN_TITLE.sub("", clean)
    clean = clean.strip(" -|—–,")
    return clip_text(clean, limit) if clean else ""


def ensure_shorts_title(title: str, *, limit: int = YOUTUBE_TITLE_MAX_CHARS) -> str:
    """Deprecated: Aiwake titles no longer append ``#Shorts``. Strips it instead."""
    return strip_shorts_title(title, limit=limit)


def merge_high_rpm_hashtags(hashtags: Iterable[str] | None = None) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for raw in (*(hashtags or ()), *HIGH_RPM_HASHTAGS):
        token = " ".join(str(raw or "").split()).strip()
        if not token:
            continue
        if not token.startswith("#"):
            token = f"#{token.lstrip('#')}"
        key = token.lower()
        if key in seen:
            continue
        seen.add(key)
        merged.append(token)
    return merged


def merge_high_rpm_search_tags(tags: Iterable[str] | None = None) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for raw in (*(tags or ()), *HIGH_RPM_SEARCH_TAGS):
        token = " ".join(str(raw or "").replace("#", " ").split()).strip().lower()
        if not token or token in seen:
            continue
        seen.add(token)
        merged.append(token)
    return merged


def _nested_dict(row: dict[str, Any], *keys: str) -> dict[str, Any]:
    cursor: Any = row
    for key in keys:
        if not isinstance(cursor, dict):
            return {}
        cursor = cursor.get(key)
    return cursor if isinstance(cursor, dict) else {}


def validate_queue_ready(
    row: dict[str, Any] | None,
    *,
    scheduled_time: str | None = None,
    require_scheduled_time: bool = True,
) -> list[str]:
    """Return schema errors that block the processing queue. Empty = ready."""
    errors: list[str] = []
    if not isinstance(row, dict):
        return ["row is not an object"]
    base = row.get("base_metadata")
    if not isinstance(base, dict) or not base:
        errors.append("base_metadata is missing")
    else:
        if not str(base.get("title") or "").strip():
            errors.append("base_metadata.title is empty")
        if not str(base.get("caption") or "").strip():
            errors.append("base_metadata.caption is empty")
        tags = base.get("search_tags")
        if not isinstance(tags, list) or not any(str(tag).strip() for tag in tags):
            errors.append("search_tags is empty")
    if require_scheduled_time:
        youtube = _nested_dict(row, "platform_overrides", "youtube")
        slot = scheduled_time if scheduled_time is not None else youtube.get("scheduled_time")
        if not is_utc_z(str(slot or "").strip()):
            errors.append(
                "platform_overrides.youtube.scheduled_time must be YYYY-MM-DDTHH:MM:SSZ"
            )
    return errors


def clip_text(text: str, limit: int) -> str:
    """Fit *text* into *limit* characters, preferring a word boundary."""
    clean = _WORD_CUT_RE.sub(" ", (text or "").strip())
    if limit <= 0:
        return ""
    if len(clean) <= limit:
        return clean
    window = clean[: max(1, limit - 1)].rstrip(" |—–,;:-")
    if " " in window:
        window = window.rsplit(" ", 1)[0].rstrip(" |—–,;:-")
    if not window:
        window = clean[: max(1, limit - 1)].rstrip()
    clipped = window + "…"
    return clipped if len(clipped) <= limit else window[:limit]


def default_posting_status() -> dict[str, str]:
    return {name: PostingState.PENDING.value for name in DISTRIBUTION_PLATFORMS}


def merge_posting_status(
    existing: dict[str, Any] | None,
    incoming: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Keep non-pending publisher writes; fill the rest from *incoming* or pending."""
    allowed = {state.value for state in PostingState}
    merged = default_posting_status()
    if isinstance(incoming, dict):
        for name in DISTRIBUTION_PLATFORMS:
            value = str(incoming.get(name) or "").strip().lower()
            if value in allowed:
                merged[name] = value
    if isinstance(existing, dict):
        for name in DISTRIBUTION_PLATFORMS:
            value = str(existing.get(name) or "").strip().lower()
            if value and value != PostingState.PENDING.value:
                merged[name] = value
    return merged


class BaseMetadata(BaseModel):
    """Channel-agnostic copy pack used when a network has no override."""

    model_config = ConfigDict(extra="allow")

    title: str = Field(..., max_length=TITLE_MAX_CHARS)
    caption: str
    hashtags: list[str] = Field(default_factory=list)
    search_tags: list[str] = Field(default_factory=list)
    hooks: list[str] = Field(default_factory=list)

    @field_validator("title")
    @classmethod
    def _clip_title(cls, value: str) -> str:
        return clip_text(value, TITLE_MAX_CHARS)

    @field_validator("hashtags", "search_tags", "hooks")
    @classmethod
    def _clean_tags(cls, value: list[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for raw in value or []:
            token = " ".join(str(raw or "").split()).strip()
            if not token:
                continue
            key = token.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(token)
        return out


class XOverride(BaseModel):
    caption: str = Field(..., max_length=X_CAPTION_MAX_CHARS)

    @field_validator("caption")
    @classmethod
    def _clip_x(cls, value: str) -> str:
        return clip_text(value, X_CAPTION_MAX_CHARS)


class PinterestOverride(BaseModel):
    board_name: str = "AI Consciousness & Tech"
    destination_url: str = ""


class YouTubeOverride(BaseModel):
    """YouTube Shorts payload. Extra keys (video_id, scheduled_time) are legal."""

    model_config = ConfigDict(extra="allow")

    category_id: str = YOUTUBE_CATEGORY_SCIENCE_TECH
    is_short: bool = True
    title: str = ""
    caption: str = ""
    video_id: str = ""
    scheduled_time: str = ""

    @field_validator("category_id")
    @classmethod
    def _category(cls, value: str) -> str:
        return str(value or YOUTUBE_CATEGORY_SCIENCE_TECH).strip() or YOUTUBE_CATEGORY_SCIENCE_TECH

    @field_validator("title")
    @classmethod
    def _clean_title(cls, value: str) -> str:
        return strip_shorts_title(str(value or "").strip())

    @field_validator("scheduled_time")
    @classmethod
    def _utc_z_slot(cls, value: str) -> str:
        return coerce_scheduled_time(value)


class PlatformOverrides(BaseModel):
    """Sparse per-network deltas. Unlisted networks use ``base_metadata``."""

    model_config = ConfigDict(extra="allow")

    x: XOverride
    pinterest: PinterestOverride = Field(default_factory=PinterestOverride)
    youtube: YouTubeOverride = Field(default_factory=YouTubeOverride)


class PostingStatus(BaseModel):
    youtube: PostingState = PostingState.PENDING
    instagram: PostingState = PostingState.PENDING
    tiktok: PostingState = PostingState.PENDING
    facebook: PostingState = PostingState.PENDING
    x: PostingState = PostingState.PENDING
    pinterest: PostingState = PostingState.PENDING
    kwai: PostingState = PostingState.PENDING

    def as_map(self) -> dict[str, str]:
        return {name: getattr(self, name).value for name in DISTRIBUTION_PLATFORMS}


class DistributionRecord(BaseModel):
    """One distributable asset. Serialises as a content_library row."""

    model_config = ConfigDict(extra="allow")

    schema_version: str = SCHEMA_VERSION
    channel_id: str
    post_type: str
    session_id: str = ""
    topic: str = ""
    video_path: str
    final_caption: str = ""
    timestamp: str = Field(default_factory=utcnow_iso)
    imgbb_url: str = ""
    transcript_path: str = ""
    asset_id: str = ""
    base_metadata: BaseMetadata
    platform_overrides: PlatformOverrides
    posting_status: PostingStatus = Field(default_factory=PostingStatus)

    def to_library_row(self) -> dict[str, Any]:
        """JSON row with lean aliases for existing Facebook/YouTube readers."""
        payload = self.model_dump(mode="json")
        caption = self.base_metadata.caption
        payload["final_caption"] = self.final_caption or caption
        payload["humanized_caption"] = caption
        payload["facebook_caption"] = caption
        payload.setdefault("imgbb_url", self.imgbb_url or "")
        return payload


def build_platform_overrides(
    *,
    x_caption: str,
    board_name: str = "AI Consciousness & Tech",
    destination_url: str = "",
    youtube_category_id: str = YOUTUBE_CATEGORY_SCIENCE_TECH,
    is_short: bool = True,
    youtube_title: str = "",
    youtube_caption: str = "",
    scheduled_time: str | datetime | None = "",
) -> PlatformOverrides:
    return PlatformOverrides(
        x=XOverride(caption=x_caption),
        pinterest=PinterestOverride(
            board_name=board_name,
            destination_url=destination_url,
        ),
        youtube=YouTubeOverride(
            category_id=str(youtube_category_id or YOUTUBE_CATEGORY_SCIENCE_TECH),
            is_short=bool(is_short),
            title=youtube_title or "",
            caption=youtube_caption or "",
            scheduled_time=coerce_scheduled_time(scheduled_time),
        ),
    )


def posting_status_from_map(raw: dict[str, Any] | None) -> PostingStatus:
    merged = merge_posting_status(raw, default_posting_status())
    return PostingStatus.model_validate(merged)


def content_library_path(channel: str, *, outputs_dir: Path | None = None) -> Path:
    """Primary: ``channels_config/<channel>/store/content_library.json``.

    Pass *outputs_dir* only for tests or an explicit override. Production
    callers should omit it so reads/writes go through the durable store.
    """
    if outputs_dir is not None:
        return Path(outputs_dir) / "content_library.json"
    from utils.pipeline_paths import channel_store_dir

    return channel_store_dir(channel) / "content_library.json"


def load_distribution_library(path: Path) -> list[dict[str, Any]]:
    """Return library rows. Accepts a bare array or ``{entries: [...]}``."""
    from modules.durable_store import hydrate_state_file

    path = hydrate_state_file(path)
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if isinstance(raw, list):
        return [row for row in raw if isinstance(row, dict)]
    if isinstance(raw, dict) and isinstance(raw.get("entries"), list):
        return [row for row in raw["entries"] if isinstance(row, dict)]
    return []


def save_distribution_library(path: Path, rows: Iterable[dict[str, Any]]) -> Path:
    from modules.durable_store import sync_state_file

    path.parent.mkdir(parents=True, exist_ok=True)
    payload = list(rows)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)
    sync_state_file(path)
    return path


def _row_identity(row: dict[str, Any]) -> tuple[str, str]:
    video = str(row.get("video_path") or "").replace("\\", "/").rstrip("/").lower()
    session = str(row.get("session_id") or "").strip().lower()
    return video, session


def _youtube_block(row: dict[str, Any]) -> dict[str, Any]:
    return dict(_nested_dict(row, "platform_overrides", "youtube"))


def _preserve_youtube_identity(
    existing: dict[str, Any],
    incoming: dict[str, Any],
) -> dict[str, Any]:
    """Keep live YouTube ids / slots when a catalog refresh omits them."""
    overrides = incoming.get("platform_overrides")
    if not isinstance(overrides, dict):
        return incoming
    existing_yt = _youtube_block(existing)
    incoming_yt = dict(overrides.get("youtube") or {}) if isinstance(overrides.get("youtube"), dict) else {}
    if existing_yt.get("video_id") and not incoming_yt.get("video_id"):
        incoming_yt["video_id"] = existing_yt["video_id"]
    existing_status = str((existing.get("posting_status") or {}).get("youtube") or "").lower()
    if existing_status in {"scheduled", "posted"} and existing_yt.get("scheduled_time"):
        incoming_yt["scheduled_time"] = existing_yt["scheduled_time"]
    elif not str(incoming_yt.get("scheduled_time") or "").strip() and existing_yt.get("scheduled_time"):
        incoming_yt["scheduled_time"] = existing_yt["scheduled_time"]
    merged_overrides = dict(overrides)
    merged_overrides["youtube"] = incoming_yt
    updated = dict(incoming)
    updated["platform_overrides"] = merged_overrides
    return updated


def upsert_distribution_row(
    path: Path,
    record: DistributionRecord | dict[str, Any],
) -> dict[str, Any]:
    """Insert or refresh one row. Never resets a non-pending posting_status."""
    incoming = record.to_library_row() if isinstance(record, DistributionRecord) else dict(record)
    incoming["posting_status"] = merge_posting_status(
        None, incoming.get("posting_status")
    )
    video_key, session_key = _row_identity(incoming)
    rows = load_distribution_library(path)
    updated = False
    for index, existing in enumerate(rows):
        existing_video, existing_session = _row_identity(existing)
        matched = bool(video_key and existing_video == video_key)
        if not matched and session_key and existing_session == session_key:
            matched = True
        if not matched:
            continue
        incoming = _preserve_youtube_identity(existing, incoming)
        merged = dict(existing)
        merged.update(incoming)
        merged["posting_status"] = merge_posting_status(
            existing.get("posting_status"), incoming.get("posting_status")
        )
        rows[index] = merged
        incoming = merged
        updated = True
        break
    if not updated:
        rows.append(incoming)
    save_distribution_library(path, rows)
    return incoming


__all__ = [
    "DEFAULT_HASHTAGS",
    "DISTRIBUTION_PLATFORMS",
    "HIGH_RPM_HASHTAGS",
    "HIGH_RPM_SEARCH_TAGS",
    "SCHEMA_VERSION",
    "SHORTS_TAG",
    "TITLE_MAX_CHARS",
    "US_PEAK_HOUR",
    "US_PEAK_TZ",
    "X_CAPTION_MAX_CHARS",
    "YOUTUBE_CATEGORY_SCIENCE_TECH",
    "BaseMetadata",
    "DistributionRecord",
    "PinterestOverride",
    "PlatformOverrides",
    "PostingState",
    "PostingStatus",
    "XOverride",
    "YouTubeOverride",
    "build_platform_overrides",
    "build_us_peak_slots",
    "clip_text",
    "coerce_scheduled_time",
    "content_library_path",
    "default_posting_status",
    "ensure_shorts_title",
    "strip_shorts_title",
    "first_us_peak_slot",
    "is_utc_z",
    "load_distribution_library",
    "merge_high_rpm_hashtags",
    "merge_high_rpm_search_tags",
    "merge_posting_status",
    "posting_status_from_map",
    "save_distribution_library",
    "slot_iso_utc",
    "upsert_distribution_row",
    "utcnow_iso",
    "validate_queue_ready",
]

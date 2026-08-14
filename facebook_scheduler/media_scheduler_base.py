# -*- coding: utf-8 -*-
"""
facebook_scheduler/media_scheduler_base.py
==========================================
Reusable foundation for Meta Business Suite **media** scheduling
(Reels, Images, Carousels).

Shared pieces
-------------
1. ``LocalMediaQueue`` — scan a channel folder, track posted files in
   ``facebook_history.json``, and move completed media into
   ``clips/posted_facebook/`` (or a format-specific posted folder).
2. ``UniversalComposerScheduler`` — route by media type:
   * Video (``.mp4`` / ``.mov`` / …) → dedicated
     ``/latest/reels_composer`` + CDP inject
   * Photo (``.jpg`` / ``.png`` / …) → Universal ``/latest/`` Create post
   then caption → schedule (last + 2 h + 60–180 min jitter).

Subclasses implement ``select_format()`` / ``on_media_uploaded()`` hooks and
optionally ``prepare_caption()`` / ``advance_composer()``.
"""
from __future__ import annotations

import json
import random
import re
import shutil
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, TYPE_CHECKING

from facebook_scheduler import config
from facebook_scheduler.human_behavior import HumanBehavior
from facebook_scheduler.logger import get_logger, save_screenshot

if TYPE_CHECKING:
    from playwright.sync_api import Locator, Page

_log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Paths / scheduling defaults
# ---------------------------------------------------------------------------

OUTPUTS_BASE_DIR: Path = Path(
    r"G:\My Drive\Z sosFiles\Z_act\@ NETWORK"
    r"\@MEDIAUPSCALE_FACTORY_DYNAMIC_CONTENT"
    r"\Unified Multi-Page Factory\outputs"
)

BUSINESS_SUITE_HOME = "https://business.facebook.com/latest/"
REELS_COMPOSER_URL = "https://business.facebook.com/latest/reels_composer"
HISTORY_FILENAME = "facebook_history.json"
POSTED_SUBDIR = "posted_facebook"

_VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v"}
_PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}


def is_video_file(path: str | Path) -> bool:
    """True when the path extension is a Meta Reel / video type."""
    return Path(path).suffix.lower() in _VIDEO_EXTENSIONS

# First: now + random(FIRST_OFFSET_MIN..FIRST_OFFSET_MAX) minutes
# Subsequent: last + BASE_INTERVAL_HOURS + random(JITTER_MIN..JITTER_MAX) minutes
FIRST_OFFSET_MIN_MINUTES = getattr(config, "REELS_FIRST_OFFSET_MIN_MINUTES", 25)
FIRST_OFFSET_MAX_MINUTES = getattr(config, "REELS_FIRST_OFFSET_MAX_MINUTES", 60)
BASE_INTERVAL_HOURS = getattr(config, "REELS_BASE_INTERVAL_HOURS", 4)
JITTER_MIN_MINUTES = getattr(config, "REELS_JITTER_MIN_MINUTES", 0)
JITTER_MAX_MINUTES = getattr(config, "REELS_JITTER_MAX_MINUTES", 60)

# Never schedule closer than this to "now" (Meta requires ≥20 min)
MIN_LEAD_MINUTES = getattr(config, "REELS_MIN_LEAD_MINUTES", 25)

_DATE_FORMAT = "%m/%d/%Y"
_HISTORY_DT_FMT = "%Y-%m-%d %H:%M:%S"


def ensure_page_active(page: "Page") -> None:
    """
    Soft page wake without stealing OS window focus.

    Intentionally does **not** call ``bring_to_front()`` or ``window.focus()``
    — those pull AdsPower/Dolphin over the user's active desktop apps.
    Kept as a no-op hook so call sites remain stable.
    """
    _ = page  # reserved for future non-focus keep-alives


# ---------------------------------------------------------------------------
# English UI selectors (universal composer)
# ---------------------------------------------------------------------------

_CREATE_POST_CANDIDATES: list[tuple] = [
    ("role", "button", {"name": "Create post"}),
    ("locator", 'button:has-text("Create post")'),
    ("locator", '[aria-label="Create post"]'),
    ("locator", '[data-testid="composer-trigger"]'),
]

_CAPTION_CANDIDATES: list[tuple] = [
    ("locator", 'div[role="textbox"][contenteditable="true"]'),
    ("locator", 'div[contenteditable="true"][role="textbox"]'),
    ("locator", 'div[aria-label*="Write a caption" i]'),
    ("locator", 'div[aria-label*="caption" i][contenteditable="true"]'),
    ("locator", 'div[aria-label*="description" i][contenteditable="true"]'),
    ("locator", 'div[role="textbox"]'),
]

_SCHEDULE_TOGGLE_SELS = [
    'input[role="switch"][aria-label="Set date and time"]',
    'input[role="switch"][aria-label*="date and time" i]',
    'div[role="switch"][aria-label*="date and time" i]',
    'span[role="switch"][aria-label*="date and time" i]',
]

_SCHEDULE_CONFIRM_CANDIDATES: list[tuple] = [
    ("role", "button", {"name": "Schedule"}),
    ("locator", 'button:has-text("Schedule")'),
    ("locator", '[aria-label="Schedule"]'),
    ("locator", 'div[role="button"]:has-text("Schedule")'),
]


# ===========================================================================
# Locator helpers (self-contained — do not depend on facebook_scheduler.py)
# ===========================================================================

def _build_locator(page: "Page", descriptor: tuple) -> "Locator":
    kind = descriptor[0]
    if kind == "role":
        role = descriptor[1]
        opts = descriptor[2] if len(descriptor) > 2 else {}
        return page.get_by_role(role, **opts)
    if kind == "locator":
        return page.locator(descriptor[1])
    if kind == "label":
        return page.get_by_label(descriptor[1])
    if kind == "text":
        return page.get_by_text(descriptor[1])
    raise ValueError(f"Unknown locator kind: {kind!r}")


def try_first_visible(
    page: "Page",
    candidates: list[tuple],
    timeout_each_ms: int = 4_000,
    label: str = "element",
) -> "Locator":
    """Return the first visible locator from *candidates*."""
    errors: list[str] = []
    for descriptor in candidates:
        try:
            loc = _build_locator(page, descriptor).first
            loc.wait_for(state="visible", timeout=timeout_each_ms)
            _log.debug("Resolved %s: %s", label, descriptor)
            return loc
        except Exception as exc:
            errors.append(f"{descriptor!r}: {exc}")
    raise RuntimeError(
        f"Could not find visible '{label}' after trying "
        f"{len(candidates)} candidate(s).\n"
        + "\n".join(f"  {e}" for e in errors)
    )


# ===========================================================================
# Local media queue + Facebook history
# ===========================================================================

@dataclass
class MediaItem:
    """One pending media file ready to schedule."""

    path: Path
    caption: str
    format_type: str = "reel"
    metadata: dict[str, Any] | None = None
    caption_source: str = "fallback"  # facebook_caption | caption | ... | fallback

    @property
    def filename(self) -> str:
        return self.path.name


class LocalMediaQueue:
    """
    Channel-scoped local queue for Facebook media scheduling.

    Layout
    ------
    ``outputs/<channel_name>/clips/*.mp4``          — pending queue
    ``outputs/<channel_name>/clips/posted_facebook/`` — completed (moved)
    ``outputs/<channel_name>/facebook_history.json`` — durable state
    ``outputs/<channel_name>/content_library.json``  — network captions
    ``outputs/<channel_name>/library/post_*.json``   — per-post metadata
    """

    def __init__(
        self,
        channel_name: str,
        *,
        media_subdir: str = "clips",
        extensions: tuple[str, ...] = (".mp4",),
        outputs_base: Path | None = None,
        move_on_success: bool = True,
    ) -> None:
        self.channel_name = channel_name
        self.media_subdir = media_subdir
        self.extensions = tuple(e.lower() for e in extensions)
        self.outputs_base = Path(outputs_base) if outputs_base else OUTPUTS_BASE_DIR
        self.move_on_success = move_on_success

        self.channel_dir = self.outputs_base / channel_name
        self.media_dir = self.channel_dir / media_subdir
        self.posted_dir = self.media_dir / POSTED_SUBDIR
        self.history_path = self.channel_dir / HISTORY_FILENAME
        self.content_library_path = self.channel_dir / "content_library.json"
        self.posts_library_dir = self.channel_dir / "library"

        self.media_dir.mkdir(parents=True, exist_ok=True)
        self.posted_dir.mkdir(parents=True, exist_ok=True)

        self._history: dict[str, Any] = self._load_history()
        self._metadata_by_filename: dict[str, dict[str, Any]] | None = None

    # ------------------------------------------------------------------
    # History I/O
    # ------------------------------------------------------------------

    def _load_history(self) -> dict[str, Any]:
        if not self.history_path.is_file():
            return {"last_scheduled_at": None, "posted": []}
        try:
            data = json.loads(self.history_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("history root is not an object")
            data.setdefault("last_scheduled_at", None)
            data.setdefault("posted", [])
            return data
        except Exception as exc:
            _log.warning(
                "Corrupt facebook_history.json (%s) — starting fresh: %s",
                self.history_path,
                exc,
            )
            return {"last_scheduled_at": None, "posted": []}

    def _save_history(self) -> None:
        self.history_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.history_path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(self._history, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        tmp.replace(self.history_path)

    def posted_filenames(self) -> set[str]:
        names: set[str] = set()
        for entry in self._history.get("posted", []):
            if isinstance(entry, dict) and entry.get("filename"):
                names.add(str(entry["filename"]))
            elif isinstance(entry, str):
                names.add(entry)
        # Also treat anything already sitting in posted_facebook/ as done.
        if self.posted_dir.is_dir():
            for p in self.posted_dir.iterdir():
                if p.is_file():
                    names.add(p.name)
        return names

    def last_scheduled_at(self) -> datetime | None:
        raw = self._history.get("last_scheduled_at")
        if not raw:
            return None
        for fmt in (_HISTORY_DT_FMT, "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M"):
            try:
                return datetime.strptime(str(raw), fmt)
            except ValueError:
                continue
        _log.warning("Unparseable last_scheduled_at=%r", raw)
        return None

    def next_schedule_datetime(self, *, now: datetime | None = None) -> datetime:
        """
        First post (or last in the past): ``now + random(25..60) minutes``.
        Subsequent: ``last_scheduled + 4 hours + random(0..60) minutes``.
        """
        now = now or datetime.now()
        last = self.last_scheduled_at()

        if not last or last < now:
            offset_minutes = random.randint(
                FIRST_OFFSET_MIN_MINUTES, FIRST_OFFSET_MAX_MINUTES
            )
            next_time = now + timedelta(minutes=offset_minutes)
            _log.info(
                "First post in queue (or last schedule in the past). "
                "Scheduling %d minutes from now.",
                offset_minutes,
            )
        else:
            random_minutes = random.randint(JITTER_MIN_MINUTES, JITTER_MAX_MINUTES)
            next_time = last + timedelta(
                hours=BASE_INTERVAL_HOURS, minutes=random_minutes
            )
            _log.info(
                "Subsequent post. Scheduling %dh and %dm after the previous post.",
                BASE_INTERVAL_HOURS,
                random_minutes,
            )

        # Safety floor: Meta requires ≥20 minutes lead
        floor = now + timedelta(minutes=MIN_LEAD_MINUTES)
        if next_time < floor:
            next_time = floor
            _log.info(
                "Clamped schedule to Meta min-lead floor (%d min from now).",
                MIN_LEAD_MINUTES,
            )

        return next_time.replace(second=0, microsecond=0)

    def mark_scheduled(
        self,
        item: MediaItem,
        scheduled_dt: datetime,
        *,
        dry_run: bool = False,
    ) -> Path | None:
        """
        Record success in JSON and optionally move the file into
        ``posted_facebook/``.  Call ONLY after Playwright confirms schedule.
        """
        if dry_run:
            _log.info(
                "[DRY-RUN] Would mark scheduled: %s @ %s",
                item.filename,
                scheduled_dt.strftime(_HISTORY_DT_FMT),
            )
            return None

        entry = {
            "filename": item.filename,
            "format": item.format_type,
            "scheduled_at": scheduled_dt.strftime(_HISTORY_DT_FMT),
            "posted_at": datetime.now().strftime(_HISTORY_DT_FMT),
            "caption_preview": (item.caption or "")[:160],
        }
        self._history.setdefault("posted", []).append(entry)
        self._history["last_scheduled_at"] = scheduled_dt.strftime(_HISTORY_DT_FMT)
        self._save_history()
        _log.info(
            "History updated → %s (last_scheduled_at=%s)",
            self.history_path.name,
            self._history["last_scheduled_at"],
        )

        moved_to: Path | None = None
        if self.move_on_success and item.path.is_file():
            dest = self.posted_dir / item.path.name
            if dest.exists():
                stem, suf = item.path.stem, item.path.suffix
                dest = self.posted_dir / f"{stem}_{int(time.time())}{suf}"
            shutil.move(str(item.path), str(dest))
            moved_to = dest
            _log.info("Moved %s → %s", item.filename, dest)
        return moved_to

    # ------------------------------------------------------------------
    # Metadata library + caption routing
    # ------------------------------------------------------------------

    # Fallback-only truncation (network captions are never shortened).
    MAX_CAPTION_CHARS = 180
    MAX_CAPTION_SENTENCES = 2

    # Network-optimized caption keys, in priority order.
    # ``facebook_caption`` is the preferred Facebook/Reels body (+ hashtags).
    # Legacy library rows store the same text as ``final_caption`` /
    # ``humanized_caption`` — those are promoted into ``facebook_caption``.
    _CAPTION_PRIORITY = (
        "facebook_caption",
        "caption",
        "final_caption",
        "humanized_caption",
        "caption_body",
        "description",
    )

    def _index_metadata_library(self) -> dict[str, dict[str, Any]]:
        """
        Build ``filename.lower() → metadata`` from content_library.json and
        ``library/post_*.json``. Later sources overwrite earlier ones so the
        freshest per-post snapshot wins.
        """
        index: dict[str, dict[str, Any]] = {}

        def _ingest(row: dict[str, Any]) -> None:
            if not isinstance(row, dict):
                return
            video = (
                row.get("video_path")
                or row.get("clip_path")
                or row.get("local_video")
                or row.get("media_path")
                or ""
            )
            if not video:
                return
            name = Path(str(video)).name.lower()
            if not name:
                return
            meta = dict(row)
            # Promote best available caption into facebook_caption when missing.
            if not str(meta.get("facebook_caption") or "").strip():
                for key in (
                    "final_caption",
                    "humanized_caption",
                    "caption",
                    "caption_body",
                    "description",
                ):
                    val = str(meta.get(key) or "").strip()
                    if val:
                        meta["facebook_caption"] = val
                        break
            if not str(meta.get("caption") or "").strip():
                # Keep a generic caption alias for the priority chain.
                fb = str(meta.get("facebook_caption") or "").strip()
                if fb:
                    meta["caption"] = fb
            index[name] = meta

        # 1) Lean content library
        if self.content_library_path.is_file():
            try:
                data = json.loads(
                    self.content_library_path.read_text(encoding="utf-8")
                )
                if isinstance(data, list):
                    for row in data:
                        _ingest(row)
                _log.info(
                    "Loaded content_library.json (%d video row(s)).",
                    len(data) if isinstance(data, list) else 0,
                )
            except Exception as exc:
                _log.warning("Could not read %s: %s", self.content_library_path, exc)

        # 2) Per-post snapshots (overwrite with richer / newer fields)
        if self.posts_library_dir.is_dir():
            post_files = sorted(self.posts_library_dir.glob("post_*.json"))
            for post_path in post_files:
                try:
                    row = json.loads(post_path.read_text(encoding="utf-8"))
                    _ingest(row)
                except Exception:
                    continue
            _log.info(
                "Indexed %d post_*.json snapshot(s) from %s.",
                len(post_files),
                self.posts_library_dir.name,
            )

        return index

    def metadata_for(self, media_path: Path) -> dict[str, Any]:
        """Return metadata dict for *media_path* (may be empty)."""
        if self._metadata_by_filename is None:
            self._metadata_by_filename = self._index_metadata_library()
        return dict(self._metadata_by_filename.get(media_path.name.lower(), {}))

    @classmethod
    def resolve_caption(
        cls,
        media_path: Path,
        metadata: dict[str, Any] | None = None,
    ) -> tuple[str, str]:
        """
        Resolve description/caption with network-first priority.

        Returns ``(caption_text, source_key)``.

        Priority
        --------
        ``facebook_caption`` → ``caption`` → ``final_caption`` →
        ``humanized_caption`` → ``caption_body`` → ``description`` →
        sidecar ``.txt`` → filename stem fallback.

        Network captions are returned **verbatim** (no truncation) so
        hashtags / post body stay intact.
        """
        meta = metadata or {}
        for key in cls._CAPTION_PRIORITY:
            val = str(meta.get(key) or "").strip()
            if val:
                return val, key

        sidecar = media_path.with_suffix(".txt")
        if sidecar.is_file():
            text = sidecar.read_text(encoding="utf-8", errors="replace").strip()
            if text:
                return text, "sidecar_txt"

        return cls.caption_from_sidecar_or_stem(media_path), "fallback"

    @staticmethod
    def caption_from_sidecar_or_stem(media_path: Path) -> str:
        """
        Last-resort fallback: ``<stem>.txt`` or a shortened stem hook.
        Network captions must NOT go through this path.
        """
        sidecar = media_path.with_suffix(".txt")
        if sidecar.is_file():
            text = sidecar.read_text(encoding="utf-8", errors="replace").strip()
            if text:
                return LocalMediaQueue.shorten_caption(text)

        stem = media_path.stem
        stem = re.sub(r"^(reel|clip|video)_", "", stem, flags=re.I)
        stem = re.sub(r"_v\d+(_v\d+)?$", "", stem, flags=re.I)
        stem = stem.replace("___", " - ").replace("__", " - ").replace("_", " ")
        stem = re.sub(r"\s+", " ", stem).strip(" -")
        if not stem:
            return media_path.stem
        raw = stem[0].upper() + stem[1:] if len(stem) > 1 else stem.upper()
        return LocalMediaQueue.shorten_caption(raw)

    @staticmethod
    def shorten_caption(text: str) -> str:
        """Collapse *fallback* text into a short 1–2 sentence hook."""
        cleaned = re.sub(r"\s+", " ", (text or "").strip())
        if not cleaned:
            return ""

        parts = re.split(r"(?<=[.!?])\s+", cleaned)
        hook_parts: list[str] = []
        for part in parts:
            part = part.strip()
            if not part:
                continue
            hook_parts.append(part)
            if len(hook_parts) >= LocalMediaQueue.MAX_CAPTION_SENTENCES:
                break
        hook = " ".join(hook_parts) if hook_parts else cleaned

        if len(hook) > LocalMediaQueue.MAX_CAPTION_CHARS:
            cut = hook[: LocalMediaQueue.MAX_CAPTION_CHARS - 1]
            if " " in cut:
                cut = cut.rsplit(" ", 1)[0]
            hook = cut.rstrip(" ,;:-") + "..."
        return hook

    # ------------------------------------------------------------------
    # Scan
    # ------------------------------------------------------------------

    @staticmethod
    def _mp4_basename_from_cell(val: Any) -> str | None:
        """Extract an ``.mp4`` basename from a path, URL, or bare filename cell."""
        from urllib.parse import unquote

        s = str(val).strip()
        if ".mp4" not in s.lower():
            return None
        s = unquote(s.split("?")[0].split("#")[0]).replace("\\", "/")
        base = Path(s).name
        if base.lower().endswith(".mp4"):
            return base
        return None

    def _load_postplanner_posted_mp4s(self) -> set[str]:
        """
        Collect posted ``.mp4`` basenames from ancient_knowledge Postplanner sheets.

        Always reads ``postplan_20260802_212650.xlsx`` (canonical batch marker),
        then unions every other ``postplanner/postplan_*.xlsx`` (and the bulk
        import sheet when present) so URL-based MEDIA rows are not missed.
        """
        import pandas as pd

        postplanner_dir = self.channel_dir / "postplanner"
        primary = postplanner_dir / "postplan_20260802_212650.xlsx"
        sheets: list[Path] = []
        if primary.is_file():
            sheets.append(primary)
        if postplanner_dir.is_dir():
            for p in sorted(postplanner_dir.glob("postplan_*.xlsx")):
                if p.resolve() != primary.resolve():
                    sheets.append(p)
        bulk = self.channel_dir / "automated_bulk_posts_import.xlsx"
        if bulk.is_file():
            sheets.append(bulk)

        posted_files: set[str] = set()
        if not sheets:
            _log.error(
                "No Postplanner Excel found under %s — ancient_knowledge "
                "queue filter aborted.",
                postplanner_dir,
            )
            return posted_files

        for excel_path in sheets:
            try:
                df = pd.read_excel(excel_path)
                before = len(posted_files)
                for col in df.columns:
                    for val in df[col].dropna():
                        name = self._mp4_basename_from_cell(val)
                        if name:
                            posted_files.add(name)
                added = len(posted_files) - before
                if added:
                    _log.info(
                        "Postplanner %s: +%d .mp4 name(s) (running total %d).",
                        excel_path.name,
                        added,
                        len(posted_files),
                    )
            except Exception as exc:
                _log.error(
                    "Failed to read Postplanner Excel %s: %s", excel_path, exc
                )

        _log.info(
            "Loaded %d previously posted files from Postplanner Excel set.",
            len(posted_files),
        )
        return posted_files

    def _get_ancient_knowledge_queue(
        self, clips_dir: str | Path, max_clips: int = 6
    ) -> list[Path]:
        """
        Postplanner Excel + mtime filter for ``ancient_knowledge``.

        1. Load posted ``.mp4`` basenames from Postplanner Excel sheets.
        2. Cutoff = mtime of the newest posted clip still on disk.
        3. Keep unposted clips newer than that cutoff.
        4. Take the newest ``max_clips``, then order oldest→newest for schedule.
        """
        clips_path = Path(clips_dir)
        posted_files = self._load_postplanner_posted_mp4s()
        if not posted_files and not (self.channel_dir / "postplanner").is_dir():
            return []

        all_clips: list[dict[str, Any]] = []
        if not clips_path.is_dir():
            return []
        for path in clips_path.iterdir():
            if not path.is_file():
                continue
            if path.suffix.lower() != ".mp4":
                continue
            if path.name.endswith(".bak") or ".bak_" in path.name:
                continue
            all_clips.append(
                {
                    "name": path.name,
                    "path": path,
                    "mtime": path.stat().st_mtime,
                }
            )

        if not all_clips:
            return []

        posted_mtimes = [
            c["mtime"] for c in all_clips if c["name"] in posted_files
        ]
        cutoff_mtime = max(posted_mtimes) if posted_mtimes else 0.0

        unposted_clips = [
            c
            for c in all_clips
            if c["name"] not in posted_files and c["mtime"] > cutoff_mtime
        ]

        unposted_clips.sort(key=lambda x: x["mtime"], reverse=True)
        target_clips = unposted_clips[:max_clips]
        # Schedule oldest of the selected 6 first
        target_clips.sort(key=lambda x: x["mtime"])

        _log.info(
            "Filtered ancient_knowledge queue down to %d clips "
            "(cutoff_mtime=%.0f, excel_posted=%d, candidates=%d).",
            len(target_clips),
            cutoff_mtime,
            len(posted_files),
            len(unposted_clips),
        )
        return [c["path"] for c in target_clips]

    def scan_pending(self, *, format_type: str = "reel") -> list[MediaItem]:
        """Return pending media files not yet recorded as posted."""
        if not self.media_dir.is_dir():
            _log.warning("Media directory missing: %s", self.media_dir)
            return []

        # Force a fresh metadata index each scan.
        self._metadata_by_filename = self._index_metadata_library()
        posted = self.posted_filenames()
        items: list[MediaItem] = []
        meta_hits = 0

        if self.channel_name == "ancient_knowledge":
            pending_paths = self._get_ancient_knowledge_queue(
                self.media_dir, max_clips=6
            )
            # Still honor local facebook_history / posted_facebook moves
            pending_paths = [p for p in pending_paths if p.name not in posted]
        else:
            pending_paths = []
            for path in sorted(
                self.media_dir.iterdir(), key=lambda p: p.name.lower()
            ):
                if not path.is_file():
                    continue
                if path.suffix.lower() not in self.extensions:
                    continue
                if path.name in posted:
                    continue
                # Skip backup / intermediate files
                if path.name.endswith(".bak") or ".bak_" in path.name:
                    continue
                pending_paths.append(path)

        for path in pending_paths:
            meta = self.metadata_for(path)
            caption, source = self.resolve_caption(path, meta)
            if source != "fallback":
                meta_hits += 1
            items.append(
                MediaItem(
                    path=path,
                    caption=caption,
                    format_type=format_type,
                    metadata=meta,
                    caption_source=source,
                )
            )

        _log.info(
            "Queue scan [%s/%s]: %d pending file(s) (%d already posted, "
            "%d with library captions).",
            self.channel_name,
            self.media_subdir,
            len(items),
            len(posted),
            meta_hits,
        )
        return items


# ===========================================================================
# Universal composer scheduler (abstract base)
# ===========================================================================

class UniversalComposerScheduler(ABC):
    """
    Shared Playwright flow for Meta Business Suite media scheduling.

    Subclasses must implement format selection + media upload. Everything
    else (home navigation, Create post, caption, Next, schedule timing,
    confirmation, error reset, queue loop) lives here.
    """

    format_type: str = "media"
    next_clicks: int = 2  # Reel optimization wizard uses two Next presses

    def __init__(
        self,
        page: "Page",
        channel_name: str,
        *,
        dry_run: bool = False,
        outputs_base: Path | None = None,
        move_on_success: bool = True,
        max_items: int | None = None,
    ) -> None:
        self.page = page
        self.channel_name = channel_name
        self.dry_run = dry_run
        self.max_items = max_items
        self.hb = HumanBehavior(page, dry_run=dry_run)
        self.queue = LocalMediaQueue(
            channel_name,
            media_subdir=self.media_subdir,
            extensions=self.media_extensions,
            outputs_base=outputs_base,
            move_on_success=move_on_success,
        )

    # ---- subclass knobs -------------------------------------------------

    @property
    def media_subdir(self) -> str:
        return "clips"

    @property
    def media_extensions(self) -> tuple[str, ...]:
        return (".mp4",)

    @abstractmethod
    def select_format(self) -> None:
        """Optional in-composer format step (usually a no-op)."""

    def on_media_uploaded(self, item: MediaItem, *, is_video: bool) -> None:
        """Hook after CDP file attach (e.g. Reel processing wait)."""

    def _dismiss_composer_error_modals(self) -> None:
        """Dismiss Meta error dialogs like ``Can't Read Files`` if present."""
        close_modal_btn = self.page.locator(
            'button:has-text("Close"), '
            'div[role="button"]:has-text("Close"), '
            'div[aria-label="Close"], '
            'div[role="button"][aria-label*="Close" i]'
        ).first
        try:
            if close_modal_btn.is_visible(timeout=1_000):
                close_modal_btn.click(force=True)
                self.page.wait_for_timeout(500)
                _log.info("Dismissed composer error / Close modal.")
        except Exception:
            pass

    def prepare_composer_for_item(self, item: MediaItem) -> None:
        """
        Open the correct Meta Business Suite composer for this file type.

        * Video → always ``goto`` ``reels_composer`` (fresh UI every item;
          Meta leaves a success state that hides "Add video" after schedule)
        * Photo → ``https://business.facebook.com/latest/`` (Universal Post)

        Always dismisses leftover error modals first. Does not steal OS focus.
        """
        if self.dry_run:
            return

        file_path = str(item.path.resolve())
        video = is_video_file(file_path)

        self._dismiss_composer_error_modals()

        current = self.page.url or ""
        if video:
            _log.info("Forcing navigation to fresh Reel Composer endpoint...")
            self.page.goto(REELS_COMPOSER_URL, wait_until="domcontentloaded")
            try:
                self.page.wait_for_load_state(
                    "networkidle", timeout=config.LONG_TIMEOUT_MS
                )
            except Exception:
                pass
            # Give Meta's heavy React UI time to render Add video / controls
            self.page.wait_for_timeout(4_000)
        else:
            _log.info("Opening Universal Post Composer endpoint...")
            if "reels_composer" in current or "/latest/" not in current:
                self.page.goto(BUSINESS_SUITE_HOME, wait_until="domcontentloaded")
                try:
                    self.page.wait_for_load_state(
                        "networkidle", timeout=config.LONG_TIMEOUT_MS
                    )
                except Exception:
                    pass
                self.page.wait_for_timeout(3_000)
            else:
                _log.info("Already on Business Suite /latest/ — skipping navigation.")

        self._dismiss_composer_error_modals()

    def _inject_file_via_cdp(self, file_path: str) -> None:
        """
        Assign a local file path to ``input[type=file]`` via CDP
        ``DOM.setFileInputFiles`` (bypasses Playwright's 50 MB WS buffer).
        """
        cdp = None
        try:
            cdp = self.page.context.new_cdp_session(self.page)
            cdp.send("DOM.enable")

            backend_node_id = None
            try:
                doc = cdp.send("DOM.getDocument", {"depth": -1, "pierce": True})
                for selector in (
                    'input[type="file"][accept*="video"]',
                    'input[type="file"]',
                ):
                    queried = cdp.send(
                        "DOM.querySelector",
                        {
                            "nodeId": doc["root"]["nodeId"],
                            "selector": selector,
                        },
                    )
                    node_id = queried.get("nodeId") or 0
                    if node_id:
                        described = cdp.send(
                            "DOM.describeNode", {"nodeId": node_id}
                        )
                        backend_node_id = described["node"]["backendNodeId"]
                        break
            except Exception as q_exc:
                _log.debug("DOM.querySelector for file input failed: %s", q_exc)

            if not backend_node_id:
                evaluated = cdp.send(
                    "Runtime.evaluate",
                    {
                        "expression": (
                            'document.querySelector(\'input[type="file"][accept*="video"]\')'
                            ' || document.querySelector(\'input[type="file"]\')'
                        ),
                        "objectGroup": "file-input-upload",
                        "returnByValue": False,
                    },
                )
                object_id = (evaluated.get("result") or {}).get("objectId")
                if not object_id:
                    raise RuntimeError(
                        "Runtime.evaluate did not return objectId for file input"
                    )
                node_info = cdp.send("DOM.describeNode", {"objectId": object_id})
                backend_node_id = node_info["node"]["backendNodeId"]

            cdp.send(
                "DOM.setFileInputFiles",
                {"files": [file_path], "backendNodeId": backend_node_id},
            )
            self.page.evaluate(
                """() => {
                    const inputs = document.querySelectorAll('input[type="file"]');
                    inputs.forEach((i) => {
                        i.dispatchEvent(new Event('input', { bubbles: true }));
                        i.dispatchEvent(new Event('change', { bubbles: true }));
                    });
                }"""
            )
            _log.info("File attached via CDP successfully.")
        finally:
            if cdp is not None:
                try:
                    cdp.detach()
                except Exception:
                    pass

    def _cdp_set_files_on_backend_node(
        self, *, file_path: str, backend_node_id: int
    ) -> None:
        """Assign local path via CDP ``DOM.setFileInputFiles`` + change events."""
        cdp = None
        try:
            cdp = self.page.context.new_cdp_session(self.page)
            cdp.send("DOM.enable")
            cdp.send(
                "DOM.setFileInputFiles",
                {"files": [file_path], "backendNodeId": backend_node_id},
            )
            self.page.evaluate(
                """() => {
                    const inputs = document.querySelectorAll('input[type="file"]');
                    inputs.forEach((i) => {
                        i.dispatchEvent(new Event('input', { bubbles: true }));
                        i.dispatchEvent(new Event('change', { bubbles: true }));
                    });
                }"""
            )
        finally:
            if cdp is not None:
                try:
                    cdp.detach()
                except Exception:
                    pass

    def _backend_node_id_from_element(self, element_handle: Any) -> int | None:
        """Resolve Playwright ElementHandle → CDP backendNodeId (best-effort)."""
        cdp = None
        try:
            cdp = self.page.context.new_cdp_session(self.page)
            cdp.send("DOM.enable")
            object_id = getattr(
                getattr(element_handle, "_impl_obj", None), "_object_id", None
            )
            if not object_id:
                return None
            node_info = cdp.send("DOM.describeNode", {"objectId": object_id})
            return int(node_info["node"]["backendNodeId"])
        except Exception as exc:
            _log.debug("ElementHandle → backendNodeId failed: %s", exc)
            return None
        finally:
            if cdp is not None:
                try:
                    cdp.detach()
                except Exception:
                    pass

    def upload_media(self, item: MediaItem) -> None:
        """
        Attach media via ``expect_file_chooser`` + ``window.__uploadTarget`` CDP.

        Strict rules:
        * Click Add video / Add photo inside ``expect_file_chooser`` (suppresses
          the native OS dialog).
        * Bind ``file_chooser.element`` to ``window.__uploadTarget``, then
          resolve ``objectId`` / ``backendNodeId`` via CDP ``Runtime.evaluate``
          (works across nested React containers; no DOM.querySelector).
        * Never ``locator.wait_for(input[type=file])``.
        * Never ``file_chooser.set_files()`` (50 MB WebSocket crash on CDP).
        """
        file_path = str(item.path.resolve())
        is_video = is_video_file(file_path)
        target_text = "Add video" if is_video else "Add photo"
        kind = "Video/Reel" if is_video else "Photo"
        size_mb = (
            item.path.stat().st_size / (1024 * 1024) if item.path.is_file() else -1
        )
        _log.info(
            "Uploading media (%s) via expect_file_chooser: %s (%.1f MB)",
            kind,
            file_path,
            size_mb,
        )
        if self.dry_run:
            return
        if not item.path.is_file():
            raise FileNotFoundError(f"Media file missing: {file_path}")

        # Step 1: Dismiss leftover error modals
        close_modal_btn = self.page.locator(
            'button:has-text("Close"), div[role="button"]:has-text("Close")'
        ).first
        if close_modal_btn.is_visible():
            try:
                close_modal_btn.click(force=True)
                self.page.wait_for_timeout(500)
            except Exception:
                pass

        # Step 2: Locate target media button
        add_btn = self.page.locator(
            f'div[role="button"]:has-text("{target_text}"), '
            f'button:has-text("{target_text}")'
        ).first

        if not add_btn.is_visible():
            add_btn = self.page.locator(
                'div[role="button"][aria-label*="Add"], button:has-text("Add")'
            ).first

        add_btn.wait_for(state="visible", timeout=15_000)

        # Step 3: Intercept FileChooser (suppresses OS dialog)
        _log.info("Triggering %r with FileChooser interception...", target_text)
        with self.page.expect_file_chooser(timeout=10_000) as fc_info:
            add_btn.click(force=True)

        file_chooser = fc_info.value
        element_handle = file_chooser.element

        if not element_handle:
            raise RuntimeError(
                "FileChooser intercepted, but no element handle was returned."
            )

        _log.info(
            "FileChooser intercepted — resolving CDP node via global reference..."
        )

        # Step 4: Store handle in window scope to resolve objectId cleanly
        self.page.evaluate("el => { window.__uploadTarget = el; }", element_handle)

        cdp = None
        try:
            cdp = self.page.context.new_cdp_session(self.page)
            eval_res = cdp.send(
                "Runtime.evaluate", {"expression": "window.__uploadTarget"}
            )
            object_id = (eval_res.get("result") or {}).get("objectId")
            if not object_id:
                raise RuntimeError(
                    "CDP Runtime.evaluate did not return objectId for "
                    "window.__uploadTarget"
                )

            node_info = cdp.send("DOM.describeNode", {"objectId": object_id})
            backend_node_id = node_info["node"]["backendNodeId"]

            # Step 5: Inject local file path via backendNodeId (bypasses 50MB limit)
            cdp.send(
                "DOM.setFileInputFiles",
                {"files": [file_path], "backendNodeId": backend_node_id},
            )
        finally:
            if cdp is not None:
                try:
                    cdp.detach()
                except Exception:
                    pass

        # Step 6: Trigger React change event and cleanup reference
        self.page.evaluate(
            """() => {
                if (window.__uploadTarget) {
                    window.__uploadTarget.dispatchEvent(
                        new Event('change', { bubbles: true })
                    );
                    delete window.__uploadTarget;
                }
            }"""
        )

        _log.info(
            "File path successfully injected via CDP. Waiting for upload preview..."
        )
        self.page.wait_for_timeout(8000)
        self.on_media_uploaded(item, is_video=is_video)

    def prepare_caption(self, item: MediaItem) -> str:
        """
        Prefer network-optimized library captions verbatim.

        Truncation applies ONLY to stem/sidecar fallbacks.
        """
        source = getattr(item, "caption_source", "") or ""
        text = (item.caption or "").strip()
        if not text and item.metadata:
            text, source = LocalMediaQueue.resolve_caption(item.path, item.metadata)
        if source in ("fallback", "") and text:
            return LocalMediaQueue.shorten_caption(text)
        return text

    def wait_for_upload_complete(self, *, max_wait_s: int = 60) -> None:
        """
        Poll until the Meta upload progress bar hits 100% or disappears.

        Must run BEFORE the first Reel-wizard ``Next`` click — advancing early
        skips the Share/Schedule tab and breaks the date toggle.
        """
        if self.dry_run:
            return
        _log.info("Waiting for video upload to reach 100%%...")
        progress = self.page.locator('div[role="progressbar"]')
        pct_100 = self.page.locator('div:has-text("100%")')
        uploading_text = self.page.locator(
            'text=/uploading|processing|\\d+%/i'
        )

        for i in range(max_wait_s):
            try:
                if pct_100.first.is_visible(timeout=300):
                    _log.info("Upload indicator shows 100%% (t=%ds).", i)
                    break
            except Exception:
                pass

            progress_visible = False
            try:
                progress_visible = progress.first.is_visible(timeout=300)
            except Exception:
                progress_visible = False

            still_uploading = False
            try:
                still_uploading = uploading_text.first.is_visible(timeout=300)
            except Exception:
                still_uploading = False

            # Progress bar gone AND no "Uploading xx%" text → treat as done
            # (but only after we've waited at least a couple seconds so we
            # don't race the bar's initial mount).
            if i >= 3 and not progress_visible and not still_uploading:
                _log.info(
                    "Upload progress bar gone / no uploading text (t=%ds).", i
                )
                break

            self.page.wait_for_timeout(1_000)
        else:
            _log.warning(
                "Upload did not report 100%% within %ds — continuing with buffer.",
                max_wait_s,
            )

        # Extra safety buffer after 100% so Meta finishes DOM shift.
        self.page.wait_for_timeout(3_000)
        _log.info("Upload wait complete — safe to advance wizard.")

    def wait_for_schedule_screen(self, *, timeout_ms: int = 15_000) -> None:
        """
        Wait until the Share tab exposes scheduling controls.

        Reel wizard uses a segmented ``Share now | Schedule`` control under
        ``Scheduling options`` (not always a ``Set date and time`` switch).
        """
        if self.dry_run:
            return
        _log.info("Waiting for Share/Schedule tab controls (≤%dms)...", timeout_ms)
        deadline = time.time() + (timeout_ms / 1000.0)
        last_err: Exception | None = None
        while time.time() < deadline:
            for finder in (
                lambda: self.page.locator("text=Scheduling options").first,
                lambda: self.page.locator(
                    'div[role="button"]:has-text("Share now")'
                ).first,
                lambda: self.page.locator(
                    'div[role="button"]:has-text("Schedule")'
                ).first,
                lambda: self.page.locator("text=Set date and time").first,
                lambda: self.page.get_by_role(
                    "switch", name="Set date and time"
                ).first,
            ):
                try:
                    loc = finder()
                    if loc.is_visible(timeout=500):
                        _log.info("Schedule screen ready.")
                        return
                except Exception as exc:
                    last_err = exc
                    continue
            self.page.wait_for_timeout(500)
        raise RuntimeError(
            "Share tab did not expose Scheduling options / Schedule control "
            f"within {timeout_ms}ms. Last error: {last_err}"
        )

    def _locate_wizard_footer_action(self) -> "Locator":
        """
        Locate the bottom-right wizard primary action (Next / Share / Schedule).

        Meta Reels footer is ``<div role="button">…Schedule…</div>``. Prefer
        Schedule → Share → Next; always use ``.last`` so the Scheduling-options
        chip is not clicked.
        """
        page = self.page
        # Footer Schedule is a div[role=button], not a native <button>.
        selectors = [
            'div[role="button"]:has-text("Schedule")',
            'div[role="button"]:has-text("Share")',
            'div[role="button"]:has-text("Next")',
            'button:has-text("Schedule")',
            'button:has-text("Share")',
            'button:has-text("Next")',
        ]

        for sel in selectors:
            btn = page.locator(sel).last
            try:
                if btn.is_visible(timeout=1_500):
                    btn.scroll_into_view_if_needed()
                    for _ in range(12):
                        try:
                            if btn.get_attribute("aria-disabled") != "true":
                                break
                        except Exception:
                            break
                        page.wait_for_timeout(500)
                    try:
                        label = (btn.inner_text(timeout=800) or "").strip()
                    except Exception:
                        label = sel
                    _log.debug(
                        "Footer action resolved via %r (label=%r).",
                        sel,
                        label[:40],
                    )
                    return btn
            except Exception:
                continue

        # Explicit fallback wait for the Schedule div button
        page.wait_for_selector(
            'div[role="button"]:has-text("Schedule")',
            timeout=12_000,
        )
        btn = page.locator('div[role="button"]:has-text("Schedule")').last
        btn.scroll_into_view_if_needed()
        return btn

    def _locate_wizard_footer_next(self) -> "Locator":
        """Backward-compatible alias for ``_locate_wizard_footer_action``."""
        return self._locate_wizard_footer_action()

    def _click_wizard_footer_action(self, *, step_label: str) -> None:
        """Click the modal footer primary action and wait for tab animation."""
        _log.info("Clicking wizard footer action (%s)...", step_label)
        btn = self._locate_wizard_footer_action()
        try:
            label = (btn.inner_text(timeout=1_000) or "").strip().replace("\n", " ")
            _log.info("Footer action label: %r", label[:60])
        except Exception:
            pass
        btn.scroll_into_view_if_needed()
        btn.click(force=True)
        # Allow Meta's wizard step to transition (Create → Edit → Share)
        self.page.wait_for_timeout(2_000)

    def _click_wizard_footer_next(self, *, step_label: str) -> None:
        """Alias kept for callers — footer may show Next, Share, or Schedule."""
        self._click_wizard_footer_action(step_label=step_label)

    def _wizard_step_signals(self) -> dict[str, bool]:
        """Snapshot which Reel wizard stage markers are currently visible."""
        dialog = self.page.locator('div[role="dialog"]').last
        signals = {
            "create": False,
            "edit": False,
            "share": False,
            "schedule": False,
            "describe": False,
            "footer_share_or_schedule": False,
        }

        def _visible(locator: "Locator") -> bool:
            try:
                return bool(locator.first.is_visible(timeout=400))
            except Exception:
                return False

        signals["create"] = _visible(
            dialog.locator(
                '[role="tab"][aria-selected="true"]:has-text("Create"), '
                '[aria-selected="true"]:has-text("Create")'
            )
        )
        signals["edit"] = (
            _visible(
                dialog.locator(
                    '[role="tab"][aria-selected="true"]:has-text("Edit"), '
                    '[aria-selected="true"]:has-text("Edit")'
                )
            )
            or _visible(dialog.get_by_text("Edit cover", exact=False))
            or _visible(dialog.get_by_text("Edit video", exact=False))
        )
        signals["share"] = _visible(
            dialog.locator(
                '[role="tab"][aria-selected="true"]:has-text("Share"), '
                '[aria-selected="true"]:has-text("Share")'
            )
        )
        signals["schedule"] = _visible(
            self.page.locator("text=Set date and time")
        )
        signals["describe"] = _visible(
            self.page.locator(
                'div[role="textbox"][aria-label*="Describe your reel" i]'
            )
        )
        # Footer primary already flipped to Share/Schedule ⇒ past Create.
        try:
            footer = self._locate_wizard_footer_action()
            text = (footer.inner_text(timeout=800) or "").lower()
            signals["footer_share_or_schedule"] = (
                "share" in text or "schedule" in text
            ) and "next" not in text.split()
        except Exception:
            signals["footer_share_or_schedule"] = False
        return signals

    def _verify_wizard_advanced_past_create(self, *, timeout_ms: int = 12_000) -> bool:
        """
        Soft check that the wizard left Create after the first footer click.

        Returns True if Edit / Share / Schedule (or the date toggle) appears.
        Never raises — inconclusive advancement only logs a warning so the
        next footer action (Share/Schedule) can still proceed.
        """
        if self.dry_run:
            return True
        _log.info("Verifying wizard advanced past Create (≤%dms)...", timeout_ms)
        deadline = time.time() + (timeout_ms / 1000.0)
        while time.time() < deadline:
            now = self._wizard_step_signals()
            advanced = (
                now["edit"]
                or now["share"]
                or now["schedule"]
                or now["footer_share_or_schedule"]
            )
            if advanced:
                _log.info(
                    "Wizard step advanced: edit=%s share=%s schedule=%s "
                    "footer_share_or_schedule=%s",
                    now["edit"],
                    now["share"],
                    now["schedule"],
                    now["footer_share_or_schedule"],
                )
                return True
            self.page.wait_for_timeout(500)

        now = self._wizard_step_signals()
        if now["edit"] or now["share"] or now["schedule"] or now["footer_share_or_schedule"]:
            return True
        _log.warning(
            "Wizard advance past Create inconclusive (signals=%s). "
            "Continuing — next footer click may be Share/Schedule.",
            now,
        )
        return False

    def advance_composer(self) -> None:
        """
        Wait for 100% upload, click footer Next/Share until the
        Share/Schedule tab exposes scheduling controls.

        Meta renames the footer control from ``Next`` → ``Share``/``Schedule``.
        Stop early once ``Set date and time`` is visible so the final
        ``Schedule`` click is left to ``confirm_schedule()``.
        """
        self.wait_for_upload_complete()
        if self.dry_run:
            return

        for i in range(1, self.next_clicks + 1):
            # Already on the Share tab with Scheduling options? Stop advancing
            # so enable_schedule_and_fill() can click the Schedule chip.
            on_share_tab = False
            for sel in (
                "text=Scheduling options",
                'div[role="button"]:has-text("Share now")',
                "text=Set date and time",
            ):
                try:
                    if self.page.locator(sel).first.is_visible(timeout=700):
                        on_share_tab = True
                        break
                except Exception:
                    continue
            if on_share_tab:
                _log.info(
                    "Share/Scheduling options already visible — "
                    "stopping wizard advances."
                )
                break

            self._click_wizard_footer_action(step_label=f"{i}/{self.next_clicks}")
            if i == 1 and self.next_clicks >= 2:
                self._verify_wizard_advanced_past_create(timeout_ms=12_000)

        # Final wizard step must expose scheduling controls.
        self.wait_for_schedule_screen(timeout_ms=15_000)

    # ---- shared steps ---------------------------------------------------

    def navigate_home(self) -> None:
        _log.info("Navigating to Business Suite home...")
        if self.dry_run:
            return
        self.page.goto(BUSINESS_SUITE_HOME, wait_until="domcontentloaded")
        try:
            self.page.wait_for_load_state("networkidle", timeout=config.LONG_TIMEOUT_MS)
        except Exception:
            pass
        self.page.wait_for_timeout(2_000)
        self.hb.pause(0.8, 1.5)

    def open_create_post(self) -> None:
        """
        Click Create post, then confirm the composer is ready by waiting for
        the ``Add photo/video`` upload control (not ``div[role=dialog]``,
        which is unreliable / often missing in Business Suite layouts).
        """
        _log.info("Opening universal Create post composer...")
        if self.dry_run:
            return

        ensure_page_active(self.page)

        upload_ready = self.page.locator(
            'div[role="button"]:has-text("Add photo/video")'
        ).first
        file_input = self.page.locator('input[type="file"]').first

        # Already-open composer? Upload control visible OR hidden file input.
        try:
            if upload_ready.is_visible(timeout=1_500) or file_input.count() > 0:
                _log.info(
                    "Composer already open (Add photo/video / file input) — "
                    "skipping Create post click."
                )
                return
        except Exception:
            pass

        btn = try_first_visible(
            self.page,
            _CREATE_POST_CANDIDATES,
            timeout_each_ms=config.COMPOSER_CANDIDATE_TIMEOUT_MS,
            label="Create post",
        )
        try:
            self.hb.click(btn)
        except Exception:
            btn.click(force=True)
        self.hb.pause(1.0, 2.0)
        ensure_page_active(self.page)

        # Composer-open confirmation: visible Add control OR attached file input.
        deadline = time.time() + 15.0
        while time.time() < deadline:
            try:
                if upload_ready.is_visible(timeout=400):
                    _log.info("Composer ready — Add photo/video is visible.")
                    return
            except Exception:
                pass
            try:
                if file_input.count() > 0:
                    _log.info(
                        "Composer ready — hidden input[type=file] attached."
                    )
                    return
            except Exception:
                pass
            self.page.wait_for_timeout(400)

        upload_ready.wait_for(state="visible", timeout=5_000)
        _log.info("Composer ready — Add photo/video is visible.")

    def fill_caption(self, caption: str) -> None:
        """
        Fill caption into Meta's Universal Composer editor (full text).

        Background-safe: polls caption selectors while video processes, prefers
        a visible editor when available, fills via ``force`` click + ``fill`` /
        JS ``dispatchEvent`` (no OS keyboard focus).
        """
        caption_text = (caption or "").strip()
        _log.info("Filling caption (%d chars)...", len(caption_text))
        if self.dry_run or not caption_text:
            return

        caption_selectors = [
            'div[role="textbox"][aria-label*="Describe your reel" i]',
            'div[contenteditable="true"][role="textbox"]',
            'div[contenteditable="true"]',
            'div[aria-label*="caption" i]',
            'div[aria-label*="description" i]',
            "textarea",
            'div[role="textbox"]',
        ]

        textbox = None
        attached_fallback = None
        # Poll up to 20s — caption often mounts only after upload processing.
        deadline = time.time() + 20.0
        while time.time() < deadline and textbox is None:
            for selector in caption_selectors:
                loc = self.page.locator(selector).first
                try:
                    if loc.count() == 0:
                        continue
                    if loc.is_visible(timeout=300):
                        textbox = loc
                        _log.info("Caption field visible via %r", selector)
                        break
                    if attached_fallback is None:
                        attached_fallback = loc
                except Exception:
                    continue
            if textbox is None:
                self.page.wait_for_timeout(400)

        if textbox is None and attached_fallback is not None:
            textbox = attached_fallback
            _log.info("Caption field using attached (not yet visible) fallback.")

        if textbox is None:
            textbox = self.page.get_by_role("textbox").first
            textbox.wait_for(state="attached", timeout=15_000)

        try:
            textbox.click(force=True)
            self.page.wait_for_timeout(300)
            textbox.fill(caption_text)
        except Exception as exc:
            _log.warning(
                "Standard fill fallback to JS text injection: %s", exc
            )
            textbox.evaluate(
                """(el, text) => {
                    el.focus();
                    if (el.tagName === 'TEXTAREA' || el.tagName === 'INPUT') {
                        el.value = text;
                    } else {
                        el.innerText = text;
                    }
                    el.dispatchEvent(new Event('input', { bubbles: true }));
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                }""",
                caption_text,
            )

        _log.info("Caption filled successfully.")
        self.hb.pause(0.6, 1.2)

    def enable_schedule_and_fill(self, scheduled_dt: datetime) -> None:
        """
        On the Reel Share tab: select the segmented ``Schedule`` option under
        ``Scheduling options``, then fill date/time inputs.

        Meta does **not** use a ``Set date and time`` switch here — it uses
        ``Share now | Schedule`` radio-style ``div[role=button]`` chips.
        """
        _log.info(
            "Enabling Schedule option + filling datetime for %s...",
            scheduled_dt.strftime(_HISTORY_DT_FMT),
        )
        if self.dry_run:
            return

        self.wait_for_schedule_screen(timeout_ms=15_000)
        self._scroll_composer_panel()

        # ---- 1. Click segmented "Schedule" (not footer; use .first) ----
        page = self.page
        schedule_option_btn = None

        # Prefer the chip inside the Scheduling options section.
        scoped = page.locator(
            'div:has-text("Scheduling options") >> div[role="button"]:has-text("Schedule")'
        )
        try:
            if scoped.count() > 0 and scoped.first.is_visible(timeout=2_000):
                schedule_option_btn = scoped.first
        except Exception:
            schedule_option_btn = None

        if schedule_option_btn is None:
            schedule_option_btn = page.locator(
                'div[role="button"]:has-text("Schedule")'
            ).first
            try:
                if not schedule_option_btn.is_visible(timeout=1_500):
                    raise RuntimeError("not visible")
            except Exception:
                page.wait_for_selector(
                    'div[role="button"]:has-text("Schedule")',
                    timeout=10_000,
                )
                schedule_option_btn = page.locator(
                    'div[role="button"]:has-text("Schedule")'
                ).first

        schedule_option_btn.scroll_into_view_if_needed()
        schedule_option_btn.click(force=True)
        _log.info("Clicked Scheduling options → Schedule.")
        page.wait_for_timeout(1_500)  # wait for date/time inputs to render

        # ---- 2. Fill date + time (never hard-fail — proceed to footer Schedule) ----
        self._fill_datetime_fields(scheduled_dt)
        page.wait_for_timeout(1_000)

    def confirm_schedule(self) -> None:
        """
        Click the bottom-right footer ``div[role=button]`` containing
        ``Schedule`` (or Share fallback), then wait 6s for Meta to process.
        """
        _log.info("Clicking final Schedule action button in footer...")
        if self.dry_run:
            return

        final_btn = self._locate_wizard_footer_action()
        try:
            label = (final_btn.inner_text(timeout=1_000) or "").strip().replace("\n", " ")
            _log.info("Final footer action label: %r", label[:60])
        except Exception:
            pass

        final_btn.scroll_into_view_if_needed()
        final_btn.click(force=True)
        _log.info("Clicked final Schedule action button in footer.")

        # Allow Meta to process the network request while unfocused/throttled.
        self.page.wait_for_timeout(6_000)
        try:
            dialog = self.page.locator('div[role="dialog"]').last
            if dialog.is_visible(timeout=1_500):
                _log.info(
                    "Composer dialog still visible after Schedule — "
                    "treating as submitted (optimistic)."
                )
            else:
                _log.info("Composer dialog closed — schedule confirmed.")
        except Exception:
            _log.info("Composer dialog gone / detached — schedule confirmed.")
        self.hb.pause(1.0, 2.0)

    def reset_page(self) -> None:
        """Dismiss stuck modals and return to Business Suite home."""
        _log.info("Resetting page after error...")
        if self.dry_run:
            return
        try:
            for _ in range(3):
                self.page.keyboard.press("Escape")
                self.page.wait_for_timeout(400)
        except Exception:
            pass
        try:
            self.navigate_home()
        except Exception as exc:
            _log.warning("Home navigation during reset failed: %s", exc)

    # ---- internals ------------------------------------------------------

    def _scroll_composer_panel(self) -> None:
        scroll_js = """
        (() => {
            const candidates = [
                document.querySelector('div[role="dialog"]'),
                document.querySelector('form'),
            ];
            for (const el of candidates) {
                if (el && el.scrollHeight > el.clientHeight) {
                    el.scrollTop += 500;
                    return 'scrolled';
                }
            }
            window.scrollBy(0, 400);
            return 'window';
        })()
        """
        try:
            self.page.evaluate(scroll_js)
        except Exception:
            pass
        time.sleep(0.4)

    def _find_schedule_toggle(self) -> "Locator | None":
        # Prefer accessible role name (English Meta UI).
        try:
            loc = self.page.get_by_role("switch", name="Set date and time").first
            loc.wait_for(state="attached", timeout=3_000)
            return loc
        except Exception:
            pass
        try:
            loc = self.page.get_by_role("switch", name=re.compile(r"date and time", re.I)).first
            loc.wait_for(state="attached", timeout=2_500)
            return loc
        except Exception:
            pass
        for sel in _SCHEDULE_TOGGLE_SELS:
            try:
                loc = self.page.locator(sel).first
                loc.wait_for(state="attached", timeout=2_500)
                return loc
            except Exception:
                continue
        try:
            # Click the visible label text — often toggles the adjacent switch.
            label = self.page.locator("text=Set date and time").first
            label.wait_for(state="visible", timeout=3_000)
            return label
        except Exception:
            return None

    def _toggle_is_on(self, toggle: "Locator") -> bool:
        try:
            aria = (toggle.get_attribute("aria-checked") or "").lower()
            if aria in ("true", "false"):
                return aria == "true"
        except Exception:
            pass
        try:
            return bool(toggle.is_checked())
        except Exception:
            return False

    def _set_input_value(self, loc: "Locator", value: str) -> None:
        """
        Background-safe input set: ``fill()`` first, then JS value assignment.

        Avoids ``page.keyboard`` / Tab — those drop when the OS window is
        unfocused.
        """
        try:
            loc.fill(value)
            return
        except Exception:
            pass
        loc.evaluate(
            """(el, v) => {
                el.focus();
                const proto = window.HTMLInputElement
                    ? window.HTMLInputElement.prototype
                    : null;
                const desc = proto
                    ? Object.getOwnPropertyDescriptor(proto, 'value')
                    : null;
                if (desc && desc.set) {
                    desc.set.call(el, v);
                } else {
                    el.value = v;
                }
                el.dispatchEvent(new Event('input', { bubbles: true }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
            }""",
            value,
        )

    def _fill_datetime_fields(self, dt: datetime) -> None:
        """
        Fill Meta Reels Schedule date + hours/minutes/meridiem spinbuttons.

        Background-safe (no Tab / keyboard shortcuts):
        * Date: ``input[placeholder="mm/dd/yyyy"]`` via ``fill()`` / JS
        * Time: ``role=spinbutton`` inside the ``Time input`` container

        Never raises — missing fields are logged; caller still clicks footer
        Schedule.
        """
        page = self.page
        formatted_date = dt.strftime("%m/%d/%Y")
        hours_val = dt.strftime("%I").lstrip("0") or "12"
        mins_val = dt.strftime("%M")
        ampm_val = dt.strftime("%p")

        # --- 1. DATE INPUT (no Tab / Escape / OS focus) ---
        date_input = page.locator(
            'input[placeholder="mm/dd/yyyy"], input[aria-label*="Date" i]'
        ).first
        try:
            if date_input.is_visible(timeout=5_000):
                date_input.click(force=True)
                self._set_input_value(date_input, formatted_date)
                _log.info("Date input filled: %s", formatted_date)
                page.wait_for_timeout(random.randint(800, 1500))
            else:
                _log.warning("Date input (mm/dd/yyyy) not visible — skipping.")
        except Exception as exc:
            _log.warning("Date input fill failed: %s", exc)

        # --- 2. TIME SPINBUTTONS (inside "Time input" when possible) ---
        time_container = page.locator(
            '[aria-label="Time input"], div:has-text("Time input")'
        ).last
        use_time_scope = False
        try:
            time_container.wait_for(state="attached", timeout=3_000)
            use_time_scope = True
        except Exception:
            use_time_scope = False

        def _spin(aria: str) -> "Locator":
            sel = f'input[role="spinbutton"][aria-label="{aria}"]'
            if use_time_scope:
                try:
                    scoped = time_container.locator(sel)
                    if scoped.count() > 0:
                        return scoped.first
                except Exception:
                    pass
            return page.locator(sel).first

        hours_input = _spin("hours")
        try:
            if hours_input.is_visible(timeout=5_000):
                hours_input.click(force=True)
                self._set_input_value(hours_input, hours_val)
                _log.info("Hours spinbutton filled: %s", hours_val)
                page.wait_for_timeout(random.randint(600, 1200))
            else:
                _log.warning("Hours spinbutton not visible — skipping.")
        except Exception as exc:
            _log.warning("Hours spinbutton fill failed: %s", exc)

        mins_input = _spin("minutes")
        try:
            if mins_input.is_visible(timeout=3_000):
                mins_input.click(force=True)
                self._set_input_value(mins_input, mins_val)
                _log.info("Minutes spinbutton filled: %s", mins_val)
                page.wait_for_timeout(random.randint(500, 1100))
            else:
                _log.warning("Minutes spinbutton not visible — skipping.")
        except Exception as exc:
            _log.warning("Minutes spinbutton fill failed: %s", exc)

        ampm_input = _spin("meridiem")
        try:
            if ampm_input.is_visible(timeout=3_000):
                ampm_input.click(force=True)
                self._set_input_value(ampm_input, ampm_val)
                _log.info("Meridiem spinbutton filled: %s", ampm_val)
                page.wait_for_timeout(random.randint(1000, 2000))
            else:
                _log.warning("Meridiem spinbutton not visible — skipping.")
        except Exception as exc:
            _log.warning("Meridiem spinbutton fill failed: %s", exc)

    # ---- end-to-end item + run loop -------------------------------------

    def schedule_item(self, item: MediaItem) -> datetime:
        """
        Full composer flow for one media item. Returns the scheduled datetime.
        Does NOT update the local queue — caller marks success after return.
        """
        scheduled_dt = self.queue.next_schedule_datetime()
        _log.info(
            "Scheduling %s [%s] for %s",
            item.filename,
            self.format_type,
            scheduled_dt.strftime(_HISTORY_DT_FMT),
        )

        ensure_page_active(self.page)
        # Route video → reels_composer, photo → Universal /latest/
        self.prepare_composer_for_item(item)
        if not is_video_file(item.path):
            # Photos still need the Create-post modal on /latest/
            self.open_create_post()
        self.select_format()
        self.upload_media(item)
        self.fill_caption(self.prepare_caption(item))
        self.advance_composer()
        self.enable_schedule_and_fill(scheduled_dt)
        self.confirm_schedule()

        # Brief settle — treat click success + settle as confirmation for
        # queue update (banner polling is unreliable in Business Suite).
        if not self.dry_run:
            wait_s = round(random.uniform(3.0, 8.0), 2)
            _log.info("Schedule submitted — settling %.1fs...", wait_s)
            self.page.wait_for_timeout(int(wait_s * 1000))

        return scheduled_dt

    def run(self) -> dict[str, int]:
        """Scan the channel queue and schedule pending media files."""
        items = self.queue.scan_pending(format_type=self.format_type)
        if self.max_items is not None:
            items = items[: max(0, self.max_items)]

        stats = {"total": len(items), "scheduled": 0, "failed": 0, "skipped": 0}
        if not items:
            print(f"[MediaScheduler] No pending {self.format_type}s for '{self.channel_name}'.")
            return stats

        print(
            f"[MediaScheduler] {len(items)} pending {self.format_type}(s) "
            f"for channel '{self.channel_name}'."
        )

        for i, item in enumerate(items, 1):
            print(f"\n--- [{i}/{len(items)}] {item.filename} ---")
            try:
                scheduled_dt = self.schedule_item(item)
                # QUEUE INTEGRITY: mark/move ONLY after the full schedule path
                # returns. Upload failures (incl. OS file-dialog errors) raise
                # inside schedule_item and must never reach mark_scheduled, so
                # the same file stays pending for the next run.
                self.queue.mark_scheduled(item, scheduled_dt, dry_run=self.dry_run)
                stats["scheduled"] += 1
                _log.info("OK: %s → %s", item.filename, scheduled_dt)
                if i < len(items):
                    self.hb.post_cooldown()
            except Exception as exc:
                # Do NOT update facebook_history.json or move the file.
                stats["failed"] += 1
                _log.error("FAILED %s: %s", item.filename, exc, exc_info=True)
                try:
                    save_screenshot(
                        self.page,
                        f"{self.format_type}_error_{item.path.stem[:40]}",
                    )
                except Exception:
                    pass
                self.reset_page()
                continue

        return stats

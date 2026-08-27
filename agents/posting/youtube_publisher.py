"""YouTube Shorts auto-publisher with per-page OAuth token isolation.

Authentication flow
-------------------
1. Tokens live under ``credentials/tokens/youtube_token_{page_name}.json``
   (e.g. ``youtube_token_master_mei.json`` vs ``youtube_token_ancient_knowledge.json``).
2. Pages NEVER share tokens — there is no fallback to a root ``token.json``.
3. If a page token is missing/invalid, ``InstalledAppFlow`` opens the browser
   once so the operator can pick that page's Brand Account / channel.
4. Before every ``videos().insert``, the authorised channel is fetched and
   logged so uploads cannot silently land on the wrong channel.

Smart drip scheduler
--------------------
When uploading multiple videos (bulk run), they are staggered via
``POST_INTERVAL_HOURS`` (default 6 h) using ``privacyStatus=private`` +
``publishAt``.

Security note
-------------
``client_secret*.json`` and ``credentials/tokens/youtube_token_*.json`` are
gitignored and must NEVER be committed.
"""

from __future__ import annotations

import json
import logging
import os
import re as _re
import shutil
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

_LOG = logging.getLogger(__name__)


class YouTubeQuotaExceededError(RuntimeError):
    """Raised when YouTube's ~20 videos/day channel upload cap (or a related
    quota/rate error) is hit mid-upload. Callers should catch this distinctly
    from other upload failures: queue the remaining videos via
    ``queue_pending_upload`` / ``queue_pending_upload_from_envelope`` and let
    the pipeline complete gracefully instead of crashing."""


class YouTubeChannelMismatchError(RuntimeError):
    """Raised when the OAuth token is authorized for the wrong Brand Account."""


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SCOPES = [
    # Full channel management scope — required for upload, playlists, and
    # playlistItems.insert().  The narrower youtube.upload scope causes 403
    # Forbidden on any playlist or channel-management API call.
    "https://www.googleapis.com/auth/youtube",
]
_CHUNK_SIZE = 8 * 1024 * 1024  # 8 MB resumable chunk

_ENGINE_ROOT: Path = Path(__file__).resolve().parents[2]
_DEFAULT_CLIENT_SECRETS: Path = _ENGINE_ROOT / "client_secret.json"
_DEFAULT_TOKEN_DIR: Path = _ENGINE_ROOT / "credentials" / "tokens"
_DEFAULT_PENDING_QUEUE_PATH: Path = _ENGINE_ROOT / "credentials" / "pending_youtube_uploads.json"
_QUEUE_LOCK = threading.Lock()

# YouTube channel upload reasons that map to the daily video-count cap
# (or a closely-related quota/rate condition). Anything in this set — or a
# RESOURCE_EXHAUSTED status — is treated as "daily quota reached", not a
# generic upload failure.
_UPLOAD_LIMIT_REASONS: frozenset[str] = frozenset({
    "uploadLimitExceeded",
    "quotaExceeded",
    "dailyLimitExceeded",
    "rateLimitExceeded",
})
_UPLOAD_LIMIT_PHRASES: tuple[str, ...] = (
    "upload limit",
    "exceeded the number of videos",
    "daily limit",
)

_DEFAULT_CATEGORY_ID = "27"  # Education
_DEFAULT_PRIVACY = "private"  # Scheduler always uses private+publishAt
POST_INTERVAL_HOURS = int(os.getenv("YT_POST_INTERVAL_HOURS", "6"))  # 2 posts/day
_DEFAULT_BASE_HOUR_UTC = 12  # 12:00 UTC when no prior slot found

# Global per-execution / per-channel-run upload safety cap.
# Proactive client-side gate (independent of YouTube API quota errors).
# Override via env YT_MAX_DAILY_UPLOADS or CLI --limit N.
MAX_DAILY_UPLOADS: int = int(os.getenv("YT_MAX_DAILY_UPLOADS", "20"))
_GLOBAL_SAFETY_NOTICE = (
    "[GLOBAL SAFETY] Reached daily upload quota cap ({limit} videos). "
    "Remaining queue deferred for next run."
)


def resolve_daily_upload_limit(override: Optional[int] = None) -> int:
    """Return the effective upload cap for this execution.

    * ``None`` → ``MAX_DAILY_UPLOADS`` (default 20).
    * CLI ``--limit N`` → ``N`` (``0`` means allow no uploads).
    """
    if override is None:
        return max(0, int(MAX_DAILY_UPLOADS))
    return max(0, int(override))


class DailyUploadSafetyGate:
    """Track successful uploads in one scheduling run and hard-stop at the cap.

    Callers check ``can_upload()`` before each upload attempt, call
    ``record_success()`` after a confirmed upload, and on halt leave remaining
    staging-queue items untouched for the next daily run.
    """

    def __init__(self, limit: Optional[int] = None) -> None:
        self.limit = resolve_daily_upload_limit(limit)
        self.successful = 0
        self.halted = False

    def can_upload(self) -> bool:
        if self.halted:
            return False
        return self.successful < self.limit

    def record_success(self) -> None:
        self.successful += 1
        if self.successful >= self.limit:
            self.halted = True

    def notify_halt(self) -> None:
        """Print/log the non-blocking global-safety notice once."""
        msg = _GLOBAL_SAFETY_NOTICE.format(limit=self.limit)
        print(msg)
        _LOG.warning(msg)

    def remaining_capacity(self) -> int:
        return max(0, self.limit - self.successful)

# ---------------------------------------------------------------------------
# Per-page default playlist titles / descriptions
# ---------------------------------------------------------------------------
# Master Mei | Mind Control — Stoic finance playlist (page-exclusive).
_MASTER_MEI_PLAYLIST_TITLE = "Stoic Financial Freedom & Wealth Mindset"
_MASTER_MEI_PLAYLIST_DESCRIPTION = (
    "Welcome to the official Master Mei playlist on Stoic discipline, financial clarity, "
    "and executive mindset. In this series, Master Mei breaks down ancient Stoic philosophy "
    "to help you build unbreakable mental toughness, eliminate distractions, control your "
    "emotions, and make strategic financial decisions. Learn how to master self-discipline, "
    "build long-term wealth, and lead with quiet power in business and life. "
    "Subscribe to Master Mei | Mind Control for daily wisdom on Stoicism, business strategy, "
    "and personal mastery."
)

_PAGE_DEFAULT_PLAYLISTS: dict[str, str] = {
    "ancient_knowledge": "Ancient Mysteries & Forbidden History",
    "master_mei": _MASTER_MEI_PLAYLIST_TITLE,
    "wonder_feed": "",
    "anna_protocol": "",
    # Three ACT playlists are created by wealth_main.py — do not auto-dump here.
    "principles_of_wealth_finance_economics": "",
    "endless_summer_paradise": (
        "Endless Summer Paradise — 1950s Surreal Architecture & Retro Dreamscapes"
    ),
}

_PAGE_PLAYLIST_DESCRIPTIONS: dict[str, str] = {
    "master_mei": _MASTER_MEI_PLAYLIST_DESCRIPTION,
}

# Expected channel title substrings (enforce on upload; mismatch → token wipe + re-auth).
# Master Mei matches "Master Mei" / "Master Mei | Mind Control" — rejects Ancient Knowledge tokens.
_PAGE_EXPECTED_CHANNEL_HINTS: dict[str, str] = {
    "master_mei": "Master Mei",
    "ancient_knowledge": "Ancient Knowledge",
    "principles_of_wealth_finance_economics": "Principles of Wealth",
    "endless_summer_paradise": "Endless Summer Paradise",
}

# Legacy Master Mei playlist titles — never assign new uploads here.
_MASTER_MEI_DEPRECATED_PLAYLISTS: frozenset[str] = frozenset({
    "master mei — warrior discipline",
    "master mei - warrior discipline",
    "warrior discipline",
})

_PLAYLIST_ID_CACHE: dict[str, str] = {}


# ---------------------------------------------------------------------------
# Auth helpers — multi-channel token isolation
# ---------------------------------------------------------------------------

def _sanitize_page_name(page_name: str) -> str:
    slug = (page_name or "default").strip().lower()
    slug = _re.sub(r"[^a-z0-9_\-]+", "_", slug).strip("_")
    return slug or "default"


def default_token_dir() -> Path:
    """Return the dedicated per-page YouTube token directory."""
    try:
        import config as app_config  # local import avoids circulars at module load

        override = getattr(app_config, "YOUTUBE_TOKEN_DIR", None)
        if override:
            return Path(override)
    except Exception:  # noqa: BLE001
        pass
    return _DEFAULT_TOKEN_DIR


def _is_upload_limit_error(exc: BaseException) -> bool:
    """
    True for YouTube's ~20 videos/day channel upload cap, or a closely-related
    quota/rate condition (``uploadLimitExceeded`` / ``quotaExceeded`` /
    ``dailyLimitExceeded`` / ``RESOURCE_EXHAUSTED``). Inspects the structured
    JSON error body on ``HttpError``/``ResumableUploadError`` first, then falls
    back to scanning the raw message text for known phrases.
    """
    content = getattr(exc, "content", None)
    if content:
        try:
            raw = content.decode("utf-8") if isinstance(content, bytes) else str(content)
            payload = json.loads(raw)
            err_body = payload.get("error") or {}
            for item in err_body.get("errors") or []:
                if isinstance(item, dict) and item.get("reason") in _UPLOAD_LIMIT_REASONS:
                    return True
            if err_body.get("status") == "RESOURCE_EXHAUSTED":
                return True
        except Exception:  # noqa: BLE001
            pass
    msg = str(exc)
    msg_low = msg.lower()
    if any(reason.lower() in msg_low for reason in _UPLOAD_LIMIT_REASONS):
        return True
    return any(phrase in msg_low for phrase in _UPLOAD_LIMIT_PHRASES)


def _resolve_client_secrets(path: Optional[str | Path] = None) -> Path:
    target = Path(path) if path else _DEFAULT_CLIENT_SECRETS
    if not target.is_file():
        raise FileNotFoundError(
            f"Google OAuth2 client-secrets file not found: {target}\n"
            "Download it from Google Cloud Console → APIs & Services → Credentials."
        )
    return target


def resolve_youtube_token_path(
    page_name: str,
    token_dir: Optional[str | Path] = None,
) -> Path:
    """Return ``…/credentials/tokens/youtube_token_{page}.json`` (no shared root token)."""
    base = Path(token_dir) if token_dir else default_token_dir()
    return base / f"youtube_token_{_sanitize_page_name(page_name)}.json"


def _legacy_token_candidates(page_name: str) -> list[Path]:
    """Same-page legacy paths only — never another page's token or bare token.json."""
    slug = _sanitize_page_name(page_name)
    return [
        _ENGINE_ROOT / "channels_config" / slug / f"token_{slug}.json",
        _ENGINE_ROOT / f"token_{slug}.json",
        _ENGINE_ROOT / "credentials" / f"token_{slug}.json",
        _ENGINE_ROOT / f"youtube_token_{slug}.json",
    ]


def _maybe_migrate_legacy_token(page_name: str, token_path: Path) -> None:
    """One-time copy of same-page legacy token → isolated credentials/tokens path."""
    if token_path.is_file():
        return
    for legacy in _legacy_token_candidates(page_name):
        if legacy.is_file() and legacy.resolve() != token_path.resolve():
            token_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(legacy, token_path)
            _LOG.info(
                "Migrated same-page YouTube token %s → %s (page=%s)",
                legacy.name,
                token_path,
                _sanitize_page_name(page_name),
            )
            print(
                f"[YouTube Auth] Migrated token for '{page_name}' → {token_path}"
            )
            return


def _persist_token(creds, token_path: Path) -> None:
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(creds.to_json(), encoding="utf-8")


def build_credentials(
    page_name: str,
    client_secrets_path: Optional[str | Path] = None,
    token_dir: Optional[str | Path] = None,
):
    """Load or acquire OAuth2 credentials for *page_name* only.

    * Uses ``credentials/tokens/youtube_token_{page}.json``.
    * Never falls back to a shared ``token.json`` or another page's token.
    * Auto-refreshes when expired; otherwise launches browser consent.
    """
    from google.oauth2.credentials import Credentials  # type: ignore[import]
    from google.auth.transport.requests import Request  # type: ignore[import]
    from google_auth_oauthlib.flow import InstalledAppFlow  # type: ignore[import]

    slug = _sanitize_page_name(page_name)
    token_path = resolve_youtube_token_path(slug, token_dir)
    secrets_path = _resolve_client_secrets(client_secrets_path)
    _maybe_migrate_legacy_token(slug, token_path)

    creds: Optional[Credentials] = None

    if token_path.is_file():
        try:
            creds = Credentials.from_authorized_user_file(str(token_path), _SCOPES)
            _LOG.debug("Loaded OAuth2 token from %s", token_path)
        except Exception as exc:  # noqa: BLE001
            _LOG.warning(
                "Invalid YouTube token at %s (%s) — will re-auth.", token_path, exc
            )
            creds = None

    if creds and creds.expired and creds.refresh_token:
        _LOG.info("OAuth2 token expired for '%s' — refreshing …", slug)
        try:
            creds.refresh(Request())
            _persist_token(creds, token_path)
        except Exception as exc:  # noqa: BLE001
            _LOG.warning(
                "Token refresh failed for '%s' (%s) — launching consent flow.",
                slug,
                exc,
            )
            creds = None

    if not creds or not creds.valid:
        _LOG.info(
            "No valid token for page='%s' at %s — launching browser consent flow …",
            slug,
            token_path,
        )
        print(
            f"\n[YouTube Auth] Opening browser for OAuth2 consent (page: {slug}).\n"
            f"Select the Brand Account / channel for THIS page only.\n"
            f"Token will be saved to: {token_path}\n"
            "Other pages keep their own isolated tokens and will not be overwritten.\n"
        )
        flow = InstalledAppFlow.from_client_secrets_file(str(secrets_path), _SCOPES)
        creds = flow.run_local_server(port=0, open_browser=True)
        _persist_token(creds, token_path)
        _LOG.info("Consent complete — token saved → %s", token_path)
        print(f"[YouTube Auth] ✓ Token saved → {token_path}")

    return creds


def build_youtube_client(creds):
    """Return an authorised YouTube Data API v3 resource."""
    from googleapiclient.discovery import build  # type: ignore[import]
    import googleapiclient.discovery as _disc

    _disc.logger.setLevel(logging.WARNING)
    return build("youtube", "v3", credentials=creds)


# ---------------------------------------------------------------------------
# Channel verification
# ---------------------------------------------------------------------------

def fetch_authorized_channel(youtube) -> dict[str, str]:
    """Return ``{"id", "title", "customUrl"}`` for ``channels().list(mine=True)``."""
    resp = youtube.channels().list(
        part="id,snippet",
        mine=True,
    ).execute()
    items = resp.get("items") or []
    if not items:
        raise RuntimeError(
            "No YouTube channel found for the authenticated account. "
            "Re-run OAuth and select the correct Brand Account."
        )

    ch = items[0]
    snippet = ch.get("snippet") or {}
    return {
        "id": ch.get("id") or "",
        "title": snippet.get("title") or "(untitled)",
        "customUrl": snippet.get("customUrl") or "",
    }


def verify_authorized_channel(
    youtube,
    page_name: str = "",
    *,
    enforce: bool = True,
) -> dict[str, str]:
    """
    Fetch and log the authorised channel before any upload.

    When *enforce* is True and the channel title does not match the page hint
    (e.g. Master Mei token authorized for Ancient Knowledge), raises
    ``YouTubeChannelMismatchError`` so callers can delete the token and re-auth.
    """
    info = fetch_authorized_channel(youtube)
    title = info["title"]
    ch_id = info["id"]
    custom = info["customUrl"]
    slug = _sanitize_page_name(page_name) if page_name else "?"

    msg = (
        f'[YouTube Uploader] Authorized target channel: "{title}" '
        f"(ID: {ch_id})"
        + (f" | {custom}" if custom else "")
    )
    print(msg)
    _LOG.info(
        "YouTube channel verified | page=%s title=%r id=%s customUrl=%s",
        slug,
        title,
        ch_id,
        custom,
    )

    hint = _PAGE_EXPECTED_CHANNEL_HINTS.get(slug, "")
    if hint and hint.lower() not in title.lower():
        warn = (
            f'[YouTube Uploader] CHANNEL MISMATCH: page="{page_name}" expected a channel '
            f'containing "{hint}", but token is authorized for "{title}".'
        )
        print(warn)
        _LOG.warning(warn)
        if enforce:
            raise YouTubeChannelMismatchError(
                f'Page "{slug}" expects channel containing "{hint}", '
                f'but OAuth token is for "{title}".'
            )

    return info


def invalidate_youtube_token(
    page_name: str,
    token_dir: Optional[str | Path] = None,
) -> Path | None:
    """Delete the page-isolated YouTube token so the next auth is a clean OAuth."""
    path = resolve_youtube_token_path(page_name, token_dir)
    if path.is_file():
        try:
            path.unlink()
            _LOG.warning("Deleted mismatched YouTube token → %s", path)
            print(f"[YouTube Auth] Deleted mismatched token → {path}")
            return path
        except OSError as exc:
            _LOG.error("Failed to delete YouTube token %s: %s", path, exc)
    return None


def build_youtube_client_for_page(
    page_name: str,
    client_secrets_path: Optional[str | Path] = None,
    token_dir: Optional[str | Path] = None,
    *,
    enforce_channel: bool = True,
):
    """
    Build an authorised YouTube client and enforce the expected channel.

    On mismatch: delete ``youtube_token_{page}.json`` and re-run OAuth once.
    """
    slug = _sanitize_page_name(page_name)
    creds = build_credentials(slug, client_secrets_path, token_dir)
    youtube = build_youtube_client(creds)
    try:
        verify_authorized_channel(youtube, page_name=slug, enforce=enforce_channel)
    except YouTubeChannelMismatchError:
        if not enforce_channel:
            raise
        print(
            f"\n[YouTube Auth] Wrong channel for '{slug}'. "
            "Invalidating token and launching clean OAuth re-auth …\n"
            "Select the correct Brand Account "
            f'("{_PAGE_EXPECTED_CHANNEL_HINTS.get(slug, slug)}").\n'
        )
        invalidate_youtube_token(slug, token_dir)
        creds = build_credentials(slug, client_secrets_path, token_dir)
        youtube = build_youtube_client(creds)
        verify_authorized_channel(youtube, page_name=slug, enforce=True)
    return youtube


def _get_channel_id(youtube) -> str:
    """Return the authenticated user's channel ID (no console print)."""
    return fetch_authorized_channel(youtube)["id"]


# ---------------------------------------------------------------------------
# Smart drip scheduler
# ---------------------------------------------------------------------------

def parse_interval_spec(spec: str | float | int | None, *, default_hours: float = 84.0) -> timedelta:
    """Parse ``84h`` / ``12h`` / ``3d`` / bare hours into a ``timedelta``.

    Examples: ``"84h"``, ``"12H"``, ``"2d"``, ``84``, ``84.0``, ``None`` → default.
    """
    if spec is None or (isinstance(spec, str) and not str(spec).strip()):
        return timedelta(hours=float(default_hours))
    if isinstance(spec, (int, float)):
        hours = float(spec)
        if hours <= 0:
            hours = float(default_hours)
        return timedelta(hours=hours)
    text = str(spec).strip().lower().replace(" ", "")
    m = _re.fullmatch(r"(\d+(?:\.\d+)?)(h|hr|hrs|hour|hours|d|day|days|m|min|mins|minutes)?", text)
    if not m:
        raise ValueError(
            f"Invalid interval spec '{spec}'. Use forms like 84h, 12h, 2d, or a numeric hour count."
        )
    value = float(m.group(1))
    unit = m.group(2) or "h"
    if unit in {"d", "day", "days"}:
        return timedelta(days=value)
    if unit in {"m", "min", "mins", "minutes"}:
        return timedelta(minutes=value)
    return timedelta(hours=value)


def list_future_scheduled_videos(
    youtube,
    *,
    channel_id: Optional[str] = None,
    max_pages: int = 8,
    page_size: int = 50,
) -> list[dict]:
    """Return channel videos with a future ``status.publishAt`` (newest pages first).

    Each item: ``{"video_id", "publish_at", "privacy_status", "title"}``.
    """
    if channel_id is None:
        channel_id = _get_channel_id(youtube)
    try:
        ch_resp = youtube.channels().list(
            part="contentDetails", id=channel_id
        ).execute()
        uploads_pl = (
            ch_resp["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]
        )
    except Exception as exc:  # noqa: BLE001
        _LOG.warning("Could not resolve uploads playlist (%s).", exc)
        return []

    now = datetime.now(timezone.utc)
    out: list[dict] = []
    page_token: Optional[str] = None
    for _ in range(max(1, int(max_pages))):
        try:
            params: dict = {
                "part": "snippet,contentDetails",
                "playlistId": uploads_pl,
                "maxResults": min(50, max(1, int(page_size))),
            }
            if page_token:
                params["pageToken"] = page_token
            pl_resp = youtube.playlistItems().list(**params).execute()
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("playlistItems.list failed (%s).", exc)
            break

        items = pl_resp.get("items") or []
        video_ids = [
            (it.get("contentDetails") or {}).get("videoId")
            or ((it.get("snippet") or {}).get("resourceId") or {}).get("videoId")
            for it in items
        ]
        video_ids = [vid for vid in video_ids if vid]
        titles = {
            (
                (it.get("contentDetails") or {}).get("videoId")
                or ((it.get("snippet") or {}).get("resourceId") or {}).get("videoId")
            ): ((it.get("snippet") or {}).get("title") or "")
            for it in items
        }
        if video_ids:
            try:
                v_resp = youtube.videos().list(
                    part="status,snippet",
                    id=",".join(video_ids),
                ).execute()
            except Exception as exc:  # noqa: BLE001
                _LOG.warning("videos.list(status) failed (%s).", exc)
                v_resp = {"items": []}
            for v in v_resp.get("items") or []:
                status = v.get("status") or {}
                pa = status.get("publishAt")
                if not pa:
                    continue
                try:
                    dt = datetime.fromisoformat(str(pa).replace("Z", "+00:00"))
                except ValueError:
                    continue
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                if dt <= now:
                    continue
                vid = v.get("id") or ""
                out.append(
                    {
                        "video_id": vid,
                        "publish_at": dt,
                        "privacy_status": status.get("privacyStatus") or "",
                        "title": (v.get("snippet") or {}).get("title")
                        or titles.get(vid)
                        or "",
                    }
                )

        page_token = pl_resp.get("nextPageToken")
        if not page_token:
            break

    out.sort(key=lambda row: row["publish_at"])
    return out


def _get_last_scheduled_publish_time(youtube, channel_id: str) -> Optional[datetime]:
    """Return the latest future ``publishAt`` among scheduled channel videos."""
    try:
        scheduled = list_future_scheduled_videos(youtube, channel_id=channel_id)
        if not scheduled:
            return None
        return max(row["publish_at"] for row in scheduled)
    except Exception as exc:
        _LOG.warning(
            "Could not query scheduled videos (%s) — defaulting to tomorrow noon.",
            exc,
        )
        return None


def get_next_publish_slot(
    youtube,
    channel_id: Optional[str] = None,
    interval_hours: float | int = POST_INTERVAL_HOURS,
    base_hour_utc: int = _DEFAULT_BASE_HOUR_UTC,
    interval: Optional[timedelta] = None,
    initial_offset: Optional[timedelta] = None,
) -> datetime:
    """Compute the next available publish slot for a new video.

    * If future scheduled videos exist: ``max(publishAt) + interval``
    * Else: ``now + initial_offset`` (default = interval), floored to a clean minute
    """
    if channel_id is None:
        channel_id = _get_channel_id(youtube)

    delta = interval if interval is not None else timedelta(hours=float(interval_hours))
    if delta.total_seconds() <= 0:
        delta = timedelta(hours=float(POST_INTERVAL_HOURS))
    offset = initial_offset if initial_offset is not None else delta

    last = _get_last_scheduled_publish_time(youtube, channel_id)
    now = datetime.now(timezone.utc)

    if last is None:
        # Prefer a clean clock time when starting a fresh drip.
        base = now + offset
        base = base.replace(second=0, microsecond=0)
        # Keep legacy noon-anchor behavior only when offset is the default day-ish window.
        if offset >= timedelta(hours=20):
            noon = now.replace(
                hour=base_hour_utc, minute=0, second=0, microsecond=0
            ) + timedelta(days=1)
            if noon > now:
                base = max(base, noon)
        _LOG.info(
            "No prior scheduled video found -> first slot: %s UTC",
            base.strftime("%Y-%m-%d %H:%M"),
        )
        return base

    nxt = last + delta
    if nxt <= now:
        nxt = now + offset
    nxt = nxt.replace(second=0, microsecond=0)
    _LOG.info(
        "Last scheduled slot: %s -> next slot: %s UTC (interval=%s)",
        last.strftime("%Y-%m-%d %H:%M"),
        nxt.strftime("%Y-%m-%d %H:%M"),
        delta,
    )
    return nxt


def advance_slot(
    current: datetime,
    interval_hours: float | int = POST_INTERVAL_HOURS,
    *,
    interval: Optional[timedelta] = None,
) -> datetime:
    """Return ``current + interval`` for use in bulk upload loops."""
    delta = interval if interval is not None else timedelta(hours=float(interval_hours))
    return current + delta


def update_video_publish_at(
    youtube,
    video_id: str,
    publish_at: datetime,
    *,
    privacy_status: str = "private",
) -> datetime:
    """Patch ``status.publishAt`` (and privacy) for an already-uploaded video."""
    if publish_at.tzinfo is None:
        publish_at = publish_at.replace(tzinfo=timezone.utc)
    iso = publish_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    body = {
        "id": video_id,
        "status": {
            "privacyStatus": privacy_status or "private",
            "publishAt": iso,
            "selfDeclaredMadeForKids": False,
        },
    }
    youtube.videos().update(part="status", body=body).execute()
    _LOG.info("Updated publishAt | id=%s publish_at=%s", video_id, iso)
    return publish_at


def reschedule_conflicting_publish_slots(
    youtube,
    *,
    interval: timedelta | None = None,
    interval_hours: float = 84.0,
    dry_run: bool = True,
    conflict_window: timedelta | None = None,
    page_name: str = "",
) -> dict:
    """Re-index overlapping/identical future ``publishAt`` slots with a fixed gap.

    Scans scheduled channel videos, detects collisions (same timestamp or within
    ``conflict_window``), then rewrites ``publishAt`` sequentially:

        slot_i = first_valid_slot + i * interval
    """
    del page_name  # reserved for future per-page filtering / logging
    delta = interval if interval is not None else timedelta(hours=float(interval_hours))
    if delta.total_seconds() <= 0:
        delta = timedelta(hours=84.0)
    window = conflict_window if conflict_window is not None else timedelta(minutes=30)

    scheduled = list_future_scheduled_videos(youtube)
    if not scheduled:
        print("[YouTube] No future scheduled videos found — nothing to reschedule.")
        return {"scanned": 0, "conflicts": 0, "updated": [], "dry_run": dry_run}

    # Detect identical / overlapping clusters.
    conflict_ids: set[str] = set()
    for i, row in enumerate(scheduled):
        for other in scheduled[i + 1:]:
            gap = other["publish_at"] - row["publish_at"]
            if gap <= window:
                conflict_ids.add(row["video_id"])
                conflict_ids.add(other["video_id"])
            else:
                break

    # If any conflicts exist, re-index the full future schedule for clean spacing.
    targets = scheduled if conflict_ids else []
    if not targets:
        print(
            f"[YouTube] Scanned {len(scheduled)} scheduled video(s) — "
            f"no overlapping publishAt within {window}."
        )
        return {
            "scanned": len(scheduled),
            "conflicts": 0,
            "updated": [],
            "dry_run": dry_run,
        }

    now = datetime.now(timezone.utc)
    first = max(scheduled[0]["publish_at"], now + timedelta(hours=1))
    first = first.replace(second=0, microsecond=0)
    print(
        f"[YouTube] Reschedule | scanned={len(scheduled)} conflicted={len(conflict_ids)} "
        f"interval={delta} first_slot={first.strftime('%Y-%m-%d %H:%M')} UTC "
        f"dry_run={dry_run}"
    )

    updated: list[dict] = []
    for i, row in enumerate(scheduled):
        new_at = first + (delta * i)
        old_at: datetime = row["publish_at"]
        print(
            f"  [{i + 1}/{len(scheduled)}] {row['video_id']}  "
            f"{old_at.strftime('%Y-%m-%d %H:%M')} -> {new_at.strftime('%Y-%m-%d %H:%M')} UTC  "
            f"{(row.get('title') or '')[:50]}"
        )
        if dry_run:
            updated.append(
                {
                    "video_id": row["video_id"],
                    "old_publish_at": old_at.isoformat(),
                    "new_publish_at": new_at.isoformat(),
                    "dry_run": True,
                }
            )
            continue
        if new_at == old_at.replace(second=0, microsecond=0):
            continue
        try:
            update_video_publish_at(
                youtube,
                row["video_id"],
                new_at,
                privacy_status=row.get("privacy_status") or "private",
            )
            updated.append(
                {
                    "video_id": row["video_id"],
                    "old_publish_at": old_at.isoformat(),
                    "new_publish_at": new_at.isoformat(),
                }
            )
        except Exception as exc:  # noqa: BLE001
            _LOG.error("Failed to reschedule %s: %s", row["video_id"], exc)
            updated.append(
                {
                    "video_id": row["video_id"],
                    "error": str(exc),
                    "old_publish_at": old_at.isoformat(),
                    "new_publish_at": new_at.isoformat(),
                }
            )

    return {
        "scanned": len(scheduled),
        "conflicts": len(conflict_ids),
        "updated": updated,
        "dry_run": dry_run,
        "interval": str(delta),
        "first_slot": first.isoformat(),
    }


# ---------------------------------------------------------------------------
# Playlist helpers
# ---------------------------------------------------------------------------

def _resolve_page_playlist_meta(page_name: str) -> tuple[str, str]:
    """Return (title, description) for *page_name*, preferring page_config overrides."""
    slug = _sanitize_page_name(page_name)
    title = _PAGE_DEFAULT_PLAYLISTS.get(slug, "")
    description = _PAGE_PLAYLIST_DESCRIPTIONS.get(slug, "")

    # Master Mei page_config is the source of truth when available.
    if slug == "master_mei":
        try:
            from channels_config.master_mei import page_config as _mm_cfg  # type: ignore

            title = (
                getattr(_mm_cfg, "YOUTUBE_PLAYLIST_TITLE", None) or title
            ).strip() or _MASTER_MEI_PLAYLIST_TITLE
            description = (
                getattr(_mm_cfg, "YOUTUBE_PLAYLIST_DESCRIPTION", None) or description
            ).strip() or _MASTER_MEI_PLAYLIST_DESCRIPTION
        except Exception:  # noqa: BLE001
            title = title or _MASTER_MEI_PLAYLIST_TITLE
            description = description or _MASTER_MEI_PLAYLIST_DESCRIPTION

    return title, description


def get_or_create_playlist(
    youtube,
    playlist_title: str,
    playlist_description: str = "",
    *,
    page_name: str = "",
) -> str:
    """Return the playlist ID for *playlist_title* on the authenticated channel.

    When *playlist_description* is provided (Master Mei Stoic finance playlist),
    it is written on create and synced on match — never the legacy
    ``Unified Multi-Page Factory`` auto-string for Master Mei.
    """
    from googleapiclient.errors import HttpError  # type: ignore[import]

    cache_key = playlist_title.strip().lower()
    if cache_key in _PLAYLIST_ID_CACHE:
        _LOG.debug(
            "Playlist cache hit | title='%s' id=%s",
            playlist_title,
            _PLAYLIST_ID_CACHE[cache_key],
        )
        return _PLAYLIST_ID_CACHE[cache_key]

    # Refuse to target deprecated Master Mei "Warrior Discipline" playlist titles.
    if (
        _sanitize_page_name(page_name) == "master_mei"
        and cache_key in _MASTER_MEI_DEPRECATED_PLAYLISTS
    ):
        _LOG.warning(
            "Deprecated Master Mei playlist title '%s' — redirecting to '%s'.",
            playlist_title,
            _MASTER_MEI_PLAYLIST_TITLE,
        )
        playlist_title = _MASTER_MEI_PLAYLIST_TITLE
        cache_key = playlist_title.strip().lower()
        if not playlist_description:
            playlist_description = _MASTER_MEI_PLAYLIST_DESCRIPTION

    _LOG.info("Searching channel playlists for '%s' …", playlist_title)
    page_token: Optional[str] = None
    found_id: Optional[str] = None
    found_desc: str = ""

    while True:
        params: dict = dict(part="snippet", mine=True, maxResults=50)
        if page_token:
            params["pageToken"] = page_token
        resp = youtube.playlists().list(**params).execute()

        for item in resp.get("items", []):
            if item["snippet"]["title"].strip().lower() == cache_key:
                found_id = item["id"]
                found_desc = item["snippet"].get("description") or ""
                break

        if found_id:
            break

        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    if not found_id:
        _LOG.info("Playlist '%s' not found — creating …", playlist_title)
        # Master Mei: use official Stoic finance description only.
        # Other pages keep a neutral description (no Master Mei branding).
        if playlist_description:
            desc = playlist_description
        elif _sanitize_page_name(page_name) == "master_mei":
            desc = _MASTER_MEI_PLAYLIST_DESCRIPTION
        else:
            desc = f"Official playlist: {playlist_title}."
        try:
            create_resp = youtube.playlists().insert(
                part="snippet,status",
                body={
                    "snippet": {
                        "title": playlist_title,
                        "description": desc,
                    },
                    "status": {"privacyStatus": "public"},
                },
            ).execute()
            found_id = create_resp["id"]
            _LOG.info("Playlist created | title='%s' id=%s", playlist_title, found_id)
            print(f"[YouTube] ✓ Created playlist '{playlist_title}' (id={found_id})")
        except HttpError as exc:
            _LOG.error("Failed to create playlist '%s': %s", playlist_title, exc)
            raise
    elif playlist_description and found_desc.strip() != playlist_description.strip():
        # Sync Master Mei (or any explicit) description onto an existing playlist.
        try:
            youtube.playlists().update(
                part="snippet",
                body={
                    "id": found_id,
                    "snippet": {
                        "title": playlist_title,
                        "description": playlist_description,
                    },
                },
            ).execute()
            _LOG.info(
                "Playlist description synced | title='%s' id=%s",
                playlist_title,
                found_id,
            )
            print(f"[YouTube] ✓ Synced playlist description → '{playlist_title}'")
        except Exception as exc:  # noqa: BLE001
            _LOG.warning(
                "Could not sync playlist description for '%s' (%s) — continuing.",
                playlist_title,
                exc,
            )

    _PLAYLIST_ID_CACHE[cache_key] = found_id
    return found_id


def update_playlist_snippet(
    youtube,
    playlist_id: str,
    title: str,
    description: str = "",
) -> str:
    """Patch title/description on an existing playlist ID (no duplicate create)."""
    from googleapiclient.errors import HttpError  # type: ignore[import]

    body = {
        "id": playlist_id,
        "snippet": {
            "title": (title or "")[:150],
            "description": (description or "")[:5000],
        },
    }
    try:
        youtube.playlists().update(part="snippet", body=body).execute()
    except HttpError as exc:
        _LOG.error("playlists().update failed for %s: %s", playlist_id, exc)
        raise
    _PLAYLIST_ID_CACHE[title.strip().lower()] = playlist_id
    _LOG.info("Playlist snippet updated | id=%s title='%s'", playlist_id, title)
    print(f"[YouTube] Updated playlist {playlist_id} -> '{title}'")
    return playlist_id


def add_video_to_playlist(
    youtube,
    video_id: str,
    playlist_id: str,
    position: Optional[int] = None,
) -> None:
    """Insert *video_id* into *playlist_id* via ``playlistItems().insert()``.

    When *position* is set (0-based), the item is placed at that index so
    chronological learning-journey order is preserved instead of newest-first.
    If the video is already in the playlist, its position is updated.
    """
    from googleapiclient.errors import HttpError  # type: ignore[import]

    existing_item_id: Optional[str] = None
    try:
        page_token: Optional[str] = None
        while True:
            params: dict = dict(
                part="snippet",
                playlistId=playlist_id,
                maxResults=50,
            )
            if page_token:
                params["pageToken"] = page_token
            resp = youtube.playlistItems().list(**params).execute()
            for item in resp.get("items", []):
                rid = (item.get("snippet") or {}).get("resourceId") or {}
                if rid.get("videoId") == video_id:
                    existing_item_id = item.get("id")
                    break
            if existing_item_id:
                break
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
    except Exception as exc:  # noqa: BLE001
        _LOG.warning(
            "Could not list playlist %s before insert (%s) — inserting anyway.",
            playlist_id,
            exc,
        )

    snippet: dict = {
        "playlistId": playlist_id,
        "resourceId": {
            "kind": "youtube#video",
            "videoId": video_id,
        },
    }
    if position is not None:
        snippet["position"] = max(0, int(position))

    try:
        if existing_item_id and position is not None:
            youtube.playlistItems().update(
                part="snippet",
                body={"id": existing_item_id, "snippet": snippet},
            ).execute()
            _LOG.info(
                "Playlist position updated | video_id=%s playlist_id=%s pos=%s",
                video_id,
                playlist_id,
                position,
            )
            return
        if existing_item_id:
            _LOG.info(
                "Playlist already contains video | video_id=%s playlist_id=%s",
                video_id,
                playlist_id,
            )
            return
        youtube.playlistItems().insert(
            part="snippet",
            body={"snippet": snippet},
        ).execute()
        _LOG.info(
            "Playlist assignment OK | video_id=%s playlist_id=%s pos=%s",
            video_id,
            playlist_id,
            position,
        )
    except HttpError as exc:
        _LOG.warning(
            "playlistItems insert/update failed for video %s → playlist %s: %s",
            video_id,
            playlist_id,
            exc,
        )
        print(
            f"[YouTube] ⚠ Could not add {video_id} to playlist "
            f"(id={playlist_id}): {exc.resp.status} — continuing."
        )


# ---------------------------------------------------------------------------
# Metadata helpers
# ---------------------------------------------------------------------------

def _extract_hashtags(text: str) -> list[str]:
    return list(dict.fromkeys(tag.lower() for tag in _re.findall(r"#(\w+)", text or "")))


def sanitize_youtube_title(
    title: str,
    page_name: str = "",
) -> tuple[str, list[str]]:
    """
    High-CTR US title sanitizer.

    Strips ``#hashtags`` and brand strings (``MASTER MEI``, ``Master Mei | …``)
    from the title. Returns ``(clean_title, hashtags_for_description)``.
    """
    raw = (title or "").strip() or "Untitled Short"
    tags = _extract_hashtags(raw)
    clean = _re.sub(r"#\w+", " ", raw)
    slug = _sanitize_page_name(page_name)
    # Principles of Wealth keeps the SEO pipe template and #shorts marker.
    if slug == "principles_of_wealth_finance_economics":
        clean = _re.sub(r"\s{2,}", " ", (title or "").strip())
        if not clean:
            clean = "Principles of Wealth"
        return clean[:100], tags
    # Endless Summer Paradise keeps [Hook] — Anchor | Brand pipe structure.
    if slug == "endless_summer_paradise":
        clean = _re.sub(r"#\w+", " ", raw)
        clean = _re.sub(r"\s{2,}", " ", clean).strip()
        if not clean:
            clean = "1950s Surreal Jell-O Waterpark | Endless Summer Paradise"
        return clean[:100], tags
    if slug == "master_mei":
        clean = _re.sub(r"(?i)\bMASTER\s*MEI\b", " ", clean)
        clean = _re.sub(r"(?i)\bMaster\s*Mei\b", " ", clean)
        clean = _re.sub(r"(?i)\|\s*Mind\s*Control\b", " ", clean)
        clean = _re.sub(r"(?i)\bMind\s*Control\b", " ", clean)
    # Collapse leftover pipes / punctuation noise
    clean = _re.sub(r"\s*[|·•]\s*", " — ", clean)
    clean = _re.sub(r"(?:\s*—\s*){2,}", " — ", clean)
    clean = _re.sub(r"\s{2,}", " ", clean).strip(" —-|·•")
    if not clean:
        clean = "The Discipline They Don't Want You to Learn"
    return clean[:100], tags


def _default_tags_for_page(page_name: str) -> list[str]:
    slug = _sanitize_page_name(page_name)
    if slug == "master_mei":
        try:
            from channels_config.master_mei import page_config as _mm_cfg  # type: ignore

            tags = getattr(_mm_cfg, "YOUTUBE_DEFAULT_TAGS", None)
            if isinstance(tags, list) and tags:
                return [str(t) for t in tags]
        except Exception:  # noqa: BLE001
            pass
        return [
            "master mei",
            "mind control",
            "stoicism",
            "financial freedom",
            "wealth mindset",
            "stoic philosophy",
            "business strategy",
            "self discipline",
            "executive mindset",
            "personal finance",
            "shorts",
        ]
    if slug == "principles_of_wealth_finance_economics":
        try:
            from channels_config.principles_of_wealth_finance_economics import (  # type: ignore
                page_config as _pow_cfg,
            )

            tags = getattr(_pow_cfg, "YOUTUBE_DEFAULT_TAGS", None)
            if isinstance(tags, list) and tags:
                return [str(t) for t in tags]
        except Exception:  # noqa: BLE001
            pass
        return [
            "principles of wealth",
            "stock market analysis",
            "S&P 500 strategy",
            "asset allocation",
            "inflation protection",
            "risk management",
            "wealth preservation",
            "ray dalio",
        ]
    if slug == "endless_summer_paradise":
        try:
            from channels_config.endless_summer_paradise import (  # type: ignore
                page_config as _esp_cfg,
            )

            tags = getattr(_esp_cfg, "YOUTUBE_DEFAULT_TAGS", None)
            if isinstance(tags, list) and tags:
                return [str(t) for t in tags]
        except Exception:  # noqa: BLE001
            pass
        return [
            "endless summer paradise",
            "1950s surreal jell-o waterpark",
            "1950s mid-century architecture",
            "vintage aesthetic",
            "4K visual art",
            "retro dreamscape",
            "cinematic surrealism",
        ]
    defaults: dict[str, list[str]] = {
        "ancient_knowledge": [
            "ancient knowledge",
            "ancient mysteries",
            "forbidden history",
            "shorts",
        ],
    }
    return list(defaults.get(slug, ["shorts"]))


def _normalize_tags(tags: Optional[list[str]], page_name: str, description: str) -> list[str]:
    merged: list[str] = []
    for t in (tags or []) + _extract_hashtags(description) + _default_tags_for_page(page_name):
        clean = str(t).strip().lstrip("#")
        if clean and clean.lower() not in {x.lower() for x in merged}:
            merged.append(clean)
    # Final hard sanitization — prevents invalidTags / invalidVideoKeywords.
    return sanitize_youtube_tags(merged)


# YouTube's videos.insert description field hard-caps at 5000 characters;
# stay well under that (4000) to leave headroom for tag/hashtag appends.
_YT_DESCRIPTION_MAX_CHARS = 4000

# YouTube keyword list soft-cap (official is 500 incl. commas; stay under).
_YT_TAGS_MAX_TOTAL_CHARS = 400
_YT_TAG_MAX_LEN = 100
_YT_TAGS_MAX_COUNT = 30


def sanitize_youtube_tags(tags: "list | str | None") -> list[str]:
    """Sanitize *tags* into a YouTube-safe ``snippet.tags`` list.

    Guards against ``HttpError 400 (invalidTags / invalidVideoKeywords)``:

    * Accepts ``list`` or comma/newline-separated ``str``
    * Strips ``#``, HTML angle brackets, emojis, control chars, newlines
    * Truncates each tag to 100 chars
    * Caps combined length (tags + commas) at 400 chars (under YouTube's 500)
    * Deduplicates (case-insensitive) and drops empties / ``None``
    * Preserves first-seen order
    """
    raw_items: list[str] = []
    if tags is None:
        raw_items = []
    elif isinstance(tags, str):
        # Comma / newline separated blob — never iterate a string as characters.
        chunk = tags.replace("\r", "\n")
        for part in _re.split(r"[\n,;|]+", chunk):
            part = part.strip()
            if part:
                raw_items.append(part)
    elif isinstance(tags, (list, tuple, set)):
        for item in tags:
            if item is None:
                continue
            if isinstance(item, str) and ("," in item or "\n" in item):
                raw_items.extend(sanitize_youtube_tags(item))
            else:
                raw_items.append(str(item))
    else:
        raw_items.append(str(tags))

    cleaned: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        text = str(item or "")
        # Strip hashtags, HTML, newlines, emojis / symbols, non-printables.
        text = text.replace("#", " ")
        text = _re.sub(r"[<>]", " ", text)
        text = _re.sub(r"[\r\n\t]+", " ", text)
        text = _re.sub(
            r"[\U0001F300-\U0001FAFF\U00002700-\U000027BF\u2600-\u26FF"
            r"⚠⚠️✅❌⭐🌟✨💫🔥💯▶►◆◇•·]+",
            " ",
            text,
        )
        text = "".join(ch for ch in text if ch.isprintable())
        # Keep letters / digits / spaces / hyphen / apostrophe only.
        text = _re.sub(r"[^\w\s\-']+", " ", text, flags=_re.UNICODE)
        text = _re.sub(r"\s+", " ", text).strip(" -_")
        # Repair broken acronym splits: "a i video" → "ai video"
        text = _re.sub(r"\ba i\b", "ai", text, flags=_re.IGNORECASE)
        text = _re.sub(r"\b4 k\b", "4k", text, flags=_re.IGNORECASE)
        if len(text) < 2:
            continue
        text = text[:_YT_TAG_MAX_LEN].strip(" -_")
        key = text.lower()
        if not key or key in seen:
            continue
        seen.add(key)
        cleaned.append(text)

    # Enforce combined length budget (commas between tags count toward YouTube's cap).
    limited: list[str] = []
    total = 0
    for tag in cleaned:
        extra = len(tag) + (1 if limited else 0)
        if total + extra > _YT_TAGS_MAX_TOTAL_CHARS:
            break
        limited.append(tag)
        total += extra
        if len(limited) >= _YT_TAGS_MAX_COUNT:
            break
    return limited


def sanitize_youtube_description(raw: "object") -> str:
    """
    Sanitize *raw* into a plain string safe for YouTube's ``description`` field.

    Guards against ``HttpError 400 (invalidDescription)`` caused by:
      * a dict/list slipping through instead of a string (e.g. an un-extracted
        ``caption_body`` JSON payload),
      * a string that is itself a serialized JSON object/array (extracts a
        sensible text field when possible, otherwise strips the braces),
      * descriptions exceeding YouTube's length limit.

    Always returns a plain ``str`` truncated to ``_YT_DESCRIPTION_MAX_CHARS``.
    """
    text: str

    if isinstance(raw, dict):
        text = str(
            raw.get("caption_body")
            or raw.get("caption")
            or raw.get("description")
            or raw.get("text")
            or ""
        ).strip()
        if not text:
            # Last resort: flatten the dict's string-ish values.
            text = " ".join(str(v) for v in raw.values() if isinstance(v, (str, int, float)))
    elif isinstance(raw, (list, tuple)):
        text = " ".join(str(v) for v in raw)
    else:
        text = str(raw or "")

    text = text.strip()

    # If the string itself looks like a raw JSON object/array, try to parse
    # and extract a usable text field before falling back to brace-stripping.
    if text[:1] in "{[":
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                text = str(
                    parsed.get("caption_body")
                    or parsed.get("caption")
                    or parsed.get("description")
                    or parsed.get("text")
                    or text
                ).strip()
            elif isinstance(parsed, list):
                text = " ".join(str(v) for v in parsed).strip()
        except (ValueError, TypeError):
            # Not valid JSON — strip stray leading/trailing braces/brackets only.
            text = text.strip("{}[]").strip()

    # Collapse accidental JSON-key artifacts (e.g. `"caption_body":`) that can
    # leak through from partially-serialized payloads.
    text = _re.sub(r'"?\b(caption_body|caption|description)\b"?\s*:\s*', "", text)
    text = _re.sub(r"[ \t]{2,}", " ", text).strip()

    if len(text) > _YT_DESCRIPTION_MAX_CHARS:
        text = text[:_YT_DESCRIPTION_MAX_CHARS].rstrip()
        _LOG.warning(
            "YouTube description truncated to %d chars (was %d).",
            _YT_DESCRIPTION_MAX_CHARS, len(str(raw or "")),
        )

    return text


# ---------------------------------------------------------------------------
# Upload function
# ---------------------------------------------------------------------------

def upload_short(
    video_path: str | Path,
    title: str,
    description: str = "",
    tags: Optional[list[str]] = None,
    privacy_status: str = _DEFAULT_PRIVACY,
    publish_at: Optional[datetime] = None,
    category_id: str = _DEFAULT_CATEGORY_ID,
    page_name: str = "default",
    client_secrets_path: Optional[str | Path] = None,
    token_dir: Optional[str | Path] = None,
    playlist_title: Optional[str] = None,
    playlist_description: Optional[str] = None,
    youtube=None,
    related_video_id: Optional[str] = None,
    skip_playlist: bool = False,
    preserve_title: bool = False,
    thumbnail_path: Optional[str | Path] = None,
    default_language: str = "en-US",
    default_audio_language: str = "en-US",
) -> tuple[str, str, Optional[datetime]]:
    """Upload *video_path* to the page-isolated YouTube channel.

    Returns ``(video_id, "https://youtu.be/{video_id}", publish_at_or_None)``.

    *related_video_id* is sent as ``snippet.relatedVideoId`` when the API
    accepts it (Shorts → long-form link). If the field is rejected the upload
    retries without it; callers should also put the long URL in the description.
    """
    from googleapiclient.http import MediaFileUpload  # type: ignore[import]
    from googleapiclient.errors import HttpError  # type: ignore[import]

    video_path = Path(video_path)
    if not video_path.is_file():
        raise FileNotFoundError(f"Video file not found: {video_path}")

    page_slug = _sanitize_page_name(page_name)
    # High-CTR title: strip #hashtags + MASTER MEI brand; move tags to description
    if preserve_title:
        safe_title = (title or "").strip()[:100] or "Untitled"
        title_hashtags = _extract_hashtags(title or "")
    else:
        safe_title, title_hashtags = sanitize_youtube_title(title, page_slug)
    # Sanitize to a plain string + truncate to 4000 chars BEFORE any further
    # string-only processing below — prevents HttpError 400 (invalidDescription)
    # from raw JSON structures or oversized text leaking into the API payload.
    description = sanitize_youtube_description(description)
    # Strip legacy factory / Warrior Discipline branding from Master Mei payloads.
    if page_slug == "master_mei":
        description = (description or "").replace("Unified Multi-Page Factory", "").strip()
        description = _re.sub(
            r"(?i)warrior\s+discipline",
            "Stoic discipline",
            description,
        )
        if "Warrior Discipline" in safe_title or "warrior discipline" in safe_title.lower():
            safe_title = "Stoic Financial Freedom"
    # Append title-stripped hashtags into description (never leave them in the title)
    if title_hashtags:
        hash_line = " ".join(f"#{t}" for t in title_hashtags)
        if hash_line.lower() not in (description or "").lower():
            description = (
                f"{description.rstrip()}\n\n{hash_line}" if description else hash_line
            )
            description = sanitize_youtube_description(description)
    file_mb = video_path.stat().st_size / 1_048_576
    # Never pass a raw string into list() — that would explode into 1-char tags.
    tag_list = _normalize_tags(
        sanitize_youtube_tags(tags) + list(title_hashtags or []),
        page_slug,
        description,
    )
    # Belt-and-suspenders: sanitize again immediately before payload build.
    tag_list = sanitize_youtube_tags(tag_list)
    # Never ship deprecated martial tags on Master Mei uploads.
    if page_slug == "master_mei":
        tag_list = [
            t for t in tag_list
            if "warrior discipline" not in t.lower() and t.lower() != "warrior mindset"
        ]
        tag_list = sanitize_youtube_tags(tag_list)

    # Final assignment point for snippet.tags (must stay a clean list[str]).
    body_tags = sanitize_youtube_tags(tag_list)
    effective_privacy = "private" if publish_at else privacy_status
    scheduled_str = publish_at.strftime("%Y-%m-%dT%H:%M:%SZ") if publish_at else None

    _LOG.info(
        "YouTube upload | file=%s (%.1fMB) title='%s' privacy=%s publish_at=%s "
        "page=%s token=%s tags=%d",
        video_path.name,
        file_mb,
        safe_title,
        effective_privacy,
        scheduled_str,
        page_slug,
        resolve_youtube_token_path(page_slug, token_dir),
        len(body_tags),
    )
    if publish_at:
        print(
            f"[YouTube] Scheduling '{video_path.name}' ({file_mb:.1f} MB) "
            f"-> publish at {publish_at.strftime('%Y-%m-%d %H:%M UTC')} ..."
        )
    else:
        print(
            f"[YouTube] Uploading '{video_path.name}' ({file_mb:.1f} MB) "
            f"-> privacy={effective_privacy} ..."
        )

    if youtube is None:
        # Enforces channel match; deletes token + re-auths on mismatch
        youtube = build_youtube_client_for_page(
            page_slug, client_secrets_path, token_dir, enforce_channel=True
        )
    else:
        # CRITICAL: confirm which channel this page token actually owns
        try:
            verify_authorized_channel(youtube, page_name=page_slug, enforce=True)
        except YouTubeChannelMismatchError:
            youtube = build_youtube_client_for_page(
                page_slug, client_secrets_path, token_dir, enforce_channel=True
            )

    status_body: dict = {
        "privacyStatus": effective_privacy,
        "selfDeclaredMadeForKids": False,
        # YouTube AI / synthetic media disclosure (best practice)
        "containsSyntheticMedia": True,
    }
    if scheduled_str:
        status_body["publishAt"] = scheduled_str

    snippet_body: dict = {
        "title": safe_title,
        "description": description or "",
        "tags": body_tags,
        "categoryId": str(category_id or _DEFAULT_CATEGORY_ID),
        # US audience targeting — language signals for recommendations / ads.
        "defaultLanguage": (default_language or "en-US").strip() or "en-US",
        "defaultAudioLanguage": (
            (default_audio_language or default_language or "en-US").strip() or "en-US"
        ),
    }
    if related_video_id:
        snippet_body["relatedVideoId"] = related_video_id

    body: dict = {
        "snippet": snippet_body,
        "status": status_body,
    }

    media = MediaFileUpload(
        str(video_path),
        mimetype="video/mp4",
        chunksize=_CHUNK_SIZE,
        resumable=True,
    )

    def _insert(yt_client, payload: dict):
        return yt_client.videos().insert(
            part=",".join(payload.keys()),
            body=payload,
            media_body=media,
        )

    request = _insert(youtube, body)
    response = None
    last_pct = -1
    file_bytes = video_path.stat().st_size
    _synthetic_retried = False

    while response is None:
        try:
            status_chunk, response = request.next_chunk()
        except HttpError as exc:
            # Older API surfaces may reject containsSyntheticMedia — retry once without it
            err_txt = str(exc).lower()
            if not _synthetic_retried and (
                "containssyntheticmedia" in err_txt
                or ("synthetic" in err_txt and "invalid" in err_txt)
            ):
                _synthetic_retried = True
                status_body.pop("containsSyntheticMedia", None)
                _LOG.warning(
                    "containsSyntheticMedia rejected by API — retrying upload without it."
                )
                request = _insert(youtube, body)
                continue
            if related_video_id and "relatedvideoid" in err_txt:
                snippet_body.pop("relatedVideoId", None)
                related_video_id = None
                _LOG.warning(
                    "relatedVideoId rejected by API — retrying upload without it "
                    "(long-form URL remains in the description)."
                )
                request = _insert(youtube, body)
                continue
            if _is_upload_limit_error(exc):
                _LOG.warning(
                    "[YouTube] Daily upload limit (20 videos) reached for this channel."
                )
                raise YouTubeQuotaExceededError(
                    f"YouTube daily upload limit reached while uploading "
                    f"'{video_path.name}' (page={page_slug}): {exc}"
                ) from exc
            if exc.resp.status in (500, 502, 503, 504):
                _LOG.warning(
                    "YouTube transient error (%s) — retrying …", exc.resp.status
                )
                continue
            raise
        except Exception as exc:  # noqa: BLE001 — covers non-HttpError upload errors
            if _is_upload_limit_error(exc):
                _LOG.warning(
                    "[YouTube] Daily upload limit (20 videos) reached for this channel."
                )
                raise YouTubeQuotaExceededError(
                    f"YouTube daily upload limit reached while uploading "
                    f"'{video_path.name}' (page={page_slug}): {exc}"
                ) from exc
            raise
        if status_chunk:
            pct = int(status_chunk.resumable_progress / file_bytes * 100)
            if pct != last_pct:
                print(f"[YouTube] Upload progress: {pct}% ...", end="\r", flush=True)
                last_pct = pct

    print()
    video_id = response.get("id", "")
    yt_url = f"https://youtu.be/{video_id}"

    if publish_at:
        _LOG.info(
            "YouTube upload+schedule OK | id=%s  publish_at=%s  url=%s",
            video_id,
            scheduled_str,
            yt_url,
        )
        print(
            f"[YouTube] Scheduled -> {yt_url}\n"
            f"          Publishes at: {publish_at.strftime('%Y-%m-%d %H:%M UTC')}"
        )
    else:
        _LOG.info("YouTube upload OK | id=%s  url=%s", video_id, yt_url)
        print(f"[YouTube] Published -> {yt_url}")

    if thumbnail_path and video_id:
        try:
            set_video_thumbnail(youtube, video_id, thumbnail_path)
        except Exception as thumb_exc:  # noqa: BLE001
            _LOG.warning(
                "Thumbnail upload failed for %s (%s) — video itself succeeded.",
                video_id,
                thumb_exc,
            )

    if not skip_playlist:
        _default_title, _default_desc = _resolve_page_playlist_meta(page_slug)
        _pl_title = playlist_title if playlist_title is not None else _default_title
        _pl_desc = (
            playlist_description
            if playlist_description is not None
            else _default_desc
        )
        if _pl_title and video_id:
            try:
                _pl_id = get_or_create_playlist(
                    youtube,
                    _pl_title,
                    playlist_description=_pl_desc,
                    page_name=page_slug,
                )
                add_video_to_playlist(youtube, video_id, _pl_id)
                print(f'[YouTube] ✓ Added video {video_id} to playlist: "{_pl_title}"')
            except Exception as _pl_exc:
                _LOG.warning(
                    "Playlist assignment failed for %s → '%s': %s — upload itself succeeded.",
                    video_id,
                    _pl_title,
                    _pl_exc,
                )

    return video_id, yt_url, publish_at


# ---------------------------------------------------------------------------
# Metadata update function (patch an already-uploaded video)
# ---------------------------------------------------------------------------

def update_video_metadata(
    youtube,
    video_id: str,
    title: str = "",
    description: str = "",
    tags: Optional[list[str]] = None,
    category_id: str = _DEFAULT_CATEGORY_ID,
    default_language: str = "en-US",
    default_audio_language: str = "en-US",
) -> str:
    """Patch the ``snippet`` of an already-uploaded YouTube video."""
    from googleapiclient.errors import HttpError  # type: ignore[import]

    _LOG.info("Fetching existing snippet for video_id=%s …", video_id)
    list_resp = youtube.videos().list(part="snippet,status", id=video_id).execute()

    items = list_resp.get("items", [])
    if not items:
        raise ValueError(
            f"Video ID '{video_id}' not found on this channel. "
            "Verify the ID and that the authenticated account owns the video."
        )

    existing_snippet: dict = items[0]["snippet"]

    if title:
        existing_snippet["title"] = title[:100]
    if description:
        existing_snippet["description"] = sanitize_youtube_description(description)
    if tags is not None:
        existing_snippet["tags"] = sanitize_youtube_tags(tags)
    existing_snippet["categoryId"] = str(category_id or _DEFAULT_CATEGORY_ID)
    existing_snippet["defaultLanguage"] = (
        (default_language or "en-US").strip() or "en-US"
    )
    existing_snippet["defaultAudioLanguage"] = (
        (default_audio_language or default_language or "en-US").strip() or "en-US"
    )

    update_body = {
        "id": video_id,
        "snippet": existing_snippet,
    }

    try:
        update_resp = youtube.videos().update(part="snippet", body=update_body).execute()
    except HttpError as exc:
        _LOG.error("videos().update failed for %s: %s", video_id, exc)
        raise

    updated_id = update_resp.get("id", video_id)
    _LOG.info(
        "Metadata updated | id=%s title='%s' tags=%d",
        updated_id,
        existing_snippet.get("title", ""),
        len(existing_snippet.get("tags") or []),
    )
    return updated_id


def set_video_thumbnail(youtube, video_id: str, thumbnail_path: str | Path) -> None:
    """Upload a custom thumbnail via ``thumbnails().set()``."""
    from googleapiclient.http import MediaFileUpload  # type: ignore[import]
    from googleapiclient.errors import HttpError  # type: ignore[import]

    path = Path(thumbnail_path)
    if not path.is_file():
        raise FileNotFoundError(path)
    suffix = path.suffix.lower()
    mime = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
    }.get(suffix, "image/jpeg")
    media = MediaFileUpload(str(path), mimetype=mime, resumable=False)
    try:
        youtube.thumbnails().set(videoId=video_id, media_body=media).execute()
    except HttpError as exc:
        _LOG.warning("thumbnails.set failed for %s: %s", video_id, exc)
        raise
    print(f"[YouTube] ✓ Thumbnail set → {path.name} on {video_id}")


def link_short_to_related_long(
    youtube,
    short_video_id: str,
    long_video_id: str,
) -> bool:
    """Best-effort Short → long-form link.

    Tries ``snippet.relatedVideoId`` on ``videos.update``. YouTube Studio is
    still the official UI for this field; if the API rejects it, the Short
    description must already contain ``https://youtu.be/{long_id}``.
    Returns True when the API accepted the field.
    """
    from googleapiclient.errors import HttpError  # type: ignore[import]

    if not short_video_id or not long_video_id:
        return False
    try:
        list_resp = youtube.videos().list(
            part="snippet", id=short_video_id
        ).execute()
        items = list_resp.get("items") or []
        if not items:
            return False
        snippet = items[0].get("snippet") or {}
        snippet["relatedVideoId"] = long_video_id
        youtube.videos().update(
            part="snippet",
            body={"id": short_video_id, "snippet": snippet},
        ).execute()
        print(
            f"[YouTube] ✓ relatedVideoId {long_video_id} → Short {short_video_id}"
        )
        return True
    except HttpError as exc:
        _LOG.warning(
            "relatedVideoId update rejected for Short %s → %s (%s). "
            "Description URL remains the guaranteed link.",
            short_video_id,
            long_video_id,
            exc,
        )
        return False


# ---------------------------------------------------------------------------
# Daily-quota pending-upload queue
#
# When ``upload_short`` raises ``YouTubeQuotaExceededError`` (the channel's
# ~20 videos/day cap was hit), the caller queues the remaining already-
# generated videos here instead of losing them. ``resume_pending_youtube_uploads``
# (or CLI ``--resume-youtube-queue``) replays the queue once the quota rolls
# over, removing each entry as soon as it uploads successfully.
# ---------------------------------------------------------------------------

def _pending_queue_path() -> Path:
    try:
        import config as app_config  # local import avoids circulars at module load

        override = getattr(app_config, "YOUTUBE_PENDING_QUEUE_PATH", None)
        if override:
            return Path(override)
    except Exception:  # noqa: BLE001
        pass
    return _DEFAULT_PENDING_QUEUE_PATH


def load_pending_uploads(page_name: Optional[str] = None) -> list[dict]:
    """Return all queued pending uploads, optionally filtered to one page."""
    path = _pending_queue_path()
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        _LOG.warning(
            "Could not read pending YouTube queue at %s (%s) — treating as empty.",
            path, exc,
        )
        return []
    items = data if isinstance(data, list) else (data.get("items") or [])
    if page_name:
        slug = _sanitize_page_name(page_name)
        items = [it for it in items if _sanitize_page_name(it.get("page_name", "")) == slug]
    return items


def _write_pending_uploads(items: list[dict]) -> None:
    path = _pending_queue_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(items, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def queue_pending_upload(
    *,
    page_name: str,
    video_path: str | Path,
    title: str = "",
    description: str = "",
    tags: Optional[list[str]] = None,
    privacy_status: str = _DEFAULT_PRIVACY,
    publish_at: Optional[datetime] = None,
    category_id: str = _DEFAULT_CATEGORY_ID,
    playlist_title: Optional[str] = None,
    playlist_description: Optional[str] = None,
    reason: str = "daily_upload_limit_exceeded",
) -> None:
    """Append one video's metadata to ``credentials/pending_youtube_uploads.json``."""
    entry = {
        "page_name": _sanitize_page_name(page_name),
        "video_path": str(video_path),
        "title": title,
        "description": description,
        "tags": list(tags or []),
        "privacy_status": privacy_status,
        "publish_at": publish_at.isoformat() if publish_at else None,
        "category_id": category_id,
        "playlist_title": playlist_title,
        "playlist_description": playlist_description,
        "reason": reason,
        "queued_at": datetime.now(timezone.utc).isoformat(),
    }
    with _QUEUE_LOCK:
        items = load_pending_uploads()
        if any(
            it.get("video_path") == entry["video_path"] and it.get("page_name") == entry["page_name"]
            for it in items
        ):
            return  # already queued — avoid duplicate entries on repeated failures
        items.append(entry)
        _write_pending_uploads(items)
    _LOG.info(
        "Queued pending YouTube upload | page=%s reason=%s → %s",
        entry["page_name"], reason, entry["video_path"],
    )
    print(f"[YouTube] Queued for later upload → {Path(str(video_path)).name} (page={page_name})")


def queue_pending_upload_from_envelope(
    row: dict,
    page_name: str,
    privacy_status: str = _DEFAULT_PRIVACY,
    publish_at: Optional[datetime] = None,
    reason: str = "daily_upload_limit_exceeded",
) -> None:
    """
    Queue one already-compiled envelope row (same field extraction as
    ``upload_short_from_envelope``) so a later ``resume_pending_youtube_uploads``
    call can upload it identically to how it would have uploaded immediately.
    """
    video_path = row.get("video_path") or ""
    if not video_path:
        _LOG.warning("queue_pending_upload_from_envelope: no video_path in row — skipping.")
        return

    _raw_title = (
        row.get("title")
        or row.get("topic")
        or row.get("subject")
        or row.get("overlay_text")
        or ""
    )
    if not _raw_title:
        _stem = Path(str(video_path)).stem
        _stem = _re.sub(r"_v\d+$", "", _stem)
        _raw_title = _stem.replace("reel_", "").replace("_", " ").title()
    title = (_raw_title or "Untitled Short")[:100]
    description = row.get("caption") or row.get("description") or ""
    tags_raw = row.get("tags") or []
    tags = tags_raw if isinstance(tags_raw, list) else [str(tags_raw)]

    queue_pending_upload(
        page_name=page_name,
        video_path=video_path,
        title=title,
        description=description,
        tags=tags,
        privacy_status=privacy_status,
        publish_at=publish_at,
        reason=reason,
    )


def resume_pending_youtube_uploads(
    page_name: Optional[str] = None,
    client_secrets_path: Optional[str | Path] = None,
    token_dir: Optional[str | Path] = None,
    dry_run: bool = False,
    limit: Optional[int] = None,
) -> dict:
    """
    Replay ``credentials/pending_youtube_uploads.json`` (optionally scoped to
    one page) — used by CLI ``--resume-youtube-queue`` once the daily quota
    has rolled over. Each successful upload is removed from the queue
    immediately so partial progress is never lost. If the global safety cap
    or YouTube quota is hit mid-resume, remaining entries stay queued and
    this returns gracefully instead of raising.

    ``limit`` overrides ``MAX_DAILY_UPLOADS`` for this execution (CLI ``--limit``).
    """
    gate = DailyUploadSafetyGate(limit=limit)
    with _QUEUE_LOCK:
        all_items = load_pending_uploads()
    if page_name:
        slug = _sanitize_page_name(page_name)
        target_items = [it for it in all_items if _sanitize_page_name(it.get("page_name", "")) == slug]
        other_items = [it for it in all_items if _sanitize_page_name(it.get("page_name", "")) != slug]
    else:
        target_items = list(all_items)
        other_items = []

    if not target_items:
        print("[YouTube] No pending queued uploads found.")
        return {
            "uploaded": [],
            "still_pending": 0,
            "quota_hit_again": False,
            "safety_cap_hit": False,
            "limit": gate.limit,
        }

    print(
        f"[YouTube] Resuming {len(target_items)} queued upload(s) from "
        f"{_pending_queue_path().name} | safety cap={gate.limit} …"
    )
    uploaded: list[dict] = []
    remaining: list[dict] = list(target_items)
    quota_hit_again = False
    safety_cap_hit = False
    _yt_clients: dict[str, object] = {}

    for entry in list(target_items):
        if not gate.can_upload():
            safety_cap_hit = True
            gate.notify_halt()
            break
        page = entry.get("page_name") or "default"
        vpath = entry.get("video_path") or ""
        if not vpath or not Path(vpath).is_file():
            _LOG.warning("Pending upload skipped — video file missing: %s", vpath)
            remaining.remove(entry)
            continue
        if dry_run:
            print(f"[YouTube][dry-run] Would upload: {vpath} (page={page})")
            gate.record_success()
            remaining.remove(entry)
            continue
        try:
            if page not in _yt_clients:
                creds = build_credentials(page, client_secrets_path, token_dir)
                _yt_clients[page] = build_youtube_client(creds)
            youtube = _yt_clients[page]

            publish_at: Optional[datetime] = None
            raw_pa = entry.get("publish_at")
            if raw_pa:
                try:
                    parsed = datetime.fromisoformat(raw_pa)
                    if parsed.tzinfo is None:
                        parsed = parsed.replace(tzinfo=timezone.utc)
                    if parsed > datetime.now(timezone.utc):
                        publish_at = parsed
                except ValueError:
                    publish_at = None
            if publish_at is None and entry.get("publish_at"):
                # Original slot already passed — reschedule to the next open
                # slot rather than silently dropping the "Programado" intent.
                try:
                    publish_at = get_next_publish_slot(youtube)
                except Exception:  # noqa: BLE001
                    publish_at = None

            vid_id, url, pa = upload_short(
                video_path=vpath,
                title=entry.get("title", ""),
                description=entry.get("description", ""),
                tags=entry.get("tags") or [],
                privacy_status=entry.get("privacy_status", _DEFAULT_PRIVACY),
                publish_at=publish_at,
                category_id=entry.get("category_id", _DEFAULT_CATEGORY_ID),
                page_name=page,
                client_secrets_path=client_secrets_path,
                token_dir=token_dir,
                playlist_title=entry.get("playlist_title"),
                playlist_description=entry.get("playlist_description"),
                youtube=youtube,
            )
            uploaded.append({"video_path": vpath, "video_id": vid_id, "url": url})
            remaining.remove(entry)
            gate.record_success()
            with _QUEUE_LOCK:
                _write_pending_uploads(other_items + remaining)
            print(f"[YouTube] ✓ Resumed upload → {url}")
        except YouTubeQuotaExceededError:
            _LOG.warning(
                "[YouTube] Daily upload limit (20 videos) reached again while "
                "resuming queue — %d video(s) remain queued.",
                len(remaining),
            )
            quota_hit_again = True
            break
        except Exception as exc:  # noqa: BLE001
            _LOG.error("Resume upload failed for %s: %s — leaving queued.", vpath, exc)
            continue

    if not dry_run:
        with _QUEUE_LOCK:
            _write_pending_uploads(other_items + remaining)

    print(
        f"[YouTube] Resume complete: {len(uploaded)} uploaded, "
        f"{len(remaining)} still pending"
        + (" (quota hit again)" if quota_hit_again else "")
        + (" (safety cap)" if safety_cap_hit else "")
        + "."
    )
    return {
        "uploaded": uploaded,
        "still_pending": len(remaining),
        "quota_hit_again": quota_hit_again,
        "safety_cap_hit": safety_cap_hit,
        "limit": gate.limit,
    }


# ---------------------------------------------------------------------------
# Convenience wrapper for the envelope row format
# ---------------------------------------------------------------------------

def upload_short_from_envelope(
    row: dict,
    page_name: str,
    privacy_status: str = _DEFAULT_PRIVACY,
    publish_at: Optional[datetime] = None,
    client_secrets_path: Optional[str | Path] = None,
    token_dir: Optional[str | Path] = None,
    youtube=None,
) -> tuple[str, str, Optional[datetime]]:
    """Upload the reel described by *row* using page-isolated credentials."""
    video_path = row.get("video_path") or ""
    if not video_path:
        _LOG.warning("upload_short_from_envelope: no video_path in row — skipping.")
        return "", "", None

    _raw_title = (
        row.get("title")
        or row.get("topic")
        or row.get("subject")
        or row.get("overlay_text")
        or ""
    )
    if not _raw_title:
        _stem = Path(str(video_path)).stem
        _stem = _re.sub(r"_v\d+$", "", _stem)
        _raw_title = _stem.replace("reel_", "").replace("_", " ").title()
    title = (_raw_title or "Untitled Short")[:100]
    description = row.get("caption") or row.get("description") or ""
    tags_raw = row.get("tags") or []
    tags = tags_raw if isinstance(tags_raw, list) else [str(tags_raw)]

    return upload_short(
        video_path=video_path,
        title=title,
        description=description,
        tags=tags,
        privacy_status=privacy_status,
        publish_at=publish_at,
        page_name=page_name,
        client_secrets_path=client_secrets_path,
        token_dir=token_dir,
        youtube=youtube,
    )


# ---------------------------------------------------------------------------
# Standalone test entry-point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")

    ap = argparse.ArgumentParser(
        description="Test-upload / schedule a single MP4 to YouTube Shorts."
    )
    ap.add_argument("video", help="Path to the MP4 file to upload.")
    ap.add_argument("--title", default="Test Upload", help="Video title.")
    ap.add_argument(
        "--privacy",
        default="unlisted",
        choices=["public", "unlisted", "private"],
    )
    ap.add_argument(
        "--page",
        default="master_mei",
        help="Page name (selects youtube_token_{page}.json).",
    )
    ap.add_argument("--secrets", default=None, help="Override client_secret.json path.")
    ap.add_argument(
        "--schedule",
        action="store_true",
        help="Use smart scheduler instead of publishing immediately.",
    )
    ns = ap.parse_args()

    _publish_at: Optional[datetime] = None
    _creds = build_credentials(ns.page, ns.secrets)
    _yt_cli = build_youtube_client(_creds)
    verify_authorized_channel(_yt_cli, page_name=ns.page)
    if ns.schedule:
        _ch_id = _get_channel_id(_yt_cli)
        _publish_at = get_next_publish_slot(_yt_cli, _ch_id)
        print(f"[Scheduler] Next slot: {_publish_at.strftime('%Y-%m-%d %H:%M UTC')}")

    _desc = (
        "Subscribe to Master Mei | Mind Control for cold discipline, classical strategy, "
        "and true wealth sovereignty.\n"
        "#Shorts #MasterMei #Stoicism #FinancialFreedom #Discipline #SunTzu #WealthMindset"
        if _sanitize_page_name(ns.page) == "master_mei"
        else f"Uploaded for {ns.page}.\n#Shorts"
    )
    _vid_id, _url, _pa = upload_short(
        video_path=ns.video,
        title=ns.title,
        description=_desc,
        tags=_default_tags_for_page(ns.page),
        privacy_status=ns.privacy,
        publish_at=_publish_at,
        page_name=ns.page,
        client_secrets_path=ns.secrets,
        youtube=_yt_cli,
    )
    print(f"\nVideo ID    : {_vid_id}")
    print(f"URL         : {_url}")
    if _pa:
        print(f"Publish At  : {_pa.strftime('%Y-%m-%d %H:%M UTC')}")

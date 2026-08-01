"""publish_existing.py — Standalone CLI tool to publish, schedule, or update
existing MP4 files and already-uploaded YouTube Shorts via the factory's
``youtube_publisher`` module.

Usage examples
--------------
# Publish immediately (auto-loads caption from content_library.json):
python publish_existing.py \\
    --page ancient_knowledge \\
    --video "outputs/ancient_knowledge/clips/reel_what_if_sumerian_tablets_hold_pr_v01.mp4"

# Explicit title + caption override:
python publish_existing.py \\
    --page ancient_knowledge \\
    --video "outputs/.../reel_example_v01.mp4" \\
    --title "The Anunnaki Sumerian Tablets" \\
    --caption "Groundbreaking ancient history..."

# Schedule automatically (finds next free slot on your channel):
python publish_existing.py \\
    --page ancient_knowledge \\
    --video "outputs/.../reel_example_v01.mp4" \\
    --schedule

# Schedule at a specific UTC time:
python publish_existing.py \\
    --page ancient_knowledge \\
    --video "outputs/.../reel_example_v01.mp4" \\
    --publish-at "2026-07-24 14:00"

# Upload multiple files with auto drip-scheduling:
python publish_existing.py \\
    --page ancient_knowledge \\
    --video "reel_v01.mp4" "reel_v02.mp4" "reel_v03.mp4" \\
    --schedule

# ── Update metadata for an already-uploaded video ────────────────────────
# Update title + description by video ID (auto-loads caption from library):
python publish_existing.py \\
    --page ancient_knowledge \\
    --video-id "nSHx1ihMFPk" \\
    --update-only

# Update with explicit new caption:
python publish_existing.py \\
    --page ancient_knowledge \\
    --video-id "nSHx1ihMFPk" \\
    --update-only \\
    --title "The Anunnaki Sumerian Tablets (Updated)" \\
    --caption "New SEO-optimised description..."

# Dry-run preview without calling the API:
python publish_existing.py \\
    --page ancient_knowledge \\
    --video "reel_v01.mp4" "reel_v02.mp4" \\
    --schedule --dry-run
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Bootstrap: make sure the engine root is on the import path so that
# ``avatar_engine.*`` modules resolve correctly when the script is run from
# any working directory.
# ---------------------------------------------------------------------------
_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

# Load .env so YOUTUBE_* env vars, etc. are available.
try:
    from dotenv import load_dotenv
    load_dotenv(_SCRIPT_DIR / ".env", override=False)
except ImportError:
    pass

from avatar_engine.publishers.youtube_publisher import (  # noqa: E402
    build_credentials,
    build_youtube_client,
    get_or_create_playlist,
    add_video_to_playlist,
    get_next_publish_slot,
    advance_slot,
    upload_short,
    update_video_metadata,
    POST_INTERVAL_HOURS,
    YouTubeQuotaExceededError,
    queue_pending_upload,
    resume_pending_youtube_uploads,
)


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def _setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%H:%M:%S",
    )
    if not verbose:
        for _noisy in ("googleapiclient.discovery", "google.auth", "urllib3"):
            logging.getLogger(_noisy).setLevel(logging.WARNING)


# ---------------------------------------------------------------------------
# Content library metadata lookup
# ---------------------------------------------------------------------------

def _extract_hashtags(text: str) -> list[str]:
    """Return deduplicated hashtag strings (without the # prefix) from *text*."""
    return list(dict.fromkeys(tag.lower() for tag in re.findall(r"#(\w+)", text)))


def _library_path(page: str) -> Path:
    return _SCRIPT_DIR / "outputs" / page / "content_library.json"


def lookup_metadata_from_library(
    video_path: Path,
    page: str,
) -> dict[str, Any]:
    """Search ``content_library.json`` for an entry matching *video_path*.

    Matching strategy (in priority order):
      1. Exact ``video_path`` field match.
      2. Filename stem contained anywhere in a stored ``video_path`` value.
      3. When the library has exactly one entry, return it unconditionally
         (single-run convenience).

    Returns a dict with keys ``caption``, ``tags``, ``topic``, ``title``
    (all empty strings when the entry is not found).
    """
    lib_path = _library_path(page)
    if not lib_path.is_file():
        return {}

    try:
        rows: list[dict] = json.loads(lib_path.read_text(encoding="utf-8"))
        if not isinstance(rows, list) or not rows:
            return {}
    except (json.JSONDecodeError, OSError):
        return {}

    stem  = video_path.stem.lower()
    match: dict | None = None

    for row in rows:
        stored = str(row.get("video_path", "")).lower()
        if stored and (stored == str(video_path).lower() or stem in stored):
            match = row
            break

    if match is None and len(rows) == 1:
        match = rows[0]

    if match is None:
        return {}

    raw_caption: str = match.get("final_caption") or match.get("caption") or ""
    topic: str = match.get("topic") or ""
    return {
        "caption": raw_caption,
        "tags":    _extract_hashtags(raw_caption),
        "topic":   topic,
        "title":   topic[:100] if topic else "",
    }


def lookup_metadata_by_video_id(page: str, video_id: str) -> dict[str, Any]:
    """Search ``content_library.json`` for an entry matching a YouTube video ID."""
    lib_path = _library_path(page)
    if not lib_path.is_file():
        return {}
    try:
        rows = json.loads(lib_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    for row in (rows if isinstance(rows, list) else []):
        if str(row.get("youtube_video_id", "")) == video_id:
            raw_cap = row.get("final_caption") or row.get("caption") or ""
            return {
                "caption": raw_cap,
                "tags":    _extract_hashtags(raw_cap),
                "topic":   row.get("topic") or "",
            }
    return {}


# ---------------------------------------------------------------------------
# Core helpers
# ---------------------------------------------------------------------------

def resolve_video_paths(raw_paths: list[str]) -> list[Path]:
    resolved: list[Path] = []
    for raw in raw_paths:
        p = Path(raw)
        if not p.is_absolute():
            p = _SCRIPT_DIR / p
        if not p.is_file():
            print(f"[ERROR] Video file not found: {p}", file=sys.stderr)
            sys.exit(1)
        if p.suffix.lower() not in (".mp4", ".mov", ".mkv", ".webm"):
            print(f"[WARNING] Unexpected file extension '{p.suffix}' — continuing anyway.")
        resolved.append(p)
    return resolved


def build_tags(tags_raw: list[str], page: str, extra: list[str] | None = None) -> list[str]:
    """Merge CLI tags with page-level defaults and library-extracted hashtags."""
    defaults = {
        "ancient_knowledge": ["ancient knowledge", "ancient mysteries", "shorts", "history", "conspiracy"],
        "master_mei":        [
            "master mei", "mind control", "stoicism", "financial freedom",
            "wealth mindset", "business strategy", "self discipline", "shorts",
        ],
        "wonder_feed":       ["wonder feed", "amazing facts", "shorts", "mindblowing"],
        "anna_protocol":     ["relationship advice", "self-growth", "shorts"],
    }
    base = defaults.get(page.lower(), ["shorts"])
    all_tags: list[str] = list(base)
    for t in (extra or []) + tags_raw:
        if t and t not in all_tags:
            all_tags.append(t)
    return all_tags[:30]


def _title_from_stem(stem: str) -> str:
    """Convert ``reel_what_if_sumerian_tablets_v01`` → ``What If Sumerian Tablets``."""
    clean = re.sub(r"_v\d+$", "", stem)
    return clean.replace("reel_", "").replace("_", " ").title()


# ---------------------------------------------------------------------------
# Update-only flow
# ---------------------------------------------------------------------------

def run_update_only(
    video_id: str,
    page: str,
    title: str,
    caption: str,
    tags_raw: list[str],
    secrets_path: str | None,
    dry_run: bool,
) -> None:
    """Fetch an existing YouTube video and patch its snippet metadata."""
    log = logging.getLogger(__name__)

    creds   = build_credentials(page, secrets_path)
    youtube = build_youtube_client(creds)

    # Auto-load from library when metadata not explicitly supplied
    lib = lookup_metadata_by_video_id(page, video_id)

    eff_caption = caption or lib.get("caption", "")
    eff_title   = (title or lib.get("topic") or video_id)[:100]
    eff_tags    = build_tags(tags_raw, page, extra=lib.get("tags") or [])

    print(f"\n[Update] video_id = {video_id}")
    print(f"  Title   : {eff_title}")
    print(f"  Tags    : {', '.join(eff_tags[:6])}{'…' if len(eff_tags) > 6 else ''}")
    if eff_caption:
        print(f"  Caption : {eff_caption[:120]}{'…' if len(eff_caption) > 120 else ''}")
    if lib:
        print("  ↳ Metadata loaded from content_library.json")
    else:
        print("  ↳ No library entry found — using CLI values only")

    if dry_run:
        print("  [DRY RUN] — no API call made.")
        return

    try:
        updated_id = update_video_metadata(
            youtube,
            video_id=video_id,
            title=eff_title,
            description=eff_caption,
            tags=eff_tags,
        )
        print(f"  ✓ Metadata updated | https://youtu.be/{updated_id}")
        log.info("Metadata update OK | id=%s", updated_id)
    except Exception as exc:
        log.error("Metadata update failed for '%s': %s", video_id, exc, exc_info=True)
        print(f"  [ERROR] {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)


# ---------------------------------------------------------------------------
# Publish / schedule flow
# ---------------------------------------------------------------------------

def publish_videos(
    video_paths: list[Path],
    page: str,
    title_template: str,
    description: str,
    tags: list[str],
    privacy: str,
    schedule: bool,
    publish_at_override: datetime | None,
    secrets_path: str | None,
    interval_hours: int,
    dry_run: bool,
) -> None:
    log = logging.getLogger(__name__)

    creds   = build_credentials(page, secrets_path)
    youtube = build_youtube_client(creds)

    current_slot: datetime | None = None

    if publish_at_override:
        current_slot = publish_at_override
        log.info("Using explicit publish time: %s UTC", current_slot.strftime("%Y-%m-%d %H:%M"))
    elif schedule:
        current_slot = get_next_publish_slot(youtube, interval_hours=interval_hours)
        log.info(
            "Smart scheduler | first slot: %s UTC  interval: %dh  videos: %d",
            current_slot.strftime("%Y-%m-%d %H:%M"), interval_hours, len(video_paths),
        )
        print(
            f"\n[Scheduler] {len(video_paths)} video(s) will be scheduled "
            f"starting {current_slot.strftime('%Y-%m-%d %H:%M UTC')} "
            f"(every {interval_hours} h)"
        )

    results: list[dict] = []

    for idx, vpath in enumerate(video_paths, 1):
        # Per-video metadata: CLI values take priority; library fills gaps
        lib = lookup_metadata_from_library(vpath, page)

        vid_title = (title_template or lib.get("title") or _title_from_stem(vpath.stem))
        if len(video_paths) > 1 and vid_title:
            vid_title = f"{vid_title} [{idx}/{len(video_paths)}]"
        vid_title = vid_title[:100]

        vid_caption = description or lib.get("caption") or ""
        vid_tags    = build_tags(tags, page, extra=lib.get("tags") or [])

        print(f"\n[{idx}/{len(video_paths)}] {'(DRY RUN) ' if dry_run else ''}Processing: {vpath.name}")
        if lib.get("caption") and not description:
            print(f"  ↳ Caption auto-loaded from content_library.json ({len(vid_caption)} chars, "
                  f"{len(vid_tags)} tags)")
        elif not vid_caption:
            print("  ↳ No caption found — uploading without description")
        if current_slot:
            print(f"  → Scheduled publish: {current_slot.strftime('%Y-%m-%d %H:%M UTC')}")
        else:
            print(f"  → Privacy: {privacy}")

        if dry_run:
            results.append({
                "file":       str(vpath),
                "title":      vid_title,
                "caption":    vid_caption[:80] + ("…" if len(vid_caption) > 80 else ""),
                "publish_at": current_slot.isoformat() if current_slot else "immediate",
                "status":     "dry_run",
            })
            if current_slot:
                current_slot = advance_slot(current_slot, interval_hours)
            continue

        try:
            vid_id, yt_url, pa = upload_short(
                video_path      = vpath,
                title           = vid_title,
                description     = vid_caption,
                tags            = vid_tags,
                privacy_status  = privacy,
                publish_at      = current_slot,
                page_name       = page,
                client_secrets_path = secrets_path,
            )
            results.append({
                "file":       str(vpath),
                "video_id":   vid_id,
                "url":        yt_url,
                "title":      vid_title,
                "publish_at": pa.strftime("%Y-%m-%d %H:%M UTC") if pa else "immediate",
                "status":     "scheduled" if pa else "published",
            })
            log.info("OK | id=%s url=%s publish_at=%s", vid_id, yt_url,
                     pa.strftime("%Y-%m-%d %H:%M UTC") if pa else "immediate")
        except YouTubeQuotaExceededError as exc:
            log.warning(
                "[YouTube] Daily upload limit (20 videos) reached for this channel."
            )
            print(
                "\n[YouTube] Daily upload limit (20 videos) reached for this channel. "
                f"Queuing remaining {len(video_paths) - idx + 1} video(s) → "
                "credentials/pending_youtube_uploads.json"
            )
            for _remaining_vpath in video_paths[idx - 1:]:
                _r_lib = lookup_metadata_from_library(_remaining_vpath, page)
                _r_title = (title_template or _r_lib.get("title") or _title_from_stem(_remaining_vpath.stem))[:100]
                queue_pending_upload(
                    page_name=page,
                    video_path=str(_remaining_vpath),
                    title=_r_title,
                    description=description or _r_lib.get("caption") or "",
                    tags=build_tags(tags, page, extra=_r_lib.get("tags") or []),
                    privacy_status=privacy,
                    publish_at=current_slot,
                    category_id="27",
                    reason="daily_upload_limit_exceeded",
                )
                results.append({"file": str(_remaining_vpath), "status": "failed", "error": str(exc)})
                if current_slot:
                    current_slot = advance_slot(current_slot, interval_hours)
            break
        except Exception as exc:
            log.error("Upload failed for '%s': %s", vpath.name, exc, exc_info=True)
            results.append({"file": str(vpath), "status": "failed", "error": str(exc)})

        if current_slot:
            current_slot = advance_slot(current_slot, interval_hours)

    _print_summary(results, dry_run)


def _print_summary(results: list[dict], dry_run: bool) -> None:
    sep = "+" + "=" * 66 + "+"
    print(f"\n{sep}")
    label = "DRY RUN PREVIEW" if dry_run else "UPLOAD SUMMARY"
    print(f"| {label:<64} |")
    print(sep)

    ok  = [r for r in results if r["status"] in ("published", "scheduled", "dry_run")]
    err = [r for r in results if r["status"] == "failed"]

    for r in ok:
        fn = Path(r["file"]).name
        if r["status"] == "dry_run":
            print(f"  [DRY] {fn}")
            print(f"        Title      : {r['title']}")
            print(f"        Caption    : {r.get('caption', '(empty)')}")
            print(f"        Publish at : {r.get('publish_at', 'immediate')}")
        else:
            print(f"  [OK]  {fn}")
            print(f"        URL        : {r.get('url', '?')}")
            print(f"        Publish at : {r.get('publish_at', 'immediate')}")
        print()

    for r in err:
        fn = Path(r["file"]).name
        print(f"  [ERR] {fn}")
        print(f"        Error : {r.get('error', '?')}")
        print()

    print(sep)
    print(f"  Total: {len(results)} | OK: {len(ok)} | Failed: {len(err)}")
    print(sep)


# ---------------------------------------------------------------------------
# CLI argument parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="publish_existing",
        description=(
            "Publish, schedule, or update YouTube Shorts.\n\n"
            "Caption and tags are auto-loaded from outputs/{page}/content_library.json\n"
            "when --caption / --tags are not explicitly provided."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    ap.add_argument("--page", "-p", default=None, metavar="PAGE",
                    help="Page ID (e.g. ancient_knowledge). Selects token_{page}.json. "
                         "Required unless --resume-youtube-queue is used without --page "
                         "(resumes ALL pages' queued uploads).")

    # ── Video source ─────────────────────────────────────────────────────────
    # Not required when --resume-youtube-queue is used instead of a direct upload.
    _src = ap.add_mutually_exclusive_group(required=False)
    _src.add_argument("--video", "-v", nargs="+", metavar="PATH",
                      help="Path(s) to MP4 file(s) to upload / schedule.")
    _src.add_argument("--video-id", dest="video_id", metavar="VIDEO_ID",
                      help="YouTube video ID for --update-only operations.")
    _src.add_argument("--resume-youtube-queue", dest="resume_youtube_queue", action="store_true",
                      help=(
                          "Read credentials/pending_youtube_uploads.json and publish any "
                          "videos queued after a previous run hit YouTube's daily upload "
                          "limit (~20 videos/channel/day). Scoped to --page if given, "
                          "otherwise resumes ALL pages."
                      ))

    # ── Mode ─────────────────────────────────────────────────────────────────
    ap.add_argument("--update-only", dest="update_only", action="store_true",
                    help="Update metadata of an already-uploaded video (requires --video-id).")

    # ── Metadata ─────────────────────────────────────────────────────────────
    ap.add_argument("--title", "-t", default="", metavar="TITLE",
                    help="Video title. Auto-derived from filename / library if omitted.")
    ap.add_argument("--caption", "--description", "-d", dest="caption", default="", metavar="TEXT",
                    help="Video description. Auto-loaded from content_library.json if omitted.")
    ap.add_argument("--tags", nargs="*", default=[], metavar="TAG",
                    help="Extra tags. Page defaults + library hashtags are always included.")
    ap.add_argument("--category", default="27", metavar="ID",
                    help="YouTube category ID (default: 27 = Education).")

    # ── Scheduling ───────────────────────────────────────────────────────────
    _sched = ap.add_mutually_exclusive_group()
    _sched.add_argument("--schedule", "-s", action="store_true",
                        help="Auto-detect next free slot and schedule with --interval spacing.")
    _sched.add_argument("--publish-at", dest="publish_at", default=None, metavar="DATETIME",
                        help="Schedule at a specific UTC time: 'YYYY-MM-DD HH:MM'.")
    _sched.add_argument("--privacy", default=None, choices=["public", "unlisted", "private"],
                        metavar="STATUS", help="Privacy for immediate upload (default: unlisted).")

    ap.add_argument("--interval", dest="interval_hours", type=int,
                    default=POST_INTERVAL_HOURS, metavar="HOURS",
                    help=f"Hours between scheduled posts (default: {POST_INTERVAL_HOURS}).")

    # ── Auth ─────────────────────────────────────────────────────────────────
    ap.add_argument("--secrets", default=None, metavar="PATH",
                    help="Override client_secret.json path.")

    # ── Misc ─────────────────────────────────────────────────────────────────
    ap.add_argument("--dry-run", "-n", dest="dry_run", action="store_true",
                    help="Preview without calling the API.")
    ap.add_argument("--verbose", action="store_true", help="Enable debug-level logging.")

    return ap


# ---------------------------------------------------------------------------
# Entry-point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args   = parser.parse_args(argv)
    _setup_logging(args.verbose)

    # ── RESUME-QUEUE path — reads pending_youtube_uploads.json, no video/page
    # source required beyond an optional --page scope filter. ─────────────
    if getattr(args, "resume_youtube_queue", False):
        print("\n" + "=" * 68)
        print("  YouTube Pending-Upload Queue Resume — Unified Multi-Page Factory")
        print("=" * 68)
        print(f"  Scope : {args.page if args.page else 'ALL pages'}")
        if args.dry_run:
            print("  *** DRY RUN — no API calls will be made ***")
        print("=" * 68)
        resume_pending_youtube_uploads(
            page_name=args.page,
            client_secrets_path=args.secrets,
            dry_run=args.dry_run,
        )
        return

    # Guard: exactly one video source is required for every other mode.
    if not args.video and not args.video_id:
        parser.error("one of the arguments --video/-v --video-id --resume-youtube-queue is required")
    if not args.page:
        parser.error("--page is required (unless using --resume-youtube-queue without --page).")

    # Guard: --update-only ↔ --video-id are coupled
    if args.update_only and not args.video_id:
        parser.error("--update-only requires --video-id.")
    if args.video_id and not args.update_only:
        parser.error("--video-id is only valid with --update-only.")

    # ── UPDATE-ONLY path ────────────────────────────────────────────────────
    if args.update_only:
        print("\n" + "=" * 68)
        print("  YouTube Metadata Update — Unified Multi-Page Factory")
        print("=" * 68)
        print(f"  Page     : {args.page}")
        print(f"  Video ID : {args.video_id}")
        if args.dry_run:
            print("  *** DRY RUN — no API calls will be made ***")
        print("=" * 68)
        run_update_only(
            video_id    = args.video_id,
            page        = args.page,
            title       = args.title,
            caption     = args.caption,
            tags_raw    = args.tags or [],
            secrets_path= args.secrets,
            dry_run     = args.dry_run,
        )
        return

    # ── UPLOAD / SCHEDULE path ──────────────────────────────────────────────
    video_paths = resolve_video_paths(args.video)

    title = args.title
    if not title:
        title = _title_from_stem(video_paths[0].stem)
        logging.getLogger(__name__).info("No --title — derived: '%s'", title)

    explicit_slot: datetime | None = None
    if args.publish_at:
        try:
            explicit_slot = datetime.strptime(args.publish_at, "%Y-%m-%d %H:%M").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            print(f"[ERROR] --publish-at must be 'YYYY-MM-DD HH:MM' (UTC), got '{args.publish_at}'",
                  file=sys.stderr)
            sys.exit(1)

    privacy = args.privacy or "unlisted"
    tags    = build_tags(args.tags or [], args.page)

    print("\n" + "=" * 68)
    print("  YouTube Shorts Publisher — Unified Multi-Page Factory")
    print("=" * 68)
    print(f"  Page          : {args.page}")
    print(f"  Videos        : {len(video_paths)}")
    for vp in video_paths:
        mb       = vp.stat().st_size / 1_048_576
        lib_info = lookup_metadata_from_library(vp, args.page)
        lib_hint = " [library ✓]" if lib_info.get("caption") else " [no library entry]"
        print(f"    • {vp.name} ({mb:.1f} MB){lib_hint}")
    print(f"  Base title    : {title[:80]}{'...' if len(title) > 80 else ''}")
    print(f"  Category ID   : {args.category}")
    if explicit_slot:
        print(f"  First slot    : {explicit_slot.strftime('%Y-%m-%d %H:%M UTC')} (manual)")
    elif args.schedule:
        print(f"  Scheduling    : auto (interval {args.interval_hours} h)")
    else:
        print(f"  Privacy       : {privacy} (immediate)")
    if args.dry_run:
        print("  *** DRY RUN — no API calls will be made ***")
    print("=" * 68)

    publish_videos(
        video_paths         = video_paths,
        page                = args.page,
        title_template      = title,
        description         = args.caption,
        tags                = tags,
        privacy             = privacy,
        schedule            = args.schedule,
        publish_at_override = explicit_slot,
        secrets_path        = args.secrets,
        interval_hours      = args.interval_hours,
        dry_run             = args.dry_run,
    )


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""CLI for Endless Summer Paradise library-ingest + YouTube schedule.

Source videos stay on the MEDIAUPSCALE production drive. Metadata comes from
``FACTORY_OUTPUT/global_video_library.json``. Duration gate: keep only files
with runtime strictly greater than 40 seconds. Missing metadata →
``outputs/endless_summer_paradise/needs_metadata/``.

Typical first run
-----------------
    python channels_config/endless_summer_paradise/esp_main.py scan
    python channels_config/endless_summer_paradise/esp_main.py scan --dry-report
    python channels_config/endless_summer_paradise/esp_main.py schedule --dry-run
    python channels_config/endless_summer_paradise/esp_main.py schedule --limit 1
    python channels_config/endless_summer_paradise/esp_main.py schedule --interval 84h --limit 1
    python channels_config/endless_summer_paradise/esp_main.py reschedule --dry-run
    python channels_config/endless_summer_paradise/esp_main.py reschedule --interval 84h

YouTube token:
    credentials/tokens/youtube_token_endless_summer_paradise.json
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

_FACTORY_ROOT = Path(__file__).resolve().parents[2]
if str(_FACTORY_ROOT) not in sys.path:
    sys.path.insert(0, str(_FACTORY_ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(_FACTORY_ROOT / ".env", override=False)
except ImportError:
    pass

from channels_config.endless_summer_paradise.handler import (  # noqa: E402
    load_schedule_queue,
    print_scan_report,
    reschedule_existing_conflicts,
    scan_and_ingest,
    schedule_ready_uploads,
)


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%H:%M:%S",
    )
    if not verbose:
        for noisy in ("googleapiclient.discovery", "google.auth", "urllib3", "moviepy"):
            logging.getLogger(noisy).setLevel(logging.WARNING)


def _resolve_interval_arg(args: argparse.Namespace) -> str | float:
    """Prefer ``--interval 84h``; fall back to legacy ``--interval-hours``."""
    raw = getattr(args, "interval", None)
    if raw is not None and str(raw).strip():
        return str(raw).strip()
    hours = getattr(args, "interval_hours", None)
    if hours is not None:
        return float(hours)
    return "84h"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="esp_main.py",
        description=(
            "Endless Summer Paradise — scan production masters (duration > 40s), "
            "map global_video_library metadata, stage needs_metadata, schedule YouTube."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_scan = sub.add_parser(
        "scan",
        help="Scan source dir, filter duration > 40s, map library, stage needs_metadata.",
    )
    p_scan.add_argument(
        "--source-dir",
        default=None,
        help="Override Endless Summers Paradise production folder.",
    )
    p_scan.add_argument(
        "--library",
        default=None,
        help="Override path to global_video_library.json.",
    )
    p_scan.add_argument(
        "--min-duration",
        type=float,
        default=None,
        help="Override minimum duration gate (default 40). Keep files with duration > N.",
    )
    p_scan.add_argument(
        "--no-stage",
        action="store_true",
        help="Do not copy missing-metadata files into needs_metadata/.",
    )
    p_scan.add_argument(
        "--dry-report",
        action="store_true",
        help="Print report without writing asset map / queue JSON.",
    )
    p_scan.add_argument("-v", "--verbose", action="store_true")

    p_q = sub.add_parser("queue", help="Show the current ready schedule queue.")
    p_q.add_argument("-v", "--verbose", action="store_true")

    p_sched = sub.add_parser(
        "schedule",
        help="Upload ready queue as private + publishAt (YouTube Scheduler).",
    )
    p_sched.add_argument(
        "--interval",
        default="84h",
        help=(
            "Gap between publishAt slots. Accepts 84h, 12h, 24h, 2d, or a bare "
            "hour count (default: 84h). Next slot = max existing future publishAt + interval."
        ),
    )
    p_sched.add_argument(
        "--interval-hours",
        type=float,
        default=None,
        help="Deprecated alias for --interval (numeric hours only).",
    )
    p_sched.add_argument(
        "--limit",
        type=int,
        default=None,
        help=(
            "Override global daily upload safety cap for this run "
            "(default: MAX_DAILY_UPLOADS=20). Remaining queue items stay for next run."
        ),
    )
    p_sched.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview schedule slots without uploading.",
    )
    p_sched.add_argument("-v", "--verbose", action="store_true")

    p_re = sub.add_parser(
        "reschedule",
        help=(
            "Scan scheduled YouTube videos for identical/overlapping publishAt "
            "and re-index them with a fixed interval (default 84h)."
        ),
    )
    p_re.add_argument(
        "--interval",
        default="84h",
        help="Gap used when re-indexing conflicting publishAt slots (default: 84h).",
    )
    p_re.add_argument(
        "--interval-hours",
        type=float,
        default=None,
        help="Deprecated alias for --interval (numeric hours only).",
    )
    p_re.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview reschedule plan without calling videos.update.",
    )
    p_re.add_argument(
        "--apply",
        action="store_true",
        help="Apply videos.update changes (omit --dry-run). Default is dry-run.",
    )
    p_re.add_argument("-v", "--verbose", action="store_true")

    args = parser.parse_args(argv)
    _setup_logging(getattr(args, "verbose", False))

    if args.command == "scan":
        report = scan_and_ingest(
            source_dir=args.source_dir,
            library_path=args.library,
            min_duration_s=args.min_duration,
            stage_missing=not args.no_stage,
            persist=not args.dry_report,
        )
        print_scan_report(report)
        return 0

    if args.command == "queue":
        queue = load_schedule_queue()
        items = list(queue.get("items") or [])
        print(f"[ESP] Ready queue: {len(items)} item(s)")
        for i, row in enumerate(items, 1):
            print(
                f"  {i:3d}. {row.get('duration_s', '?')}s  "
                f"{Path(str(row.get('video_path') or '')).name}  |  "
                f"{str(row.get('title') or '')[:70]}"
            )
        return 0

    if args.command == "schedule":
        interval = _resolve_interval_arg(args)
        result = schedule_ready_uploads(
            interval_hours=interval,
            dry_run=bool(args.dry_run),
            limit=args.limit,
        )
        slots = [
            str(row.get("publish_at") or "")
            for row in (result.get("uploaded") or [])
        ]
        print(
            f"[ESP] Done | attempted={result['attempted']} "
            f"uploaded={len(result['uploaded'])} errors={len(result['errors'])} "
            f"interval={result.get('interval')}"
        )
        for i, slot in enumerate(slots, 1):
            print(f"  slot {i}: {slot}")
        return 0 if not result["errors"] else 1

    if args.command == "reschedule":
        interval = _resolve_interval_arg(args)
        # Default dry-run unless --apply is passed.
        dry = True if not bool(getattr(args, "apply", False)) else False
        if bool(args.dry_run):
            dry = True
        result = reschedule_existing_conflicts(
            interval_hours=interval,
            dry_run=dry,
        )
        print(
            f"[ESP] Reschedule done | scanned={result.get('scanned', 0)} "
            f"conflicts={result.get('conflicts', 0)} "
            f"updated={len(result.get('updated') or [])} dry_run={result.get('dry_run')}"
        )
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())

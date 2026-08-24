# -*- coding: utf-8 -*-
"""CLI for the Principles of Wealth library-ingest + YouTube publish fabric.

Source videos stay on the external production drive (read-only). Processed
files are written to a Processed/ sibling folder. YouTube tokens:
``credentials/tokens/youtube_token_principles_of_wealth_finance_economics.json``.

Typical first run
-----------------
    python wealth_main.py scan
    python wealth_main.py process --episodes 1-2 --dry-run
    python wealth_main.py publish --mode longs --privacy-long unlisted --episodes 1
    python wealth_main.py publish --mode shorts --episodes 1
    python wealth_main.py playlists
    python wealth_main.py status
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

try:
    from dotenv import load_dotenv

    load_dotenv(_SCRIPT_DIR / ".env", override=False)
except ImportError:
    pass

from core_engine.principles_of_wealth.pipeline import (  # noqa: E402
    run_playlists,
    run_process,
    run_publish,
    run_scan,
    run_status,
)


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%H:%M:%S",
    )
    if not verbose:
        for noisy in ("googleapiclient.discovery", "google.auth", "urllib3"):
            logging.getLogger(noisy).setLevel(logging.WARNING)


def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--source-dir",
        default=None,
        help="Override the production folder (default: page_config.SOURCE_DIRECTORY).",
    )
    p.add_argument(
        "--episodes",
        default=None,
        help="Episode filter, e.g. 1,2,5-8. Default = every matched episode.",
    )
    p.add_argument("--dry-run", action="store_true", help="Preview without writing or uploading.")
    p.add_argument("-v", "--verbose", action="store_true")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="wealth_main.py",
        description=(
            "Principles of Wealth — scan the production drive, re-sign assets, "
            "upload longs then Shorts (relatedVideoId), and build ACT playlists."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_scan = sub.add_parser("scan", help="Match Ray Dalio epN / Short N / ThumbN to the 30-episode catalog.")
    _add_common(p_scan)

    p_proc = sub.add_parser("process", help="FFmpeg uniqueness pass + thumbnail re-sign into Processed/.")
    _add_common(p_proc)
    p_proc.add_argument("--processed-dir", default=None, help="Override Processed/ output folder.")
    p_proc.add_argument("--force", action="store_true", help="Re-process even when output already exists.")
    p_proc.add_argument("--no-hwaccel", action="store_true", help="Disable FFmpeg -hwaccel auto.")
    p_proc.add_argument(
        "--hw-encode",
        action="store_true",
        help="Use h264_nvenc instead of libx264 ultrafast (NVIDIA GPU).",
    )

    p_pub = sub.add_parser(
        "publish",
        help="Upload longs first (private/unlisted), then Shorts linked to those IDs.",
    )
    _add_common(p_pub)
    p_pub.add_argument(
        "--mode",
        choices=["longs", "shorts", "all"],
        default="all",
        help="all = longs then Shorts then playlists (default).",
    )
    p_pub.add_argument(
        "--privacy-long",
        choices=["private", "unlisted", "public"],
        default="unlisted",
    )
    p_pub.add_argument(
        "--privacy-short",
        choices=["private", "unlisted", "public"],
        default="unlisted",
    )
    p_pub.add_argument(
        "--use-originals",
        action="store_true",
        help="Upload source files instead of Processed/ uniqueness outputs.",
    )
    p_pub.add_argument(
        "--process-first",
        action="store_true",
        help="Run the uniqueness pass before uploading.",
    )
    p_pub.add_argument(
        "--reupload",
        action="store_true",
        help="Upload even when wealth_publish_state.json already has a video ID.",
    )
    p_pub.add_argument("--no-hwaccel", action="store_true")
    p_pub.add_argument("--hw-encode", action="store_true")

    p_pl = sub.add_parser("playlists", help="Create/sync the three ACT playlists in chronological order.")
    _add_common(p_pl)

    p_st = sub.add_parser("status", help="Show source matches vs uploaded IDs.")
    _add_common(p_st)

    args = parser.parse_args(argv)
    _setup_logging(getattr(args, "verbose", False))

    if args.command == "scan":
        run_scan(source_dir=args.source_dir)
        return 0
    if args.command == "process":
        run_process(
            source_dir=args.source_dir,
            processed_dir=getattr(args, "processed_dir", None),
            episodes=args.episodes,
            skip_existing=not args.force,
            hwaccel=not args.no_hwaccel,
            hw_encode=args.hw_encode,
            dry_run=args.dry_run,
        )
        return 0
    if args.command == "publish":
        run_publish(
            source_dir=args.source_dir,
            mode=args.mode,
            episodes=args.episodes,
            privacy_long=args.privacy_long,
            privacy_short=args.privacy_short,
            use_processed=not args.use_originals,
            process_first=args.process_first,
            skip_existing_uploads=not args.reupload,
            dry_run=args.dry_run,
            hwaccel=not args.no_hwaccel,
            hw_encode=args.hw_encode,
        )
        return 0
    if args.command == "playlists":
        run_playlists(
            source_dir=args.source_dir,
            episodes=args.episodes,
            dry_run=args.dry_run,
        )
        return 0
    if args.command == "status":
        run_status(source_dir=args.source_dir)
        return 0
    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

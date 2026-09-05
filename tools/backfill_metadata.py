# -*- coding: utf-8 -*-
"""Generic metadata auditor for every channel under ``channels_config/``.

Restores missing local store JSONs from the G: outputs tree, then catalogs
rendered MP4s into ``content_library.json`` + ``asset_library.json``.

    python tools/backfill_metadata.py --channel ancient_knowledge
    python tools/backfill_metadata.py --channel aiwake --dry-run
    python tools/backfill_metadata.py --all
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

_FACTORY = Path(__file__).resolve().parents[1]
if str(_FACTORY) not in sys.path:
    sys.path.insert(0, str(_FACTORY))

from modules.metadata_auditor import (  # noqa: E402
    print_audit_report,
    run_all_channels_backfill,
    run_channel_backfill,
)
from utils.pipeline_paths import discover_channel_ids  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="backfill-metadata",
        description=(
            "Restore channel store state from G: and backfill "
            "content_library.json / asset_library.json for any channel."
        ),
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--channel", help="Channel slug under channels_config/")
    group.add_argument("--all", action="store_true", help="Audit every discovered channel")
    parser.add_argument(
        "--outputs-dir",
        type=Path,
        help="Override {OUTPUT_PATH}/<channel> (single-channel only)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Scan without writing catalogs")
    parser.add_argument("--skip-restore", action="store_true", help="Do not pull missing files from G:")
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s | %(name)s | %(message)s",
    )
    restore = not args.skip_restore
    if args.all:
        if args.outputs_dir is not None:
            print("--outputs-dir cannot be combined with --all", file=sys.stderr)
            return 2
        known = discover_channel_ids()
        print(f"Auditing {len(known)} channel(s): {', '.join(known)}")
        results = run_all_channels_backfill(dry_run=args.dry_run, restore=restore)
        errored = False
        for channel, items in results.items():
            print_audit_report(channel, items, dry_run=args.dry_run)
            if any(item.status == "error" for item in items):
                errored = True
        return 2 if errored else 0

    items = run_channel_backfill(
        args.channel,
        outputs_dir=args.outputs_dir,
        dry_run=args.dry_run,
        restore=restore,
    )
    print_audit_report(args.channel, items, dry_run=args.dry_run)
    if any(item.status == "error" for item in items):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

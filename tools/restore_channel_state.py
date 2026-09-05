# -*- coding: utf-8 -*-
"""Restore missing ``channels_config/<channel>/store/`` files from G:.

    python tools/restore_channel_state.py --channel ancient_knowledge
    python tools/restore_channel_state.py --all
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

_FACTORY = Path(__file__).resolve().parents[1]
if str(_FACTORY) not in sys.path:
    sys.path.insert(0, str(_FACTORY))

from modules.durable_store import (  # noqa: E402
    restore_all_channels_state,
    restore_channel_state,
)
from utils.pipeline_paths import channel_store_dir, discover_channel_ids  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Copy missing local channel store files from the G: outputs tree.",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--channel", help="Channel slug under channels_config/")
    group.add_argument("--all", action="store_true")
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s | %(name)s | %(message)s",
    )
    if args.all:
        results = restore_all_channels_state(force=True)
        for channel, counts in results.items():
            print(
                f"{channel}: restored={counts['restored']} "
                f"skipped={counts['skipped']} store={channel_store_dir(channel)}"
            )
        return 0
    counts = restore_channel_state(args.channel, force=True)
    print(
        f"{args.channel}: restored={counts['restored']} "
        f"skipped={counts['skipped']} store={channel_store_dir(args.channel)}"
    )
    print(f"known channels: {', '.join(discover_channel_ids())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

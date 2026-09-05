# -*- coding: utf-8 -*-
"""Delete ephemeral build artifacts only.

Never touches ``channels_config/*/store/`` (topic history, transcripts,
libraries, publishing logs). Targets tmp/scratch/render trees and
``seq_run_*`` / ``lofi_run_*`` folders under ``OUTPUT_PATH``.

    python tools/cleanup_ephemeral.py --channel ancient_knowledge --dry-run
    python tools/cleanup_ephemeral.py --all
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

_FACTORY = Path(__file__).resolve().parents[1]
if str(_FACTORY) not in sys.path:
    sys.path.insert(0, str(_FACTORY))

from utils.pipeline_paths import (  # noqa: E402
    ProtectedStateError,
    channel_mirror_dir,
    contains_protected_store,
    discover_channel_ids,
    is_ephemeral_artifact_dir,
    outputs_root,
    safe_rmtree,
)

_LOG = logging.getLogger("cleanup_ephemeral")


def _candidate_dirs(channel: str | None) -> list[Path]:
    found: list[Path] = []
    roots = (
        [channel_mirror_dir(channel)]
        if channel
        else [outputs_root() / slug for slug in discover_channel_ids()]
    )
    roots.append(outputs_root() / "tmp")
    for root in roots:
        if not root.is_dir():
            continue
        try:
            children = list(root.iterdir())
        except OSError:
            continue
        for child in children:
            if child.is_dir() and is_ephemeral_artifact_dir(child):
                found.append(child)
        assets = root / "assets"
        if assets.is_dir():
            try:
                for child in assets.iterdir():
                    if child.is_dir() and is_ephemeral_artifact_dir(child):
                        found.append(child)
            except OSError:
                pass
    return found


def cleanup_ephemeral(
    *,
    channel: str | None = None,
    dry_run: bool = False,
) -> list[Path]:
    removed: list[Path] = []
    for path in _candidate_dirs(channel):
        if contains_protected_store(path):
            _LOG.error("skip protected path %s", path)
            continue
        if dry_run:
            print(f"would remove {path}")
            removed.append(path)
            continue
        try:
            safe_rmtree(path)
            print(f"removed {path}")
            removed.append(path)
        except ProtectedStateError as exc:
            _LOG.error("%s", exc)
        except OSError as exc:
            _LOG.warning("could not remove %s (%s)", path, exc)
    return removed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Remove ephemeral build artifacts. Never deletes channel store/.",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--channel", help="Limit to one channel outputs tree")
    group.add_argument("--all", action="store_true", help="Scan every channel outputs tree")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s | %(name)s | %(message)s",
    )
    channel = None if args.all or not args.channel else args.channel
    removed = cleanup_ephemeral(channel=channel, dry_run=args.dry_run)
    print(f"{'would remove' if args.dry_run else 'removed'} {len(removed)} ephemeral folder(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

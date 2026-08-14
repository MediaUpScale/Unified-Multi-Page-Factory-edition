# -*- coding: utf-8 -*-
"""
facebook_scheduler/reels_scheduler.py
=====================================
Automate **Reels** scheduling on Meta Business Suite via the dedicated
Reel composer endpoint.

Architecture
------------
Built on ``media_scheduler_base.UniversalComposerScheduler``:

* ``LocalMediaQueue`` — scan ``outputs/<channel>/clips/*.mp4``, track state in
  ``facebook_history.json``, move completed files to ``clips/posted_facebook/``
* Video → ``https://business.facebook.com/latest/reels_composer``
  (never the Universal photo Create-post flow)
* Photo schedulers (future) → Universal ``/latest/`` Create post
* CDP ``DOM.setFileInputFiles`` for large uploads (no OS focus steal)
* Dynamic interval — first: ``now + random(25–60) min``; later: ``last + 4h + random(0–60) min``

Usage
-----
    # Dry-run (scan queue, print plan — no browser clicks)
    python -m facebook_scheduler.reels_scheduler --channel master_mei --dry-run

    # Live schedule (attach to Dolphin CDP / running Business Suite tab)
    python -m facebook_scheduler.reels_scheduler --channel master_mei

    # Limit batch size
    python -m facebook_scheduler.reels_scheduler --channel master_mei --max 3

Requirements
------------
* Meta Business Suite UI language = **English**
* Dolphin{anty} (or Chrome) profile already open on business.facebook.com
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import TYPE_CHECKING

# Ensure project root is importable when run as a script
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from facebook_scheduler import config
from facebook_scheduler.facebook_scheduler import attach_to_dolphin_profile
from facebook_scheduler.logger import get_logger
from facebook_scheduler.media_scheduler_base import (
    REELS_COMPOSER_URL,
    MediaItem,
    UniversalComposerScheduler,
    is_video_file,
)

if TYPE_CHECKING:
    from playwright.sync_api import Page

_log = get_logger(__name__)

# Explicit wait after MP4 attach — Meta finishes Reel composer processing.
_REEL_CONVERSION_WAIT_MS = 10_000


class ReelsScheduler(UniversalComposerScheduler):
    """
    Schedule local ``.mp4`` Reels via the dedicated Reel composer.

    Flow (English UI)
    -----------------
    1. ``prepare_composer_for_item`` →
       ``https://business.facebook.com/latest/reels_composer``
       (dismisses ``Can't Read Files`` first).
    2. Wait for ``input[type=file]``; CDP ``DOM.setFileInputFiles``.
    3. Fill caption (``contenteditable`` / ``textarea``) without OS focus.
    4. Advance wizard → Schedule option → datetime → footer Schedule.

    Queue integrity
    ---------------
    ``facebook_history.json`` / ``posted_facebook/`` updates happen ONLY in
    ``run()`` after ``schedule_item()`` returns successfully. Upload failures
    raise and leave the file pending for retry.
    """

    format_type = "reel"
    next_clicks = 2  # Reel optimization: Next → Next → schedule screen

    def select_format(self) -> None:
        """No-op — Reel composer URL already selects the Reel product surface."""
        _log.info(
            "Reel composer endpoint active (%s) — no format card click.",
            REELS_COMPOSER_URL,
        )

    def prepare_composer_for_item(self, item: MediaItem) -> None:
        """
        Always force a fresh Reel composer load for every queue item.

        Meta leaves a success / post-schedule UI that hides "Add video";
        skipping navigation when already on ``reels_composer`` breaks batches.
        """
        if self.dry_run:
            return
        if not is_video_file(item.path):
            _log.warning(
                "Non-video file in ReelsScheduler queue (%s) — "
                "delegating to base photo/universal routing.",
                item.path.name,
            )
            super().prepare_composer_for_item(item)
            return

        self._dismiss_composer_error_modals()
        _log.info("Forcing navigation to fresh Reel Composer endpoint...")
        self.page.goto(REELS_COMPOSER_URL, wait_until="domcontentloaded")
        try:
            self.page.wait_for_load_state(
                "networkidle", timeout=config.LONG_TIMEOUT_MS
            )
        except Exception:
            pass
        # Give Meta's heavy React UI time to fully render the buttons
        self.page.wait_for_timeout(4_000)
        self._dismiss_composer_error_modals()

    def on_media_uploaded(self, item: MediaItem, *, is_video: bool) -> None:
        """After CDP attach, wait for Reel composer processing."""
        if not is_video:
            return
        _log.info(
            "Waiting %dms for Reel composer upload processing...",
            _REEL_CONVERSION_WAIT_MS,
        )
        self.page.wait_for_timeout(_REEL_CONVERSION_WAIT_MS)

    def prepare_caption(self, item: MediaItem) -> str:
        """
        Network-first caption routing (no truncation for library captions).

        ``facebook_caption`` → ``caption`` → ``final_caption`` /
        ``humanized_caption`` → sidecar → shortened stem fallback.
        """
        from facebook_scheduler.media_scheduler_base import LocalMediaQueue

        meta = item.metadata or {}
        text, source = LocalMediaQueue.resolve_caption(item.path, meta)
        item.caption = text
        item.caption_source = source
        if source == "fallback":
            text = LocalMediaQueue.shorten_caption(text)
        _log.info(
            "Caption source=%s (%d chars): %r",
            source,
            len(text),
            text[:100],
        )
        return text

    def fill_caption(self, caption: str) -> None:
        """
        Reel composer caption — delegates to base background-safe fill
        (``contenteditable`` / ``textarea`` / ``role=textbox``).
        """
        super().fill_caption(caption)

    def advance_composer(self) -> None:
        """Wait for upload, advance Reel wizard to the schedule screen."""
        super().advance_composer()


# ===========================================================================
# CLI
# ===========================================================================

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="reels_scheduler",
        description=(
            "Schedule local channel Reels (.mp4) on Meta Business Suite "
            "using the dedicated reels_composer endpoint."
        ),
    )
    ap.add_argument(
        "--channel",
        required=True,
        help='Channel folder name under outputs/ (e.g. "master_mei").',
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Scan queue and print plan without browser actions or state writes.",
    )
    ap.add_argument(
        "--max",
        type=int,
        default=None,
        metavar="N",
        help="Schedule at most N reels in this run.",
    )
    ap.add_argument(
        "--cdp",
        default="",
        help="CDP endpoint or port (default: auto-detect Dolphin / config).",
    )
    ap.add_argument(
        "--no-move",
        action="store_true",
        default=False,
        help="Keep files in clips/ after success (still write facebook_history.json).",
    )
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    from facebook_scheduler.media_scheduler_base import LocalMediaQueue

    queue = LocalMediaQueue(
        args.channel,
        media_subdir="clips",
        extensions=(".mp4",),
        move_on_success=not args.no_move,
    )
    pending = queue.scan_pending(format_type="reel")
    if args.max is not None:
        pending = pending[: max(0, args.max)]

    print(
        f"[reels_scheduler] channel={args.channel!r} pending={len(pending)} "
        f"composer={REELS_COMPOSER_URL}"
    )
    if args.dry_run:
        cursor = queue.next_schedule_datetime()
        print(f"[dry-run] first slot ≈ {cursor.strftime('%Y-%m-%d %H:%M')}")
        for i, item in enumerate(pending, 1):
            print(f"  {i:>3}  {item.filename}")
        return 0

    from playwright.sync_api import sync_playwright

    cdp_port: int | None = None
    if args.cdp:
        try:
            cdp_port = int(args.cdp.split(":")[-1].strip("/"))
        except ValueError:
            cdp_port = None

    with sync_playwright() as pw:
        _ctx, page = attach_to_dolphin_profile(pw, port=cdp_port)
        scheduler = ReelsScheduler(
            page,
            args.channel,
            dry_run=False,
            move_on_success=not args.no_move,
            max_items=args.max,
        )
        stats = scheduler.run()
        print(
            f"[reels_scheduler] done scheduled={stats['scheduled']} "
            f"failed={stats['failed']} skipped={stats.get('skipped', 0)}"
        )
        return 0 if stats["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

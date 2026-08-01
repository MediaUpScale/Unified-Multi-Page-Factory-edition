# -*- coding: utf-8 -*-
"""
facebook_scheduler/main.py
============================
Entry point for the Facebook post scheduler.

Usage
-----
Full run (connects to running browser, reads Google Sheet):
    python facebook_scheduler/main.py

Dry-run / simulation mode (no browser clicks, no sheet writes):
    python facebook_scheduler/main.py --dry-run

Fill schedule datetimes only (no posting):
    python facebook_scheduler/main.py --fill-times-only

Record mode (launch Playwright Codegen):
    python facebook_scheduler/main.py --record

Override sheet or worksheet:
    python facebook_scheduler/main.py --sheet-id <ID> --worksheet MySheet

Options
-------
  --dry-run           Simulate all actions (no browser interaction, no sheet writes).
  --fill-times-only   Only fill empty Column B datetimes, then exit.
  --record            Launch Playwright Codegen and exit.
  --sheet-id          Override GSHEET_ID from config.
  --worksheet         Override WORKSHEET_NAME from config.
  --background        Background preset name (default: no background).
  --cdp               CDP endpoint (default: http://localhost:9222).
  --url               Target page URL to navigate to before posting.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Ensure project root is on sys.path when run directly
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from facebook_scheduler import config
from facebook_scheduler.logger import get_logger
from facebook_scheduler.sheet import SheetQueue

_log = get_logger(__name__)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="facebook_scheduler",
        description="Schedule Facebook background text posts from Google Sheets.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        default=config.DRY_RUN,
        help="Simulate all actions without touching the browser or sheet.",
    )
    ap.add_argument(
        "--fill-times-only",
        action="store_true",
        default=False,
        help="Only fill empty Column B datetimes, then exit.",
    )
    ap.add_argument(
        "--record",
        action="store_true",
        default=False,
        help="Launch Playwright Codegen for workflow recording.",
    )
    ap.add_argument("--sheet-id",   default="", help="Override Google Sheet ID.")
    ap.add_argument("--worksheet",  default="", help="Override worksheet tab name.")
    ap.add_argument("--background", default="", help="(legacy) Background preset name.")
    ap.add_argument("--cdp",        default="", help="CDP endpoint URL.")
    ap.add_argument("--url",        default="", help="Navigate to this URL before posting.")
    ap.add_argument(
        "--interval",
        type=float,
        default=3.0,
        metavar="HOURS",
        help="Hours between scheduled posts (default: 3)",
    )
    ap.add_argument(
        "--set-bg",
        action="store_true",
        default=False,
        dest="set_bg",
        help=(
            "Training mode: interactively choose a background tile once "
            "and save it to credentials/bg_config.json for all future runs."
        ),
    )
    ap.add_argument(
        "--show-bg",
        action="store_true",
        default=False,
        dest="show_bg",
        help="Print the currently saved background tile config and exit.",
    )
    return ap


# ---------------------------------------------------------------------------
# Main logic
# ---------------------------------------------------------------------------

def main(argv: "list[str] | None" = None) -> int:
    args = build_parser().parse_args(argv)

    # Apply overrides
    if args.sheet_id:
        config.GSHEET_ID = args.sheet_id
    if args.worksheet:
        config.WORKSHEET_NAME = args.worksheet
    if args.cdp:
        config.CDP_ENDPOINT = args.cdp
    dry_run = args.dry_run

    # ------------------------------------------------------------------
    # Mode: Playwright Codegen recorder
    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    # Mode: Workflow recording session
    # ------------------------------------------------------------------
    if args.record:
        from playwright.sync_api import sync_playwright
        from facebook_scheduler.facebook_scheduler import (
            FacebookScheduler,
            attach_to_dolphin_profile,
        )
        from facebook_scheduler.workflow_recorder import (
            run_recording_session,
            workflow_path as _wf_path,
        )

        _log.info("Starting workflow recording session...")
        print()
        print("[Record] Connecting to Dolphin profile...")

        cdp_port: int | None = None
        if args.cdp:
            try:
                cdp_port = int(args.cdp.split(":")[-1].strip("/"))
            except ValueError:
                pass

        with sync_playwright() as pw:
            try:
                _ctx, page = attach_to_dolphin_profile(pw, port=cdp_port)
            except Exception as exc:
                _log.error("Could not attach to browser: %s", exc)
                return 1

            # Step 1: open composer and type sample text
            scheduler = FacebookScheduler(page=page, dry_run=False)
            print("[Record] Opening composer and typing sample text...")
            try:
                scheduler.open_composer()
                scheduler.type_post_text(
                    "Recording mode — testing background and schedule toggle. "
                    "Do not publish this post."
                )
                _log.info("Sample text typed. Entering recording mode.")
            except Exception as exc:
                _log.error("Could not open composer: %s", exc)
                return 1

            # Step 2: hand control to the user + capture their actions
            run_recording_session(page)

        print(f"[Record] Workflow saved to: {_wf_path()}")
        print("[Record] Run without --record to use the recorded workflow.")
        return 0

    # ------------------------------------------------------------------
    # Mode: Show saved background config
    # ------------------------------------------------------------------
    if args.show_bg:
        from facebook_scheduler.background_selector import list_presets
        print(f"\n[Background config] {list_presets()}\n")
        return 0

    # ------------------------------------------------------------------
    # Mode: Fill schedule datetimes only
    # ------------------------------------------------------------------
    _log.info(
        "Facebook Scheduler starting | dry_run=%s fill_only=%s set_bg=%s",
        dry_run, args.fill_times_only, args.set_bg,
    )

    # In dry-run mode, sheet access is optional — we can simulate without it
    queue = None
    if dry_run:
        try:
            queue = SheetQueue.from_config()
        except (FileNotFoundError, ValueError, OSError) as exc:
            _log.warning(
                "Could not open Google Sheet (dry-run mode -- continuing without it): %s", exc
            )
            print(
                "\n[DRY-RUN] NOTE: Google Sheet not accessible -- simulating with no rows.\n"
                "To test with real data set GSHEET_ID=<your-sheet-id> in .env\n"
                f"  Detail: {exc}\n"
            )
    else:
        try:
            queue = SheetQueue.from_config()
        except (FileNotFoundError, ValueError, OSError) as exc:
            _log.error("Failed to open Google Sheet: %s", exc)
            print(
                f"\n[ERROR] Cannot open Google Sheet: {exc}\n"
                "Set GSHEET_ID=<your-sheet-id> in .env or use --sheet-id <ID>\n"
            )
            return 1

    if args.fill_times_only:
        _log.info("Mode: fill-times-only")
        if queue is None:
            print("[Scheduler] Cannot fill times — sheet not accessible.")
            return 1
        n = queue.fill_schedule_datetimes(interval_hours=args.interval)
        print(f"[Scheduler] Filled {n} schedule datetime(s) in the sheet.")
        return 0

    # Always fill empty datetimes before scheduling
    if queue is not None:
        filled = queue.fill_schedule_datetimes(interval_hours=args.interval)
        if filled:
            _log.info("Auto-filled %d empty schedule datetime(s).", filled)

    # Fetch pending rows
    rows = queue.get_pending_rows() if queue is not None else []
    if not rows:
        print("[Scheduler] No pending posts found. Nothing to do.")
        return 0

    print(f"[Scheduler] Found {len(rows)} pending post(s).")

    # ------------------------------------------------------------------
    # Mode: Dry-run (simulation — no browser needed)
    # ------------------------------------------------------------------
    if dry_run:
        print("\n[DRY-RUN] Simulation mode — NO browser actions will execute.\n")
        _run_dry(rows)
        return 0

    # ------------------------------------------------------------------
    # Mode: Live run — attach to open Dolphin profile and schedule
    # ------------------------------------------------------------------
    from playwright.sync_api import sync_playwright
    from facebook_scheduler.facebook_scheduler import (
        FacebookScheduler,
        attach_to_dolphin_profile,
    )

    with sync_playwright() as pw:
        # Honour --cdp flag as an explicit port override
        cdp_port: int | None = None
        if args.cdp:
            try:
                # Accept full URLs like http://127.0.0.1:9225 or bare port "9225"
                cdp_port = int(args.cdp.split(":")[-1].strip("/"))
            except ValueError:
                pass

        _log.info("Attaching to open Dolphin profile (port=%s)...", cdp_port or "auto")
        try:
            _ctx, page = attach_to_dolphin_profile(pw, port=cdp_port)
        except Exception as exc:
            _log.error(
                "Could not attach to Dolphin profile: %s\n"
                "  Make sure the Dolphin{anty} profile is running and remote\n"
                "  debugging is enabled. Use DOLPHIN_PORT=<port> env var to\n"
                "  override the auto-detected port.",
                exc,
            )
            return 1

        # Optionally navigate to the target page
        if args.url:
            _log.info("Navigating to: %s", args.url)
            page.goto(args.url)
            page.wait_for_load_state("networkidle", timeout=config.LONG_TIMEOUT_MS)

        scheduler = FacebookScheduler(
            page=page,
            dry_run=False,
            training_mode=args.set_bg,
        )
        stats = scheduler.run(rows, sheet_queue=queue)

    _print_summary(stats)
    return 0 if stats["failed"] == 0 else 1


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run_dry(rows: list) -> None:
    """Print a simulation summary without touching the browser or sheet."""
    print(f"{'Row':>4}  {'Scheduled At':<20}  {'Text (preview)'}")
    print("-" * 70)
    for row in rows:
        sched = row.get("datetime_raw") or row.get("scheduled_dt") or "(no time)"
        if hasattr(sched, "strftime"):
            sched = sched.strftime("%Y-%m-%d %H:%M")
        text_preview = row.get("text", "")[:45]
        print(f"{row['row_index']:>4}  {str(sched):<20}  {text_preview}...")
    print()
    print(f"[DRY-RUN] Would schedule {len(rows)} post(s). No actions taken.")


def _print_summary(stats: dict) -> None:
    print()
    print("=" * 40)
    print("  Scheduler Summary")
    print("=" * 40)
    print(f"  Total rows   : {stats['total']}")
    print(f"  Scheduled    : {stats['scheduled']}")
    print(f"  Skipped      : {stats['skipped']}")
    print(f"  Failed       : {stats['failed']}")
    print("=" * 40)
    if stats["failed"]:
        print(f"  [!] Check screenshots in: {config.SCREENSHOTS_DIR}")


if __name__ == "__main__":
    sys.exit(main())

# -*- coding: utf-8 -*-
"""
pinterest_engine.scheduler
---------------------------
Human-Mimic Safe-Drip Scheduler.

Publishes 3-5 pins per session with randomised intervals between each pin
to mimic authentic human posting behaviour (avoids algorithmic spam flags).

Key behaviours:
  - Reads exclusively from master_inventory.json (never posts twice)
  - Updates posted_on_pinterest + pinterest_pin_id IMMEDIATELY after success
  - Saves master_inventory.json after EVERY pin (crash-safe)
  - Sleeps get_random_interval() minutes between pins (real sleep)
  - On 401 token expiry: logs, saves ledger, raises PinterestTokenExpiredError
  - --no-wait flag skips sleep (for testing / dry runs)

Interval config (in .env):
    MIN_INTERVAL_HOURS  default 3
    MAX_INTERVAL_HOURS  default 6
"""
from __future__ import annotations

import logging
import os
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

log = logging.getLogger(__name__)

_DEFAULT_MIN_HOURS = 3.0
_DEFAULT_MAX_HOURS = 6.0
_DEFAULT_PINS_MIN = 3
_DEFAULT_PINS_MAX = 5
_INTER_PIN_COURTESY_SEC = 3   # brief pause before starting sleep countdown


# ---------------------------------------------------------------------------
# Public scheduling helpers
# ---------------------------------------------------------------------------

def get_random_interval(
    min_hours: float | None = None,
    max_hours: float | None = None,
) -> int:
    """
    Return a random interval in MINUTES between min_hours and max_hours.
    Reads MIN_INTERVAL_HOURS / MAX_INTERVAL_HOURS from env if not passed.
    """
    if min_hours is None:
        try:
            min_hours = float(os.getenv("MIN_INTERVAL_HOURS", str(_DEFAULT_MIN_HOURS)))
        except ValueError:
            min_hours = _DEFAULT_MIN_HOURS
    if max_hours is None:
        try:
            max_hours = float(os.getenv("MAX_INTERVAL_HOURS", str(_DEFAULT_MAX_HOURS)))
        except ValueError:
            max_hours = _DEFAULT_MAX_HOURS

    min_minutes = int(min_hours * 60)
    max_minutes = int(max_hours * 60)
    return random.randint(min_minutes, max_minutes)


def _resolve_pins_per_run() -> int:
    env = os.getenv("PINTEREST_PINS_PER_DAY", "")
    if env.isdigit():
        return int(env)
    return random.randint(_DEFAULT_PINS_MIN, _DEFAULT_PINS_MAX)


def _sleep_with_log(minutes: int, label: str = "") -> None:
    """
    Sleep for `minutes` minutes, logging progress every 10 minutes.
    Can be interrupted cleanly with Ctrl-C.
    """
    total_sec = minutes * 60
    elapsed = 0
    chunk = min(600, total_sec)   # log every 10 min or less

    eta = datetime.now(timezone.utc).isoformat(timespec="seconds")
    log.info(
        "Waiting %d min (~%.1f h) before next pin. ETA: %s  %s",
        minutes, minutes / 60, eta, f"[{label}]" if label else "",
    )
    try:
        while elapsed < total_sec:
            time.sleep(chunk)
            elapsed += chunk
            remaining = total_sec - elapsed
            if remaining > 0:
                log.info(
                    "  ... %d min elapsed, %d min remaining.",
                    elapsed // 60, remaining // 60,
                )
    except KeyboardInterrupt:
        log.warning("Sleep interrupted by user (Ctrl-C). Continuing with next pin...")


# ---------------------------------------------------------------------------
# PinterestScheduler
# ---------------------------------------------------------------------------

class PinterestScheduler:
    """
    Orchestrates safe-drip publishing against master_inventory.json.

    Parameters
    ----------
    outputs_dir : Path, optional
        Root outputs/ directory.
    pins_per_run : int, optional
        Override for the number of pins to publish this session.
    dry_run : bool
        Full pipeline except the actual API call.
    no_wait : bool
        Skip the sleep interval (useful for testing).
    min_interval_hours : float, optional
        Override for MIN_INTERVAL_HOURS.
    max_interval_hours : float, optional
        Override for MAX_INTERVAL_HOURS.
    """

    def __init__(
        self,
        outputs_dir: Path | None = None,
        pins_per_run: int | None = None,
        dry_run: bool = False,
        no_wait: bool = False,
        min_interval_hours: float | None = None,
        max_interval_hours: float | None = None,
    ) -> None:
        import config as cfg  # noqa: PLC0415

        self.outputs_dir = outputs_dir or cfg.OUTPUTS_DIR
        self.dry_run = dry_run
        self.no_wait = no_wait
        self.min_interval_hours = min_interval_hours
        self.max_interval_hours = max_interval_hours
        self.pins_per_run = pins_per_run or _resolve_pins_per_run()

        log.info(
            "Scheduler ready: pins_per_run=%d  dry_run=%s  no_wait=%s  "
            "interval=%.1f-%.1fh",
            self.pins_per_run, dry_run, no_wait,
            min_interval_hours or float(os.getenv("MIN_INTERVAL_HOURS", "3")),
            max_interval_hours or float(os.getenv("MAX_INTERVAL_HOURS", "6")),
        )

    # ------------------------------------------------------------------

    def run(self) -> dict:
        """
        Execute one publishing session.

        Returns stats dict: {published, skipped, errors, remaining}.
        """
        from pinterest_engine.image_transformer import PinTransformer  # noqa: PLC0415
        from pinterest_engine.inventory import MasterInventory, validate_caption_safe  # noqa: PLC0415
        from pinterest_engine.publisher import (  # noqa: PLC0415
            PinterestPublisher,
            PinterestTokenExpiredError,
        )

        inv = MasterInventory(outputs_dir=self.outputs_dir)
        data = inv.load()

        if not data.get("entries"):
            log.warning(
                "master_inventory.json is empty or missing. "
                "Run: python pinterest_main.py sync"
            )
            return {"published": 0, "skipped": 0, "errors": 0, "remaining": 0}

        unposted = inv.get_unposted(data)
        total_eligible = len(unposted)
        batch = unposted[: self.pins_per_run]

        log.info(
            "Inventory: %d total | %d unposted | %d selected",
            len(data["entries"]), total_eligible, len(batch),
        )

        if not batch:
            log.warning("No unposted pins with Pinterest metadata. Run sync first.")
            return {"published": 0, "skipped": 0, "errors": 0, "remaining": 0}

        # Initialise tools
        transformer = PinTransformer(outputs_dir=self.outputs_dir)

        publisher = None
        if not self.dry_run:
            try:
                publisher = PinterestPublisher()
                if not publisher.validate_token():
                    from pinterest_engine.publisher import PinterestTokenExpiredError  # noqa: PLC0415
                    raise PinterestTokenExpiredError("Token validation failed before batch.")
            except PinterestTokenExpiredError:
                inv.save(data)
                raise

        stats = {"published": 0, "skipped": 0, "errors": 0, "remaining": total_eligible}

        for i, entry in enumerate(batch):
            post_id = entry["post_id"]
            topic = entry.get("topic", "?")
            title = entry.get("pinterest_title", topic)

            log.info(
                "[%d/%d] Processing: '%s'  post_id=%s",
                i + 1, len(batch), topic, post_id,
            )

            # --- Safety: validate caption ---
            caption = entry.get("pinterest_caption", "")
            valid, reason = validate_caption_safe(caption)
            if not valid:
                log.warning("  Caption safety check failed (%s) -- auto-fixing.", reason)
                from pinterest_engine.inventory import build_caption_regex  # noqa: PLC0415
                entry["pinterest_caption"] = build_caption_regex(
                    entry.get("original_caption", ""), entry.get("variant_index", 0)
                )

            # --- Transform image ---
            pin_bytes = transformer.get_pin_bytes(entry)
            if pin_bytes is None:
                log.warning("  No image for %s -- skipping.", post_id)
                stats["skipped"] += 1
                continue

            transformer.transform(entry)   # also save pin to disk for audit

            if self.dry_run:
                log.info(
                    "  [DRY RUN] Would publish '%s' (%d bytes pin image).",
                    title, len(pin_bytes),
                )
                inv.mark_posted(data, post_id, "DRY_RUN_PIN_ID")
                inv.save(data)
                stats["published"] += 1
            else:
                # --- Publish ---
                try:
                    result = publisher.publish(entry, pin_bytes)
                except Exception as exc:  # noqa: BLE001
                    from pinterest_engine.publisher import PinterestTokenExpiredError  # noqa: PLC0415
                    if isinstance(exc, PinterestTokenExpiredError):
                        inv.save(data)
                        raise
                    log.error("  Publish exception for %s: %s", post_id, exc)
                    stats["errors"] += 1
                    continue

                if result:
                    pin_id = result.get("id", "unknown")
                    inv.mark_posted(data, post_id, pin_id)
                    inv.save(data)          # crash-safe: save after EVERY success
                    stats["published"] += 1
                    log.info("  Published! Pin ID=%s", pin_id)
                else:
                    log.warning("  Soft failure for %s.", post_id)
                    stats["errors"] += 1

            # --- Human-mimic interval (skip after last pin) ---
            is_last = (i == len(batch) - 1)
            if not is_last and not self.no_wait:
                interval = get_random_interval(
                    self.min_interval_hours, self.max_interval_hours
                )
                time.sleep(_INTER_PIN_COURTESY_SEC)
                _sleep_with_log(interval, label=f"next: {batch[i+1].get('topic', '?')[:40]}")

        stats["remaining"] = max(0, total_eligible - stats["published"])
        log.info(
            "Session done -- published=%d  skipped=%d  errors=%d  remaining=%d",
            stats["published"], stats["skipped"],
            stats["errors"], stats["remaining"],
        )
        return stats

    # ------------------------------------------------------------------

    def show_status(self) -> None:
        """Print a rich queue/history summary."""
        from pinterest_engine.inventory import MasterInventory  # noqa: PLC0415

        inv = MasterInventory(outputs_dir=self.outputs_dir)
        data = inv.load()

        total = len(data.get("entries", []))
        posted = sum(
            1 for e in data.get("entries", [])
            if e.get("publication_status", {}).get("posted_on_pinterest")
        )
        unposted = inv.get_unposted(data)
        no_img = sum(
            1 for e in data.get("entries", [])
            if not e.get("local_image_path") and not e.get("imgbb_url")
        )
        no_meta = sum(
            1 for e in data.get("entries", [])
            if not e.get("pinterest_title")
        )

        print("\nPinterest Engine Status")
        print(f"  Inventory entries : {total}")
        print(f"  Published         : {posted}")
        print(f"  Queue (unposted)  : {len(unposted)}")
        print(f"  No image data     : {no_img}")
        print(f"  No Pinterest meta : {no_meta}  (run sync to generate)")

        if unposted:
            print(f"\n  Next {min(5, len(unposted))} in queue:")
            for e in unposted[:5]:
                print(
                    f"    [{e['post_id']}] {e.get('topic', '?')[:45]}"
                    f" | {e.get('pinterest_title', '(no title)')[:50]}"
                )
        print()

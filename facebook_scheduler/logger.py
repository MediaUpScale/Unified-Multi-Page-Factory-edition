# -*- coding: utf-8 -*-
"""
facebook_scheduler/logger.py
==============================
Centralised logging setup.

Usage
-----
    from facebook_scheduler.logger import get_logger
    log = get_logger(__name__)
    log.info("Something happened")
    log.error("Failure", exc_info=True)

Screenshots are saved via:
    from facebook_scheduler.logger import save_screenshot
    save_screenshot(page, "composer_error")
"""
from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.sync_api import Page

from facebook_scheduler import config

# ---------------------------------------------------------------------------
# Bootstrap (called once at import time)
# ---------------------------------------------------------------------------

_LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_root_configured = False


def _configure_root() -> None:
    global _root_configured
    if _root_configured:
        return

    config.LOGS_DIR.mkdir(parents=True, exist_ok=True)
    config.SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    # Console handler
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(logging.Formatter(_LOG_FORMAT, _DATE_FORMAT))
    root.addHandler(ch)

    # File handler — one log file per day
    today = datetime.now().strftime("%Y-%m-%d")
    log_path = config.LOGS_DIR / f"fb_scheduler_{today}.log"
    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(_LOG_FORMAT, _DATE_FORMAT))
    root.addHandler(fh)

    _root_configured = True


_configure_root()


def get_logger(name: str) -> logging.Logger:
    """Return a named logger; root handlers are already configured."""
    return logging.getLogger(name)


# ---------------------------------------------------------------------------
# Screenshot helper
# ---------------------------------------------------------------------------

def save_screenshot(page: "Page", label: str = "error") -> Path:
    """
    Capture a full-page screenshot and save it to SCREENSHOTS_DIR.

    Returns the saved file Path.
    """
    config.SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"{label}_{ts}.png"
    dest = config.SCREENSHOTS_DIR / filename

    try:
        page.screenshot(path=str(dest), full_page=True)
        get_logger(__name__).info("Screenshot saved -> %s", dest.name)
    except Exception as exc:
        get_logger(__name__).warning(
            "Could not save screenshot '%s': %s", label, exc
        )

    return dest

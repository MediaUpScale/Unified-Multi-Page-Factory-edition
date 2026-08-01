# -*- coding: utf-8 -*-
"""
facebook_scheduler/record_mode.py
===================================
Playwright Codegen launcher for recording Facebook scheduling workflows.

PURPOSE
-------
This module opens Playwright Codegen pointed at Facebook so you can record
the exact selectors needed by facebook_scheduler.py and background_selector.py.

HOW TO USE
----------
1. Make sure Chrome is NOT running with --remote-debugging-port already.
2. Run this file:
       python facebook_scheduler/record_mode.py

3. A Chrome browser window + Playwright Inspector (Codegen) will open.
4. Log in to Facebook manually if needed.
5. Navigate to the Momma Circle page.
6. Perform the scheduling workflow step by step:
     a. Click the "What's on your mind" / "Create post" button.
     b. Type some text in the composer.
     c. Click the background color button.
     d. Click a color tile.
     e. Click "Schedule" or "More options".
     f. Fill in the date/time fields.
     g. Click the final confirmation button.
     h. Observe the success banner.
7. Codegen will record every click, fill, and selector automatically.
8. Copy the generated selectors into the SELECTORS section of
   facebook_scheduler.py and into background_selector.py.

CAPTURED SELECTOR MAPPING
--------------------------
Step  | Target module             | Constant name in facebook_scheduler.py
------|---------------------------|----------------------------------------
a     | facebook_scheduler.py     | _SEL_COMPOSER_TRIGGER
b     | facebook_scheduler.py     | _SEL_COMPOSER_TEXTAREA
c     | background_selector.py    | _open_background_picker()
d     | background_selector.py    | _BACKGROUNDS dict
e     | facebook_scheduler.py     | _SEL_SCHEDULE_BUTTON
f     | facebook_scheduler.py     | _SEL_SCHEDULE_DATE_INPUT, _SEL_SCHEDULE_TIME_INPUT
g     | facebook_scheduler.py     | _SEL_SCHEDULE_CONFIRM
h     | facebook_scheduler.py     | _SEL_SUCCESS_BANNER
"""
from __future__ import annotations

import subprocess
import sys

from facebook_scheduler.logger import get_logger

_log = get_logger(__name__)

# Target URL opened in the recorder — change to your page's URL if needed
_RECORD_TARGET_URL = "https://www.facebook.com/MommaCircle"


def launch_codegen(
    target_url: str = _RECORD_TARGET_URL,
    output_file: str = "recorded_workflow.py",
) -> None:
    """
    Launch ``playwright codegen`` pointing at *target_url*.

    The generated code is printed to the Codegen Inspector window AND
    optionally saved to *output_file* if the ``--output`` flag is accepted
    by the installed Playwright version.

    Parameters
    ----------
    target_url : str
        URL to open in the recorded browser session.
    output_file : str
        Where to save the auto-generated Python code.
    """
    _log.info("Launching Playwright Codegen -> %s", target_url)
    print("\n" + "=" * 60)
    print("  Playwright Codegen — Facebook Workflow Recorder")
    print("=" * 60)
    print(f"  Target URL  : {target_url}")
    print(f"  Output file : {output_file}")
    print()
    print("  Instructions:")
    print("  1. The browser and Inspector will open.")
    print("  2. Log in to Facebook if required.")
    print("  3. Perform the full scheduling workflow.")
    print("  4. Copy generated selectors into facebook_scheduler.py")
    print("     and background_selector.py.")
    print("  5. Close the browser when done.")
    print("=" * 60 + "\n")

    cmd = [
        sys.executable, "-m", "playwright", "codegen",
        "--target", "python",
        "--output", output_file,
        target_url,
    ]

    try:
        subprocess.run(cmd, check=True)
    except FileNotFoundError:
        _log.error(
            "playwright not found. Install with: pip install playwright "
            "&& playwright install chromium"
        )
        sys.exit(1)
    except subprocess.CalledProcessError as exc:
        _log.error("Codegen exited with code %d", exc.returncode)
    else:
        print(f"\nRecording saved to: {output_file}")
        print("Open it and copy the selectors into:")
        print("  - facebook_scheduler/facebook_scheduler.py  (SELECTORS section)")
        print("  - facebook_scheduler/background_selector.py (_BACKGROUNDS dict)")


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(
        description="Launch Playwright Codegen for Facebook workflow recording."
    )
    ap.add_argument(
        "--url",
        default=_RECORD_TARGET_URL,
        help="Target URL to open in the recorder.",
    )
    ap.add_argument(
        "--output",
        default="recorded_workflow.py",
        help="File to save the generated Playwright code.",
    )
    ns = ap.parse_args()
    launch_codegen(ns.url, ns.output)

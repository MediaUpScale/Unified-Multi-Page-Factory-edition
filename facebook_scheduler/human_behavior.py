# -*- coding: utf-8 -*-
"""
facebook_scheduler/human_behavior.py
======================================
Human-like timing and interaction helpers.

CDP / background-window mode
-----------------------------
When Playwright is attached to an existing browser via CDP (Dolphin{anty}),
calling ``page.mouse.move()`` sends a real ``Input.dispatchMouseEvent`` over
the DevTools protocol which physically moves the OS hardware cursor.  This
makes it impossible to use the PC while the scheduler is running.

This module is intentionally **cursor-free**:

- All clicks go through ``locator.click()`` — Playwright resolves the element
  centre and sends a synthetic click without moving the OS cursor.
- All scrolling goes through ``window.scrollBy()`` JavaScript evaluation —
  no ``page.mouse.wheel()`` calls.
- ``move_to_element()`` and ``jitter_mouse()`` are intentional no-ops (the
  random timing pauses are preserved for anti-bot spacing).
- ``locator.hover()`` is never called.

Result: the Dolphin{anty} browser window can run completely in the background
while you work in other applications.
"""
from __future__ import annotations

import random
import time
from typing import TYPE_CHECKING

from facebook_scheduler import config
from facebook_scheduler.logger import get_logger, save_screenshot

if TYPE_CHECKING:
    from playwright.sync_api import Locator, Page

_log = get_logger(__name__)


class HumanBehavior:
    """
    Wraps Playwright interactions with randomised human-like delays.

    All interactions use Playwright's element-level APIs (locator.click(),
    locator.fill(), page.evaluate()) so the OS hardware cursor is never
    touched and the browser can run in the background.

    Parameters
    ----------
    page : Page
        The active Playwright Page object.
    dry_run : bool
        When True, log actions but do NOT execute them (simulation mode).
    """

    def __init__(self, page: "Page", dry_run: bool = False) -> None:
        self.page    = page
        self.dry_run = dry_run

    # ------------------------------------------------------------------
    # Low-level timing
    # ------------------------------------------------------------------

    def pause(
        self,
        min_s: float = config.CLICK_PAUSE_MIN,
        max_s: float = config.CLICK_PAUSE_MAX,
    ) -> None:
        """Sleep for a random duration between *min_s* and *max_s* seconds."""
        delay = random.uniform(min_s, max_s)
        _log.debug("Human pause: %.2fs", delay)
        time.sleep(delay)

    def short_pause(self) -> None:
        """Quick pause between 0.3–0.9 s."""
        self.pause(0.3, 0.9)

    def micro_pause(self) -> None:
        """Very brief pause 80–250 ms."""
        time.sleep(random.uniform(0.08, 0.25))

    def post_cooldown(self) -> None:
        """
        Long cooldown between scheduling two consecutive posts.
        Uses POST_COOLDOWN_MIN / MAX from config.
        """
        delay = random.uniform(config.POST_COOLDOWN_MIN, config.POST_COOLDOWN_MAX)
        _log.info("Post cooldown: %.1fs before next post...", delay)
        time.sleep(delay)

    # ------------------------------------------------------------------
    # Mouse movement — intentional no-ops (cursor-free mode)
    # ------------------------------------------------------------------

    def move_to_element(self, locator: "Locator") -> None:
        """
        No-op in cursor-free mode.

        Physical mouse movement via ``page.mouse.move()`` would move the OS
        hardware cursor and disrupt foreground applications.  The random
        timing pause is kept so inter-action spacing still looks organic.
        """
        # Timing preserved — cursor deliberately NOT moved
        time.sleep(random.uniform(0.05, 0.15))

    def jitter_mouse(self, count: int = 3) -> None:
        """
        No-op in cursor-free mode.

        Idle micro-movements via ``page.mouse.move()`` are suppressed.
        A small timing pause is kept for pacing.
        """
        time.sleep(random.uniform(0.1, 0.3) * max(1, count))

    # ------------------------------------------------------------------
    # Click — element-level, no hardware cursor movement
    # ------------------------------------------------------------------

    def click(
        self,
        locator: "Locator",
        pause_before: bool = True,
        drift: bool = True,   # accepted but ignored — cursor-free mode
    ) -> None:
        """
        Click *locator* using Playwright's element-level ``locator.click()``.

        Playwright resolves the element's bounding box internally and sends a
        synthetic click via CDP without touching the OS hardware cursor.

        A human-like pause precedes the click.
        """
        if pause_before:
            self.pause()

        _log.debug("Click: %s", locator)

        if self.dry_run:
            _log.info("[DRY-RUN] Would click: %s", locator)
            return

        try:
            locator.wait_for(state="visible", timeout=config.DEFAULT_TIMEOUT_MS)
        except Exception as exc:
            save_screenshot(self.page, "click_wait_failed")
            raise RuntimeError(
                f"Element not visible for click [{locator}]: {exc}"
            ) from exc

        self.short_pause()

        try:
            locator.click()
        except Exception as exc:
            save_screenshot(self.page, "click_failed")
            raise RuntimeError(
                f"Click failed on locator [{locator}]: {exc}"
            ) from exc

    def click_with_hover(self, locator: "Locator") -> None:
        """
        Pause, then click — hover step omitted in cursor-free mode.

        ``locator.hover()`` routes through CDP Input events and moves the
        hardware cursor.  Skipping it keeps the OS cursor untouched while
        still maintaining a natural pre-click timing pause.
        """
        self.pause(0.8, 1.8)
        self.click(locator, pause_before=False)

    # ------------------------------------------------------------------
    # Typing — element-level, no hardware cursor movement
    # ------------------------------------------------------------------

    def type_text(self, locator: "Locator", text: str) -> None:
        """
        Type *text* into *locator* character-by-character using
        ``locator.press_sequentially()``, which dispatches individual key
        events through CDP without moving the OS hardware cursor.

        A random per-keystroke delay (20–70 ms) produces organic typing
        cadence.  A brief pre-type pause (scaled to text length) and a
        post-type review pause are also applied.
        """
        # Pre-type pause: human reads what they're about to write (capped 8 s)
        natural_typing_s = min(len(text) * 0.05, 8.0)
        jitter_s = random.uniform(0.0, natural_typing_s * 0.25)
        pre_pause = max(0.6, natural_typing_s * 0.3 + jitter_s)

        _log.debug(
            "type_text into %s: len=%d, pre_pause=%.1fs",
            locator, len(text), pre_pause,
        )

        if self.dry_run:
            _log.info("[DRY-RUN] Would type %d chars: %r...", len(text), text[:40])
            time.sleep(pre_pause)
            return

        locator.wait_for(state="visible", timeout=config.DEFAULT_TIMEOUT_MS)

        # Brief pause before starting to type (human reads before typing)
        self.pause(0.5, 1.5)

        # Character-by-character typing with random inter-key delay (20–70 ms)
        key_delay_ms = random.randint(20, 70)
        locator.press_sequentially(text, delay=key_delay_ms)

        # Post-type pause: simulate reading/reviewing what was typed
        time.sleep(pre_pause)

    def clear_and_type(self, locator: "Locator", text: str) -> None:
        """Clear existing content, then type *text*."""
        if not self.dry_run:
            locator.wait_for(state="visible", timeout=config.DEFAULT_TIMEOUT_MS)
            locator.fill("")
        self.type_text(locator, text)

    def press_key(self, key: str) -> None:
        """
        Press a keyboard key (e.g. 'Tab', 'Enter').

        Keyboard events do not move the hardware cursor and are safe in
        cursor-free mode.
        """
        self.short_pause()
        _log.debug("Press key: %s", key)
        if not self.dry_run:
            self.page.keyboard.press(key)

    # ------------------------------------------------------------------
    # Scrolling — JavaScript-based, no hardware cursor movement
    # ------------------------------------------------------------------

    def scroll_down(self, amount: "int | None" = None) -> None:
        """
        Scroll the page down via ``window.scrollBy()`` (cursor-free).

        ``page.mouse.wheel()`` is NOT used because in some CDP configurations
        it triggers ``Input.dispatchMouseEvent`` which may move the cursor.
        """
        px = amount or random.randint(config.SCROLL_AMOUNT_MIN, config.SCROLL_AMOUNT_MAX)
        _log.debug("Scroll down: %dpx (JS)", px)
        if not self.dry_run:
            self.page.evaluate(f"window.scrollBy(0, {px})")
        self.short_pause()

    def scroll_up(self, amount: "int | None" = None) -> None:
        """Scroll the page up via ``window.scrollBy()`` (cursor-free)."""
        px = amount or random.randint(config.SCROLL_AMOUNT_MIN, config.SCROLL_AMOUNT_MAX)
        _log.debug("Scroll up: %dpx (JS)", px)
        if not self.dry_run:
            self.page.evaluate(f"window.scrollBy(0, -{px})")
        self.short_pause()

    def idle_scroll(self) -> None:
        """
        Simulate a brief idle scroll and scroll-back before an action.
        Uses JavaScript scroll — hardware cursor not moved.
        """
        self.scroll_down(random.randint(60, 160))
        self.pause(0.4, 1.0)
        self.scroll_up(random.randint(40, 120))
        self.micro_pause()

    # ------------------------------------------------------------------
    # Wait helpers
    # ------------------------------------------------------------------

    def wait_for_locator(
        self,
        locator: "Locator",
        state: str = "visible",
        timeout_ms: "int | None" = None,
        label: str = "",
    ) -> None:
        """
        Wait for *locator* to reach *state*.

        On timeout: saves screenshot, logs error, raises RuntimeError.
        """
        ms = timeout_ms or config.DEFAULT_TIMEOUT_MS
        _log.debug("Waiting for [%s] state=%s", label or str(locator), state)

        if self.dry_run:
            _log.info("[DRY-RUN] Would wait for: %s", label or locator)
            return

        try:
            locator.wait_for(state=state, timeout=ms)
        except Exception as exc:
            save_screenshot(self.page, f"wait_failed_{label or 'element'}")
            raise RuntimeError(
                f"Element not found [{label or locator}] "
                f"(state={state}, timeout={ms}ms): {exc}"
            ) from exc

    def wait_for_url(self, url_pattern: str, timeout_ms: "int | None" = None) -> None:
        """Wait until the page URL matches *url_pattern* (substring match)."""
        ms = timeout_ms or config.DEFAULT_TIMEOUT_MS
        _log.debug("Waiting for URL containing: %s", url_pattern)
        if not self.dry_run:
            self.page.wait_for_url(f"**{url_pattern}**", timeout=ms)

    def wait_for_load(self) -> None:
        """Wait for the page to reach networkidle state."""
        if not self.dry_run:
            self.page.wait_for_load_state("networkidle", timeout=config.LONG_TIMEOUT_MS)

# -*- coding: utf-8 -*-
"""
agents/posting/facebook_scheduler/background_selector.py
==========================================
Persistent background-tile selection for the Facebook post composer.

Design
------
- The user selects a background tile ONCE in training mode (--set-bg).
- The chosen tile index is saved to ``credentials/bg_config.json``.
- All future automated runs load that index and click the tile automatically.
- No hard-coded selectors required — selection is purely index-based
  (nth() child within the background tile container).

Workflow modes
--------------
1. **Training mode** (``select_background(..., training=True)``):
   - Opens the background picker.
   - Prints a prompt asking the user to click the desired tile in the browser.
   - Waits for the user to press ENTER in the terminal.
   - Detects which tile is now marked active/selected in the DOM.
   - Falls back to prompting the user for the 1-based tile number if
     DOM detection fails.
   - Saves ``{"bg_index": N, "bg_selector": "...", "recorded_at": "..."}``
     to ``credentials/bg_config.json``.

2. **Automated mode** (``select_background(...)`` — normal runs):
   - Loads ``bg_config.json``.
   - Opens the background picker.
   - Clicks the tile at the saved index.
   - If no config exists, logs a warning and skips (post uses no background).

Selector stubs
--------------
The picker-toggle button and tile container selectors live in the
``_SEL_*`` constants below. Fill them in from a Playwright Codegen
recording session — only these two constants ever need updating.
"""
from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from agents.posting.facebook_scheduler.logger import get_logger, save_screenshot
from agents.posting.facebook_scheduler import config

if TYPE_CHECKING:
    from playwright.sync_api import Page

_log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Config file path
# ---------------------------------------------------------------------------
_BG_CONFIG_PATH = (
    Path(__file__).resolve().parents[3] / "credentials" / "bg_config.json"
)

# ---------------------------------------------------------------------------
# Selectors — ONLY edit after a new Playwright Codegen recording session.
# ---------------------------------------------------------------------------

# Step A — "Aa" / background-colour icon inside the text editor area.
# Clicking it reveals the horizontal quick-color strip.
_BG_AA_BUTTON_CANDIDATES: list[str] = [
    # Exact aria-label variants seen in Business Suite
    '[aria-label="Background"]',
    '[aria-label="Add background color"]',
    '[aria-label="Add a background color"]',
    '[aria-label="Text background"]',
    # Partial match (handles locale variants)
    '[aria-label*="background" i]:not([aria-label*="Choose" i])',
    # data-testid fallback
    '[data-testid*="background-toggle"]',
    '[data-testid*="background-aa"]',
]

# Step B — 9-dot grid icon at the RIGHT end of the quick-strip.
# Clicking it opens the full "Choose a background" popover.
_BG_GRID_ICON_CANDIDATES: list[str] = [
    # Most specific: exact aria-label on the grid/expand button
    '[aria-label="Choose a background"]',
    'button[aria-label="Choose a background"]',
    'div[role="button"][aria-label="Choose a background"]',
    # Partial matches
    '[aria-label*="Choose a background" i]',
    '[aria-label*="More backgrounds" i]',
    '[aria-label*="more background" i]',
    # data-testid fallback
    '[data-testid*="background-grid"]',
    '[data-testid*="more-backgrounds"]',
    # Last resort: the last visible element that has a background aria-label
    # in the quick-strip (catches the grid icon when aria-label is generic)
    '[aria-label*="background" i]:last-of-type',
]

# Popup container — wait for this BEFORE clicking any tile to confirm
# the full "Choose a background" popover has opened.
_BG_POPUP_CONTAINER_CANDIDATES: list[str] = [
    # Container itself carries the aria-label
    'div[aria-label="Choose a background"]',
    'div[role="dialog"][aria-label*="background" i]',
    # Container detected via text content in child elements
    'div:has(> div > span:has-text("Choose a background"))',
    'div:has(> span:has-text("Choose a background"))',
    # Fallback: presence of "Decorative" section header inside the popup
    'div:has(> div:has-text("Decorative"))',
]

# CSS selector for the container holding ALL background tiles.
# Used when _BG_POPUP_CONTAINER_CANDIDATES succeeds.
_SEL_BG_TILE_CONTAINER: str = "PLACEHOLDER_BG_TILE_CONTAINER"

# CSS selector for the CURRENTLY SELECTED / active tile (training mode).
_SEL_BG_ACTIVE_TILE: str = "PLACEHOLDER_BG_ACTIVE_TILE"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _config_path() -> Path:
    return _BG_CONFIG_PATH


def load_bg_config() -> "dict | None":
    """
    Load saved background config from ``credentials/bg_config.json``.
    Returns None if the file does not exist or is invalid.
    """
    p = _config_path()
    if not p.is_file():
        return None
    try:
        with p.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        _log.debug("Loaded bg_config: %s", data)
        return data
    except (json.JSONDecodeError, OSError) as exc:
        _log.warning("Could not read bg_config.json: %s", exc)
        return None


def save_bg_config(bg_index: int, bg_selector: str = "") -> None:
    """
    Persist the chosen background tile index to ``credentials/bg_config.json``.

    Parameters
    ----------
    bg_index : int
        1-based index of the tile within the tile container.
    bg_selector : str
        Optional raw CSS selector detected from DOM (for debugging).
    """
    p = _config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "bg_index":    bg_index,
        "bg_selector": bg_selector,
        "recorded_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    with p.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    _log.info(
        "Background config saved: tile index %d -> %s",
        bg_index, p,
    )
    print(f"\n[BG] Saved background tile index {bg_index} to {p}\n")


def _is_stub(selector: str) -> bool:
    return "PLACEHOLDER" in selector


# Default tile index used when bg_config.json doesn't exist yet.
# Update this whenever you want a different default background.
DEFAULT_BG_INDEX: int = 14


# ---------------------------------------------------------------------------
# Picker actions
# ---------------------------------------------------------------------------

def _click_btn(page: "Page", candidates: list[str], label: str, timeout: int = 2_500) -> bool:
    """
    Try each CSS selector in *candidates* in order; click the first visible one.
    Returns True if a click was made, False if none matched.
    """
    for selector in candidates:
        if "PLACEHOLDER" in selector:
            continue
        try:
            btn = page.locator(selector).first
            btn.wait_for(state="visible", timeout=timeout)
            btn.click()
            _log.debug("[%s] clicked via: %s", label, selector)
            return True
        except Exception:
            continue
    _log.warning("[%s] no matching button found — skipping.", label)
    return False


def _wait_for_popup(page: "Page", timeout_each: int = 3_000) -> "str | None":
    """
    Wait for the 'Choose a background' popup to appear.
    Returns the winning selector string, or None if none matched.
    """
    for sel in _BG_POPUP_CONTAINER_CANDIDATES:
        try:
            page.locator(sel).first.wait_for(state="attached", timeout=timeout_each)
            _log.debug("Background popup confirmed via: %s", sel)
            return sel
        except Exception:
            continue
    return None


def _open_background_picker(page: "Page", dry_run: bool) -> None:
    """
    Three-step sequence to open the full 'Choose a background' tile grid:

    Step A — Click the **Aa** icon inside the text area to reveal the
             quick-color horizontal strip.
    Step B — Click the **9-dot grid icon** at the right end of the strip
             to open the full 'Choose a background' popover.
    Step C — Wait for the popup container to confirm the modal is ready
             before any tile click is attempted.
    """
    if dry_run:
        _log.info("[DRY-RUN] Would open background picker (Aa → grid icon → popup).")
        return

    # Step A: click the Aa icon to reveal the quick-color strip
    clicked_aa = _click_btn(page, _BG_AA_BUTTON_CANDIDATES, "Aa background toggle")
    if clicked_aa:
        time.sleep(0.5)   # wait for the strip animation
    else:
        _log.warning("Aa button not found — quick strip may already be visible.")

    # Step B: click the 9-dot grid icon to open the full background modal
    clicked_grid = _click_btn(page, _BG_GRID_ICON_CANDIDATES, "background grid icon", timeout=3_000)
    if not clicked_grid:
        _log.warning("Grid icon not found — popup may already be open.")

    time.sleep(0.4)

    # Step C: confirm the popup opened before we try to click any tile
    popup_sel = _wait_for_popup(page)
    if popup_sel:
        _log.info("Background popover open (matched: %s).", popup_sel)
    else:
        _log.warning(
            "Could not confirm background popup opened. "
            "Tile click will proceed with fallback logic."
        )
        time.sleep(0.8)   # extra wait for a slow modal


def _close_background_picker(page: "Page", dry_run: bool) -> None:
    """
    Close the background palette after a tile has been selected.

    Step D:
    1. Re-click the 9-dot grid icon (toggles the popup off).
    2. Fall back to pressing Escape.
    """
    if dry_run:
        _log.info("[DRY-RUN] Would close background picker.")
        return

    # Strategy 1: re-click the grid icon
    if _click_btn(page, _BG_GRID_ICON_CANDIDATES, "background grid icon (close)", timeout=2_000):
        time.sleep(0.4)
        return

    # Strategy 2: Escape
    try:
        page.keyboard.press("Escape")
        time.sleep(0.3)
        _log.debug("Background picker closed via Escape.")
    except Exception as exc:
        _log.debug("Escape failed (%s) — popup may have auto-closed.", exc)


def _click_tile_by_index(page: "Page", index: int, dry_run: bool) -> None:
    """
    Click the background tile at *index* (1-based) inside the open
    'Choose a background' popup.

    Strategy
    --------
    1. Primary: enumerate ALL ``[role="button"]`` elements inside the open
       dialog/popover using ``nth()`` and click the one at the target index.
       This correctly targets interactive tile buttons and bypasses transparent
       overlay divs that share the same parent.
    2. Scoped fallback: search inside each known popup container selector
       for ``[role="button"]`` children.
    3. Full-page fallback via ``_fallback_tile_click()``.

    All clicks use ``force=True`` so CSS-hidden inner elements do not block
    the action.
    """
    _log.info("Clicking background tile index %d...", index)
    if dry_run:
        _log.info("[DRY-RUN] Would click tile index %d.", index)
        return

    nth = index - 1   # Playwright nth() is 0-based

    # --- Primary: dialog-scoped [role="button"] enumeration via nth() ---
    _PRIMARY_TILE_CONTAINERS = [
        'div[role="dialog"] [role="button"]',
        'div[aria-modal="true"] [role="button"]',
        '[aria-label*="Choose" i] [role="button"]',
        '[aria-label*="background" i][role="dialog"] [role="button"]',
        '[aria-label*="background color" i] [role="button"]',
    ]
    for container_sel in _PRIMARY_TILE_CONTAINERS:
        try:
            tiles = page.locator(container_sel)
            count = tiles.count()
            if count == 0:
                continue
            if count > nth:
                tiles.nth(nth).click(force=True, timeout=3_000)
                _log.info(
                    "Clicked tile %d via nth() in '%s' (%d buttons found).",
                    index, container_sel, count,
                )
                return
            # Index out of range — click the last available tile
            tiles.last.click(force=True, timeout=3_000)
            _log.warning(
                "tile_index=%d out of range (%d found) — clicked last tile in '%s'.",
                index, count, container_sel,
            )
            return
        except Exception:
            continue

    # --- Scoped fallback: popup container + legacy patterns ---
    _log.warning("Primary nth() strategy found no dialog buttons. Trying scoped fallback...")
    for popup_sel in _BG_POPUP_CONTAINER_CANDIDATES:
        try:
            popup = page.locator(popup_sel).first
            if not popup.is_visible():
                continue

            tile_patterns = [
                '[role="button"]',
                'div[role="option"]',
                'li[role="option"]',
                'div[tabindex="0"]',
                'li',
                '> div > div',
            ]
            for pattern in tile_patterns:
                tiles = popup.locator(pattern)
                count = tiles.count()
                if count > nth:
                    tiles.nth(nth).click(force=True, timeout=3_000)
                    _log.info(
                        "Clicked tile %d inside popup (%s) via '%s' (%d tiles).",
                        index, popup_sel, pattern, count,
                    )
                    return
        except Exception:
            continue

    # --- Full-page fallback ---
    _log.warning(
        "Scoped strategies exhausted for tile index %d. "
        "Falling back to full-page tile scan.",
        index,
    )
    _fallback_tile_click(page, nth)


def _fallback_tile_click(page: "Page", nth: int) -> None:
    """
    Attempt to click the nth background tile using common DOM patterns
    when no container selector has been recorded.
    """
    generic_patterns = [
        '[aria-label*="background" i] > div',
        '[data-testid*="background"]',
        '[data-testid*="colorpicker"] > div',
        'div[role="radio"][aria-label]',
    ]
    for pattern in generic_patterns:
        try:
            tiles = page.locator(pattern)
            count = tiles.count()
            if count > nth:
                tiles.nth(nth).click(timeout=config.DEFAULT_TIMEOUT_MS)
                _log.info(
                    "Fallback tile click: pattern=%r nth=%d (of %d found)",
                    pattern, nth, count,
                )
                return
        except Exception:
            continue

    raise RuntimeError(
        f"Could not click background tile (index {nth + 1}). "
        "None of the generic tile patterns matched. "
        "Please record _SEL_BG_TILE_CONTAINER with Playwright Codegen "
        "and add it to background_selector.py."
    )


def _detect_active_tile_index(page: "Page") -> "int | None":
    """
    After the user clicks a tile in training mode, detect which tile index
    is now marked active/selected in the DOM.

    Returns the 1-based tile index, or None if detection fails.
    """
    # Strategy 1: check the recorded active-tile selector
    if not _is_stub(_SEL_BG_ACTIVE_TILE):
        try:
            active = page.locator(_SEL_BG_ACTIVE_TILE)
            if active.count() > 0:
                # Determine its position among siblings via JS
                idx = page.evaluate(
                    """
                    (selector) => {
                        const el = document.querySelector(selector);
                        if (!el || !el.parentElement) return -1;
                        return Array.from(el.parentElement.children).indexOf(el) + 1;
                    }
                    """,
                    _SEL_BG_ACTIVE_TILE,
                )
                if idx and idx > 0:
                    return int(idx)
        except Exception as exc:
            _log.debug("Active tile DOM detection failed: %s", exc)

    # Strategy 2: look for aria-checked / aria-selected pattern generically
    generic_active = [
        'div[aria-checked="true"]',
        'div[aria-selected="true"]',
        '[role="radio"][aria-checked="true"]',
    ]
    for pattern in generic_active:
        try:
            el = page.locator(pattern).first
            if el.count() > 0:
                idx = page.evaluate(
                    "(selector) => { const el = document.querySelector(selector); "
                    "return el && el.parentElement "
                    "? Array.from(el.parentElement.children).indexOf(el) + 1 : -1; }",
                    pattern,
                )
                if idx and idx > 0:
                    _log.info("Active tile detected at index %d via pattern %r", idx, pattern)
                    return int(idx)
        except Exception:
            continue

    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def select_background(
    page: "Page",
    dry_run: bool = False,
    training: bool = False,
) -> bool:
    """
    Apply the saved background tile to the currently open post composer.

    In **training mode** (``training=True``):
      1. Opens the background picker.
      2. Prompts the user in the terminal to click the desired tile.
      3. Auto-detects (or asks for) the tile index.
      4. Saves the index to ``credentials/bg_config.json``.

    In **automated mode** (``training=False``, default):
      1. Loads ``credentials/bg_config.json``.
      2. Opens the background picker.
      3. Clicks the tile at the saved index.

    Parameters
    ----------
    page : Page
        Active Playwright page with the composer already open.
    dry_run : bool
        Simulate without clicking.
    training : bool
        Run the interactive selection flow and persist the choice.

    Returns
    -------
    bool — True if a background was applied (or simulated), False if skipped.
    """
    if training:
        return _run_training_mode(page, dry_run)
    else:
        return _run_automated_mode(page, dry_run)


def _run_automated_mode(page: "Page", dry_run: bool) -> bool:
    cfg = load_bg_config()
    if cfg is None:
        _log.info(
            "No bg_config.json found — using default tile index %d. "
            "Run with --set-bg to choose a different tile.",
            DEFAULT_BG_INDEX,
        )
        cfg = {"bg_index": DEFAULT_BG_INDEX}

    bg_index = cfg.get("bg_index")
    if not bg_index or not isinstance(bg_index, int) or bg_index < 1:
        _log.warning("bg_config.json has invalid bg_index=%r — skipping.", bg_index)
        return False

    _log.info("Automated background selection: tile index %d", bg_index)
    try:
        _open_background_picker(page, dry_run)
        time.sleep(0.4)
        _click_tile_by_index(page, bg_index, dry_run)
        time.sleep(0.3)
        _close_background_picker(page, dry_run)
        _log.info("Background tile %d applied.", bg_index)
        return True
    except Exception as exc:
        save_screenshot(page, "bg_auto_error")
        _log.error("Failed to apply background tile %d: %s", bg_index, exc)
        raise RuntimeError(f"Background tile {bg_index} failed: {exc}") from exc


def _run_training_mode(page: "Page", dry_run: bool) -> bool:
    """
    Interactive flow: prompt the user to click a background tile once,
    then save the selection.
    """
    print("\n" + "=" * 60)
    print("  BACKGROUND TRAINING MODE")
    print("=" * 60)
    print("  The post composer should be open and the text has been typed.")
    print()

    # Open the picker so the tiles are visible
    print("  [1/3] Opening the background palette...")
    _open_background_picker(page, dry_run)
    time.sleep(0.8)

    print()
    print("  [2/3] --> Please click your chosen festive background")
    print("          pattern IN THE BROWSER now.")
    print()
    input("          Press ENTER here when you have clicked the tile... ")
    print()

    # Give the UI a moment to register the selection
    time.sleep(0.5)

    # Try to auto-detect which tile is now active
    detected_index: "int | None" = None
    if not dry_run:
        print("  [3/3] Detecting selected tile...")
        detected_index = _detect_active_tile_index(page)

    if detected_index:
        print(f"\n  Auto-detected: tile index {detected_index}")
        confirm = input(f"  Confirm tile index {detected_index}? [Y/n]: ").strip().lower()
        if confirm in ("", "y", "yes"):
            bg_index = detected_index
        else:
            bg_index = _prompt_tile_index()
    else:
        print("  Could not auto-detect the active tile from the DOM.")
        bg_index = _prompt_tile_index()

    if dry_run:
        print(f"\n[DRY-RUN] Would save bg_index={bg_index} — no file written.")
        return True

    save_bg_config(bg_index)
    _close_background_picker(page, dry_run)

    print()
    print(f"  Background tile index {bg_index} saved to credentials/bg_config.json")
    print("  Future runs will apply this tile automatically.")
    print("=" * 60 + "\n")
    return True


def _prompt_tile_index() -> int:
    """Ask the user to enter the tile index manually."""
    while True:
        try:
            raw = input(
                "  Enter the tile number manually "
                "(1 = first tile, 2 = second, etc.): "
            ).strip()
            val = int(raw)
            if val >= 1:
                return val
            print("  Please enter a number >= 1.")
        except ValueError:
            print("  Invalid input — please enter a whole number.")


def list_presets() -> str:
    """Return a short description of the current background config."""
    cfg = load_bg_config()
    if cfg is None:
        return "(no background configured — run with --set-bg to set one)"
    return (
        f"tile index {cfg['bg_index']} "
        f"(recorded {cfg.get('recorded_at', 'unknown')})"
    )

# -*- coding: utf-8 -*-
"""
agents/posting/facebook_scheduler/facebook_scheduler.py
==========================================
Core Playwright automation engine.

Design principles
-----------------
- ALL selectors are defined in the ``# SELECTORS`` section at the top of this
  file.  After a new Playwright Codegen recording session, ONLY edit that
  section — no other code needs to change.
- Actions go through HumanBehavior wrappers (random pauses, natural typing).
- Background selection is fully delegated to background_selector.py.
- On any unexpected state: screenshot saved, error logged, execution halts.

How selectors were recorded
---------------------------
See record_mode.py for instructions on launching Playwright Codegen and
capturing the exact selectors for your Facebook / Business Suite workflow.

CURRENT STATE: Selectors are STUBS captured from standard Facebook Web.
Replace them with values from your own Codegen recording session.
"""
from __future__ import annotations

import os
import random
import re
import time
from datetime import datetime
from typing import Any, TYPE_CHECKING

from agents.posting.facebook_scheduler import config
from agents.posting.facebook_scheduler.background_selector import (
    select_background,
    load_bg_config,
    DEFAULT_BG_INDEX,
)
from agents.posting.facebook_scheduler.human_behavior import HumanBehavior
from agents.posting.facebook_scheduler.logger import get_logger, save_screenshot
from agents.posting.facebook_scheduler.workflow_recorder import WorkflowRecorder, workflow_path as _default_workflow_path

if TYPE_CHECKING:
    from playwright.sync_api import Browser, BrowserContext, Page, Playwright

_log = get_logger(__name__)


# ===========================================================================
# SELECTORS — edit ONLY this section after a new recording session
# ===========================================================================
# All values are Playwright locator descriptors.
# Format: ("method", *args) where method is one of:
#   "role"     -> page.get_by_role(*args)
#   "label"    -> page.get_by_label(*args)
#   "text"     -> page.get_by_text(*args)
#   "locator"  -> page.locator(*args)
#   "placeholder" -> page.get_by_placeholder(*args)

# ---------------------------------------------------------------------------
# Composer trigger — tried in ORDER until one is visible.
# Meta Business Suite (/latest/posts/published_posts) uses "Create post".
# Add locale variants as needed.
# ---------------------------------------------------------------------------
_COMPOSER_TRIGGER_CANDIDATES: list[tuple] = [
    # Meta Business Suite — English
    ("role",    "button", {"name": "Create post"}),
    # Meta Business Suite — Portuguese (Brazil / Portugal)
    ("role",    "button", {"name": "Criar publicação"}),
    # CSS text fallbacks (handles partial or dynamic labels)
    ("locator", 'button:has-text("Create post")'),
    ("locator", 'button:has-text("Criar publicação")'),
    # aria-label variations seen in some Business Suite layouts
    ("locator", '[aria-label="Create post"]'),
    ("locator", '[aria-label="Criar publicação"]'),
    # Generic composer area click target (last resort)
    ("locator", '[data-testid="composer-trigger"]'),
]

# ---------------------------------------------------------------------------
# Composer textarea — tried in ORDER until one is visible.
# Meta Business Suite uses a Lexical/Draft.js contenteditable div.
# ---------------------------------------------------------------------------
_COMPOSER_TEXTAREA_CANDIDATES: list[tuple] = [
    # Lexical / Draft.js rich text editor (most common in Business Suite)
    ("locator", 'div[role="textbox"][contenteditable="true"]'),
    ("locator", 'div[contenteditable="true"]'),
    ("locator", 'div[data-contents="true"]'),
    # Legacy Facebook class-based selector
    ("locator", '.notranslate._5r2x'),
    # aria-label text variants — English
    ("locator", 'div[aria-label*="text" i][contenteditable="true"]'),
    ("locator", 'div[aria-label*="write" i][contenteditable="true"]'),
    # aria-label text variants — Portuguese
    ("locator", 'div[aria-label*="texto" i][contenteditable="true"]'),
    # Business Suite aria-label on the textbox role
    ("locator", 'div[role="textbox"][aria-label*="post" i]'),
    ("locator", 'div[role="textbox"][aria-label*="publicação" i]'),
    # Generic textbox fallback
    ("locator", 'div[role="textbox"]'),
    # Standard Facebook feed (last resort)
    ("role",    "textbox", {"name": "What's on your mind"}),
    ("locator", '[data-testid="status-attachment-mentions-input"]'),
]

# ---------------------------------------------------------------------------
# Background — Aa icon (Step A: reveals the quick colour strip)
# These mirror background_selector._BG_AA_BUTTON_CANDIDATES and are kept
# here so facebook_scheduler.py is self-contained for the direct approach.
# ---------------------------------------------------------------------------
_BG_AA_BUTTON_CANDIDATES: list[tuple] = [
    ("locator", '[aria-label="Background"]'),
    ("locator", '[aria-label="Add background color"]'),
    ("locator", '[aria-label="Add a background color"]'),
    ("locator", '[aria-label="Text background"]'),
    ("locator", '[aria-label*="background" i]:not([aria-label*="Choose" i])'),
    ("locator", '[data-testid*="background-toggle"]'),
    ("locator", '[data-testid*="background-aa"]'),
]

# ---------------------------------------------------------------------------
# Background — 9-dot grid icon (Step B: opens full "Choose a background" modal)
# Ordered: Facebook internal class → aria-label variants → strip last-child.
# ---------------------------------------------------------------------------
_BG_GRID_OPENER_CANDIDATES: list[tuple] = [
    # Facebook internal palette strip table — the grid icon is the last cell
    ("locator", "table._4ukb td:last-child div._2j78"),
    ("locator", "table._4ukb td:last-child"),
    ("locator", 'div[data-pagelet="BusinessComposerSATPTTextEditorContainer"] table div._2j78'),
    # aria-label variants
    ("locator", '[aria-label="Choose a background"]'),
    ("locator", 'button[aria-label="Choose a background"]'),
    ("locator", '[aria-label*="Choose a background" i]'),
    ("locator", '[aria-label*="More backgrounds" i]'),
    ("locator", '[aria-label*="more background" i]'),
    ("locator", '[data-testid*="background-grid"]'),
    ("locator", '[data-testid*="more-backgrounds"]'),
    # Last element in the quick strip as absolute fallback
    ("locator", '[aria-label*="background" i]:last-of-type'),
]

# CSS selector for background tiles inside the open dialog/modal.
# Used with .nth(index) — never with .first alone so we hit the correct tile.
_BG_TILE_IN_DIALOG_SEL: str = (
    "div[role=\"dialog\"] td div, "
    "div[data-visual-completion=\"ignore\"] table td div"
)

# ---------------------------------------------------------------------------
# Single-value selectors (these rarely vary between locales)
# ---------------------------------------------------------------------------

# Step 4 — open the scheduling panel / section
_SCHEDULE_PANEL_CANDIDATES: list[tuple] = [
    # Business Suite primary
    ("role",    "button", {"name": "Schedule post"}),
    ("role",    "button", {"name": "Agendar publicação"}),
    # CSS text fallbacks
    ("locator", 'button:has-text("Schedule post")'),
    ("locator", 'button:has-text("Agendar publicação")'),
    # Calendar / scheduling icon buttons (aria-label)
    ("locator", '[aria-label*="schedule" i]'),
    ("locator", '[aria-label*="agendar" i]'),
    # Dropdown arrow next to the Publish button
    ("locator", 'div[aria-haspopup="listbox"]'),
    ("locator", 'div[aria-haspopup="menu"]'),
]

# Step 5a — "Set date and time" toggle inside the scheduling panel.
# Primary strategy: anchor on the visible label text, then locate the
# input/switch sibling.  Fallback to bare role/aria selectors.
_SCHEDULE_TOGGLE_CANDIDATES: list[tuple] = [
    # Text-anchored: element containing "Set date and time" → child input/switch
    ("locator", '*:has-text("Set date and time") input[type="checkbox"]'),
    ("locator", '*:has-text("Set date and time") div[role="switch"]'),
    ("locator", '*:has-text("Set date and time") span[role="switch"]'),
    ("locator", '*:has-text("Set date and time") label input'),
    # Portuguese locale
    ("locator", '*:has-text("Definir data e hora") input[type="checkbox"]'),
    ("locator", '*:has-text("Definir data e hora") div[role="switch"]'),
    # get_by_label (picks up aria-label="Set date and time" on the input)
    ("label",   "Set date and time"),
    ("label",   "Definir data e hora"),
    # Direct aria-label fallbacks
    ("locator", '[aria-label*="date and time" i]'),
    ("locator", '[aria-label*="data e hora" i]'),
    # Bare role fallbacks (may match other unrelated switches — kept as last resort)
    ("locator", 'div[role="switch"]'),
    ("locator", 'span[role="switch"]'),
    ("locator", 'input[type="checkbox"]'),
]

# Step 5b — date picker input
_SCHEDULE_DATE_CANDIDATES: list[tuple] = [
    # Visible date display button (e.g. "Jul 27, 2026") — click to activate
    ("locator", 'div[role="button"][aria-label*="Date" i]'),
    ("locator", 'div[role="button"][aria-label*="Data" i]'),
    # Input fields (standard HTML inputs)
    ("label",   "Date"),
    ("locator", 'input[aria-label*="Date" i]'),
    ("locator", 'input[aria-label*="Data" i]'),
    ("locator", 'input[placeholder*="MM/DD/YYYY"]'),
    ("locator", 'input[placeholder*="DD/MM/YYYY"]'),
    ("locator", 'input[type="date"]'),
    # Text-containing date display (fallback click-to-open)
    ("locator", 'div:has-text("2026")[role="button"]'),
]

# Step 5b — time picker input
_SCHEDULE_TIME_CANDIDATES: list[tuple] = [
    ("label",   "Time"),
    ("locator", 'input[placeholder*="HH:MM"]'),
    ("locator", 'input[placeholder*=":MM"]'),
    ("locator", 'input[type="time"]'),
    ("locator", 'input[aria-label*="time" i]'),
    ("locator", 'input[aria-label*="hora" i]'),
]

# Step 6 — final "Schedule" confirmation button
_SCHEDULE_CONFIRM_CANDIDATES: list[tuple] = [
    ("role",    "button", {"name": "Schedule"}),
    ("locator", 'button:has-text("Schedule")'),
    ("locator", 'button:has-text("Agendar")'),
    ("locator", '[aria-label="Schedule"]'),
    ("locator", '[aria-label="Agendar"]'),
]

_SEL_SUCCESS_BANNER = ("text", "Your post has been scheduled")
# Alternatives:
#   ("text", "Scheduled")
#   ("role", "alert")

_SEL_CLOSE_DIALOG = ("role", "button", {"name": "Close"})

# ---------------------------------------------------------------------------
# Date / time formats written into the Facebook schedule fields.
# Adjust if your locale / Facebook UI shows different date formats.
# ---------------------------------------------------------------------------
_DATE_FORMAT = "%m/%d/%Y"   # e.g. 07/25/2026
_TIME_FORMAT = "%I:%M %p"   # e.g. 03:00 PM


# ===========================================================================
# Locator builder
# ===========================================================================

def _build_locator(page: "Page", descriptor: tuple):
    """
    Build a Playwright Locator from a descriptor tuple.

    Supported formats
    -----------------
    ("role",        role_name, {**kwargs})
    ("label",       label_text)
    ("text",        text)
    ("placeholder", placeholder_text)
    ("locator",     css_or_xpath)
    """
    method = descriptor[0]
    args   = descriptor[1:]

    if method == "role":
        role = args[0]
        kwargs = args[1] if len(args) > 1 else {}
        return page.get_by_role(role, **kwargs)
    elif method == "label":
        return page.get_by_label(args[0])
    elif method == "text":
        return page.get_by_text(args[0])
    elif method == "placeholder":
        return page.get_by_placeholder(args[0])
    elif method == "locator":
        return page.locator(args[0])
    else:
        raise ValueError(f"Unknown locator method: {method!r}")


# ===========================================================================
# Background tile helpers
# ===========================================================================

# 4-step rotation: festivo → ceu → escuro → darkblue → festivo …
# Mode 3 (darkblue) targets the quick horizontal strip directly;
# modes 0-2 require opening the expanded grid modal.
_BG_CYCLE: tuple[str, ...] = ("festivo", "ceu", "escuro", "darkblue")


def _select_bg_table_cell(
    page: "Page",
    category_name: str = "Decorative",
    row_idx: int = 3,
    col_idx: int = 2,
) -> bool:
    """
    Select a background tile using table row/column indices anchored to a
    named category section header inside the background popover.

    Meta renders the tile grid as ``<table cols="5">`` inside labelled
    sections ("Decorative", "Gradient", "Solid").  Background image URLs
    are dynamic so table row/col addressing is the only reliable strategy.

    Parameters
    ----------
    category_name : str
        Section heading text to anchor on (e.g. ``"Decorative"``).
    row_idx : int
        0-based row index inside that category's ``<table>``.
        Default 3 = 4th row.
    col_idx : int
        0-based column index.  Default 2 = 3rd column.

    Returns
    -------
    bool
        ``True`` if a tile was clicked, ``False`` otherwise.
    """
    # ---- primary: category header → following table → row/col cell ----
    try:
        header = page.locator(f'span:has-text("{category_name}")').first
        if header.is_visible(timeout=3_000):
            bg_table = header.locator(
                "xpath=following-sibling::table | following::table"
            ).first
            target_cell = (
                bg_table.locator("tr").nth(row_idx).locator("td").nth(col_idx)
            )
            if target_cell.is_visible(timeout=2_000):
                target_cell.click(force=True)
                _log.info(
                    "BG tile selected: section='%s' row=%d col=%d.",
                    category_name, row_idx, col_idx,
                )
                page.wait_for_timeout(500)
                page.keyboard.press("Escape")
                page.wait_for_timeout(800)
                return True
    except Exception as exc:
        _log.debug("Category '%s' table-cell strategy failed: %s", category_name, exc)

    # ---- fallback 1: first cell in table._1u4b (legacy Business Suite) ----
    try:
        fb1 = page.locator("table._1u4b tr").first.locator("td").first
        if fb1.is_visible(timeout=2_000):
            fb1.click(force=True)
            _log.info("BG tile selected via fallback: table._1u4b first cell.")
            page.wait_for_timeout(500)
            page.keyboard.press("Escape")
            page.wait_for_timeout(800)
            return True
    except Exception as exc:
        _log.debug("Fallback table._1u4b strategy failed: %s", exc)

    # ---- fallback 2: any <td> inside the dialog ----
    try:
        fb2 = page.locator('div[role="dialog"] td').first
        if fb2.is_visible(timeout=2_000):
            fb2.click(force=True)
            _log.info("BG tile selected via generic dialog td fallback.")
            page.wait_for_timeout(500)
            page.keyboard.press("Escape")
            page.wait_for_timeout(800)
            return True
    except Exception as exc:
        _log.debug("Generic dialog td fallback failed: %s", exc)

    _log.warning(
        "All BG tile selection strategies failed — sending Escape and continuing."
    )
    try:
        page.keyboard.press("Escape")
    except Exception:
        pass
    return False


# ===========================================================================
# Selector helpers
# ===========================================================================

def _try_first_visible(
    page: "Page",
    candidates: list[tuple],
    timeout_each_ms: int = 4_000,
    label: str = "element",
) -> "Any":
    """
    Try each descriptor in *candidates* in order and return the first
    Playwright Locator that becomes visible within *timeout_each_ms*.

    Parameters
    ----------
    page : Page
        Active Playwright page.
    candidates : list[tuple]
        List of locator descriptor tuples (same format as ``_build_locator``).
    timeout_each_ms : int
        Per-candidate visibility timeout in milliseconds.
    label : str
        Human-readable name for error messages.

    Returns
    -------
    Locator — the first visible locator found.

    Raises
    ------
    RuntimeError
        If none of the candidates are visible within their individual
        timeouts.
    """
    errors: list[str] = []
    for descriptor in candidates:
        try:
            loc = _build_locator(page, descriptor)
            # Always narrow to a single element — avoids Playwright strict-mode
            # errors when a selector matches more than one element (e.g. the
            # primary "Create post" button and a secondary dropdown variant).
            loc = loc.first
            loc.wait_for(state="visible", timeout=timeout_each_ms)
            _log.debug("Resolved %s: %s", label, descriptor)
            return loc
        except Exception as exc:
            errors.append(f"{descriptor!r}: {exc}")
            continue

    raise RuntimeError(
        f"Could not find visible '{label}' after trying "
        f"{len(candidates)} candidate(s).\n"
        + "\n".join(f"  {e}" for e in errors)
    )


# ===========================================================================
# Core scheduler class
# ===========================================================================

class FacebookScheduler:
    """
    Playwright-based automation engine that schedules text posts on Facebook.

    Parameters
    ----------
    page : Page
        An active Playwright Page already showing the target Facebook Page /
        Business Suite feed.  Authentication is assumed to be done.
    dry_run : bool
        When True, simulate all actions without touching the browser.
    background_preset : str
        Name of the background preset to apply (see background_selector.py).
        Pass ``""`` or ``None`` to skip background selection.
    """

    def __init__(
        self,
        page: "Page",
        dry_run: bool = False,
        background_preset: str = "",
        training_mode: bool = False,
        workflow_path: "Path | None" = None,
    ) -> None:
        from pathlib import Path as _Path
        self.page           = page
        self.dry_run        = dry_run
        self.background_preset = background_preset
        self.training_mode  = training_mode
        self.workflow_path  = workflow_path or _default_workflow_path()
        self.hb             = HumanBehavior(page, dry_run=dry_run)
        # Background rotation: cycles festive → sky → black → festive ...
        self._bg_cycle_idx: int = 0

    # ------------------------------------------------------------------
    # Low-level step methods (each maps to one recorded Playwright action)
    # ------------------------------------------------------------------

    def _is_composer_already_open(self) -> bool:
        """
        Return True if an active post-composer modal is already visible.

        Used when a previous iteration left the composer open (or a partial
        failure stuck it on screen) so Step 1 can skip the trigger click.
        """
        # Fast structural signals — dialog / pagelet / background controls.
        quick_sels = (
            '[data-pagelet="bizweb_create_post"]',
            'div[aria-label*="Create post" i][role="dialog"]',
            'div[aria-label*="Criar publicação" i][role="dialog"]',
            'div[role="dialog"] div[role="textbox"][contenteditable="true"]',
            'div[role="dialog"] div[contenteditable="true"]',
            'div[role="dialog"] [aria-label*="background" i]',
            'div[role="dialog"] [aria-label="Aa"]',
        )
        for sel in quick_sels:
            try:
                loc = self.page.locator(sel).first
                if loc.is_visible(timeout=400):
                    _log.debug("Composer already-open signal: %s", sel)
                    return True
            except Exception:
                continue

        # Textarea candidates with a short per-candidate timeout.
        try:
            _try_first_visible(
                self.page,
                _COMPOSER_TEXTAREA_CANDIDATES[:5],
                timeout_each_ms=400,
                label="composer already-open textarea",
            )
            return True
        except RuntimeError:
            return False

    def _click_composer_trigger(self, *, label: str = "composer trigger") -> None:
        """Locate the 'Create post' trigger, click it, and confirm textarea."""
        trigger = _try_first_visible(
            self.page,
            _COMPOSER_TRIGGER_CANDIDATES,
            timeout_each_ms=config.COMPOSER_CANDIDATE_TIMEOUT_MS,
            label=label,
        )
        self.hb.click_with_hover(trigger)

        # Confirm the composer opened by waiting for the textarea
        try:
            _try_first_visible(
                self.page,
                _COMPOSER_TEXTAREA_CANDIDATES,
                timeout_each_ms=config.COMPOSER_CANDIDATE_TIMEOUT_MS,
                label="composer textarea (open confirmation)",
            )
        except RuntimeError:
            _log.warning(
                "Composer textarea not immediately visible after clicking trigger. "
                "Continuing — the textarea may appear after a short animation."
            )

    def open_composer(self) -> None:
        """
        Click the 'Create post' button in Meta Business Suite to open the
        post composer.

        Strategy
        --------
        1. Wait for the page DOM to stabilise.
        2. If the composer is already open (stale modal), skip the trigger.
        3. Perform a brief idle scroll (looks like a human scanning the feed).
        4. Try each selector in ``_COMPOSER_TRIGGER_CANDIDATES`` in order,
           using a short per-candidate timeout.  The first visible element
           wins.
        5. Drift mouse to the element → hover → click.
        6. Wait for the textarea to appear (confirming the composer opened).
        7. On total trigger failure: hard-navigate to Business Suite home and
           retry the trigger click once.
        """
        _log.info("Step 1: Opening post composer...")
        if self.dry_run:
            _log.info("[DRY-RUN] Would open composer.")
            return

        # Let the page settle
        try:
            self.page.wait_for_load_state("domcontentloaded", timeout=config.LONG_TIMEOUT_MS)
        except Exception:
            pass   # may already be loaded

        # Stale/stuck composer from a prior iteration — proceed without click.
        if self._is_composer_already_open():
            _log.info("[Step 1] Composer is already open. Proceeding directly...")
            self.hb.pause(0.5, 1.0)
            return

        self.hb.jitter_mouse(count=random.randint(2, 4))
        self.hb.idle_scroll()
        self.hb.pause(0.8, 1.8)

        # Attempt to find/click the "Create post" trigger; recover via home nav.
        try:
            self._click_composer_trigger(label="composer trigger")
        except RuntimeError:
            _log.info(
                "[Step 1] Could not find composer trigger. "
                "Composer may be stuck or broken. Navigating to home..."
            )
            self.page.wait_for_timeout(3000)
            self.page.goto(
                "https://business.facebook.com/latest/",
                wait_until="domcontentloaded",
            )
            self.page.wait_for_timeout(3000)

            # After recovery, composer might already be open — or retry trigger.
            if self._is_composer_already_open():
                _log.info("[Step 1] Composer is already open. Proceeding directly...")
                self.hb.pause(0.5, 1.0)
                return

            self.hb.jitter_mouse(count=random.randint(1, 3))
            self.hb.pause(0.5, 1.2)
            # One retry after home navigation — propagates if it fails again
            self._click_composer_trigger(label="composer trigger (post-home-nav)")

        self.hb.pause(1.0, 2.2)

    def type_post_text(self, text: str) -> None:
        """Focus the composer textarea and type the post text."""
        _log.info("Step 2: Typing post text (%d chars)...", len(text))
        textarea = _try_first_visible(
            self.page,
            _COMPOSER_TEXTAREA_CANDIDATES,
            timeout_each_ms=config.COMPOSER_CANDIDATE_TIMEOUT_MS,
            label="composer textarea",
        )
        self.hb.type_text(textarea, text)

    def apply_background_preset(self) -> None:
        """
        Apply the saved background tile to the open composer.

        In **training mode** (``self.training_mode=True``):
          - Opens the picker and interactively guides the user to click a tile.
          - Saves the tile index to ``credentials/bg_config.json``.
          - Skips on the first post of a training run (the training prompt IS
            the post action — subsequent posts use the saved index).

        In **automated mode** (default):
          - Loads ``credentials/bg_config.json`` and clicks the saved tile.
          - If no config exists, logs a notice and skips (no background).
        """
        _log.info(
            "Step 3: Applying background (training=%s)...", self.training_mode
        )
        try:
            applied = select_background(
                self.page,
                dry_run=self.dry_run,
                training=self.training_mode,
            )
            if applied:
                self.hb.pause(0.5, 1.0)
            else:
                _log.info("No background applied — continuing without one.")
            # After training mode, switch to automated for remaining posts
            if self.training_mode:
                self.training_mode = False
        except RuntimeError as exc:
            _log.warning(
                "Background selection failed (%s). "
                "Continuing without background.", exc
            )
        except NotImplementedError:
            _log.warning(
                "Background picker selectors not yet recorded. "
                "Continuing without background."
            )

    def apply_background_direct(self) -> None:
        """
        Apply a background tile using a strict 4-step rotating cycle.

        Cycle
        -----
        0 — **Festivo**              grid modal → Decorative tr[3] > td[2]
        1 — **Céu**                  grid modal → Decorative tr[1] > td[1]
        2 — **Escuro com Ondas**     grid modal → Decorative tr[2] > td[3]
        3 — **Dark Blue Illustration** direct horizontal strip (no modal)

        Modes 0–2 open the expanded grid modal then click the Decorative table.
        Mode 3 targets the quick strip directly and skips the modal entirely.
        """
        _log.info("Step 3 (direct): Applying background...")
        if self.dry_run:
            _log.info("[DRY-RUN] Would apply background.")
            return

        bg_mode = self._bg_cycle_idx % len(_BG_CYCLE)
        _log.info(
            "BG cycle index %d → mode %d (%s).",
            self._bg_cycle_idx, bg_mode, _BG_CYCLE[bg_mode],
        )
        self._bg_cycle_idx += 1

        # ---- A: Aa icon — reveals the quick colour strip ----
        try:
            aa_btn = _try_first_visible(
                self.page,
                _BG_AA_BUTTON_CANDIDATES,
                timeout_each_ms=3_000,
                label="Aa background icon",
            )
            self.hb.click(aa_btn)
            self.page.wait_for_timeout(500)
            _log.debug("Aa icon clicked; quick strip visible.")
        except RuntimeError:
            _log.warning(
                "Aa icon not found — strip may already be visible. Continuing."
            )

        # ---- Mode 3: Dark Blue — click directly from the horizontal strip ----
        # The quick strip is already open after step A; no modal required.
        if bg_mode == 3:
            _log.info("Mode 3: Targeting Dark Blue Illustration from horizontal bar...")
            try:
                dark_blue = self.page.locator(
                    'a[aria-label*="Dark blue illustration"], '
                    '[aria-label*="Dark blue" i]'
                ).first
                if dark_blue.is_visible(timeout=3_000):
                    dark_blue.click(force=True)
                    _log.info("Dark Blue tile clicked from strip.")
                else:
                    _log.warning(
                        "Dark Blue not visible in strip — falling back to "
                        "table._4ukb td nth(9)."
                    )
                    self.page.locator("table._4ukb td").nth(9).click(force=True)
            except Exception as exc:
                _log.warning("Dark Blue strip selection failed: %s", exc)
                try:
                    self.page.keyboard.press("Escape")
                except Exception:
                    pass
            self.page.wait_for_timeout(500)
            self.hb.pause(0.3, 0.7)
            return   # strip items close the picker automatically — no Escape needed

        # ---- Modes 0–2: open the expanded grid modal ----
        try:
            grid_btn = _try_first_visible(
                self.page,
                _BG_GRID_OPENER_CANDIDATES,
                timeout_each_ms=3_000,
                label="background grid icon",
            )
            try:
                grid_btn.scroll_into_view_if_needed(timeout=2_000)
            except Exception:
                pass
            self.page.wait_for_timeout(300)
            grid_btn.click(force=True)
            self.page.wait_for_timeout(800)   # wait for modal animation
            _log.info("Background grid modal opened.")
        except RuntimeError as exc:
            _log.warning("Grid icon not found (%s). Skipping background.", exc)
            return

        # ---- Modes 0–2: click the correct Decorative table cell ----
        try:
            decorative_hdr = self.page.locator('span:has-text("Decorative")').first
            target_table   = decorative_hdr.locator(
                "xpath=following-sibling::table | following::table"
            ).first

            if bg_mode == 0:
                _log.info("Mode 0: Festivo — tr[3] > td[2]")
                target_table.locator("tr").nth(3).locator("td").nth(2).click(
                    force=True
                )
            elif bg_mode == 1:
                _log.info("Mode 1: Céu — tr[1] > td[1]")
                target_table.locator("tr").nth(1).locator("td").nth(1).click(
                    force=True
                )
            elif bg_mode == 2:
                _log.info("Mode 2: Escuro com Ondas — tr[2] > td[3]")
                # Primary: match the unique background image URL fragment
                target_img = target_table.locator('div[style*="386759842"]').first
                try:
                    if target_img.is_visible(timeout=1_500):
                        target_img.click(force=True)
                    else:
                        raise Exception("image URL not visible")
                except Exception:
                    # Fallback: direct cell index (Row 3, Col 4 — 0-indexed tr[2] td[3])
                    target_table.locator("tr").nth(2).locator("td").nth(3).click(
                        force=True
                    )

            # Dismiss the expanded palette modal
            self.page.wait_for_timeout(400)
            self.page.keyboard.press("Escape")
            self.page.wait_for_timeout(500)
            _log.debug("Grid modal dismissed via Escape.")

        except Exception as exc:
            _log.warning("Grid tile selection failed (mode %d): %s", bg_mode, exc)
            try:
                self.page.keyboard.press("Escape")
            except Exception:
                pass

        self.hb.pause(0.3, 0.7)

    def click_schedule_button(self) -> None:
        """
        Step 4: Open the scheduling panel.

        Tries each candidate in ``_SCHEDULE_PANEL_CANDIDATES`` — the first
        visible one is clicked.  This expands the panel that contains the
        "Set date and time" toggle and the date/time fields.
        """
        _log.info("Step 4: Opening schedule panel...")
        if self.dry_run:
            _log.info("[DRY-RUN] Would click schedule panel button.")
            return
        btn = _try_first_visible(
            self.page,
            _SCHEDULE_PANEL_CANDIDATES,
            timeout_each_ms=config.COMPOSER_CANDIDATE_TIMEOUT_MS,
            label="schedule panel button",
        )
        self.hb.click(btn)
        self.hb.pause(0.8, 1.5)

    def _scroll_composer_panel(self) -> None:
        """
        Scroll the composer dialog panel downward so off-screen elements
        (Schedule section, date/time inputs) become reachable.

        Tries three strategies in order:
        1. scrollTop += 500 on the nearest scrollable dialog container.
        2. scrollTop += 500 on the first scrollable form ancestor.
        3. window.scrollBy(0, 400) as a last resort.
        """
        if self.dry_run:
            return
        scroll_js = """
        (function() {
            var candidates = [
                document.querySelector('div[role="dialog"]'),
                document.querySelector('form'),
                document.querySelector('div[class*="scroll"]'),
                document.querySelector('div[style*="overflow"]'),
            ];
            for (var i = 0; i < candidates.length; i++) {
                var el = candidates[i];
                if (el && el.scrollHeight > el.clientHeight) {
                    el.scrollTop += 500;
                    return 'scrolled:' + (el.tagName + (el.className || '').slice(0, 40));
                }
            }
            window.scrollBy(0, 400);
            return 'fallback:window.scrollBy';
        })()
        """
        try:
            result = self.page.evaluate(scroll_js)
            _log.debug("Composer panel scroll: %s", result)
        except Exception as exc:
            _log.debug("Composer scroll failed (%s) — continuing.", exc)
        time.sleep(0.4)

    def enable_schedule_toggle(self) -> None:
        """
        Step 6 (fallback): Scroll the composer panel down, then ensure the
        English "Set date and time" toggle is switched ON.

        Primary strategy
        ----------------
        Anchor on the visible label text "Set date and time", then locate
        the input/switch widget that sits inside or immediately adjacent to
        that label container.  This is locale-resilient as long as the text
        stays the same and avoids matching unrelated switches elsewhere in the
        DOM.

        Fallback strategy
        -----------------
        Fall through to ``_SCHEDULE_TOGGLE_CANDIDATES`` (role/aria selectors)
        when the text-anchored approach does not find a checkable element.
        """
        _log.info("Step 6: Ensuring 'Set date and time' toggle is ON...")
        if self.dry_run:
            _log.info("[DRY-RUN] Would enable schedule toggle.")
            return

        self._scroll_composer_panel()

        # ---- Primary: strict aria-label + role="switch" on the input ----
        # This is the most precise selector: targets the exact switch element
        # without matching the "date and time" text that appears on other nodes
        # (e.g. the dialog title or date inputs).
        toggle = None
        _STRICT_TOGGLE_SELS = [
            'input[role="switch"][aria-label="Set date and time"]',
            'input[role="switch"][aria-label="Definir data e hora"]',
            'input[role="switch"][aria-label*="date and time" i]',
            'input[role="switch"][aria-label*="data e hora" i]',
            # div/span variants used by some Business Suite builds
            'div[role="switch"][aria-label*="date and time" i]',
            'span[role="switch"][aria-label*="date and time" i]',
        ]
        for sel in _STRICT_TOGGLE_SELS:
            try:
                loc = self.page.locator(sel).first
                loc.wait_for(state="attached", timeout=2_000)
                toggle = loc
                _log.debug("Toggle found via strict selector: %s", sel)
                break
            except Exception:
                continue

        # ---- Text-anchored walk (second tier) ----
        if toggle is None:
            _TEXT_ANCHORS = ["Set date and time", "Definir data e hora"]
            _INNER_SEL = (
                "input[type='checkbox'], div[role='switch'], "
                "span[role='switch'], label input"
            )
            for anchor_text in _TEXT_ANCHORS:
                try:
                    loc = (
                        self.page
                        .locator(f'*:has-text("{anchor_text}")')
                        .locator(_INNER_SEL)
                        .last
                    )
                    loc.wait_for(state="attached", timeout=3_000)
                    toggle = loc
                    _log.debug("Toggle found via text anchor '%s'.", anchor_text)
                    break
                except Exception:
                    continue

        # ---- Fallback: generic candidate list ----
        if toggle is None:
            _log.debug(
                "Precise toggle not found. Trying _SCHEDULE_TOGGLE_CANDIDATES..."
            )
            try:
                toggle = _try_first_visible(
                    self.page,
                    _SCHEDULE_TOGGLE_CANDIDATES,
                    timeout_each_ms=config.COMPOSER_CANDIDATE_TIMEOUT_MS,
                    label="schedule toggle",
                )
            except RuntimeError as exc:
                _log.error(
                    "Schedule toggle not found via any strategy: %s. "
                    "Date/time scheduling will be skipped.",
                    exc,
                )
                return

        # Scroll into view
        try:
            toggle.scroll_into_view_if_needed(timeout=3_000)
            time.sleep(0.3)
        except Exception:
            pass

        # Read current state — prefer aria-checked, fall back to is_checked()
        is_on = False
        try:
            aria = toggle.get_attribute("aria-checked")
            if aria is not None:
                is_on = (aria == "true")
            else:
                is_on = toggle.is_checked()
        except Exception as exc:
            _log.debug("Could not read toggle state (%s) — clicking to be safe.", exc)

        if not is_on:
            _log.info("Toggle is OFF — clicking to enable scheduling.")
            # Prefer clicking via the associated <label> element (avoids
            # strict-mode conflicts on some React builds where the input is
            # pointer-events:none and the label is the real hit-target).
            try:
                toggle_id = toggle.get_attribute("id") or ""
                if toggle_id:
                    label_loc = self.page.locator(f'label[for="{toggle_id}"]')
                    if label_loc.count() > 0:
                        label_loc.first.click(force=True)
                        _log.debug("Toggle clicked via label[for='%s'].", toggle_id)
                    else:
                        toggle.click(force=True)
                else:
                    toggle.click(force=True)
            except Exception as exc:
                _log.warning("Toggle click via label failed (%s); retrying direct.", exc)
                toggle.click(force=True)

            self.page.wait_for_timeout(1_000)   # React needs ~1 s to inject date fields

            # Confirm date/time inputs appeared
            _log.info("Waiting for date/time fields to appear after toggle...")
            try:
                self.page.wait_for_selector(
                    ", ".join([
                        'input[placeholder*="mm/dd/yyyy" i]',
                        'input[placeholder*="dd/mm/yyyy" i]',
                        'input[placeholder*="MM/DD"]',
                        'input[aria-label*="Date" i]',
                        'input[aria-label*="Data" i]',
                        'input[type="date"]',
                    ]),
                    state="attached",
                    timeout=6_000,
                )
                _log.info("Date/time fields detected in DOM.")
            except Exception:
                _log.warning(
                    "Date/time fields not detected within 6 s — "
                    "continuing after fallback delay."
                )
                time.sleep(1.5)

            self.hb.pause(0.4, 0.8)
        else:
            _log.info("Toggle already ON — skipping click.")
            self.hb.pause(0.2, 0.4)

    def fill_schedule_datetime(self, dt: datetime) -> None:
        """
        Step 7: Fill the date and time fields from the Google Sheets queue.

        Date field
        ----------
        Uses explicit ``placeholder`` selectors — cannot collide with the
        "Set date and time" toggle switch.

        Time field
        ----------
        Meta Business Suite renders the time picker as **three separate
        ``spinbutton`` inputs**: hours, minutes, and meridiem (AM/PM).
        Each is targeted by its exact ``aria-label`` attribute and filled
        independently.

        Typing sequence per field
        -------------------------
        click → Ctrl+A → Backspace → type(value, delay=100) → Tab
        """
        date_str = dt.strftime(_DATE_FORMAT)
        _log.info("Step 7: Filling schedule datetime %s", dt.strftime("%Y-%m-%d %H:%M"))

        # ---- Date input ----
        _DATE_INPUT_SEL = ", ".join([
            'input[placeholder*="mm/dd/yyyy" i]',
            'input[placeholder*="dd/mm/yyyy" i]',
            'input[placeholder*="MM/DD/YYYY"]',
            'input[placeholder*="MM/DD"]',
            'input[type="text"][aria-label*="Date" i]',
            'input[type="text"][aria-label*="Data" i]',
            'input[type="date"]',
        ])
        try:
            date_input = self.page.locator(_DATE_INPUT_SEL).first
            date_input.wait_for(state="attached", timeout=config.DEFAULT_TIMEOUT_MS)
            date_input.scroll_into_view_if_needed(timeout=3_000)
            time.sleep(0.2)
            date_input.click()
            self.page.keyboard.press("Control+A")
            self.page.keyboard.press("Backspace")
            date_input.type(date_str, delay=100)
            self.page.keyboard.press("Tab")
            _log.info("Date field filled: %s", date_str)
        except Exception as exc:
            _log.error("Date input fill failed: %s", exc)
            raise

        self.hb.pause(0.3, 0.6)

        # ---- Time — spinbutton inputs ----
        # Meta renders three separate aria-labelled spinbuttons.
        # We parse hour (12-hour, no leading zero), minute, and meridiem.
        hour_12  = dt.strftime("%I").lstrip("0") or "12"  # "3" not "03"; "12" not ""
        minute   = dt.strftime("%M")                       # "05", "30", etc.
        meridiem = dt.strftime("%p")                       # "AM" or "PM"
        _log.info("Time parts: hour=%s min=%s meridiem=%s", hour_12, minute, meridiem)

        _HOURS_SEL = (
            'input[aria-label="hours"], '
            'input[role="spinbutton"][aria-label*="hour" i]'
        )
        _MINUTES_SEL = (
            'input[aria-label="minutes"], '
            'input[role="spinbutton"][aria-label*="minute" i]'
        )
        _MERIDIEM_SEL = (
            'input[aria-label="meridiem"], '
            'input[role="spinbutton"][aria-label*="meridiem" i], '
            'input[aria-label="AM/PM"], '
            'input[role="spinbutton"][aria-label*="AM" i]'
        )

        # hours
        try:
            hours_input = self.page.locator(_HOURS_SEL).first
            hours_input.wait_for(state="visible", timeout=5_000)
            hours_input.scroll_into_view_if_needed(timeout=2_000)
            hours_input.click()
            self.page.keyboard.press("Control+A")
            self.page.keyboard.press("Backspace")
            hours_input.type(hour_12, delay=100)
            self.page.keyboard.press("Tab")
            _log.info("Hours spinbutton filled: %s", hour_12)
        except Exception as exc:
            _log.error("Hours spinbutton fill failed: %s", exc)
            raise

        self.hb.pause(0.2, 0.4)

        # minutes
        try:
            minutes_input = self.page.locator(_MINUTES_SEL).first
            minutes_input.wait_for(state="visible", timeout=3_000)
            minutes_input.click()
            self.page.keyboard.press("Control+A")
            self.page.keyboard.press("Backspace")
            minutes_input.type(minute, delay=100)
            self.page.keyboard.press("Tab")
            _log.info("Minutes spinbutton filled: %s", minute)
        except Exception as exc:
            _log.error("Minutes spinbutton fill failed: %s", exc)
            raise

        self.hb.pause(0.2, 0.4)

        # meridiem (AM / PM) — only click if current value differs
        try:
            meridiem_input = self.page.locator(_MERIDIEM_SEL).first
            meridiem_input.wait_for(state="visible", timeout=3_000)
            current_val = (
                meridiem_input.get_attribute("aria-valuetext")
                or meridiem_input.get_attribute("value")
                or meridiem_input.input_value()
                or ""
            )
            _log.debug("Meridiem current='%s' target='%s'", current_val, meridiem)
            if meridiem.upper() not in current_val.upper():
                meridiem_input.click()
                self.page.keyboard.press("ArrowUp")   # toggles AM ↔ PM
                self.page.wait_for_timeout(300)
                _log.info("Meridiem toggled to %s.", meridiem)
            else:
                _log.info("Meridiem already correct (%s) — no click needed.", meridiem)
        except Exception as exc:
            _log.warning(
                "Meridiem spinbutton not found or not needed (%s) — skipping.", exc
            )

        self.hb.pause(0.4, 0.8)

    def confirm_schedule(self) -> None:
        """
        Step 8: Click the final English "Schedule" confirmation button.

        Waits for the button label to update from "Publish" to "Schedule"
        (which happens after the toggle + datetime are set), then clicks it.
        """
        _log.info("Step 8: Confirming schedule...")
        confirm = _try_first_visible(
            self.page,
            _SCHEDULE_CONFIRM_CANDIDATES,
            timeout_each_ms=config.COMPOSER_CANDIDATE_TIMEOUT_MS,
            label="schedule confirm button",
        )
        self.hb.click(confirm)

    def wait_for_success(self) -> None:
        """
        Deprecated — success banner polling removed in favour of an optimistic
        randomised delay (see ``schedule_post``).  Kept as a no-op stub so that
        any external callers or dry-run tests continue to work.
        """
        _log.debug("wait_for_success: no-op (optimistic delay mode active).")

    def close_composer(self) -> None:
        """Attempt to close the composer dialog."""
        _log.debug("Closing composer dialog...")
        if self.dry_run:
            return
        try:
            close_btn = _build_locator(self.page, _SEL_CLOSE_DIALOG)
            close_btn.click(timeout=config.DEFAULT_TIMEOUT_MS)
        except Exception:
            # Not critical — proceed even if close fails
            _log.debug("Could not click close button (dialog may have auto-closed).")

    # ------------------------------------------------------------------
    # High-level: schedule one post
    # ------------------------------------------------------------------

    def _wait_composer_gone(self, timeout_ms: int = 8_000) -> None:
        """
        Block until the post-creation composer modal is fully detached / hidden.

        Called between iterations to prevent the next ``open_composer()``
        from colliding with a stale React modal (``bizweb_create_post``),
        which causes stale-element exceptions and overlapping-modal errors.
        """
        _COMPOSER_DETACH_SELS = [
            '[data-pagelet="bizweb_create_post"]',
            'div[aria-label*="Create post" i][role="dialog"]',
            'div[role="dialog"]',
        ]
        for sel in _COMPOSER_DETACH_SELS:
            try:
                self.page.wait_for_selector(sel, state="detached", timeout=timeout_ms)
                _log.debug("Composer DOM detach confirmed via: %s", sel)
                return
            except Exception:
                continue
        _log.debug(
            "Composer detach check inconclusive — continuing after %d ms.", timeout_ms
        )

    def _run_recorded_workflow_or_fallback(self) -> None:
        """
        Execute the recorded middle workflow (steps 1–6 of the 8-step
        sequence), or fall back to hard-coded English selectors.

        Recorded steps (credentials/recorded_workflow.json)
        ----------------------------------------------------
        1. Open Backgrounds — Aa / background strip
        2. Select Grid      — full background grid
        3. Choose Background
        4. Close Grid
        5. Scroll Down      — reveal scheduling controls
        6. Open Scheduler   — "Set date and time" toggle

        Steps 7–8 (date from Google Sheets + final Schedule click) are
        handled by the caller after this returns.
        """
        recorder = WorkflowRecorder.load(self.workflow_path)
        if recorder is not None and recorder.action_count > 0:
            _log.info(
                "Replaying recorded workflow (%d action(s)) from %s",
                recorder.action_count,
                self.workflow_path,
            )
            try:
                recorder.replay(self.page, dry_run=self.dry_run)
                return
            except Exception as exc:
                _log.warning(
                    "Recorded workflow replay failed (%s). "
                    "Falling back to hard-coded English selectors.",
                    exc,
                )

        _log.info(
            "No usable recorded workflow at %s — using selector fallback "
            "(English Meta Business Suite UI).",
            self.workflow_path,
        )
        # Fallback mirrors the same 6-step order (English UI).
        self.apply_background_direct()       # 1–4: open bgs → grid → choose → close
        self._scroll_composer_panel()        # 5: scroll to scheduling options
        self.enable_schedule_toggle()        # 6: open scheduler toggle

    def schedule_post(self, text: str, scheduled_dt: "datetime | None") -> None:
        """
        Schedule a single post end-to-end.

        Prerequisites (always automated)
        --------------------------------
        - open_composer()       — English "Create post"
        - type_post_text(text)  — fill Lexical editor

        8-step scheduling sequence
        --------------------------
        1–6. Recorded workflow replay (preferred) or English selector fallback:
             Open Backgrounds → Select Grid → Choose Background → Close Grid
             → Scroll Down → Open Scheduler
        7. fill_schedule_datetime(scheduled_dt) — date/time from Google Sheets
        8. confirm_schedule()                   — click "Schedule"

        Record a workflow once with ``python agents/posting/facebook_scheduler/main.py --record``.
        Meta Business Suite UI language must be English.
        """
        if not text:
            _log.warning("Empty text — skipping post.")
            return

        if scheduled_dt is None:
            _log.warning("No scheduled datetime for post: %r — skipping.", text[:50])
            return

        _log.info(
            "Scheduling post for %s: %r...",
            scheduled_dt.strftime("%Y-%m-%d %H:%M"),
            text[:60],
        )

        try:
            self.open_composer()                          # prerequisite
            self.type_post_text(text)                     # prerequisite
            self._run_recorded_workflow_or_fallback()     # Steps 1–6
            self.fill_schedule_datetime(scheduled_dt)     # Step 7 (sheet_queue date)
            self.confirm_schedule()                       # Step 8

            # Optimistic delay — assume post is submitted; give Meta time to
            # process instead of polling a success banner.
            if not self.dry_run:
                wait_s = round(random.uniform(3.0, 12.0), 2)
                _log.info(
                    "Post submitted. Waiting %.2fs (human delay) before cleanup...",
                    wait_s,
                )
                self.page.wait_for_timeout(int(wait_s * 1_000))

            # Safety modal cleanup — next iteration starts from a clean state.
            if not self.dry_run:
                try:
                    modal = self.page.locator('div[role="dialog"]')
                    if modal.is_visible(timeout=1_500):
                        _log.info("Lingering modal detected — sending Escape.")
                        self.page.keyboard.press("Escape")
                        self.page.wait_for_timeout(1_000)
                except Exception:
                    pass   # modal already gone — nothing to do

            _log.info("Post scheduled successfully.")
        except Exception as exc:
            save_screenshot(self.page, "schedule_post_error")
            raise RuntimeError(
                f"Failed to schedule post [{text[:40]}...]: {exc}"
            ) from exc

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def run(
        self,
        rows: list[dict[str, Any]],
        sheet_queue: "Any | None" = None,
    ) -> dict[str, int]:
        """
        Iterate over *rows* (from SheetQueue.get_pending_rows()) and
        schedule each one.

        Parameters
        ----------
        rows : list[dict]
            Each dict must have: row_index, text, scheduled_dt (datetime|None).
        sheet_queue : SheetQueue | None
            If provided, marks each row DONE/FAILED in the sheet.

        Returns
        -------
        dict with keys: total, scheduled, skipped, failed.
        """
        stats = {"total": len(rows), "scheduled": 0, "skipped": 0, "failed": 0}

        _log.info(
            "Starting scheduler loop | rows=%d dry_run=%s",
            len(rows), self.dry_run,
        )

        for i, row in enumerate(rows, 1):
            row_idx   = row["row_index"]
            text      = row.get("text", "")
            sched_dt  = row.get("scheduled_dt")

            _log.info("--- Row %d / %d (sheet row %d) ---", i, len(rows), row_idx)

            if not text:
                _log.info("Row %d: empty text — skipping.", row_idx)
                stats["skipped"] += 1
                continue

            if sched_dt is None:
                _log.warning(
                    "Row %d: no parsed datetime ('%s') — skipping.",
                    row_idx, row.get("datetime_raw", ""),
                )
                stats["skipped"] += 1
                continue

            try:
                self.schedule_post(text, sched_dt)
                stats["scheduled"] += 1

                if sheet_queue is not None:
                    sheet_queue.mark_done(row_idx)

                # Wait for the composer modal to fully detach from the DOM
                # before starting the next iteration — prevents stale-element
                # crashes caused by overlapping bizweb_create_post modals.
                if i < len(rows):
                    self._wait_composer_gone()

            except Exception as exc:
                _log.error(
                    "Row %d failed: %s", row_idx, exc, exc_info=True
                )
                stats["failed"] += 1

                if sheet_queue is not None:
                    sheet_queue.mark_failed(row_idx, str(exc)[:100])

                # Best-effort cleanup: dismiss any stale modal so the next
                # iteration opens a fresh composer from a clean screen.
                try:
                    self.page.keyboard.press("Escape")
                    self.page.wait_for_timeout(800)
                except Exception:
                    pass

                _log.warning(
                    "Row %d failed — continuing to next post. "
                    "Screenshot saved to %s.",
                    row_idx, config.SCREENSHOTS_DIR,
                )
                # continue to next row (non-blocking loop)

            # Post cooldown between rows
            if i < len(rows):
                self.hb.post_cooldown()

        _log.info(
            "Scheduler finished | scheduled=%d skipped=%d failed=%d total=%d",
            stats["scheduled"], stats["skipped"], stats["failed"], stats["total"],
        )
        return stats


# ===========================================================================
# Browser connection helpers
# ===========================================================================

def _find_dolphin_cdp_port(
    port_range: "tuple[int, int]" = (9222, 9350),
) -> int:
    """
    Scan the local port range used by Dolphin{anty} antidetect profiles and
    return the first port that responds to the Chrome DevTools Protocol.

    Resolution order
    ----------------
    1. ``DOLPHIN_PORT`` environment variable (manual override).
    2. Scan ports *port_range[0]* through *port_range[1]* for a live CDP
       ``/json/version`` endpoint.

    Raises RuntimeError if no port is found.
    """
    import os, socket
    import requests as _req

    # 1. Manual override
    env_port = os.getenv("DOLPHIN_PORT", "").strip()
    if env_port:
        _log.info("Using DOLPHIN_PORT env override: %s", env_port)
        return int(env_port)

    # 2. Dynamic scan
    start, end = port_range
    _log.info("Scanning ports %d-%d for active Dolphin CDP endpoint...", start, end)

    for port in range(start, end + 1):
        try:
            resp = _req.get(
                f"http://127.0.0.1:{port}/json/version",
                timeout=0.25,
            )
            if resp.status_code == 200:
                data = resp.json()
                browser_info = data.get("Browser", "")
                _log.info(
                    "CDP found on port %d | Browser: %s", port, browser_info
                )
                print(f"[CDP Discovery] Active debug port: {port} ({browser_info})")
                return port
        except Exception:
            continue

    raise RuntimeError(
        "Could not find an open CDP port in range "
        f"{start}-{end}. "
        "Make sure the Dolphin{anty} profile is running and remote "
        "debugging is enabled (profile settings -> Additional -> "
        "Debugging port)."
    )


def attach_to_dolphin_profile(
    playwright: "Playwright",
    port: "int | None" = None,
) -> "tuple[BrowserContext, Page]":
    """
    Attach Playwright to the already-running Dolphin{anty} browser profile.

    Rules
    -----
    - Never opens new tabs or navigates.
    - Strictly targets the currently active ``business.facebook.com`` page.
    - Falls back to any open ``facebook.com`` page if Business Suite is not
      found, then to the first available page.

    Parameters
    ----------
    playwright : Playwright
        Active sync_playwright() context.
    port : int | None
        CDP port.  Auto-discovered if None.

    Returns
    -------
    (BrowserContext, Page) — context + the active Meta/Facebook page.
    """
    cdp_port = port or _find_dolphin_cdp_port()
    cdp_url  = f"http://127.0.0.1:{cdp_port}"

    _log.info("Connecting to Dolphin profile via CDP: %s", cdp_url)
    browser: "Browser" = playwright.chromium.connect_over_cdp(cdp_url)
    context: "BrowserContext" = browser.contexts[0]
    pages = context.pages

    if not pages:
        raise RuntimeError(
            "No open pages found in the Dolphin profile. "
            "Ensure the browser profile has at least one open tab."
        )

    # Priority 1: Meta Business Suite
    target = next(
        (p for p in pages if "business.facebook.com" in p.url), None
    )
    if target:
        _log.info("Attached to Meta Business Suite tab: %s", target.url[:80])
        print(f"[CDP] Attached to Meta Business Suite: {target.url[:80]}")
        _ensure_page_active_safe(target)
        return context, target

    # Priority 2: Any facebook.com tab
    target = next(
        (p for p in pages if "facebook.com" in p.url), None
    )
    if target:
        _log.warning(
            "business.facebook.com not found — falling back to: %s", target.url[:80]
        )
        print(f"[CDP] WARNING: Using facebook.com fallback tab: {target.url[:80]}")
        _ensure_page_active_safe(target)
        return context, target

    # Priority 3: First available page (log a warning)
    target = pages[0]
    _log.warning(
        "No facebook.com tab found. Attached to first available page: %s",
        target.url[:80],
    )
    print(
        f"[CDP] WARNING: No Facebook tab found. Using first page: {target.url[:80]}\n"
        "      Navigate to business.facebook.com in the Dolphin profile, then re-run."
    )
    _ensure_page_active_safe(target)
    return context, target


def _ensure_page_active_safe(page: "Page") -> None:
    """
    Soft wake after CDP attach — no OS focus steal.

    Does not call ``bring_to_front`` / ``window.focus``.
    """
    try:
        from agents.posting.facebook_scheduler.media_scheduler_base import ensure_page_active

        ensure_page_active(page)
    except Exception:
        pass
    _ = page


# Keep old name as alias for backward compatibility with main.py
def attach_to_running_browser(playwright: "Playwright") -> "tuple[BrowserContext, Page]":
    """
    Alias for attach_to_dolphin_profile().
    Uses CDP_ENDPOINT from config as the base URL.
    """
    _log.info("Connecting via CDP: %s", config.CDP_ENDPOINT)
    browser: "Browser" = playwright.chromium.connect_over_cdp(config.CDP_ENDPOINT)
    context: "BrowserContext" = browser.contexts[0]
    pages = context.pages

    if not pages:
        raise RuntimeError("No open pages in connected browser.")

    target = (
        next((p for p in pages if "business.facebook.com" in p.url), None)
        or next((p for p in pages if "facebook.com" in p.url), None)
        or pages[0]
    )
    _log.info("Attached to: %s", target.url[:80])
    _ensure_page_active_safe(target)
    return context, target


def launch_fresh_browser(playwright: "Playwright") -> "tuple[BrowserContext, Page]":
    """
    Launch a new browser instance (non-headless).
    Used for record_mode and dry-run testing when no live browser is running.
    """
    _log.info("Launching fresh %s browser...", config.BROWSER_CHANNEL)
    browser = playwright.chromium.launch(
        channel=config.BROWSER_CHANNEL,
        headless=config.HEADLESS,
    )
    context = browser.new_context()
    page    = context.new_page()
    _log.info("Browser launched.")
    return context, page

# -*- coding: utf-8 -*-
"""
facebook_scheduler/workflow_recorder.py
=========================================
Records and replays the manual UI interaction sequence for:
  A. Click Aa icon  → show quick background strip
  B. Click 9-dot grid icon → open full background modal
  C. Click background tile
  D. Click grid icon again → close background modal
  E. Scroll composer sidebar → reveal Schedule section
  F. Click "Set date and time" toggle

Recording works by injecting a lightweight JavaScript event listener into the
live page that captures every click and scroll the user makes, then collects
the results when the user presses ENTER in the terminal.

Each captured action is described by a stable CSS selector (aria-label → 
data-testid → role+nth → tag path) so replays survive minor DOM changes.

Workflow file: ``credentials/recorded_workflow.json``

Replay is the PREFERRED path for steps 3–5a.  Automated selector fallback
remains active when no workflow file exists.
"""
from __future__ import annotations

import json
import random
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.sync_api import Page

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_WORKFLOW_PATH = (
    Path(__file__).resolve().parents[1] / "credentials" / "recorded_workflow.json"
)

# ---------------------------------------------------------------------------
# JavaScript injected into the page during recording
# ---------------------------------------------------------------------------

_CAPTURE_JS = r"""
(function() {
    if (window.__wf_recording) return 'already_running';
    window.__wf_actions = [];

    // IDs that are dynamically generated on every page load and must be ignored.
    function isDynamicId(id) {
        if (!id) return false;
        return /^(js_|u_|:|r[0-9a-f]{4,}|[0-9a-f]{8,})/i.test(id) ||
               id.length > 40;
    }

    // Build a SINGLE stable selector for one element.
    // Returns empty string if no stable selector can be found.
    function stableSelector(el) {
        if (!el || el.nodeType !== 1) return '';

        // Priority 1: aria-label
        var al = el.getAttribute('aria-label');
        if (al) return '[aria-label=' + JSON.stringify(al) + ']';

        // Priority 2: data-testid
        var tid = el.getAttribute('data-testid');
        if (tid) return '[data-testid=' + JSON.stringify(tid) + ']';

        // Priority 3: non-dynamic id
        if (el.id && !isDynamicId(el.id)) return '#' + CSS.escape(el.id);

        // Priority 4: role + tag + nth-of-type within stable parent
        var role = el.getAttribute('role');
        var tag  = el.tagName.toLowerCase();
        var base = role ? tag + '[role="' + role + '"]' : tag;

        var parent = el.parentElement;
        if (!parent || parent === document.body || parent === document.documentElement)
            return base;

        var sameBase = Array.from(parent.children).filter(function(c) {
            return c.tagName === el.tagName &&
                   (!role || c.getAttribute('role') === role);
        });
        if (sameBase.length > 1) {
            base += ':nth-of-type(' + (sameBase.indexOf(el) + 1) + ')';
        }

        var parentSel = stableSelector(parent);
        return parentSel ? parentSel + ' > ' + base : base;
    }

    // Build multiple candidate selectors for an element and its ancestors.
    function candidateSelectors(el) {
        var candidates = [];
        var seen = {};

        function add(s) {
            if (s && !seen[s]) { seen[s] = true; candidates.push(s); }
        }

        // The element itself
        add(stableSelector(el));

        // aria-label partial variants (more resilient to locale changes)
        var al = el.getAttribute('aria-label');
        if (al) {
            add('[aria-label*=' + JSON.stringify(al.split(' ')[0]) + ']');
        }

        // Role-based
        var role = el.getAttribute('role');
        if (role) add('[role="' + role + '"]');

        // Walk up to find the nearest ancestor with an aria-label or testid
        var ancestor = el.parentElement;
        var depth = 0;
        while (ancestor && ancestor !== document.body && depth < 5) {
            var aal = ancestor.getAttribute('aria-label');
            if (aal) { add('[aria-label=' + JSON.stringify(aal) + '] ' + el.tagName.toLowerCase()); break; }
            var atid = ancestor.getAttribute('data-testid');
            if (atid) { add('[data-testid=' + JSON.stringify(atid) + '] ' + el.tagName.toLowerCase()); break; }
            ancestor = ancestor.parentElement;
            depth++;
        }

        // Tag + nth-child as last resort (no dynamic IDs used)
        var tag = el.tagName.toLowerCase();
        var parent = el.parentElement;
        if (parent) {
            var siblings = Array.from(parent.children);
            var idx = siblings.indexOf(el) + 1;
            add(tag + ':nth-child(' + idx + ')');
        }

        return candidates;
    }

    // Tags that are never the real click target — always walk up to the
    // nearest interactive ancestor instead.
    var NON_CLICKABLE_TAGS = {'i': 1, 'img': 1, 'svg': 1, 'path': 1,
                               'use': 1, 'circle': 1, 'rect': 1, 'span': 1};

    function resolveClickTarget(el) {
        if (!NON_CLICKABLE_TAGS[el.tagName.toLowerCase()]) return el;
        var p = el.parentElement;
        var depth = 0;
        while (p && p !== document.body && depth < 6) {
            var pr  = p.getAttribute('role');
            var pt  = p.tagName.toLowerCase();
            var ti  = p.getAttribute('tabindex');
            // Any explicitly interactive element wins
            if (pr === 'button' || pt === 'button' || pt === 'a') return p;
            // A div/li that carries role, tabindex, or an inline handler
            if ((pt === 'div' || pt === 'li') && (pr || ti !== null)) return p;
            // A div with a background-image style is a tile
            if (pt === 'div' && p.style && p.style.backgroundImage) return p;
            p = p.parentElement;
            depth++;
        }
        return el; // give up — keep original
    }

    // Walk up from el to find the nearest dialog/modal ancestor,
    // then return the 0-based index of el among its role=button descendants.
    function tileIndexInModal(el) {
        var ancestor = el.parentElement;
        var d = 0;
        while (ancestor && d < 12) {
            var ar = ancestor.getAttribute('role');
            var am = ancestor.getAttribute('aria-modal');
            if (ar === 'dialog' || ar === 'alertdialog' || am === 'true') {
                var buttons = Array.from(
                    ancestor.querySelectorAll('[role="button"], button')
                );
                var idx = buttons.indexOf(el);
                return idx;   // -1 if not found
            }
            ancestor = ancestor.parentElement;
            d++;
        }
        return -1;
    }

    document.addEventListener('click', function(e) {
        var el = resolveClickTarget(e.target);
        var sels = candidateSelectors(el);
        var tileIdx = tileIndexInModal(el);

        // Bounding-box centre of the RESOLVED element (viewport coordinates).
        // Used as the XY coordinate fallback during replay.
        var rect   = el.getBoundingClientRect();
        var clickX = Math.round(rect.left + rect.width  / 2);
        var clickY = Math.round(rect.top  + rect.height / 2);

        window.__wf_actions.push({
            type:        'click',
            selector:    sels[0] || '',
            selectors:   sels,
            aria_label:  el.getAttribute('aria-label') || '',
            data_testid: el.getAttribute('data-testid') || '',
            role:        el.getAttribute('role') || '',
            tag:         el.tagName.toLowerCase(),
            text:        (el.innerText || el.textContent || '').slice(0, 80).trim(),
            tile_index:  tileIdx,
            click_x:     clickX,
            click_y:     clickY,
            timestamp:   Date.now()
        });
    }, true);

    var _lastScrollTime = 0;
    document.addEventListener('scroll', function(e) {
        var now = Date.now();
        if (now - _lastScrollTime < 600) return;
        _lastScrollTime = now;
        var el = e.target;
        var isDoc = (el === document || el === document.documentElement);
        var scrollY = isDoc ? window.scrollY : el.scrollTop;
        window.__wf_actions.push({
            type:      'scroll',
            selector:  isDoc ? 'window' : (stableSelector(el) || 'window'),
            selectors: [isDoc ? 'window' : (stableSelector(el) || 'window')],
            delta_y:   Math.round(scrollY),
            timestamp: now
        });
    }, true);

    // Keys worth recording — navigation / confirmation / dismissal.
    // Printable characters are NOT captured (they come from TTS text, not the
    // workflow; recording them would produce thousands of noise events).
    // Hover / mousemove events are intentionally absent from this listener.
    var TRACKED_KEYS = {
        'Tab': 1, 'Enter': 1, ' ': 1, 'Escape': 1,
        'ArrowUp': 1, 'ArrowDown': 1, 'ArrowLeft': 1, 'ArrowRight': 1,
        'Backspace': 1, 'Delete': 1, 'Home': 1, 'End': 1,
        'PageUp': 1, 'PageDown': 1
    };

    // Debounce: skip repeat keydown events fired while a key is held.
    var _lastKeyTime = 0;
    var _lastKey     = '';
    document.addEventListener('keydown', function(e) {
        var key = e.key;
        if (!TRACKED_KEYS[key]) return;   // ignore printable / untracked keys
        var now = Date.now();
        // Debounce held keys: same key within 150 ms is likely a repeat
        if (key === _lastKey && (now - _lastKeyTime) < 150) return;
        _lastKey     = key;
        _lastKeyTime = now;

        var focused = document.activeElement;
        var focusSel = focused ? (stableSelector(focused) || focused.tagName.toLowerCase()) : '';
        window.__wf_actions.push({
            type:       'keypress',
            key:        key,
            selector:   focusSel,
            selectors:  focusSel ? [focusSel] : [],
            timestamp:  now
        });
    }, true);

    window.__wf_recording = true;
    return 'started';
})()
"""

_COLLECT_JS = r"""
(function() {
    var actions = (window.__wf_actions || []).slice();
    window.__wf_recording = false;
    window.__wf_actions   = [];
    return actions;
})()
"""


# ---------------------------------------------------------------------------
# WorkflowRecorder
# ---------------------------------------------------------------------------

class WorkflowRecorder:
    """
    Manages recording and replay of the middle interaction steps.

    Usage — recording
    -----------------
    recorder = WorkflowRecorder(page)
    recorder.start()
    input("Perform actions in the browser, then press ENTER...")
    recorder.stop_and_save()

    Usage — replay
    --------------
    recorder = WorkflowRecorder.load()
    recorder.replay(page, dry_run=False)
    """

    def __init__(
        self,
        page: "Page | None" = None,
        workflow_path: Path = _WORKFLOW_PATH,
    ) -> None:
        self.page          = page
        self.workflow_path = workflow_path
        self._raw_actions: list[dict] = []

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Inject the JS event listeners into the live page."""
        if self.page is None:
            raise RuntimeError("WorkflowRecorder.start() requires a live Page.")
        result = self.page.evaluate(_CAPTURE_JS)
        print(f"\n[Recorder] JS listeners injected: {result}")

    def stop_and_save(self, description: str = "") -> list[dict]:
        """
        Collect the captured actions from the page, clean them, and save
        to ``workflow_path``.  Returns the action list.
        """
        if self.page is None:
            raise RuntimeError("WorkflowRecorder.stop_and_save() requires a live Page.")
        raw: list[dict] = self.page.evaluate(_COLLECT_JS)
        cleaned = _clean_actions(raw)
        self._raw_actions = cleaned
        self.save(description=description)
        return cleaned

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self, description: str = "") -> None:
        """Write the current action list to ``workflow_path``."""
        import datetime as _dt
        self.workflow_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "recorded_at": _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "description":  description or "Background + schedule toggle workflow",
            "action_count": len(self._raw_actions),
            "actions":      self._raw_actions,
        }
        with self.workflow_path.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, ensure_ascii=False)
        print(f"\n[Recorder] Saved {len(self._raw_actions)} action(s) to {self.workflow_path}")

    @classmethod
    def load(cls, workflow_path: Path = _WORKFLOW_PATH) -> "WorkflowRecorder | None":
        """
        Load a previously saved workflow.  Returns None if the file does
        not exist or is invalid.
        """
        if not workflow_path.is_file():
            return None
        try:
            data = json.loads(workflow_path.read_text(encoding="utf-8"))
            recorder = cls(workflow_path=workflow_path)
            recorder._raw_actions = data.get("actions", [])
            return recorder
        except (json.JSONDecodeError, OSError):
            return None

    # ------------------------------------------------------------------
    # Replay
    # ------------------------------------------------------------------

    def replay(self, page: "Page", dry_run: bool = False) -> None:
        """
        Replay the recorded action sequence on *page*.

        Special timing rules:
        - After Action 1 (Aa icon): wait 1 s for the quick-strip to render.
        - All other actions: 0.4–1.0 s random human pause.

        Click actions use the ``selectors`` array (multi-candidate), trying
        each in order — most stable (aria-label) first, dynamic IDs never
        appear.  Scroll actions use ``window.scrollBy`` (cursor-free).
        """
        if not self._raw_actions:
            print("[Recorder] No actions to replay.")
            return

        print(f"\n[Recorder] Replaying {len(self._raw_actions)} recorded action(s)...")
        for i, action in enumerate(self._raw_actions, 1):
            label = action.get("description") or f"step {i}"
            atype = action.get("type", "")

            try:
                if atype == "click":
                    _replay_click(page, action, dry_run, label, step_index=i)
                elif atype == "scroll":
                    _replay_scroll(page, action, dry_run, label)
                elif atype == "keypress":
                    _replay_keypress(page, action, dry_run, label)
                else:
                    print(f"  [{i}] Unknown action type '{atype}' — skipping.")
                    continue

                # Per-action timing: give the UI time to react
                if i == 1 and not dry_run:
                    # After Aa icon: wait for the quick-strip to animate in
                    print("  [Replay] Waiting 1 s for quick background strip...")
                    time.sleep(1.0)
                elif i == 2 and not dry_run:
                    # After 9-dot grid icon: wait for tile modal to fully render
                    print("  [Replay] Waiting 0.8 s for background modal to render...")
                    time.sleep(0.8)
                else:
                    time.sleep(random.uniform(0.4, 1.0))

            except Exception as exc:
                # Action 4 = close background modal.  Meta often auto-closes the
                # modal as soon as a tile is selected, so Action 4 is non-fatal.
                if i == 4:
                    print(
                        f"  [Replay] Action 4 (close modal) failed — "
                        f"modal likely auto-closed after tile selection. Continuing. ({exc})"
                    )
                    continue
                raise RuntimeError(
                    f"Replay failed at action {i} ({label}): {exc}"
                ) from exc

        print("[Recorder] Replay complete.\n")

    # ------------------------------------------------------------------
    # Convenience
    # ------------------------------------------------------------------

    @property
    def action_count(self) -> int:
        return len(self._raw_actions)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _clean_actions(raw: list[dict]) -> list[dict]:
    """
    Post-process raw captured actions:

    * Remove duplicate **click** events (same selector within 300 ms).
    * Remove duplicate **keypress** events (same key within 150 ms — held-key
      repeats that slipped past the JS debounce).
    * Hover / mousemove events are never emitted by the JS listener, so no
      filtering is needed for those.
    * Attach a human-readable step description to each action.
    """
    cleaned: list[dict] = []
    prev_click_sel = ""
    prev_click_ts  = 0
    prev_key       = ""
    prev_key_ts    = 0

    step_descriptions = [
        "Aa icon — show quick background strip",
        "9-dot grid icon — open full background modal",
        "Background tile — select background",
        "9-dot grid icon — close background modal",
        "Scroll — reveal Schedule section",
        "Set date and time toggle — enable scheduling",
    ]

    for action in raw:
        atype = action.get("type", "")
        ts    = action.get("timestamp", 0)

        if atype == "click":
            sel = action.get("selector", "")
            if sel == prev_click_sel and (ts - prev_click_ts) < 300:
                continue
            prev_click_sel = sel
            prev_click_ts  = ts

        elif atype == "keypress":
            key = action.get("key", "")
            # Python-side duplicate guard (backup for JS debounce)
            if key == prev_key and (ts - prev_key_ts) < 150:
                continue
            prev_key    = key
            prev_key_ts = ts

        # Assign a human description to the first N *non-keypress* actions
        # so that keyboard events do not consume a named slot.
        idx = sum(1 for a in cleaned if a.get("type") != "keypress")
        if atype == "keypress":
            action["description"] = f"Key: {action.get('key', '?')}"
        elif idx < len(step_descriptions):
            action["description"] = step_descriptions[idx]
        else:
            action["description"] = f"Action {idx + 1}"

        cleaned.append(action)

    return cleaned


def _is_dynamic_selector(sel: str) -> bool:
    """
    Return True if the selector contains a dynamic ID that will break on
    the next page load (e.g. #js_3br, #u_0_1, long hex strings).
    """
    import re
    return bool(re.search(r'#(js_|u_|r[0-9a-f]{4,}|[0-9a-f]{8,})', sel, re.I))


def _js_synthetic_click(page: "Page", x: int, y: int) -> bool:
    """
    Fire a full click event chain (mousedown → mouseup → click) at viewport
    coordinates (x, y) using JavaScript ``dispatchEvent`` inside the browser
    process.

    **Why not ``page.mouse.click``?**
    Playwright's ``page.mouse.click`` sends CDP ``Input.dispatchMouseEvent``
    which moves the *physical* OS cursor — unusable while the user is working
    in another window.  ``dispatchEvent`` is handled entirely inside Chromium's
    JS engine and never touches the OS pointer.

    Returns True if ``document.elementFromPoint`` found an element at the
    given coordinates; False otherwise (coordinates may be off-screen).
    """
    result: bool = page.evaluate(
        """
        ([x, y]) => {
            var el = document.elementFromPoint(x, y);
            if (!el) return false;
            ['mousedown', 'mouseup', 'click'].forEach(function(evtType) {
                el.dispatchEvent(new MouseEvent(evtType, {
                    bubbles:    true,
                    cancelable: true,
                    view:       window,
                    clientX:    x,
                    clientY:    y
                }));
            });
            return true;
        }
        """,
        [x, y],
    )
    return bool(result)


def _replay_click(
    page: "Page",
    action: dict,
    dry_run: bool,
    label: str,
    step_index: int = 0,
) -> None:
    """
    Replay a single click action.

    Execution strategy
    ------------------
    A) **Coordinates present** (``click_x`` / ``click_y`` recorded):
       1. Try every CSS selector with a SHORT 500 ms timeout + ``force=True``
          (skips visibility checks — works on CSS-hidden ``<i>`` / ``<img>``).
       2. If all CSS fail → immediately fire cursor-free JS synthetic click at
          the recorded viewport coordinates.  No waiting through long timeouts.

    B) **No coordinates** (legacy recording or scroll action):
       3. Try every CSS selector with a FULL 3 s timeout + ``force=True``.
       4. Step-specific structural fallbacks (Action 2 strip, Action 3 modal).
       5. JS synthetic click as absolute last resort.

    All ``locator.click()`` calls use ``force=True`` — Playwright will skip
    actionability checks (visibility, stable, enabled) and fire the CDP click
    event directly.  This handles CSS-hidden inner ``<i>`` tags that Meta uses
    as decorative icons inside interactive containers.

    The JS synthetic click dispatches ``mousedown / mouseup / click`` via
    ``dispatchEvent`` inside Chromium's JS engine.  **The OS cursor never
    moves**, so the user can keep working in other windows.
    """
    # ---- Build candidate CSS selectors ----
    candidates: list[str] = []

    if action.get("aria_label"):
        candidates.append(f'[aria-label={json.dumps(action["aria_label"])}]')
        first_word = action["aria_label"].split()[0] if action["aria_label"] else ""
        if first_word and len(first_word) > 2:
            candidates.append(f'[aria-label*={json.dumps(first_word)}]')

    if action.get("data_testid"):
        candidates.append(f'[data-testid={json.dumps(action["data_testid"])}]')

    for sel in action.get("selectors", []):
        if sel and sel != "window" and not _is_dynamic_selector(sel):
            candidates.append(sel)

    for key in ("selector", "full_selector"):
        val = action.get(key, "")
        if val and val != "window" and not _is_dynamic_selector(val):
            candidates.append(val)

    seen: set[str] = set()
    ordered = [s for s in candidates if s and not (s in seen or seen.add(s))]

    # ---- Extract coordinates ----
    _cx = action.get("click_x")
    _cy = action.get("click_y")
    try:
        coord_x: "int | None" = int(_cx) if _cx and int(_cx) > 0 else None
        coord_y: "int | None" = int(_cy) if _cy and int(_cy) > 0 else None
    except (TypeError, ValueError):
        coord_x = coord_y = None
    has_coords = coord_x is not None and coord_y is not None

    # ---- Dry-run ----
    if dry_run:
        target = ordered[0] if ordered else (f"XY({coord_x},{coord_y})" if has_coords else "?")
        print(f"  [DRY-RUN] Would click '{label}': {target}")
        return

    # ======================================================
    # ACTION 3 — DIRECT nth() TILE STRATEGY
    # ======================================================
    # Skip the normal CSS/coordinate cascade entirely for tile clicks.
    # Transparent overlay divs (div:nth-child(1)) cause phantom clicks that
    # register in Playwright but don't actually select the background in Meta.
    # Instead, enumerate ALL [role="button"] elements inside the open dialog
    # and click the one at the recorded tile_index.  No CSS path-guessing.
    if step_index == 3:
        page.wait_for_timeout(500)   # let modal grid finish rendering

        tile_index = action.get("tile_index", -1)  # 0-based position
        _TILE_CONTAINER_PATTERNS = [
            'div[role="dialog"] [role="button"]',
            'div[aria-modal="true"] [role="button"]',
            '[aria-label*="Choose" i] [role="button"]',
            '[aria-label*="background" i][role="dialog"] [role="button"]',
            '[aria-label*="background color" i] [role="button"]',
        ]

        for container_sel in _TILE_CONTAINER_PATTERNS:
            try:
                tiles = page.locator(container_sel)
                count = tiles.count()
                if count == 0:
                    continue

                if tile_index >= 0 and count > tile_index:
                    tiles.nth(tile_index).click(force=True, timeout=2_000)
                    page.wait_for_timeout(1_000)   # confirm selection registers
                    print(
                        f"  [Replay] Tile clicked at index {tile_index} "
                        f"via nth() in '{container_sel}' ({count} buttons found)"
                    )
                    return

                # tile_index out of range — click first available tile
                tiles.first.click(force=True, timeout=2_000)
                page.wait_for_timeout(1_000)
                print(
                    f"  [Replay] Tile (first) clicked via '{container_sel}' "
                    f"(tile_index={tile_index} out of range, {count} found)"
                )
                return
            except Exception:
                continue

        # nth() strategy exhausted — fall through to XY coordinate click
        print(
            f"  [Replay] nth() tile strategy exhausted for '{label}'. "
            "Attempting XY coordinate fallback..."
        )
        if has_coords:
            if _js_synthetic_click(page, coord_x, coord_y):
                page.wait_for_timeout(1_000)
                print(f"  [Replay] JS tile click succeeded at ({coord_x}, {coord_y})")
                return
        raise RuntimeError(
            f"Action 3 tile click failed — could not select background tile "
            f"(tile_index={tile_index}, tried {len(_TILE_CONTAINER_PATTERNS)} container patterns)"
        )

    # ======================================================
    # PATH A — coordinates known: fast 500 ms CSS then JS XY
    # ======================================================
    if has_coords:
        for sel in ordered:
            try:
                page.locator(sel).first.click(timeout=500, force=True)
                print(f"  [Replay] Clicked '{label}' via (fast) {sel}")
                return
            except Exception:
                continue

        # CSS didn't pan out quickly — go straight to JS synthetic click
        print(
            f"  [Replay] Fast CSS failed for '{label}'. "
            f"Firing JS click at ({coord_x}, {coord_y})..."
        )
        if _js_synthetic_click(page, coord_x, coord_y):
            print(f"  [Replay] JS click succeeded for '{label}' at ({coord_x}, {coord_y})")
            return
        print(
            f"  [Replay] WARNING: JS click missed (elementFromPoint returned null). "
            f"Falling through to deep CSS pass..."
        )

    # ======================================================
    # PATH B — no coordinates, or JS XY missed: deep CSS pass
    # ======================================================
    last_exc: "Exception | None" = None
    for sel in ordered:
        try:
            page.locator(sel).first.click(timeout=3_000, force=True)
            print(f"  [Replay] Clicked '{label}' via (deep) {sel}")
            return
        except Exception as exc:
            last_exc = exc
            continue

    # ---- Structural fallback — Action 2: 9-dot grid icon ----
    if step_index == 2:
        print(f"  [Replay] Trying last-child fallback for '{label}'...")
        for pattern in [
            '[aria-label*="background" i] > :last-child',
            '[aria-label*="background" i] > div:last-of-type',
            '[aria-label="Show background options"] > :last-child',
            'ul[aria-label*="background" i] > li:last-child',
            'div[role="textbox"] ~ ul > li:last-child',
            'div[role="textbox"] ~ div > div:last-of-type',
        ]:
            try:
                page.locator(pattern).first.click(timeout=2_000, force=True)
                print(f"  [Replay] Clicked '{label}' via strip fallback: {pattern}")
                return
            except Exception:
                continue

    # ---- Structural fallback — Action 3 (dead path) ----
    # step_index==3 returns early via the direct nth() tile block above.
    # This branch is kept only for recordings pre-dating that strategy.
    if step_index == 3:
        pass  # already handled — will reach the RuntimeError below

    # ---- Absolute last resort: JS XY click (not yet tried in path B) ----
    if has_coords and not (step_index == 3 or step_index == 2):
        # Already tried above in path A; avoid double-logging.
        pass
    elif has_coords:
        print(f"  [Replay] Final JS click attempt at ({coord_x}, {coord_y})...")
        if _js_synthetic_click(page, coord_x, coord_y):
            print(f"  [Replay] JS click succeeded for '{label}' at ({coord_x}, {coord_y})")
            return

    raise RuntimeError(
        f"Could not click '{label}' (step {step_index}). "
        f"Tried {len(ordered)} CSS selector(s) + structural fallbacks + JS XY. "
        f"Last CSS error: {last_exc}"
    )


def _replay_scroll(page: "Page", action: dict, dry_run: bool, label: str) -> None:
    """
    Replay a scroll action using ``window.scrollBy`` (cursor-free).
    """
    delta = action.get("delta_y", 300)
    if dry_run:
        print(f"  [DRY-RUN] Would scroll '{label}': +{delta}px")
        return
    page.evaluate(f"window.scrollBy(0, {delta})")
    print(f"  [Replay] Scrolled '{label}': +{delta}px")
    time.sleep(0.3)


def _replay_keypress(page: "Page", action: dict, dry_run: bool, label: str) -> None:
    """
    Replay a recorded keypress action via ``page.keyboard.press()``.

    ``page.keyboard.press`` fires the full ``keydown → keypress → keyup``
    sequence via CDP ``Input.dispatchKeyEvent`` — no OS cursor movement.

    If a ``selector`` was captured (the element that had focus when the key
    was pressed), the function optionally focuses that element first so the
    keystroke is routed to the correct widget.  Focus is skipped gracefully
    when the element cannot be found (e.g., it was a transient dropdown).

    Hover / mousemove events are never emitted by the recorder, so this
    function will never be called for those event types.
    """
    key = action.get("key", "")
    if not key:
        print(f"  [Replay] Skipping empty keypress action.")
        return

    if dry_run:
        print(f"  [DRY-RUN] Would press key '{key}' ({label})")
        return

    # Optionally focus the element that had keyboard focus during recording.
    # Non-fatal — if focus fails the key still fires on whatever is focused.
    focus_sel = action.get("selector", "")
    if focus_sel and focus_sel != "window":
        try:
            page.locator(focus_sel).first.focus(timeout=1_000)
        except Exception:
            pass   # element gone — proceed without refocusing

    page.keyboard.press(key)
    print(f"  [Replay] Pressed key '{key}' ({label})")
    page.wait_for_timeout(300)   # brief settle after each key event


# ---------------------------------------------------------------------------
# Public helpers used by main.py
# ---------------------------------------------------------------------------

def workflow_path() -> Path:
    """Return the default workflow file path."""
    return _WORKFLOW_PATH


def run_recording_session(
    page: "Page",
    sample_text: str = "Recording session — sample text for background selection.",
    workflow_path: Path = _WORKFLOW_PATH,
) -> int:
    """
    Full recording session:

    1. If a previous recording exists, ask whether to overwrite it.
    2. Print step-by-step instructions.
    3. Inject JS event listeners.
    4. Wait for the user to press ENTER (they perform the actions in the
       browser during this window).
    5. Collect and save the recorded actions.
    6. Print a summary of every captured action (selector + coordinates).

    Returns the number of captured actions (0 if the user aborted).

    Note: The post composer must already be open and the sample text must
    have already been typed before calling this function.
    """
    print()
    print("=" * 65)
    print("  WORKFLOW RECORDING MODE")
    print("=" * 65)
    print()

    # ---- Stale / existing file guard ----
    if workflow_path.is_file():
        try:
            existing_data = json.loads(workflow_path.read_text(encoding="utf-8"))
            recorded_at   = existing_data.get("recorded_at", "unknown date")
            action_count  = existing_data.get("action_count", "?")
            print(f"  An existing recording was found:")
            print(f"    Recorded : {recorded_at}")
            print(f"    Actions  : {action_count}")
            print()
            ans = input("  Overwrite with a fresh recording? [y/N] ").strip().lower()
            if ans != "y":
                print()
                print("  Recording cancelled — keeping existing workflow.")
                print("=" * 65)
                print()
                return int(action_count) if str(action_count).isdigit() else 0
            print()
        except Exception:
            pass  # Corrupted file — proceed with fresh recording
    else:
        print("  No existing recording found — starting fresh.")
        print()

    print("  The bot has opened the composer and typed sample text.")
    print("  Now please manually perform these steps IN THE BROWSER:")
    print()
    print("  Action A  Click the [Aa] icon below the text box")
    print("            (reveals the quick background colour strip)")
    print()
    print("  Action B  Click the [9-dot grid] icon at the right end")
    print("            of the strip (opens the full background modal)")
    print()
    print("  Action C  Click the background tile you want (e.g. tile 14)")
    print()
    print("  Action D  Click the [9-dot grid] icon again to close the modal")
    print()
    print("  Action E  Scroll the left panel downward to reveal 'Schedule'")
    print()
    print("  Action F  Click the [Set date and time] toggle switch")
    print()
    print("  When ALL 6 actions are done, press ENTER here.")
    print()
    print("-" * 65)

    recorder = WorkflowRecorder(page=page, workflow_path=workflow_path)
    recorder.start()

    input("  --> Press ENTER when you have finished all 6 actions... ")

    print()
    print("  Collecting recorded actions...")
    actions = recorder.stop_and_save(
        description="Background selection + schedule toggle (recorded)"
    )

    print()
    print(f"  Captured {len(actions)} action(s):")
    for i, a in enumerate(actions, 1):
        atype = a.get("type", "?")
        desc  = a.get("description", f"step {i}")
        if atype == "keypress":
            key = a.get("key", "?")
            print(f"    {i}. [key   ] {desc}  (key='{key}')")
        else:
            sel   = a.get("selector", "") or a.get("aria_label", "")
            cx    = a.get("click_x", "")
            cy    = a.get("click_y", "")
            coord = f"  XY=({cx},{cy})" if cx and cy else ""
            print(f"    {i}. [{atype:6}] {desc}")
            print(f"            selector : {str(sel)[:60]}{coord}")
    print()
    print(f"  Workflow saved to: {recorder.workflow_path}")
    print("=" * 65)
    print()

    return len(actions)

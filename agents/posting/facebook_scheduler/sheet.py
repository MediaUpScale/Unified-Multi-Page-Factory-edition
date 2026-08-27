# -*- coding: utf-8 -*-
"""
agents/posting/facebook_scheduler/sheet.py
============================
Google Sheets queue reader/writer — connects via gspread + service-account.

Sheet ID  : 1tQfl-FWRk-ND6VZMr-mcts3HvI9dTlv3nf9pJ-qZAKI
Worksheet : Ready_to_post

Column convention
-----------------
  A  Post Text         (required)
  B  Scheduled Time    ISO string "YYYY-MM-DD HH:MM" — filled by this module
  C  Status            "DONE" | "FAILED" | blank (pending)

Logic
-----
- get_pending_quotes() returns rows where A is non-empty AND B is blank.
- update_scheduled_times() writes 3-hour-apart datetimes into empty B cells.
- mark_done() / mark_failed() write back into column C.
"""
from __future__ import annotations

import datetime
import os
from pathlib import Path
from typing import Any

from agents.posting.facebook_scheduler.logger import get_logger

_log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------
_DEFAULT_CREDS = str(
    Path(__file__).resolve().parents[3] / "credentials" / "gsheet_service_account.json"
)

_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

_DEFAULT_SHEET_ID  = "1tQfl-FWRk-ND6VZMr-mcts3HvI9dTlv3nf9pJ-qZAKI"
_DEFAULT_WORKSHEET = "Ready_to_post"


# ---------------------------------------------------------------------------
# SheetQueue
# ---------------------------------------------------------------------------

class SheetQueue:
    """
    Interface to the Momma Circle Google Sheets post queue.

    Parameters
    ----------
    spreadsheet_id : str
        Google Spreadsheet ID (default: Momma Circle queue).
    tab_name : str
        Worksheet tab name (default: "Ready_to_post").
    creds_path : str | None
        Path to the service-account JSON.  Falls back to
        ``GOOGLE_APPLICATION_CREDENTIALS`` env var then to
        ``credentials/gsheet_service_account.json``.
    """

    def __init__(
        self,
        spreadsheet_id: str = _DEFAULT_SHEET_ID,
        tab_name: str = _DEFAULT_WORKSHEET,
        creds_path: "str | None" = None,
    ) -> None:
        self.spreadsheet_id = spreadsheet_id
        self.tab_name       = tab_name
        self._sheet         = None   # lazy-loaded gspread Worksheet
        self._creds_path    = (
            creds_path
            or os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "")
            or _DEFAULT_CREDS
        )

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def from_config(cls) -> "SheetQueue":
        """Build from agents.posting.facebook_scheduler.config values (for main.py)."""
        from agents.posting.facebook_scheduler import config
        sheet_id = config.GSHEET_ID.strip() or _DEFAULT_SHEET_ID
        return cls(
            spreadsheet_id=sheet_id,
            tab_name=config.WORKSHEET_NAME,
        )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _open(self):
        """Lazy-initialise and cache the gspread Worksheet."""
        if self._sheet is not None:
            return self._sheet

        try:
            import gspread
            from google.oauth2.service_account import Credentials
        except ImportError as exc:
            raise ImportError(
                "Missing dependency: pip install gspread google-auth"
            ) from exc

        creds_path = self._creds_path
        if not Path(creds_path).is_file():
            raise FileNotFoundError(
                f"Service-account JSON not found: {creds_path}\n"
                "Download it from Google Cloud Console -> IAM -> Service Accounts -> Keys\n"
                "then set GOOGLE_APPLICATION_CREDENTIALS=/path/to/file.json"
            )

        creds = Credentials.from_service_account_file(creds_path, scopes=_SCOPES)
        client = gspread.authorize(creds)
        ss = client.open_by_key(self.spreadsheet_id)
        self._sheet = ss.worksheet(self.tab_name)
        _log.info(
            "Opened sheet '%s' | tab: '%s'",
            ss.title, self._sheet.title,
        )
        return self._sheet

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_pending_quotes(self) -> list[dict[str, Any]]:
        """
        Return rows where Column A (text) is non-empty AND Column B
        (scheduled time) is blank — these are the next posts to schedule.

        Returns list of dicts: {row, quote, scheduled_time}.
        """
        sheet = self._open()
        rows  = sheet.get_all_values()
        pending: list[dict[str, Any]] = []

        for idx, row in enumerate(rows[1:], start=2):   # skip header (row 1)
            quote          = row[0].strip() if len(row) > 0 else ""
            scheduled_time = row[1].strip() if len(row) > 1 else ""

            if quote and not scheduled_time:
                pending.append({
                    "row":            idx,
                    "quote":          quote,
                    "scheduled_time": "",
                })

        _log.info("Pending (unscheduled) rows: %d", len(pending))
        return pending

    def get_pending_rows(self) -> list[dict[str, Any]]:
        """
        Return rows that are ready to post: Column A non-empty,
        Column B has a datetime, Column C is NOT 'DONE'.

        Each dict: {row_index, text, datetime_raw, scheduled_dt}.
        """
        from datetime import datetime as _dt

        _DT_FORMATS = [
            "%Y-%m-%d %H:%M",
            "%Y-%m-%dT%H:%M",
            "%Y-%m-%d %H:%M:%S",
            "%d/%m/%Y %H:%M",
        ]

        def _parse(s: str):
            for fmt in _DT_FORMATS:
                try:
                    return _dt.strptime(s.strip(), fmt)
                except ValueError:
                    continue
            return None

        sheet = self._open()
        rows  = sheet.get_all_values()
        result: list[dict[str, Any]] = []

        for idx, row in enumerate(rows[1:], start=2):
            while len(row) < 3:
                row.append("")
            text      = row[0].strip()
            sched_raw = row[1].strip()
            status    = row[2].strip().upper()

            if not text:
                continue
            if status == "DONE":
                continue

            result.append({
                "row_index":    idx,
                "text":         text,
                "datetime_raw": sched_raw,
                "scheduled_dt": _parse(sched_raw),
            })

        _log.info("Ready-to-post rows: %d", len(result))
        return result

    def update_scheduled_times(
        self,
        pending_items: list[dict[str, Any]],
        interval_hours: int = 3,
        start_time: "datetime.datetime | None" = None,
    ) -> list[dict[str, Any]]:
        """
        Write a 3-hour-apart schedule into Column B for each item in
        *pending_items* (rows where B was empty).

        Existing values in Column B are NEVER touched — this only fills
        items returned by get_pending_quotes() (which requires B to be blank).

        Returns *pending_items* with 'scheduled_time' filled in.
        """
        sheet = self._open()

        if not start_time:
            start_time = datetime.datetime.now() + datetime.timedelta(minutes=30)

        current = start_time
        updates = []

        for item in pending_items:
            time_str = current.strftime("%Y-%m-%d %H:%M")
            item["scheduled_time"] = time_str
            updates.append({
                "range":  f"B{item['row']}",
                "values": [[time_str]],
            })
            _log.info("Row %d: scheduling at %s", item["row"], time_str)
            current += datetime.timedelta(hours=interval_hours)

        if updates:
            sheet.batch_update(updates)
            _log.info("Wrote %d schedule datetime(s) to sheet.", len(updates))

        return pending_items

    def fill_schedule_datetimes(self, interval_hours: float = 3.0) -> int:
        """
        Convenience wrapper: fetch unscheduled rows, fill B column.
        Returns number of rows updated.
        """
        pending = self.get_pending_quotes()
        if not pending:
            return 0
        self.update_scheduled_times(pending, interval_hours=interval_hours)
        return len(pending)

    def mark_done(self, row_index: int) -> None:
        """Write 'DONE' into Column C of *row_index*."""
        sheet = self._open()
        sheet.update(f"C{row_index}", [["DONE"]])
        _log.info("Row %d marked DONE.", row_index)

    def mark_failed(self, row_index: int, reason: str = "") -> None:
        """Write 'FAILED: <reason>' into Column C of *row_index*."""
        sheet = self._open()
        value = f"FAILED: {reason[:100]}" if reason else "FAILED"
        sheet.update(f"C{row_index}", [[value]])
        _log.warning("Row %d marked FAILED: %s", row_index, reason[:60])

    def mark_pending(self, row_index: int) -> None:
        """Clear Column C of *row_index* (reset for retry)."""
        sheet = self._open()
        sheet.update(f"C{row_index}", [[""]])
        _log.info("Row %d reset to pending.", row_index)

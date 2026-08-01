# -*- coding: utf-8 -*-
"""
avatar_engine/publishers/scheduler_engine.py
=============================================
Google Sheets queue reader + interval scheduler for Facebook publishing.

Architecture
------------
1.  GoogleSheetsQueueClient -- reads/writes the Google Sheet queue.
2.  FacebookScheduler       -- polling loop that pulls pending rows and
                               publishes them via FacebookPagePublisher.

Sheet column convention (1-based, row 1 = header)
--------------------------------------------------
  A  Post Text          (required)
  B  Status             Pending | Published | Failed | Skip
  C  Scheduled Time     ISO datetime string or human label (optional)
  D  Published Post ID  filled on success
  E  Published At       UTC ISO timestamp filled on success
  F  Error              error message on failure (optional)

Environment variables
---------------------
GOOGLE_SHEET_QUEUE_ID          Spreadsheet ID from the Sheet URL
GOOGLE_SERVICE_ACCOUNT_JSON    Path to the service-account credentials JSON
                               (default: credentials/gsheet_service_account.json)
POSTING_INTERVAL_HOURS         Loop interval in hours (default: 3)

Quick start
-----------
    from avatar_engine.publishers.scheduler_engine import FacebookScheduler
    scheduler = FacebookScheduler.from_env("momma_circle")
    scheduler.start(interval_hours=3)

Run once without blocking:
    scheduler.run_once()
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_LOG = logging.getLogger(__name__)

_ENGINE_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_SA_JSON = _ENGINE_ROOT / "credentials" / "gsheet_service_account.json"

# Sheet column indices (0-based for Python list access)
_COL_TEXT       = 0   # A
_COL_STATUS     = 1   # B
_COL_SCHED_TIME = 2   # C
_COL_POST_ID    = 3   # D
_COL_PUB_AT     = 4   # E
_COL_ERROR      = 5   # F

_STATUS_PENDING   = "Pending"
_STATUS_PUBLISHED = "Published"
_STATUS_FAILED    = "Failed"
_STATUS_SKIP      = "Skip"

_HEADER_ROW = 1   # row 1 is the header; data starts at row 2


# ===========================================================================
# Google Sheets queue client
# ===========================================================================

class GoogleSheetsQueueClient:
    """
    Read and update a Google Sheet used as a post queue.

    Authentication
    --------------
    Uses a service-account JSON key file.  Grant the service account
    *Editor* access to the spreadsheet.

    Parameters
    ----------
    sheet_id : str
        The spreadsheet ID from the Google Sheet URL.
    credentials_path : str | Path | None
        Path to the service-account JSON.  Defaults to
        ``credentials/gsheet_service_account.json``.
    worksheet_name : str
        Tab name inside the spreadsheet (default: first sheet).
    """

    def __init__(
        self,
        sheet_id: str,
        credentials_path: str | Path | None = None,
        worksheet_name: str = "",
    ) -> None:
        self.sheet_id = sheet_id
        self.cred_path = Path(credentials_path or _DEFAULT_SA_JSON)
        self.worksheet_name = worksheet_name
        self._ws = None   # lazy-loaded gspread Worksheet

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def from_env(cls, worksheet_name: str = "") -> "GoogleSheetsQueueClient":
        """Build from environment variables."""
        try:
            from dotenv import load_dotenv  # type: ignore[import]
            load_dotenv()
        except ImportError:
            pass

        sheet_id = os.getenv("GOOGLE_SHEET_QUEUE_ID", "").strip()
        if not sheet_id:
            raise EnvironmentError(
                "GOOGLE_SHEET_QUEUE_ID env var is not set. "
                "Add it to .env with the Google Sheet ID."
            )
        sa_json = os.getenv(
            "GOOGLE_SERVICE_ACCOUNT_JSON",
            str(_DEFAULT_SA_JSON),
        ).strip()
        return cls(sheet_id, credentials_path=sa_json, worksheet_name=worksheet_name)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _open_worksheet(self):
        """Open (or reuse) the gspread Worksheet object."""
        if self._ws is not None:
            return self._ws
        try:
            import gspread  # type: ignore[import]
        except ImportError:
            raise ImportError(
                "gspread is required for the Google Sheets queue. "
                "Install it with: pip install gspread"
            )

        if not self.cred_path.is_file():
            raise FileNotFoundError(
                f"Service-account JSON not found: {self.cred_path}\n"
                "Download it from Google Cloud Console -> IAM & Admin -> "
                "Service Accounts -> Keys, then place it at the above path."
            )

        gc = gspread.service_account(filename=str(self.cred_path))
        ss = gc.open_by_key(self.sheet_id)

        if self.worksheet_name:
            self._ws = ss.worksheet(self.worksheet_name)
        else:
            self._ws = ss.get_worksheet(0)   # first tab

        _LOG.info(
            "Opened Google Sheet '%s' | tab: '%s'",
            ss.title,
            self._ws.title,
        )
        return self._ws

    def _ensure_header(self) -> None:
        """Write the header row if the sheet appears empty."""
        ws = self._open_worksheet()
        try:
            first = ws.row_values(1)
        except Exception:
            first = []

        if not first or first[0].strip().lower() not in ("post text", "text", "message"):
            ws.update(
                "A1:F1",
                [["Post Text", "Status", "Scheduled Time",
                  "Published Post ID", "Published At", "Error"]],
            )
            _LOG.info("Header row written to Google Sheet.")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_all_rows(self) -> list[dict[str, Any]]:
        """
        Return all data rows (skipping header) as a list of dicts with keys:
        row_index, text, status, scheduled_time, post_id, published_at, error.
        """
        ws = self._open_worksheet()
        all_values = ws.get_all_values()

        rows: list[dict[str, Any]] = []
        for i, row in enumerate(all_values):
            if i == 0:
                continue   # skip header
            # Pad short rows
            while len(row) < 6:
                row.append("")
            rows.append({
                "row_index":      i + 1,   # 1-based gspread row
                "text":           row[_COL_TEXT].strip(),
                "status":         row[_COL_STATUS].strip(),
                "scheduled_time": row[_COL_SCHED_TIME].strip(),
                "post_id":        row[_COL_POST_ID].strip(),
                "published_at":   row[_COL_PUB_AT].strip(),
                "error":          row[_COL_ERROR].strip(),
            })
        return rows

    def get_next_pending_post(self) -> "dict[str, Any] | None":
        """
        Return the first row where Status == 'Pending' (case-insensitive).
        Returns None if no pending rows exist.
        """
        for row in self.get_all_rows():
            if row["status"].lower() == _STATUS_PENDING.lower() and row["text"]:
                _LOG.info(
                    "Next pending post | row=%d text='%s...'",
                    row["row_index"],
                    row["text"][:60],
                )
                return row
        _LOG.info("No pending posts found in the queue.")
        return None

    def mark_as_published(
        self,
        row_index: int,
        post_id: str,
        published_at: "str | None" = None,
    ) -> None:
        """
        Update row *row_index* to reflect a successful publish.

        Sets Status = 'Published', Post ID, and Published At timestamp.
        """
        ws = self._open_worksheet()
        ts = published_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        ws.update(
            f"B{row_index}:E{row_index}",
            [[_STATUS_PUBLISHED, "", post_id, ts]],
        )
        _LOG.info(
            "Sheet row %d marked Published | post_id=%s ts=%s",
            row_index, post_id, ts,
        )
        print(f"[Queue] Row {row_index} marked as Published (post_id={post_id})")

    def mark_as_failed(self, row_index: int, error_message: str) -> None:
        """
        Update row *row_index* to reflect a publish failure.
        Sets Status = 'Failed' and writes the error in column F.
        """
        ws = self._open_worksheet()
        short_err = str(error_message)[:500]
        ws.update(
            f"B{row_index}:F{row_index}",
            [[_STATUS_FAILED, "", "", "", short_err]],
        )
        _LOG.warning(
            "Sheet row %d marked Failed | error=%s",
            row_index, short_err[:120],
        )
        print(f"[Queue] Row {row_index} marked as Failed: {short_err[:80]}")

    def append_post(
        self,
        text: str,
        status: str = _STATUS_PENDING,
        scheduled_time: str = "",
    ) -> int:
        """
        Append a new post row to the queue.
        Returns the 1-based row index of the new row.
        """
        ws = self._open_worksheet()
        ws.append_row([text, status, scheduled_time, "", "", ""])
        all_vals = ws.get_all_values()
        row_idx = len(all_vals)
        _LOG.info("Appended new post to queue | row=%d", row_idx)
        return row_idx


# ===========================================================================
# Facebook scheduler
# ===========================================================================

class FacebookScheduler:
    """
    Polling scheduler that reads the Google Sheets queue and publishes each
    pending post to Facebook at a configurable interval.

    Parameters
    ----------
    publisher : FacebookPagePublisher
        Authenticated publisher instance.
    sheet_client : GoogleSheetsQueueClient | None
        Queue client.  If None, ``run_once()`` requires an explicit message.
    post_type : str
        "text" (default) | "photo_url" | "video_url" | "video_file"
    """

    def __init__(
        self,
        publisher: Any,
        sheet_client: "GoogleSheetsQueueClient | None" = None,
        post_type: str = "text",
    ) -> None:
        self.publisher    = publisher
        self.sheet_client = sheet_client
        self.post_type    = post_type

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def from_env(
        cls,
        page_name: str = "momma_circle",
        worksheet_name: str = "",
        post_type: str = "text",
    ) -> "FacebookScheduler":
        """
        Build a fully wired scheduler from environment variables.

        Requires:
          - FB_MOMMA_CIRCLE_PAGE_ID / FB_MOMMA_CIRCLE_ACCESS_TOKEN (or equivalent)
          - GOOGLE_SHEET_QUEUE_ID
          - GOOGLE_SERVICE_ACCOUNT_JSON (or default path)
        """
        from avatar_engine.publishers.facebook_publisher import FacebookPagePublisher

        publisher    = FacebookPagePublisher.from_env(page_name)
        sheet_client = GoogleSheetsQueueClient.from_env(worksheet_name)
        return cls(publisher, sheet_client, post_type)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _publish_row(self, row: dict[str, Any]) -> str:
        """Publish one queue row and return the FB post ID."""
        text = row["text"]
        pt   = self.post_type

        if pt == "text":
            return self.publisher.post_text(text)
        elif pt == "photo_url":
            photo_url = row.get("scheduled_time", "")  # reuse spare column if needed
            return self.publisher.post_photo_url(text, photo_url)
        elif pt == "video_url":
            video_url = row.get("scheduled_time", "")
            return self.publisher.post_video_url(text, video_url)
        elif pt == "video_file":
            video_path = row.get("scheduled_time", "")
            return self.publisher.post_video_file(text, video_path)
        else:
            return self.publisher.post_text(text)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run_once(self) -> bool:
        """
        Pick the next pending row, publish it, and update the sheet.

        Returns True if a post was published, False if queue was empty.
        """
        if self.sheet_client is None:
            _LOG.warning("run_once called with no sheet_client — nothing to process.")
            return False

        row = self.sheet_client.get_next_pending_post()
        if row is None:
            print("[Scheduler] No pending posts in queue.")
            return False

        print(
            f"[Scheduler] Publishing row {row['row_index']}: "
            f"{row['text'][:70]}..."
        )
        try:
            post_id = self._publish_row(row)
            self.sheet_client.mark_as_published(row["row_index"], post_id)
            return True
        except Exception as exc:
            _LOG.error(
                "Publish failed for row %d: %s", row["row_index"], exc, exc_info=True
            )
            try:
                self.sheet_client.mark_as_failed(row["row_index"], str(exc))
            except Exception as sheet_exc:
                _LOG.warning("Could not update sheet on failure: %s", sheet_exc)
            return False

    def start(
        self,
        interval_hours: "float | None" = None,
        max_iterations: "int | None" = None,
    ) -> None:
        """
        Start the blocking publish loop.

        Parameters
        ----------
        interval_hours : float | None
            Hours between each queue check.  Falls back to the
            ``POSTING_INTERVAL_HOURS`` env var, then to 3.0 h.
        max_iterations : int | None
            Stop after this many iterations (useful for testing).
            None = run forever.
        """
        try:
            from dotenv import load_dotenv  # type: ignore[import]
            load_dotenv()
        except ImportError:
            pass

        if interval_hours is None:
            interval_hours = float(os.getenv("POSTING_INTERVAL_HOURS", "3"))

        interval_s = interval_hours * 3600
        page_id = getattr(self.publisher, "page_id", "?")

        print(
            f"[Scheduler] Starting for page_id={page_id} | "
            f"interval={interval_hours:.1f}h"
        )
        _LOG.info(
            "FacebookScheduler.start | page_id=%s interval_hours=%.1f",
            page_id, interval_hours,
        )

        iterations = 0
        while True:
            iterations += 1
            print(
                f"\n[Scheduler] Tick #{iterations} | "
                f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
            )
            try:
                self.run_once()
            except Exception as loop_exc:
                _LOG.error("Scheduler loop error: %s", loop_exc, exc_info=True)
                print(f"[Scheduler] Loop error (continuing): {loop_exc}")

            if max_iterations is not None and iterations >= max_iterations:
                print(f"[Scheduler] Reached max_iterations={max_iterations}. Stopping.")
                break

            print(f"[Scheduler] Sleeping {interval_hours:.1f}h until next run...")
            time.sleep(interval_s)

    def publish_text_directly(self, message: str) -> str:
        """
        Bypass the queue and publish *message* directly to Facebook.
        Useful for one-off manual posts or testing.

        Returns the post ID.
        """
        return self.publisher.post_text(message)

# -*- coding: utf-8 -*-
"""
pinterest_engine.publisher
---------------------------
Pinterest API v5 pin creation module.

Reads credentials from .env:
    PINTEREST_ACCESS_TOKEN  -- Bearer token (required)
    PINTEREST_BOARD_ID      -- Target board ID (required)

Endpoint: POST https://api.pinterest.com/v5/pins

Pin payload:
    title       -- from pinterest_title field
    description -- pinterest_caption + 5 hashtags
    link        -- http://blueprint.holisticprotocolslab.com/
    media_source -- image_base64 of the transformed 2:3 pin JPEG

Error handling:
    - 401 Unauthorized: logs error + raises PinterestTokenExpiredError
    - 429 Rate Limited:  backs off with exponential retry (3 attempts)
    - Other HTTP errors: logs and returns None
"""
from __future__ import annotations

import base64
import json
import logging
import time
from pathlib import Path

import requests

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_API_BASE = "https://api.pinterest.com/v5"
_PINS_ENDPOINT = f"{_API_BASE}/pins"
_SALES_URL = "http://blueprint.holisticprotocolslab.com/"
_HASHTAGS = "#NaturalHealth #HolisticProtocol #CellularHealing #NaturalRemedies #HolisticLiving"
_MAX_RETRIES = 3
_BACKOFF_BASE_SEC = 10


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class PinterestTokenExpiredError(Exception):
    """Raised when the Pinterest API returns HTTP 401."""


class PinterestPublishError(Exception):
    """Raised for unrecoverable publish failures."""


# ---------------------------------------------------------------------------
# PinterestPublisher
# ---------------------------------------------------------------------------

class PinterestPublisher:
    """
    Publishes a single pin to Pinterest via the v5 API.

    Parameters
    ----------
    access_token : str, optional
        Pinterest Bearer token. Falls back to PINTEREST_ACCESS_TOKEN env var.
    board_id : str, optional
        Target board ID. Falls back to PINTEREST_BOARD_ID env var.
    """

    def __init__(
        self,
        access_token: str | None = None,
        board_id: str | None = None,
    ) -> None:
        import sys  # noqa: PLC0415
        _root = Path(__file__).resolve().parents[1]
        if str(_root) not in sys.path:
            sys.path.insert(0, str(_root))
        import config as cfg  # noqa: PLC0415
        import os  # noqa: PLC0415

        self.token: str = (
            access_token
            or os.getenv("PINTEREST_ACCESS_TOKEN", "")
        )
        self.board_id: str = (
            board_id
            or os.getenv("PINTEREST_BOARD_ID", "")
        )

        if not self.token:
            raise ValueError(
                "PINTEREST_ACCESS_TOKEN is not set. "
                "Add it to .env or pass access_token= explicitly."
            )
        if not self.board_id:
            raise ValueError(
                "PINTEREST_BOARD_ID is not set. "
                "Add it to .env or pass board_id= explicitly."
            )

    # ------------------------------------------------------------------
    # Public

    def publish(
        self,
        record: dict,
        pin_image_bytes: bytes,
    ) -> dict | None:
        """
        Create a Pinterest pin from a master_inventory entry + pre-rendered pin JPEG.

        Guards:
          - Refuses to post if publication_status.posted_on_pinterest == True
          - Validates caption is free of social CTAs and contains target_url

        Parameters
        ----------
        record : dict
            A master_inventory entry with pinterest_title and pinterest_caption.
        pin_image_bytes : bytes
            The JPEG bytes of the 1000x1500 transformed pin.

        Returns
        -------
        dict | None
            The Pinterest API response dict on success, None on soft failure.

        Raises
        ------
        PinterestTokenExpiredError
            If the API returns HTTP 401.
        """
        # Guard: never double-post
        if record.get("publication_status", {}).get("posted_on_pinterest"):
            log.warning(
                "Skipping already-posted entry: %s (pin_id=%s)",
                record.get("post_id", "?"),
                record.get("publication_status", {}).get("pinterest_pin_id"),
            )
            return None

        # Guard: caption safety
        caption = record.get("pinterest_caption", "")
        from pinterest_engine.inventory import validate_caption_safe  # noqa: PLC0415
        valid, reason = validate_caption_safe(caption)
        if not valid:
            log.warning(
                "Caption safety issue (%s) for %s   auto-fixing.",
                reason, record.get("post_id", "?"),
            )
            from pinterest_engine.inventory import build_caption_regex  # noqa: PLC0415
            caption = build_caption_regex(
                record.get("original_caption", ""), record.get("variant_index", 0)
            )
            record["pinterest_caption"] = caption

        title = (record.get("pinterest_title") or record.get("topic", "Holistic Protocol"))[:100]
        description = self._build_description(record)
        image_b64 = base64.b64encode(pin_image_bytes).decode("ascii")

        payload = {
            "board_id": self.board_id,
            "title": title,
            "description": description,
            "link": _SALES_URL,
            "media_source": {
                "source_type": "image_base64",
                "content_type": "image/jpeg",
                "data": image_b64,
            },
        }

        log.info(
            "Publishing pin: '%s' (board=%s, image=%d bytes)",
            title, self.board_id, len(pin_image_bytes),
        )
        return self._post_with_retry(payload)

    def validate_token(self) -> bool:
        """
        Quick connectivity check using GET /v5/boards (requires boards:read only).
        Avoids /v5/user_account which needs user_accounts:read scope.
        Returns True if the token is valid, False otherwise.
        Does NOT raise PinterestTokenExpiredError.
        """
        url = f"{_API_BASE}/boards"
        try:
            resp = requests.get(url, headers=self._headers(),
                                params={"page_size": 1}, timeout=15)
            if resp.status_code == 200:
                items = resp.json().get("items", [])
                board_name = items[0].get("name", "?") if items else "(no boards)"
                log.info("Pinterest token valid. First board: '%s'", board_name)
                return True
            if resp.status_code == 401:
                log.error("Pinterest token is invalid or expired (HTTP 401).")
                return False
            log.warning(
                "Token validation returned HTTP %d: %s",
                resp.status_code, resp.text[:200],
            )
            return False
        except requests.RequestException as exc:
            log.error("Token validation request failed: %s", exc)
            return False

    # ------------------------------------------------------------------
    # Private

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

    def _build_description(self, record: dict) -> str:
        """Compose Pinterest pin description: caption + hashtags (max 500 chars)."""
        caption = record.get("pinterest_caption") or record.get("humanized_caption", "")

        # Truncate caption to leave room for hashtags
        max_caption = 500 - len(_HASHTAGS) - 2
        if len(caption) > max_caption:
            caption = caption[:max_caption - 3].rsplit(" ", 1)[0] + "..."

        return f"{caption}\n\n{_HASHTAGS}"

    def _post_with_retry(self, payload: dict) -> dict | None:
        """POST payload with exponential back-off on 429, raise on 401."""
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                resp = requests.post(
                    _PINS_ENDPOINT,
                    headers=self._headers(),
                    data=json.dumps(payload),
                    timeout=60,
                )
            except requests.RequestException as exc:
                log.error("Network error on attempt %d/%d: %s", attempt, _MAX_RETRIES, exc)
                if attempt < _MAX_RETRIES:
                    time.sleep(_BACKOFF_BASE_SEC * attempt)
                    continue
                return None

            if resp.status_code == 201:
                data = resp.json()
                log.info("Pin created! ID=%s", data.get("id", "?"))
                return data

            if resp.status_code == 401:
                log.error(
                    "Pinterest 401 Unauthorized. Token expired or invalid. "
                    "Update PINTEREST_ACCESS_TOKEN in .env and restart."
                )
                raise PinterestTokenExpiredError(
                    "Pinterest access token is expired or invalid (HTTP 401). "
                    "Obtain a new token and update PINTEREST_ACCESS_TOKEN in .env."
                )

            if resp.status_code == 429:
                wait = _BACKOFF_BASE_SEC * (2 ** attempt)
                log.warning(
                    "Pinterest rate limit (429). Waiting %ds before retry %d/%d ...",
                    wait, attempt, _MAX_RETRIES,
                )
                time.sleep(wait)
                continue

            log.error(
                "Pinterest API error HTTP %d on attempt %d/%d: %s",
                resp.status_code, attempt, _MAX_RETRIES, resp.text[:500],
            )
            if attempt < _MAX_RETRIES:
                time.sleep(_BACKOFF_BASE_SEC)
                continue
            return None

        return None

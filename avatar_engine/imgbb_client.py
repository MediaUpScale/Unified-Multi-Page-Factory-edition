# -*- coding: utf-8 -*-
from __future__ import annotations

import base64
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

_IMGBB_ENDPOINT = "https://api.imgbb.com/1/upload"

# Upload resilience: 30-second timeout, up to 2 retries (3 total attempts)
# with a short backoff between attempts. The caller (main.py) already wraps
# this call in a try/except and logs-and-continues on total failure.
_IMGBB_MAX_ATTEMPTS: int = 3  # 1 initial attempt + 2 retries
_IMGBB_RETRY_BACKOFF_S: float = 2.0


def upload_image_file_to_imgbb(
    api_key: str,
    image_path: Path,
    *,
    timeout_s: float = 30.0,
) -> str | None:
    """
    ImgBB REST upload (multipart-style form encoded body).

    Retries up to 2 times (3 total attempts, 30 s timeout each) on any
    network-level failure before giving up. Returns the public HTTPS URL
    (`display_url`, else `url`) or ``None`` on failure — never raises, so a
    flaky ImgBB endpoint can never halt the pipeline.
    """
    raw_key = (api_key or "").strip()
    ip = Path(image_path).expanduser().resolve()
    if not raw_key or not ip.is_file():
        return None

    payload = urllib.parse.urlencode(
        {"key": raw_key, "image": base64.b64encode(ip.read_bytes()).decode("ascii")},
    ).encode("utf-8")

    body: str | None = None
    for attempt in range(1, _IMGBB_MAX_ATTEMPTS + 1):
        req = urllib.request.Request(
            _IMGBB_ENDPOINT,
            data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded; charset=utf-8"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                body = resp.read().decode("utf-8", errors="replace")
            break
        except (OSError, urllib.error.HTTPError, urllib.error.URLError) as exc:  # noqa: PERF203
            if attempt < _IMGBB_MAX_ATTEMPTS:
                logger.warning(
                    "ImgBB upload attempt %d/%d failed for %s (%s) — retrying in %.1fs…",
                    attempt, _IMGBB_MAX_ATTEMPTS, ip.name, exc, _IMGBB_RETRY_BACKOFF_S,
                )
                time.sleep(_IMGBB_RETRY_BACKOFF_S)
            else:
                logger.warning(
                    "ImgBB upload failed for %s after %d attempt(s): %s",
                    ip.name, _IMGBB_MAX_ATTEMPTS, exc,
                )
                return None

    if body is None:
        return None

    try:
        decoded = json.loads(body)
    except json.JSONDecodeError:
        logger.warning("ImgBB non-JSON response for %s (first bytes): %s", ip.name, body[:400])
        return None

    if not decoded.get("success"):
        logger.warning(
            "ImgBB reported failure for %s: status=%s err=%s",
            ip.name,
            decoded.get("status"),
            decoded.get("error") or decoded,
        )
        return None

    data = decoded.get("data") or {}
    url = ((data.get("display_url") or data.get("url") or "").strip())
    return url or None

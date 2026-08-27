# -*- coding: utf-8 -*-
"""
Global ImgBB uploader — always stores the full-resolution direct image URL.

ImgBB returns several variants. Official sample (api.imgbb.com):

    url / image.url  → full-size original
    display_url      → same as medium.url (display-optimized / compressed)
    thumb.url        → thumbnail

PostPlanner, Excel, CSVs, and asset_library.json must receive url/image.url,
never thumb or medium.
"""
from __future__ import annotations

import base64
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_IMGBB_ENDPOINT = "https://api.imgbb.com/1/upload"

_IMGBB_MAX_ATTEMPTS: int = 3  # 1 initial attempt + 2 retries
_IMGBB_RETRY_BACKOFF_S: float = 2.0


def _variant_url(block: Any) -> str:
    if isinstance(block, dict):
        return str(block.get("url") or "").strip()
    return ""


def _is_viewer_page(url: str) -> bool:
    """ibb.co viewer pages are not hotlinkable image files."""
    host = url.lower()
    return "ibb.co/" in host and "i.ibb.co/" not in host


def select_full_res_direct_url(data: dict[str, Any] | None) -> tuple[str, str]:
    """
    Pick the full-size direct image URL from an ImgBB ``data`` object.

    Preference: ``image.url`` → ``url`` → ``display_url`` (only when it is
    not the medium/thumb variant). Never returns thumb.url or medium.url
    when a distinct full-size URL exists.

    Returns ``(url, field_name)``; both empty on failure.
    """
    if not isinstance(data, dict):
        return "", ""

    thumb = _variant_url(data.get("thumb"))
    medium = _variant_url(data.get("medium"))
    image_url = _variant_url(data.get("image"))
    top_url = str(data.get("url") or "").strip()
    display = str(data.get("display_url") or "").strip()

    reduced = {u for u in (thumb, medium) if u}

    def _ok(candidate: str, *, allow_if_only: bool) -> bool:
        if not candidate or _is_viewer_page(candidate):
            return False
        if candidate in reduced and not allow_if_only:
            return False
        return True

    for name, raw in (("image.url", image_url), ("url", top_url)):
        if _ok(raw, allow_if_only=False):
            return raw, name

    # Tiny uploads: ImgBB often uses one URL for original + medium. Accept
    # image.url / url even when they equal medium, rather than returning empty.
    for name, raw in (("image.url", image_url), ("url", top_url)):
        if _ok(raw, allow_if_only=True):
            return raw, name

    if display and not _is_viewer_page(display):
        if display not in reduced or not (image_url or top_url):
            logger.warning(
                "ImgBB falling back to display_url (may be medium-size). "
                "image.url=%r url=%r medium=%r",
                image_url, top_url, medium,
            )
            return display, "display_url"
    return "", ""


def preserve_full_quality_still(image_path: Path) -> Path:
    """
    Confirm the still is native pixel size and log it. Never downscales.
    JPEG/PNG bytes are uploaded as saved by FLUX — no second compress pass.
    """
    ip = Path(image_path)
    if not ip.is_file():
        return ip
    try:
        from PIL import Image
    except Exception:
        return ip

    try:
        with Image.open(ip) as img:
            img.load()
            w, h = img.size
            fmt = (img.format or ip.suffix.lstrip(".").upper() or "PNG").upper()
            logger.info(
                "ImgBB local still | %s | %dx%d | format=%s | %d bytes",
                ip.name, w, h, fmt, ip.stat().st_size,
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("ImgBB still inspect skipped for %s (%s)", ip.name, exc)
    return ip


def upload_image_file_to_imgbb(
    api_key: str,
    image_path: Path,
    *,
    timeout_s: float = 30.0,
) -> str | None:
    """
    ImgBB REST upload (form-encoded base64 body).

    Retries up to 2 times (3 total attempts, 30 s timeout each) on any
    network-level failure before giving up. Returns the public HTTPS
    **full-size** direct URL (`image.url` / `url`) or ``None`` on failure —
    never raises, so a flaky ImgBB endpoint can never halt the pipeline.
    """
    raw_key = (api_key or "").strip()
    ip = Path(image_path).expanduser().resolve()
    if not raw_key or not ip.is_file():
        return None

    ip = preserve_full_quality_still(ip)

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
    url, field = select_full_res_direct_url(data if isinstance(data, dict) else {})
    width = str((data or {}).get("width") or "") if isinstance(data, dict) else ""
    height = str((data or {}).get("height") or "") if isinstance(data, dict) else ""
    size = str((data or {}).get("size") or "") if isinstance(data, dict) else ""
    if url:
        logger.info(
            "ImgBB full-res | field=%s | claimed=%sx%s | %s bytes | %s",
            field, width or "?", height or "?", size or "?", url,
        )
        print(
            f"[ImgBB] Full-res URL ({field}) {width}x{height} → {url}",
            flush=True,
        )
    return url or None

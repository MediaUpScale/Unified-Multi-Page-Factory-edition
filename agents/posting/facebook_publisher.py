# -*- coding: utf-8 -*-
"""
agents.media/publishers/facebook_publisher.py
================================================
Facebook Graph API publisher — pure HTTP, no heavy SDK.

Supports text posts, photo posts (URL or local file), and video posts for a
single Page.  Credentials are read from environment variables so no secret
is hard-coded here.

Environment variables
---------------------
FB_MOMMA_CIRCLE_PAGE_ID        Facebook Page numeric ID
FB_MOMMA_CIRCLE_ACCESS_TOKEN   Long-lived Page Access Token
FB_GRAPH_API_VERSION           API version string (default: v25.0)

Usage
-----
    from agents.posting.facebook_publisher import FacebookPagePublisher

    pub = FacebookPagePublisher.from_env("momma_circle")
    post_id = pub.post_text("Hello from Momma Circle!")
    print("Post ID:", post_id)
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import requests

_LOG = logging.getLogger(__name__)

_GRAPH_BASE = "https://graph.facebook.com"

# ---------------------------------------------------------------------------
# Per-page credential registry
# Pages added here can be loaded via FacebookPagePublisher.from_env(page_name)
# ---------------------------------------------------------------------------
_PAGE_ENV_MAP: dict[str, dict[str, str]] = {
    "momma_circle": {
        "page_id":      "FB_MOMMA_CIRCLE_PAGE_ID",
        "access_token": "FB_MOMMA_CIRCLE_ACCESS_TOKEN",
    },
    # Add more pages here as needed:
    # "ancient_knowledge": {
    #     "page_id":      "FB_ANCIENT_KNOWLEDGE_PAGE_ID",
    #     "access_token": "FB_ANCIENT_KNOWLEDGE_ACCESS_TOKEN",
    # },
}


# ===========================================================================
# Core publisher
# ===========================================================================

class FacebookPagePublisher:
    """
    Publishes content to a single Facebook Page via the Graph API.

    Parameters
    ----------
    page_id : str
        Numeric Facebook Page ID (e.g. "415144745345466").
    access_token : str
        Page-scoped long-lived access token with ``pages_manage_posts``
        and ``pages_read_engagement`` permissions.
    api_version : str
        Graph API version string (default: "v25.0").
    """

    def __init__(
        self,
        page_id: str,
        access_token: str,
        api_version: str = "v25.0",
    ) -> None:
        if not page_id:
            raise ValueError("page_id must not be empty.")
        if not access_token:
            raise ValueError("access_token must not be empty.")
        self.page_id      = str(page_id)
        self.access_token = access_token
        self.api_version  = api_version.lstrip("v")
        self._base        = f"{_GRAPH_BASE}/v{self.api_version}"

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def from_env(cls, page_name: str = "momma_circle") -> "FacebookPagePublisher":
        """
        Construct a publisher from environment variables for *page_name*.

        Raises
        ------
        KeyError
            If *page_name* is not registered in ``_PAGE_ENV_MAP``.
        EnvironmentError
            If the required env vars are missing or empty.
        """
        mapping = _PAGE_ENV_MAP.get(page_name)
        if mapping is None:
            raise KeyError(
                f"Page '{page_name}' is not in _PAGE_ENV_MAP. "
                f"Known pages: {list(_PAGE_ENV_MAP)}"
            )

        # Load dotenv if python-dotenv is available (dev convenience)
        try:
            from dotenv import load_dotenv  # type: ignore[import]
            load_dotenv()
        except ImportError:
            pass

        page_id      = os.getenv(mapping["page_id"], "").strip()
        access_token = os.getenv(mapping["access_token"], "").strip()

        missing = []
        if not page_id:
            missing.append(mapping["page_id"])
        if not access_token:
            missing.append(mapping["access_token"])
        if missing:
            raise EnvironmentError(
                f"Missing env var(s) for page '{page_name}': {missing}"
            )

        api_version = os.getenv("FB_GRAPH_API_VERSION", "v25.0")
        return cls(page_id, access_token, api_version)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _endpoint(self, path: str) -> str:
        return f"{self._base}/{path.lstrip('/')}"

    def _raise_for_error(self, data: dict[str, Any], context: str = "") -> None:
        """Raise RuntimeError if the Graph API returned an error object."""
        err = data.get("error")
        if err:
            code    = err.get("code", "?")
            subcode = err.get("error_subcode", "")
            msg     = err.get("message", str(err))
            detail  = f"[{code}" + (f"/{subcode}" if subcode else "") + f"] {msg}"
            raise RuntimeError(
                f"Facebook Graph API error{' (' + context + ')' if context else ''}: "
                f"{detail}"
            )

    def _post(self, endpoint: str, **extra_params) -> dict[str, Any]:
        """
        POST to *endpoint* with access_token injected; return parsed JSON.

        Encoding strategy
        -----------------
        * If ``text_format_preset_id`` is present → form-encoded ``data=``
          payload (required by Meta's internal background-preset path).
        * All other posts → ``json=`` body (handles Unicode / emoji correctly
          and is accepted by all standard Graph API feed endpoints).
        """
        payload: dict[str, Any] = {"access_token": self.access_token}
        payload.update(extra_params)

        use_form = "text_format_preset_id" in extra_params

        if use_form:
            resp = requests.post(endpoint, data=payload, timeout=30)
        else:
            resp = requests.post(endpoint, json=payload, timeout=30)

        data: dict[str, Any] = resp.json()
        self._raise_for_error(data, context=endpoint)
        return data

    def _get(self, endpoint: str, **extra_params) -> dict[str, Any]:
        """GET *endpoint* with access_token injected; return parsed JSON."""
        params: dict[str, Any] = {"access_token": self.access_token}
        params.update(extra_params)
        resp = requests.get(endpoint, params=params, timeout=15)
        data: dict[str, Any] = resp.json()
        self._raise_for_error(data, context=endpoint)
        return data

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_page_info(self) -> dict[str, Any]:
        """
        Fetch basic page metadata to verify the token is valid and the
        page_id is correct.

        Returns dict with keys: id, name, fan_count, category.
        """
        endpoint = self._endpoint(f"{self.page_id}")
        data = self._get(endpoint, fields="id,name,fan_count,category")
        _LOG.info(
            "FB page verified | id=%s name=%r fans=%s",
            data.get("id"),
            data.get("name"),
            data.get("fan_count"),
        )
        return data

    def post_text(
        self,
        message: str,
        text_format_preset_id: "str | None" = None,
    ) -> str:
        """
        Publish a plain-text post to the page feed.

        Parameters
        ----------
        message : str
            The caption / body text. Supports Unicode (emoji, etc.).
        text_format_preset_id : str | None
            Optional Facebook text-format preset ID for styled background posts.
            When supplied the payload is sent as ``data=`` (form-encoded) as
            required by Meta's background-preset path.  If the API rejects it
            the method automatically retries as a plain text post so the post
            is never lost.

        Returns
        -------
        str
            The generated Facebook post ID (``{page_id}_{post_id}``).
        """
        if not message:
            raise ValueError("message must not be empty.")

        endpoint = self._endpoint(f"{self.page_id}/feed")

        if text_format_preset_id:
            try:
                data = self._post(
                    endpoint,
                    message=message,
                    text_format_preset_id=text_format_preset_id,
                )
                post_id = data.get("id", "")
                _LOG.info(
                    "FB styled text post published | preset=%s post_id=%s",
                    text_format_preset_id, post_id,
                )
                print(
                    f"[Facebook] Styled text post published"
                    f" | preset={text_format_preset_id} | ID: {post_id}"
                )
                return post_id
            except RuntimeError as preset_err:
                _LOG.warning(
                    "text_format_preset_id=%s rejected (%s) -- retrying as plain text.",
                    text_format_preset_id, preset_err,
                )
                print(
                    f"[Facebook] WARNING: preset {text_format_preset_id} rejected"
                    f" ({preset_err}). Retrying as plain text post."
                )

        # Plain text (primary path or preset fallback)
        data = self._post(endpoint, message=message)
        post_id = data.get("id", "")
        _LOG.info("FB text post published | post_id=%s", post_id)
        print(f"[Facebook] Text post published | ID: {post_id}")
        return post_id

    def post_photo_url(self, message: str, photo_url: str) -> str:
        """
        Publish a post with an image fetched from *photo_url*.

        Returns the Facebook post ID.
        """
        if not photo_url:
            raise ValueError("photo_url must not be empty.")

        endpoint = self._endpoint(f"{self.page_id}/photos")
        data = self._post(endpoint, message=message, url=photo_url)
        post_id = data.get("post_id") or data.get("id", "")
        _LOG.info("FB photo-URL post published | post_id=%s", post_id)
        print(f"[Facebook] Photo post published | ID: {post_id}")
        return post_id

    def post_photo_file(self, message: str, photo_path: str | Path) -> str:
        """
        Publish a post uploading a local image file.

        Returns the Facebook post ID.
        """
        photo_path = Path(photo_path)
        if not photo_path.is_file():
            raise FileNotFoundError(f"Photo not found: {photo_path}")

        endpoint = self._endpoint(f"{self.page_id}/photos")
        with photo_path.open("rb") as fh:
            resp = requests.post(
                endpoint,
                data={"message": message, "access_token": self.access_token},
                files={"source": (photo_path.name, fh, "image/jpeg")},
                timeout=60,
            )
        data: dict[str, Any] = resp.json()
        self._raise_for_error(data, context="post_photo_file")
        post_id = data.get("post_id") or data.get("id", "")
        _LOG.info("FB photo-file post published | post_id=%s", post_id)
        print(f"[Facebook] Photo file post published | ID: {post_id}")
        return post_id

    def post_video_url(self, message: str, video_url: str, title: str = "") -> str:
        """
        Post a video by providing a publicly accessible URL.
        Meta fetches the file server-side; no local upload needed.

        Returns the Facebook video/post ID.
        """
        if not video_url:
            raise ValueError("video_url must not be empty.")

        endpoint = self._endpoint(f"{self.page_id}/videos")
        params: dict[str, Any] = {
            "description": message,
            "file_url": video_url,
        }
        if title:
            params["title"] = title
        data = self._post(endpoint, **params)
        post_id = data.get("id", "")
        _LOG.info("FB video-URL post published | post_id=%s", post_id)
        print(f"[Facebook] Video (URL) post published | ID: {post_id}")
        return post_id

    def post_video_file(
        self,
        message: str,
        video_path: str | Path,
        title: str = "",
    ) -> str:
        """
        Upload a local MP4 video to the page feed.

        Uses multipart/form-data; suitable for files up to ~1 GB.
        For larger files use the resumable upload API instead.

        Returns the Facebook video/post ID.
        """
        video_path = Path(video_path)
        if not video_path.is_file():
            raise FileNotFoundError(f"Video not found: {video_path}")

        endpoint = self._endpoint(f"{self.page_id}/videos")
        file_mb = video_path.stat().st_size / 1_048_576
        print(f"[Facebook] Uploading video '{video_path.name}' ({file_mb:.1f} MB)...")

        with video_path.open("rb") as fh:
            form_data: dict[str, Any] = {
                "description": message,
                "access_token": self.access_token,
            }
            if title:
                form_data["title"] = title
            resp = requests.post(
                endpoint,
                data=form_data,
                files={"source": (video_path.name, fh, "video/mp4")},
                timeout=300,
            )

        data: dict[str, Any] = resp.json()
        self._raise_for_error(data, context="post_video_file")
        post_id = data.get("id", "")
        _LOG.info("FB video-file post published | post_id=%s", post_id)
        print(f"[Facebook] Video file uploaded | ID: {post_id}")
        return post_id

    def get_post(self, post_id: str, fields: str = "id,message,created_time") -> dict[str, Any]:
        """Fetch metadata for an existing post by *post_id*."""
        endpoint = self._endpoint(post_id)
        return self._get(endpoint, fields=fields)

# -*- coding: utf-8 -*-
"""
Backblaze B2 upload client — S3-compatible via boto3.

Ported from the working implementation in
  @ MEDIAUPSCALE_FACTORY/scheduler_module.py
with the same connection contract (SigV4, path-style addressing).

Credentials are read from the project .env:
  B2_KEY_ID            — application key ID
  B2_APPLICATION_KEY   — application key secret
  B2_BUCKET_NAME       — target bucket  (default: MediaupscaleStorage)
  B2_ENDPOINT_URL      — regional S3 endpoint

Usage
-----
    from avatar_engine.b2_client import B2VideoUploader

    uploader = B2VideoUploader()
    b2_url = uploader.upload(Path("clips/my_reel.mp4"))
    # → "https://MediaupscaleStorage.s3.us-east-005.backblazeb2.com/my_reel.mp4"
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# Public URL base is derived at import time from the bucket name + endpoint.
# Format: https://<bucket>.s3.<region>.backblazeb2.com
_B2_ENDPOINT_URL  = "https://s3.us-east-005.backblazeb2.com"
_B2_BUCKET_NAME   = "MediaupscaleStorage"
_B2_PUBLIC_BASE   = f"https://{_B2_BUCKET_NAME}.s3.us-east-005.backblazeb2.com"

# Upload resilience: 30-second connect/read timeout, up to 2 retries (3 total
# attempts) with a short backoff between attempts. Callers (main.py) already
# wrap ``upload()`` in a try/except and log-and-continue on total failure —
# this budget just maximizes the odds a transient network hiccup self-heals
# before that final fallback kicks in.
_B2_TIMEOUT_S: float = 30.0
_B2_MAX_ATTEMPTS: int = 3  # 1 initial attempt + 2 retries
_B2_RETRY_BACKOFF_S: float = 2.0


def _get_b2_resource():
    """
    Create a boto3 S3 resource connected to Backblaze B2.

    - signature_version='s3v4'      : mandatory for B2
    - addressing_style='path'       : forces path-style URLs (B2 rejects
                                      virtual-hosted style on multipart uploads)
    - endpoint_url hardcoded        : immune to .env whitespace/BOM issues
    """
    try:
        import boto3                       # noqa: PLC0415
        from botocore.config import Config  # noqa: PLC0415
    except ImportError as exc:
        raise RuntimeError(
            "boto3 is required for B2 uploads.  Run: pip install boto3"
        ) from exc

    key_id  = (os.getenv("B2_KEY_ID") or "").strip()
    app_key = (os.getenv("B2_APPLICATION_KEY") or "").strip()
    if not key_id or not app_key:
        raise RuntimeError(
            "B2_KEY_ID and B2_APPLICATION_KEY must be set in .env "
            "to use the B2 uploader."
        )

    endpoint = (os.getenv("B2_ENDPOINT_URL") or _B2_ENDPOINT_URL).strip()

    return boto3.resource(
        service_name="s3",
        endpoint_url=endpoint,
        aws_access_key_id=key_id,
        aws_secret_access_key=app_key,
        region_name="us-east-005",
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            connect_timeout=_B2_TIMEOUT_S,
            read_timeout=_B2_TIMEOUT_S,
        ),
    )


class B2VideoUploader:
    """
    Thin wrapper around boto3 for uploading video clips to Backblaze B2.

    Parameters
    ----------
    bucket_name : str
        B2 bucket name.  Defaults to the B2_BUCKET_NAME env var or
        "MediaupscaleStorage".
    """

    def __init__(self, bucket_name: str | None = None) -> None:
        self.bucket_name = (
            bucket_name
            or (os.getenv("B2_BUCKET_NAME") or _B2_BUCKET_NAME).strip()
        )
        self._b2 = _get_b2_resource()

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def upload(self, local_path: Path | str, *, content_type: str = "video/mp4") -> str:
        """
        Upload *local_path* to the bucket and return its public HTTPS URL.

        The object key is always the bare filename (no folder nesting) so
        the public URL is predictable:
            https://<bucket>.s3.us-east-005.backblazeb2.com/<filename>

        Returns the public URL regardless of whether the file was freshly
        uploaded or was already present in the bucket.
        """
        path = Path(local_path)
        if not path.is_file():
            raise FileNotFoundError(f"B2 upload source not found: {path}")

        key = path.name

        if self._object_exists(key):
            logger.info("[B2] Already uploaded: %s — skipping re-upload.", key)
        else:
            size_mb = path.stat().st_size / (1024 * 1024)
            print(f"[B2] Uploading {key} ({size_mb:.1f} MB) → {self.bucket_name} …")
            logger.info("[B2] Uploading %s (%.1f MB)", key, size_mb)

            last_exc: Exception | None = None
            for attempt in range(1, _B2_MAX_ATTEMPTS + 1):
                try:
                    self._b2.Object(self.bucket_name, key).upload_file(
                        str(path),
                        ExtraArgs={"ContentType": content_type},
                    )
                    last_exc = None
                    break
                except Exception as exc:  # noqa: BLE001
                    last_exc = exc
                    if attempt < _B2_MAX_ATTEMPTS:
                        logger.warning(
                            "[B2] Upload attempt %d/%d failed for %s (%s) — retrying in %.1fs…",
                            attempt, _B2_MAX_ATTEMPTS, key, exc, _B2_RETRY_BACKOFF_S,
                        )
                        time.sleep(_B2_RETRY_BACKOFF_S)
                    else:
                        logger.warning(
                            "[B2] Upload failed for %s after %d attempt(s): %s — "
                            "caller will fall back to local path.",
                            key, _B2_MAX_ATTEMPTS, exc,
                        )
            if last_exc is not None:
                raise last_exc

            print(f"[B2] Upload complete: {key}")
            logger.info("[B2] Upload complete: %s", key)

        return self.public_url(key)

    @staticmethod
    def public_url(filename: str) -> str:
        """Return the public HTTPS URL for a bare filename (no path prefix)."""
        bucket = (os.getenv("B2_BUCKET_NAME") or _B2_BUCKET_NAME).strip()
        return f"https://{bucket}.s3.us-east-005.backblazeb2.com/{Path(filename).name}"

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _object_exists(self, key: str) -> bool:
        """
        Check whether *key* already exists in the bucket.

        B2 returns 403 (not 404) for HeadObject on private buckets when the
        object is missing, so we treat both 403 and 404 as "not found".
        """
        try:
            from botocore.exceptions import ClientError  # noqa: PLC0415
        except ImportError:
            return False

        try:
            self._b2.Object(self.bucket_name, key).load()
            return True
        except Exception as exc:  # noqa: BLE001
            try:
                code = exc.response["Error"]["Code"]   # type: ignore[attr-defined]
                if code in ("404", "NoSuchKey"):
                    return False
                if code == "403":
                    logger.warning(
                        "[B2] HeadObject 403 for '%s' — "
                        "treating as not-yet-uploaded, proceeding.",
                        key,
                    )
                    return False
            except AttributeError:
                pass
            return False

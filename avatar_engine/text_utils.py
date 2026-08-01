# -*- coding: utf-8 -*-
"""Small string helpers shared across avatar_engine."""

from __future__ import annotations

import hashlib
import re

# Windows path budget is tight under Google Drive + page/assets nesting.
# Keep folder + filename stems short so full paths stay under MAX_PATH (~260).
_MAX_SUBJECT_SLUG_CHARS: int = 35
_MAX_OUTPUT_STEM_CHARS: int = 42


def _collapse_underscores(raw: str) -> str:
    parts = [p for p in raw.split("_") if p]
    return "_".join(parts) if parts else ""


def _truncate_slug(slug: str, max_len: int, *, digest_src: str | None = None) -> str:
    """Truncate *slug* to *max_len*, appending a short hash when truncated."""
    if len(slug) <= max_len:
        return slug or "general"
    # Leave room for "_xxxxxx" uniqueness suffix when truncating.
    keep = max(8, max_len - 7)
    head = slug[:keep].rstrip("_")
    if "_" in head:
        # Prefer cutting on a word boundary when possible.
        candidate = head.rsplit("_", 1)[0]
        if len(candidate) >= 8:
            head = candidate
    digest = hashlib.md5((digest_src or slug).encode("utf-8")).hexdigest()[:6]
    out = f"{head}_{digest}"
    return out[:max_len] if len(out) > max_len else out


def subject_slug(subject: str, *, max_len: int = _MAX_SUBJECT_SLUG_CHARS) -> str:
    """Filesystem-safe, length-capped slug for ``outputs/.../assets/<slug>/``."""
    raw = (subject or "").strip().lower()
    out = "".join(ch if ch.isalnum() else "_" for ch in raw)
    slug = _collapse_underscores(out) or "general"
    return _truncate_slug(slug, max_len, digest_src=raw or slug)


def safe_output_stem(stem: str, *, max_len: int = _MAX_OUTPUT_STEM_CHARS) -> str:
    """Filesystem-safe, length-capped basename stem for generated image files."""
    raw = (stem or "").strip()
    out = "".join(ch if ch.isalnum() else "_" for ch in raw)
    slug = _collapse_underscores(out) or "generated"
    # Preserve trailing ``_v01`` / ``_act02`` markers when truncating long stems.
    suffix_m = re.search(r"(_v\d{2,}(?:_act\d{2,})?)$", slug, flags=re.IGNORECASE)
    suffix = suffix_m.group(1) if suffix_m else ""
    body = slug[: -len(suffix)] if suffix else slug
    if not body:
        body = "generated"
    body_budget = max(8, max_len - len(suffix))
    body = _truncate_slug(body, body_budget, digest_src=slug)
    combined = f"{body}{suffix}"
    return combined[:max_len] if len(combined) > max_len else combined

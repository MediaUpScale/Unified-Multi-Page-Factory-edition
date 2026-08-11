# -*- coding: utf-8 -*-
"""
ECONOMIC_REEL_LOFI single-image aesthetic test.

Review/approval tool only — does NOT touch RAG stores, theme rotation,
history, validator, video assembly, or publish queues.
"""
from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image as PILImage

from core_engine.economic_reel_lofi import config as lofi_cfg
from core_engine.economic_reel_lofi.assembler import grade_still_frame
from core_engine.economic_reel_lofi.caption_style_lofi import (
    default_test_caption,
    render_lofi_caption_layer,
    render_lofi_watermark_layer,
)
from core_engine.economic_reel_lofi.image_gen import generate_scene_image

_LOG = logging.getLogger(__name__)

# Stable mid-narrative baseline (seed-aligned emotional beat — not an establishing shot).
DEFAULT_TEST_VISUAL_PROMPT: str = (
    "couple sitting together on a quiet hillside at dusk, soft silhouette against "
    "a muted sky, intimate mid-distance framing, emotional stillness, "
    "graphic-novel storytelling beat, central composition, vertical 9:16"
)


def _engine_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _preview_dir(page_id: str) -> Path:
    """Inside the page clips tree: outputs/{page}/clips/economic_reels_tests/."""
    return _engine_root() / "outputs" / page_id / "clips" / "economic_reels_tests"


def _purge_legacy_preview_dirs(page_id: str) -> None:
    """Remove the old duplicated economic_reel_lofi/test_preview tree."""
    legacy_root = _engine_root() / "outputs" / page_id / "economic_reel_lofi"
    if legacy_root.exists():
        try:
            shutil.rmtree(legacy_root)
            _LOG.info("Removed legacy LOFI preview folder: %s", legacy_root)
            print(f"[LOFI test-preview] removed legacy folder: {legacy_root}")
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("Could not remove legacy folder %s (%s)", legacy_root, exc)


def default_test_visual_prompt() -> str:
    """Prefer a seed mid-line context; fall back to the fixed hillside baseline."""
    seed = lofi_cfg.DATA_DIR / "seed_reference_structures.json"
    try:
        rows = json.loads(seed.read_text(encoding="utf-8"))
        if isinstance(rows, list) and rows:
            lines = rows[0].get("lines") or []
            if len(lines) >= 5:
                beat = str(lines[4]).strip()
                return f"{DEFAULT_TEST_VISUAL_PROMPT}, narrative mood: {beat}"
    except Exception:  # noqa: BLE001
        pass
    return DEFAULT_TEST_VISUAL_PROMPT


def run_lofi_test_preview(
    *,
    page_id: str,
    prompt: str | None = None,
    caption: str | None = None,
) -> dict[str, Any]:
    """
    Generate one graded PNG with LOFI caption + italic-serif watermark.

    Side effects: writes only under
    ``outputs/{page}/clips/economic_reels_tests/``.
    Never mutates lofi_generated_history_*, theme banks, or validator state.
    """
    page = (page_id or "").strip().lower()
    if page not in lofi_cfg.VALID_PAGES:
        raise ValueError(
            f"--test-preview supports pages {sorted(lofi_cfg.VALID_PAGES)} "
            f"(got {page!r})"
        )

    _purge_legacy_preview_dirs(page)

    engine_root = _engine_root()
    out_dir = _preview_dir(page)
    out_dir.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    visual = (prompt or "").strip() or default_test_visual_prompt()
    caption_text = (caption or "").strip() or default_test_caption()

    raw_path = out_dir / f"{stamp}_raw.png"
    final_path = out_dir / f"{stamp}.png"

    print(f"[LOFI test-preview] page={page}")
    print(f"[LOFI test-preview] prompt={visual[:160]}{'…' if len(visual) > 160 else ''}")
    print(f"[LOFI test-preview] caption={caption_text!r}")
    print("[LOFI test-preview] typography=Lora Italic (upper-middle)")

    # 1) Flux Schnell via shared production style prefix
    generate_scene_image(visual, raw_path)

    # 2) Exact production grading
    graded = grade_still_frame(raw_path, grain_seed=42)
    rgba = PILImage.fromarray(graded).convert("RGBA")

    # 3) LOFI caption — Lora Italic, white, soft shadow, upper-middle
    cap = render_lofi_caption_layer(caption_text, engine_root=engine_root)
    rgba = PILImage.alpha_composite(rgba, cap)

    # 4) Matching italic-serif text watermark (not a mixed sans/PNG brand mark)
    cfg = lofi_cfg.channel_assembly_cfg(page)
    handle = str(cfg.get("watermark_handle") or f"@{page.replace('_', ' ').title()}")
    wm = render_lofi_watermark_layer(
        handle,
        engine_root=engine_root,
        opacity=float(cfg.get("logo_opacity", 0.55)),
    )
    rgba = PILImage.alpha_composite(rgba, wm)

    rgba.convert("RGB").save(final_path, format="PNG", optimize=True)

    try:
        if final_path.is_file() and raw_path.is_file():
            raw_path.unlink()
    except Exception:  # noqa: BLE001
        pass

    result = {
        "ok": True,
        "post_type": "ECONOMIC_REEL_LOFI",
        "mode": "test_preview",
        "page": page,
        "prompt": visual,
        "caption": caption_text,
        "watermark_handle": handle,
        "font": "Lora Italic",
        "output_png": str(final_path),
        "style_prefix": lofi_cfg.LOFI_STYLE_PREFIX,
    }
    print(f"[LOFI test-preview] wrote {final_path}")
    return result

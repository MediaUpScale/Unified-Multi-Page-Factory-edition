# -*- coding: utf-8 -*-
"""
ECONOMIC_REEL_LOFI single-image aesthetic test.

Review/approval tool only — does NOT touch RAG stores, theme rotation,
history, validator, video assembly, or publish queues.
"""
from __future__ import annotations

import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image as PILImage

from core_engine.economic_reel_lofi import config as lofi_cfg
from core_engine.economic_reel_lofi.assembler import grade_still_frame, render_logo_layer
from core_engine.economic_reel_lofi.caption_style_lofi import (
    STYLE_DISPLAY_NAME,
    default_test_caption,
    normalize_caption_style,
    render_lofi_caption_layer,
    render_lofi_watermark_layer,
)
from core_engine.economic_reel_lofi.image_gen import generate_scene_image

_LOG = logging.getLogger(__name__)

# Concise mid-narrative beat — keep combined prompt (prefix + scene) under ~300 chars.
# Scene stays lighting-agnostic; mood lighting is injected via STYLE_PREFIX swap.
DEFAULT_TEST_VISUAL_PROMPT: str = (
    "couple sitting on a hillside, mid shot, emotional stillness"
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
    """Stable short baseline scene (no seed append — keeps Schnell prompt under budget)."""
    return DEFAULT_TEST_VISUAL_PROMPT


def run_lofi_test_preview(
    *,
    page_id: str,
    prompt: str | None = None,
    caption: str | None = None,
    mood_id: str | None = None,
    caption_style: str | None = None,
) -> dict[str, Any]:
    """
    Generate one graded PNG with LOFI caption + watermark.

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
    style_key = normalize_caption_style(
        caption_style or lofi_cfg.DEFAULT_CAPTION_STYLE
    )
    font_name = STYLE_DISPLAY_NAME.get(style_key, style_key)

    raw_path = out_dir / f"{stamp}_raw.png"
    final_path = out_dir / f"{stamp}.png"

    print(f"[LOFI test-preview] page={page}")
    print(f"[LOFI test-preview] prompt={visual[:160]}{'...' if len(visual) > 160 else ''}")
    print(f"[LOFI test-preview] caption={caption_text!r}")
    print(f"[LOFI test-preview] typography={font_name} (style={style_key})")

    # 1) Flux Schnell via mood-swapped style prefix
    _, mood = generate_scene_image(visual, raw_path, mood_id=mood_id)
    prefix = lofi_cfg.build_style_prefix(mood)

    # 2) Exact production grading — duotone pair matches the lighting mood
    sh = tuple(mood["shadow"])
    hi = tuple(mood["highlight"])
    print(
        "[LOFI test-preview] grading via grade_still_frame "
        f"(mood={mood['id']} | duotone shadow={sh} highlight={hi})"
    )
    graded = grade_still_frame(
        raw_path,
        grain_seed=42,
        shadow=sh,
        highlight=hi,
        mood_id=str(mood["id"]),
    )
    rgba = PILImage.fromarray(graded).convert("RGBA")

    # 3) LOFI caption — default Caveat (caveat)
    cap = render_lofi_caption_layer(
        caption_text, engine_root=engine_root, style=style_key,
    )
    rgba = PILImage.alpha_composite(rgba, cap)

    # 4) Channel watermark — PNG logo when configured, else text handle
    cfg = lofi_cfg.channel_assembly_cfg(page)
    handle = str(cfg.get("watermark_handle") or f"@{page.replace('_', ' ').title()}")
    logo_scale = float(cfg.get("logo_scale", 0.04))
    logo_opacity = float(cfg.get("logo_opacity", 0.55))
    use_text = bool(cfg.get("use_text_watermark", True))
    logo_path = lofi_cfg.resolve_logo_path(page, engine_root)

    if (not use_text) and logo_path is not None:
        logo_arr = render_logo_layer(
            logo_path,
            opacity=logo_opacity,
            scale=logo_scale,
        )
        if logo_arr is not None:
            rgba = PILImage.alpha_composite(rgba, PILImage.fromarray(logo_arr))
            print(
                f"[LOFI test-preview] PNG logo placed: {logo_path} "
                f"(scale={logo_scale}, opacity={logo_opacity})"
            )
        else:
            print(f"[LOFI test-preview] WARN: logo failed to load: {logo_path}")
    else:
        wm = render_lofi_watermark_layer(
            handle,
            engine_root=engine_root,
            opacity=logo_opacity,
            style=style_key,
        )
        # Shrink text watermark to match logo_scale (0.06 baseline → current).
        _wm_scale = logo_scale / 0.06
        if abs(_wm_scale - 1.0) > 0.01:
            _alpha = wm.split()[-1]
            _bbox = _alpha.getbbox()
            if _bbox:
                _crop = wm.crop(_bbox)
                _nw = max(1, int(_crop.width * _wm_scale))
                _nh = max(1, int(_crop.height * _wm_scale))
                _crop = _crop.resize((_nw, _nh), PILImage.LANCZOS)
                _wm2 = PILImage.new("RGBA", wm.size, (0, 0, 0, 0))
                _x = (wm.width - _nw) // 2
                _y = _bbox[3] - _nh
                _wm2.paste(_crop, (_x, _y), _crop)
                wm = _wm2
                print(
                    f"[LOFI test-preview] text watermark scale logo_scale={logo_scale} "
                    f"(was 0.06) -> factor={_wm_scale:.3f}"
                )
        rgba = PILImage.alpha_composite(rgba, wm)
        if use_text and logo_path is None:
            print("[LOFI test-preview] WARN: no PNG logo found; used text handle")
        elif use_text:
            print(f"[LOFI test-preview] text watermark (PNG available at {logo_path})")

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
        "logo_path": str(logo_path) if logo_path else None,
        "use_text_watermark": use_text,
        "font": font_name,
        "caption_style": style_key,
        "mood_id": mood.get("id"),
        "lighting": mood.get("lighting"),
        "duotone_shadow": sh,
        "duotone_highlight": hi,
        "output_png": str(final_path),
        "style_prefix": prefix,
        "full_prompt_len": len(f"{prefix}. {visual}".strip()),
    }
    print(
        f"[LOFI test-preview] wrote {final_path} | "
        f"mood={mood.get('id')} | full_prompt_len={result['full_prompt_len']} | "
        f"font={font_name}"
    )
    return result

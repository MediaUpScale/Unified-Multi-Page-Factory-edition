# -*- coding: utf-8 -*-
"""LOFI image generation — Together.ai Flux Schnell, no LoRA."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from core_engine.economic_reel_lofi import config as lofi_cfg

_LOG = logging.getLogger(__name__)


def build_scene_prompt(visual_prompt: str) -> str:
    body = " ".join((visual_prompt or "").strip().split())
    return f"{lofi_cfg.LOFI_STYLE_PREFIX}. {body}".strip()


def generate_scene_image(
    visual_prompt: str,
    output_path: Path,
    *,
    width: int = 768,
    height: int = 1344,
) -> Path:
    """Generate one vertical still with Flux Schnell (base model, no LoRA)."""
    from avatar_engine.providers.together_image import (
        FLUX_SCHNELL_MODEL,
        TogetherImageGenerator,
    )

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    prompt = build_scene_prompt(visual_prompt)
    print(f"[LOFI image_gen] STYLE_PREFIX={lofi_cfg.LOFI_STYLE_PREFIX!r}")
    print(f"[LOFI image_gen] full_prompt_len={len(prompt)}")
    _LOG.info("LOFI STYLE_PREFIX | %s", lofi_cfg.LOFI_STYLE_PREFIX)
    gen = TogetherImageGenerator(model=FLUX_SCHNELL_MODEL)
    # No LoRA kwargs — economic LOFI tier is base Schnell only.
    gen.generate_image(
        prompt,
        output_path,
        orientation="vertical",
        width=width,
        height=height,
        negative_prompt=lofi_cfg.LOFI_NEGATIVE_PROMPT,
        model_name=FLUX_SCHNELL_MODEL,
        steps=4,
    )
    if not output_path.is_file():
        raise FileNotFoundError(f"Flux Schnell did not write image: {output_path}")
    _LOG.info("LOFI image OK → %s", output_path.name)
    return output_path


def generate_all_scene_images(
    lines: list[dict[str, Any]],
    run_dir: Path,
) -> list[Path]:
    paths: list[Path] = []
    for row in lines:
        scene = int(row.get("scene") or len(paths) + 1)
        out = run_dir / f"scene_{scene:02d}.png"
        generate_scene_image(str(row.get("visual_prompt") or ""), out)
        paths.append(out)
    return paths

# -*- coding: utf-8 -*-
"""LOFI image generation — Together.ai Flux Schnell only, no LoRA, no MODEL_API_FLOW."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from core_engine.economic_reel_lofi import config as lofi_cfg

_LOG = logging.getLogger(__name__)

# Hard-locked provider identity for ECONOMIC_REEL_LOFI (never inherit env flow).
LOFI_IMAGE_PROVIDER: str = "together.ai"
LOFI_IMAGE_MODEL: str = "black-forest-labs/FLUX.1-schnell"
LOFI_IMAGE_STEPS: int = 4


def build_scene_prompt(
    visual_prompt: str,
    *,
    mood: dict[str, Any] | None = None,
    mood_id: str | None = None,
    mood_key: str | int | None = None,
) -> tuple[str, dict[str, Any]]:
    """
    Build short Flux prompt = style base + lighting mood + scene body.

    Returns (full_prompt, resolved_mood).
    """
    resolved = (
        mood
        if isinstance(mood, dict) and mood.get("lighting")
        else lofi_cfg.select_lighting_mood(key=mood_key, mood_id=mood_id)
    )
    prefix = lofi_cfg.build_style_prefix(resolved)
    body = " ".join((visual_prompt or "").strip().split())
    # Drop redundant time-of-day words from scene if mood already carries lighting
    return f"{prefix}. {body}".strip(), resolved


def generate_scene_image(
    visual_prompt: str,
    output_path: Path,
    *,
    width: int = 768,
    height: int = 1344,
    mood: dict[str, Any] | None = None,
    mood_id: str | None = None,
    mood_key: str | int | None = None,
) -> tuple[Path, dict[str, Any]]:
    """
    Generate one vertical still via Together.ai FLUX.1-schnell (base model, no LoRA).

    Explicitly bypasses MODEL_API_FLOW / remote_gpu / ComfyUI / any LoRA inheritance,
    regardless of ENABLE_REMOTE_GPU_WORKFLOWS or env_default presets.

    Returns (output_path, resolved_mood) so grading can match the lighting pair.
    """
    from avatar_engine.providers.together_image import TogetherImageGenerator

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    prompt, resolved = build_scene_prompt(
        visual_prompt, mood=mood, mood_id=mood_id, mood_key=mood_key,
    )
    prefix = lofi_cfg.build_style_prefix(resolved)

    print(
        "[LOFI image_gen] PROVIDER FORCED | "
        f"provider={LOFI_IMAGE_PROVIDER} | model={LOFI_IMAGE_MODEL} | "
        f"steps={LOFI_IMAGE_STEPS} | lora=OFF | "
        "bypasses MODEL_API_FLOW / remote_gpu / ComfyUI"
    )
    print(
        f"[LOFI image_gen] mood={resolved.get('id')} | "
        f"lighting={resolved.get('lighting')!r}"
    )
    print(f"[LOFI image_gen] STYLE_PREFIX={prefix!r}")
    print(f"[LOFI image_gen] STYLE_PREFIX_len={len(prefix)}")
    print(f"[LOFI image_gen] full_prompt_len={len(prompt)}")
    print(f"[LOFI image_gen] full_prompt={prompt!r}")
    _LOG.info(
        "LOFI image FORCED together/schnell | lora=OFF | mood=%s | prefix=%s",
        resolved.get("id"),
        prefix,
    )

    # Pin model at construction AND per-call; allow_lora=False skips resolve_effective_lora.
    gen = TogetherImageGenerator(model=LOFI_IMAGE_MODEL)
    gen.generate_image(
        prompt,
        output_path,
        orientation="vertical",
        width=width,
        height=height,
        negative_prompt=lofi_cfg.LOFI_NEGATIVE_PROMPT,
        model_name=LOFI_IMAGE_MODEL,
        steps=LOFI_IMAGE_STEPS,
        allow_lora=False,
    )
    if not output_path.is_file():
        raise FileNotFoundError(f"Flux Schnell did not write image: {output_path}")
    print(
        f"[LOFI image_gen] OK | file={output_path.name} | "
        f"confirmed_model={LOFI_IMAGE_MODEL} | lora=OFF | mood={resolved.get('id')}"
    )
    _LOG.info("LOFI image OK → %s mood=%s", output_path.name, resolved.get("id"))
    return output_path, resolved


def generate_all_scene_images(
    lines: list[dict[str, Any]],
    run_dir: Path,
    *,
    theme: str = "",
) -> tuple[list[Path], list[dict[str, Any]]]:
    paths: list[Path] = []
    moods: list[dict[str, Any]] = []
    for row in lines:
        scene = int(row.get("scene") or len(paths) + 1)
        out = run_dir / f"scene_{scene:02d}.png"
        # Prefer explicit lighting_mood from script metadata; else rotate by theme+scene
        mood_id = row.get("lighting_mood") or row.get("mood")
        mood_key = f"{theme}|scene{scene}"
        _, resolved = generate_scene_image(
            str(row.get("visual_prompt") or ""),
            out,
            mood_id=str(mood_id) if mood_id else None,
            mood_key=mood_key,
        )
        # Persist onto the row so assembler / meta can reuse
        row["lighting_mood"] = resolved.get("id")
        paths.append(out)
        moods.append(resolved)
    return paths, moods

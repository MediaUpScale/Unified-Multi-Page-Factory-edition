# -*- coding: utf-8 -*-
"""LOFI image generation — Schnell live path + Flux Dev sibling. No LoRA."""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from core_engine.economic_reel_lofi import config as lofi_cfg

_LOG = logging.getLogger(__name__)

# Hard-locked provider identity for ECONOMIC_REEL_LOFI (never inherit env flow).
LOFI_IMAGE_PROVIDER: str = "together.ai"
LOFI_IMAGE_MODEL: str = "black-forest-labs/FLUX.1-schnell"
LOFI_IMAGE_STEPS: int = 4
# Actual per-call resolution — aliases of config so cost accounting and
# generation always request the same size (720×1280 exact 9:16).
LOFI_IMAGE_WIDTH: int = int(lofi_cfg.LOFI_IMAGE_WIDTH)
LOFI_IMAGE_HEIGHT: int = int(lofi_cfg.LOFI_IMAGE_HEIGHT)


def build_scene_prompt(
    visual_prompt: str,
    *,
    mood: dict[str, Any] | None = None,
    mood_id: str | None = None,
    mood_key: str | int | None = None,
    verbatim: bool | None = None,
) -> tuple[str, dict[str, Any]]:
    """
    Build Flux prompt.

    When ``verbatim`` (default: USE_RISO_PROMPT_LIBRARY), the visual_prompt is
    used as-is — no mood lighting / style prefix that remaps the library palette.
    """
    use_verbatim = (
        bool(lofi_cfg.USE_RISO_PROMPT_LIBRARY)
        if verbatim is None
        else bool(verbatim)
    )
    body = " ".join((visual_prompt or "").strip().split())
    guard = str(getattr(lofi_cfg, "LOFI_PROMPT_EXPOSURE_GUARD", "") or "").strip()
    if use_verbatim and body:
        if guard and guard.lower() not in body.lower():
            body = f"{body} {guard}"
        # Lightweight metadata only (not used to remap palette)
        meta = {
            "id": "verbatim_riso",
            "lighting": "from_prompt",
            "shadow": lofi_cfg.DUOTONE_SHADOW,
            "highlight": lofi_cfg.DUOTONE_HIGHLIGHT,
        }
        if isinstance(mood, dict):
            meta = {**meta, **mood}
        return body, meta

    resolved = (
        mood
        if isinstance(mood, dict) and mood.get("lighting")
        else lofi_cfg.select_lighting_mood(key=mood_key, mood_id=mood_id)
    )
    prefix = lofi_cfg.build_style_prefix(resolved)
    return f"{prefix}. {body}".strip(), resolved


def generate_scene_image(
    visual_prompt: str,
    output_path: Path,
    *,
    width: int = LOFI_IMAGE_WIDTH,
    height: int = LOFI_IMAGE_HEIGHT,
    mood: dict[str, Any] | None = None,
    mood_id: str | None = None,
    mood_key: str | int | None = None,
    verbatim: bool | None = None,
) -> tuple[Path, dict[str, Any]]:
    """
    Generate one vertical still via Together.ai FLUX.1-schnell (base model, no LoRA).

    Returns (output_path, resolved_mood_meta).
    """
    from avatar_engine.providers.together_image import TogetherImageGenerator

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    prompt, resolved = build_scene_prompt(
        visual_prompt,
        mood=mood,
        mood_id=mood_id,
        mood_key=mood_key,
        verbatim=verbatim,
    )

    print(
        "[LOFI image_gen] PROVIDER FORCED | "
        f"provider={LOFI_IMAGE_PROVIDER} | model={LOFI_IMAGE_MODEL} | "
        f"steps={LOFI_IMAGE_STEPS} | lora=OFF | "
        "bypasses MODEL_API_FLOW / remote_gpu / ComfyUI"
    )
    print(
        f"[LOFI image_gen] verbatim={bool(lofi_cfg.USE_RISO_PROMPT_LIBRARY if verbatim is None else verbatim)} | "
        f"meta_id={resolved.get('id')}"
    )
    print(f"[LOFI image_gen] full_prompt_len={len(prompt)}")
    print(f"[LOFI image_gen] full_prompt={prompt!r}")
    _LOG.info(
        "LOFI image FORCED together/schnell | lora=OFF | verbatim | id=%s",
        resolved.get("id"),
    )

    print(
        f"[LOFI image_gen] request={width}x{height} "
        f"aspect={width / max(height, 1):.6f} target_9_16=0.562500"
    )
    t0 = time.perf_counter()
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
    elapsed = time.perf_counter() - t0
    if not output_path.is_file():
        raise FileNotFoundError(f"Flux Schnell did not write image: {output_path}")
    print(
        f"[LOFI image_gen] OK | file={output_path.name} | "
        f"confirmed_model={LOFI_IMAGE_MODEL} | lora=OFF | id={resolved.get('id')} "
        f"| size={width}x{height} elapsed_s={elapsed:.2f}"
    )
    _LOG.info("LOFI image OK → %s id=%s", output_path.name, resolved.get("id"))
    return output_path, resolved


def generate_scene_image_dev(
    visual_prompt: str,
    output_path: Path,
    *,
    width: int = LOFI_IMAGE_WIDTH,
    height: int = LOFI_IMAGE_HEIGHT,
    mood: dict[str, Any] | None = None,
    mood_id: str | None = None,
    mood_key: str | int | None = None,
    verbatim: bool | None = None,
) -> tuple[Path, dict[str, Any]]:
    """
    Generate one vertical still via Together.ai FLUX.1-dev (no LoRA).

    Same contract as generate_scene_image so generate_and_qa_scene can swap.
    Does not call generate_scene_image or the Schnell DeepInfra branch.
    """
    from avatar_engine.providers.together_image import TogetherImageGenerator

    model = str(getattr(lofi_cfg, "LOFI_DEV_IMAGE_MODEL", "") or "black-forest-labs/FLUX.1-dev")
    steps = int(getattr(lofi_cfg, "LOFI_DEV_IMAGE_STEPS", 20) or 20)
    guidance = float(getattr(lofi_cfg, "LOFI_DEV_GUIDANCE_SCALE", 4.0) or 4.0)
    negative = str(getattr(lofi_cfg, "LOFI_DEV_NEGATIVE_PROMPT", "") or "")

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    prompt, resolved = build_scene_prompt(
        visual_prompt,
        mood=mood,
        mood_id=mood_id,
        mood_key=mood_key,
        verbatim=verbatim,
    )

    print(
        "[LOFI image_gen_dev] PROVIDER FORCED | "
        f"model={model} | via=TogetherImageGenerator | "
        f"steps={steps} | guidance_scale={guidance} | lora=OFF | "
        "skip_mandatory_negative=1 | "
        "DeepInfra FLUX-1-dev if Together serverless is unavailable"
    )
    print(
        f"[LOFI image_gen_dev] verbatim={bool(lofi_cfg.USE_RISO_PROMPT_LIBRARY if verbatim is None else verbatim)} | "
        f"meta_id={resolved.get('id')}"
    )
    print(f"[LOFI image_gen_dev] full_prompt_len={len(prompt)}")
    print(f"[LOFI image_gen_dev] full_prompt={prompt!r}")
    print(f"[LOFI image_gen_dev] negative_len={len(negative)}")
    _LOG.info(
        "LOFI image FORCED together/dev | lora=OFF | verbatim | steps=%s cfg=%s id=%s",
        steps,
        guidance,
        resolved.get("id"),
    )

    print(
        f"[LOFI image_gen_dev] request={width}x{height} "
        f"aspect={width / max(height, 1):.6f} target_9_16=0.562500"
    )
    t0 = time.perf_counter()
    gen = TogetherImageGenerator(model=model)
    gen.generate_image(
        prompt,
        output_path,
        orientation="vertical",
        width=width,
        height=height,
        negative_prompt=negative,
        model_name=model,
        steps=steps,
        allow_lora=False,
        skip_mandatory_negative=True,
        guidance_scale=guidance,
    )
    elapsed = time.perf_counter() - t0
    if not output_path.is_file():
        raise FileNotFoundError(f"Flux Dev did not write image: {output_path}")
    print(
        f"[LOFI image_gen_dev] OK | file={output_path.name} | "
        f"confirmed_model={model} | lora=OFF | id={resolved.get('id')} "
        f"| size={width}x{height} elapsed_s={elapsed:.2f}"
    )
    _LOG.info("LOFI Dev image OK → %s id=%s", output_path.name, resolved.get("id"))
    return output_path, resolved

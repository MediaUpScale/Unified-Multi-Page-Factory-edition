# -*- coding: utf-8 -*-
"""LOFI image generation through the active style's Together.ai model."""
from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path
from typing import Any

from core.economic_reel_lofi import config as lofi_cfg

_LOG = logging.getLogger(__name__)
FULL_BLEED_TRIM_FRAC: float = 0.05


def enforce_full_bleed(
    image_path: Path,
    *,
    trim_frac: float = FULL_BLEED_TRIM_FRAC,
) -> Path:
    """Crop the generated outer edge so model-invented paper margins cannot ship."""
    from PIL import Image

    path = Path(image_path)
    with Image.open(path) as source:
        image = source.convert("RGB")
        width, height = image.size
        dx = max(1, round(width * max(0.0, min(float(trim_frac), 0.15))))
        dy = max(1, round(height * max(0.0, min(float(trim_frac), 0.15))))
        cropped = image.crop((dx, dy, width - dx, height - dy))
        cropped.resize((width, height), Image.Resampling.LANCZOS).save(path)
    print(
        f"[LOFI full-bleed] cropped outer {float(trim_frac) * 100:.1f}% "
        f"and restored {width}x{height} → {path.name}"
    )
    return path


def _stamp_gen_meta(
    resolved: dict[str, Any] | None,
    *,
    prompt: str,
    negative: str,
    model: str,
    guidance_scale: float | None,
    seed: Any = None,
) -> dict[str, Any]:
    rec = dict(resolved or {})
    rec["gen_prompt"] = prompt
    rec["gen_negative_prompt"] = negative
    rec["gen_model"] = model
    rec["gen_guidance_scale"] = guidance_scale
    rec["gen_seed"] = seed
    return rec

# Active style identity for ECONOMIC_REEL_LOFI (never inherit global env model).
LOFI_IMAGE_PROVIDER: str = "together.ai"
LOFI_IMAGE_MODEL: str = lofi_cfg.LOFI_DEV_IMAGE_MODEL
LOFI_IMAGE_STEPS: int = int(lofi_cfg.LOFI_DEV_IMAGE_STEPS)
LOFI_IMAGE_GUIDANCE_SCALE: float = float(lofi_cfg.LOFI_DEV_GUIDANCE_SCALE)
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
    Generate one vertical still via the active Together.ai model, without LoRA.

    Returns (output_path, resolved_mood_meta).
    """
    from agents.media.providers.together_image import TogetherImageGenerator

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
        f"steps={LOFI_IMAGE_STEPS} | guidance_scale={LOFI_IMAGE_GUIDANCE_SCALE} | "
        "lora=OFF | "
        "bypasses MODEL_API_FLOW / remote_gpu / ComfyUI"
    )
    print(
        f"[LOFI image_gen] verbatim={bool(lofi_cfg.USE_RISO_PROMPT_LIBRARY if verbatim is None else verbatim)} | "
        f"meta_id={resolved.get('id')}"
    )
    print(f"[LOFI image_gen] full_prompt_len={len(prompt)}")
    print(f"[LOFI image_gen] full_prompt={prompt!r}")
    _LOG.info(
        "LOFI image FORCED together/active-style | lora=OFF | verbatim | id=%s",
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
        negative_prompt=lofi_cfg.LOFI_DEV_NEGATIVE_PROMPT,
        model_name=LOFI_IMAGE_MODEL,
        steps=LOFI_IMAGE_STEPS,
        allow_lora=False,
        skip_mandatory_negative=True,
        guidance_scale=LOFI_IMAGE_GUIDANCE_SCALE,
    )
    elapsed = time.perf_counter() - t0
    if not output_path.is_file():
        raise FileNotFoundError(f"Active Flux model did not write image: {output_path}")
    enforce_full_bleed(output_path)
    print(
        f"[LOFI image_gen] OK | file={output_path.name} | "
        f"confirmed_model={LOFI_IMAGE_MODEL} | lora=OFF | id={resolved.get('id')} "
        f"| size={width}x{height} elapsed_s={elapsed:.2f}"
    )
    _LOG.info("LOFI image OK → %s id=%s", output_path.name, resolved.get("id"))
    return output_path, _stamp_gen_meta(
        resolved,
        prompt=prompt,
        negative=str(lofi_cfg.LOFI_DEV_NEGATIVE_PROMPT or ""),
        model=LOFI_IMAGE_MODEL,
        guidance_scale=LOFI_IMAGE_GUIDANCE_SCALE,
        seed=None,
    )


def generate_scene_image_gemini(
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
    """Generate a LOFI still through Google's Flash Image-only model chain."""
    from PIL import Image, ImageOps
    from agents.media.providers.image_provider import GeminiImageAdapter

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    prompt, resolved = build_scene_prompt(
        visual_prompt,
        mood=mood,
        mood_id=mood_id,
        mood_key=mood_key,
        verbatim=verbatim,
    )
    model = "models/gemini-3.1-flash-image"
    print(
        "[LOFI image_gen] provider=google | model=Gemini Flash Image chain | "
        f"request_aspect=9:16 target={width}x{height}"
    )
    t0 = time.perf_counter()
    adapter = GeminiImageAdapter(model_id=model, tier="cheap")
    generated = adapter.generate(
        prompt,
        output_stem=output_path.stem,
        output_directory=output_path.parent,
        aspect_ratio="9:16",
        avatar_mode="OFF",
    )
    generated = Path(generated)
    if generated.resolve() != output_path.resolve():
        shutil.copy2(generated, output_path)
    with Image.open(output_path) as image:
        if image.size != (width, height):
            fitted = ImageOps.fit(image.convert("RGB"), (width, height))
            fitted.save(output_path)
    if not output_path.is_file():
        raise FileNotFoundError(f"Gemini Flash did not write image: {output_path}")
    used_model = str(getattr(adapter, "last_gemini_image_model_used", model) or model)
    elapsed = time.perf_counter() - t0
    print(f"[LOFI image_gen] Gemini OK | model={used_model} elapsed_s={elapsed:.2f}")
    return output_path, _stamp_gen_meta(
        resolved,
        prompt=prompt,
        negative="",
        model=used_model,
        guidance_scale=None,
        seed=None,
    )


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
    Generate one vertical still via the active Together.ai Flux Dev model.

    Same contract as generate_scene_image so generate_and_qa_scene can swap.
    Does not call generate_scene_image or the Schnell DeepInfra branch.
    """
    from agents.media.providers.together_image import TogetherImageGenerator

    if bool(getattr(lofi_cfg, "uses_flux2_dev", lambda: False)()):
        model = str(
            getattr(lofi_cfg, "LOFI_FLUX2_DEV_MODEL", "")
            or "black-forest-labs/FLUX.2-dev"
        )
        steps = int(getattr(lofi_cfg, "LOFI_FLUX2_DEV_STEPS", 24) or 24)
        guidance = float(
            getattr(lofi_cfg, "LOFI_FLUX2_DEV_GUIDANCE_SCALE", 5.5) or 5.5
        )
    else:
        model = str(getattr(lofi_cfg, "LOFI_DEV_IMAGE_MODEL", "") or "black-forest-labs/FLUX.1-dev")
        steps = int(getattr(lofi_cfg, "LOFI_DEV_IMAGE_STEPS", 20) or 20)
        guidance = float(getattr(lofi_cfg, "LOFI_DEV_GUIDANCE_SCALE", 4.0) or 4.0)
    licensed = ""
    not_in: list[str] = []
    if isinstance(mood, dict):
        licensed = str(mood.get("licensed_object") or "")
        raw_not = mood.get("not_in_frame") or []
        if isinstance(raw_not, (list, tuple)):
            not_in = [str(x).strip() for x in raw_not if str(x).strip()]
    stamped = ""
    if isinstance(mood, dict):
        stamped = str(mood.get("negative_prompt") or "").strip()
    compose = getattr(lofi_cfg, "compose_beat_negative", None) or getattr(
        lofi_cfg, "compose_dev_negative", None
    )
    if stamped:
        negative = stamped
    elif callable(compose):
        try:
            negative = str(compose(licensed_object=licensed, not_in_frame=not_in) or "")
        except TypeError:
            negative = str(compose(licensed_object=licensed) or "")
    else:
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
        "Together.ai direct active-style request"
    )
    print(
        f"[LOFI image_gen_dev] verbatim={bool(lofi_cfg.USE_RISO_PROMPT_LIBRARY if verbatim is None else verbatim)} | "
        f"meta_id={resolved.get('id')}"
    )
    print(f"[LOFI image_gen_dev] full_prompt_len={len(prompt)}")
    print(f"[LOFI image_gen_dev] full_prompt={prompt!r}")
    print(f"[LOFI image_gen_dev] negative_len={len(negative)}")
    print(f"[LOFI image_gen_dev] negative_prompt={negative!r}")
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
    enforce_full_bleed(output_path)
    print(
        f"[LOFI image_gen_dev] OK | file={output_path.name} | "
        f"confirmed_model={model} | lora=OFF | id={resolved.get('id')} "
        f"| size={width}x{height} elapsed_s={elapsed:.2f}"
    )
    _LOG.info("LOFI Dev image OK → %s id=%s", output_path.name, resolved.get("id"))
    return output_path, _stamp_gen_meta(
        resolved,
        prompt=prompt,
        negative=negative,
        model=model,
        guidance_scale=guidance,
        seed=None,
    )

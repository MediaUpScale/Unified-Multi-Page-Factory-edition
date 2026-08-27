# -*- coding: utf-8 -*-
"""
Gemini Vision Agent — HARD RESET.

1. Load ALL images from ``channels_config/master_mei/style_reference/``.
2. Force Gemini Vision to inspect them as style ground truth (palette / texture / env).
3. Emit the CANONICAL scene template for the concept — never freeform LLM scene rewrites
   that soften into gym/stock photography.
"""
from __future__ import annotations

import logging
import os
import re
import threading
from pathlib import Path
from typing import Any, Sequence

_LOG = logging.getLogger(__name__)

_IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp")
_INSTRUCTION_VERSION = "v2-hard-reset-canonical"

MASTER_STYLE_ANCHOR: str = (
    "dark 80s dystopian cyberpunk, brutalist concrete architecture, ancient stone "
    "monoliths, dark muted cinematic tones, heavy ash rain, rusted iron, monochrome "
    "CRT monitor walls, high contrast cinematic lighting, 35mm film grain, "
    "8k photorealistic shot"
)

_STYLE_INSPECT_INSTRUCTION: str = (
    "Analyze ALL of these visual style reference images for dark brutalist dystopian DNA: "
    "brutalist concrete, ancient stone monoliths, monochrome CRT walls, ash rain, rusted "
    "iron, muted cinematic tones — NOT smartphones, neon chokers, or gore. "
    "Output ONLY a short confirmation line starting with STYLE_OK: followed by "
    "8–20 comma-separated atmosphere nouns. "
    "Do NOT invent a new scene. Do NOT mention smartphones, glowing collars, gore, "
    "gym clothes, or Midjourney tags."
)

_lock = threading.Lock()
_parts_cache: dict[str, list[Any]] = {}
_name_cache: dict[str, list[str]] = {}
_inspected_folders: set[str] = set()


def default_style_reference_folder(engine_root: "str | Path | None" = None) -> Path:
    if engine_root is None:
        try:
            import config as app_config
            root = Path(app_config.ENGINE_ROOT)
        except Exception:
            root = Path(__file__).resolve().parents[2]
    else:
        root = Path(engine_root)
    return (root / "channels_config" / "master_mei" / "style_reference").resolve()


def list_style_reference_images(reference_folder: str | Path) -> list[Path]:
    folder = Path(reference_folder)
    if not folder.is_dir():
        return []
    return [
        p for p in sorted(folder.iterdir(), key=lambda x: x.name.lower())
        if p.is_file() and p.suffix.lower() in _IMAGE_EXTENSIONS
    ]


def _mime_for(filename: str) -> str:
    lo = filename.lower()
    if lo.endswith(".png"):
        return "image/png"
    if lo.endswith(".webp"):
        return "image/webp"
    return "image/jpeg"


def _folder_cache_key(folder: Path) -> str:
    try:
        mtimes = [
            p.stat().st_mtime_ns
            for p in folder.iterdir()
            if p.is_file() and p.suffix.lower() in _IMAGE_EXTENSIONS
        ]
        stamp = max(mtimes) if mtimes else 0
    except Exception:
        stamp = 0
    return f"{folder.resolve()}::{stamp}::{_INSTRUCTION_VERSION}"


def load_reference_image_parts(
    reference_folder: str | Path,
    *,
    max_images: int = 16,
) -> tuple[list[Any], list[str]]:
    """Load ALL style_reference images as Gemini Part objects."""
    from google.genai import types

    folder = Path(reference_folder)
    key = _folder_cache_key(folder)
    with _lock:
        if key in _parts_cache:
            return list(_parts_cache[key]), list(_name_cache.get(key, []))

    parts: list[Any] = []
    names: list[str] = []
    # Load every image in the folder (no silent 3-image trim)
    for path in list_style_reference_images(folder)[: max(1, int(max_images))]:
        try:
            data = path.read_bytes()
            if not data:
                continue
            parts.append(types.Part.from_bytes(data=data, mime_type=_mime_for(path.name)))
            names.append(path.name)
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("VISION_AGENT | could not read %s (%s)", path, exc)

    if names:
        _LOG.info(
            "VISION_AGENT | loaded %d style_reference file(s): %s",
            len(names), ", ".join(names),
        )
        print(
            f"[DEBUG] VISION_AGENT | Gemini Vision inspecting {len(names)} image(s) from "
            f"{folder}: {names}"
        )
    else:
        print(f"[DEBUG] VISION_AGENT | WARNING — no images found in {folder}")

    with _lock:
        _parts_cache[key] = list(parts)
        _name_cache[key] = list(names)
    return parts, names


def _get_gemini_client():
    import config as app_config
    from agents.media.providers.gemini_utils import make_gemini_client_with_fallback

    api_key = getattr(app_config, "GEMINI_API_KEY", None) or os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY missing — cannot run Vision Agent.")
    return make_gemini_client_with_fallback(str(api_key))


def inspect_style_references(reference_folder: str | Path | None = None) -> str:
    """
    Force Gemini Vision to inspect ALL local style_reference images once per folder.
    Returns a short STYLE_OK confirmation (logged only — does not rewrite scenes).
    """
    from agents.media.providers.gemini_utils import generate_content_with_model_fallback
    from agents.media.providers.model_router import text_model

    folder = Path(reference_folder) if reference_folder else default_style_reference_folder()
    folder_key = str(folder.resolve())
    parts, names = load_reference_image_parts(folder)

    with _lock:
        already = folder_key in _inspected_folders

    if not parts:
        print("[DEBUG] VISION_AGENT | style inspect skipped — no reference images")
        return ""

    if already:
        print(
            f"[DEBUG] VISION_AGENT | style refs already inspected this run "
            f"({len(names)} files)"
        )
        return "STYLE_OK: cached"

    try:
        client = _get_gemini_client()
        route = text_model(task="research", log=True)
        chain = route.as_list() or [route.model_id]
        contents: list[Any] = list(parts) + [_STYLE_INSPECT_INSTRUCTION]
        response = generate_content_with_model_fallback(
            client, chain, contents=contents,
        )
        text = " ".join((getattr(response, "text", "") or "").split())
        print(f"[DEBUG] VISION_AGENT | style ground-truth OK | {text[:220]}")
        with _lock:
            _inspected_folders.add(folder_key)
        return text
    except Exception as exc:  # noqa: BLE001
        _LOG.warning("VISION_AGENT | style inspect failed (%s)", exc)
        print(f"[DEBUG] VISION_AGENT | style inspect failed ({exc}) — continuing with canonical templates")
        with _lock:
            _inspected_folders.add(folder_key)
        return ""


def compile_vision_flux_prompt(
    *,
    narrative_beat: str,
    concept_key: str,
    matrix_scene: str,
    reference_folder: str | Path | None = None,
    style_anchor: str | None = None,
) -> str:
    """
    Inspect local style references, then emit the CANONICAL template.

    ``narrative_beat`` / ``matrix_scene`` are ignored for scene content — they must not
    soften the prompt into gym/stock photography.
    """
    del narrative_beat, matrix_scene, style_anchor
    from agents.media.visual_compiler import get_canonical_prompt

    folder = Path(reference_folder) if reference_folder else default_style_reference_folder()
    inspect_style_references(folder)
    prompt = get_canonical_prompt(concept_key)
    print(
        f"[DEBUG] VISION_AGENT | canonical template locked | concept={concept_key} | "
        f"{len(prompt.split())} words"
    )
    return prompt


def compile_vision_prompts_batch(
    beats: Sequence[tuple[str, str, str]],
    *,
    reference_folder: str | Path | None = None,
    style_anchor: str | None = None,
) -> list[str]:
    folder = Path(reference_folder) if reference_folder else default_style_reference_folder()
    inspect_style_references(folder)
    return [
        compile_vision_flux_prompt(
            narrative_beat=beat,
            concept_key=concept_key,
            matrix_scene=matrix_scene,
            reference_folder=folder,
            style_anchor=style_anchor,
        )
        for concept_key, beat, matrix_scene in beats
    ]

# -*- coding: utf-8 -*-
"""
Gemini Vision style reader — extracts biomechanical style DNA from
``pages_config/<page>/style_reference/`` and builds FLUX prompts with
hardcore 80s practical body horror (H.R. Giger), gaunt scarred industrial
slaves, full torso coverage, and visceral tech-into-flesh integration.
"""
from __future__ import annotations

import logging
import os
import re
import threading
from pathlib import Path
from typing import Any

_LOG = logging.getLogger(__name__)

_IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp")
# Bump when vision instructions change so cached DNA is invalidated.
_DNA_INSTRUCTION_VERSION: str = "v8-vision-driven-flux"

_FALLBACK_STYLE_ANCHOR: str = (
    "dark 80s dystopian cyberpunk, brutalist concrete architecture, ancient stone "
    "monoliths, dark muted cinematic tones, heavy ash rain, rusted iron, monochrome "
    "CRT monitor walls, high contrast cinematic lighting, 35mm film grain, "
    "8k photorealistic shot"
)

_ANALYSIS_CONSTRAINTS: str = (
    "Analyze these reference images to extract the exact style rules. "
    "Focus on: human flesh MERGED with machinery — wires/copper/hydraulic tubes "
    "actively piercing through skin and muscle with raw scarred flesh at entry points; "
    "microchips and steel plates crudely bolted into bone/skulls/foreheads; "
    "dark 80s biomechanical cyberpunk, practical body horror FX, dirty raw corroded "
    "metal textures, heavy 35mm film grain, flickering crimson neon rim lighting. "
    "Subjects: weathered, gaunt, scarred, exhausted, biomechanically integrated. "
    "Vary tech mods — do NOT put identical VR goggles on every face."
)

_STYLE_DNA_INSTRUCTION: str = (
    f"{_ANALYSIS_CONSTRAINTS} "
    "Output ONLY a dense English visual style anchor (80-140 words, comma-separated "
    "phrases, no markdown, no preamble) matching the reference photos while obeying "
    "the BAN rules above."
)

_BEAT_VISION_INSTRUCTION: str = (
    "Translate the SCRIPT BEAT into ONE concrete subject-and-action phrase for an "
    "80s biomechanical body-horror FLUX prompt. "
    "Output 20–35 English words ONLY — no markdown, no preamble, no bans, no NEVER/NO lists. "
    "Describe positive concrete biomechanical action only "
    "(e.g. young man with copper conduits forced into throat in a CRT dungeon). "
    "Do NOT mention rubber aprons, chestplates, or tactical armor."
)

_HARDCORE_TAIL: str = (
    "dark 80s dystopian cyberpunk, brutalist concrete architecture, ancient stone "
    "monoliths, dark muted cinematic tones, heavy ash rain, rusted iron, monochrome "
    "CRT monitor walls, high contrast cinematic lighting, 35mm film grain, "
    "8k photorealistic shot"
)

# Vision models sometimes enumerate bans WITHOUT "no/never" — FLUX then paints them.
_BAN_LEAK_PHRASES: tuple[str, ...] = (
    "clean skin",
    "handsome symmetrical faces",
    "handsome actors",
    "fitness models",
    "gym-bros",
    "gym bros",
    "underwear-ad aesthetics",
    "underwear aesthetics",
    "commercial underwear aesthetics",
    "bare chests",
    "commercial looks",
    "modern clean clothes",
    "symmetrical clean faces",
)


def _neutralize_ban_leaks(text: str) -> str:
    """Prefix accidental positive ban-list tokens with NO so FLUX does not paint them."""
    import re

    out = text or ""
    for phrase in sorted(_BAN_LEAK_PHRASES, key=len, reverse=True):
        pattern = re.compile(
            rf"(?<![A-Za-z]){re.escape(phrase)}(?![A-Za-z])",
            re.IGNORECASE,
        )
        pieces: list[str] = []
        last = 0
        for m in pattern.finditer(out):
            window = out[max(0, m.start() - 18):m.start()].lower()
            if any(
                tok in window
                for tok in ("no ", "never ", "ban ", "banned", "avoid", "devoid", "zero ")
            ):
                pieces.append(out[last:m.end()])
            else:
                pieces.append(out[last:m.start()])
                pieces.append(f"NO {phrase}")
            last = m.end()
        pieces.append(out[last:])
        out = "".join(pieces)
    return out

# Process-wide caches
_lock = threading.Lock()
_dna_cache: dict[str, str] = {}
_parts_cache: dict[str, list[Any]] = {}


def default_master_mei_ref_folder(engine_root: "str | Path | None" = None) -> Path:
    """Canonical Master Mei style_reference directory."""
    if engine_root is None:
        try:
            import config as app_config
            root = Path(app_config.ENGINE_ROOT)
        except Exception:
            root = Path(__file__).resolve().parents[1]
    else:
        root = Path(engine_root)
    return (root / "pages_config" / "master_mei" / "style_reference").resolve()


def _mime_for(filename: str) -> str:
    lo = filename.lower()
    if lo.endswith(".png"):
        return "image/png"
    if lo.endswith(".webp"):
        return "image/webp"
    return "image/jpeg"


def _folder_cache_key(folder: Path) -> str:
    """Cache key includes folder path + newest mtime + instruction version."""
    try:
        mtimes = [
            p.stat().st_mtime_ns
            for p in folder.iterdir()
            if p.is_file() and p.suffix.lower() in _IMAGE_EXTENSIONS
        ]
        stamp = max(mtimes) if mtimes else 0
    except Exception:
        stamp = 0
    return f"{folder.resolve()}::{stamp}::{_DNA_INSTRUCTION_VERSION}"


def list_style_reference_images(reference_folder_path: str | Path) -> list[Path]:
    folder = Path(reference_folder_path)
    if not folder.is_dir():
        return []
    found = [
        p for p in sorted(folder.iterdir(), key=lambda x: x.name.lower())
        if p.is_file() and p.suffix.lower() in _IMAGE_EXTENSIONS
    ]
    return found


def _load_image_parts(reference_folder_path: str | Path) -> list[Any]:
    """Load style reference images as google-genai Part objects (cached)."""
    from google.genai import types

    folder = Path(reference_folder_path)
    key = _folder_cache_key(folder)
    with _lock:
        if key in _parts_cache:
            return list(_parts_cache[key])

    parts: list[Any] = []
    names: list[str] = []
    for path in list_style_reference_images(folder):
        try:
            data = path.read_bytes()
            if not data:
                continue
            parts.append(types.Part.from_bytes(data=data, mime_type=_mime_for(path.name)))
            names.append(path.name)
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("STYLE_READER | could not read %s (%s)", path, exc)

    if names:
        _LOG.info(
            "STYLE_READER | Gemini Vision loaded %d style_reference file(s): %s",
            len(names), ", ".join(names),
        )
        print(
            f"[DEBUG] STYLE_READER | Gemini Vision processing {len(names)} file(s): "
            f"{names}"
        )

    with _lock:
        _parts_cache[key] = list(parts)
    return parts


def _get_gemini_client():
    import config as app_config
    from avatar_engine.providers.gemini_utils import make_gemini_client_with_fallback

    api_key = getattr(app_config, "GEMINI_API_KEY", None) or os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY missing — cannot run style_reader vision.")
    return make_gemini_client_with_fallback(str(api_key))


def extract_style_dna_from_references(
    reference_folder_path: str | Path,
    *,
    force_refresh: bool = False,
) -> str:
    """
    Send style_reference images ONCE to Gemini Vision and cache a dense style DNA
    string. Falls back to ``_FALLBACK_STYLE_ANCHOR`` on any failure.
    """
    folder = Path(reference_folder_path)
    key = _folder_cache_key(folder)
    with _lock:
        if not force_refresh and key in _dna_cache:
            return _dna_cache[key]

    parts = _load_image_parts(folder)
    if not parts:
        _LOG.warning(
            "STYLE_READER | no images in %s — using fallback style DNA", folder
        )
        with _lock:
            _dna_cache[key] = _FALLBACK_STYLE_ANCHOR
        return _FALLBACK_STYLE_ANCHOR

    try:
        from avatar_engine.providers.gemini_utils import (
            generate_content_with_model_fallback,
        )
        from avatar_engine.providers.model_router import text_model

        client = _get_gemini_client()
        route = text_model(task="research", log=True)
        chain = route.as_list() or [route.model_id]

        contents = list(parts) + [_STYLE_DNA_INSTRUCTION]
        response = generate_content_with_model_fallback(
            client, chain, contents=contents,
        )
        text = (getattr(response, "text", "") or "").strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text.lower().startswith("text"):
                text = text[4:].lstrip()
        text = " ".join(text.split())
        if not text:
            raise RuntimeError("empty vision response")

        _LOG.info(
            "STYLE_READER | style DNA extracted (%d words): %s",
            len(text.split()), text[:240],
        )
        print(
            f"[DEBUG] STYLE_READER | Gemini Vision style DNA OK "
            f"({len(text.split())} words): {text[:220]}…"
        )
        with _lock:
            _dna_cache[key] = text
        return text
    except Exception as exc:  # noqa: BLE001
        _LOG.warning(
            "STYLE_READER | vision DNA extraction failed (%s) — using fallback", exc
        )
        print(f"[DEBUG] STYLE_READER | vision failed ({exc}) — using fallback DNA")
        with _lock:
            _dna_cache[key] = _FALLBACK_STYLE_ANCHOR
        return _FALLBACK_STYLE_ANCHOR


def generate_reference_driven_prompt(
    spoken_beat_script: str,
    concept_key: str,
    reference_folder_path: str | Path,
    *,
    per_beat_vision: bool = True,
    matrix_scene: str | None = None,
) -> str:
    """
    Vision-driven FLUX prompt: load style_reference images → Gemini Vision → prompt.

    Delegates to ``avatar_engine.vision_agent.compile_vision_flux_prompt``.
    Falls back to matrix compile if Vision is unavailable.
    """
    from avatar_engine.visual_compiler import (
        MASTER_STYLE_ANCHOR_DEFAULT,
        VISUAL_MATRIX,
        compile_flux_prompt,
        resolve_concept_key,
    )

    beat = (spoken_beat_script or "").strip()
    key = resolve_concept_key(concept_key)
    base_visual = (matrix_scene or "").strip() or (
        VISUAL_MATRIX.get(key) or VISUAL_MATRIX["tech_slavery"]
    )
    folder = Path(reference_folder_path)

    if per_beat_vision:
        try:
            from avatar_engine.vision_agent import compile_vision_flux_prompt

            return compile_vision_flux_prompt(
                narrative_beat=beat or key,
                concept_key=key,
                matrix_scene=base_visual,
                reference_folder=folder,
                style_anchor=MASTER_STYLE_ANCHOR_DEFAULT,
            )
        except Exception as exc:  # noqa: BLE001
            _LOG.warning(
                "STYLE_READER | vision_agent failed (%s) — matrix fallback", exc
            )
            print(f"[DEBUG] STYLE_READER | vision_agent failed ({exc}) — matrix fallback")

    return compile_flux_prompt(
        spoken_beat_script=beat or key,
        concept_key=key,
        style_anchor=MASTER_STYLE_ANCHOR_DEFAULT,
        shot_type="",
    )

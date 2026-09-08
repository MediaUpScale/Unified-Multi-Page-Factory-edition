# -*- coding: utf-8 -*-
"""IMAGE-TEXTS quote renderer — Module 2 of the factory pipeline.

Reads extracted quotes from ``ocr_vault.json``, loads channel-specific notebook
templates / typewriter fonts / logos, and composites realistic handwritten or
typed quote cards with Pillow.

Typical usage::

    from utils.quote_renderer import ImageTextsRenderEngine

    engine = ImageTextsRenderEngine()
    paths = engine.generate_image_texts("momma_circle", limit=5)

    # or:
    # python utils/quote_renderer.py --channel momma_circle --dataset ocr_momma_deploy --limit 1
"""
from __future__ import annotations

import argparse
import importlib
import json
import logging
import math
import os
import random
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Factory paths (same G: root as ``utils/ocr_engine.py``)
# ---------------------------------------------------------------------------

_ENGINE_ROOT: Path = Path(__file__).resolve().parents[1]
if str(_ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(_ENGINE_ROOT))

from utils.pipeline_paths import page_outputs_dir
FACTORY_ROOT: Path = Path(
    r"G:\My Drive\Z sosFiles\Z_act\@ NETWORK"
    r"\@MEDIAUPSCALE_FACTORY_DYNAMIC_CONTENT"
    r"\Unified Multi-Page Factory"
)
VAULT_PATH: Path = FACTORY_ROOT / "assets" / "ocr_vault.json"
DEFAULT_DATASET_KEY: str = "ocr_momma_deploy"

IMAGE_TEXTS_DIRNAME: str = "image_texts"
TEMPLATE_DIRNAME: str = "image_texts_templates"
IMAGE_EXTENSIONS: frozenset[str] = frozenset({
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".bmp",
    ".tif",
    ".tiff",
})
FONT_EXTENSIONS: frozenset[str] = frozenset({".ttf", ".otf"})

# Notebook safe-zone as fractions of canvas size. Leaves room for spiral
# binding on the left and a bottom footer for the channel logo.
_MARGIN_LEFT: float = 0.20
_MARGIN_RIGHT: float = 0.14
_MARGIN_TOP: float = 0.15
_MARGIN_BOTTOM: float = 0.16
_LINE_HEIGHT_RATIO: float = 1.38
_MIN_FONT_SIZE: int = 14
_MIN_READABLE_SIZE: int = 22
_DEFAULT_FONT_SIZE: int = 49
_DEFAULT_CANVAS: tuple[int, int] = (1080, 1350)
OUTPUT_SIZE: tuple[int, int] = (1080, 1350)
_LAYOUT_REF_WIDTH: int = 1080
_TEXT_OPACITY: float = 0.8
_BIG_QUOTE_WORDS: int = 24
_BIG_QUOTE_LINES: int = 7
_ROTATION_CHOICES: tuple[float, ...] = (-20.0, -15.0, -10.0, -5.0, 0.0, 5.0, 10.0, 15.0, 20.0)
_ROTATION_CHOICES_BIG: tuple[float, ...] = (-15.0, -10.0, -5.0, 0.0, 5.0, 10.0, 15.0)
_CROP_KEEP: float = 1.0
_ROTATION_SAFE_PAD: float = 0.04
_BIG_QUOTE_HEIGHT_FILL: float = 0.66
_BIG_QUOTE_SIDE_INSET: float = 0.06
_HEART_CHARS: frozenset[str] = frozenset("♡❤♥💕💗❥")
_HEART_SENTINEL: str = "\u0001"
_GLYPH_FALLBACKS: dict[str, str] = {
    "—": "-",
    "–": "-",
    "“": '"',
    "”": '"',
    "‘": "'",
    "’": "'",
}


class ImageTextsAssetError(FileNotFoundError):
    """Raised when a required IMAGE-TEXTS asset cannot be resolved."""


@dataclass(frozen=True)
class ChannelAssets:
    channel_name: str
    channel_dir: Path
    image_texts_dir: Path
    templates_dir: Path
    templates: tuple[Path, ...]
    font_path: Path | None
    logo_path: Path | None
    output_dir: Path


@dataclass(frozen=True)
class TemplatePage:
    image: Image.Image
    text_left: int | None = None
    first_line_y: int | None = None
    pitch: int | None = None
    text_box: tuple[int, int, int, int] | None = None


# ---------------------------------------------------------------------------
# Path / vault helpers
# ---------------------------------------------------------------------------

def _candidate_factory_roots(explicit: Path | None = None) -> list[Path]:
    roots: list[Path] = []
    if explicit is not None:
        roots.append(Path(explicit))
    # Prefer the local repo channels_config (C:) so channel assets like
    # momma_circle/image_texts_templates are found before the G: factory copy.
    roots.extend((_ENGINE_ROOT, FACTORY_ROOT))
    seen: set[Path] = set()
    unique: list[Path] = []
    for root in roots:
        try:
            key = root.resolve()
        except OSError:
            key = root
        if key in seen:
            continue
        seen.add(key)
        unique.append(root)
    return unique


def _first_existing_dir(paths: Sequence[Path]) -> Path | None:
    for path in paths:
        if path.is_dir():
            return path
    return None


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", (value or "").strip())
    return cleaned.strip("._") or "quote"


def _parse_rgb(raw: str | Sequence[int]) -> tuple[int, int, int]:
    if isinstance(raw, str):
        parts = [part.strip() for part in raw.replace(" ", "").split(",") if part.strip()]
        if len(parts) != 3:
            raise ValueError(f"text color must be R,G,B — got {raw!r}")
        values = [int(part) for part in parts]
    else:
        values = [int(part) for part in raw]
        if len(values) != 3:
            raise ValueError(f"text color must have 3 channels — got {raw!r}")
    if any(channel < 0 or channel > 255 for channel in values):
        raise ValueError(f"text color channels must be 0-255 — got {values}")
    return values[0], values[1], values[2]


def _load_vault(vault_path: Path) -> dict[str, Any]:
    if not vault_path.is_file():
        raise FileNotFoundError(f"OCR vault not found: {vault_path}")
    try:
        payload = json.loads(vault_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Corrupt OCR vault JSON at {vault_path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"OCR vault root must be a JSON object: {vault_path}")
    return payload


def _coerce_items(raw: Any) -> dict[str, dict[str, Any]]:
    if isinstance(raw, Mapping):
        items: dict[str, dict[str, Any]] = {}
        for key, value in raw.items():
            if isinstance(value, Mapping):
                items[str(key)] = dict(value)
        return items
    if isinstance(raw, list):
        items = {}
        for index, value in enumerate(raw, start=1):
            if not isinstance(value, Mapping):
                continue
            file_path = str(value.get("file_path") or "")
            key = Path(file_path).name if file_path else f"item_{index}"
            items[key] = dict(value)
        return items
    return {}


def _iter_quote_entries(dataset: Mapping[str, Any]) -> list[tuple[str, str]]:
    items = _coerce_items(dataset.get("items") if "items" in dataset else dataset)
    quotes: list[tuple[str, str]] = []
    for key, item in items.items():
        text = str(item.get("extracted_text") or item.get("text") or "").strip()
        if not text:
            continue
        status = str(item.get("status") or "success").strip().lower()
        if status and status not in {"success", "ok", "cached"}:
            logger.info("IMAGE-TEXTS | skip %s (status=%s)", key, status)
            continue
        quotes.append((key, text))
    return quotes


def _list_images(folder: Path) -> list[Path]:
    if not folder.is_dir():
        return []
    found = [
        path
        for path in folder.iterdir()
        if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
    ]
    return sorted(found, key=lambda path: path.name.lower())


def _list_fonts(folder: Path) -> list[Path]:
    if not folder.is_dir():
        return []
    found: list[Path] = []
    for path in folder.rglob("*"):
        if path.is_file() and path.suffix.lower() in FONT_EXTENSIONS:
            found.append(path)
    return sorted(found, key=lambda path: (len(path.parts), path.name.lower()))


# ---------------------------------------------------------------------------
# Typography
# ---------------------------------------------------------------------------

def _measure_text(font: ImageFont.FreeTypeFont | ImageFont.ImageFont, text: str) -> tuple[int, int]:
    dummy = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    bbox = dummy.textbbox((0, 0), text, font=font)
    return max(0, bbox[2] - bbox[0]), max(0, bbox[3] - bbox[1])


def _glyph_supported(font: ImageFont.FreeTypeFont | ImageFont.ImageFont, char: str) -> bool:
    if not char or char.isspace():
        return True
    try:
        width, height = _measure_text(font, char)
        return width > 0 and height > 0
    except Exception:  # noqa: BLE001
        return False


def _sanitize_quote_text(
    text: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
) -> str:
    cleaned = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    out: list[str] = []
    for char in cleaned:
        if char in _HEART_CHARS:
            out.append(_HEART_SENTINEL)
            continue
        candidate = _GLYPH_FALLBACKS.get(char, char)
        if candidate.isascii() or _glyph_supported(font, candidate):
            out.append(candidate)
            continue
        if char.isascii():
            out.append(char)
    return "".join(out).strip()


def _wrap_line(
    text: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    max_width: int,
) -> list[str]:
    raw = text.strip()
    if not raw:
        return [""]
    words = raw.split()
    if not words:
        return [""]
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        trial = f"{current} {word}"
        width, _ = _measure_text(font, trial)
        if width <= max_width:
            current = trial
            continue
        lines.append(current)
        current = word
        word_width, _ = _measure_text(font, current)
        if word_width > max_width:
            # Hard-split an oversized token so it cannot overflow the page.
            chunk = ""
            for char in current:
                probe = f"{chunk}{char}"
                probe_w, _ = _measure_text(font, probe)
                if chunk and probe_w > max_width:
                    lines.append(chunk)
                    chunk = char
                else:
                    chunk = probe
            current = chunk
    if current:
        lines.append(current)
    return lines


def wrap_quote(
    text: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    max_width: int,
) -> list[str]:
    """Wrap *text* to *max_width* pixels, preserving author-supplied newlines."""
    lines: list[str] = []
    for paragraph in text.split("\n"):
        if not paragraph.strip():
            lines.append("")
            continue
        lines.extend(_wrap_line(paragraph, font, max_width))
    while lines and lines[0] == "":
        lines.pop(0)
    while lines and lines[-1] == "":
        lines.pop()
    return lines or [""]


def _flatten_quote(text: str) -> str:
    return " ".join((text or "").replace("\r\n", "\n").split())


def _merge_orphan_lines(
    lines: Sequence[str],
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    max_width: int,
) -> list[str]:
    """Pull single-word leftover lines into a neighbor when they fit."""
    merged: list[str] = []
    index = 0
    items = list(lines)
    while index < len(items):
        line = items[index]
        words = line.split()
        if len(words) == 1 and merged:
            trial = f"{merged[-1]} {line}".strip()
            if _measure_text(font, trial)[0] <= max_width:
                merged[-1] = trial
                index += 1
                continue
        if len(words) == 1 and index + 1 < len(items):
            trial = f"{line} {items[index + 1]}".strip()
            if _measure_text(font, trial)[0] <= max_width:
                merged.append(trial)
                index += 2
                continue
        merged.append(line)
        index += 1
    return merged


def wrap_quote_balanced(
    text: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    max_width: int,
) -> list[str]:
    """Flatten OCR line breaks, wrap, and even out leftover stub lines."""
    flat = _flatten_quote(text)
    if not flat:
        return [""]
    greedy = _merge_orphan_lines(_wrap_line(flat, font, max_width), font, max_width)
    if len(greedy) <= 1:
        return greedy
    last_w = _measure_text(font, greedy[-1])[0]
    longest = max(_measure_text(font, line)[0] for line in greedy)
    if last_w >= int(longest * 0.55) and all(len(line.split()) >= 2 for line in greedy):
        return greedy
    low, high = max(40, max_width // 2), max_width
    best = greedy
    for _ in range(14):
        mid = (low + high) // 2
        trial = _merge_orphan_lines(_wrap_line(flat, font, mid), font, max_width)
        if len(trial) <= len(greedy) + 1:
            best = trial
            high = mid
        else:
            low = mid + 1
        if low >= high:
            break
    return _merge_orphan_lines(best, font, max_width)


def _block_height(lines: Sequence[str], line_height: int) -> int:
    if not lines:
        return 0
    return len(lines) * line_height


def _quote_overflows(
    lines: Sequence[str],
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    max_width: int,
    max_height: int,
    line_height: int,
) -> bool:
    if _block_height(lines, line_height) > max_height:
        return True
    heart = max(16, int(line_height * 0.55))
    return any(_line_pixel_width(line, font, heart) > max_width for line in lines if line)


def _fit_wrapped_text(
    text: str,
    font_path: Path | None,
    font_size: int,
    max_width: int,
    max_height: int,
    line_height_ratio: float,
) -> tuple[ImageFont.FreeTypeFont | ImageFont.ImageFont, list[str], int, int]:
    size = max(_MIN_FONT_SIZE, int(font_size))
    chosen_font = _load_font(font_path, size)
    lines = wrap_quote_balanced(text, chosen_font, max_width)
    line_height = max(1, int(round(size * line_height_ratio)))
    floor = _MIN_READABLE_SIZE if size > _MIN_READABLE_SIZE else _MIN_FONT_SIZE
    while size > floor and _quote_overflows(
        lines, chosen_font, max_width, max_height, line_height
    ):
        size -= 1
        chosen_font = _load_font(font_path, size)
        lines = wrap_quote_balanced(text, chosen_font, max_width)
        line_height = max(1, int(round(size * line_height_ratio)))
    while size > _MIN_FONT_SIZE and _quote_overflows(
        lines, chosen_font, max_width, max_height, line_height
    ):
        size -= 1
        chosen_font = _load_font(font_path, size)
        lines = wrap_quote_balanced(text, chosen_font, max_width)
        line_height = max(1, int(round(size * line_height_ratio)))
    return chosen_font, lines, size, line_height


def _quote_word_count(text: str) -> int:
    return len(_flatten_quote(text).split())


def _is_big_quote(text: str, line_count: int | None = None) -> bool:
    if _quote_word_count(text) >= _BIG_QUOTE_WORDS:
        return True
    return line_count is not None and line_count >= _BIG_QUOTE_LINES


def _rotation_choices_for_quote(text: str, line_count: int | None = None) -> tuple[float, ...]:
    if _is_big_quote(text, line_count):
        return _ROTATION_CHOICES_BIG
    return _ROTATION_CHOICES


def _length_font_scale(text: str) -> float:
    """Comfort size for long copy: readable, not page-filling, never tiny."""
    words = _quote_word_count(text)
    if words >= 55:
        return 0.68
    if words >= 40:
        return 0.74
    if words >= _BIG_QUOTE_WORDS:
        return 0.82
    return 1.0


# ---------------------------------------------------------------------------
# Asset discovery
# ---------------------------------------------------------------------------

def _discover_templates_dir(channel_dir: Path, image_texts_dir: Path) -> Path:
    """Resolve ``image_texts_templates/`` for a channel.

    Preferred location (momma_circle and matching channels)::
        {channel}/image_texts_templates/

    Nested fallback::
        {channel}/image_texts/image_texts_templates/
    """
    sibling = channel_dir / TEMPLATE_DIRNAME
    nested = image_texts_dir / TEMPLATE_DIRNAME
    if _list_images(sibling):
        return sibling
    if _list_images(nested):
        return nested
    return sibling


def _factory_font_fallbacks(factory_roots: Sequence[Path]) -> list[Path]:
    relative = (
        Path("Fonts") / "MoreSugar" / "MoreSugar-Thin.ttf",
        Path("Fonts") / "MoreSugar" / "MoreSugar-Thin.otf",
        Path("Fonts") / "EduNSW" / "EduNSWACTFoundation-Regular.ttf",
        Path("Fonts") / "Caveat" / "Caveat-VariableFont_wght.ttf",
        Path("Fonts") / "PlaywriteNZBasic" / "PlaywriteNZBasic-VariableFont_wght.ttf",
        Path("Fonts") / "ComicSans" / "ComicSansMS.ttf",
        Path("Fonts") / "Lora" / "Lora-Italic.ttf",
    )
    found: list[Path] = []
    for root in factory_roots:
        for rel in relative:
            path = root / rel
            if path.is_file():
                found.append(path)
    windir = Path(r"C:\Windows\Fonts")
    for name in ("cour.ttf", "courbd.ttf", "comic.ttf", "georgia.ttf", "arial.ttf"):
        path = windir / name
        if path.is_file():
            found.append(path)
    return found


def _load_font(font_path: Path | None, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    if font_path is not None:
        try:
            return ImageFont.truetype(str(font_path), size=size)
        except OSError as exc:
            logger.warning("IMAGE-TEXTS | failed to load font %s (%s)", font_path, exc)
    try:
        return ImageFont.truetype("cour.ttf", size=size)
    except OSError:
        return ImageFont.load_default()


def resolve_channel_assets(
    channel_name: str,
    *,
    factory_root: Path | None = None,
    channels_config_root: Path | None = None,
    output_dir: Path | None = None,
) -> ChannelAssets:
    slug = (channel_name or "").strip()
    if not slug:
        raise ValueError("channel_name is required")

    roots = _candidate_factory_roots(factory_root)
    channel_candidates: list[Path] = []
    if channels_config_root is not None:
        channel_candidates.append(Path(channels_config_root) / slug)
    for root in roots:
        channel_candidates.append(root / "channels_config" / slug)

    channel_dir = _first_existing_dir(channel_candidates)
    if channel_dir is None:
        expected = channel_candidates[0] if channel_candidates else Path("channels_config") / slug
        raise ImageTextsAssetError(
            f"Channel config directory not found for '{slug}'. Looked in: "
            + ", ".join(str(path) for path in channel_candidates)
            if channel_candidates
            else f"Channel config directory not found for '{slug}': {expected}"
        )

    image_texts_dir = channel_dir / IMAGE_TEXTS_DIRNAME
    templates_dir = _discover_templates_dir(channel_dir, image_texts_dir)
    templates = tuple(_list_images(templates_dir))

    font_path: Path | None = None
    channel_fonts = [
        path
        for path in (*_list_fonts(image_texts_dir), *_list_fonts(channel_dir))
        if TEMPLATE_DIRNAME not in path.parts
    ]
    # Prefer a font sitting directly in image_texts/, then any other channel font.
    channel_fonts.sort(key=lambda path: (0 if path.parent == image_texts_dir else 1, path.name.lower()))
    if channel_fonts:
        font_path = channel_fonts[0]
    else:
        fallbacks = _factory_font_fallbacks(roots)
        if fallbacks:
            font_path = fallbacks[0]
            logger.warning(
                "IMAGE-TEXTS | no .ttf in %s — falling back to %s",
                image_texts_dir,
                font_path,
            )
        else:
            logger.warning(
                "IMAGE-TEXTS | no channel or factory font found under %s; "
                "Pillow default bitmap font will be used",
                image_texts_dir,
            )

    logo_candidates = (
        image_texts_dir / "logo.png",
        channel_dir / "logo" / "logo.png",
        channel_dir / "logo.png",
    )
    logo_path = next((path for path in logo_candidates if path.is_file()), None)

    resolved_output = (
        Path(output_dir)
        if output_dir is not None
        else page_outputs_dir(slug) / IMAGE_TEXTS_DIRNAME
    )
    return ChannelAssets(
        channel_name=slug,
        channel_dir=channel_dir,
        image_texts_dir=image_texts_dir,
        templates_dir=templates_dir,
        templates=templates,
        font_path=font_path,
        logo_path=logo_path,
        output_dir=resolved_output,
    )


# ---------------------------------------------------------------------------
# Notebook fallback + compositing
# ---------------------------------------------------------------------------

def generate_notebook_template(
    size: tuple[int, int] = _DEFAULT_CANVAS,
    *,
    variant: int = 0,
) -> Image.Image:
    """Synthesize a lined spiral-notebook page when no photo templates exist."""
    return _build_notebook_page(size, variant=variant).image


def _build_notebook_page(
    size: tuple[int, int] = _DEFAULT_CANVAS,
    *,
    variant: int = 0,
) -> TemplatePage:
    width, height = size
    papers = (
        (247, 241, 228),
        (238, 234, 226),
        (252, 247, 236),
        (234, 228, 218),
    )
    line_colors = (
        (186, 200, 214),
        (206, 196, 186),
        (176, 192, 208),
        (198, 188, 176),
    )
    paper = papers[variant % len(papers)]
    line = line_colors[variant % len(line_colors)]
    canvas = Image.new("RGB", (width, height), paper)
    try:
        grain = Image.effect_noise(size, 22).convert("RGB")
        canvas = Image.blend(canvas, grain, 0.07)
    except Exception:  # noqa: BLE001
        pass
    canvas = ImageEnhance.Color(canvas).enhance(1.02)

    draw = ImageDraw.Draw(canvas)
    first_line_y = int(height * 0.12)
    pitch = max(28, int(round(height / 34)))
    for y in range(first_line_y, height - int(height * 0.06), pitch):
        draw.line([(int(width * 0.07), y), (width - int(width * 0.05), y)], fill=line, width=2)

    margin_x = int(width * 0.13)
    draw.line(
        [(margin_x, int(height * 0.07)), (margin_x, height - int(height * 0.04))],
        fill=(196, 92, 92),
        width=3,
    )

    if variant % 2 == 0:
        ring_x = int(width * 0.045)
        ring_r = max(10, int(width * 0.016))
        for cy in range(int(height * 0.08), height - int(height * 0.06), pitch * 2):
            box = (ring_x - ring_r, cy - ring_r, ring_x + ring_r, cy + ring_r)
            draw.ellipse(box, outline=(92, 96, 102), width=4)
            hole = (ring_x - 3, cy - 3, ring_x + 3, cy + 3)
            draw.ellipse(hole, fill=(168, 160, 148))

    textured = ImageEnhance.Contrast(canvas).enhance(1.03).filter(ImageFilter.SMOOTH)
    return TemplatePage(
        image=textured,
        text_left=margin_x + max(14, int(width * 0.02)),
        first_line_y=first_line_y + 4,
        pitch=pitch,
    )


def _pick_template(
    templates: Sequence[Path],
    index: int,
    *,
    mode: str,
    rng: random.Random,
) -> Path | None:
    if not templates:
        return None
    if mode == "random":
        return rng.choice(list(templates))
    return templates[index % len(templates)]


def _estimate_paper_box(image: Image.Image) -> tuple[int, int, int, int] | None:
    """Find the cream notebook page inside a photographed template."""
    probe_w, probe_h = 90, 120
    small = image.convert("RGB").resize((probe_w, probe_h), Image.Resampling.BILINEAR)
    pixels = small.load()
    hits_x: list[int] = []
    hits_y: list[int] = []
    for y in range(probe_h):
        for x in range(probe_w):
            red, green, blue = pixels[x, y]
            lum = (red + green + blue) / 3.0
            sat = max(red, green, blue) - min(red, green, blue)
            if lum >= 188 and sat <= 60 and red >= blue - 8:
                hits_x.append(x)
                hits_y.append(y)
    if len(hits_x) < 40:
        return None
    width, height = image.size
    left = int(min(hits_x) / probe_w * width)
    right = int((max(hits_x) + 1) / probe_w * width)
    top = int(min(hits_y) / probe_h * height)
    bottom = int((max(hits_y) + 1) / probe_h * height)
    # Inset past the spiral binding and keep a footer for the logo.
    pad_l = max(36, int((right - left) * 0.20))
    pad_r = max(28, int((right - left) * 0.14))
    pad_t = max(18, int((bottom - top) * 0.09))
    pad_b = max(26, int((bottom - top) * 0.11))
    box = (left + pad_l, top + pad_t, right - pad_r, bottom - pad_b)
    if box[2] - box[0] < 80 or box[3] - box[1] < 80:
        return None
    return box


def _rotation_safe_box(
    width: int,
    height: int,
    max_angle: float = 20.0,
) -> tuple[int, int, int, int]:
    """Keep type inside the area that survives the heaviest tilt crop."""
    ins_w, ins_h = _inscribed_crop_size(width, height, max_angle)
    crop_w = int(round(width - _CROP_KEEP * (width - min(ins_w, width))))
    crop_h = int(round(height - _CROP_KEEP * (height - min(ins_h, height))))
    pad_x = max(0, (width - crop_w) // 2) + max(12, int(width * _ROTATION_SAFE_PAD))
    pad_y = max(0, (height - crop_h) // 2) + max(10, int(height * _ROTATION_SAFE_PAD))
    return pad_x, pad_y, width - pad_x, height - pad_y


def _intersect_boxes(
    first: tuple[int, int, int, int],
    second: tuple[int, int, int, int],
) -> tuple[int, int, int, int]:
    left = max(first[0], second[0])
    top = max(first[1], second[1])
    right = min(first[2], second[2])
    bottom = min(first[3], second[3])
    if right - left < 80 or bottom - top < 80:
        return first
    return left, top, right, bottom


def _text_box_for_page(
    page: TemplatePage,
    width: int,
    height: int,
    *,
    max_angle: float = 20.0,
    extra_inset: float = 0.0,
) -> tuple[int, int, int, int]:
    if page.text_box is not None:
        paper = page.text_box
    else:
        left = page.text_left if page.text_left is not None else int(width * _MARGIN_LEFT)
        right = width - int(width * _MARGIN_RIGHT)
        top = page.first_line_y if page.first_line_y is not None else int(height * _MARGIN_TOP)
        bottom = height - int(height * _MARGIN_BOTTOM)
        paper = (left, top, right, bottom)
    box = _intersect_boxes(paper, _rotation_safe_box(width, height, max_angle))
    if extra_inset <= 0:
        return box
    inset_x = max(8, int((box[2] - box[0]) * extra_inset))
    left, top, right, bottom = box
    left += inset_x
    right -= inset_x
    if right - left < 80:
        return box
    return left, top, right, bottom


def _open_template(path: Path | None, variant: int) -> TemplatePage:
    if path is None:
        return _build_notebook_page(variant=variant)
    try:
        with Image.open(path) as handle:
            image = handle.convert("RGB")
    except OSError as exc:
        logger.warning(
            "IMAGE-TEXTS | template unreadable (%s): %s — using generated notebook",
            path,
            exc,
        )
        return _build_notebook_page(variant=variant)
    return TemplatePage(image=image, text_box=_estimate_paper_box(image))


def _logo_needs_ink_recolor(logo: Image.Image) -> bool:
    """True when the mark is mostly light ink (invisible on cream paper)."""
    samples = list(logo.getdata())
    solid = [(r, g, b, a) for r, g, b, a in samples if a >= 180]
    if len(solid) < 12:
        return False
    mean_lum = sum((r + g + b) / 3.0 for r, g, b, _a in solid) / len(solid)
    return mean_lum >= 160.0


def _prepare_logo(
    logo_path: Path,
    target_width: int,
    opacity: float,
    *,
    ink: tuple[int, int, int] = (30, 30, 30),
) -> Image.Image:
    logo = Image.open(logo_path).convert("RGBA")
    width = max(1, int(target_width))
    if logo.width != width:
        height = max(1, int(round(logo.height * (width / max(logo.width, 1)))))
        logo = logo.resize((width, height), Image.Resampling.LANCZOS)
    if _logo_needs_ink_recolor(logo):
        alpha = logo.getchannel("A")
        recolored = Image.new("RGBA", logo.size, (*ink, 0))
        recolored.putalpha(alpha)
        logo = recolored
    if opacity < 0.999:
        red, green, blue, alpha = logo.split()
        alpha = alpha.point(lambda px: int(px * max(0.0, min(1.0, opacity))))
        logo = Image.merge("RGBA", (red, green, blue, alpha))
    return logo


def _paste_logo(
    canvas: Image.Image,
    logo: Image.Image,
    *,
    position: str = "bottom_right",
    paper_box: tuple[int, int, int, int] | None = None,
) -> Image.Image:
    page = canvas.convert("RGBA")
    width, height = page.size
    lw, lh = logo.size
    if paper_box is not None:
        left, top, right, bottom = paper_box
        margin_x = max(10, int((right - left) * 0.04))
        margin_y = max(10, int((bottom - top) * 0.03))
        region = {
            "bottom_right": (right - lw - margin_x, bottom - lh - margin_y),
            "bottom_left": (left + margin_x, bottom - lh - margin_y),
            "bottom_center": (left + ((right - left) - lw) // 2, bottom - lh - margin_y),
            "top_right": (right - lw - margin_x, top + margin_y),
        }
    else:
        margin_x = max(18, int(width * 0.06))
        margin_y = max(16, int(height * 0.045))
        region = {
            "bottom_right": (width - lw - margin_x, height - lh - margin_y),
            "bottom_left": (margin_x, height - lh - margin_y),
            "bottom_center": ((width - lw) // 2, height - lh - margin_y),
            "top_right": (width - lw - margin_x, margin_y),
        }
    x, y = region.get(position, region["bottom_right"])
    page.paste(logo, (x, y), logo)
    return page


def _draw_heart(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    size: int,
    color: tuple[int, int, int],
) -> None:
    x, y = xy
    s = max(10, int(size))
    left = [
        (x + s * 0.50, y + s * 0.92),
        (x + s * 0.08, y + s * 0.48),
        (x + s * 0.10, y + s * 0.22),
        (x + s * 0.32, y + s * 0.08),
        (x + s * 0.50, y + s * 0.24),
    ]
    right = [
        (x + s * 0.50, y + s * 0.24),
        (x + s * 0.68, y + s * 0.08),
        (x + s * 0.90, y + s * 0.22),
        (x + s * 0.92, y + s * 0.48),
        (x + s * 0.50, y + s * 0.92),
    ]
    draw.polygon(left + right[1:], fill=(*color, 255))


def _line_pixel_width(
    line: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    heart_size: int,
) -> int:
    if _HEART_SENTINEL not in line:
        return _measure_text(font, line)[0]
    width = 0
    buffer = ""
    for char in line:
        if char != _HEART_SENTINEL:
            buffer += char
            continue
        if buffer:
            width += _measure_text(font, buffer)[0]
            buffer = ""
        width += heart_size + 12
    if buffer:
        width += _measure_text(font, buffer)[0]
    return width


def _draw_text_layer(
    size: tuple[int, int],
    lines: Sequence[str],
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    *,
    color: tuple[int, int, int],
    origin: tuple[int, int],
    line_height: int,
    rng: random.Random,
    jitter_px: int = 2,
    align: str = "center",
    box_width: int | None = None,
) -> Image.Image:
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    x0, y0 = origin
    heart_size = max(16, int(line_height * 0.55))
    for index, line in enumerate(lines):
        if not line:
            continue
        jitter = rng.randint(-jitter_px, jitter_px) if jitter_px else 0
        x = x0 + jitter
        if align == "center" and box_width:
            line_w = _line_pixel_width(line, font, heart_size)
            x = x0 + max(0, (box_width - line_w) // 2) + jitter
        y = y0 + index * line_height
        if _HEART_SENTINEL not in line:
            draw.text((x, y), line, font=font, fill=(*color, 255))
            continue
        cursor = x
        buffer = ""
        for char in line:
            if char != _HEART_SENTINEL:
                buffer += char
                continue
            if buffer:
                draw.text((cursor, y), buffer, font=font, fill=(*color, 255))
                cursor += _measure_text(font, buffer)[0]
                buffer = ""
            _draw_heart(draw, (cursor + 4, y + 4), heart_size, color)
            cursor += heart_size + 8
        if buffer:
            draw.text((cursor, y), buffer, font=font, fill=(*color, 255))
    return layer


def _channel_display_name(channel_name: str) -> str:
    slug = (channel_name or "").strip()
    if slug:
        try:
            module = importlib.import_module(f"channels_config.{slug}.page_config")
            label = getattr(module, "PAGE_DISPLAY_NAME", None)
            if label and str(label).strip():
                return str(label).strip()
        except Exception:  # noqa: BLE001
            pass
    return slug.replace("_", " ").title() or "Channel"


def _cover_resize(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    """Scale-to-cover and center-crop so the card is exactly *size* with no bars."""
    target_w, target_h = size
    if target_w < 1 or target_h < 1:
        raise ValueError("output size must be positive")
    source = image.convert("RGB")
    sw, sh = source.size
    scale = max(target_w / max(sw, 1), target_h / max(sh, 1))
    resized = source.resize(
        (max(1, int(round(sw * scale))), max(1, int(round(sh * scale)))),
        Image.Resampling.LANCZOS,
    )
    left = max(0, (resized.width - target_w) // 2)
    top = max(0, (resized.height - target_h) // 2)
    return resized.crop((left, top, left + target_w, top + target_h))


def _apply_opacity(layer: Image.Image, opacity: float) -> Image.Image:
    if opacity >= 0.999:
        return layer
    red, green, blue, alpha = layer.convert("RGBA").split()
    alpha = alpha.point(lambda px: int(px * max(0.0, min(1.0, opacity))))
    return Image.merge("RGBA", (red, green, blue, alpha))


def _inscribed_crop_size(width: int, height: int, angle_deg: float) -> tuple[int, int]:
    """Largest axis-aligned crop that stays inside a rotated rectangle."""
    angle = math.radians(abs(angle_deg) % 180.0)
    if angle > math.pi / 2:
        angle = math.pi - angle
    if angle < 1e-6:
        return width, height
    sin_a = math.sin(angle)
    cos_a = math.cos(angle)
    if sin_a < 1e-6 or cos_a < 1e-6:
        return width, height
    width_is_longer = width >= height
    side_long = float(width if width_is_longer else height)
    side_short = float(height if width_is_longer else width)
    if side_short <= 2.0 * sin_a * cos_a * side_long or abs(sin_a - cos_a) < 1e-6:
        half = 0.5 * side_short
        crop_w, crop_h = (half / sin_a, half / cos_a) if width_is_longer else (half / cos_a, half / sin_a)
    else:
        cos_2a = cos_a * cos_a - sin_a * sin_a
        crop_w = (width * cos_a - height * sin_a) / cos_2a
        crop_h = (height * cos_a - width * sin_a) / cos_2a
    return max(1, int(crop_w)), max(1, int(crop_h))


def _sample_page_fill(image: Image.Image) -> tuple[int, int, int]:
    """Pick a light paper-like color so rotated corners do not go black."""
    rgb = image.convert("RGB")
    width, height = rgb.size
    xs = (max(0, width // 2), max(0, width // 3), min(width - 1, (2 * width) // 3))
    ys = (max(0, height // 6), max(0, height // 4), max(0, height // 3))
    samples = [rgb.getpixel((x, y)) for y in ys for x in xs]
    samples.sort(key=lambda pixel: pixel[0] + pixel[1] + pixel[2], reverse=True)
    best = samples[:3] or [(247, 241, 228)]
    return (
        sum(pixel[0] for pixel in best) // len(best),
        sum(pixel[1] for pixel in best) // len(best),
        sum(pixel[2] for pixel in best) // len(best),
    )


def _rotate_and_crop(image: Image.Image, angle: float) -> Image.Image:
    """Tilt the card, then zoom-crop so side bars never remain."""
    rgb = image.convert("RGB")
    fill = _sample_page_fill(rgb)
    rotated = rgb.rotate(
        angle,
        resample=Image.Resampling.BICUBIC,
        expand=False,
        fillcolor=fill,
    )
    if abs(angle) < 0.01:
        return rotated
    ins_w, ins_h = _inscribed_crop_size(rgb.width, rgb.height, angle)
    crop_w = min(max(1, ins_w), rotated.width)
    crop_h = min(max(1, ins_h), rotated.height)
    left = max(0, (rotated.width - crop_w) // 2)
    top = max(0, (rotated.height - crop_h) // 2)
    return rotated.crop((left, top, left + crop_w, top + crop_h))


def _atomic_save_png(image: Image.Image, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = dest.with_name(f".{dest.stem}.{os.getpid()}.tmp.png")
    try:
        image.save(tmp_path, format="PNG", optimize=True)
        tmp_path.replace(dest)
    except OSError:
        image.save(dest, format="PNG", optimize=True)
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
    finally:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

class ImageTextsRenderEngine:
    """Render IMAGE-TEXTS quote cards for a factory channel.

    Parameters
    ----------
    vault_path:
        ``ocr_vault.json``. Defaults to the factory assets path.
    factory_root:
        Unified Multi-Page Factory root used to resolve channel configs.
    channels_config_root:
        Optional override for ``channels_config/`` (useful in tests).
    output_dir:
        Optional override. Default is ``{OUTPUT_PATH}/{channel}/image_texts``
        from the factory ``.env``.
    template_mode:
        ``random`` (default) or ``cycle``.
    rotate_text:
        Rotate the finished card by a random angle (0° allowed), then
        zoom-crop empty corners and fit ``output_size``. Short quotes use
        up to ±20°; long quotes cap at ±15°.
    output_size:
        Final card size. Defaults to ``OUTPUT_SIZE`` (1080×1350).
    seed:
        Optional RNG seed for reproducible jitter / template picks.
    """

    def __init__(
        self,
        vault_path: str | Path | None = None,
        factory_root: str | Path | None = None,
        channels_config_root: str | Path | None = None,
        output_dir: str | Path | None = None,
        template_mode: str = "random",
        rotate_text: bool = True,
        line_height_ratio: float = _LINE_HEIGHT_RATIO,
        output_size: tuple[int, int] = OUTPUT_SIZE,
        seed: int | None = None,
    ) -> None:
        self.vault_path: Path = Path(vault_path) if vault_path else VAULT_PATH
        self.factory_root: Path | None = Path(factory_root) if factory_root else None
        self.channels_config_root: Path | None = (
            Path(channels_config_root) if channels_config_root else None
        )
        self.output_dir_override: Path | None = Path(output_dir) if output_dir else None
        mode = (template_mode or "random").strip().lower()
        if mode not in {"cycle", "random"}:
            raise ValueError("template_mode must be 'cycle' or 'random'")
        self.template_mode: str = mode
        self.rotate_text: bool = bool(rotate_text)
        self.line_height_ratio: float = float(line_height_ratio)
        out_w, out_h = output_size
        self.output_size: tuple[int, int] = (max(1, int(out_w)), max(1, int(out_h)))
        self._rng = random.Random(seed)

    def generate_image_texts(
        self,
        channel_name: str,
        dataset_key: str = DEFAULT_DATASET_KEY,
        font_size: int = _DEFAULT_FONT_SIZE,
        text_color: tuple[int, int, int] = (30, 30, 30),
        limit: int | None = None,
    ) -> list[str]:
        """Render quote cards for every usable item under *dataset_key*.

        Returns absolute paths of the PNG files that were written.
        """
        color = _parse_rgb(text_color)
        assets = resolve_channel_assets(
            channel_name,
            factory_root=self.factory_root,
            channels_config_root=self.channels_config_root,
            output_dir=self.output_dir_override,
        )
        assets.output_dir.mkdir(parents=True, exist_ok=True)
        assets.templates_dir.mkdir(parents=True, exist_ok=True)

        if not assets.templates:
            logger.warning(
                "IMAGE-TEXTS | no notebook templates in '%s'. "
                "Using generated lined-notebook pages. Drop 4-5 background "
                "images into that folder to customize the channel look.",
                assets.templates_dir,
            )
        if assets.font_path is None:
            logger.warning(
                "IMAGE-TEXTS | missing channel font under %s "
                "(expected a .ttf such as Courier/Typewriter).",
                assets.image_texts_dir,
            )

        vault = _load_vault(self.vault_path)
        if dataset_key not in vault:
            available = ", ".join(sorted(str(key) for key in vault.keys())) or "(none)"
            raise KeyError(
                f"Dataset '{dataset_key}' not in vault {self.vault_path}. Available: {available}"
            )
        dataset = vault[dataset_key]
        if not isinstance(dataset, Mapping):
            raise RuntimeError(f"Vault dataset '{dataset_key}' must be a JSON object")
        quotes = _iter_quote_entries(dataset)
        if limit is not None:
            if limit < 0:
                raise ValueError("limit must be >= 0")
            quotes = quotes[:limit]
        if not quotes:
            logger.warning("IMAGE-TEXTS | no usable quotes in dataset '%s'", dataset_key)
            return []

        written: list[str] = []
        for index, (source_key, raw_text) in enumerate(quotes):
            try:
                dest = self._render_one(
                    assets=assets,
                    source_key=source_key,
                    raw_text=raw_text,
                    index=index,
                    font_size=font_size,
                    text_color=color,
                )
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "IMAGE-TEXTS | failed %s (%s): %s",
                    source_key,
                    channel_name,
                    exc,
                    exc_info=True,
                )
                continue
            written.append(str(dest))
            logger.info("IMAGE-TEXTS | wrote %s", dest)
        return written

    def _render_one(
        self,
        *,
        assets: ChannelAssets,
        source_key: str,
        raw_text: str,
        index: int,
        font_size: int,
        text_color: tuple[int, int, int],
    ) -> Path:
        template_path = _pick_template(
            assets.templates,
            index,
            mode=self.template_mode,
            rng=self._rng,
        )
        page = _open_template(template_path, variant=index)
        canvas = page.image
        width, height = canvas.size
        big_quote = _is_big_quote(raw_text)
        max_tilt = 15.0 if big_quote else 20.0
        layout_scale = width / _LAYOUT_REF_WIDTH
        fitted_font_size = max(_MIN_READABLE_SIZE, int(round(font_size * layout_scale)))
        fitted_font_size = max(
            _MIN_READABLE_SIZE,
            int(round(fitted_font_size * _length_font_scale(raw_text))),
        )
        left, top, box_right, box_bottom = _text_box_for_page(
            page,
            width,
            height,
            max_angle=max_tilt,
            extra_inset=_BIG_QUOTE_SIDE_INSET if big_quote else 0.0,
        )
        max_width = max(80, box_right - left)
        max_height = max(80, box_bottom - top)
        height_ratio = self.line_height_ratio
        if page.pitch:
            # Prefer double-ruled spacing (matches the source notebook photos),
            # then fall back to single-line pitch for long quotes.
            double = page.pitch * 2 / max(fitted_font_size, 1)
            single = page.pitch / max(fitted_font_size, 1)
            height_ratio = double if double >= 1.05 else single

        probe_font = _load_font(assets.font_path, fitted_font_size)
        quote = _sanitize_quote_text(raw_text, probe_font)
        sig_reserve = max(22, int(round(fitted_font_size * 0.58))) + max(
            18, int(round(fitted_font_size * height_ratio * 0.50))
        )
        quote_height = max(80, max_height - sig_reserve)
        if big_quote:
            quote_height = max(80, min(quote_height, int(max_height * _BIG_QUOTE_HEIGHT_FILL)))
        font, lines, used_size, line_height = _fit_wrapped_text(
            quote,
            assets.font_path,
            fitted_font_size,
            max_width,
            quote_height,
            height_ratio,
        )
        if page.pitch:
            line_height = page.pitch if _block_height(lines, page.pitch * 2) > max_height else page.pitch * 2
        block_h = _block_height(lines, line_height)
        signature = _channel_display_name(assets.channel_name)
        sig_size = max(_MIN_FONT_SIZE, int(round(used_size * 0.58)))
        sig_font = _load_font(assets.font_path, sig_size)
        sig_w, sig_h = _measure_text(sig_font, signature)
        sig_gap = max(18, int(line_height * 0.50))
        total_h = block_h + sig_gap + sig_h
        origin_y = (height - total_h) // 2
        origin_x = left
        origin_y = min(max(origin_y, top), top + max(0, max_height - total_h))
        origin_x = min(max(origin_x, left), left + max(0, max_width - 80))
        text_layer = _draw_text_layer(
            canvas.size,
            lines,
            font,
            color=text_color,
            origin=(origin_x, origin_y),
            line_height=line_height,
            rng=self._rng,
            align="center",
            box_width=max_width,
        )
        heart_size = max(16, int(line_height * 0.55))
        line_rights = [
            origin_x + (max_width + _line_pixel_width(line, font, heart_size)) // 2
            for line in lines
            if line
        ]
        block_right = max(line_rights) if line_rights else origin_x + max_width
        # Slightly right of the quote, like a short handwritten sign-off.
        sig_x = block_right - sig_w + max(14, int(used_size * 0.34))
        sig_x = min(max(sig_x, origin_x), box_right - sig_w)
        sig_x = max(left, min(sig_x, width - sig_w - 8))
        sig_y = origin_y + block_h + sig_gap
        ImageDraw.Draw(text_layer).text((sig_x, sig_y), signature, font=sig_font, fill=(*text_color, 255))
        text_layer = _apply_opacity(text_layer, _TEXT_OPACITY)

        composed = Image.alpha_composite(canvas.convert("RGBA"), text_layer)
        finished = composed.convert("RGB")
        if self.rotate_text:
            choices = _rotation_choices_for_quote(quote, len(lines))
            angle = float(self._rng.choice(choices))
            if abs(angle) > 0.01:
                finished = _rotate_and_crop(finished, angle)
        finished = _cover_resize(finished, self.output_size)

        dest = assets.output_dir / f"{_slug(Path(source_key).stem)}_{index:03d}.png"
        _atomic_save_png(finished, dest)
        logger.debug(
            "IMAGE-TEXTS | %s font=%s size=%d lines=%d template=%s",
            dest.name,
            assets.font_path.name if assets.font_path else "default",
            used_size,
            len(lines),
            template_path.name if template_path else "generated",
        )
        return dest.resolve()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _configure_logging() -> None:
    if logging.getLogger().handlers:
        return
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Render IMAGE-TEXTS quote cards from ocr_vault.json",
    )
    parser.add_argument("--channel", required=True, help="Channel slug (e.g. ancient_knowledge)")
    parser.add_argument("--dataset", default=DEFAULT_DATASET_KEY, help="Vault dataset key")
    parser.add_argument("--vault", default=str(VAULT_PATH), help="ocr_vault.json path")
    parser.add_argument("--limit", type=int, default=None, help="Max items to render")
    parser.add_argument("--font-size", type=int, default=_DEFAULT_FONT_SIZE, help="Starting type size")
    parser.add_argument("--text-color", default="30,30,30", help="Ink color as R,G,B")
    parser.add_argument(
        "--template-mode",
        choices=("cycle", "random"),
        default="random",
        help="How notebook backgrounds are selected",
    )
    parser.add_argument("--width", type=int, default=OUTPUT_SIZE[0], help="Final card width")
    parser.add_argument("--height", type=int, default=OUTPUT_SIZE[1], help="Final card height")
    parser.add_argument("--output-dir", default=None, help="Override destination folder")
    parser.add_argument("--no-rotate", action="store_true", help="Keep the card unrotated")
    parser.add_argument("--seed", type=int, default=None, help="RNG seed")
    return parser


def main(argv: list[str] | None = None) -> int:
    _configure_logging()
    args = build_parser().parse_args(argv)
    engine = ImageTextsRenderEngine(
        vault_path=args.vault,
        output_dir=args.output_dir,
        template_mode=args.template_mode,
        rotate_text=not args.no_rotate,
        output_size=(args.width, args.height),
        seed=args.seed,
    )
    paths = engine.generate_image_texts(
        channel_name=args.channel,
        dataset_key=args.dataset,
        font_size=args.font_size,
        text_color=_parse_rgb(args.text_color),
        limit=args.limit,
    )
    print(f"IMAGE-TEXTS rendered {len(paths)} file(s)")
    for path in paths:
        print(path)
    return 0 if paths else 1


if __name__ == "__main__":
    sys.exit(main())

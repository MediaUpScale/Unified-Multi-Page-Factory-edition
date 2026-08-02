# -*- coding: utf-8 -*-
"""
Page context loader for the Unified Multi-Page Factory.

Resolves page-specific paths and pipeline flags from the active --page,
--avatar, and --format CLI arguments. Each page lives in an isolated
directory under pages_config/{page_id}/ and carries its own:

  - master_dna.json      — persona data, environments, voice, CTAs
  - persona_dna.py       — Python interface over master_dna.json
  - page_config.py       — page-level overrides (aspect ratio, atmosphere style, etc.)
  - avatar_reference/    — optional: avatar.png for likeness-locked generation
  - product_reference/   — optional: PDF corpus for the research brain

Supported pages
---------------
  anna_protocol   Holistic Legacy — ancestral wellness, natural remedies, avatar ON
  master_mei      SUPER channel — Stoic financial freedom / wealth mindset (US), avatar ON
  wonder_feed     Emotional intelligence, attachment science, avatar OFF (default)
  down_dirty      Matrix escape, financial sovereignty, raw mindset, avatar OFF (default)
  ancient_knowledge  Ancient history, conspiracies, mysteries, photorealistic style, avatar OFF
  momma_circle    Parenting / PARENTAL_CONTENTS — reference-clip reels, warm lullaby audio, avatar OFF

Usage
-----
    from page_loader import load_page_context, PageContext

    ctx = load_page_context("anna_protocol", avatar_mode="ON", post_format="IMAGE_AVATAR")
    print(ctx.page_dir)
    print(ctx.avatar_on)
    print(ctx.atmosphere_style)
"""
from __future__ import annotations

import importlib.util
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_LOG = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# MODULE 2 — Gemini Vision style-reference extraction. Sent ONCE per page
# per run; the extracted ~40-word anchor is cached on the PageContext
# instance and prepended to every image prompt generated for that run.
# ---------------------------------------------------------------------------
_STYLE_VISION_PROMPT: str = (
    "Analyze these reference images to extract the exact style rules. "
    "Focus on: dark 80s biomechanical cyberpunk, practical body horror FX, "
    "H.R. Giger aesthetics, flesh pierced by cables/tubes with scarred entry wounds, "
    "exposed copper wiring stitched into pale distressed skin, dirty raw metal, "
    "gritty 35mm film grain. Do NOT describe rubber aprons, chestplates, or tactical vests. "
    "Vary tech mods — do not put VR goggles on every face."
)


def _extract_style_anchor_via_gemini_vision(image_paths: "list[Path]", page_id: str) -> str:
    """
    Send ``image_paths`` ONCE to gemini-2.5-flash and extract a dense ~40-word
    visual style anchor string. Returns "" on ANY failure (missing API key,
    unreadable image, API error) so callers can gracefully fall back to a
    static style anchor — this call must never crash the pipeline.
    """
    if not image_paths:
        return ""
    try:
        from PIL import Image
        from google import genai

        import config as app_config
        api_key = getattr(app_config, "GEMINI_API_KEY", None)
        if not api_key:
            _LOG.warning(
                "DYNAMIC_STYLE_ANCHOR | GEMINI_API_KEY missing — skipping vision "
                "extraction for page=%s; falling back to static style anchor.",
                page_id,
            )
            return ""

        contents: list[Any] = []
        for p in image_paths:
            try:
                contents.append(Image.open(p))
            except Exception as exc:  # noqa: BLE001
                _LOG.warning("DYNAMIC_STYLE_ANCHOR | could not open %s (%s)", p, exc)
        if not contents:
            return ""
        contents.append(_STYLE_VISION_PROMPT)

        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model="models/gemini-2.5-flash", contents=contents,
        )
        text = (getattr(response, "text", "") or "").strip()
        if text:
            _LOG.info(
                "DYNAMIC_STYLE_ANCHOR extracted | page=%s | %d word(s): %s",
                page_id, len(text.split()), text[:200],
            )
        return text
    except Exception as exc:  # noqa: BLE001
        _LOG.warning(
            "DYNAMIC_STYLE_ANCHOR | vision extraction failed for page=%s (%s) — "
            "falling back to static style anchor.",
            page_id, exc,
        )
        return ""


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VALID_PAGES: tuple[str, ...] = (
    "anna_protocol",
    "master_mei",
    "wonder_feed",
    "down_dirty",
    "ancient_knowledge",
    "momma_circle",
)

VALID_AVATAR_MODES: tuple[str, ...] = ("ON", "OFF")

VALID_FORMATS: tuple[str, ...] = (
    "IMAGE_AVATAR",       # standard portrait image ± avatar
    "IMAGE_QUOTE",        # Gemini image + text overlay (legacy alias)
    "IMAGE_BACKGROUND",   # hyper-literal Gemini background + text overlay (SMART_BAIT default)
    "HYBRID_VIDEO",       # 7-second Ken Burns zoom loop from generated image
    "TEXT_QUOTE",         # brand-colour solid backdrop + text only (no Gemini image call)
    "DYNAMIC_REEL",       # ECONOMIC_REEL: single image → MP4 via video_engine
    "SEQUENCE_REEL",      # multi-image 80-second reel via core_engine.reel_sequence_engine
    "REFERENCE_BASED_REELS",  # raw footage clip + hook overlay + lullaby audio (momma_circle)
)

_ENGINE_ROOT: Path = Path(__file__).resolve().parent
_PAGES_CONFIG_ROOT: Path = _ENGINE_ROOT / "pages_config"


# ---------------------------------------------------------------------------
# PageContext dataclass
# ---------------------------------------------------------------------------

@dataclass
class PageContext:
    """
    All page-specific runtime parameters resolved from --page, --avatar, --format.

    Attributes
    ----------
    page_id:
        Slug identifying the active page (e.g. 'anna_protocol').
    avatar_mode:
        'ON' — include human subject + reference image in image generation.
        'OFF' — bypass avatar pipeline; generate purely atmospheric imagery.
    post_format:
        'IMAGE_AVATAR'      — standard portrait image with (or without) avatar.
        'IMAGE_QUOTE'       — Gemini image + text overlay (legacy alias for IMAGE_BACKGROUND).
        'IMAGE_BACKGROUND'  — hyper-literal Gemini background + text overlay (SMART_BAIT default).
        'HYBRID_VIDEO'      — 7-second Ken Burns zoom loop from generated image.
        'TEXT_QUOTE'        — brand-colour solid backdrop + text only (no Gemini image call).
    page_dir:
        Absolute path to pages_config/{page_id}/.
    persona_dna_path:
        Absolute path to pages_config/{page_id}/persona_dna.py.
    master_dna_path:
        Absolute path to pages_config/{page_id}/master_dna.json.
    avatar_reference_dir:
        Absolute path to pages_config/{page_id}/avatar_reference/ (auto-created).
    logo_dir:
        Absolute path to pages_config/{page_id}/logo/ (auto-created).
        Drop a transparent PNG here to activate the logo watermark layer.
    product_reference_dir:
        Absolute path to pages_config/{page_id}/product_reference/ (may not exist).
    outputs_dir:
        Absolute path to outputs/{page_id}/ for all page-namespaced artifacts.
    page_cfg:
        Dict of values exported from page_config.py (atmosphere_style, aspect_ratio, etc.).
    """

    page_id: str
    avatar_mode: str
    post_format: str
    page_dir: Path
    persona_dna_path: Path
    master_dna_path: Path
    avatar_reference_dir: Path
    logo_dir: Path
    product_reference_dir: Path
    outputs_dir: Path
    page_cfg: dict[str, Any] = field(default_factory=dict)
    _dynamic_style_anchor_cache: "str | None" = field(default=None, repr=False, compare=False)

    # ------------------------------------------------------------------
    # Convenience properties
    # ------------------------------------------------------------------

    @property
    def avatar_on(self) -> bool:
        """True when --avatar ON is active."""
        return self.avatar_mode == "ON"

    @property
    def is_hybrid_video(self) -> bool:
        """True when --format HYBRID_VIDEO is active."""
        return self.post_format == "HYBRID_VIDEO"

    @property
    def is_image_quote(self) -> bool:
        """True when --format IMAGE_QUOTE is active."""
        return self.post_format == "IMAGE_QUOTE"

    @property
    def display_name(self) -> str:
        return self.page_cfg.get("PAGE_DISPLAY_NAME", self.page_id)

    @property
    def atmosphere_style(self) -> str:
        """Atmospheric visual style string used when --avatar OFF."""
        return self.page_cfg.get(
            "ATMOSPHERE_STYLE",
            "Cinematic environmental photography. Moody, high-fidelity, no human subjects.",
        )

    @property
    def image_aspect_ratio(self) -> str:
        """Aspect ratio override from page_config.py; falls back to global config."""
        return self.page_cfg.get("IMAGE_ASPECT_RATIO", "")

    @property
    def content_niche(self) -> str:
        return self.page_cfg.get("CONTENT_NICHE", "")

    @property
    def avatar_reference_png(self) -> Path:
        """Resolved path to avatar.png inside avatar_reference/."""
        return self.avatar_reference_dir / "avatar.png"

    @property
    def avatar_reference_exists(self) -> bool:
        return self.avatar_reference_png.is_file()

    @property
    def uses_avatar_reference(self) -> bool:
        """Whether the page was designed to use a human likeness reference."""
        return bool(self.page_cfg.get("USES_AVATAR_REFERENCE", False))

    @property
    def logo_png(self) -> Path | None:
        """
        Returns the first .png found in logo_dir, or None if the folder is empty.

        The logo PNG should have a transparent background (RGBA).  It is applied
        as a watermark on all final image outputs and HYBRID_VIDEO frames.
        """
        if not self.logo_dir.is_dir():
            return None
        for candidate in sorted(self.logo_dir.iterdir()):
            if candidate.suffix.lower() == ".png" and candidate.is_file():
                return candidate
        return None

    @property
    def logo_exists(self) -> bool:
        """True when at least one .png is present in logo_dir."""
        return self.logo_png is not None

    @property
    def logo_size_scale(self) -> float:
        """
        Logo width as a fraction of canvas width (e.g. 0.30 = 30 %).
        Sourced from LOGO_SIZE_SCALE in page_config.py; defaults to 0.30.
        """
        raw = self.page_cfg.get("LOGO_SIZE_SCALE", 0.30)
        try:
            val = float(raw)
            return max(0.05, min(val, 0.50))   # clamp to 5–50 %
        except (TypeError, ValueError):
            return 0.18

    @property
    def caption_signature(self) -> str:
        """
        Brand copyright line appended to every LONG_CAPTION_IMAGE caption.

        Sources (in priority order):
          1. CAPTION_SIGNATURE in page_config.py  (explicit override)
          2. ``© {display_name} | by MediaUpScale`` derived from display_name
          3. ``© MediaUpScale`` ultimate fallback
        """
        explicit = self.page_cfg.get("CAPTION_SIGNATURE", "")
        if explicit:
            return str(explicit).strip()
        name = self.display_name
        if name:
            return f"© {name} | by MediaUpScale"
        return "© MediaUpScale"

    @property
    def logo_position(self) -> str:
        """
        Placement for the logo watermark.
        One of: 'top_left', 'top_right', 'bottom_left', 'bottom_right',
                'bottom_center', 'top_center'.
        Sourced from LOGO_POSITION in page_config.py; defaults to 'bottom_right'.
        """
        raw = str(self.page_cfg.get("LOGO_POSITION", "bottom_right")).lower().strip()
        valid = {
            "top_left", "top_right", "bottom_left", "bottom_right",
            "bottom_center", "top_center",
        }
        return raw if raw in valid else "bottom_right"

    @property
    def illustration_style(self) -> str:
        """
        Visual style directive used for LONG_CAPTION_IMAGE and as a supplementary
        modifier for SMART_BAIT image prompts.
        Sourced from ILLUSTRATION_STYLE in page_config.py; falls back to empty string.
        """
        return str(self.page_cfg.get("ILLUSTRATION_STYLE", "")).strip()

    @property
    def font_path(self) -> str:
        """
        Relative path (from engine root) to the preferred .ttf font file.
        Sourced from FONT_PATH in page_config.py; falls back to empty string.
        The caller is responsible for resolving to an absolute path.
        """
        return str(self.page_cfg.get("FONT_PATH", "")).strip()

    @property
    def font_size_scale(self) -> float:
        """
        Font size expressed as a fraction of canvas width (e.g. 0.08 = 8 %).
        Sourced from FONT_SIZE_SCALE in page_config.py; defaults to 0.08.
        """
        raw = self.page_cfg.get("FONT_SIZE_SCALE", 0.08)
        try:
            return max(0.02, min(float(raw), 0.30))
        except (TypeError, ValueError):
            return 0.08

    @property
    def elevenlabs_voice_id(self) -> str:
        """ElevenLabs voice ID for ECONOMIC_REEL voiceover generation."""
        return str(self.page_cfg.get("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")).strip()

    @property
    def elevenlabs_model(self) -> str:
        """ElevenLabs TTS model for ECONOMIC_REEL voiceover generation."""
        return str(self.page_cfg.get("ELEVENLABS_MODEL", "eleven_multilingual_v2")).strip()

    @property
    def tts_voice_preference(self) -> str:
        """
        Human-readable voice label for documentation and logging.
        Sourced from TTS_VOICE_PREFERENCE in page_config.py; defaults to empty string.
        This is a descriptive label only — the actual API call uses elevenlabs_voice_id.
        """
        return str(self.page_cfg.get("TTS_VOICE_PREFERENCE", "")).strip()

    @property
    def reel_duration(self) -> float:
        """Target ECONOMIC_REEL duration in seconds (fallback if no audio)."""
        try:
            return max(5.0, float(self.page_cfg.get("REEL_DURATION", 30.0)))
        except (TypeError, ValueError):
            return 30.0

    @property
    def reel_overlay_opacity(self) -> float:
        """Dark vignette opacity (0-1) applied over the graphite base in ECONOMIC_REEL."""
        try:
            val = float(self.page_cfg.get("REEL_OVERLAY_OPACITY", 0.35))
            return max(0.0, min(1.0, val))
        except (TypeError, ValueError):
            return 0.35

    @property
    def subtitle_words_per_phrase(self) -> int:
        try:
            return max(3, min(5, int(self.page_cfg.get("SUBTITLE_WORDS_PER_PHRASE", 4))))
        except (TypeError, ValueError):
            return 4

    @property
    def subtitle_fill(self) -> tuple:
        raw = self.page_cfg.get("SUBTITLE_FILL", (255, 230, 0))
        if isinstance(raw, (list, tuple)) and len(raw) >= 3:
            return (int(raw[0]), int(raw[1]), int(raw[2]))
        return (255, 230, 0)

    @property
    def subtitle_stroke_fill(self) -> "tuple | None":
        raw = self.page_cfg.get("SUBTITLE_STROKE_FILL", None)
        if isinstance(raw, (list, tuple)) and len(raw) >= 3:
            return (int(raw[0]), int(raw[1]), int(raw[2]))
        return None

    @property
    def subtitle_stroke_width(self) -> int:
        try:
            return max(0, int(self.page_cfg.get("SUBTITLE_STROKE_WIDTH", 0)))
        except (TypeError, ValueError):
            return 0

    @property
    def subtitle_fontsize(self) -> int:
        """Subtitle font size in pixels for ECONOMIC_REEL word-level subtitles."""
        try:
            return max(20, int(self.page_cfg.get("SUBTITLE_FONTSIZE", 46)))
        except (TypeError, ValueError):
            return 46

    @property
    def subtitle_y_position(self) -> "int | None":
        """Absolute Y-pixel from canvas top for subtitle placement.
        Returns None when not set, causing video_engine to fall back to its
        default y_frac=0.82 positioning."""
        raw = self.page_cfg.get("SUBTITLE_Y_POSITION", None)
        if raw is None:
            return None
        try:
            return max(0, int(raw))
        except (TypeError, ValueError):
            return None

    @property
    def logo_width_px(self) -> int:
        """Absolute pixel width to resize the logo PNG in ECONOMIC_REEL.
        Overrides the fractional 18%-of-canvas-width default."""
        try:
            return max(40, int(self.page_cfg.get("LOGO_WIDTH", 160)))
        except (TypeError, ValueError):
            return 160

    @property
    def logo_y_offset_px(self) -> int:
        """Pixels from the bottom canvas edge to the bottom of the logo in ECONOMIC_REEL.
        Reads LOGO_BOTTOM_MARGIN first (canonical key); falls back to LOGO_Y_OFFSET for
        pages that still use the older key name."""
        try:
            val = (
                self.page_cfg.get("LOGO_BOTTOM_MARGIN")
                or self.page_cfg.get("LOGO_Y_OFFSET", 90)
            )
            return max(10, int(val))
        except (TypeError, ValueError):
            return 90

    @property
    def logo_opacity(self) -> float:
        """Logo PNG opacity (0.0–1.0) applied during ECONOMIC_REEL pre-render.
        60% = humble, authentic blend; 70% = prominent."""
        try:
            val = float(self.page_cfg.get("LOGO_OPACITY", 0.70))
            return max(0.05, min(1.0, val))
        except (TypeError, ValueError):
            return 0.70

    @property
    def logo_max_height_px(self) -> "int | None":
        """Hard pixel cap on logo height in ECONOMIC_REEL.
        Returns None when unset, allowing free aspect-ratio scaling from logo_width_px."""
        raw = self.page_cfg.get("LOGO_MAX_HEIGHT", None)
        if raw is None:
            return None
        try:
            return max(1, int(raw))
        except (TypeError, ValueError):
            return None

    @property
    def hook_y_frac(self) -> float:
        """Vertical centre of the hook/bait text as a fraction of canvas height.
        0.30 = upper-middle Zone A (~576 px on a 1920 px canvas).
        Defaults to 0.55 (legacy centre) when not set."""
        try:
            val = float(self.page_cfg.get("HOOK_Y_FRAC", 0.55))
            return max(0.05, min(0.95, val))
        except (TypeError, ValueError):
            return 0.55

    @property
    def topic_pool(self) -> "list[str]":
        """Rotating topic seeds used when no --topic flag is supplied.
        Prevents the static 'Holistic vitality protocol' fallback from driving
        identical LLM output on every run.  Returns an empty list when unset."""
        raw = self.page_cfg.get("TOPIC_POOL", [])
        if not isinstance(raw, list):
            return []
        return [str(t).strip() for t in raw if t and str(t).strip()]

    @property
    def base_graphite_prompt(self) -> str:
        """Single permanent visual style string sent to Gemini for all post formats."""
        return str(self.page_cfg.get("BASE_GRAPHITE_PROMPT", "")).strip()

    @property
    def enable_sketch_style(self) -> bool:
        """True = use fine-art sketch pipeline; False = photorealistic fallback."""
        return bool(self.page_cfg.get("ENABLE_SKETCH_STYLE", False))

    @property
    def enable_horror_transformations(self) -> bool:
        """True = inject dark surrealist mask/monster transformation directives."""
        return bool(self.page_cfg.get("ENABLE_HORROR_TRANSFORMATIONS", False))

    @property
    def sketch_style_prompt(self) -> str:
        """Fine-art graphite-sketch style directive for SMART_BAIT image generation."""
        return str(self.page_cfg.get("SKETCH_STYLE_PROMPT", "")).strip()

    @property
    def raw_graphite_horror_prompt(self) -> str:
        """Dark psychological surrealism / horror-transformation style directive."""
        return str(self.page_cfg.get("RAW_GRAPHITE_HORROR_PROMPT", "")).strip()

    @property
    def use_style_reference(self) -> bool:
        """Toggle: if True, image prompt includes recurring style_characters personas."""
        return bool(self.page_cfg.get("USE_STYLE_REFERENCE", False))

    @property
    def style_characters(self) -> str:
        """Recurring persona description injected into the image prompt when use_style_reference=True."""
        return str(self.page_cfg.get("STYLE_CHARACTERS", "")).strip()

    @property
    def text_outline_width(self) -> int:
        """
        PIL stroke_width for text overlay (0 = no outline).
        Sourced from TEXT_OUTLINE_WIDTH in page_config.py; defaults to 0.
        """
        try:
            return max(0, int(self.page_cfg.get("TEXT_OUTLINE_WIDTH", 0)))
        except (TypeError, ValueError):
            return 0

    @property
    def style_reference_dir(self) -> str:
        """
        Relative path (from engine root) to the aesthetic reference image directory.
        Sourced from STYLE_REFERENCE_DIR in page_config.py; falls back to empty string.
        """
        return str(self.page_cfg.get("STYLE_REFERENCE_DIR", "")).strip()

    @property
    def style_reference_weight(self) -> float:
        """
        IP-Adapter / style-ref strength (0.65–0.80 recommended) — enforce local
        visual style while still allowing prompt compliance. Sourced from
        STYLE_REFERENCE_WEIGHT in page_config.py; defaults to 0.72.
        """
        try:
            w = float(self.page_cfg.get("STYLE_REFERENCE_WEIGHT", 0.72))
        except (TypeError, ValueError):
            w = 0.72
        return max(0.0, min(1.0, w))

    @property
    def style_reference_max_images(self) -> int:
        """Max style reference images loaded per generation run (2–3 recommended)."""
        try:
            return max(1, int(self.page_cfg.get("STYLE_REFERENCE_MAX_IMAGES", 3)))
        except (TypeError, ValueError):
            return 3

    @property
    def master_style_anchor(self) -> str:
        """
        Style-anchor text appended VERBATIM to every generated image prompt.
        Sourced from MASTER_STYLE_ANCHOR in page_config.py; defaults to "".
        """
        return str(self.page_cfg.get("MASTER_STYLE_ANCHOR", "")).strip()

    def resolve_style_reference_images(self, max_images: "int | None" = None) -> list[Path]:
        """
        MODULE 3 — dynamic per-page style reference resolution.

        Base directory pattern: ``pages_config/<page_id>/style_reference/``
        (or the explicit ``STYLE_REFERENCE_DIR`` override when set). Loads all
        valid ``*.png`` / ``*.jpg`` / ``*.jpeg`` assets, sorted by filename,
        capped at ``max_images`` (defaults to ``style_reference_max_images``,
        2–3 recommended).
        """
        limit = max_images if max_images is not None else self.style_reference_max_images
        override = self.style_reference_dir
        sref_dir = (
            (_ENGINE_ROOT / override) if override else (self.page_dir / "style_reference")
        )
        if not sref_dir.is_dir():
            return []
        found: list[Path] = []
        for ext in ("*.png", "*.jpg", "*.jpeg"):
            found.extend(sorted(sref_dir.glob(ext)))
        # De-dupe while preserving order, then cap.
        seen: set[str] = set()
        ordered: list[Path] = []
        for p in sorted(found, key=lambda x: x.name.lower()):
            key = str(p.resolve())
            if key not in seen:
                seen.add(key)
                ordered.append(p)
        return ordered[: max(1, limit)]

    def resolve_dynamic_style_anchor(self, *, force_refresh: bool = False) -> str:
        """
        MODULE 2 — Gemini Vision style-reference extraction.

        Loads ``pages_config/<page_id>/style_reference/`` images (via
        ``resolve_style_reference_images``), sends them ONCE to
        ``gemini-2.5-flash`` to extract a dense ~40-word visual style anchor,
        and caches the result (``DYNAMIC_STYLE_ANCHOR``) on this PageContext
        instance for the remainder of the run. Falls back to the static
        ``MASTER_STYLE_ANCHOR`` (page_config.py) when no reference images
        exist or vision extraction fails for any reason — never blocks or
        crashes the pipeline.
        """
        if self._dynamic_style_anchor_cache is not None and not force_refresh:
            return self._dynamic_style_anchor_cache

        images = self.resolve_style_reference_images()
        print(
            f"[DEBUG] Loaded {len(images)} style reference assets for {self.page_id}: "
            f"{[p.name for p in images]}"
        )
        anchor = _extract_style_anchor_via_gemini_vision(images, self.page_id) if images else ""
        resolved = anchor or self.master_style_anchor
        self._dynamic_style_anchor_cache = resolved
        return resolved

    @property
    def font_color(self) -> tuple[int, int, int]:
        """
        RGB text colour tuple (e.g. (255, 255, 255) for white).
        Sourced from FONT_COLOR in page_config.py; defaults to white.
        """
        raw = self.page_cfg.get("FONT_COLOR", (255, 255, 255))
        try:
            r, g, b = int(raw[0]), int(raw[1]), int(raw[2])
            return (r, g, b)
        except Exception:
            return (255, 255, 255)

    # ------------------------------------------------------------------
    # Core-engine modular properties (new — all pages may define these)
    # ------------------------------------------------------------------

    @property
    def cost_tier(self) -> str:
        """
        Cost tier for this page: 'nano' | 'economic' | 'premium'.
        Drives CostTracker pricing keys and model selection.
        Sourced from COST_TIER in page_config.py; defaults to 'economic'.
        """
        raw = str(self.page_cfg.get("COST_TIER", "economic")).lower().strip()
        return raw if raw in ("nano", "economic", "premium") else "economic"

    @property
    def enable_cost_tracking(self) -> bool:
        """True = write cost telemetry JSON (cost_*.json) after each variant."""
        return bool(self.page_cfg.get("ENABLE_COST_TRACKING", False))

    @property
    def enable_sequence_reel(self) -> bool:
        """
        True = use core_engine.reel_sequence_engine (4-image 80s reel).
        False = use avatar_engine.video_engine single-image DYNAMIC_REEL.
        Sourced from ENABLE_SEQUENCE_REEL in page_config.py; defaults to False.
        """
        return bool(self.page_cfg.get("ENABLE_SEQUENCE_REEL", False))

    @property
    def reel_image_count(self) -> int:
        """
        Max distinct images stitched in a SEQUENCE_REEL (dense ~4 s/act).
        Sourced from REEL_IMAGE_COUNT in page_config.py; defaults to 18.
        """
        try:
            return max(2, int(self.page_cfg.get("REEL_IMAGE_COUNT", 18)))
        except (TypeError, ValueError):
            return 18

    @property
    def reel_seconds_per_act(self) -> float:
        """
        Target spoken seconds per visual act for dense scene sync (3.5–5.0).
        Sourced from REEL_SECONDS_PER_ACT; defaults to 4.0.
        NOTE: upper clamp is 5.0 (not 4.0) so pages configured for the
        strict "4-5 s per image" pacing rule (e.g. master_mei @ 4.5) are
        never silently forced back down to a faster/denser cadence.
        """
        try:
            spa = float(self.page_cfg.get("REEL_SECONDS_PER_ACT", 4.0))
        except (TypeError, ValueError):
            spa = 4.0
        return max(3.5, min(5.0, spa))

    @property
    def reel_image_min_count(self) -> int:
        """
        Min distinct images/acts (floor) for dense scene sync.
        Sourced from REEL_IMAGE_MIN_COUNT in page_config.py; defaults to 12.
        """
        try:
            return max(2, int(self.page_cfg.get("REEL_IMAGE_MIN_COUNT", 12)))
        except (TypeError, ValueError):
            return 12

    @property
    def reel_act_duration(self) -> float:
        """
        Per-act clip length in seconds used when no audio drives the timeline.
        Sourced from REEL_ACT_DURATION in page_config.py; defaults to 4.0.
        Audio-driven compiles use ``total_audio_duration / n_acts`` instead.
        """
        try:
            return max(3.5, float(self.page_cfg.get("REEL_ACT_DURATION", 4.0)))
        except (TypeError, ValueError):
            return 4.0

    @property
    def enable_top_hook_text(self) -> bool:
        """
        When False the headline/hook text is NOT burned into the top of the frame.
        Only lower-third word subtitles and the logo remain.
        Sourced from ENABLE_TOP_HOOK_TEXT in page_config.py; defaults to True
        for backward compatibility with wonder_feed and other pages.
        """
        return bool(self.page_cfg.get("ENABLE_TOP_HOOK_TEXT", True))

    @property
    def vignette_strength(self) -> float:
        """
        Vignette darkening applied at the frame corners (0 = off, 1 = full black).
        Sourced from VIGNETTE_STRENGTH in page_config.py; defaults to 0.0.
        """
        try:
            val = float(self.page_cfg.get("VIGNETTE_STRENGTH", 0.0))
            return max(0.0, min(1.0, val))
        except (TypeError, ValueError):
            return 0.0

    @property
    def grain_intensity(self) -> float:
        """
        Film grain amplitude in pixel value units (±grain_intensity added to each pixel).
        Sourced from GRAIN_INTENSITY in page_config.py; defaults to 18.0.
        """
        try:
            return max(0.0, float(self.page_cfg.get("GRAIN_INTENSITY", 18.0)))
        except (TypeError, ValueError):
            return 18.0

    @property
    def enable_flicker(self) -> bool:
        """
        Subtle ±5 % brightness oscillation at ~0.10 s random intervals (torch/flame effect).
        Sourced from ENABLE_FLICKER in page_config.py; defaults to False.
        """
        return bool(self.page_cfg.get("ENABLE_FLICKER", False))

    @property
    def enable_light_rays(self) -> bool:
        """
        Animated warm-gold volumetric light beam column sweeping across the canvas.
        Sourced from ENABLE_LIGHT_RAYS in page_config.py; defaults to False.
        """
        return bool(self.page_cfg.get("ENABLE_LIGHT_RAYS", False))

    @property
    def enable_dust_particles(self) -> bool:
        """
        Floating dust/debris particles drifting upward (for ruin/cave environments).
        Sourced from ENABLE_DUST_PARTICLES in page_config.py; defaults to False.
        """
        return bool(self.page_cfg.get("ENABLE_DUST_PARTICLES", False))

    @property
    def enable_light_refraction(self) -> bool:
        """
        Subtle prismatic light-refraction glow (for glass/crystal subjects).
        Sourced from ENABLE_LIGHT_REFRACTION in page_config.py; defaults to False.
        """
        return bool(self.page_cfg.get("ENABLE_LIGHT_REFRACTION", False))

    @property
    def niche_disclaimer(self) -> str:
        """
        Optional niche-specific disclaimer injected into LLM system prompts.
        Sourced from NICHE_DISCLAIMER in page_config.py; defaults to empty string.
        """
        return str(self.page_cfg.get("NICHE_DISCLAIMER", "")).strip()

    @property
    def narrative_mode(self) -> str:
        """
        Script/prompt structure mode for reel narration.
        Sourced from NARRATIVE_MODE in page_config.py.
        Known values: 'investigative' | 'warrior_discipline' | 'psychology' (default).
        """
        raw = str(self.page_cfg.get("NARRATIVE_MODE", "")).strip().lower()
        if raw:
            return raw
        # Infer from known page IDs when unset
        pid = (self.page_id or "").lower()
        if pid == "ancient_knowledge":
            return "investigative"
        if pid == "master_mei":
            return "warrior_discipline"
        return "psychology"

    @property
    def reel_cta_text(self) -> str:
        """
        Spoken CTA line generated as a separate audio block after narration.
        Sourced from REEL_CTA_TEXT in page_config.py; empty = no CTA stitch.
        """
        return str(self.page_cfg.get("REEL_CTA_TEXT", "")).strip()

    @property
    def ambient_sfx_prompt(self) -> str:
        """
        ElevenLabs SFX prompt for ambient underlay.
        Sourced from AMBIENT_SFX_PROMPT in page_config.py; empty = engine default.
        """
        return str(self.page_cfg.get("AMBIENT_SFX_PROMPT", "")).strip()

    @property
    def ambient_volume(self) -> float:
        """
        Ambient BGM bed volume (0–1) mixed under voiceover.
        Sourced from AMBIENT_VOLUME in page_config.py.
        Master Mei cinematic bed locked to 0.35–0.40 (distinctly audible under VO).
        """
        try:
            val = float(self.page_cfg.get("AMBIENT_VOLUME", 0.38))
            if (self.page_id or "").lower() == "master_mei":
                return max(0.35, min(0.40, val))
            return max(0.08, min(1.0, val))
        except (TypeError, ValueError):
            return 0.38

    @property
    def ambient_duck_ratio(self) -> float:
        """Multiply ambient by this while voiceover plays (Master Mei default 0.70)."""
        try:
            val = float(self.page_cfg.get("AMBIENT_DUCK_RATIO", 0.70))
            return max(0.30, min(1.0, val))
        except (TypeError, ValueError):
            return 0.70

    @property
    def master_audio_gain(self) -> float:
        """Overall master mix gain after loudnorm (Master Mei default +15% = 1.15)."""
        try:
            val = float(self.page_cfg.get("MASTER_AUDIO_GAIN", 1.15))
            return max(1.0, min(1.50, val))
        except (TypeError, ValueError):
            return 1.15

    @property
    def voice_volume_gain(self) -> "float | None":
        """Linear VO gain override (e.g. 1.334 ≈ +2.5 dB). None → engine default."""
        raw = self.page_cfg.get("VOICE_VOLUME_GAIN", None)
        if raw is None:
            return None
        try:
            val = float(raw)
            return val if val > 0 else None
        except (TypeError, ValueError):
            return None

    @property
    def ambient_sfx_gain_mul(self) -> "float | None":
        """Ambient/SFX multiplier after bed volume (e.g. ~1.995 for +3 dB over 1.4×)."""
        raw = self.page_cfg.get("AMBIENT_SFX_GAIN_MUL", None)
        if raw is None:
            return None
        try:
            val = float(raw)
            return val if val > 0 else None
        except (TypeError, ValueError):
            return None

    @property
    def hook_environments(self) -> list:
        """
        Rotating scenic environments for Master Mei Act-1 seated-meditation hook.
        Sourced from HOOK_ENVIRONMENTS in page_config.py.
        """
        raw = self.page_cfg.get("HOOK_ENVIRONMENTS", [])
        if not isinstance(raw, list):
            return []
        return [str(t).strip() for t in raw if t and str(t).strip()]

    @property
    def master_mei_visual_dna(self) -> str:
        """Mandatory elder-sage visual DNA string for Master Mei image prompts."""
        return str(self.page_cfg.get("MASTER_MEI_VISUAL_DNA", "")).strip()

    @property
    def sequence_force_avatar_off(self) -> bool:
        """When True, sequence/ECONOMIC_REEL images ignore avatar likeness refs."""
        return bool(self.page_cfg.get("SEQUENCE_FORCE_AVATAR_OFF", False))

    @property
    def avatar_image_weight(self) -> float:
        """Likeness strength 0–1 injected into Gemini reference prompt (IP-adapter analogue)."""
        try:
            return max(0.1, min(1.0, float(self.page_cfg.get("AVATAR_IMAGE_WEIGHT", 0.75))))
        except (TypeError, ValueError):
            return 0.75

    @property
    def forced_avatar_reference_path(self) -> "Path | None":
        """
        Absolute path to the page's mandatory avatar.png.

        For master_mei: ALWAYS resolves to
        pages_config/master_mei/avatar_reference/avatar.png (zero-hallucination lock).
        """
        page_id = (self.page_id or "").lower()
        if page_id == "master_mei":
            try:
                from avatar_engine.mei_visual import resolve_master_mei_avatar_path
                p = resolve_master_mei_avatar_path(_ENGINE_ROOT)
                return p
            except Exception:
                pass
        rel = str(self.page_cfg.get("AVATAR_REFERENCE_PATH", "")).strip()
        if rel:
            p = Path(rel)
            if not p.is_absolute():
                p = _ENGINE_ROOT / rel
            return p
        if self.avatar_reference_png.is_file():
            return self.avatar_reference_png
        return None

    @property
    def reel_force_exact_duration(self) -> bool:
        """When True, sequence reel container is floored at REEL_DURATION (e.g. 80 s)."""
        return bool(self.page_cfg.get("REEL_FORCE_EXACT_DURATION", False))

    @property
    def reel_narration_words(self) -> int:
        """Target narration word count for sequence TTS (master_mei ≈ 230 for ~100–120 s)."""
        try:
            return max(80, int(self.page_cfg.get("REEL_NARRATION_WORDS", 140)))
        except (TypeError, ValueError):
            return 140

    @property
    def reel_narration_min_words(self) -> int:
        """Minimum acceptable narration word count before regenerate/fallback warn."""
        try:
            return max(60, int(self.page_cfg.get("REEL_NARRATION_MIN_WORDS", 110)))
        except (TypeError, ValueError):
            return 110

    @property
    def reel_narration_max_words(self) -> int:
        """Hard ceiling for narration trim (master_mei ≈ 240)."""
        try:
            return max(
                self.reel_narration_words,
                int(self.page_cfg.get("REEL_NARRATION_MAX_WORDS", 240)),
            )
        except (TypeError, ValueError):
            return max(240, self.reel_narration_words)

    @property
    def strip_audio_tags_before_tts(self) -> bool:
        """Strip comedy bracket tags before ElevenLabs TTS (page-configurable)."""
        if "STRIP_AUDIO_TAGS_BEFORE_TTS" in self.page_cfg:
            return bool(self.page_cfg.get("STRIP_AUDIO_TAGS_BEFORE_TTS"))
        return False

    @property
    def tts_enable_ssml(self) -> bool:
        """Enable ElevenLabs SSML parsing (``<break time="…" />``) when True."""
        if "TTS_ENABLE_SSML" in self.page_cfg:
            return bool(self.page_cfg.get("TTS_ENABLE_SSML"))
        return (self.page_id or "").lower() == "master_mei"

    @property
    def reel_tail_pad_s(self) -> float:
        """
        Extra seconds appended after final audio so CTA/subtitles never clip.
        Sourced from REEL_TAIL_PAD_S; defaults to 1.0 (ancient_knowledge buffer).
        """
        try:
            return max(0.0, float(self.page_cfg.get("REEL_TAIL_PAD_S", 1.0)))
        except (TypeError, ValueError):
            return 1.0

    @property
    def ambient_audio_path(self) -> "Path | None":
        """
        Optional local ambient loop preferred over ElevenLabs SFX.
        Sourced from AMBIENT_AUDIO_RELPATH in page_config.py (relative to engine root).
        """
        rel = str(self.page_cfg.get("AMBIENT_AUDIO_RELPATH", "")).strip()
        if not rel:
            return None
        candidate = _ENGINE_ROOT / rel
        return candidate if candidate.is_file() else candidate  # path for existence check by caller

    @property
    def avatar_assets_dir(self) -> "Path | None":
        """
        Priority directory of cycleable avatar reference portraits.
        Sourced from AVATAR_ASSETS_DIR in page_config.py (relative to engine root).
        Falls back to pages_config/{page}/avatar_reference/ when unset.
        """
        rel = str(self.page_cfg.get("AVATAR_ASSETS_DIR", "")).strip()
        if rel:
            return _ENGINE_ROOT / rel
        return self.avatar_reference_dir

    def list_avatar_references(self) -> list:
        """
        Return sorted portrait paths for likeness cycling.
        Priority: AVATAR_ASSETS_DIR images, then pages_config avatar_reference/.
        """
        found: list = []
        seen: set = set()
        for folder in (self.avatar_assets_dir, self.avatar_reference_dir):
            if folder is None or not folder.is_dir():
                continue
            for ext in ("*.png", "*.jpg", "*.jpeg", "*.webp"):
                for p in sorted(folder.glob(ext)):
                    if p.is_file() and p.resolve() not in seen:
                        seen.add(p.resolve())
                        found.append(p)
        return found

    def cycle_avatar_reference(self, index: int = 0) -> "Path | None":
        """Pick avatar reference by index (wraps). None if no portraits exist."""
        refs = self.list_avatar_references()
        if not refs:
            return None
        return refs[index % len(refs)]

    @property
    def tts_narration_speed(self) -> float:
        """
        ElevenLabs narration speed multiplier.
        Prefer ELEVENLABS_VOICE_SETTINGS['speed'], else TTS_NARRATION_SPEED.
        Clamped to ElevenLabs practical range ~0.70–1.20 for short-form.
        """
        try:
            vs = self.page_cfg.get("ELEVENLABS_VOICE_SETTINGS", None)
            if isinstance(vs, dict) and vs.get("speed") is not None:
                val = float(vs["speed"])
            else:
                val = float(self.page_cfg.get("TTS_NARRATION_SPEED", 1.05))
            return max(0.70, min(1.20, val))
        except (TypeError, ValueError):
            return 1.05

    @property
    def elevenlabs_voice_settings(self) -> dict:
        """
        Optional ElevenLabs VoiceSettings overrides from page_config.
        Sourced from ELEVENLABS_VOICE_SETTINGS; empty dict = engine defaults.
        Keys: stability, similarity_boost, style, use_speaker_boost, speed.
        """
        raw = self.page_cfg.get("ELEVENLABS_VOICE_SETTINGS", None)
        if not isinstance(raw, dict):
            return {}
        out: dict = {}
        for key in ("stability", "similarity_boost", "style", "speed"):
            if key in raw:
                try:
                    out[key] = float(raw[key])
                except (TypeError, ValueError):
                    pass
        if "use_speaker_boost" in raw:
            out["use_speaker_boost"] = bool(raw["use_speaker_boost"])
        # Keep speed aligned with TTS_NARRATION_SPEED when only one is set
        if "speed" not in out:
            try:
                out["speed"] = float(self.page_cfg.get("TTS_NARRATION_SPEED", 1.05))
            except (TypeError, ValueError):
                pass
        return out

    @property
    def audio_behavior_tags(self) -> list:
        """
        Bracketed ElevenLabs behavioral tags for this page's voice
        (e.g. [chuckles], [cackles]). Sourced from AUDIO_BEHAVIOR_TAGS.
        """
        raw = self.page_cfg.get("AUDIO_BEHAVIOR_TAGS", [])
        if not isinstance(raw, list):
            return []
        return [str(t).strip() for t in raw if t and str(t).strip()]

    @property
    def image_model_override(self) -> "str | None":
        """
        Explicit image model ID override sourced from IMAGE_MODEL_OVERRIDE in
        page_config.py.  When set, this takes highest priority in main.py's
        img_model_id resolution — overrides both the nano-tier constant and the
        global economic flag.  Returns None when not configured.
        Invalid flash-lite SKUs are remapped to a live Imagen model.
        """
        val = self.page_cfg.get("IMAGE_MODEL_OVERRIDE", None)
        if not val:
            return None
        raw = str(val).strip()
        if not raw:
            return None
        try:
            import config as _cfg
            return _cfg.normalize_image_model_id(raw)
        except Exception:  # noqa: BLE001
            return raw

    @property
    def prompt_negative_terms(self) -> list:
        """
        List of words / phrases to strip from inherited atmosphere prompts.
        Used to prevent cross-page style contamination (e.g. 'graphite' leaking
        into ancient_knowledge's photorealistic prompts).
        Sourced from PROMPT_NEGATIVE_TERMS in page_config.py; defaults to [].
        """
        raw = self.page_cfg.get("PROMPT_NEGATIVE_TERMS", [])
        if not isinstance(raw, list):
            return []
        return [str(t).strip() for t in raw if t and str(t).strip()]

    @property
    def page_economic_brain_mode(self) -> "bool | None":
        """
        Page-level override for economic brain mode.
        Returns True / False if ECONOMIC_BRAIN_MODE is explicitly set in
        page_config.py, otherwise None (no override — CLI flag decides).
        """
        val = self.page_cfg.get("ECONOMIC_BRAIN_MODE", None)
        if val is None:
            return None
        return bool(val)


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

def load_page_context(
    page_id: str,
    avatar_mode: str = "ON",
    post_format: str = "IMAGE_AVATAR",
) -> PageContext:
    """
    Build and return a PageContext for the given page slug.

    Validates all three runtime flags and resolves filesystem paths.
    Loads page_config.py from pages_config/{page_id}/ if present.

    Parameters
    ----------
    page_id:
        One of: anna_protocol, master_mei, wonder_feed, down_dirty.
    avatar_mode:
        'ON' or 'OFF'.
    post_format:
        'IMAGE_AVATAR', 'IMAGE_QUOTE', or 'HYBRID_VIDEO'.

    Raises
    ------
    ValueError
        If any of the three arguments are not in their valid sets.
    """
    page_id = page_id.lower().strip()
    avatar_mode = avatar_mode.upper().strip()
    post_format = post_format.upper().strip()

    if page_id not in VALID_PAGES:
        raise ValueError(
            f"Unknown --page '{page_id}'. "
            f"Valid options: {', '.join(VALID_PAGES)}"
        )
    if avatar_mode not in VALID_AVATAR_MODES:
        raise ValueError(
            f"Unknown --avatar '{avatar_mode}'. "
            f"Valid options: {', '.join(VALID_AVATAR_MODES)}"
        )
    if post_format not in VALID_FORMATS:
        raise ValueError(
            f"Unknown --format '{post_format}'. "
            f"Valid options: {', '.join(VALID_FORMATS)}"
        )

    page_dir = _PAGES_CONFIG_ROOT / page_id
    outputs_dir = _ENGINE_ROOT / "outputs" / page_id

    # Ensure outputs directory exists at load time.
    (outputs_dir / "assets").mkdir(parents=True, exist_ok=True)
    (outputs_dir / "library").mkdir(parents=True, exist_ok=True)
    (outputs_dir / "postplanner").mkdir(parents=True, exist_ok=True)

    # Ensure brand asset subfolders exist inside the page config directory.
    (page_dir / "avatar_reference").mkdir(parents=True, exist_ok=True)
    (page_dir / "logo").mkdir(parents=True, exist_ok=True)

    page_cfg = _load_page_config(page_dir, page_id)

    return PageContext(
        page_id=page_id,
        avatar_mode=avatar_mode,
        post_format=post_format,
        page_dir=page_dir,
        persona_dna_path=page_dir / "persona_dna.py",
        master_dna_path=page_dir / "master_dna.json",
        avatar_reference_dir=page_dir / "avatar_reference",
        logo_dir=page_dir / "logo",
        product_reference_dir=page_dir / "product_reference",
        outputs_dir=outputs_dir,
        page_cfg=page_cfg,
    )


def _load_page_config(page_dir: Path, page_id: str) -> dict[str, Any]:
    """
    Dynamically import pages_config/{page_id}/page_config.py and return
    its public symbols as a plain dict. Returns empty dict if file is absent.
    """
    config_py = page_dir / "page_config.py"
    if not config_py.is_file():
        return {}

    module_name = f"pages_config.{page_id}.page_config"
    spec = importlib.util.spec_from_file_location(module_name, config_py)
    if spec is None or spec.loader is None:
        return {}

    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    try:
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
    except Exception as exc:  # noqa: BLE001
        import logging
        logging.getLogger(__name__).warning(
            "Failed to load page_config for '%s': %s", page_id, exc
        )
        return {}

    return {k: v for k, v in vars(mod).items() if not k.startswith("_")}


# ---------------------------------------------------------------------------
# Default avatar mode resolution (per-page preference)
# ---------------------------------------------------------------------------

def resolve_default_avatar_mode(page_cfg: dict[str, Any]) -> str:
    """Return the page's preferred avatar mode ('ON' or 'OFF')."""
    raw = str(page_cfg.get("DEFAULT_AVATAR_MODE", "ON")).upper().strip()
    return raw if raw in VALID_AVATAR_MODES else "ON"


def resolve_default_format(page_cfg: dict[str, Any]) -> str:
    """Return the page's preferred post format."""
    raw = str(page_cfg.get("DEFAULT_FORMAT", "IMAGE_AVATAR")).upper().strip()
    return raw if raw in VALID_FORMATS else "IMAGE_AVATAR"

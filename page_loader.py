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
  master_mei      Stoic discipline, cold exposure, performance protocols, avatar ON
  wonder_feed     Emotional intelligence, attachment science, avatar OFF (default)
  down_dirty      Matrix escape, financial sovereignty, raw mindset, avatar OFF (default)
  ancient_knowledge  Ancient history, conspiracies, mysteries, photorealistic style, avatar OFF

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
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VALID_PAGES: tuple[str, ...] = (
    "anna_protocol",
    "master_mei",
    "wonder_feed",
    "down_dirty",
    "ancient_knowledge",
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
        Logo width as a fraction of canvas width (e.g. 0.15 = 15 %).
        Sourced from LOGO_SIZE_SCALE in page_config.py; defaults to 0.18.
        """
        raw = self.page_cfg.get("LOGO_SIZE_SCALE", 0.18)
        try:
            val = float(raw)
            return max(0.05, min(val, 0.50))   # clamp to 5–50 %
        except (TypeError, ValueError):
            return 0.18

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
        Number of distinct images generated and stitched in a SEQUENCE_REEL.
        Sourced from REEL_IMAGE_COUNT in page_config.py; defaults to 4.
        """
        try:
            return max(2, int(self.page_cfg.get("REEL_IMAGE_COUNT", 4)))
        except (TypeError, ValueError):
            return 4

    @property
    def reel_act_duration(self) -> float:
        """
        Per-act clip length in seconds used when no audio drives the timeline.
        Sourced from REEL_ACT_DURATION in page_config.py; defaults to 20.0.
        Total reel = reel_image_count × reel_act_duration.
        """
        try:
            return max(5.0, float(self.page_cfg.get("REEL_ACT_DURATION", 20.0)))
        except (TypeError, ValueError):
            return 20.0

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
    def niche_disclaimer(self) -> str:
        """
        Optional niche-specific disclaimer injected into LLM system prompts.
        Sourced from NICHE_DISCLAIMER in page_config.py; defaults to empty string.
        """
        return str(self.page_cfg.get("NICHE_DISCLAIMER", "")).strip()

    @property
    def image_model_override(self) -> "str | None":
        """
        Explicit image model ID override sourced from IMAGE_MODEL_OVERRIDE in
        page_config.py.  When set, this takes highest priority in main.py's
        img_model_id resolution — overrides both the nano-tier constant and the
        global economic flag.  Returns None when not configured.
        """
        val = self.page_cfg.get("IMAGE_MODEL_OVERRIDE", None)
        return str(val).strip() or None if val else None

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

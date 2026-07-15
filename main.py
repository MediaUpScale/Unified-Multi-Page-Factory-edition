# -*- coding: utf-8 -*-
from __future__ import annotations

import sys
from pathlib import Path


def _repair_drive_text_file(file_path: Path) -> bool:
    """
    Re-encode a UTF-16 file (Google Drive desktop corruption) as clean UTF-8,
    or strip stray NUL bytes introduced by sync.

    Handles:
    - UTF-16-LE / UTF-16-BE with explicit BOM
    - UTF-16-LE without BOM (Python files starting '#\x00', JSON files starting '{\x00')
    - Files with embedded NUL bytes but otherwise valid UTF-8
    """
    if not file_path.is_file():
        return False
    raw = file_path.read_bytes()

    text = ""
    if raw.startswith(b"\xff\xfe"):
        text = raw[2:].decode("utf-16-le")
    elif raw.startswith(b"\xfe\xff"):
        text = raw[2:].decode("utf-16-be")
    elif raw.startswith((b"#\x00 ", b"#\x00-")):
        text = raw.decode("utf-16-le")
    elif raw.startswith(b"{\x00") or raw.startswith(b"[\x00"):
        text = raw.decode("utf-16-le")

    if text:
        file_path.write_text(text, encoding="utf-8", newline="\n")
        return True

    if b"\x00" not in raw:
        return False
    file_path.write_bytes(raw.replace(b"\x00", b""))
    return True


def _clean_all_python_sources(engine_root: Path) -> None:
    """
    Glob every .py and .json in the project and repair Drive-sync encoding issues.
    Runs silently at bootstrap before any imports that touch persona/config files.
    """
    for pattern in ("*.py", "*.json"):
        for candidate in sorted(engine_root.rglob(pattern)):
            if _repair_drive_text_file(candidate):
                try:
                    rel = candidate.relative_to(engine_root)
                except ValueError:
                    rel = candidate
                print(f"[bootstrap] Repaired {rel}", file=sys.stderr)


_ENGINE_ROOT_BOOT = Path(__file__).resolve().parent
_clean_all_python_sources(_ENGINE_ROOT_BOOT)


# ---------------------------------------------------------------------------
# Pre-parse --page from sys.argv BEFORE any module-level import so that
# config.py and persona_dna.py resolve the correct page paths at import time.
# ---------------------------------------------------------------------------

def _preparse_active_page() -> str:
    """
    Extract --page value from sys.argv without full argparse.
    Sets ACTIVE_PAGE in the environment so all subsequent module imports
    (config, persona_dna) resolve the correct page-specific paths.
    Returns the page slug for informational logging.
    """
    import os
    page = "anna_protocol"  # default
    argv = sys.argv[1:]
    for i, arg in enumerate(argv):
        if arg == "--page" and i + 1 < len(argv):
            page = argv[i + 1].lower().strip()
            break
        if arg.startswith("--page="):
            page = arg.split("=", 1)[1].lower().strip()
            break
    os.environ["ACTIVE_PAGE"] = page
    return page


_PRELOADED_PAGE = _preparse_active_page()


# ---------------------------------------------------------------------------
# Bind .env to os.environ BEFORE any third-party or engine import fires.
# Uses an absolute path anchored to this file so it works regardless of the
# current working directory (Drive mounts, subprocess launches, etc.).
# override=True ensures values in .env win over any stale shell-level vars.
# ---------------------------------------------------------------------------
from dotenv import load_dotenv as _load_dotenv  # noqa: E402

_load_dotenv(Path(__file__).resolve().parent / ".env", override=True, encoding="utf-8-sig")


# ---------------------------------------------------------------------------
# Standard imports (after ACTIVE_PAGE is set and .env is loaded)
# ---------------------------------------------------------------------------

import argparse
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any

from google import genai

import config as app_config
from avatar_engine.caption_engine import (
    CaptionEngine,
    build_gemini_researcher_instruction,
    economic_humanizer_instruction_preview,
    humanizer_preview_with_placeholder,
    build_batch_researcher_instruction,
    build_smart_bait_image_prompt,
    maybe_inject_horror_mutation,
)
from avatar_engine.b2_client import B2VideoUploader
from avatar_engine.imgbb_client import upload_image_file_to_imgbb
from avatar_engine.audio_engine import (
    generate_voiceover,
    generate_voiceover_with_timestamps,
    generate_ambient_track,
)
from avatar_engine.video_engine import compile_dynamic_reel
from avatar_engine.brand_composer import (
    apply_text_overlay as _brand_apply_text,
    apply_logo_watermark as _brand_apply_logo,
    burn_text_on_video as _brand_burn_video,
    generate_text_quote_background as _brand_text_quote_bg,
)
from avatar_engine.content_library import (
    append_entry,
    build_library_metadata,
    dump_raw_research_to_log,
)
from avatar_engine.durable_library import (
    PENDING_CAPTION,
    merge_update_json,
    path_under_engine,
    write_atomic_json,
)
from avatar_engine.knowledge.pdf_loader import list_pdf_relative_paths, load_digital_product_corpus
from avatar_engine.post_planner import (
    append_planner_row,
    append_postplanner_xlsx_row,
    scheduled_bulk_post_display,
    update_planner_row,
)
from avatar_engine.providers.gemini_utils import build_model_chain, get_latest_model
from avatar_engine.persona_dna import contextual_cta_keyword
from avatar_engine.providers.image_provider import GeminiImageAdapter
from avatar_engine.subject_brain import imagine_subject, imagine_subject_instruction_preview
from avatar_engine.text_utils import subject_slug
from avatar_engine.visual_architect import VisualArchitect
from page_loader import (
    PageContext,
    load_page_context,
    resolve_default_avatar_mode,
    resolve_default_format,
    VALID_PAGES,
    VALID_AVATAR_MODES,
    VALID_FORMATS,
)
from run_ledger import (
    PlannedModels,
    activate_run_ledger,
    configure_file_logging,
    ledger_file_path,
)


_LOG = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# ENGAGEMENT BAIT LOADER
# Reads historical high-engagement quote hooks from the wonder_feed reference
# spreadsheet and returns them as a formatted block the LLM can learn from.
# ---------------------------------------------------------------------------
def _load_engagement_bait_examples(page_ctx: "Any | None" = None) -> str:
    """
    Read '@REDES REF SOURCE FONTE.xlsx' and return up to 30 viral bait hooks
    sampled from the three highest-signal sheets as a formatted reference block.

    Returns an empty string silently if pandas is missing or the file is absent.
    """
    try:
        import pandas as _pd  # noqa: PLC0415
        import random as _rnd_xl  # noqa: PLC0415
    except ImportError:
        return ""

    _XLSX_REL = (
        "pages_config/wonder_feed/Quotes_reference/@REDES REF SOURCE FONTE.xlsx"
    )
    try:
        from pathlib import Path as _P  # noqa: PLC0415
        _xl_abs = _P(__file__).parent / _XLSX_REL
        if not _xl_abs.exists():
            _LOG.warning("Engagement bait spreadsheet not found: %s", _xl_abs)
            return ""

        _xl = _pd.ExcelFile(str(_xl_abs))
        _SHEETS = ["source quotes", "Quotes 26", "Randomize Quote"]
        _rows: list[str] = []
        for _sname in _SHEETS:
            if _sname not in _xl.sheet_names:
                continue
            _df = _pd.read_excel(_xl, sheet_name=_sname)
            _col = _df.iloc[:, 0].dropna().astype(str)
            # Keep only text rows (skip URLs, single-word cells)
            for _v in _col:
                _v = _v.strip()
                if (
                    len(_v) > 20
                    and not _v.startswith("http")
                    and "\n" not in _v[:10]
                ):
                    _rows.append(_v)

        if not _rows:
            return ""

        # Sample up to 30 unique examples, shuffled so every run gets variety
        _sample = _rnd_xl.sample(_rows, min(30, len(_rows)))
        _block = "\n".join(f"  - {r}" for r in _sample)
        _LOG.info(
            "Engagement bait loaded: %d examples from %s",
            len(_sample),
            _XLSX_REL,
        )
        return _block
    except Exception as _exc:  # noqa: BLE001
        _LOG.warning("_load_engagement_bait_examples failed (%s) — skipping.", _exc)
        return ""
    """Humanizer-offline path: short header + raw fact sheet body."""
    body = raw_sheet.strip() if isinstance(raw_sheet, str) else ""
    if not body:
        return ""
    return "[Caption from researcher output - humanizer skipped]" + chr(10) + chr(10) + body


def _silence_noisy_http_loggers() -> None:
    """Quiet httpx / anthropic / google SDK chatter without muting ``__main__`` run logs."""
    for name in (
        "anthropic",
        "httpx",
        "google",
        "httpcore",
        "google.genai",
        "google_genai",
        "google.auth",
        "google.cloud",
        "google.api_core",
        "urllib3",
    ):
        logging.getLogger(name).setLevel(logging.WARNING)


def _looks_like_upstream_api_failure(exc: BaseException) -> bool:
    """Heuristic: Anthropic / Gemini transport & API errors."""
    mod = getattr(type(exc), "__module__", "") or ""
    if mod.startswith("anthropic"):
        return True
    if "anthropic." in mod:
        return True
    if "google.genai" in mod:
        return True
    return mod.startswith("httpx.") or exc.__class__.__name__.endswith("HTTPError")


def _color(text: str, code: str) -> str:
    if not getattr(sys.stdout, "isatty", lambda: False)():
        return text
    return f"{code}{text}\033[0m"


def _emit_clean_api_error(exc: BaseException) -> None:
    red = "\033[91m"
    dim = "\033[2m"
    reset = "\033[0m"
    title = type(exc).__name__
    body = getattr(exc, "message", None) or str(exc).strip() or repr(exc)
    print()
    print(_color("API request failed", red))
    print(_color(title, red))
    snippet = body[:2000] + ("\u2026" if len(body) > 2000 else "")
    if not snippet:
        snippet = repr(exc)
    print(dim + snippet + reset)
    print(dim + "Detail: see logs/run_*.log in this project." + reset)
    print()


def _print_production_summary(
    envelope: dict[str, Any],
    page_ctx: PageContext | None = None,
) -> None:
    """End-of-run: image paths and captions surfaced for scheduling / paste."""
    green = "\033[92m"
    cyan = "\033[96m"
    yellow = "\033[93m"
    sep = "+" + "=" * 62 + "+"
    topic_display = envelope.get("resolved_subject") or "(subject)"
    rows = envelope.get("items") or []
    if not isinstance(rows, list):
        rows = []

    print()
    print(_color(sep, green))
    if page_ctx:
        print(_color(f"| PAGE: {page_ctx.display_name:<55}|", cyan))
        print(_color(
            f"| AVATAR: {page_ctx.avatar_mode:<8} FORMAT: {page_ctx.post_format:<43}|", cyan
        ))
    print(_color("| PRODUCTION SUMMARY                                            |", cyan))
    print(_color(sep, green))
    print(_color("Topic:", green), topic_display)
    print()

    if not rows:
        print(_color("(no artifact rows emitted)", cyan))
        print(_color(sep, green))
        print()
        return

    for row in rows:
        cap = row.get("caption") or ""
        img = row.get("local_image_path") or row.get("image_path") or ""
        bb = row.get("imgbb_url") or ""
        mode = row.get("caption_mode", "humanized")
        video = row.get("video_path") or ""
        print(_color(sep, green))
        print(_color(f"Variant {row.get('variant_index', '?')}", cyan))
        if mode == "researcher_fallback":
            print(_color("  Note: Caption is raw researcher output (humanizer failed).", yellow))
        print(_color("  Image path:", green), img or "(skipped)")
        if video:
            print(_color("  Video path:", green), video)
        if bb:
            print(_color("  ImgBB URL:", green), bb)
        print(_color("  Caption:", green))
        for line in str(cap).splitlines() or ["(empty)"]:
            print(" ", line)
    print(_color(sep, green))
    xlsx_rel = path_under_engine(app_config.ENGINE_ROOT, app_config.POST_PLANNER_XLSX)
    lib_hint = ""
    first = rows[0] if rows else {}
    if isinstance(first, dict) and first.get("library_json_relative"):
        lib_hint = str(first["library_json_relative"])
    print(_color("Records:", green), f"bulk workbook `{xlsx_rel}`" + (f"; library `{lib_hint}`" if lib_hint else ""))
    print()


def _snapshot_verified_models(*, economic_brain_mode: bool) -> PlannedModels:
    """Determine first-hop model IDs (matches CaptionEngine/GeminiImageAdapter chain heads)."""
    gem_key = app_config.GEMINI_API_KEY
    humanizer = (
        f"Gemini `{app_config.GEMINI_ECONOMIC_BRAIN_MODEL}` (captions + research)"
        if economic_brain_mode
        else f"Anthropic Claude `{app_config.CLAUDE_MODEL}`"
    )
    # Economic mode strictly forces the cheaper image model tier.
    img_pref = (
        app_config.GEMINI_ECONOMIC_IMAGE_MODEL
        if economic_brain_mode
        else app_config.GEMINI_IMAGE_MODEL
    )
    if not gem_key:
        research_pref = (
            app_config.GEMINI_ECONOMIC_BRAIN_MODEL
            if economic_brain_mode
            else app_config.GEMINI_RESEARCH_MODEL
        )
        return PlannedModels(
            image_primary_id=img_pref,
            research_primary_id=research_pref,
            humanizer_summary=humanizer,
        )

    client = genai.Client(api_key=gem_key)
    img_chain = build_model_chain(client, capability_type="image", preferred=img_pref)
    research_pref = (
        app_config.GEMINI_ECONOMIC_BRAIN_MODEL
        if economic_brain_mode
        else app_config.GEMINI_RESEARCH_MODEL
    )
    txt_chain = build_model_chain(client, capability_type="text", preferred=research_pref)

    verified_image   = img_chain[0] if img_chain else img_pref
    verified_research = txt_chain[0]

    discovery_img = get_latest_model(client, kind="image")
    discovery_txt = get_latest_model(client, kind="text")
    _LOG.info(
        "Gemini discovery | strongest image SKU (or safe default) = `%s`; text = `%s`",
        discovery_img,
        discovery_txt,
    )

    # In economic mode hard-clamp to the configured cheap image model regardless
    # of what the discovery chain promoted.
    if economic_brain_mode:
        verified_image = img_pref
        _LOG.info(
            "Economic mode | image model clamped to `%s` (ignoring premium chain head `%s`)",
            img_pref,
            img_chain[0] if img_chain else "n/a",
        )

    return PlannedModels(
        image_primary_id=verified_image,
        research_primary_id=verified_research,
        humanizer_summary=humanizer,
    )


def _bootstrap_pipeline_intro(
    *,
    economic_brain_mode: bool,
    verified: PlannedModels,
    compact: bool = False,
    page_ctx: PageContext | None = None,
) -> None:
    """Confirm persona, credentials, likeness, routing, and verified model IDs."""
    app_config.print_dotenv_bootstrap()

    if page_ctx:
        print(f"[bootstrap] Active page:    {page_ctx.display_name} ({page_ctx.page_id})")
        print(f"[bootstrap] Avatar mode:    {page_ctx.avatar_mode}")
        print(f"[bootstrap] Post format:    {page_ctx.post_format}")

    gem_ok = bool(app_config.GEMINI_API_KEY and str(app_config.GEMINI_API_KEY).strip())
    claude_ok = bool(app_config.ANTHROPIC_API_KEY and str(app_config.ANTHROPIC_API_KEY).strip())

    print(f"[bootstrap] Gemini API Key detected: {'Yes' if gem_ok else 'No'}")
    print(f"[bootstrap] Claude API Key detected: {'Yes' if claude_ok else 'No'}")
    print(
        "[bootstrap] Gemini image pipeline:",
        f"{app_config.GEMINI_ECONOMIC_IMAGE_MODEL if economic_brain_mode else app_config.GEMINI_IMAGE_MODEL}",
        "(economic)" if economic_brain_mode else "(premium)",
        "(aspect",
        f"{app_config.GEMINI_IMAGE_ASPECT_RATIO})",
    )
    if economic_brain_mode:
        print(
            "[bootstrap] Economic brain Gemini preference (research + humanizer):",
            app_config.GEMINI_ECONOMIC_BRAIN_MODEL,
            "(fallback chain rotates on 404; see GEMINI_ALERT log lines)",
        )
    else:
        print(
            "[bootstrap] Premium relay | Gemini researcher preference:",
            app_config.GEMINI_RESEARCH_MODEL,
            "| Claude humanizer:",
            app_config.CLAUDE_MODEL,
        )

    print(f"[bootstrap] Verified Image Model: {verified.image_primary_id}")
    print(f"[bootstrap] Verified Research Model: {verified.research_primary_id}")
    print(f"[bootstrap] Economic brain mode = {economic_brain_mode}")

    if compact:
        avatar_on = page_ctx.avatar_on if page_ctx else True
        if avatar_on and not app_config.reference_avatar_exists():
            print(
                f"[bootstrap] Warning: reference avatar missing at "
                f"{app_config.reference_avatar_resolved_path()} (text-only likeness).",
            )
        return

    dn_path = app_config.PERSONA_DNA_PATH.resolve()
    print(f"[bootstrap] Persona DNA file in use: {dn_path}")
    print(f"[bootstrap] File present on disk: {dn_path.is_file()}")

    avatar_on = page_ctx.avatar_on if page_ctx else True
    if avatar_on:
        canonical = app_config.reference_avatar_resolved_path()
        print("[bootstrap] Likeness reference path:", canonical)
        print(f"[bootstrap] Reference avatar exists: {app_config.reference_avatar_exists()}")
        app_config.warn_if_reference_avatar_missing()
    else:
        print("[bootstrap] Avatar mode OFF — skipping likeness reference; atmospheric imagery only.")


# ---------------------------------------------------------------------------
# HYBRID_VIDEO: import video converter if available
# ---------------------------------------------------------------------------

def _maybe_convert_to_video(
    image_path: Path,
    *,
    duration: int = 7,
    page_ctx: PageContext | None = None,
) -> str:
    """
    Attempt to convert a generated image into a 7-second Ken Burns zoom loop.
    Returns the video path string, or empty string if unavailable / failed.
    Only called when --format HYBRID_VIDEO is active.
    """
    if page_ctx is None or not page_ctx.is_hybrid_video:
        return ""
    try:
        from avatar_engine.video_converter import make_zoom_loop
        video_path = make_zoom_loop(image_path, duration_seconds=duration)
        return str(video_path)
    except ImportError:
        _LOG.warning("video_converter not available; HYBRID_VIDEO skipped.")
        return ""
    except Exception as vexc:  # noqa: BLE001
        _LOG.warning("HYBRID_VIDEO conversion failed: %s", vexc, exc_info=True)
        return ""


# ---------------------------------------------------------------------------
# Per-variant worker — runs in a ThreadPoolExecutor for bulk production
# ---------------------------------------------------------------------------

def _produce_variant_worker(
    variant: int,
    *,
    qty: int,
    slug: str,
    resolved_subject: str,
    corpus: Any,
    pre_narratives: list[str],
    caption_engine: "CaptionEngine | None",
    skip_image: bool,
    skip_caption: bool,
    avatar_mode: str,
    post_format: str,
    atmosphere_style: str,
    page_aspect_ratio: str,
    effective_ref_path: "Path | None",
    economic: bool,
    econ_model: str,
    bm: "PlannedModels",
    page_ctx: "PageContext | None",
    subject_assets: Path,
    run_stamp: str,
    postplanner_dir: Path,
    logs_dir: Path,
    write_lock: threading.Lock,
    cta_enabled: bool = True,
    post_type: str = "STANDARD_QUOTE",
    image_style: str = "NATURAL",
    generated_hooks_cache: "list[str] | None" = None,
    hooks_cache_lock: "threading.Lock | None" = None,
    hooks_cache_path: "Path | None" = None,
) -> "dict[str, Any]":
    """
    Produce one complete post variant inside a worker thread.

    Handles image generation, ImgBB upload, caption research, humanisation,
    and all durable artefact writes for variant ``variant`` (0-based index).

    File writes that touch shared resources (Excel workbooks,
    content_library.json) are serialised through ``write_lock`` so concurrent
    workers never corrupt each other's output.

    Raises on fatal errors so the ThreadPoolExecutor caller can catch them
    per-future; for qty==1 the exception propagates naturally to cli().
    """
    stem = f"{slug}_v{variant + 1:02d}"
    variation_index = variant

    cta_kw = contextual_cta_keyword(resolved_subject)
    humanizer_notes = bm.humanizer_summary

    caption: str = "(skipped)"
    raw_sheet: str = "(skipped)"
    img_path_display: Path | str = "(skipped)"
    caption_mode_tag: str | None = None
    durable_abs: Path | None = None
    planner_row_ix: int | None = None
    video_path_str: str = ""

    # ====================================================================
    # PHASE B1: Caption types that run BEFORE image generation
    #   SMART_BAIT    — overlay_text drives the image prompt atmosphere
    #   LONG_CAPTION_IMAGE — caption is generated here; image uses illustration_style
    # ====================================================================
    overlay_text: str = ""
    visual_subject: str = ""  # LLM-authored scene description for image generation

    if post_type in ("SMART_BAIT", "ECONOMIC_REEL") and not skip_caption:
        assert caption_engine is not None
        try:
            # Load engagement bait examples from the wonder_feed reference spreadsheet
            # so the LLM can analyse the psychological engagement patterns without
            # copying verbatim.  Returns "" silently if pandas / file is unavailable.
            _bait_examples = (
                _load_engagement_bait_examples(page_ctx)
                if page_ctx and (page_ctx.page_id or "").lower() == "wonder_feed"
                else ""
            )
            # Snapshot the cache at call time so all workers see consistent history.
            _hooks_snapshot: list[str] = list(generated_hooks_cache or [])
            overlay_text, caption, caption_mode_tag, visual_subject = caption_engine.humanize_smart_bait(
                resolved_subject,
                page_display_name=page_ctx.display_name if page_ctx else "",
                page_niche=page_ctx.content_niche if page_ctx else "",
                cta_enabled=cta_enabled,
                economic=economic,
                model_id=econ_model if economic else None,
                post_type=post_type,
                engagement_bait_examples=_bait_examples,
                previously_generated_hooks=_hooks_snapshot,
            )
        except Exception as exc:  # noqa: BLE001
            _LOG.error("Smart bait generation failed variant %s: %s", variant + 1, exc, exc_info=True)
            logging.error("VARIANT_FAIL | smart_bait | variant=%s | err=%s", variant + 1, exc)
            raise

        # Persist the new hook to the cache so the next variant / run avoids it.
        if overlay_text and hooks_cache_lock is not None and generated_hooks_cache is not None:
            with hooks_cache_lock:
                if overlay_text not in generated_hooks_cache:
                    generated_hooks_cache.append(overlay_text)
                    # Keep the file-based store bounded to the last 60 hooks.
                    if hooks_cache_path is not None:
                        try:
                            import json as _jw
                            _to_save = generated_hooks_cache[-60:]
                            hooks_cache_path.parent.mkdir(parents=True, exist_ok=True)
                            hooks_cache_path.write_text(
                                _jw.dumps(_to_save, ensure_ascii=False, indent=2),
                                encoding="utf-8",
                            )
                        except Exception as _hwe:  # noqa: BLE001
                            _LOG.warning("Could not persist hooks cache (%s).", _hwe)

        if caption_mode_tag == "researcher_fallback" or (not overlay_text and not caption):
            _LOG.warning(
                "Smart bait generation yielded no usable content for variant %s of '%s'.",
                variant + 1, resolved_subject,
            )

    elif post_type == "LONG_CAPTION_IMAGE" and not skip_caption:
        assert caption_engine is not None
        try:
            caption, caption_mode_tag = caption_engine.humanize_long_caption(
                resolved_subject,
                page_display_name=page_ctx.display_name if page_ctx else "",
                page_niche=page_ctx.content_niche if page_ctx else "",
                cta_enabled=cta_enabled,
                economic=economic,
                model_id=econ_model if economic else None,
            )
        except Exception as exc:  # noqa: BLE001
            _LOG.error("Long caption generation failed variant %s: %s", variant + 1, exc, exc_info=True)
            logging.error("VARIANT_FAIL | long_caption | variant=%s | err=%s", variant + 1, exc)
            raise

        if not caption:
            _LOG.warning(
                "Long caption generation yielded no content for variant %s of '%s'.",
                variant + 1, resolved_subject,
            )

    # ====================================================================
    # MASTER STYLE ROUTING GATE
    # For wonder_feed (and any page with BASE_GRAPHITE_PROMPT configured),
    # SMART_BAIT / LONG_CAPTION_IMAGE / ECONOMIC_REEL are hard-locked to the
    # graphite illustration pipeline via page_ctx.base_graphite_prompt.
    # The --draw-style CLI flag is SILENTLY IGNORED for these three post types.
    # ====================================================================
    _graphite_locked = (
        post_type in ("SMART_BAIT", "LONG_CAPTION_IMAGE", "ECONOMIC_REEL")
        and (
            bool(page_ctx and (page_ctx.base_graphite_prompt or page_ctx.sketch_style_prompt))
            or image_style == "SKETCH"   # --draw-style SKETCH always forces graphite pipeline
        )
    )
    if _graphite_locked:
        _LOG.debug(
            "%s | Graphite style lock active (image_style=%s). "
            "Pipeline: BASE_GRAPHITE_PROMPT → build_smart_bait_image_prompt().",
            post_type, image_style,
        )

    effective_atmosphere = atmosphere_style
    if post_type in ("SMART_BAIT", "ECONOMIC_REEL") and overlay_text and caption_engine is not None:
        # visual_subject from LLM takes priority; fall back to keyword extraction
        scene = visual_subject or caption_engine.extract_smart_bait_image_theme(
            overlay_text, economic=economic
        )
        if scene:
            _base_prompt = (
                page_ctx.base_graphite_prompt or page_ctx.sketch_style_prompt
                if page_ctx else ""
            )
            # --draw-style SKETCH override: when no page-level graphite prompt is
            # configured, use the hardcoded sketch directive so the CLI flag
            # always routes Gemini into the charcoal illustration pipeline.
            if not _base_prompt and image_style == "SKETCH":
                _base_prompt = (
                    "in the style of a detailed, emotional charcoal pencil sketch illustration, "
                    "monochrome cross-hatching, moody atmosphere, dark vignette, "
                    "soft horror style couple with expressionless porcelain masks"
                )
            # 35% chance: seamlessly inject one biomechanical horror mutation
            # into the scene description before it reaches the image generator.
            scene = maybe_inject_horror_mutation(scene, probability=0.35)
            effective_atmosphere = build_smart_bait_image_prompt(scene, _base_prompt)
            _LOG.debug("%s | scene (post-mutation): %s", post_type, scene[:120])
    elif post_type == "LONG_CAPTION_IMAGE":
        # Mirror SMART_BAIT exactly: same base prompt, same builder function.
        # resolved_subject is the scene concept; the builder appends it to BASE_GRAPHITE_PROMPT.
        _base_prompt = (
            page_ctx.base_graphite_prompt or page_ctx.sketch_style_prompt
            if page_ctx else ""
        )
        if not _base_prompt and image_style == "SKETCH":
            _base_prompt = (
                "in the style of a detailed, emotional charcoal pencil sketch illustration, "
                "monochrome cross-hatching, moody atmosphere, dark vignette, "
                "soft horror style couple with expressionless porcelain masks"
            )
        if _base_prompt:
            # Horror mutation also fires for LONG_CAPTION_IMAGE at 35% probability
            _mutated_subject = maybe_inject_horror_mutation(resolved_subject, probability=0.35)
            effective_atmosphere = build_smart_bait_image_prompt(
                _mutated_subject, _base_prompt
            )
            _LOG.debug("LONG_CAPTION_IMAGE | scene (post-mutation): %s", _mutated_subject[:120])

    # ====================================================================
    # PHASE A: Image generation
    # Economic mode uses the cheaper image model tier.
    # SMART_BAIT forces avatar_mode="OFF" so the atmospheric context
    # derived from the text hook drives the image prompt unconditionally.
    # ====================================================================
    # SMART_BAIT and LONG_CAPTION_IMAGE both need an environmental/illustration
    # background (not an avatar portrait). Force avatar_mode="OFF" locally so the
    # page-level avatar_mode cannot override this.
    image_avatar_mode = "OFF" if post_type in ("SMART_BAIT", "LONG_CAPTION_IMAGE", "ECONOMIC_REEL") else avatar_mode

    # For SMART_BAIT / LONG_CAPTION_IMAGE / ECONOMIC_REEL: if theme extraction
    # returned empty, substitute a vivid non-generic fallback.
    # IMPORTANT: this photorealistic fallback is BLOCKED for graphite-locked pages
    # (e.g. wonder_feed).  Firing it there would route Gemini into a photographic
    # pipeline, overriding the BASE_GRAPHITE_PROMPT — the exact regression seen in
    # reel_what_you_tolerate_speaks_volumes_v01.mp4.
    if (
        post_type in ("SMART_BAIT", "LONG_CAPTION_IMAGE", "ECONOMIC_REEL")
        and effective_atmosphere == atmosphere_style
        and not _graphite_locked
    ):
        _LOG.debug(
            "SMART_BAIT | theme extraction did not override page default — applying vivid abstract fallback."
        )
        effective_atmosphere = (
            "Dramatic, vivid cinematic wide shot. Emotionally charged abstract environment "
            "with bold colours, rich textures, dynamic lighting. Bright and visually striking. "
            "No avatar, no coffee cups, no generic office or desk props."
        )

    # ── HARD SKETCH ENFORCEMENT ──────────────────────────────────────────────
    # Final-pass guardian: for every graphite-locked prompt, unconditionally
    # append the charcoal-sketch style directive so Gemini cannot silently
    # drift into photorealism regardless of what the upstream prompt built.
    _SKETCH_LOCK_SUFFIX = (
        " in the style of a detailed, emotional charcoal pencil sketch illustration, "
        "monochrome cross-hatching, moody atmosphere"
    )
    if _graphite_locked and effective_atmosphere and _SKETCH_LOCK_SUFFIX.strip() not in effective_atmosphere:
        effective_atmosphere = effective_atmosphere.rstrip(" .") + _SKETCH_LOCK_SUFFIX
        _LOG.debug("SKETCH LOCK | enforced sketch suffix on effective_atmosphere.")

    # ── WONDER_FEED HORROR-SKETCH OVERRIDE ───────────────────────────────────
    # This block fires LAST in the prompt assembly chain. It:
    #   1. Strips every photorealistic lifestyle / furniture term that leaked
    #      through from ATMOSPHERE_STYLE or fallback strings.
    #   2. When USE_STYLE_REFERENCE is True, builds the prompt by concatenating
    #      ILLUSTRATION_STYLE + STYLE_CHARACTERS from page_config then appends
    #      the soft horror directive. This guarantees the 3-colour sketch palette
    #      and the couple personas are always present in the Gemini payload.
    if page_ctx and (page_ctx.page_id or "").lower() == "wonder_feed":
        _il  = page_ctx.illustration_style.rstrip(" .") if page_ctx.use_style_reference and page_ctx.illustration_style else (
            "Highly detailed pencil sketch style illustration, but restricted to only 3 colors: "
            "dark azure blue, black, and white. Dramatic lighting, expressive. "
            "Clean, minimalist, no messy details."
        )
        _chr = page_ctx.style_characters.rstrip(" .") if page_ctx.use_style_reference and page_ctx.style_characters else (
            "A realistic man and a woman in an intense, dramatic relationship dynamic. "
            "They are the consistent recurring personas."
        )
        _WF_SOFT_HORROR_VARIANTS = [
            (
                f"{_il}. {_chr}. "
                "The man wears a completely plain, expressionless white porcelain-like mask "
                "over his face, while the woman exhibits an intense, raw, anxious expression. "
                "Full-bleed, edge-to-edge canvas portrait, no borders, no bookshelves, no desks, no notebooks."
            ),
            (
                f"{_il}. {_chr}. "
                "The woman is tethered by a dark, fragmented shadow to a looming, faceless male "
                "silhouette fading into subtle ash particles. "
                "Full-bleed, full-screen canvas portrait, no ambient furniture, no clutter."
            ),
            (
                f"{_il}. {_chr}. "
                "The man stands with an eerie, featureless white mask in a heavy vignette shadow, "
                "while the woman stares blankly at a shattered mirror pool on the floor reflecting "
                "her anxiety. Edge-to-edge full bleed, zero margins, no generic room elements."
            ),
            (
                f"{_il}. {_chr}. "
                "The man's silhouette is partially dissolving into fine charcoal dust at the edges "
                "while the woman reaches toward him with trembling hands. Full-screen portrait, "
                "deep vignette shadows, no domestic objects or interior framing."
            ),
            (
                f"{_il}. {_chr}. "
                "Both figures are in close emotional proximity but the man's face is hidden behind "
                "a smooth white porcelain half-mask; the woman's expression is raw and unguarded. "
                "Full-bleed graphite canvas, no borders, no furniture."
            ),
        ]
        import random as _rnd_var
        _WF_SKETCH_PREFIX = _rnd_var.choice(_WF_SOFT_HORROR_VARIANTS) + " "

        _PHOTO_TERMS = [
            "leather armchair", "armchair", "library", "bookshelf", "coffee cup",
            "notebook", "pen", "wooden table", "wooden desk", "cup of tea", "dried flowers",
            "earthenware", "fairy lights", "terracotta", "blush pink", "warm cream", "35mm film",
            "linen curtains", "journaling", "emotionally safe", "aesthetically cozy",
            "cozy", "lifestyle photography", "morning light", "clean surface",
            "window seat", "blanket", "cozy room", "sketchbook", "page", "borders",
            "desk", "table",
            # Block the generic "no human subjects" environmental fallback
            "CINEMATIC ENVIRONMENTAL PHOTOGRAPHY", "NO HUMAN SUBJECTS",
        ]
        _clean = effective_atmosphere
        for _pt in _PHOTO_TERMS:
            _lo = _clean.lower()
            _idx = _lo.find(_pt.lower())
            while _idx != -1:
                _clean = _clean[:_idx] + _clean[_idx + len(_pt):]
                _lo = _clean.lower()
                _idx = _lo.find(_pt.lower())
        import re as _re_mod
        _clean = _re_mod.sub(r"[ \t]{2,}", " ", _clean).strip(" ,.")
        effective_atmosphere = _WF_SKETCH_PREFIX + _clean
        _LOG.info(
            "wonder_feed PROMPT LOCK | use_style_ref=%s | compiled: %s",
            page_ctx.use_style_reference,
            effective_atmosphere[:160],
        )

    adapter: GeminiImageAdapter | None = None

    # ------------------------------------------------------------------
    # TEXT_QUOTE: skip Gemini entirely — create a brand solid-gradient bg.
    # IMAGE_BACKGROUND / IMAGE_QUOTE / IMAGE_AVATAR: call Gemini as normal.
    # ------------------------------------------------------------------
    _is_text_quote = (post_format == "TEXT_QUOTE") and not skip_image

    if _is_text_quote:
        # Resolve canvas dimensions from aspect ratio
        _ratio = (page_aspect_ratio or app_config.GEMINI_IMAGE_ASPECT_RATIO or "3:4").strip()
        _w, _h = 1080, 1350   # default 4:5 portrait
        try:
            _parts = [int(p) for p in _ratio.replace(":", "/").split("/")]
            if len(_parts) == 2 and _parts[0] > 0 and _parts[1] > 0:
                _w = 1080
                _h = int(1080 * _parts[1] / _parts[0])
        except Exception:  # noqa: BLE001
            pass
        try:
            img_path_display = _brand_text_quote_bg(
                (_w, _h),
                page_id=page_ctx.page_id if page_ctx else None,
                output_path=subject_assets / f"{stem}_tq_bg.png",
            )
            logging.info(
                "Variant %s | TEXT_QUOTE_BG_OK | path=%s", variant + 1, img_path_display
            )
        except Exception as exc:  # noqa: BLE001
            _LOG.error(
                "TEXT_QUOTE background generation failed variant %s: %s",
                variant + 1, exc, exc_info=True,
            )
            raise

    elif not skip_image:
        architect = VisualArchitect()
        image_prompt = architect.build_prompt(
            resolved_subject,
            variation_index=variation_index,
            total_variants=qty,
            avatar_mode=image_avatar_mode,
            atmosphere_style=effective_atmosphere,
            aspect_ratio=page_aspect_ratio or None,
            style=image_style,
        )
        # ── WONDER_FEED STYLE LOCK: hard-replace the final prompt ────────────
        # Uses the exact constants from page_config.py so the 3-colour palette
        # and couple personas are always present — architect output is discarded
        # and replaced with the explicit structure the image engine requires.
        if (
            page_ctx
            and (page_ctx.page_id or "").lower() == "wonder_feed"
            and page_ctx.use_style_reference
        ):
            _wf_il = (
                page_ctx.illustration_style.rstrip(" .")
                if page_ctx.illustration_style
                else (
                    "Highly detailed pencil sketch style illustration, but restricted to only "
                    "3 colors: dark azure blue, black, and white. Dramatic lighting, expressive. "
                    "Clean, minimalist, no messy details."
                )
            )
            _wf_ch = (
                page_ctx.style_characters.rstrip(" .")
                if page_ctx.style_characters
                else (
                    "A realistic man and a woman in an intense, dramatic relationship dynamic. "
                    "They are the consistent recurring personas."
                )
            )
            _scene_desc = (visual_subject or resolved_subject).strip(" .")
            # Sanitize scene description: strip furniture/book/domestic terms
            # so the image engine cannot hallucinate room objects from the text.
            _SCENE_JUNK = [
                "bookshelf", "bookshelves", "bookcase", "shelf", "open book", "open books",
                "coffee cup", "cup of coffee", "coffee table", "armchair", "armchairs",
                "leather chair", "desk", "office desk", "wooden desk", "window", "windowsill",
                "window seat", "library", "reading room", "sofa", "couch", "lamp",
                "notebook", "journal", "pen", "pencil on paper", "table", "kitchen",
                "bedroom", "living room", "curtain", "curtains",
            ]
            import re as _re_scene
            _sd = _scene_desc
            for _jt in _SCENE_JUNK:
                _lo = _sd.lower()
                _ix = _lo.find(_jt.lower())
                while _ix != -1:
                    _sd = _sd[:_ix] + _sd[_ix + len(_jt):]
                    _lo = _sd.lower()
                    _ix = _lo.find(_jt.lower())
            _scene_desc = _re_scene.sub(r"[ \t]{2,}", " ", _sd).strip(" ,.")
            image_prompt = (
                f"{_wf_il}. {_wf_ch}. "
                f"Scene Concept: {_scene_desc}. "
                "CRITICAL COMPLIANCE: Do NOT draw any objects from the text literal. "
                "Absolutely NO bookshelves, NO open books, NO coffee cups, NO desks, "
                "NO armchairs, NO windows. "
                "The frame must only feature the expressive dark azure pencil art couple. "
                "Full-bleed, full-screen canvas."
            )
            _LOG.info(
                "wonder_feed IMAGE PROMPT LOCK | hard-override applied | scene: %s",
                _scene_desc[:80],
            )
        img_model_id = app_config.GEMINI_ECONOMIC_IMAGE_MODEL if economic else None
        if economic:
            _LOG.info(
                "ECONOMIC LOCK | variant=%s | image_model=%s | text_brain=%s",
                variant + 1,
                app_config.GEMINI_ECONOMIC_IMAGE_MODEL,
                "DeepSeek" if (caption_engine is not None and getattr(caption_engine, "_deepseek", None)) else app_config.GEMINI_ECONOMIC_BRAIN_MODEL,
            )
        # Resolve style reference image.
        # For wonder_feed the reference file is pinned to an absolute path so
        # that STYLE_REFERENCE_DIR misconfigs or directory-scan order can never
        # cause the wrong image (or no image) to be sent to Gemini.
        import os as _os
        _WF_STYLE_REF_STR = (
            r"G:\My Drive\Z sosFiles\Z_act\@ NETWORK"
            r"\@MEDIAUPSCALE_FACTORY_DYNAMIC_CONTENT\Unified Multi-Page Factory"
            r"\pages_config\wonder_feed\style_reference\Screenshot 2026-06-01 183244.png"
        )
        _WF_STYLE_REF = Path(_WF_STYLE_REF_STR)
        print(f"[DEBUG_STYLE_ENGINE] Target image reference assigned: {_WF_STYLE_REF_STR}")
        _style_ref_path: Path | None = None
        if page_ctx and (page_ctx.page_id or "").lower() == "wonder_feed":
            if _os.path.exists(_WF_STYLE_REF_STR):
                _style_ref_path = _WF_STYLE_REF
                print(f"[DEBUG_STYLE_ENGINE] Verification PASSED — style reference loaded.")
                logging.info("wonder_feed STYLE REF LOCK | pinned → %s", _WF_STYLE_REF.name)
            else:
                print(f"[ERROR_STYLE_ENGINE] Verification Failed. Path missing: {_WF_STYLE_REF_STR}")
                logging.warning(
                    "wonder_feed style reference not found at expected path: %s", _WF_STYLE_REF
                )
        elif page_ctx and page_ctx.style_reference_dir:
            _sref_dir = app_config.ENGINE_ROOT / page_ctx.style_reference_dir
            if _sref_dir.is_dir():
                for _ext in ("*.jpg", "*.jpeg", "*.png"):
                    _candidates = sorted(_sref_dir.glob(_ext))
                    if _candidates:
                        _style_ref_path = _candidates[0]
                        break

        try:
            import os as _img_os
            _dbg_ref = (
                r"G:\My Drive\Z sosFiles\Z_act\@ NETWORK"
                r"\@MEDIAUPSCALE_FACTORY_DYNAMIC_CONTENT\Unified Multi-Page Factory"
                r"\pages_config\wonder_feed\style_reference\Screenshot 2026-06-01 183244.png"
            )
            print("\n" + "=" * 60)
            print("[DEBUG] IMAGE PIPELINE INITIALIZATION")
            print(f"[DEBUG] Model            : {img_model_id or bm.image_primary_id}")
            print(f"[DEBUG] Style Ref Path   : {_dbg_ref}")
            print(f"[DEBUG] Ref File Exists  : {_img_os.path.exists(_dbg_ref)}")
            print(f"[DEBUG] Compiled prompt  : {effective_atmosphere[:200]}")
            print("=" * 60 + "\n")
            adapter = GeminiImageAdapter(model_id=img_model_id)
            img_path_display = adapter.generate(
                image_prompt,
                reference_image_path=effective_ref_path if image_avatar_mode == "ON" else None,
                style_reference_path=_style_ref_path,
                output_stem=stem,
                output_directory=subject_assets,
                avatar_mode=image_avatar_mode,
            )
            img_used = adapter.last_gemini_image_model_used or bm.image_primary_id
            logging.info(
                "Variant %s | IMAGE_OK | model_used=%s | path=%s",
                variant + 1,
                img_used,
                img_path_display,
            )
        except Exception as exc:  # noqa: BLE001
            failed_mid = img_model_id or bm.image_primary_id
            if adapter is not None:
                failed_mid = adapter.last_gemini_image_failure_model_id or failed_mid
            _LOG.error(
                "Image generation failed variant %s attempted_model=`%s`",
                variant + 1,
                failed_mid,
                exc_info=True,
            )
            logging.error(
                "VARIANT_FAIL | GeminiImageAdapter | variant=%s | model=`%s` | err=%s",
                variant + 1,
                failed_mid,
                exc,
            )
            raise

        # ECONOMIC_REEL has its own full-length video compilation in Phase D.
        # Skip the 7-second image-loop conversion so it doesn't overwrite
        # video_path_str with a redundant short clip before the reel is built.
        if (
            isinstance(img_path_display, Path)
            and img_path_display.is_file()
            and post_type != "ECONOMIC_REEL"
        ):
            video_path_str = _maybe_convert_to_video(
                img_path_display,
                duration=7,
                page_ctx=page_ctx,
            )

    # ---- Raw background reference (Gemini output, always textless) --------
    raw_bg_path: Path | None = (
        img_path_display if isinstance(img_path_display, Path) else None
    )
    img_ref_engine = ""
    if raw_bg_path is not None:
        img_ref_engine = path_under_engine(app_config.ENGINE_ROOT, raw_bg_path)

    posting_slot_display = scheduled_bulk_post_display(variant_index=variation_index)

    # ====================================================================
    # PHASE B2: STANDARD_QUOTE caption (SMART_BAIT already done in B1)
    # ====================================================================

    # ---- Caption: STANDARD_QUOTE path -----------------------------------
    # SMART_BAIT, LONG_CAPTION_IMAGE, and ECONOMIC_REEL all generate captions in Phase B1.
    if post_type not in ("SMART_BAIT", "LONG_CAPTION_IMAGE", "ECONOMIC_REEL") and not skip_caption:
        assert caption_engine is not None
        caption_mode_tag = "humanized"

        batch_narrative = (
            pre_narratives[variation_index]
            if pre_narratives
            and variation_index < len(pre_narratives)
            and pre_narratives[variation_index]
            else ""
        )

        if batch_narrative:
            raw_sheet = batch_narrative
            _LOG.debug(
                "Variant %s: using pre-computed batch narrative (%d chars).",
                variant + 1,
                len(raw_sheet),
            )
        else:
            try:
                raw_sheet = caption_engine.synthesize_facts(
                    resolved_subject,
                    corpus,
                    research_model_override=econ_model if (economic and not caption_engine._deepseek) else None,
                    variation_index=variation_index,
                    total_variants=qty,
                    economic=economic,
                )
            except Exception as rex:  # noqa: BLE001
                attempted_rid = caption_engine.research_primary_id
                _LOG.error(
                    "Gemini research failed variant=%s Gemini_head=`%s`",
                    variant + 1,
                    attempted_rid,
                    exc_info=True,
                )
                logging.error(
                    "VARIANT_FAIL | synthesize_facts | variant=%s | err=%s",
                    variant + 1,
                    rex,
                )
                raise

        # Durability checkpoint: save research output before humanization.
        # imgbb_url starts empty here; updated after brand compositing + upload.
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        durable_fname = f"post_{stamp}_v{variant + 1:02d}.json"
        durable_abs = app_config.LIBRARY_DIR / durable_fname
        created_iso = datetime.now(timezone.utc).isoformat()
        excel_rel = path_under_engine(app_config.ENGINE_ROOT, app_config.POST_PLANNER_XLSX)

        pending_payload: dict[str, Any] = {
            "page_id": page_ctx.page_id if page_ctx else app_config.ACTIVE_PAGE,
            "avatar_mode": avatar_mode,
            "post_format": post_format,
            "topic": resolved_subject,
            "subject_slug": slug,
            "variant_index": variant + 1,
            "quantity_total": qty,
            "economic_brain_mode": economic,
            "image_relative": img_ref_engine,
            "video_path": video_path_str,
            "imgbb_url": "",
            "library_relative": path_under_engine(app_config.ENGINE_ROOT, durable_abs),
            "excel_relative": excel_rel,
            "raw_fact_sheet": raw_sheet,
            "humanized_caption": PENDING_CAPTION,
            "caption_status": "pending",
            "created_utc": created_iso,
        }
        write_atomic_json(durable_abs, pending_payload)

        caption = ""
        if raw_sheet:
            caption, caption_mode_tag = caption_engine.humanize_voice_with_fallback(
                raw_sheet,
                resolved_subject,
                variation_index=variation_index,
                total_variants=qty,
                cta_keyword=cta_kw,
                economic=economic,
                model_id=econ_model if (economic and not caption_engine._deepseek) else None,
                cta_enabled=cta_enabled,
            )
        else:
            caption_mode_tag = "researcher_fallback"

    # ====================================================================
    # PHASE C: Brand compositing  (after overlay_text is known)
    # ====================================================================
    logo_path: Path | None = (
        page_ctx.logo_png if (page_ctx and page_ctx.logo_exists) else None
    )
    # Determine compositing mode:
    # - SMART_BAIT: full 4-layer stack (bg + mask + text + logo)
    # - LONG_CAPTION_IMAGE: Layer 1 (bg) + Layer 4 (logo only) — clean standalone image
    # - IMAGE_BACKGROUND / IMAGE_QUOTE / TEXT_QUOTE: text overlay if overlay_text present
    _is_long_caption_image = (post_type == "LONG_CAPTION_IMAGE")
    _is_economic_reel      = (post_type == "ECONOMIC_REEL")
    # ECONOMIC_REEL: background PNG must stay clean (logo only).
    # Text is rendered directly into video frames by video_engine — baking it into
    # the PNG here would cause double-text in the final reel.
    _needs_text_overlay = (
        not _is_long_caption_image
        and not _is_economic_reel
        and post_format in ("IMAGE_BACKGROUND", "IMAGE_QUOTE", "TEXT_QUOTE", "IMAGE_AVATAR")
    )

    # --- Resolve per-page font variables (path, size scale, colour, outline) ---
    _font_size_scale: float = page_ctx.font_size_scale if page_ctx else 0.08
    _font_color: tuple[int, int, int] = page_ctx.font_color if page_ctx else (255, 255, 255)
    _text_outline_width: int = page_ctx.text_outline_width if page_ctx else 1
    _font_path_abs: str = ""
    if page_ctx and page_ctx.font_path:
        _fp_candidate = app_config.ENGINE_ROOT / page_ctx.font_path
        if _fp_candidate.is_file():
            _font_path_abs = str(_fp_candidate)
        else:
            _LOG.debug(
                "Page font not found at '%s' — brand_composer will use system pool.", _fp_candidate
            )
    # Resolve per-page logo layout variables (size + corner position)
    _logo_size_scale: float = page_ctx.logo_size_scale if page_ctx else 0.18
    _logo_position: str     = page_ctx.logo_position   if page_ctx else "bottom_right"

    if raw_bg_path is not None and raw_bg_path.is_file():
        if _is_long_caption_image:
            # LONG_CAPTION_IMAGE:
            #   Layer 1 — graphite background  (Gemini-generated)
            #   Layer 4 — logo watermark baked in (static image shared as-is)
            if logo_path:
                img_path_display = _brand_apply_logo(
                    raw_bg_path, logo_path,
                    position=_logo_position,
                    size_scale=_logo_size_scale,
                )
            # else: leave as raw background
        elif _is_economic_reel:
            # ECONOMIC_REEL: background PNG must remain 100% logo-free.
            # The logo watermark must NOT be baked into this image because it
            # would participate in the Ken Burns zoom and appear to drift.
            # video_engine.compile_dynamic_reel() handles brand identity by
            # compositing a completely static brand_label RGBA layer AFTER
            # every zoom transform — it never moves with the background.
            img_path_display = raw_bg_path   # pass clean PNG directly to video engine
        elif _needs_text_overlay and overlay_text:
            # SMART_BAIT / IMAGE_BACKGROUND / IMAGE_QUOTE / TEXT_QUOTE: full 4-layer stack
            img_path_display = _brand_apply_text(
                raw_bg_path, overlay_text,
                logo_path=logo_path,
                page_id=page_ctx.page_id if page_ctx else None,
                logo_size_scale=_logo_size_scale,
                logo_position=_logo_position,
                font_path_override=_font_path_abs or None,
                font_size_scale=_font_size_scale,
                font_color=_font_color,
                text_outline_width=_text_outline_width,
                post_type=post_type,
            )
        elif logo_path:
            # IMAGE_AVATAR or any other format: logo watermark only
            img_path_display = _brand_apply_logo(
                raw_bg_path, logo_path,
                position=_logo_position,
                size_scale=_logo_size_scale,
            )
        # else: img_path_display stays as raw background (no compositing)

        # HYBRID_VIDEO: burn text rock-solid while background animates
        if video_path_str:
            try:
                final_video = _brand_burn_video(
                    Path(video_path_str),
                    overlay_text,
                    logo_path=logo_path,
                )
                video_path_str = str(final_video)
            except Exception as vexc:  # noqa: BLE001
                _LOG.warning("Video text burn exception (variant %s): %s", variant + 1, vexc)

    # ====================================================================
    # PHASE D: ECONOMIC_REEL video compilation
    #   Triggered only when post_type == "ECONOMIC_REEL".
    #   Uses the brand-composited image (with text + logo already burned in)
    #   as the video background, then layers ElevenLabs voiceover + ambient.
    # ====================================================================
    reel_path: Path | None = None
    _b2_video_url: str = ""  # populated after successful B2 upload of the reel

    if _is_economic_reel and img_path_display and Path(img_path_display).is_file():
        _LOG.info("ECONOMIC_REEL | Launching video compilation pipeline (variant %s)…", variant + 1)
        _voice_path: Path | None = None
        _ambient_path: Path | None = None

        # Reels go to a dedicated clips/ folder alongside assets/
        # (e.g. outputs/wonder_feed/clips/) so MP4s are easy to find.
        _reel_dir = subject_assets.parent.parent / "clips"
        _reel_dir.mkdir(parents=True, exist_ok=True)

        # -- Voiceover (ElevenLabs TTS + word-level timestamps for auto-subtitles) --
        # caption      = 4-sentence narration script spoken by Dorothy voice
        # overlay_text = short on-screen hook headline (static layer, NOT spoken)
        # Guard: never pass the "(skipped)" initialisation placeholder to TTS.
        _real_caption = caption if caption and caption != "(skipped)" else ""
        _voiceover_script = _real_caption or overlay_text
        _word_timings: list[tuple[str, float, float]] = []
        if _voiceover_script and app_config.ELEVENLABS_API_KEY:
            _voice_out = _reel_dir / f"{stem}_v{variant + 1:02d}_voice.mp3"
            _voice_id = page_ctx.elevenlabs_voice_id if page_ctx else None
            try:
                _voice_path, _word_timings = generate_voiceover_with_timestamps(
                    _voiceover_script,
                    _voice_out,
                    voice_id=_voice_id or None,
                    model_id=page_ctx.elevenlabs_model if page_ctx else "eleven_multilingual_v2",
                )
            except Exception as vaudio_exc:  # noqa: BLE001
                _LOG.warning(
                    "Voiceover generation failed (variant %s): %s — reel will be silent.",
                    variant + 1, vaudio_exc,
                )
        else:
            _LOG.warning(
                "ECONOMIC_REEL | Voiceover skipped — %s",
                "ELEVENLABS_API_KEY not set" if not app_config.ELEVENLABS_API_KEY else "no narration script",
            )

        # -- Ambient soundscape (ElevenLabs SFX) --
        if app_config.ELEVENLABS_API_KEY:
            _ambient_out = _reel_dir / f"{stem}_v{variant + 1:02d}_ambient.mp3"
            _target_dur = page_ctx.reel_duration if page_ctx else 25.0
            _ambient_path = generate_ambient_track(_ambient_out, duration_seconds=_target_dur)

        # -- Compile reel via moviepy --
        # Build a readable slug from the hook text for the filename.
        _hook_slug = (
            "".join(c if c.isalnum() else "_" for c in overlay_text.lower()).strip("_")[:32]
            if overlay_text else stem
        )
        _reel_target = _reel_dir / f"reel_{_hook_slug}_v{variant + 1:02d}.mp4"
        _reel_dur = max(30.0, page_ctx.reel_duration if page_ctx else 30.0)
        try:
            # Logo PNG — composited as a fully static post-zoom RGBA layer.
            # Use the same logo_path resolved in Phase C (identical to SMART_BAIT path).
            # Fall back to a fresh page_ctx lookup only if Phase C returned None.
            _logo_img_path: Path | None = (
                logo_path                          # Phase C resolution — shared with SMART_BAIT
                or (page_ctx.logo_png if (page_ctx and page_ctx.logo_exists) else None)
            )
            # Brand text label (used only when no logo PNG is found AND logo_image_path is unset)
            _brand_label = (
                f"@ {page_ctx.display_name}"
                if (page_ctx and page_ctx.display_name and not _logo_img_path)
                else None
            )
            reel_path = compile_dynamic_reel(
                Path(img_path_display),
                overlay_text,
                voice_audio=_voice_path,
                ambient_audio=_ambient_path,
                output_path=_reel_target,
                target_duration=_reel_dur,
                font_path=_font_path_abs or None,
                font_size_scale=_font_size_scale,
                overlay_opacity=page_ctx.reel_overlay_opacity if page_ctx else 0.35,
                word_timings=_word_timings or None,
                brand_label=_brand_label,
                logo_image_path=_logo_img_path,
                subtitle_fontsize=page_ctx.subtitle_fontsize if page_ctx else 46,
                subtitle_y_position=page_ctx.subtitle_y_position if page_ctx else None,
                logo_width_px=page_ctx.logo_width_px if page_ctx else 160,
                logo_y_offset_px=page_ctx.logo_y_offset_px if page_ctx else 90,
                logo_opacity=page_ctx.logo_opacity if page_ctx else 0.70,
                logo_max_height_px=page_ctx.logo_max_height_px if page_ctx else None,
                hook_y_frac=page_ctx.hook_y_frac if page_ctx else 0.55,
                page_id=page_ctx.page_id if page_ctx else "",
                # Never render a static CTA block in the reel — the video timeline
                # owns the lower-third via word-level subtitles.
                sub_text=None,
            )
            _LOG.info("ECONOMIC_REEL compiled → %s", reel_path.name)
            video_path_str = str(reel_path)
            print(f"[reel] Video compiled → {reel_path}")

            # ── PHASE E1: Backblaze B2 upload ────────────────────────────────
            # Upload the finished MP4 to B2 and store the public URL so the
            # postplanner MEDIA URL column contains a live, shareable link.
            _b2_video_url: str = ""
            try:
                _b2 = B2VideoUploader()
                _b2_video_url = _b2.upload(reel_path)
                _LOG.info("B2 upload OK → %s", _b2_video_url)
                print(f"[B2] Public URL: {_b2_video_url}")
            except Exception as _b2_exc:  # noqa: BLE001
                _LOG.warning(
                    "B2 upload failed for %s (%s) — postplanner will use local path.",
                    reel_path.name,
                    _b2_exc,
                )
        except Exception as reel_exc:  # noqa: BLE001
            _LOG.error(
                "ECONOMIC_REEL video compilation failed (variant %s): %s",
                variant + 1, reel_exc, exc_info=True,
            )
            print(
                f"[reel] COMPILE ERROR (variant {variant + 1}): "
                f"{type(reel_exc).__name__}: {reel_exc}"
            )
            reel_path = None

    # ====================================================================
    # PHASE E: ImgBB upload — always uploads the final composited asset
    # ====================================================================
    imgbb_url = ""
    _upload_candidate: Path | str = img_path_display
    if isinstance(_upload_candidate, Path) and _upload_candidate.is_file():
        key_ib = app_config.IMGBB_API_KEY
        if key_ib:
            try:
                imgbb_url = upload_image_file_to_imgbb(key_ib, _upload_candidate) or ""
            except Exception as up_exc:  # noqa: BLE001
                _LOG.warning(
                    "ImgBB upload exception (%s): %s",
                    _upload_candidate.name,
                    up_exc,
                    exc_info=True,
                )
            if not imgbb_url:
                _LOG.warning(
                    "ImgBB upload returned empty URL for %s; planner media column stays blank.",
                    _upload_candidate.name,
                )
        else:
            _LOG.warning("IMGBB_API_KEY missing; CONTENT: MEDIA stays blank.")

    # ====================================================================
    # PHASE E: Durable JSON + planner writes (all post types, unified)
    # ====================================================================

    # ---- SMART_BAIT / ECONOMIC_REEL durable write -----------------------
    if post_type in ("SMART_BAIT", "ECONOMIC_REEL") and not skip_caption:
        if caption_mode_tag != "researcher_fallback" and (overlay_text or caption):
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
            durable_fname = f"post_{stamp}_v{variant + 1:02d}.json"
            durable_abs = app_config.LIBRARY_DIR / durable_fname
            created_iso = datetime.now(timezone.utc).isoformat()
            smart_bait_payload: dict[str, Any] = {
                "page_id": page_ctx.page_id if page_ctx else app_config.ACTIVE_PAGE,
                "avatar_mode": avatar_mode,
                "post_format": post_format,
                "post_type": post_type,
                "topic": resolved_subject,
                "subject_slug": slug,
                "variant_index": variant + 1,
                "quantity_total": qty,
                "economic_brain_mode": economic,
                "image_relative": img_ref_engine,
                "video_path": str(reel_path) if reel_path else video_path_str,
                "b2_url": _b2_video_url,
                "imgbb_url": imgbb_url,
                "overlay_text": overlay_text,
                "humanized_caption": caption,
                "caption_status": caption_mode_tag,
                "created_utc": created_iso,
            }
            write_atomic_json(durable_abs, smart_bait_payload)
            try:
                with write_lock:
                    _planner_type = "VIDEO" if post_type == "ECONOMIC_REEL" else "IMAGE"
                    if post_type == "ECONOMIC_REEL" and reel_path:
                        # Prefer B2 public URL; fall back to local path if upload failed.
                        _planner_media = _b2_video_url or str(reel_path)
                    else:
                        _planner_media = imgbb_url
                    planner_row_ix = append_planner_row(
                        app_config.POST_PLANNER_XLSX,
                        posting_time=posting_slot_display,
                        caption=caption,
                        url_link="",
                        media_url=_planner_media,
                        post_type_value=_planner_type,
                        template_path=app_config.BULK_POSTS_TEMPLATE_XLSX,
                    )
            except Exception as fin_exc:  # noqa: BLE001
                _LOG.warning("Smart bait / reel planner write failed (variant %s): %s", variant + 1, fin_exc)

    # ---- STANDARD_QUOTE durable write ------------------------------------
    elif not skip_caption:
        if caption_mode_tag == "researcher_fallback":
            _LOG.warning(
                "All humanizers failed for variant %s of '%s'. "
                "Variant skipped in Excel/CSV; research saved to logs/.",
                variant + 1,
                resolved_subject,
            )
            merge_update_json(durable_abs, {
                "humanized_caption": "",
                "caption_status": "skipped_humanizer_failure",
                "humanized_utc": datetime.now(timezone.utc).isoformat(),
                "imgbb_url": imgbb_url,
            })
        else:
            if caption_mode_tag == "gemini_fallback":
                _LOG.info("Claude failed; Gemini fallback succeeded for variant %s.", variant + 1)
            caption_payload: dict[str, Any] = {
                "humanized_caption": caption,
                "caption_status": caption_mode_tag,
                "humanized_utc": datetime.now(timezone.utc).isoformat(),
                "imgbb_url": imgbb_url,
            }
            try:
                if durable_abs is not None:
                    merge_update_json(durable_abs, caption_payload)
                else:
                    _LOG.debug(
                        "durable_abs is None (avatar OFF or durable write skipped) — "
                        "skipping merge_update_json for variant %s; Excel write continues.",
                        variant + 1,
                    )
                with write_lock:
                    planner_row_ix = append_planner_row(
                        app_config.POST_PLANNER_XLSX,
                        posting_time=posting_slot_display,
                        caption=caption,
                        url_link="",
                        media_url=imgbb_url,
                        post_type_value="IMAGE",
                        template_path=app_config.BULK_POSTS_TEMPLATE_XLSX,
                    )
            except Exception as fin_exc:  # noqa: BLE001
                _LOG.warning(
                    "Post-humanizer durable/Excel write failed (variant %s): %s",
                    variant + 1,
                    fin_exc,
                    exc_info=True,
                )

    # ---- Skip-caption planner write --------------------------------------
    if skip_caption:
        caption_mode_tag = "skipped"
        with write_lock:
            planner_row_ix = append_planner_row(
                app_config.POST_PLANNER_XLSX,
                posting_time=posting_slot_display,
                caption=caption if isinstance(caption, str) else "(skipped)",
                url_link="",
                media_url=imgbb_url,
                post_type_value="IMAGE",
                template_path=app_config.BULK_POSTS_TEMPLATE_XLSX,
            )

    logging.info("--- VARIANT %s | TOPIC `%s` ---", variant + 1, resolved_subject)
    logging.info("RAW FACT SHEET (researcher)\n%s", raw_sheet)
    logging.info("FINAL CAPTION (humanizer)\n%s", caption)

    if isinstance(raw_sheet, str) and raw_sheet not in ("(skipped)", ""):
        try:
            dump_raw_research_to_log(
                logs_dir,
                run_stamp=run_stamp,
                topic=resolved_subject,
                variant_index=variant + 1,
                raw_fact_sheet=raw_sheet,
            )
        except Exception as log_exc:  # noqa: BLE001
            _LOG.warning("Research log write failed: %s", log_exc)

    caption_str = caption if isinstance(caption, str) else ""

    meta: dict[str, Any] = {}
    with write_lock:
        meta = append_entry(
            app_config.CONTENT_LIBRARY_PATH,
            build_library_metadata(
                topic=resolved_subject,
                final_caption=caption_str,
                imgbb_url=imgbb_url,
            ),
        )

    if caption_mode_tag not in ("researcher_fallback", "skipped"):
        try:
            xlsx_path: Path | None = None
            with write_lock:
                xlsx_path = append_postplanner_xlsx_row(
                    postplanner_dir,
                    run_stamp=run_stamp,
                    posting_time=posting_slot_display,
                    caption=caption_str,
                    media_url=imgbb_url,
                )
            if xlsx_path:
                _LOG.info("PostPlanner XLSX row written: %s", xlsx_path.name)
        except Exception as xlsx_exc:  # noqa: BLE001
            _LOG.warning("PostPlanner XLSX write failed (variant %s): %s", variant + 1, xlsx_exc)

    if skip_image:
        img_report = "(skipped)"
    elif adapter is not None and adapter.last_gemini_image_model_used:
        img_report = adapter.last_gemini_image_model_used
    else:
        img_report = bm.image_primary_id

    lib_json_rel = path_under_engine(app_config.ENGINE_ROOT, durable_abs) if durable_abs else ""
    return {
        "topic": resolved_subject,
        "variant_index": variant + 1,
        "local_image_path": img_ref_engine,
        "video_path": video_path_str,
        "imgbb_url": imgbb_url,
        "caption": caption_str,
        "overlay_text": overlay_text,
        "library_timestamp": meta.get("timestamp"),
        "library_json_relative": lib_json_rel,
        "excel_row": planner_row_ix,
        "model_image_used": img_report,
        "model_research_head": bm.research_primary_id,
        "humanizer": humanizer_notes,
        "caption_mode": caption_mode_tag if caption_mode_tag is not None else "skipped",
    }


# ---------------------------------------------------------------------------
# Core production loop
# ---------------------------------------------------------------------------

def produce(
    subject: str | None,
    *,
    quantity: int = 1,
    skip_image: bool = False,
    skip_caption: bool = False,
    test_mode: bool = False,
    economic_brain_mode: bool | None = None,
    bootstrap_models: PlannedModels | None = None,
    page_ctx: PageContext | None = None,
    cta_enabled: bool = True,
    post_type: str = "STANDARD_QUOTE",
    image_style: str = "NATURAL",
) -> dict[str, Any]:
    qty = max(1, quantity)
    economic = economic_brain_mode if economic_brain_mode is not None else app_config.ECONOMIC_BRAIN_MODE

    # Resolve avatar_mode and post_format from page context (or safe defaults).
    avatar_mode: str = page_ctx.avatar_mode if page_ctx else "ON"
    post_format: str = page_ctx.post_format if page_ctx else "IMAGE_AVATAR"
    # ECONOMIC_REEL must not fall back into the IMAGE_AVATAR image-post pipeline.
    if post_type == "ECONOMIC_REEL":
        post_format = "DYNAMIC_REEL"
    atmosphere_style: str = page_ctx.atmosphere_style if page_ctx else ""

    # Page-level aspect ratio override (falls back to global config if empty).
    page_aspect_ratio: str = (page_ctx.image_aspect_ratio if page_ctx else "") or ""

    bm = bootstrap_models or _snapshot_verified_models(economic_brain_mode=economic)
    _bootstrap_pipeline_intro(
        economic_brain_mode=economic,
        verified=bm,
        compact=not test_mode,
        page_ctx=page_ctx,
    )

    envelope: dict[str, Any] = {
        "mode": "test" if test_mode else "live",
        "quantity": qty,
        "economic_brain_mode": economic,
        "page_id": page_ctx.page_id if page_ctx else app_config.ACTIVE_PAGE,
        "avatar_mode": avatar_mode,
        "post_format": post_format,
        "post_type": post_type,
        "cta_enabled": cta_enabled,
        "items": [],
    }

    pdf_inventory = list_pdf_relative_paths(app_config.DIGITAL_PRODUCTS_PATH)

    if test_mode:
        topic_seed = (subject or "").strip() or "Auto subject imaginer (provide subject for production)"
        _LOG.info("TEST MODE scaffold | topic_hint=%s | quantity=%s", topic_seed, qty)
        print("\n=== TEST MODE - no Gemini or Anthropic network calls ===\n")
        print(f"=== Page: {page_ctx.display_name if page_ctx else 'anna_protocol'} "
              f"| Avatar: {avatar_mode} | Format: {post_format} ===\n")

        print("--- Knowledge test: PDF corpus inventory ---\n")
        if pdf_inventory:
            for name in pdf_inventory:
                print(f"  - {name}")
        else:
            print(
                f"  (No PDF files under `{app_config.DIGITAL_PRODUCTS_PATH.resolve()}`. "
                "Brain cannot ingest guides until PDFs arrive.)",
            )

        imagine_prompt = imagine_subject_instruction_preview()
        architect = VisualArchitect()
        prompt = architect.build_prompt(
            topic_seed,
            variation_index=0,
            total_variants=qty,
            avatar_mode=avatar_mode,
            atmosphere_style=atmosphere_style,
            aspect_ratio=page_aspect_ratio or None,
        )
        researcher_instruction = build_gemini_researcher_instruction(topic_seed)
        sys_prompt, usr_prompt = humanizer_preview_with_placeholder(topic_seed)

        envelope["digital_products_pdf_files"] = pdf_inventory
        envelope["imagine_subject_instruction"] = imagine_prompt
        envelope["visual_prompt"] = prompt

        print("\n--- Imagine-subject scaffold (Brain) ---\n")
        print(imagine_prompt)
        print("\n--- Visual test (upstream image prompt) ---\n")
        print(prompt)
        print("\n--- Gemini researcher scaffold ---\n")
        print(researcher_instruction)
        print("\n--- Claude humanizer (system) ---\n")
        print(sys_prompt)
        print("\n--- Claude humanizer (user scaffold; FACT SHEET dynamic in live runs) ---\n")
        print(usr_prompt)
        print("\n--- Economic Gemini-only humanizer scaffold ---\n")
        print(economic_humanizer_instruction_preview(topic_seed))

        if skip_image or skip_caption:
            print(
                "\n[hint] `--skip-image` / `--skip-caption` are informational in `--test`; "
                "scaffolds still print.\n",
            )

        envelope["items"].append(
            {
                "topic": topic_seed,
                "caption": "(dry-run)",
                "local_image_path": "(dry-run)",
                "imgbb_url": "",
                "variant_index": 0,
            }
        )
        return envelope

    corpus = load_digital_product_corpus(
        app_config.DIGITAL_PRODUCTS_PATH,
        chunk_char_limit=app_config.PDF_CHUNK_CHAR_LIMIT,
    )

    _silence_noisy_http_loggers()
    resolved_subject = (subject or "").strip()
    if not resolved_subject:
        resolved_subject = imagine_subject(corpus)

    # WONDER_FEED + ECONOMIC_REEL: ALWAYS pick a fresh topic from the TOPIC_POOL,
    # regardless of what imagine_subject() returned.  This guarantees a unique
    # script every single run — no cached subject, no static placeholder loop.
    import random as _rnd
    _page_id_lower = (page_ctx.page_id if page_ctx else "").lower()
    if (
        _page_id_lower == "wonder_feed"
        and post_type in ("ECONOMIC_REEL", "SMART_BAIT")
        and page_ctx is not None
        and page_ctx.topic_pool
    ):
        resolved_subject = _rnd.choice(page_ctx.topic_pool)
        _LOG.info(
            "wonder_feed TOPIC LOCK | fresh pool topic selected → %r (pool size=%d)",
            resolved_subject, len(page_ctx.topic_pool),
        )
    else:
        # Generic fallback: replace only the known static placeholder with a pool topic.
        _STATIC_FALLBACK_SUBJECT = "Holistic vitality protocol"
        if (
            resolved_subject == _STATIC_FALLBACK_SUBJECT
            and page_ctx is not None
            and page_ctx.topic_pool
        ):
            resolved_subject = _rnd.choice(page_ctx.topic_pool)
            _LOG.info(
                "Topic pool override | static fallback replaced → %r (pool size=%d)",
                resolved_subject, len(page_ctx.topic_pool),
            )

    _LOG.info(
        "PIPELINE LIVE | page=%s | avatar=%s | format=%s | resolved_subject=%r | qty=%s | economic=%s",
        page_ctx.page_id if page_ctx else app_config.ACTIVE_PAGE,
        avatar_mode,
        post_format,
        resolved_subject,
        qty,
        economic,
    )
    logging.info(
        "Models banner | verified_image=%s | verified_research=%s | humanizer=%s",
        bm.image_primary_id,
        bm.research_primary_id,
        bm.humanizer_summary,
    )

    slug = subject_slug(resolved_subject)
    subject_assets = app_config.ASSETS_DIR / slug
    subject_assets.mkdir(parents=True, exist_ok=True)

    caption_engine: CaptionEngine | None = None
    if not skip_caption:
        try:
            caption_engine = CaptionEngine()
            _LOG.info("CaptionEngine online | Gemini text head=`%s`", caption_engine.research_primary_id)
        except Exception as exc:  # noqa: BLE001
            _LOG.error("CaptionEngine init failed: %s", exc, exc_info=True)
            logging.error(
                "FATAL_BEFORE_EXIT | CaptionEngine init | stage=initialization | Gemini_text_head=%s | err=%s",
                bm.research_primary_id,
                exc,
            )
            raise

    envelope["resolved_subject"] = resolved_subject

    run_stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    postplanner_dir = app_config.PAGE_OUTPUTS_DIR / "postplanner"
    logs_dir = app_config.ENGINE_ROOT / "logs"

    # ------------------------------------------------------------------
    # ONE-CALL BATCH RESEARCH: generate all variant narratives upfront.
    # Skipped for SMART_BAIT (no research phase needed) and economic mode.
    # ------------------------------------------------------------------
    pre_narratives: list[str] = []
    if not skip_caption and not economic and post_type not in ("SMART_BAIT", "ECONOMIC_REEL") and caption_engine is not None:
        try:
            pre_narratives = caption_engine.synthesize_facts_batch(
                resolved_subject, corpus, num_variants=qty
            )
            _LOG.info(
                "ONE-CALL batch research: %d/%d narratives ready for '%s'.",
                sum(1 for n in pre_narratives if n),
                qty,
                resolved_subject,
            )
        except Exception as batch_exc:  # noqa: BLE001
            _LOG.warning(
                "Batch research failed (%s). Falling back to per-variant calls.", batch_exc
            )

    # Pre-compute the effective reference image path once — it is constant
    # across all variants and depends only on avatar_mode and page_ctx.
    effective_ref_path: Path | None = None
    if avatar_mode == "ON":
        if page_ctx and page_ctx.avatar_reference_exists:
            effective_ref_path = page_ctx.avatar_reference_png
        else:
            effective_ref_path = app_config.REFERENCE_IMAGE_PATH

    econ_model = app_config.GEMINI_ECONOMIC_BRAIN_MODEL

    # ------------------------------------------------------------------
    # Parallel variant execution
    # ------------------------------------------------------------------
    # write_lock serialises all writes to shared files (Excel workbooks,
    # content_library.json) so concurrent workers never interleave rows.
    write_lock = threading.Lock()

    # ── DYNAMIC SHORT-TERM HOOK MEMORY ───────────────────────────────────────
    # Load any hooks generated in prior runs for this page so the LLM always
    # has a growing list of already-used angles to avoid.  A simple JSON file
    # acts as the lightweight persistent store across sequential CLI calls.
    # Thread-safe: workers read a snapshot at call time; appends are locked.
    _hooks_cache_path = (
        app_config.PAGE_OUTPUTS_DIR / "session_hooks_cache.json"
    )
    generated_hooks_cache: list[str] = []
    try:
        import json as _jc
        if _hooks_cache_path.exists():
            _raw = _hooks_cache_path.read_text(encoding="utf-8")
            _loaded = _jc.loads(_raw)
            if isinstance(_loaded, list):
                generated_hooks_cache = [str(h) for h in _loaded if h]
                _LOG.info(
                    "Hooks cache loaded: %d prior hooks from %s",
                    len(generated_hooks_cache),
                    _hooks_cache_path.name,
                )
    except Exception as _hce:  # noqa: BLE001
        _LOG.warning("Could not load hooks cache (%s) — starting fresh.", _hce)
        generated_hooks_cache = []
    hooks_cache_lock = threading.Lock()

    _wkw: dict[str, Any] = dict(
        qty=qty,
        slug=slug,
        resolved_subject=resolved_subject,
        corpus=corpus,
        pre_narratives=pre_narratives,
        caption_engine=caption_engine,
        skip_image=skip_image,
        skip_caption=skip_caption,
        avatar_mode=avatar_mode,
        post_format=post_format,
        atmosphere_style=atmosphere_style,
        page_aspect_ratio=page_aspect_ratio,
        effective_ref_path=effective_ref_path,
        economic=economic,
        econ_model=econ_model,
        bm=bm,
        page_ctx=page_ctx,
        subject_assets=subject_assets,
        run_stamp=run_stamp,
        postplanner_dir=postplanner_dir,
        logs_dir=logs_dir,
        write_lock=write_lock,
        cta_enabled=cta_enabled,
        post_type=post_type,
        image_style=image_style,
        generated_hooks_cache=generated_hooks_cache,
        hooks_cache_lock=hooks_cache_lock,
        hooks_cache_path=_hooks_cache_path,
    )

    if qty == 1:
        # Single-variant path: exceptions propagate naturally to cli()
        # so the clean API-error display and logging remain intact.
        raw_results = [_produce_variant_worker(0, **_wkw)]
    else:
        # Multi-variant path: each worker runs concurrently; a failure in
        # one variant is caught per-future and logged, all others complete.
        max_workers = min(qty, 5)
        _LOG.info(
            "Bulk production: %d variants | max_workers=%d | page=%s",
            qty,
            max_workers,
            page_ctx.page_id if page_ctx else app_config.ACTIVE_PAGE,
        )
        print(f"\n[bulk] Launching {qty} concurrent variant workers (max_workers={max_workers})…")
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futs = {
                pool.submit(_produce_variant_worker, v, **_wkw): v
                for v in range(qty)
            }
            raw_results: list[dict[str, Any] | None] = []
            for fut in as_completed(futs):
                v_idx = futs[fut]
                try:
                    raw_results.append(fut.result())
                    print(f"  [bulk] Variant {v_idx + 1}/{qty} complete.")
                except Exception as exc:  # noqa: BLE001
                    _LOG.error(
                        "Variant %d failed and was skipped: %s",
                        v_idx + 1,
                        exc,
                        exc_info=True,
                    )
                    print(
                        f"  [bulk] Variant {v_idx + 1}/{qty} FAILED (skipped) — "
                        f"{type(exc).__name__}: {exc}"
                    )
                    raw_results.append(None)

    items: list[dict[str, Any]] = [r for r in raw_results if r is not None]
    items.sort(key=lambda x: x["variant_index"])

    skipped_count = qty - len(items)
    if skipped_count:
        _LOG.warning(
            "%d of %d variant(s) failed and were excluded from the output.",
            skipped_count,
            qty,
        )

    envelope["items"] = items

    snippet_lines: list[str] = []
    for idx, row in enumerate(items):
        cap = row.get("caption")
        if isinstance(cap, str):
            snippet_lines.append(f"{idx + 1}. {cap}")
    snippet = "\n".join(snippet_lines)
    snippet_path = app_config.PAGE_OUTPUTS_DIR / "last_captions_bundle.txt"
    if snippet.strip():
        snippet_path.write_text(snippet.strip() + "\n", encoding="utf-8")

    _LOG.info("PIPELINE DONE | page=%s | artifacts under outputs/%s/",
              page_ctx.page_id if page_ctx else app_config.ACTIVE_PAGE,
              page_ctx.page_id if page_ctx else app_config.ACTIVE_PAGE)
    return envelope


def run_pipeline(
    topic: str,
    *,
    skip_image: bool = False,
    skip_caption: bool = False,
    test_mode: bool = False,
) -> dict[str, Any]:
    """Backward-compatible alias for scripts expecting the older entrypoint."""
    bm = _snapshot_verified_models(economic_brain_mode=app_config.ECONOMIC_BRAIN_MODE)
    return produce(
        topic.strip() if topic else None,
        quantity=1,
        skip_image=skip_image,
        skip_caption=skip_caption,
        test_mode=test_mode,
        economic_brain_mode=None,
        bootstrap_models=bm,
        page_ctx=None,
    )


def _print_test_footer() -> None:
    print("\n--- Test summary ---\n")
    print("Dry-run complete; no Gemini or Claude paid calls were exercised for generation.\n")


def cli() -> None:
    parser = argparse.ArgumentParser(
        description="Unified Multi-Page Factory — holistic persona content engine.",
    )
    parser.add_argument(
        "topic",
        nargs="?",
        help='Optional topic/subject ("Castor Oil"). Omit for AI-chosen subjects.',
    )
    parser.add_argument(
        "--quantity", "--count", "-n",
        dest="quantity",
        type=int,
        default=1,
        help="Number of unique post variants to produce concurrently. Alias: --count, -n. Default: 1.",
    )
    parser.add_argument(
        "--page",
        default="anna_protocol",
        choices=list(VALID_PAGES),
        metavar="PAGE",
        help=(
            f"Target page persona. Options: {', '.join(VALID_PAGES)}. "
            "Default: anna_protocol."
        ),
    )
    parser.add_argument(
        "--avatar",
        default=None,
        choices=list(VALID_AVATAR_MODES),
        metavar="AVATAR",
        help=(
            "ON: include human subject + reference likeness in image generation. "
            "OFF: bypass avatar pipeline — generates purely atmospheric background imagery. "
            "Default: derived from page config (anna_protocol=ON, wonder_feed=OFF, down_dirty=OFF)."
        ),
    )
    parser.add_argument(
        "--format",
        dest="post_format",
        default=None,
        choices=list(VALID_FORMATS),
        metavar="FORMAT",
        help=(
            "Output format. "
            "IMAGE_AVATAR: standard portrait (default). "
            "IMAGE_QUOTE: Gemini image + text overlay (legacy alias). "
            "IMAGE_BACKGROUND: hyper-literal Gemini background + text overlay (SMART_BAIT default). "
            "TEXT_QUOTE: brand-colour solid backdrop + text only (zero Gemini image cost). "
            "HYBRID_VIDEO: 7-second Ken Burns zoom loop from generated image."
        ),
    )
    parser.add_argument(
        "--skip-image",
        action="store_true",
        help="Caption + planner only.",
    )
    parser.add_argument(
        "--skip-caption",
        action="store_true",
        help="Image synthesis only.",
    )
    parser.add_argument(
        "--test",
        action="store_true",
        help="Dry-run: print scaffold prompts/inventory without calling Gemini or Anthropic APIs.",
    )
    parser.add_argument(
        "--cta",
        dest="cta",
        default="ON",
        choices=["ON", "OFF"],
        metavar="CTA",
        help=(
            "ON (default): inject CTA keyword into captions. "
            "OFF: suppress all comment-to-receive CTAs and DM links."
        ),
    )
    parser.add_argument(
        "--post-type",
        dest="post_type",
        default="STANDARD_QUOTE",
        choices=["STANDARD_QUOTE", "SMART_BAIT", "LONG_CAPTION_IMAGE", "ECONOMIC_REEL"],
        metavar="POST_TYPE",
        help=(
            "STANDARD_QUOTE (default): long-form educational caption from PDF research. "
            "SMART_BAIT: 4-layer image stack (bg + 20%% mask + bold text + logo) with ultra-short "
            "viral hook + sarcastic one-liner caption. Uses illustration_style for Gemini prompt. "
            "LONG_CAPTION_IMAGE: contextual illustration image with ONLY a logo overlay (no text/mask). "
            "Deep long-form storytelling caption about relationships/character. "
            "ECONOMIC_REEL: graphite image base (same pipeline as SMART_BAIT) compiled into a "
            "vertical 9:16 MP4 reel with ElevenLabs TTS voiceover, dark ambient soundscape, "
            "and cinematic Ken Burns zoom-in. Outputs .mp4 + durable JSON."
        ),
    )
    parser.add_argument(
        "--economic",
        dest="economic",
        action="store_true",
        help="Force Gemini-only economic brain mode (research + captions).",
    )
    parser.add_argument(
        "--premium-relay",
        dest="premium",
        action="store_true",
        help="Force Gemini research + Claude 3.5 Sonnet captions (dual-LLM relay).",
    )
    parser.add_argument(
        "--draw-style",
        dest="draw_style",
        default="SKETCH",
        choices=["NATURAL", "CARTOON", "SKETCH"],
        metavar="DRAW_STYLE",
        help=(
            "NATURAL (default): photorealistic cinematic image generation. "
            "CARTOON: Modern 2.5D flat illustration / vibrant stylized vector art. "
            "SKETCH: Forced graphite pencil illustration pipeline (auto-applied for "
            "SMART_BAIT / LONG_CAPTION_IMAGE / ECONOMIC_REEL on wonder_feed — "
            "this flag is ignored for those post types)."
        ),
    )
    args = parser.parse_args()

    # ── PERMANENT WONDER_FEED STYLE LOCK ───────────────────────────────────
    # Fires BEFORE any config lookups so no legacy format bucket can override.
    # Idempotent — the later guard at runtime still runs for belt-and-suspenders.
    if getattr(args, "page", "").lower() == "wonder_feed":
        _wf_pt = getattr(args, "post_type", "").upper()
        if _wf_pt in ("SMART_BAIT", "LONG_CAPTION_IMAGE", "ECONOMIC_REEL"):
            args.draw_style = "SKETCH"
        if _wf_pt == "ECONOMIC_REEL":
            # Force post_format so resolve_default_format() cannot assign IMAGE_AVATAR
            args.post_format = "DYNAMIC_REEL"

    if args.economic and args.premium:
        raise SystemExit("Choose either --economic or --premium-relay, not both.")

    # ------------------------------------------------------------------
    # Build PageContext from parsed flags.
    # avatar and format fall back to page-level defaults if not specified.
    # ------------------------------------------------------------------
    page_id: str = args.page

    # Load page config to read per-page defaults.
    _tmp_page_cfg: dict = {}
    try:
        from page_loader import _load_page_config, _PAGES_CONFIG_ROOT
        _tmp_page_cfg = _load_page_config(_PAGES_CONFIG_ROOT / page_id, page_id)
    except Exception:  # noqa: BLE001
        pass

    avatar_mode: str = (
        args.avatar
        if args.avatar is not None
        else resolve_default_avatar_mode(_tmp_page_cfg)
    )
    post_format: str = (
        args.post_format
        if args.post_format is not None
        else resolve_default_format(_tmp_page_cfg)
    )
    # ECONOMIC_REEL has its own video-compilation pipeline — stop it routing into
    # the static image post_format buckets (IMAGE_AVATAR etc.) so the bootstrap
    # display and any format-sensitive guards all see the correct type.
    if getattr(args, "post_type", "").upper() == "ECONOMIC_REEL":
        post_format = "DYNAMIC_REEL"

    try:
        page_ctx = load_page_context(page_id, avatar_mode=avatar_mode, post_format=post_format)
    except ValueError as ve:
        raise SystemExit(str(ve)) from ve

    economic_choice: bool | None
    if args.premium:
        economic_choice = False
    elif args.economic:
        economic_choice = True
    else:
        economic_choice = None

    econ_resolved = economic_choice if economic_choice is not None else app_config.ECONOMIC_BRAIN_MODE
    planned_models = _snapshot_verified_models(economic_brain_mode=econ_resolved)

    logging.basicConfig(
        level=logging.WARNING,
        format="%(levelname)s | %(message)s",
        force=True,
    )
    _silence_noisy_http_loggers()

    log_path, ts_token = ledger_file_path(app_config.ENGINE_ROOT)
    configure_file_logging(log_path)

    for h in logging.root.handlers:
        if getattr(h, "_engine_run_journal", False):
            continue
        h.setLevel(logging.WARNING)

    activate_run_ledger(log_path, planned=planned_models)
    logging.getLogger(__name__).info(
        "=== ENGINE RUN BEGIN | page=%s | avatar=%s | format=%s | log=%s | ts=%s ===",
        page_id, avatar_mode, post_format, log_path, ts_token,
    )

    print(f"[bootstrap] Detailed run log: {log_path}")

    topic_raw = args.topic or None
    if topic_raw is None and args.test:
        topic_raw = input("Topic (optional, press Enter to rely on scaffold placeholder): ").strip() or ""

    # For wonder_feed graphite post types, force the draw_style to SKETCH regardless
    # of what the user passed on the CLI — the page's BASE_GRAPHITE_PROMPT is the
    # single source of truth, and SKETCH is the only correct flag for that pipeline.
    _active_post_type = args.post_type.upper() if hasattr(args, "post_type") else ""
    if (
        args.page.lower() == "wonder_feed"
        and _active_post_type in ("SMART_BAIT", "LONG_CAPTION_IMAGE", "ECONOMIC_REEL")
    ):
        args.draw_style = "SKETCH"

    envelope: dict[str, Any] | None = None
    try:
        envelope = produce(
            topic_raw,
            quantity=args.quantity,
            skip_image=args.skip_image,
            skip_caption=args.skip_caption,
            test_mode=args.test,
            economic_brain_mode=economic_choice,
            bootstrap_models=planned_models,
            page_ctx=page_ctx,
            cta_enabled=(args.cta.upper() != "OFF"),
            post_type=args.post_type.upper(),
            image_style=args.draw_style.upper(),
        )
    except Exception as exc:  # noqa: BLE001
        if isinstance(exc, KeyboardInterrupt):
            raise
        if _looks_like_upstream_api_failure(exc):
            _emit_clean_api_error(exc)
            _LOG.error("Run aborted due to upstream API failure.", exc_info=True)
            sys.exit(1)
        raise

    logging.getLogger(__name__).info(
        "=== ENGINE RUN COMPLETE | page=%s | log=%s subject=%s ===",
        page_id,
        log_path,
        envelope.get("resolved_subject") if envelope else None,
    )

    if isinstance(envelope.get("mode"), str) and envelope["mode"] == "test":
        _print_test_footer()
    else:
        _print_production_summary(envelope, page_ctx=page_ctx)


if __name__ == "__main__":
    cli()

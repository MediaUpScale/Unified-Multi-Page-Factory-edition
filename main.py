# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import shutil
import sys
import re as _re
from pathlib import Path

# Force UTF-8 for the whole process BEFORE any print / path / import side effects.
# Windows defaults to cp1252 for console I/O, which corrupts accented filenames
# (e.g. göbekli → g�bekli) and crashes on arrows/em dashes in logs.
os.environ.setdefault("PYTHONUTF8", "1")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
if sys.platform == "win32":
    try:
        import ctypes as _ctypes

        _ctypes.windll.kernel32.SetConsoleCP(65001)
        _ctypes.windll.kernel32.SetConsoleOutputCP(65001)
    except Exception:
        pass


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
import functools
import logging
import random
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

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
from avatar_engine.b2_client import B2StorageCapError, B2VideoUploader
from avatar_engine.imgbb_client import upload_image_file_to_imgbb
from avatar_engine.publishers.youtube_publisher import (
    upload_short_from_envelope as _yt_upload_short,
    build_credentials as _yt_build_credentials,
    build_youtube_client as _yt_build_client,
    verify_authorized_channel as _yt_verify_channel,
    resolve_youtube_token_path as _yt_token_path,
    get_or_create_playlist as _yt_get_or_create_playlist,  # noqa: F401 (available for future hooks)
    YouTubeQuotaExceededError,
    queue_pending_upload_from_envelope as _yt_queue_pending_upload,
    resume_pending_youtube_uploads as _yt_resume_pending_queue,
)
from avatar_engine.audio_engine import (
    apply_voice_loudnorm,
    generate_voiceover,
    generate_voiceover_with_timestamps,
    generate_ambient_track,
    generate_master_mei_soundscape,
)
from avatar_engine.video_engine import compile_dynamic_reel
from core_engine.cost_tracker import CostTracker
from core_engine.interfaces.factory import ChannelFactory
from core_engine.reel_sequence_engine import (
    compile_sequence_reel as _core_compile_sequence_reel,
    build_sequence_script_prompt as _build_sequence_script_prompt,
    build_hook_body_act_durations as _build_hook_body_act_durations,
    compute_dense_act_count as _compute_dense_act_count,
    compute_hook_body_act_count as _compute_hook_body_act_count,
    segment_script_into_act_snippets as _segment_script_into_act_snippets,
)
from core_engine.scene_pacing import (
    describe_scene_plan as _describe_scene_plan,
    plan_scenes as _plan_scenes,
    scale_scene_durations as _scale_scene_durations,
)
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
from avatar_engine.providers.image_provider import GeminiImageAdapter, get_image_adapter
from avatar_engine.subject_brain import imagine_subject, imagine_subject_instruction_preview, generate_bulk_topics
from avatar_engine.batch_planner import (
    BatchAngle,
    BatchUniquenessGuard,
    MAX_UNIQUENESS_RETRIES,
    plan_angles_matrix,
)
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
        "channels_config/wonder_feed/Quotes_reference/@REDES REF SOURCE FONTE.xlsx"
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


def _export_scene_assets_sequential(
    image_paths: "list[Path | str]",
    assets_dir: "Path | str",
) -> list[Path]:
    """
    Copy scene images into ``outputs/<page>/assets/<episode_id>/`` as
    ``scene_01.png``, ``scene_02.png``, … Creating the directory when missing.
    """
    out_dir = Path(assets_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    exported: list[Path] = []
    for i, src in enumerate(image_paths, start=1):
        src_p = Path(src)
        if not src_p.is_file():
            _LOG.warning(
                "Scene export skipped | missing source for scene_%02d: %s",
                i, src_p,
            )
            continue
        suffix = src_p.suffix.lower() if src_p.suffix else ".png"
        if suffix not in {".png", ".jpg", ".jpeg", ".webp"}:
            suffix = ".png"
        dest = out_dir / f"scene_{i:02d}{suffix}"
        try:
            if dest.resolve() != src_p.resolve():
                shutil.copy2(src_p, dest)
            else:
                # Already the canonical scene path
                pass
            if dest.is_file():
                exported.append(dest.resolve())
        except OSError as exc:
            _LOG.warning("Scene export failed for scene_%02d (%s): %s", i, src_p, exc)
    return exported


def _ensure_sequence_image(
    img_path: "Path | str | None",
    *,
    fallback: "Path | str | None",
    target_path: "Path | None" = None,
) -> Path:
    """
    Guarantee a readable act image for MoviePy/PIL.

    If *img_path* is missing/invalid, ``shutil.copy2`` the previous valid
    *fallback* (Act 1 / base) into *target_path*. Never raises
    ``FileNotFoundError`` — last resort is a solid-color placeholder so
    API depletion never crashes compilation.
    """
    from PIL import Image as _PILImage

    fb = Path(fallback) if fallback else None
    candidate = Path(img_path) if img_path else None

    if candidate is not None and candidate.is_file() and os.path.exists(candidate):
        return candidate.resolve()

    dest = Path(target_path) if target_path is not None else (
        candidate if candidate is not None else (
            (fb.parent / f"{fb.stem}_fallback{fb.suffix}") if fb is not None
            else Path("outputs") / "seq_act_placeholder.png"
        )
    )
    os.makedirs(os.path.dirname(str(dest)) or ".", exist_ok=True)

    if fb is not None and fb.is_file() and os.path.exists(fb):
        try:
            if dest.resolve() != fb.resolve():
                shutil.copy2(fb, dest)
                _LOG.warning(
                    "Act image missing (%s) — copied fallback %s → %s",
                    candidate, fb.name, dest,
                )
            if dest.is_file() and os.path.exists(dest):
                return dest.resolve()
        except OSError as exc:
            _LOG.warning("Fallback copy failed (%s) — using prior image path %s", exc, fb)
            return fb.resolve()

    # Blinded last resort — solid frame so MoviePy always completes
    try:
        _PILImage.new("RGB", (1080, 1920), (10, 10, 14)).save(str(dest))
        _LOG.warning("Blinded placeholder act image written → %s", dest)
        return dest.resolve()
    except Exception as exc:  # noqa: BLE001
        _LOG.error("Could not write placeholder act image (%s)", exc)
        # Absolute last ditch: reuse any existing path we still hold
        if fb is not None and fb.is_file():
            return fb.resolve()
        raise RuntimeError(f"Unable to guarantee sequence act image at {dest}") from exc


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


def _format_cost_block(
    breakdown: dict,
    post_format: str = "",
    total_images: int = 0,
    scheduled_publish: str = "",
) -> str:
    """
    Render a formatted terminal cost-analysis block from a CostTracker snapshot.

    Groups entries by operation category (research / image / voice) and
    displays individual line costs plus a TOTAL ESTIMATED COST footer.

    Args:
        breakdown:    CostTracker snapshot dict.
        post_format:  Page post format string (e.g. "SEQUENCE_REEL", "DYNAMIC_REEL").
                      Controls the Visual Assets description and the $0.00 render line.
        total_images: Authoritative image count from the envelope (overrides breakdown
                      count when > 0).

    Returns a multi-line string ready for ``print()``.
    """
    fallback = "\n| COST ANALYSIS SUMMARY | (unavailable — using $0.00 fallback)\n"
    try:
        if not isinstance(breakdown, dict):
            return fallback
        entries: list[dict] = breakdown.get("breakdown") or []
        if not isinstance(entries, list):
            entries = []
        tier: str = str(breakdown.get("cost_tier") or "—")

        research_cost = 0.0
        research_chars = 0
        research_model = "Gemini 2.5 Flash"

        image_cost = 0.0
        image_count = 0
        image_model = "FLUX Schnell"

        voice_cost = 0.0
        voice_chars = 0

        def _sf(value: Any, default: float = 0.0) -> float:
            if value is None:
                return default
            try:
                out = float(value)
                return default if out != out else out
            except (TypeError, ValueError):
                return default

        def _si(value: Any, default: int = 0) -> int:
            try:
                return int(_sf(value, float(default)))
            except (TypeError, ValueError):
                return default

        for e in entries:
            if not isinstance(e, dict):
                continue
            op = str(e.get("operation") or "")
            cost = max(0.0, _sf(e.get("cost_usd"), 0.0))
            units = max(0.0, _sf(e.get("units"), 0.0))
            mk = str(e.get("model_key") or "").lower()

            if op == "text_generation":
                research_cost += cost
                research_chars += _si(units, 0)
                if "deepseek" in mk:
                    research_model = "DeepSeek V4"
                elif "flash" in mk:
                    research_model = "Gemini 2.5 Flash"
                elif "pro" in mk:
                    research_model = "Gemini Pro"
            elif op == "image_generation":
                image_cost += cost
                image_count += _si(units, 0)
                if "nano" in mk or "banana" in mk:
                    image_model = "Nano Banana Pro"
                elif "flux" in mk or "schnell" in mk:
                    image_model = "FLUX Schnell"
                elif "economic" in mk:
                    image_model = "Gemini Flash Lite"
                elif "premium" in mk:
                    image_model = "Gemini Pro Image"
            elif op == "audio_generation":
                voice_cost += cost
                voice_chars += _si(units, 0)

        display_img_count = max(_si(total_images, 0), _si(image_count, 0))

        _fmt = (post_format or "").upper()
        _is_reel = _fmt in ("SEQUENCE_REEL", "DYNAMIC_REEL", "HYBRID_VIDEO")
        _is_sequence = _fmt == "SEQUENCE_REEL" or (_is_reel and display_img_count >= 2)

        if _is_sequence:
            _asset_desc = (
                f"{display_img_count} AI Base Image{'s' if display_img_count != 1 else ''}"
                " \u2192 Compiled to 1 MP4 Sequence Reel"
            )
        elif _fmt in ("DYNAMIC_REEL", "HYBRID_VIDEO"):
            _asset_desc = "1 AI Base Image \u2192 Animated to 1 MP4 Video"
        elif _fmt == "CAROUSEL":
            _asset_desc = (
                f"{display_img_count} AI Image{'s' if display_img_count != 1 else ''} (Carousel Slides)"
            )
        else:
            _asset_desc = (
                f"{display_img_count} AI Image{'s' if display_img_count != 1 else ''} (Static Post)"
            )

        total = research_cost + image_cost + voice_cost
        sep  = "+" + "=" * 62 + "+"
        thin = "  " + "-" * 60

        lines = [
            "",
            sep,
            f"| {'COST ANALYSIS SUMMARY':<60} |",
            f"| {'Tier: ' + tier:<60} |",
            sep,
            f"  {'Visual Assets:'.ljust(36)} {_asset_desc}",
            "",
            f"  - Research ({research_model}):".ljust(38) +
                f"${research_cost:.4f}  ({research_chars:,} chars)",
            f"  - Image Gen ({image_model}):".ljust(38) +
                f"${image_cost:.4f}  ({display_img_count} generation{'s' if display_img_count != 1 else ''})",
            f"  - Voice Gen (ElevenLabs):".ljust(38) +
                f"${voice_cost:.4f}  ({voice_chars:,} characters)",
        ]

        if _is_reel:
            lines.append(
                f"  - Video Render (MoviePy/FFmpeg):".ljust(38) +
                "$0.0000  (local, no API charge)"
            )

        if scheduled_publish:
            lines.append(
                f"  - Scheduled Publish (YouTube):".ljust(38) +
                f"{scheduled_publish} (Shorts)"
            )

        lines += [
            thin,
            f"  {'TOTAL ESTIMATED COST:'.ljust(36)} ${total:.4f}",
            sep,
        ]
        return "\n".join(lines)
    except Exception as exc:  # noqa: BLE001
        _LOG.warning("Cost analysis block failed (%s) — using $0.00 fallback", exc)
        return fallback


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
        carousel_slides = row.get("carousel_image_paths") or []
        if carousel_slides:
            for _sidx, _spath in enumerate(carousel_slides, 1):
                print(_color(f"  Slide {_sidx:02d}:", green), Path(_spath).name if _spath else "(missing)")
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

    # Metadata library — always announce its location so the user knows where
    # all generated titles, descriptions, and video paths are persisted.
    _lib_path = app_config.CONTENT_LIBRARY_PATH
    _lib_rel = path_under_engine(app_config.ENGINE_ROOT, _lib_path)
    print(_color("Metadata library:", cyan), str(_lib_path.resolve()))
    print(_color("  (relative):", cyan), _lib_rel)

    # ── COST ANALYSIS ──────────────────────────────────────────────────────
    # Prefer envelope-level final snapshot (has full multi-variant totals).
    # Fall back to first row's per-variant breakdown for single-variant runs.
    _cost_snap = (
        envelope.get("final_cost_breakdown")
        or (rows[0] if rows else {}).get("cost_breakdown")
    )
    _total_imgs = envelope.get("total_images_generated") or 0
    # Collect the earliest scheduled publish time from the uploaded rows (if any).
    _sched_times = [r["youtube_scheduled_at"] for r in rows if r.get("youtube_scheduled_at")]
    _sched_display = _sched_times[0] if _sched_times else ""
    if _cost_snap:
        print(_format_cost_block(
            _cost_snap,
            post_format=page_ctx.post_format if page_ctx else "",
            total_images=_total_imgs,
            scheduled_publish=_sched_display,
        ))
    print()


def _snapshot_verified_models(*, economic_brain_mode: bool) -> PlannedModels:
    """Determine first-hop model IDs via cost-first Model Router."""
    from avatar_engine.providers.model_router import image_model, text_model
    from avatar_engine.providers.together_image import FLUX_SCHNELL_MODEL

    gem_key = app_config.GEMINI_API_KEY
    text_route = text_model(
        task="caption" if economic_brain_mode else "research",
        log=True,
    )
    img_route = image_model(task="image", log=True)
    humanizer = (
        f"Gemini `{text_route.model_id}` (captions + research) [{text_route.tier}]"
        if economic_brain_mode
        else f"Anthropic Claude `{app_config.CLAUDE_MODEL}` / Gemini `{text_route.model_id}` [{text_route.tier}]"
    )
    # Images always Together FLUX Schnell
    img_pref = FLUX_SCHNELL_MODEL
    research_pref = text_route.model_id
    if not gem_key:
        return PlannedModels(
            image_primary_id=img_pref,
            research_primary_id=research_pref,
            humanizer_summary=humanizer,
        )

    client = genai.Client(api_key=gem_key)
    research_chain = build_model_chain(
        client, capability_type="text", preferred=research_pref
    )
    if text_route.tier != "premium":
        research_chain = [
            m for m in research_chain if "pro" not in m.lower() or "flash" in m.lower()
        ] or [research_pref]
    _LOG.info(
        "Image backend locked | Together AI `%s` (steps=4, ≈$0.003/img) | router_tier=%s",
        img_pref, img_route.tier,
    )
    return PlannedModels(
        image_primary_id=img_pref,
        research_primary_id=(research_chain[0] if research_chain else research_pref),
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
# Topic entity extractor — maps subject text to specific visual context hints
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Script word trimmer — CTA-preserving pre-TTS truncation
# ---------------------------------------------------------------------------

def _trim_script_to_word_limit(script: str, max_words: int = 140) -> str:
    """Trim a voiceover script to *max_words* while always preserving:
      • the opening hook (first sentence)
      • the final CTA sentence

    [ACT N] markers, persona audio tags, and SSML breaks are excluded from the
    word count so they don't inflate the budget estimate.
    Sentences are removed from the *middle* (furthest from start and end) until
    the count falls at or below the limit.
    Returns the original script unchanged when within budget.
    """
    # Strip act markers + audio behavior / persona tags + SSML for word-count only.
    _tag_pat = (
        r'\[(?:ACT\s*\d+|cackles?|chuckles?|cold\s*chuckle|arrogant\s*scoff|'
        r'deep\s*subtle\s*laugh|dry\s*laugh)\]'
        r'|<\s*break\s+[^>]*>'
    )
    _clean = _re.sub(_tag_pat, '', script, flags=_re.IGNORECASE)
    words = _clean.split()
    if len(words) <= max_words:
        return script

    # Split into individual sentences preserving trailing whitespace context.
    sentences = _re.split(r'(?<=[.!?])\s+', script.strip())
    if len(sentences) <= 2:
        # Can't trim further without destroying structure — hard truncate at word boundary.
        return " ".join(words[:max_words])

    # Iteratively drop the middle sentence until budget is met.
    result = list(sentences)
    while True:
        _test = _re.sub(_tag_pat, '', " ".join(result), flags=_re.IGNORECASE)
        if len(_test.split()) <= max_words or len(result) <= 2:
            break
        mid = len(result) // 2
        result.pop(mid)

    trimmed = " ".join(result)
    _LOG_TRIM = __import__("logging").getLogger(__name__)
    _LOG_TRIM.info(
        "Script trimmed: %d → %d words (max=%d, sentences=%d→%d)",
        len(words),
        len(_re.sub(_tag_pat, '', trimmed, flags=_re.IGNORECASE).split()),
        max_words, len(sentences), len(result),
    )
    return trimmed


def _filter_audio_tag_timings(
    word_timings: list[tuple[str, float, float]],
) -> list[tuple[str, float, float]]:
    """Drop ElevenLabs behavior-tag / SSML tokens from subtitle timings."""
    if not word_timings:
        return word_timings
    _tag_re = _re.compile(
        r'^\[?(?:cackles?|chuckles?|cold\s*chuckle|arrogant\s*scoff|'
        r'deep\s*subtle\s*laugh|dry\s*laugh|laughs?|sighs?|whispers?|'
        r'pause|beat|silence)\]?$',
        _re.IGNORECASE,
    )
    _break_re = _re.compile(r'break|time=|^\d+(\.\d+)?s?$', _re.IGNORECASE)
    out: list[tuple[str, float, float]] = []
    for w, s, e in word_timings:
        if not w:
            continue
        tok = w.strip(" .,;:!?<>/\"'")
        if _tag_re.match(tok) or _break_re.match(tok) or tok.lower() in {"<", ">", "/", "s"}:
            continue
        out.append((w, s, e))
    return out


# ---------------------------------------------------------------------------
# Sequential CTA audio stitcher
# ---------------------------------------------------------------------------

def _stitch_audio_sequential(
    narration_path: "Path",
    cta_path: "Path | None",
    output_path: "Path",
    silence_s: float = 1.0,
) -> "Path":
    """Concatenate *narration_path* + *silence_s* of silence + *cta_path* into
    *output_path* and return the combined file path.

    The 1-second gap prevents the CTA from overlapping or bleeding into the
    narration.  Returns *narration_path* unchanged if *cta_path* is absent or
    MoviePy import fails, so the pipeline degrades gracefully.
    """
    if cta_path is None or not cta_path.is_file():
        _LOG.warning("CTA audio not found — using narration-only voice track.")
        return narration_path
    try:
        from moviepy import AudioFileClip  # type: ignore[import]
        from moviepy import concatenate_audioclips  # type: ignore[import]
        from moviepy.audio.AudioClip import AudioArrayClip  # type: ignore[import]
        import numpy as _np_stitch

        narr = AudioFileClip(str(narration_path))
        cta  = AudioFileClip(str(cta_path))
        _sr  = 44100
        _sil = _np_stitch.zeros((int(_sr * silence_s), 2), dtype=_np_stitch.float32)
        silence = AudioArrayClip(_sil, fps=_sr)

        combined = concatenate_audioclips([narr, silence, cta])
        output_path.parent.mkdir(parents=True, exist_ok=True)
        combined.write_audiofile(str(output_path), fps=_sr, logger=None)
        _LOG.info(
            "Audio stitched | narr=%.1fs + %.1fsgap + cta=%.1fs → total=%.1fs → %s",
            narr.duration, silence_s, cta.duration, combined.duration, output_path.name,
        )
        for _c in (narr, cta, silence, combined):
            try:
                _c.close()
            except Exception:
                pass
        return output_path
    except Exception as _exc:
        _LOG.warning("Audio stitch failed (%s) — falling back to narration-only track.", _exc)
        return narration_path


# Known geographic/visual anchors mapped to camera-perspective image directives.
_GEO_ANCHORS: list[tuple[tuple[str, ...], str]] = [
    (("nazca", "geoglyph", "nazca lines"),
     "aerial top-down view, Nazca desert Peru, geoglyph sand line drawings of {subject}, "
     "perfectly visible from high altitude, pale sand surface, minimal colour contrast"),
    (("pyramid", "giza", "great pyramid", "khufu"),
     "wide aerial panoramic view, Great Pyramid complex Giza Plateau Egypt, "
     "dramatic golden sunset sky, vast desert landscape"),
    (("sphinx",),
     "wide-angle frontal view, Great Sphinx of Giza, Giza Plateau Egypt, "
     "golden hour light, desert panorama"),
    (("baalbek", "trilithon"),
     "wide-angle view, Baalbek temple complex Lebanon, "
     "massive stone megaliths, dramatic sky"),
    (("easter island", "moai", "rapa nui"),
     "wide overhead view, Easter Island moai stone statues, "
     "Pacific Ocean coastline visible, dramatic sky"),
    (("stonehenge",),
     "wide aerial view, Stonehenge stone circle, Salisbury Plain England, "
     "golden light, panoramic landscape"),
    (("gobekli", "göbekli", "gobeklitepe"),
     "wide aerial view, Göbekli Tepe excavation site, southeastern Turkey, "
     "ancient stone circles visible from above"),
    (("puma punku", "pumapunku", "tiahuanaco", "tiwanaku"),
     "wide panoramic view, Puma Punku megalithic site, Bolivian Altiplano, "
     "precisely cut stone blocks, mountain backdrop"),
    (("atlantis",),
     "wide cinematic view, submerged ancient city ruins underwater, "
     "ethereal blue-green light, vast underwater cityscape"),
    (("tartaria", "tartarian"),
     "wide aerial view, grand baroque stone architecture, "
     "vast ancient cityscape, dramatic sky"),
    (("sumerian", "sumer", "mesopotamia", "ur"),
     "wide-angle panoramic view, ancient Mesopotamian city, "
     "ziggurat visible, vast plain landscape"),
    (("angkor", "angkor wat"),
     "wide aerial view, Angkor Wat temple complex Cambodia, "
     "jungle canopy surrounding, golden sunrise reflection in moat"),
    (("machu picchu",),
     "wide aerial view, Machu Picchu Inca citadel, Peruvian Andes, "
     "mountain mist, terraced stone architecture"),
    (("teotihuacan", "teotihuacán"),
     "wide aerial view, Teotihuacan Pyramid of the Sun Mexico, "
     "Avenue of the Dead alignment, vast site scale"),
    # ── Artifact / thematic anchors ──────────────────────────────────────────
    (("crystal skull", "crystal skulls"),
     "dramatic wide studio or cave environment, illuminated crystal skull artefact, "
     "volumetric light refraction through quartz crystal, centrally framed, dark background"),
    (("ark of the covenant", "ark of covenant", "holy ark"),
     "wide interior shot, golden Ark of the Covenant in ancient stone chamber, "
     "dramatic divine light beams from above, awe-inspiring scale"),
    (("shroud of turin", "shroud"),
     "wide flat-lay view, ancient linen shroud with faint human image, "
     "soft warm light, archaeological context"),
    (("antikythera", "antikythera mechanism"),
     "wide flat-lay view, Antikythera Mechanism corroded bronze gears on stone surface, "
     "macro-wide shot showing full device, dramatic side lighting"),
    (("lost technology", "precision finish", "machined", "laser cut"),
     "wide archaeological shot, precisely machined ancient stone block or artefact, "
     "impossible geometric tolerances, dramatic raking side light revealing precision"),
    (("oopart", "out of place artefact", "out-of-place artifact", "oopartz"),
     "wide display or archaeological context, mysterious out-of-place ancient object, "
     "dramatic directional light, academic museum-style composition"),
    (("ancient astronaut", "ancient aliens", "extraterrestrial", "alien contact"),
     "wide cinematic illustration, ancient stone carvings depicting humanoid figures "
     "in space suits or flying craft, full-panel view, amber torchlight"),
    (("lost city", "sunken city", "underwater ruins"),
     "wide underwater cinematic view, submerged ancient city ruins, "
     "blue-green volumetric light shafts from surface above, vast architectural scale"),
    (("book of enoch", "enoch", "watchers", "nephilim"),
     "wide dramatic illustration, ancient stone manuscripts and giant humanoid figures, "
     "atmospheric biblical lighting, full-scene composition"),
    (("free energy", "tesla", "ancient electricity", "baghdad battery", "vimana"),
     "wide contextual museum shot, ancient device or artefact suggesting advanced technology, "
     "illuminated display, dramatic dramatic directional lighting"),
]


def _extract_topic_visual_entities(subject: str) -> str:
    """
    Parse the topic/subject string and return a camera-perspective directive
    string grounding image generation in the specific geographic/visual entity
    named.  Falls back to extracted proper nouns when no known anchor matches.
    """
    lower = subject.lower()
    for keywords, directive_template in _GEO_ANCHORS:
        if any(kw in lower for kw in keywords):
            return directive_template.replace("{subject}", subject)
    # Generic fallback: extract capitalised phrases as visual subjects
    candidates = _re.findall(
        r'\b([A-Z][A-Za-z\'-]+(?:\s+[A-Z][A-Za-z\'-]+){0,3})\b', subject
    )
    _stopwords = {
        "The", "A", "An", "In", "At", "On", "But", "And", "Or",
        "Some", "Ancient", "This", "That", "These", "Those",
        "What", "How", "Why", "When", "Where", "Who",
        "Today", "Here", "Now", "Then", "Its", "Our", "Their",
    }
    seen: set[str] = set()
    result: list[str] = []
    for c in candidates:
        cl = c.lower()
        if c not in _stopwords and cl not in seen and len(c) > 3:
            seen.add(cl)
            result.append(c)
    return ", ".join(result[:4]) if result else ""


# ---------------------------------------------------------------------------
# Master Mei Visual Engine — delegates to avatar_engine.mei_visual
# ---------------------------------------------------------------------------

from avatar_engine.mei_visual import (  # noqa: E402
    MEI_DNA_FALLBACK as _MEI_DNA_FALLBACK,
    _BAN_PLAIN as _MEI_BAN_PLAIN_MODERN,
    _PHOTOREAL as _MEI_PHOTOREAL,
    assert_avatar_exists as _assert_mei_avatar,
    build_master_mei_script_act_prompts as _mei_build_act_prompts,
    classify_mei_visual_theme as _classify_mei_visual_theme,
    resolve_master_mei_avatar_path as _resolve_mei_avatar_path,
)


def _strip_audio_behavior_tags(text: str) -> str:
    """Remove bracketed emotional tags / SSML so ElevenLabs never reads them aloud."""
    if not text:
        return ""
    clean = _re.sub(r"<\s*break\s+[^>]*/?\s*>", " ... ", text, flags=_re.IGNORECASE)
    clean = _re.sub(r"<[^>]+>", " ", clean)
    clean = _re.sub(
        r"\[(?:cackles?|chuckles?|cold\s*chuckle|arrogant\s*scoff|"
        r"deep\s*subtle\s*laugh|dry\s*laugh|laughs?|giggles?|sighs?|"
        r"whispers?|pause|beat|silence)[^\]]*\]",
        " ... ",
        clean,
        flags=_re.IGNORECASE,
    )
    clean = _re.sub(r"\s{2,}", " ", clean).strip()
    return clean


def _track_adapter_image(
    cost_tracker: "CostTracker | None",
    adapter: "Any",
) -> int:
    """
    Record image API cost using the adapter's real call count (incl. retries).
    Remote GPU → ``track_gpu_seconds`` (RunPod rates); Together → per-image table.
    Returns the number of API hits charged. Quiet during batch loops.
    """
    n = max(1, int(getattr(adapter, "last_api_call_count", 1) or 1))
    if cost_tracker is None:
        return n
    _cost_key = getattr(adapter, "last_cost_key", None) or "image_flux_schnell"
    _gpu_s = float(getattr(adapter, "last_gpu_seconds", 0) or 0)
    if _gpu_s > 0 or str(_cost_key).startswith("image_remote"):
        try:
            from core_engine.remote_gpu_manager import get_manager  # noqa: PLC0415

            mode = str(getattr(get_manager().client, "mode", None) or "comfyui")
        except Exception:
            mode = "comfyui"
        if _gpu_s <= 0:
            try:
                _gpu_s = float(get_manager().client.last_job_seconds or 0)
            except Exception:
                _gpu_s = 0.0
        if _gpu_s > 0:
            cost_tracker.track_gpu_seconds(_gpu_s, mode=mode, jobs=n)
            return n
    cost_tracker.track_image(model_key=_cost_key, count=n)
    _LOG.debug("CostTracker | image hit key=%s n=%d", _cost_key, n)
    return n


def _track_remote_gpu_batch(
    cost_tracker: "CostTracker | None",
    *,
    seconds_before: float,
    jobs: int,
) -> float:
    """Bill GPU-seconds accumulated on the shared remote client since *seconds_before*."""
    if cost_tracker is None:
        return 0.0
    try:
        from core_engine.remote_gpu_manager import get_manager  # noqa: PLC0415

        client = get_manager().client
        after = float(getattr(client, "total_gpu_seconds", 0) or 0)
        delta = max(0.0, after - float(seconds_before or 0))
        if delta <= 0:
            return 0.0
        cost_tracker.track_gpu_seconds(
            delta, mode=str(getattr(client, "mode", "comfyui")), jobs=max(1, jobs),
        )
        return delta
    except Exception as exc:  # noqa: BLE001
        _LOG.debug("GPU batch cost skipped: %s", exc)
        return 0.0


def _image_act_max_workers(default_legacy: int = 3) -> int:
    """
    Concurrency for sequence-reel act image generation.

    - remote_gpu: ``remote_gpu_max_parallel()`` (shared with audio/video batches).
    - Together legacy: small pool; adapter semaphore still serializes HTTP.
    """
    try:
        from core_engine.remote_gpu_manager import (  # noqa: PLC0415
            is_remote_gpu_enabled,
            remote_gpu_max_parallel,
        )

        if is_remote_gpu_enabled("image"):
            n = remote_gpu_max_parallel()
            _LOG.info(
                "Act image concurrency | remote_gpu max_workers=%d "
                "(REMOTE_GPU_MAX_PARALLEL)",
                n,
            )
            return n
    except Exception:
        pass
    return max(1, int(default_legacy))


def _run_acts_parallel(
    jobs: "dict[int, Any]",
    *,
    max_workers: int | None = None,
) -> "dict[int, Any]":
    """
    Concurrent act jobs — delegates to ``remote_gpu_manager.run_parallel_jobs``
    (generic submission-layer fan-out for image / future WAN video / audio).
    """
    from core_engine.remote_gpu_manager import run_parallel_jobs  # noqa: PLC0415

    if max_workers is None:
        max_workers = _image_act_max_workers()
    return run_parallel_jobs(jobs, max_workers=max_workers)


def _print_video_cost_summary(
    cost_tracker: "CostTracker | None",
    *,
    image_count: int,
    image_model_label: str = "FLUX Schnell",
    variant_index: int = 1,
    total_variants: int = 1,
    batch_images_so_far: int | None = None,
    reel_cost_usd: float | None = None,
) -> None:
    """Print reel-vs-batch cost summary after a video finishes rendering."""
    from core_engine.cost_tracker import print_cost_summary
    from avatar_engine.providers.model_router import format_video_cost_summary

    if cost_tracker is None:
        img_cost = 0.003 * max(0, image_count)
        total = img_cost
    else:
        snap = cost_tracker.to_dict()
        total = float(snap.get("total_estimated_usd") or 0.0)
        img_cost = 0.0
        for entry in snap.get("breakdown") or []:
            if str(entry.get("operation") or "") == "image_generation":
                img_cost += float(entry.get("cost_usd") or 0.0)
        if img_cost <= 0 and image_count > 0:
            img_cost = 0.003 * image_count
    _reel_cost = (
        float(reel_cost_usd)
        if reel_cost_usd is not None
        else (0.003 * max(0, image_count))
    )
    _batch_imgs = (
        int(batch_images_so_far)
        if batch_images_so_far is not None
        else int(image_count)
    )
    print_cost_summary(
        variant_index=int(variant_index),
        total_variants=int(total_variants),
        images_this_reel=int(image_count),
        total_batch_images=_batch_imgs,
        total_reel_cost=float(_reel_cost),
    )
    line = format_video_cost_summary(
        image_count=image_count,
        image_model_label=image_model_label,
        image_cost_usd=img_cost,
        pipeline_cost_usd=total,
    )
    print(line, flush=True)
    _LOG.info(line)


def _split_script_into_act_chunks(script: str, n_acts: int) -> list[str]:
    """Split narration into n_acts sequential ~3–4 s spoken snippets (chronological)."""
    return _segment_script_into_act_snippets(script or "", n_acts)


def _build_master_mei_script_act_prompts(
    *,
    subject: str,
    script: str,
    n_acts: int,
    base_style: str,
    visual_dna: str,
    hook_env: str,
    master_style_anchor: "str | None" = None,
    reference_folder: "str | Path | None" = None,
    use_vision_style: bool = False,
) -> tuple[list[str], set[int], list[str], list[str]]:
    """
    Build N image prompts for Master Mei sequence reels.

    Returns ``(prompts, mei_appearance_slots, roles, negatives)``.
    Avatar.png attaches ONLY for ROLE A (Master) slots — never globally.
    """
    prompts, slots, roles, negatives = _mei_build_act_prompts(
        subject=subject,
        script=script,
        n_acts=n_acts,
        base_style=base_style,
        visual_dna=visual_dna or _MEI_DNA_FALLBACK,
        hook_env=hook_env,
        segment_fn=_split_script_into_act_chunks,
        master_style_anchor=master_style_anchor,
        reference_folder=reference_folder,
        use_vision_style=use_vision_style,
    )
    return prompts, slots, roles, negatives


# ---------------------------------------------------------------------------
# Cinematic act-descriptor generator for multi-image sequence reels
# ---------------------------------------------------------------------------

def _build_reel_act_descriptors(subject: str, n_acts: int, page_id: str = "") -> list[str]:
    """Return n_acts scene-description strings (one per act, index 0 = Act 1).

    All shots use WIDE, AERIAL, or PANORAMIC framing so primary subjects remain
    recognisable and centrally composed within social-media safe zones.
    Extreme / ground-level close-ups are intentionally avoided; medium-wide
    framing is the minimum for any detail shot.
    """
    # master_mei uses _build_master_mei_script_act_prompts() for dynamic matching.
    if (page_id or "").lower() == "master_mei":
        return [
            f"MASTER MEI ACT {i + 1} — {subject}."
            for i in range(n_acts)
        ]

    _ARC: list[str] = [
        # 0 — Act 1: wide establishing anchor (used as fallback descriptor only)
        (
            f"WIDE ESTABLISHING PANORAMA — {subject}. "
            "Iconic ancient monument fully visible, wide aerial or eye-level panoramic view. "
            "Rim-lit stone edges, volumetric light shafts, deep atmospheric haze. "
            "Primary subject centrally framed, full-bleed, no borders, no frames."
        ),
        # 1 — Act 2: wide approach shot — site context visible
        (
            f"WIDE APPROACH SHOT — {subject}. "
            "Broad view of the ancient site as the camera advances — full structure visible, "
            "surrounding landscape and dramatic sky in frame. Amber rim light on stone edges, "
            "sense of impossible ancient scale. Full-bleed, no borders."
        ),
        # 2 — Act 3: medium-wide detail shot (NOT extreme close-up)
        (
            f"MEDIUM-WIDE CONSTRUCTION DETAIL — {subject}. "
            "Mid-range shot showing an anomalous section of the site: perfectly fitted stones, "
            "impossible precision cuts, or unexplained engineering features — with enough "
            "surrounding context to establish scale. Warm amber sidelight, no borders, full-bleed."
        ),
        # 3 — Act 4: wide interior — full chamber visible
        (
            f"WIDE INTERIOR CHAMBER — {subject}. "
            "Full-room view of a deep ancient chamber — carved walls, ceiling height, and "
            "floor all visible. Volumetric cold light shaft from above. "
            "Artefacts in situ, vast spatial depth. No borders, full-bleed."
        ),
        # 4 — Act 5: wide inscription wall — full panel visible
        (
            f"WIDE INSCRIPTION WALL — {subject}. "
            "Full-panel view of an ancient wall or monument face covered in glyphs, "
            "star maps, or mathematical geometry — the entire composition visible in frame. "
            "Amber fire illumination, deep shadow. No borders, full-bleed."
        ),
        # 5 — Act 6: wide contextual artefact in situ
        (
            f"WIDE CONTEXTUAL ARTEFACT SHOT — {subject}. "
            "An alien or advanced object placed naturally within the ancient environment, "
            "full surroundings visible to convey impossible era-context. "
            "Dramatic single-source directional light, wide framing. Full-bleed."
        ),
        # 6 — Act 7: human scale comparison
        (
            f"HUMAN SCALE COMPARISON — {subject}. "
            "Wide shot: lone human silhouette dwarfed by a towering megalithic structure. "
            "Emphasises impossible construction scale. Rim-lit figure against dramatic sky. "
            "Centrally composed, full-bleed."
        ),
        # 7 — Act 8: wide night stellar alignment
        (
            f"WIDE NIGHT STELLAR ALIGNMENT — {subject}. "
            "Exterior wide shot at night — full ancient structure perfectly aligned with "
            "a constellation or celestial arc overhead. Deep indigo sky, Milky Way visible, "
            "cool moonlit stone edges. Cosmic and vast. Full-bleed."
        ),
        # 8 — Act 9: high-altitude aerial geometry
        (
            f"HIGH-ALTITUDE AERIAL GEOMETRY — {subject}. "
            "Top-down or near-vertical aerial view exposing perfect geometric layout, "
            "alignment grid, or civilisation-scale construction pattern. "
            "Golden-hour light, epic landscape. Full-bleed."
        ),
        # 9 — Act 10: sweeping panoramic revelation
        (
            f"SWEEPING PANORAMIC REVELATION — {subject}. "
            "Final wide cinematic panorama — the full mystery in one frame. "
            "Amber dust haze, otherworldly volumetric light, dramatic rim lighting, "
            "hauntingly unresolved. Epic full-bleed, no borders, no frames."
        ),
    ]
    # Build descriptor list for all n_acts; cycle if more than len(_ARC)
    return [_ARC[i % len(_ARC)] for i in range(n_acts)]


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
    cost_tracker: "CostTracker | None" = None,
    per_variant_topics: "list[str] | None" = None,
    batch_angles: "list[BatchAngle] | None" = None,
    uniqueness_guard: "BatchUniquenessGuard | None" = None,
    global_topic_dna: str = "",
    batch_image_counter: "list[int] | None" = None,
    batch_image_lock: "threading.Lock | None" = None,
    render_approval_required: bool = False,
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

    # ── Batch Angle injection (qty > 1) — Global DNA + unique sub-angle ─────
    _batch_angle: "BatchAngle | None" = None
    _batch_angle_block = ""
    _visual_angle_focus = ""
    if batch_angles and variant < len(batch_angles):
        _batch_angle = batch_angles[variant]
        _batch_angle_block = _batch_angle.prompt_block()
        _visual_angle_focus = (_batch_angle.visual_focus or "").strip()
        resolved_subject = _batch_angle.combined_topic
        slug = subject_slug(resolved_subject)
        stem = f"{slug}_v{variant + 1:02d}"
        subject_assets = app_config.ASSETS_DIR / slug
        subject_assets.mkdir(parents=True, exist_ok=True)
        _LOG.info(
            "Variant %d/%d | BATCH ANGLE → %r | hook_style=%s | global=%r",
            variant + 1,
            _batch_angle.total,
            _batch_angle.angle_title,
            _batch_angle.hook_style,
            _batch_angle.global_topic,
        )
    # ── Per-variant topic override (legacy pool path when no angles matrix) ──
    elif per_variant_topics and variant < len(per_variant_topics):
        _vt = per_variant_topics[variant]
        if _vt and _vt != resolved_subject:
            resolved_subject = _vt
            slug = subject_slug(resolved_subject)
            stem = f"{slug}_v{variant + 1:02d}"
            subject_assets = app_config.ASSETS_DIR / slug
            subject_assets.mkdir(parents=True, exist_ok=True)
            _LOG.info(
                "Variant %d | per-variant topic override → %r",
                variant + 1, resolved_subject,
            )

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
    _seo_title_local = ""

    if post_type in ("SMART_BAIT", "ECONOMIC_REEL", "CAROUSEL") and not skip_caption:
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
            _reject_note = ""
            for _uniq_attempt in range(1, MAX_UNIQUENESS_RETRIES + 1):
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
                    niche_disclaimer=page_ctx.niche_disclaimer if page_ctx else "",
                    narrative_mode=page_ctx.narrative_mode if page_ctx else "",
                    batch_angle_block=_batch_angle_block,
                    uniqueness_rejection=_reject_note,
                )
                _seo_title_local = (
                    getattr(caption_engine, "last_seo_title", "") or ""
                ).strip()
                if uniqueness_guard is None:
                    break
                # Gate BEFORE any paid image/TTS — titles, hooks, caption openings
                _desc_first = (caption or "").strip().splitlines()[0] if caption else ""
                _ok, _prior, _score = uniqueness_guard.try_claim(
                    _seo_title_local,
                    overlay_text,
                    _desc_first,
                )
                if _ok:
                    _LOG.info(
                        "Batch uniqueness OK | variant=%d attempt=%d title=%r",
                        variant + 1, _uniq_attempt, (_seo_title_local or overlay_text)[:80],
                    )
                    break
                _reject_note = uniqueness_guard.rejection_instruction(
                    _prior or "", _score
                )
                _LOG.warning(
                    "Batch uniqueness REJECT | variant=%d attempt=%d sim=%.0f%% prior=%r",
                    variant + 1, _uniq_attempt, _score * 100, (_prior or "")[:80],
                )
                if _uniq_attempt >= MAX_UNIQUENESS_RETRIES:
                    _LOG.error(
                        "Batch uniqueness FAILED after %d attempts | variant=%d — "
                        "proceeding with last draft (may be similar).",
                        MAX_UNIQUENESS_RETRIES, variant + 1,
                    )
                    uniqueness_guard.register(
                        _seo_title_local, overlay_text, _desc_first,
                    )
                # Force visual uniqueness even on retry
                if _batch_angle is not None and _visual_angle_focus:
                    visual_subject = (
                        f"{_visual_angle_focus}. {visual_subject or resolved_subject}"
                    ).strip()
            # Ensure visual_subject carries the angle focus
            if _visual_angle_focus and visual_subject:
                if _visual_angle_focus.lower() not in visual_subject.lower():
                    visual_subject = f"{_visual_angle_focus}. {visual_subject}"
            elif _visual_angle_focus and not visual_subject:
                visual_subject = _visual_angle_focus
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
                signature=page_ctx.caption_signature if page_ctx else "",
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
    # SMART_BAIT / LONG_CAPTION_IMAGE / ECONOMIC_REEL / CAROUSEL are hard-locked to the
    # graphite illustration style (no avatar reference image passed to Gemini).
    # graphite illustration pipeline via page_ctx.base_graphite_prompt.
    # The --draw-style CLI flag is SILENTLY IGNORED for these three post types.
    # ====================================================================
    _is_master_mei_page = bool(
        page_ctx and (page_ctx.page_id or "").lower() == "master_mei"
    )
    # Master Mei NEVER uses wonder_feed graphite / pencil-drawing pipelines.
    _graphite_locked = (
        (not _is_master_mei_page)
        and post_type in ("SMART_BAIT", "LONG_CAPTION_IMAGE", "ECONOMIC_REEL", "CAROUSEL")
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
    # Default before conditional mutation so later debug/logs never hit UnboundLocalError
    # when LONG_CAPTION_IMAGE skips the _base_prompt branch or another post type runs.
    _mutated_subject = resolved_subject or ""
    if (
        (not _is_master_mei_page)
        and post_type in ("SMART_BAIT", "ECONOMIC_REEL")
        and overlay_text
        and caption_engine is not None
    ):
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
            _LOG.debug(
                "LONG_CAPTION_IMAGE | scene (post-mutation): %s",
                (_mutated_subject or "")[:120],
            )

    # Batch uniqueness: force angle-specific visual DNA into every image prompt
    if _visual_angle_focus:
        _vf = _visual_angle_focus.rstrip(" .")
        if _vf.lower() not in (effective_atmosphere or "").lower():
            effective_atmosphere = (
                f"{effective_atmosphere.rstrip(' .')}. "
                f"BATCH ANGLE VISUAL FOCUS (unique to this video): {_vf}."
            ).strip()
            _LOG.info(
                "Variant %d | visual angle injected → %r",
                variant + 1, _vf[:80],
            )

    # ====================================================================
    # PHASE A: Image generation
    # Economic mode uses the cheaper image model tier.
    # SMART_BAIT forces avatar_mode="OFF" so the atmospheric context
    # derived from the text hook drives the image prompt unconditionally.
    # ====================================================================
    # SMART_BAIT / LONG_CAPTION_IMAGE / CAROUSEL → atmospheric (avatar OFF).
    # ECONOMIC_REEL → OFF by default, EXCEPT master_mei which MUST likeness-lock
    # Act 1 to channels_config/master_mei/avatar_reference/avatar.png.
    if post_type in ("SMART_BAIT", "LONG_CAPTION_IMAGE", "CAROUSEL"):
        image_avatar_mode = "OFF"
    elif (
        post_type == "ECONOMIC_REEL"
        and page_ctx
        and (page_ctx.page_id or "").lower() == "master_mei"
        and not page_ctx.sequence_force_avatar_off
    ):
        image_avatar_mode = "ON"
    elif post_type == "ECONOMIC_REEL":
        image_avatar_mode = "OFF"
    else:
        image_avatar_mode = avatar_mode

    # NOTE: A hardcoded generic fallback string ("Dramatic, vivid cinematic wide
    # shot. Emotionally charged abstract environment with bold colours…") used
    # to silently override effective_atmosphere here whenever theme extraction
    # didn't produce anything. REMOVED — every image prompt must come strictly
    # from dynamic LLM generation (visual_subject / theme extraction) + the
    # page's own configured atmosphere_style, never a canned literal string.
    # When theme extraction yields nothing, effective_atmosphere simply keeps
    # its already-assigned value (atmosphere_style, which is itself page-config
    # driven, not a single global hardcoded phrase).

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

    # Isolated channels (ancient_knowledge): adapter owns the image prompt.
    # VisualArchitect.build_prompt() returns compose_image_prompt(); the old
    # hardcoded ancient_knowledge lock is not applied. master_mei keeps its lock.
    _mm_image_prompt_override: str = ""
    _isolated_channel = ChannelFactory.is_isolated(
        (page_ctx.page_id or "").lower() if page_ctx else ""
    )
    if (
        page_ctx
        and (page_ctx.page_id or "").lower() == "master_mei"
        and not _isolated_channel
    ):
        import re as _re_mm
        # Act-1 / Hook STRICT MANDATE: ROLE A Master Mei (traditional only).
        # Dynamic environment: ~70% high-altitude nature / ~30% open-air shrines.
        try:
            from avatar_engine.visual_roles import (
                compose_scene_01_prompt as _mei_s1,
                pick_mei_meditation_environment as _mei_pick_env,
            )
            _hook_env = _mei_pick_env(
                episode_seed=resolved_subject or "",
                spoken_beat=caption if caption and caption != "(skipped)" else "",
            )
            _mm_image_prompt_override = _mei_s1(
                hook_env=_hook_env,
                spoken_beat=caption if caption and caption != "(skipped)" else "",
                subject=resolved_subject or "",
                episode_seed=resolved_subject or "",
            )
        except Exception:
            _hook_env = "towering jagged mountain cliff above a sea of clouds"
            _mm_image_prompt_override = (
                "Master Mei — wise East Asian martial arts grandmaster with pure "
                "snow-white hair in a topknot, two long white locks framing his chest, "
                "extra-long bushy white eyebrows extending past temples, and a long "
                "white beard reaching his mid-chest. White robe and black vest with "
                "gold embroidery. Meditating in lotus posture atop a towering jagged "
                "mountain cliff above a sea of clouds. Serene sunrise light, vast "
                "alpine vista, dramatic cinematic framing."
            )
        # ROLE A only — traditional Master Mei; never fuse neon city / cybernetics onto him
        _mm_dna_clean = (page_ctx.master_mei_visual_dna or _MEI_DNA_FALLBACK)
        _mm_dna_clean = _re_mm.sub(
            r"\b(?:cyber|bionic|neon|vr|headset|neural|wire|cable|implant|biomechanical)\w*\b",
            "",
            _mm_dna_clean,
            flags=_re_mm.IGNORECASE,
        )
        _mm_dna_clean = _re_mm.sub(r"\s{2,}", " ", _mm_dna_clean).strip(" ,.")
        effective_atmosphere = _mm_image_prompt_override
        _LOG.info(
            "master_mei | purged graphite path | Scene1 FLUX lock: %s",
            _mm_image_prompt_override[:120],
        )

    adapter: GeminiImageAdapter | None = None

    # Per-variant image tracking initialised here so _return_dict always has these keys
    # even when skip_image is True or when image generation raises an exception.
    _carousel_image_paths: list[str] = []
    _images_generated_this_variant: int = 0
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
        architect = VisualArchitect(channel=ChannelFactory.from_env())
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

        # Isolated adapter already composed the prompt inside VisualArchitect.
        if _isolated_channel:
            _LOG.info(
                "%s IMAGE PROMPT | adapter compose_image_prompt: %s",
                (page_ctx.page_id if page_ctx else "isolated"),
                image_prompt[:180],
            )
        elif _mm_image_prompt_override:
            image_prompt = _mm_image_prompt_override
            _LOG.info(
                "master_mei IMAGE PROMPT LOCK | hard-override applied: %s",
                image_prompt[:180],
            )
        img_model_id = None
        _page_cost = page_ctx.cost_tier if page_ctx is not None else None
        # Page-level explicit override has highest priority — still logged via router.
        # model_api_flows Together model (when active) wins over page IMAGE_MODEL_OVERRIDE.
        _override = None
        if page_ctx is not None and page_ctx.image_model_override:
            _override = page_ctx.image_model_override
        try:
            from model_api_flows import (  # noqa: PLC0415
                is_flow_explicitly_set as _flow_set,
                resolved_together_image_model as _flow_img_model,
            )

            _flow_model = _flow_img_model()
            if _flow_set() and _flow_model:
                _override = _flow_model
        except Exception:
            pass
        from avatar_engine.providers.model_router import image_model as _route_image
        _img_route = _route_image(
            task="image",
            page_cost_tier=_page_cost,
            model_override=_override,
            preferred=(
                app_config.GEMINI_ECONOMIC_IMAGE_MODEL
                if economic
                else app_config.GEMINI_IMAGE_MODEL
            ),
            use_premium=True if (_page_cost or "").lower() == "premium" else None,
            log=True,
        )
        img_model_id = app_config.normalize_image_model_id(_img_route.model_id)
        if economic or _img_route.tier == "cheap":
            _LOG.info(
                "COST-FIRST LOCK | variant=%s | tier=%s | image_model=%s | text_brain=%s",
                variant + 1,
                _img_route.tier,
                img_model_id,
                app_config.GEMINI_ECONOMIC_BRAIN_MODEL,
            )
        # Resolve style reference image.
        # For wonder_feed the reference file is pinned to an absolute path so
        # that STYLE_REFERENCE_DIR misconfigs or directory-scan order can never
        # cause the wrong image (or no image) to be sent to Gemini.
        import os as _os
        _WF_STYLE_REF_STR = (
            r"G:\My Drive\Z sosFiles\Z_act\@ NETWORK"
            r"\@MEDIAUPSCALE_FACTORY_DYNAMIC_CONTENT\Unified Multi-Page Factory"
            r"\channels_config\wonder_feed\style_reference\Screenshot 2026-06-01 183244.png"
        )
        _WF_STYLE_REF = Path(_WF_STYLE_REF_STR)
        _style_ref_path: Path | None = None
        _style_ref_paths: list[Path] = []
        _style_ref_weight: float = 0.72
        if page_ctx and (page_ctx.page_id or "").lower() == "wonder_feed":
            print(f"[DEBUG_STYLE_ENGINE] Target image reference assigned: {_WF_STYLE_REF_STR}")
            if _os.path.exists(_WF_STYLE_REF_STR):
                _style_ref_path = _WF_STYLE_REF
                _style_ref_paths = [_WF_STYLE_REF]
                print(f"[DEBUG_STYLE_ENGINE] Verification PASSED — style reference loaded.")
                logging.info("wonder_feed STYLE REF LOCK | pinned → %s", _WF_STYLE_REF.name)
            else:
                print(f"[ERROR_STYLE_ENGINE] Verification Failed. Path missing: {_WF_STYLE_REF_STR}")
                logging.warning(
                    "wonder_feed style reference not found at expected path: %s", _WF_STYLE_REF
                )
        elif page_ctx:
            # MODULE 3 — dynamic per-page resolution: channels_config/<page>/style_reference/
            # (or explicit STYLE_REFERENCE_DIR override), 2-3 images, IP-Adapter-style weight.
            _style_ref_paths = page_ctx.resolve_style_reference_images()
            _style_ref_weight = page_ctx.style_reference_weight
            if _style_ref_paths:
                _style_ref_path = _style_ref_paths[0]

        try:
            import os as _img_os
            print("\n" + "=" * 60)
            print("[DEBUG] IMAGE PIPELINE INITIALIZATION")
            print(f"[DEBUG] Model            : {img_model_id or bm.image_primary_id}")
            if page_ctx and (page_ctx.page_id or "").lower() == "wonder_feed":
                _dbg_ref = _WF_STYLE_REF_STR
                print(f"[DEBUG] Style Ref Path   : {_dbg_ref}")
                print(f"[DEBUG] Ref File Exists  : {_img_os.path.exists(_dbg_ref)}")
            else:
                print(f"[DEBUG] Style Ref Path   : {_style_ref_path or '(none — text-only prompt)'}")
            print(f"[DEBUG] Compiled prompt  : {effective_atmosphere[:200]}")
            print("=" * 60 + "\n")
            # Routes to RemoteGPUImageAdapter when ENABLE_REMOTE_GPU_WORKFLOWS=true
            adapter = get_image_adapter(
                model_id=img_model_id,
                page_cost_tier=_page_cost,
                tier=_img_route.tier,
            )
            # master_mei: always prefer forced avatar.png over cycled assets
            _gen_ref = effective_ref_path if image_avatar_mode == "ON" else None
            _gen_weight: float | None = None
            if (
                page_ctx
                and (page_ctx.page_id or "").lower() == "master_mei"
                and image_avatar_mode == "ON"
            ):
                _forced = page_ctx.forced_avatar_reference_path
                if _forced is not None and _forced.is_file():
                    _gen_ref = _forced
                _gen_weight = page_ctx.avatar_image_weight
            _act1_role = None
            _act1_neg = None
            if page_ctx and (page_ctx.page_id or "").lower() == "master_mei":
                from avatar_engine.visual_roles import (
                    ROLE_A_NEGATIVE,
                    ROLE_MASTER,
                )
                _act1_role = ROLE_MASTER
                _act1_neg = ROLE_A_NEGATIVE
            img_path_display = adapter.generate(
                image_prompt,
                reference_image_path=_gen_ref,
                style_reference_path=_style_ref_path,
                style_reference_paths=_style_ref_paths or None,
                style_reference_weight=_style_ref_weight,
                output_stem=stem,
                output_directory=subject_assets,
                avatar_mode=image_avatar_mode,
                reference_image_weight=_gen_weight,
                visual_role=_act1_role,
                negative_prompt=_act1_neg,
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

    # ── COST TRACKING: image generation ──────────────────────────────────────
    # For CAROUSEL the first generate() call above is Slide 01.
    # The Slide 02 and 03 generation loop below tracks each separately.
    # Charge real API hits (retries / chain advances), not just successful frames.
    if not skip_image:
        _n_hits = _track_adapter_image(cost_tracker, adapter) if adapter is not None else 1
        _images_generated_this_variant = _n_hits
        if cost_tracker is None:
            _images_generated_this_variant = 1

    # ── CAROUSEL: generate slides 2 and 3 with distinct viewpoint directives ─
    # Slide 1 is the image already generated above (same image_prompt).
    # Slides 2 and 3 use the same base style but with a different cinematographic
    # angle so the three frames form a coherent visual narrative for the post.
    if post_type == "CAROUSEL" and not skip_image and raw_bg_path is not None and adapter is not None:
        _carousel_image_paths.append(str(raw_bg_path))  # slide 01
        _carousel_slide_directives = [
            (
                "SLIDE 02 — DETAIL CLOSE-UP",
                "Extreme close-up shot. Tangible surface textures, hyper-detailed craftsmanship, "
                "micro-scale inscriptions or material patterns. Fill the frame edge-to-edge.",
            ),
            (
                "SLIDE 03 — ATMOSPHERIC REVELATION",
                "Dramatic low-angle or aerial perspective. Symbolic composition, mysterious "
                "atmospheric haze, sense of scale and ancient grandeur revealed from a new angle.",
            ),
        ]
        for _ci, (_slide_label, _slide_extra) in enumerate(_carousel_slide_directives):
            _slide_num = _ci + 2  # 2, 3
            _slide_prompt = (
                f"{image_prompt} "
                f"{_slide_label}: {_slide_extra} "
                f"(Carousel frame {_slide_num} of 3 — maintain visual consistency with slide 01.)"
            )
            try:
                _slide_img = adapter.generate(
                    _slide_prompt,
                    reference_image_path=effective_ref_path if image_avatar_mode == "ON" else None,
                    output_stem=f"{stem}_slide_{_slide_num:02d}",
                    output_directory=subject_assets,
                    avatar_mode=image_avatar_mode,
                )
                _carousel_image_paths.append(str(_slide_img))
                _images_generated_this_variant += _track_adapter_image(cost_tracker, adapter)
                _LOG.info("CAROUSEL slide %d generated → %s", _slide_num, Path(_slide_img).name)
            except Exception as _ce:  # noqa: BLE001
                _LOG.warning("CAROUSEL slide %d generation failed: %s — skipping.", _slide_num, _ce)
    elif post_type == "CAROUSEL" and not skip_image and raw_bg_path is not None:
        _carousel_image_paths.append(str(raw_bg_path))

    # ── SEQUENCE REEL: dense ~4 s/act scene-to-audio sync ─────────────────
    # Early voiceover script → segment into N spoken snippets → one Gemini
    # image per snippet → Phase D TTS + compile (act_dur = audio / N).
    _sequence_image_paths: list = []
    _early_seq_script: str = ""
    # Episode assets: outputs/<page>/assets/<episode_id>/
    # Clips always:   outputs/<page>/clips/
    _episode_id = f"ep_{datetime.now().strftime('%Y%m%d_%H%M')}"
    _episode_assets_dir: Path | None = None
    if post_type == "ECONOMIC_REEL":
        _episode_assets_dir = Path(app_config.ASSETS_DIR) / _episode_id
        _episode_assets_dir.mkdir(parents=True, exist_ok=True)
        Path(app_config.PAGE_OUTPUTS_DIR, "clips").mkdir(parents=True, exist_ok=True)
        _LOG.info("Episode assets directory → %s", _episode_assets_dir.resolve())
    if (
        page_ctx
        and page_ctx.enable_sequence_reel
        and post_type == "ECONOMIC_REEL"
        and raw_bg_path is not None
        and adapter is not None
    ):
        _ak_base_style = (
            page_ctx.illustration_style.rstrip(" .")
            if page_ctx.illustration_style
            else page_ctx.atmosphere_style.rstrip(" .")
        )
        _is_mm = (page_ctx.page_id or "").lower() == "master_mei"
        _seq_words = page_ctx.reel_narration_words if page_ctx else 140
        _seq_min = page_ctx.reel_narration_min_words if page_ctx else 110

        # Master Mei: duration profile locks words + frame count (7/9/10).
        # Other pages: shared plan_scenes() engine (or legacy dense/hook).
        _planned_scene_durs: "list[float] | None" = None
        if _is_mm:
            from avatar_engine.mei_narrative import resolve_mei_duration_profile

            _mei_prof = resolve_mei_duration_profile(page_ctx.reel_duration)
            _seq_n = int(_mei_prof["frames"])
            _seq_words = int(_mei_prof["words_target"])
            _seq_min = int(_mei_prof["words_min"])
            _LOG.info(
                "Master Mei duration profile | target=%ss words=%d–%d (tgt %d) frames=%d "
                "max_consec_training=%d",
                _mei_prof["target_s"],
                _mei_prof["words_min"],
                _mei_prof["words_max"],
                _mei_prof["words_target"],
                _mei_prof["frames"],
                _mei_prof["max_consec_training"],
            )
        else:
            if page_ctx.uses_plan_scenes_pacing:
                _planned_scene_durs = _plan_scenes(
                    page_ctx.reel_duration,
                    page_ctx.scene_duration,
                    progressive_start_s=page_ctx.scene_progressive_start_s,
                    progressive_step_every=page_ctx.scene_progressive_step_every,
                    progressive_step_s=page_ctx.scene_progressive_step_s,
                    progressive_cap_s=page_ctx.scene_progressive_cap_s,
                    min_scenes=page_ctx.reel_image_min_count,
                    max_scenes=page_ctx.reel_image_count,
                )
                _seq_n = len(_planned_scene_durs)
            elif page_ctx.reel_use_hook_body_pacing:
                _seq_n = _compute_hook_body_act_count(
                    page_ctx.reel_duration,
                    hook_hold_s=page_ctx.reel_hook_hold_s,
                    body_hold_s=page_ctx.reel_body_hold_s,
                    min_acts=page_ctx.reel_image_min_count,
                    max_acts=page_ctx.reel_image_count,
                )
            else:
                _seq_n = _compute_dense_act_count(
                    page_ctx.reel_duration,
                    seconds_per_act=page_ctx.reel_seconds_per_act,
                    min_acts=page_ctx.reel_image_min_count,
                    max_acts=page_ctx.reel_image_count,
                )

        # ── Work dir under episode assets (final scene_XX.png copies land here) ──
        assert _episode_assets_dir is not None
        _reel_img_dir = _episode_assets_dir / "work"
        _reel_img_dir.mkdir(parents=True, exist_ok=True)
        if _is_mm:
            _pace_label = "mei_profile"
        elif locals().get("_planned_scene_durs"):
            _pace_label = (
                f"plan_scenes:{page_ctx.scene_duration} | "
                f"{_describe_scene_plan(_planned_scene_durs)}"
            )
        elif page_ctx.reel_use_hook_body_pacing:
            _pace_label = (
                f"hook={page_ctx.reel_hook_hold_s:.1f}s "
                f"body={page_ctx.reel_body_hold_s:.1f}s"
            )
        else:
            _pace_label = f"dense={page_ctx.reel_seconds_per_act:.1f}s/act"
        _LOG.info(
            "Sequence reel | acts=%d | video_length=%.0fs (window %.0f–%.0fs) | "
            "pacing=%s | episode=%s | work=%s",
            _seq_n,
            page_ctx.reel_duration,
            page_ctx.reel_duration_target_min,
            page_ctx.reel_duration_target_max,
            _pace_label,
            _episode_id,
            _reel_img_dir,
        )

        # Early script for ALL sequence pages → exact spoken-beat image prompts
        # Uniqueness gate: reject near-duplicate scripts BEFORE any act images / TTS.
        if caption_engine is not None:
            try:
                _seq_reject = ""
                _hooks_for_seq = list(generated_hooks_cache or [])
                for _seq_attempt in range(1, MAX_UNIQUENESS_RETRIES + 1):
                    _early_seq_script = caption_engine.generate_sequence_voiceover(
                        resolved_subject,
                        page_niche=page_ctx.content_niche,
                        persona_voice=page_ctx.tts_voice_preference or (
                            "calm, deep, highly wise, authoritative ancient master"
                            if _is_mm
                            else "investigative, neutral, immersive"
                        ),
                        n_acts=_seq_n,
                        duration_s=page_ctx.reel_duration,
                        total_words_target=_seq_words,
                        economic=economic,
                        niche_disclaimer=page_ctx.niche_disclaimer,
                        cta_line="",
                        narrative_mode=page_ctx.narrative_mode,
                        batch_angle_block=_batch_angle_block,
                        uniqueness_rejection=_seq_reject,
                        previously_generated_hooks=_hooks_for_seq,
                    ) or ""
                    _early_ok = (
                        len((_early_seq_script or "").split()) >= _seq_min
                        or len((_early_seq_script or "").strip()) >= 150
                    )
                    if not _early_ok:
                        _LOG.warning(
                            "SEQUENCE_REEL | early script short (%d words) — regenerating…",
                            len((_early_seq_script or "").split()),
                        )
                        if cost_tracker is not None:
                            cost_tracker.track_text(char_count=len(_early_seq_script or "") + 500)
                        _early_seq_script = caption_engine.generate_sequence_voiceover(
                            resolved_subject,
                            page_niche=page_ctx.content_niche,
                            persona_voice=(
                                "VERY SLOW meditative ancient master — write a LONG full script"
                                if _is_mm
                                else "investigative documentary narrator — write a LONG full script"
                            ),
                            n_acts=_seq_n,
                            duration_s=page_ctx.reel_duration,
                            total_words_target=max(_seq_words, _seq_min + 20),
                            economic=economic,
                            niche_disclaimer=(
                                (page_ctx.niche_disclaimer or "")
                                + f" CRITICAL: Write AT LEAST {_seq_min} words OR 150+ characters. "
                                "Short scripts are forbidden."
                            ),
                            cta_line="",
                            narrative_mode=page_ctx.narrative_mode,
                            batch_angle_block=_batch_angle_block,
                            uniqueness_rejection=_seq_reject,
                            previously_generated_hooks=_hooks_for_seq,
                        ) or _early_seq_script
                    if uniqueness_guard is None or not _early_seq_script:
                        break
                    _opening = " ".join((_early_seq_script or "").split()[:28])
                    _ok_s, _prior_s, _score_s = uniqueness_guard.try_claim(_opening)
                    if _ok_s:
                        _LOG.info(
                            "SEQUENCE uniqueness OK | variant=%d attempt=%d",
                            variant + 1, _seq_attempt,
                        )
                        break
                    _seq_reject = uniqueness_guard.rejection_instruction(
                        _prior_s or "", _score_s
                    )
                    _LOG.warning(
                        "SEQUENCE uniqueness REJECT | variant=%d attempt=%d sim=%.0f%%",
                        variant + 1, _seq_attempt, _score_s * 100,
                    )
                    if _seq_attempt >= MAX_UNIQUENESS_RETRIES:
                        uniqueness_guard.register(_opening)
                        break
                if _early_seq_script:
                    _early_seq_script = _trim_script_to_word_limit(
                        _early_seq_script, max_words=_seq_words + 15
                    )
                    if cost_tracker is not None:
                        cost_tracker.track_text(char_count=len(_early_seq_script))
                    _LOG.info(
                        "SEQUENCE_REEL | early script ready for act-image sync (%d words, %d acts)",
                        len(_early_seq_script.split()), _seq_n,
                    )
            except Exception as _early_exc:  # noqa: BLE001
                _LOG.warning(
                    "SEQUENCE_REEL early script failed (%s) — falling back to topic.",
                    _early_exc,
                )
                _early_seq_script = ""

        _spoken_snippets = _segment_script_into_act_snippets(
            _early_seq_script
            or (caption if caption and caption != "(skipped)" else resolved_subject),
            _seq_n,
        )

        # Act 1 base: Master Mei regenerates Scene 1 via FLUX (never graphite raw_bg).
        # Other pages keep the primary background as Act 1.
        if not _is_mm:
            _sequence_image_paths.append(
                _ensure_sequence_image(raw_bg_path, fallback=raw_bg_path)
            )
        _act1_base = raw_bg_path

        if _is_mm:
            try:
                from avatar_engine.visual_roles import (
                    pick_mei_meditation_environment as _mei_pick_env_prod,
                )
                _hook_env = _mei_pick_env_prod(
                    episode_seed=resolved_subject or "",
                    spoken_beat=_early_seq_script or (
                        caption if caption and caption != "(skipped)" else ""
                    ),
                )
            except Exception:
                _hook_env = "towering jagged mountain cliff above a sea of clouds"
            from avatar_engine.mei_visual import (
                MASTER_STYLE_ANCHOR_DEFAULT as _MM_STYLE_DEFAULT_PROD,
            )
            from avatar_engine.style_reader import (
                default_master_mei_ref_folder as _mm_ref_folder_prod,
            )
            _mm_fixed_anchor = (
                page_ctx.master_style_anchor or _MM_STYLE_DEFAULT_PROD
            ).strip() or _MM_STYLE_DEFAULT_PROD
            _mm_prompts, _mm_slots, _mm_roles, _mm_negatives = (
                _build_master_mei_script_act_prompts(
                    subject=resolved_subject,
                    script=_early_seq_script or (
                        caption if caption and caption != "(skipped)" else resolved_subject
                    ),
                    n_acts=_seq_n,
                    base_style=_ak_base_style,
                    visual_dna=page_ctx.master_mei_visual_dna or _MEI_DNA_FALLBACK,
                    hook_env=_hook_env,
                    master_style_anchor=_mm_fixed_anchor,
                    reference_folder=_mm_ref_folder_prod(),
                    use_vision_style=False,  # GOLDEN LOCK — no Vision prompt rewrite
                )
            )
            _LOG.info(
                "MASTER_MEI 3-ROLE distribution | roles=%s | avatar_slots=%s",
                _mm_roles, sorted(_mm_slots),
            )
            # MODULE 3 — dynamic per-page style reference (channels_config/<page>/style_reference/)
            _mm_style_refs = page_ctx.resolve_style_reference_images()
            _mm_style_weight = page_ctx.style_reference_weight
            if _mm_style_refs:
                _LOG.info(
                    "MASTER_MEI | %d style reference image(s) loaded (weight=%.2f) → %s",
                    len(_mm_style_refs), _mm_style_weight,
                    ", ".join(p.name for p in _mm_style_refs),
                )
            # ZERO HALLUCINATION: always bind canonical avatar.png for Mei frames
            _mm_avatar_ref: "Path | None" = None
            _mm_avatar_w: float | None = None
            if not page_ctx.sequence_force_avatar_off:
                _mm_avatar_ref = _assert_mei_avatar(app_config.ENGINE_ROOT)
                if not _mm_avatar_ref.is_file():
                    # Fallback to page_loader path if assert path missing
                    _forced_mm = page_ctx.forced_avatar_reference_path
                    if _forced_mm is not None and _forced_mm.is_file():
                        _mm_avatar_ref = _forced_mm
                    else:
                        _LOG.error(
                            "MASTER_MEI | avatar.png MISSING — Mei frames will fail "
                            "zero-hallucination lock. Expected: %s",
                            _resolve_mei_avatar_path(app_config.ENGINE_ROOT),
                        )
                        _mm_avatar_ref = None
                if _mm_avatar_ref is not None:
                    _mm_avatar_w = page_ctx.avatar_image_weight
                    _LOG.info(
                        "MASTER_MEI | avatar likeness LOCKED → %s (weight=%.2f)",
                        _mm_avatar_ref, _mm_avatar_w or 0.0,
                    )
            # PARALLEL IMAGE GENERATION — build all act jobs (cheap, sequential),
            # then fire them concurrently (ThreadPoolExecutor max_workers=5). The
            # Together AI adapter's global rate limiter still paces the actual
            # HTTP calls, so this only removes orchestration/IO wait time.
            _mm_act_meta: dict[int, dict[str, Any]] = {}
            _mm_act_jobs: dict[int, Any] = {}
            # Include Act 1 (index 0) — FLUX Scene1 meditation lock; never graphite base
            for _act_i in range(0, _seq_n):
                _snippet = (
                    _spoken_snippets[_act_i]
                    if _act_i < len(_spoken_snippets)
                    else resolved_subject
                )
                _act_prompt = _mm_prompts[_act_i] if _act_i < len(_mm_prompts) else (
                    "Cinematic photorealistic dystopian visual scene, no text."
                )
                # Hard strip any legacy graphite / drawing pollution before FLUX
                import re as _re_flux_purge
                _act_prompt = _re_flux_purge.sub(
                    r"(?i)\b(?:a\s+precise\s+)?graphite\s+(?:drawing|scene|sketch|illustration)"
                    r"(?:\s+of\s+a\s+man)?\b[^.]*\.?",
                    " ",
                    _act_prompt,
                )
                _act_prompt = _re_flux_purge.sub(
                    r"(?i)\bOriginal\s+scene\s+concept\s*:\s*",
                    "",
                    _act_prompt,
                )
                _act_prompt = _re_flux_purge.sub(r"\s{2,}", " ", _act_prompt).strip(" ,.")
                _act_role = (
                    _mm_roles[_act_i] if _act_i < len(_mm_roles) else "slave"
                )
                _act_neg = (
                    _mm_negatives[_act_i] if _act_i < len(_mm_negatives) else ""
                )
                _act_theme = _classify_mei_visual_theme(
                    f"{resolved_subject} {_snippet}"
                )
                # CONDITIONAL AVATAR: Role A (Master) only — never disciples/slaves
                _use_avatar = (
                    _mm_avatar_ref is not None
                    and _act_i in _mm_slots
                    and _act_role == "master"
                )
                _fallback_dest = _reel_img_dir / f"{stem}_act{_act_i + 1:02d}_fallback.png"
                _mm_act_meta[_act_i] = {
                    "snippet": _snippet,
                    "theme": _act_theme,
                    "role": _act_role,
                    "use_avatar": _use_avatar,
                    "fallback_dest": _fallback_dest,
                }
                _mm_act_jobs[_act_i] = functools.partial(
                    adapter.generate,
                    _act_prompt,
                    output_stem=f"{stem}_act{_act_i + 1:02d}",
                    output_directory=_reel_img_dir,
                    reference_image_path=_mm_avatar_ref if _use_avatar else None,
                    avatar_mode="ON" if _use_avatar else "OFF",
                    reference_image_weight=_mm_avatar_w if _use_avatar else None,
                    # Style refs OFF for Scene 3 / penultimate — avoid graphite ref bleed
                    style_reference_paths=(
                        _mm_style_refs
                        if _use_avatar and _act_i not in (2, _seq_n - 2)
                        else None
                    ) or None,
                    style_reference_weight=(
                        _mm_style_weight
                        if _use_avatar and _act_i not in (2, _seq_n - 2)
                        else None
                    ),
                    visual_role=_act_role,
                    negative_prompt=_act_neg or None,
                )

            _mm_gpu_baseline = 0.0
            try:
                from core_engine.remote_gpu_manager import (  # noqa: PLC0415
                    get_manager as _get_rgpu_mm,
                    is_remote_gpu_enabled as _rgpu_on_mm,
                )

                if _rgpu_on_mm("image"):
                    _mm_gpu_baseline = float(
                        getattr(_get_rgpu_mm().client, "total_gpu_seconds", 0) or 0
                    )
            except Exception:
                _mm_gpu_baseline = 0.0

            _mm_act_results = _run_acts_parallel(_mm_act_jobs)

            _mm_ok = 0
            for _act_i in range(0, _seq_n):
                _meta = _mm_act_meta[_act_i]
                _snippet = _meta["snippet"]
                _act_theme = _meta["theme"]
                _use_avatar = _meta["use_avatar"]
                _fallback_dest = _meta["fallback_dest"]
                _result = _mm_act_results.get(_act_i)
                if isinstance(_result, Exception):
                    _LOG.warning(
                        "MASTER_MEI act %d failed (%s) — blinded fallback.",
                        _act_i + 1, _result,
                    )
                    _fb = _act1_base if _act_i > 0 and _sequence_image_paths else raw_bg_path
                    _sequence_image_paths.append(
                        _ensure_sequence_image(
                            _fallback_dest, fallback=_fb, target_path=_fallback_dest,
                        )
                    )
                    continue
                _fb = (
                    _sequence_image_paths[0]
                    if _sequence_image_paths
                    else raw_bg_path
                )
                _act_img = _ensure_sequence_image(
                    _result, fallback=_fb, target_path=_fallback_dest,
                )
                _sequence_image_paths.append(_act_img)
                _mm_ok += 1
                if _act_i == 0:
                    _act1_base = _act_img
                _LOG.info(
                    "MASTER_MEI sequence | act %d/%d | avatar=%s theme=%s | beat=%.40s…",
                    _act_i + 1, _seq_n,
                    "ON" if _use_avatar else "OFF", _act_theme, _snippet,
                )
            _mm_gpu_delta = _track_remote_gpu_batch(
                cost_tracker, seconds_before=_mm_gpu_baseline, jobs=_mm_ok,
            )
            if _mm_gpu_delta <= 0 and _mm_ok:
                for _ in range(_mm_ok):
                    _images_generated_this_variant += _track_adapter_image(
                        cost_tracker, adapter
                    )
            else:
                _images_generated_this_variant += _mm_ok
        else:
            # ancient_knowledge (+ other sequence pages): spoken-snippet → visual
            _act_descriptors = _build_reel_act_descriptors(
                resolved_subject, _seq_n, page_id=page_ctx.page_id or ""
            )
            _topic_entity_ctx = _extract_topic_visual_entities(resolved_subject)
            _topic_entity_prefix = (
                f"VISUAL SUBJECT: {_topic_entity_ctx}. " if _topic_entity_ctx else ""
            )
            from avatar_engine.prompt_alignment import build_aligned_visual_block
            _parallax_directive = (
                "DEPTH LAYERS: Foreground — visible elements such as dust particles, "
                "a stone arch, ancient doorframe, or human observer silhouette. "
                "Background — distant temple façade, starfield, horizon, or vast landscape. "
                "Force multi-plane depth separation so camera motion creates true parallax. "
            )
            _lighting_tail = (
                "LIGHTING: Dramatic directional light — volumetric shafts, strong rim lighting "
                "on stone edges, deep chiaroscuro shadows. High-contrast graphite cinematic look. "
                "No flat or muddy lighting. Ultra-realistic photography, 35mm film grain, "
                "full-bleed, no borders, no frames."
            )
            # PARALLEL IMAGE GENERATION — build all act jobs (cheap, sequential),
            # then fire them concurrently (ThreadPoolExecutor max_workers=5); the
            # Together AI adapter's global rate limiter still paces the actual
            # HTTP calls under the hood.
            _sr_act_meta: dict[int, dict[str, Any]] = {}
            _sr_act_jobs: dict[int, Any] = {}
            for _act_i in range(1, _seq_n):
                _snippet = (
                    _spoken_snippets[_act_i]
                    if _act_i < len(_spoken_snippets)
                    else resolved_subject
                )
                _prev_snip = (
                    _spoken_snippets[_act_i - 1]
                    if _act_i > 0 and (_act_i - 1) < len(_spoken_snippets)
                    else ""
                )
                _act_desc = (
                    _act_descriptors[_act_i]
                    if _act_i < len(_act_descriptors)
                    else f"ACT {_act_i + 1}: {resolved_subject}."
                )
                _align = build_aligned_visual_block(
                    spoken_snippet=_snippet,
                    act_index=_act_i,
                    total_acts=_seq_n,
                    main_subject=resolved_subject,
                    prev_snippet=_prev_snip,
                )
                _act_prompt = (
                    f"{_topic_entity_prefix}"
                    f"{_ak_base_style}. {_act_desc} "
                    f"{_align} "
                    f"{_parallax_directive}"
                    f"{_lighting_tail}"
                )
                _fallback_dest = _reel_img_dir / f"{stem}_act{_act_i + 1:02d}_fallback.png"
                _sr_act_meta[_act_i] = {
                    "snippet": _snippet,
                    "fallback_dest": _fallback_dest,
                }
                _sr_act_jobs[_act_i] = functools.partial(
                    adapter.generate,
                    _act_prompt,
                    output_stem=f"{stem}_act{_act_i + 1:02d}",
                    output_directory=_reel_img_dir,
                    avatar_mode="OFF",
                )

            _gpu_baseline = 0.0
            try:
                from core_engine.remote_gpu_manager import (  # noqa: PLC0415
                    get_manager as _get_rgpu,
                    is_remote_gpu_enabled as _rgpu_on,
                )

                if _rgpu_on("image"):
                    _gpu_baseline = float(
                        getattr(_get_rgpu().client, "total_gpu_seconds", 0) or 0
                    )
            except Exception:
                _gpu_baseline = 0.0

            _sr_act_results = _run_acts_parallel(_sr_act_jobs)

            _ok_acts = 0
            for _act_i in range(1, _seq_n):
                _meta = _sr_act_meta[_act_i]
                _snippet = _meta["snippet"]
                _fallback_dest = _meta["fallback_dest"]
                _result = _sr_act_results.get(_act_i)
                if isinstance(_result, Exception):
                    _LOG.warning(
                        "Sequence image act %d failed (%s) — blinded fallback to Act 1.",
                        _act_i + 1, _result,
                    )
                    _sequence_image_paths.append(
                        _ensure_sequence_image(
                            _fallback_dest, fallback=_act1_base, target_path=_fallback_dest,
                        )
                    )
                    continue
                _act_img = _ensure_sequence_image(
                    _result, fallback=_act1_base, target_path=_fallback_dest,
                )
                _sequence_image_paths.append(_act_img)
                _ok_acts += 1
                _LOG.info(
                    "Sequence reel | act %d/%d generated | beat=%.48s…",
                    _act_i + 1, _seq_n, _snippet,
                )
            # One GPU bill for the parallel batch (sum of per-job seconds).
            _gpu_delta = _track_remote_gpu_batch(
                cost_tracker, seconds_before=_gpu_baseline, jobs=_ok_acts,
            )
            if _gpu_delta <= 0 and _ok_acts:
                # Together / non-timed path — charge per successful act
                for _ in range(_ok_acts):
                    _images_generated_this_variant += _track_adapter_image(
                        cost_tracker, adapter
                    )
            else:
                _images_generated_this_variant += _ok_acts

        if _sequence_image_paths:
            _images_generated_this_variant = max(
                _images_generated_this_variant,
                len(_sequence_image_paths),
            )
            _LOG.info(
                "Sequence reel | frames=%d | api_image_units=%d",
                len(_sequence_image_paths),
                _images_generated_this_variant,
            )

        # ── Visual Control Agent (Master Mei) — VLM + phash before compile ──
        if (
            _is_mm
            and _sequence_image_paths
            and adapter is not None
            and len(_sequence_image_paths) >= 2
        ):
            try:
                from avatar_engine.visual_inspector import inspect_sequence_images
                from avatar_engine.visual_roles import (
                    ROLE_MASTER,
                    assign_frame_beats,
                    build_role_prompt,
                )

                _insp = inspect_sequence_images(_sequence_image_paths, use_vlm=True)
                _LOG.info(
                    "VISUAL_INSPECTOR | passed=%s regen_frames=%s | %s",
                    _insp.passed,
                    [i + 1 for i in _insp.regenerate_indices],
                    "; ".join(_insp.notes[:4]) if _insp.notes else "ok",
                )
                _beats_insp = assign_frame_beats(len(_sequence_image_paths))
                for _ri in _insp.regenerate_indices[:5]:  # cap regen attempts
                    if _ri < 0 or _ri >= len(_sequence_image_paths):
                        continue
                    _beat = _beats_insp[_ri] if _ri < len(_beats_insp) else ""
                    _role = (
                        "master"
                        if _beat in ("intro", "outro", "training")
                        else ("disciple" if _beat == "break_free" else "slave")
                    )
                    _snip = (
                        _spoken_snippets[_ri]
                        if _ri < len(_spoken_snippets)
                        else resolved_subject
                    )
                    _pos, _neg = build_role_prompt(
                        role=_role,
                        visual_dna=(
                            (page_ctx.master_mei_visual_dna or _MEI_DNA_FALLBACK)
                            if _role == ROLE_MASTER
                            else ""
                        ),
                        spoken_beat=_snip,
                        subject=resolved_subject,
                        act_index=_ri,
                        beat=_beat,
                    )
                    _av_ref = locals().get("_mm_avatar_ref")
                    _av_w = locals().get("_mm_avatar_w")
                    _use_av = _role == ROLE_MASTER and _av_ref is not None
                    try:
                        _regen_path = adapter.generate(
                            _pos,
                            output_stem=f"{stem}_act{_ri + 1:02d}_regen",
                            output_directory=_reel_img_dir,
                            reference_image_path=_av_ref if _use_av else None,
                            avatar_mode="ON" if _use_av else "OFF",
                            reference_image_weight=_av_w if _use_av else None,
                            visual_role=_role,
                            negative_prompt=_neg or None,
                        )
                        _sequence_image_paths[_ri] = _ensure_sequence_image(
                            _regen_path,
                            fallback=_sequence_image_paths[_ri],
                            target_path=_reel_img_dir / f"{stem}_act{_ri + 1:02d}_regen.png",
                        )
                        _images_generated_this_variant += _track_adapter_image(
                            cost_tracker, adapter
                        )
                        _LOG.info(
                            "VISUAL_INSPECTOR | regenerated frame %d (beat=%s role=%s)",
                            _ri + 1, _beat, _role,
                        )
                    except Exception as _regen_exc:  # noqa: BLE001
                        _LOG.warning(
                            "VISUAL_INSPECTOR | regen frame %d failed: %s",
                            _ri + 1, _regen_exc,
                        )
            except Exception as _insp_exc:  # noqa: BLE001
                _LOG.warning("VISUAL_INSPECTOR skipped: %s", _insp_exc)

    posting_slot_display = scheduled_bulk_post_display(variant_index=variation_index)

    # ====================================================================
    # PHASE B2: STANDARD_QUOTE caption (SMART_BAIT already done in B1)
    # ====================================================================

    # ---- Caption: STANDARD_QUOTE path -----------------------------------
    # SMART_BAIT, LONG_CAPTION_IMAGE, ECONOMIC_REEL, and CAROUSEL all generate captions in Phase B1.
    if post_type not in ("SMART_BAIT", "LONG_CAPTION_IMAGE", "ECONOMIC_REEL", "CAROUSEL") and not skip_caption:
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

    # ── Sequential scene export + optional human approval gate ─────────────
    # Persist scene_01.png…scene_N.png under:
    #   outputs/<page>/assets/<episode_id>/
    # Clips always render to outputs/<page>/clips/
    if _is_economic_reel:
        if _episode_assets_dir is None:
            _episode_assets_dir = Path(app_config.ASSETS_DIR) / _episode_id
            _episode_assets_dir.mkdir(parents=True, exist_ok=True)
        _export_sources: list = (
            list(_sequence_image_paths)
            if _sequence_image_paths
            else (
                [Path(img_path_display)]
                if img_path_display and Path(img_path_display).is_file()
                else []
            )
        )
        if _export_sources:
            _exported_scenes = _export_scene_assets_sequential(
                _export_sources, _episode_assets_dir,
            )
            if _exported_scenes:
                _assets_dir_disp = str(_episode_assets_dir.resolve())
                print(f"\n[ASSETS] Episode images saved -> {_assets_dir_disp}")
                _LOG.info(
                    "Scene assets exported → %s (%d files) | episode=%s",
                    _assets_dir_disp, len(_exported_scenes), _episode_id,
                )
                if _sequence_image_paths:
                    # Resume render from the approved canonical scene assets
                    _sequence_image_paths = list(_exported_scenes)
                elif img_path_display:
                    img_path_display = str(_exported_scenes[0])
                if render_approval_required:
                    # Serialize pause across concurrent workers (stdin is shared)
                    with write_lock:
                        input(
                            f"\n [PAUSED] Images saved to episode assets folder:\n"
                            f" {_assets_dir_disp}\n"
                            " Press [ENTER] to approve and continue video rendering, "
                            "or [Ctrl+C] to abort...\n"
                        )
                    _LOG.info(
                        "Render approval granted — continuing video compilation "
                        "(variant %s | episode=%s).",
                        variant + 1, _episode_id,
                    )

    if _is_economic_reel and img_path_display and Path(img_path_display).is_file():
        _LOG.info("ECONOMIC_REEL | Launching video compilation pipeline (variant %s)…", variant + 1)
        _voice_path: Path | None = None
        _ambient_path: Path | None = None

        # Always target the page clips/ folder (never loose root / episode clips)
        _reel_dir = Path(app_config.PAGE_OUTPUTS_DIR) / "clips"
        os.makedirs(_reel_dir, exist_ok=True)

        # -- Voiceover (ElevenLabs TTS + word-level timestamps for auto-subtitles) --
        # caption      = 4-sentence narration script spoken by Dorothy voice
        # overlay_text = short on-screen hook headline (static layer, NOT spoken)
        # Guard: never pass the "(skipped)" initialisation placeholder to TTS.
        _real_caption = caption if caption and caption != "(skipped)" else ""
        _voiceover_script = _real_caption or overlay_text

        # For sequence reels the caption is a short social-media post body.
        # Generate a longer narration for TTS — or reuse the early master_mei script
        # already built for narrative-to-image B-roll matching.
        if (
            page_ctx is not None
            and page_ctx.enable_sequence_reel
            and caption_engine is not None
            and resolved_subject
        ):
            _LOG.info(
                "SEQUENCE_REEL | generating long-form voiceover script for '%s' (voice pref: %s)",
                resolved_subject, page_ctx.tts_voice_preference or "default",
            )
            try:
                if _early_seq_script and (
                    len(_early_seq_script.split()) >= (
                        page_ctx.reel_narration_min_words if page_ctx else 110
                    )
                    or len(_early_seq_script.strip()) >= 150
                ):
                    _seq_script = _early_seq_script
                    _LOG.info(
                        "SEQUENCE_REEL | reusing early narrative script: %d words",
                        len(_seq_script.split()),
                    )
                else:
                    _words_tgt = page_ctx.reel_narration_words
                    _n_for_script = max(
                        2,
                        len(_sequence_image_paths) if _sequence_image_paths else page_ctx.reel_image_count,
                    )
                    _seq_script = caption_engine.generate_sequence_voiceover(
                        resolved_subject,
                        page_niche=page_ctx.content_niche,
                        persona_voice=page_ctx.tts_voice_preference or (
                            "calm, deep, highly wise, authoritative ancient master"
                            if (page_ctx.page_id or "").lower() == "master_mei"
                            else "investigative, neutral, immersive"
                        ),
                        n_acts=_n_for_script,
                        duration_s=page_ctx.reel_duration,
                        total_words_target=_words_tgt,
                        economic=economic,
                        niche_disclaimer=page_ctx.niche_disclaimer,
                        cta_line="",   # CTA is generated as a separate audio block and stitched after narration
                        narrative_mode=page_ctx.narrative_mode,
                        batch_angle_block=_batch_angle_block,
                        previously_generated_hooks=list(generated_hooks_cache or []),
                    )
                    if cost_tracker is not None and _seq_script:
                        cost_tracker.track_text(char_count=len(_seq_script))
                _min_ok = page_ctx.reel_narration_min_words if page_ctx else 110
                if _seq_script and (
                    len(_seq_script.split()) >= _min_ok or len(_seq_script.strip()) >= 150
                ):
                    _voiceover_script = _seq_script
                    _LOG.info(
                        "SEQUENCE_REEL | documentary voiceover generated: %d words (min=%d)",
                        len(_seq_script.split()), _min_ok,
                    )
                else:
                    _LOG.warning(
                        "SEQUENCE_REEL | voiceover too short (%d words, need ≥%d or ≥150 chars) — "
                        "video will trim to audio length (no silent 80s pad).",
                        len((_seq_script or "").split()), _min_ok,
                    )
                    if _seq_script and (
                        len(_seq_script.split()) >= 60 or len(_seq_script.strip()) >= 150
                    ):
                        _voiceover_script = _seq_script
            except Exception as _seq_exc:  # noqa: BLE001
                _LOG.warning("SEQUENCE_REEL | voiceover generation failed: %s — using caption.", _seq_exc)
        _word_timings: list[tuple[str, float, float]] = []
        if _voiceover_script and app_config.ELEVENLABS_API_KEY:
            # 1. Trim narration body to page word target (master_mei ≈ 175 for ~75 s).
            #    Allow slight headroom so we don't over-trim a correctly long script.
            _max_words = (page_ctx.reel_narration_words if page_ctx else 140) + 30
            if page_ctx and (page_ctx.page_id or "").lower() == "master_mei":
                # Hard cap 260 per Master Philosophical Scriptwriter brief
                _max_words = int(page_ctx.reel_narration_max_words)
            _voiceover_script = _trim_script_to_word_limit(_voiceover_script, max_words=_max_words)
            _is_mei_page = (page_ctx.page_id if page_ctx else "").lower() == "master_mei"
            _tts_ssml = bool(page_ctx.tts_enable_ssml) if page_ctx else False
            if _is_mei_page:
                # Purge emotion tags; keep strategic <break time="1.5s"/> for 100–120 s pacing
                from avatar_engine.mei_narrative import (
                    prepare_mei_tts_text,
                    strip_inline_follow_cta,
                )
                _pre = _voiceover_script
                _voiceover_script = prepare_mei_tts_text(_voiceover_script)
                _voiceover_script = strip_inline_follow_cta(_voiceover_script)
                if _pre != _voiceover_script:
                    _LOG.info(
                        "MASTER_MEI TTS prep | POV/blacklist + break pauses (%d → %d chars, breaks=%d)",
                        len(_pre),
                        len(_voiceover_script),
                        _voiceover_script.lower().count("<break"),
                    )
                # Honor page_config TTS_ENABLE_SSML (True → send break pauses to ElevenLabs)
                _tts_ssml = bool(page_ctx.tts_enable_ssml) if page_ctx else True
            elif page_ctx is None or page_ctx.strip_audio_tags_before_tts:
                # Other pages: strip bracketed emotional tags so ElevenLabs never reads them aloud
                _pre_strip = _voiceover_script
                _voiceover_script = _strip_audio_behavior_tags(_voiceover_script)
                if _pre_strip != _voiceover_script:
                    _LOG.info(
                        "TTS tag strip | removed bracket tags before ElevenLabs (%d → %d chars)",
                        len(_pre_strip), len(_voiceover_script),
                    )
            _narration_out = _reel_dir / f"{stem}_v{variant + 1:02d}_narration.mp3"
            _voice_id = page_ctx.elevenlabs_voice_id if page_ctx else None
            _tts_speed = page_ctx.tts_narration_speed if page_ctx else None
            _tts_vs = page_ctx.elevenlabs_voice_settings if page_ctx else None
            _tts_expressive = (
                page_ctx.tts_expressive_mode
                if page_ctx and hasattr(page_ctx, "tts_expressive_mode")
                else ((page_ctx.page_id or "").lower() == "master_mei" if page_ctx else False)
            )
            try:
                _voice_path, _word_timings = generate_voiceover_with_timestamps(
                    _voiceover_script,
                    _narration_out,
                    voice_id=_voice_id or None,
                    model_id=page_ctx.elevenlabs_model if page_ctx else "eleven_v3",
                    speed=_tts_speed,
                    voice_settings=_tts_vs or None,
                    enable_ssml=_tts_ssml,
                    expressive_mode=_tts_expressive,
                )
                _word_timings = _filter_audio_tag_timings(_word_timings)
            except Exception as vaudio_exc:  # noqa: BLE001
                _LOG.warning(
                    "Voiceover generation failed (variant %s): %s — reel will be silent.",
                    variant + 1, vaudio_exc,
                )

            # 2. Generate CTA audio as a distinct block (strict zero-overlap rule).
            #    Use generate_voiceover_with_timestamps so subtitle coverage extends
            #    across the full stitched track (narration + silence + CTA).
            _cta_text = (page_ctx.reel_cta_text if page_ctx else "") or ""
            from avatar_engine.mei_narrative import approved_cta_text, fix_cta_typos
            if (page_ctx.page_id if page_ctx else "").lower() == "master_mei":
                # Force the single approved CTA — never invent or duplicate variants
                _cta_text = approved_cta_text()
            if page_ctx is None or page_ctx.strip_audio_tags_before_tts:
                _cta_text = _strip_audio_behavior_tags(_cta_text)
            # Always correct sovereianty → sovereignty before TTS / burn-in
            _cta_text = fix_cta_typos(_cta_text or "")
            _cta_path: "Path | None" = None
            _cta_word_timings: list[tuple[str, float, float]] = []
            if _cta_text and _voice_path is not None:
                _cta_out = _reel_dir / f"{stem}_v{variant + 1:02d}_cta.mp3"
                try:
                    _is_mei_cta = (
                        (page_ctx.page_id if page_ctx else "").lower() == "master_mei"
                    )
                    if _is_mei_cta:
                        # Credit optimization: synthesize CTA once, reuse cache.
                        from avatar_engine.audio_engine import (
                            default_cta_cache_path,
                            ensure_cached_cta_voiceover,
                        )
                        import shutil as _shutil_cta

                        _cache = ensure_cached_cta_voiceover(
                            _cta_text,
                            default_cta_cache_path(),
                            voice_id=_voice_id or None,
                            model_id=page_ctx.elevenlabs_model if page_ctx else "eleven_v3",
                            speed=_tts_speed,
                            voice_settings=_tts_vs or None,
                            expressive_mode=_tts_expressive,
                        )
                        if _cache is not None and Path(_cache).is_file():
                            _shutil_cta.copy2(str(_cache), str(_cta_out))
                            _cta_path = _cta_out
                            # Approximate CTA subtitle window from clip duration
                            try:
                                from moviepy import AudioFileClip as _AFC_CTA
                                with _AFC_CTA(str(_cta_path)) as _cta_afc:
                                    _cta_dur_est = float(_cta_afc.duration or 3.0)
                            except Exception:
                                _cta_dur_est = 3.0
                            _cta_words = _cta_text.split()
                            if _cta_words:
                                _slot = _cta_dur_est / max(1, len(_cta_words))
                                _cta_word_timings = [
                                    (w, i * _slot, (i + 1) * _slot)
                                    for i, w in enumerate(_cta_words)
                                ]
                            _LOG.info(
                                "CTA cache used | %s (%d chars) — ElevenLabs skip on hit",
                                _cta_path.name, len(_cta_text),
                            )
                        else:
                            raise RuntimeError("CTA cache unavailable")
                    else:
                        _cta_path, _cta_word_timings = generate_voiceover_with_timestamps(
                            _cta_text,
                            _cta_out,
                            voice_id=_voice_id or None,
                            model_id=page_ctx.elevenlabs_model if page_ctx else "eleven_v3",
                            speed=_tts_speed,
                            voice_settings=_tts_vs or None,
                            expressive_mode=_tts_expressive,
                        )
                        _cta_word_timings = _filter_audio_tag_timings(_cta_word_timings)
                        _LOG.info(
                            "CTA voiceover generated | %s (%d chars)",
                            _cta_path.name, len(_cta_text),
                        )
                except Exception as _cta_exc:  # noqa: BLE001
                    _LOG.warning("CTA voiceover failed (%s) — CTA will be omitted.", _cta_exc)

            # 3. Stitch: narration + 1.0 s silence + CTA → single voice track.
            #    After stitching, offset CTA word timings by (narration_dur + 1.0 s)
            #    and append to _word_timings so subtitles cover the whole track.
            if _voice_path is not None and _cta_path is not None:
                # Capture narration duration from the REAL returned path
                # (remote F5 may write .flac while _narration_out still says .mp3).
                _narr_dur: float = 0.0
                try:
                    from avatar_engine.audio_engine import (  # noqa: PLC0415
                        _audio_file_duration_s as _audio_dur_s,
                    )
                    _narr_dur = float(_audio_dur_s(Path(_voice_path)))
                except Exception:
                    _narr_dur = 0.0
                if _narr_dur <= 0.05 and _word_timings:
                    _narr_dur = float(_word_timings[-1][2])
                if _narr_dur <= 0.05:
                    try:
                        from moviepy import AudioFileClip as _AFC  # type: ignore[import]
                        with _AFC(str(_narration_out)) as _afc:
                            _narr_dur = float(_afc.duration or 0.0)
                    except Exception:
                        _narr_dur = 0.0

                _stitched_out = _reel_dir / f"{stem}_v{variant + 1:02d}_voice.mp3"
                _voice_path = _stitch_audio_sequential(
                    _voice_path, _cta_path, _stitched_out, silence_s=1.0
                )

                # Append offset CTA timings → full transcription coverage.
                _cta_offset = _narr_dur + 1.0   # 1.0 s silence gap
                if not _cta_word_timings:
                    try:
                        from avatar_engine.audio_engine import (  # noqa: PLC0415
                            _audio_file_duration_s as _audio_dur_s2,
                            approximate_word_timings as _approx_wt,
                        )
                        _cta_dur_fill = float(_audio_dur_s2(Path(_cta_path)))
                        _cta_word_timings = _approx_wt(_cta_text, _cta_dur_fill)
                    except Exception:
                        _cta_word_timings = []
                _word_timings = _word_timings + [
                    (w, s + _cta_offset, e + _cta_offset)
                    for w, s, e in _cta_word_timings
                ]
                _LOG.info(
                    "Subtitle coverage extended | narr=%.1fs + 1.0s + cta=%.1fs words "
                    "(%d total tokens) | cta_overlay_start=%.1fs",
                    _narr_dur,
                    _cta_word_timings[-1][2] if _cta_word_timings else 0.0,
                    len(_word_timings),
                    _cta_offset,
                )
            elif _voice_path is None:
                _voice_path = None   # silence path stays None; reel renders without audio

            # 3b. Loudnorm on final voice track (no raw gain — prevents distortion)
            if (
                _voice_path is not None
                and page_ctx
                and (page_ctx.page_id or "").lower() == "master_mei"
            ):
                try:
                    _voice_path = apply_voice_loudnorm(_voice_path)
                except Exception as _ln_exc:  # noqa: BLE001
                    _LOG.debug("VO loudnorm skipped: %s", _ln_exc)
        else:
            _LOG.warning(
                "ECONOMIC_REEL | Voiceover skipped — %s",
                "ELEVENLABS_API_KEY not set" if not app_config.ELEVENLABS_API_KEY else "no narration script",
            )

        # ── COST TRACKING: audio/TTS ─────────────────────────────────────────
        if cost_tracker is not None and _voiceover_script:
            # Include CTA characters in TTS cost if a separate CTA was generated.
            _cta_chars = len(locals().get("_cta_text", ""))
            cost_tracker.track_audio(
                char_count=len(_voiceover_script) + _cta_chars,
                sfx=bool(app_config.ELEVENLABS_API_KEY),
            )

        # -- Three-channel mix: VO + atmosphere SFX loop + music_v2 BGM --------
        # Topology ported from master_mei; enabled via page USE_MUSIC_V2_BED.
        # NO transient impact SFX — atmosphere drone + BGM bed only.
        _impact_sfx_path: "Path | None" = None
        _sfx_loop_path: "Path | None" = None
        _target_dur = page_ctx.reel_duration if page_ctx else 105.0
        _is_mei = bool(page_ctx and (page_ctx.page_id or "").lower() == "master_mei")
        _use_music_bed = bool(
            app_config.ELEVENLABS_API_KEY
            and page_ctx
            and getattr(page_ctx, "use_music_v2_bed", False)
        )
        if _use_music_bed:
            _music_min = 40.0
            try:
                _cfg = getattr(page_ctx, "page_cfg", None) or {}
                _music_min = float(_cfg.get("MUSIC_V2_MIN_SECONDS", 40.0) or 40.0)
            except Exception:
                _music_min = 40.0
            _music_style = (
                page_ctx.ambient_music_style if page_ctx else ("warrior" if _is_mei else "mystery")
            )
            _music_dir = (
                page_ctx.music_prompt_directive_path if page_ctx else None
            )
            _music_bed, _impact_sfx_path = generate_master_mei_soundscape(
                _reel_dir,
                stem=f"{stem}_v{variant + 1:02d}",
                duration_seconds=max(_music_min, float(_target_dur)),
                include_impact_sfx=False,  # kill dual/secondary impact overlap
                topic=str(resolved_subject or ""),
                directive_path=_music_dir,
                style_profile=_music_style,
            )
            _impact_sfx_path = None  # hard disable regardless of return
            if _music_bed is not None:
                _ambient_path = _music_bed
                _LOG.info(
                    "%s | BGM music_v2 ready (≥%.0fs) → %s | style=%s",
                    (page_ctx.page_id or "PAGE").upper(),
                    _music_min,
                    Path(_music_bed).name,
                    _music_style,
                )
            # Continuous 10s atmosphere tile → looped 100% of video
            _sfx_out = _reel_dir / f"{stem}_v{variant + 1:02d}_atmosphere_sfx.mp3"
            _sfx_prompt = page_ctx.ambient_sfx_prompt if page_ctx else ""
            _sfx_loop_path = generate_ambient_track(
                _sfx_out,
                duration_seconds=10.0,
                prompt=_sfx_prompt or None,
            )
            if _sfx_loop_path is not None:
                _LOG.info(
                    "%s | Atmosphere SFX 10s → %s (loop 100%%)",
                    (page_ctx.page_id or "PAGE").upper(),
                    Path(_sfx_loop_path).name,
                )
        elif app_config.ELEVENLABS_API_KEY:
            _ambient_out = _reel_dir / f"{stem}_v{variant + 1:02d}_ambient.mp3"
            _sfx_prompt = page_ctx.ambient_sfx_prompt if page_ctx else ""
            _ambient_path = generate_ambient_track(
                _ambient_out,
                duration_seconds=min(12.0, float(_target_dur)),
                prompt=_sfx_prompt or None,
            )
            # Single-tile path: also feed as loopable SFX so mix hears it
            if _ambient_path is not None and Path(_ambient_path).is_file():
                _sfx_loop_path = Path(_ambient_path)

        # -- Local ambient pad = FALLBACK only (never replace a generated bed) ─
        # NEVER prefer legacy rain/martial rain loops (hiss/clipping).
        if page_ctx and (_ambient_path is None or not Path(_ambient_path).is_file()):
            _local_ambient = page_ctx.ambient_audio_path
            _ban_rain = ("rain", "martial_loop", "storm", "thunder", "hiss")
            if (
                _local_ambient is not None
                and _local_ambient.is_file()
                and not any(b in _local_ambient.name.lower() for b in _ban_rain)
            ):
                _ambient_path = _local_ambient
                _LOG.info(
                    "%s | Using local ambient fallback: %s",
                    (page_ctx.page_id or "").upper(),
                    _local_ambient.name,
                )
            elif (
                _local_ambient is not None
                and _local_ambient.is_file()
                and any(b in _local_ambient.name.lower() for b in _ban_rain)
            ):
                _LOG.warning(
                    "%s | Ignoring rain/hiss ambient asset %s — using cinematic SFX/drone",
                    (page_ctx.page_id or "").upper(),
                    _local_ambient.name,
                )
            elif (page_ctx.page_id or "").lower() == "ancient_knowledge":
                _legacy_mystery = app_config.ENGINE_ROOT / "assets" / "audio" / "ambient_mystery_loop.mp3"
                if _legacy_mystery.is_file():
                    _ambient_path = _legacy_mystery
                    _LOG.info(
                        "ANCIENT_KNOWLEDGE | Local ambient mystery fallback: %s",
                        _legacy_mystery.name,
                    )

        # Force ambient to 48 kHz (light loudnorm) before MoviePy mix
        if _ambient_path is not None and Path(_ambient_path).is_file():
            try:
                from avatar_engine.audio_engine import resample_audio_48k as _rs48
                # Copy into reel dir if using a shared asset, so we don't mutate originals
                _amb_src = Path(_ambient_path)
                if _amb_src.resolve().parent != _reel_dir.resolve():
                    _amb_copy = _reel_dir / f"{stem}_v{variant + 1:02d}_ambient_48k.mp3"
                    import shutil as _sh_amb
                    _sh_amb.copy2(_amb_src, _amb_copy)
                    _ambient_path = _rs48(_amb_copy, apply_loudnorm=True)
                else:
                    _ambient_path = _rs48(_amb_src, apply_loudnorm=True)
            except Exception as _amb_rs_exc:  # noqa: BLE001
                _LOG.debug("Ambient 48k resample skipped: %s", _amb_rs_exc)

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
            # ── DISPATCH: sequence reel (multi-image) vs single-image reel ───
            _use_sequence = (
                page_ctx is not None
                and page_ctx.enable_sequence_reel
                and len(_sequence_image_paths) >= 2
            )
            if _use_sequence:
                _LOG.info(
                    "SEQUENCE_REEL | %d images → %s",
                    len(_sequence_image_paths), _reel_target.name,
                )
                _styled_subs = False
                if page_ctx and (page_ctx.page_id or "").lower() == "master_mei":
                    try:
                        from channels_config.master_mei.system_config import (
                            is_cinematic_yellow_subtitles_enabled as _yel_on,
                            is_cyber_samurai_subtitles_enabled as _cyber_on,
                        )
                        _styled_subs = bool(_yel_on() or _cyber_on())
                    except Exception:
                        _styled_subs = True
                _wpp = page_ctx.subtitle_words_per_phrase if page_ctx and _styled_subs else 4
                _sub_fill = (
                    page_ctx.subtitle_fill if page_ctx and _styled_subs else (255, 230, 0)
                )
                _sub_sw = page_ctx.subtitle_stroke_width if page_ctx and _styled_subs else 0
                _sub_sf = page_ctx.subtitle_stroke_fill if page_ctx and _styled_subs else None
                # Master Mei: VO +15% (VOICE_VOLUME_GAIN=1.15). Ambient bed 0.38.
                _voice_gain = (
                    float(page_ctx.voice_volume_gain)
                    if page_ctx and page_ctx.voice_volume_gain is not None
                    else (
                        1.15
                        if page_ctx and (page_ctx.page_id or "").lower() == "master_mei"
                        else 1.0
                    )
                )
                _amb_mul = (
                    page_ctx.ambient_sfx_gain_mul
                    if page_ctx and page_ctx.ambient_sfx_gain_mul is not None
                    else 1.0
                )
                _paced_act_durs = None
                if page_ctx and not _is_mei and _sequence_image_paths:
                    _timeline_s = float(_reel_dur or page_ctx.reel_duration or 80.0)
                    if _voice_path is not None:
                        try:
                            from avatar_engine.audio_engine import (  # noqa: PLC0415
                                _audio_file_duration_s as _voice_dur_s,
                            )
                            _vd = float(_voice_dur_s(Path(_voice_path)))
                            if _vd > 1.0:
                                _timeline_s = _vd + 1.0  # match audio-driven + tail pad
                        except Exception:
                            pass
                    _n_imgs = len(_sequence_image_paths)
                    _plan_ref = locals().get("_planned_scene_durs")
                    if _plan_ref and len(_plan_ref) == _n_imgs:
                        _paced_act_durs = _scale_scene_durations(_plan_ref, _timeline_s)
                        _LOG.info(
                            "ECONOMIC_REEL plan_scenes act_durs | %s → scaled %s",
                            _describe_scene_plan(_plan_ref),
                            _describe_scene_plan(_paced_act_durs),
                        )
                    elif page_ctx.uses_plan_scenes_pacing:
                        # Image count drifted — keep curve shape, pad/trim, then scale.
                        if _plan_ref:
                            _base = list(_plan_ref)
                            if len(_base) > _n_imgs:
                                _base = _base[:_n_imgs]
                            else:
                                _pad = _base[-1] if _base else 5.0
                                _base = _base + [_pad] * (_n_imgs - len(_base))
                            _paced_act_durs = _scale_scene_durations(_base, _timeline_s)
                        else:
                            _paced_act_durs = _plan_scenes(
                                _timeline_s,
                                page_ctx.scene_duration,
                                progressive_start_s=page_ctx.scene_progressive_start_s,
                                progressive_step_every=page_ctx.scene_progressive_step_every,
                                progressive_step_s=page_ctx.scene_progressive_step_s,
                                progressive_cap_s=page_ctx.scene_progressive_cap_s,
                                min_scenes=_n_imgs,
                                max_scenes=_n_imgs,
                            )
                        _LOG.info(
                            "ECONOMIC_REEL plan_scenes (aligned n=%d) | %s",
                            _n_imgs,
                            _describe_scene_plan(_paced_act_durs),
                        )
                    elif page_ctx.reel_use_hook_body_pacing:
                        _paced_act_durs = _build_hook_body_act_durations(
                            _n_imgs,
                            _timeline_s,
                            hook_hold_s=page_ctx.reel_hook_hold_s,
                            body_hold_s=page_ctx.reel_body_hold_s,
                        )
                        _LOG.info(
                            "ECONOMIC_REEL legacy hook/body act_durs | %s",
                            _describe_scene_plan(_paced_act_durs),
                        )
                reel_path = _core_compile_sequence_reel(
                    _sequence_image_paths,
                    overlay_text,
                    voice_audio=_voice_path,
                    ambient_audio=_ambient_path,
                    output_path=_reel_target,
                    target_duration=_reel_dur,
                    act_duration_s=page_ctx.reel_act_duration if page_ctx else None,
                    act_durations=_paced_act_durs,
                    word_timings=_word_timings or None,
                    font_path=_font_path_abs or None,
                    overlay_opacity=page_ctx.reel_overlay_opacity if page_ctx else 0.35,
                    enable_hook_text=page_ctx.enable_top_hook_text if page_ctx else True,
                    vignette_strength=page_ctx.vignette_strength if page_ctx else 0.0,
                    grain_intensity=page_ctx.grain_intensity if page_ctx else 18.0,
                    logo_image_path=_logo_img_path,
                    logo_width_px=page_ctx.logo_width_px if page_ctx else 200,
                    logo_y_offset_px=page_ctx.logo_y_offset_px if page_ctx else 100,
                    logo_opacity=page_ctx.logo_opacity if page_ctx else 0.85,
                    logo_max_height_px=page_ctx.logo_max_height_px if page_ctx else None,
                    subtitle_fontsize=page_ctx.subtitle_fontsize if page_ctx else 56,
                    subtitle_y_position=page_ctx.subtitle_y_position if page_ctx else None,
                    hook_y_frac=page_ctx.hook_y_frac if page_ctx else 0.50,
                    page_id=page_ctx.page_id if page_ctx else "",
                    words_per_phrase=_wpp,
                    subtitle_fill=_sub_fill,
                    subtitle_stroke_width=_sub_sw,
                    subtitle_stroke_fill=_sub_sf,
                    enable_flicker=page_ctx.enable_flicker if page_ctx else False,
                    enable_light_rays=page_ctx.enable_light_rays if page_ctx else False,
                    enable_dust_particles=page_ctx.enable_dust_particles if page_ctx else False,
                    enable_light_refraction=page_ctx.enable_light_refraction if page_ctx else False,
                    # Guaranteed CTA subtitle overlay — never cut off by act-boundary split
                    # Strip audio behavior tags so [chuckles] etc. never burn onto screen.
                    cta_text=_re.sub(
                        r'\[(?:cackles|chuckles|dry\s*laugh)\]\s*',
                        '',
                        locals().get("_cta_text", "") or "",
                        flags=_re.IGNORECASE,
                    ).strip(),
                    # Outro-only: CTA text starts after narration + 1.0s silence.
                    # Require a real narr duration (>0.5s) — a 0.0 fallback made
                    # the overlay paint from t=1s for the entire reel.
                    cta_start_s=(
                        float(locals().get("_narr_dur", -1.0)) + 1.0
                        if float(locals().get("_narr_dur", -1.0) or -1.0) > 0.5
                        else -1.0
                    ),
                    ambient_volume=(page_ctx.ambient_volume if page_ctx else None),
                    sfx_loop_audio=_sfx_loop_path,
                    sfx_loop_volume=(
                        page_ctx.atmosphere_sfx_volume if page_ctx else 0.35
                    ),
                    sfx_fade_in_s=(
                        page_ctx.atmosphere_sfx_fade_in if page_ctx else 0.2
                    ),
                    impact_sfx_audio=None if _use_music_bed else _impact_sfx_path,
                    impact_sfx_volume=(0.0 if _use_music_bed else None),
                    hook_max_s=(
                        float((getattr(page_ctx, "page_cfg", None) or {}).get(
                            "REEL_HOOK_MAX_S", 8.0
                        ))
                        if page_ctx and _is_mei
                        else 0.0
                    ),
                    voice_volume_gain=(1.0 if _is_mei else _voice_gain),
                    ambient_gain_mul=_amb_mul,
                    ambient_duck_ratio=(
                        page_ctx.ambient_duck_ratio if page_ctx else None
                    ),
                    ambient_duck_until_s=locals().get("_narr_dur", None),
                    master_audio_gain=(
                        page_ctx.master_audio_gain
                        if page_ctx and _use_music_bed
                        else None
                    ),
                    ambient_profile=(
                        page_ctx.ambient_music_style
                        if page_ctx
                        else ("warrior" if _is_mei else "mystery")
                    ),
                    bgm_start_s=(page_ctx.bgm_start_time if page_ctx else 0.0),
                    bgm_fade_in_s=(page_ctx.bgm_fade_in_duration if page_ctx else 0.0),
                    sfx_volume_gain_db=(
                        page_ctx.sfx_volume_gain_db if page_ctx else 0.0
                    ),
                    tail_pad_s=page_ctx.reel_tail_pad_s if page_ctx else 1.0,
                    force_exact_duration=(
                        page_ctx.reel_force_exact_duration if page_ctx else False
                    ),
                    exact_duration_s=(
                        page_ctx.reel_duration if page_ctx else 80.0
                    ),
                )
            else:
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
                    sub_text=None,
                )
            _LOG.info("ECONOMIC_REEL compiled → %s", reel_path.name)
            video_path_str = str(reel_path)
            print(f"[reel] Video compiled -> {reel_path}")
            _img_n = (
                len(_sequence_image_paths)
                if _sequence_image_paths
                else max(1, int(_images_generated_this_variant or 1))
            )
            _batch_so_far = int(_img_n)
            _lock = batch_image_lock or write_lock
            if batch_image_counter is not None:
                with _lock:
                    batch_image_counter[0] = int(batch_image_counter[0]) + int(_img_n)
                    _batch_so_far = int(batch_image_counter[0])
            _reel_cost = 0.003 * max(0, int(_img_n))
            if cost_tracker is not None:
                try:
                    # Prefer image-generation slice attributed to this reel's count
                    _reel_cost = max(_reel_cost, 0.003 * max(0, int(_img_n)))
                except Exception:  # noqa: BLE001
                    pass
            _print_video_cost_summary(
                cost_tracker,
                image_count=_img_n,
                image_model_label="FLUX Schnell",
                variant_index=variant + 1,
                total_variants=qty,
                batch_images_so_far=_batch_so_far,
                reel_cost_usd=_reel_cost,
            )

            # ── PHASE E1: Backblaze B2 upload ────────────────────────────────
            # Upload the finished MP4 to B2 and store the public URL so the
            # postplanner MEDIA URL column contains a live, shareable link.
            # On storage-cap AccessDenied: quiet warn → local/ImgBB only.
            _b2_video_url: str = ""
            try:
                _b2 = B2VideoUploader()
                _b2_video_url = _b2.upload(reel_path)
                _LOG.info("B2 upload OK → %s", _b2_video_url)
                print(f"[B2] Public URL: {_b2_video_url}")
            except B2StorageCapError as _b2_cap:
                _LOG.warning("%s", _b2_cap)
                _b2_video_url = ""
            except Exception as _b2_exc:  # noqa: BLE001
                _err_l = str(_b2_exc).lower()
                if "accessdenied" in _err_l or "access denied" in _err_l:
                    _LOG.warning(
                        "[B2] Storage cap / AccessDenied for %s — using local/ImgBB.",
                        reel_path.name,
                    )
                else:
                    _LOG.warning(
                        "B2 upload failed for %s (%s) — postplanner will use local path.",
                        reel_path.name,
                        type(_b2_exc).__name__,
                    )
                _b2_video_url = ""
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
            if (page_ctx.page_id if page_ctx else "").lower() == "master_mei":
                from avatar_engine.mei_narrative import (
                    build_seo_description,
                    build_seo_title,
                )
                _llm_seo = getattr(caption_engine, "last_seo_title", "") if caption_engine else ""
                smart_bait_payload["title"] = (
                    (_llm_seo or build_seo_title(resolved_subject)).strip()[:100]
                )
                smart_bait_payload["description"] = build_seo_description(
                    resolved_subject, caption=caption or "",
                )
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
                video_path=video_path_str or "",
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
                    # Prefer B2 public video URL for reel posts; fall back to
                    # local video path; then fall back to ImgBB for image posts.
                    media_url=_b2_video_url or video_path_str or imgbb_url,
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
    _seo_title = ""
    _seo_description = caption_str
    if (page_ctx.page_id if page_ctx else "").lower() == "master_mei":
        from avatar_engine.mei_narrative import (
            build_seo_description,
            build_seo_title,
        )
        _llm_seo = (
            _seo_title_local
            or (getattr(caption_engine, "last_seo_title", "") if caption_engine else "")
        )
        if _batch_angle and _batch_angle.seo_title_hint and not _llm_seo:
            _seo_title = _batch_angle.seo_title_hint.strip()[:100]
        else:
            _seo_title = (_llm_seo or build_seo_title(resolved_subject)).strip()[:100]
        _seo_description = build_seo_description(
            resolved_subject, caption=caption_str or "",
        )
    elif _batch_angle is not None:
        # Generic pages: unique angle-driven title + description first line
        _seo_title = (
            _seo_title_local
            or (_batch_angle.seo_title_hint or _batch_angle.angle_title)
            or overlay_text
            or resolved_subject
        ).strip()[:100]
        _angle_lead = (
            f"{_batch_angle.angle_title}: {_batch_angle.angle_brief}"
        ).strip()
        if caption_str:
            _seo_description = f"{_angle_lead}\n\n{caption_str}"
        else:
            _seo_description = _angle_lead
    elif _seo_title_local:
        _seo_title = _seo_title_local[:100]

    _return_dict = {
        "topic": resolved_subject,
        "title": _seo_title or overlay_text or resolved_subject,
        "batch_angle": (_batch_angle.angle_title if _batch_angle else ""),
        "hook_style": (_batch_angle.hook_style if _batch_angle else ""),
        "variant_index": variant + 1,
        "local_image_path": img_ref_engine,
        "video_path": video_path_str,
        "imgbb_url": imgbb_url,
        "caption": caption_str,
        "description": _seo_description,
        "overlay_text": overlay_text,
        "library_timestamp": meta.get("timestamp"),
        "library_json_relative": lib_json_rel,
        "excel_row": planner_row_ix,
        "model_image_used": img_report,
        "model_research_head": bm.research_primary_id,
        "humanizer": humanizer_notes,
        "caption_mode": caption_mode_tag if caption_mode_tag is not None else "skipped",
        "pinterest_title": (
            getattr(caption_engine, "last_pinterest_title", "") if caption_engine else ""
        ),
        "pinterest_description": (
            getattr(caption_engine, "last_pinterest_description", "") if caption_engine else ""
        ),
        # Carousel and image-count metadata
        "carousel_image_paths": _carousel_image_paths,
        "images_generated": _images_generated_this_variant,
    }

    # ── COST TRACKING: write telemetry + annotate return dict ────────────────
    if cost_tracker is not None and page_ctx is not None and page_ctx.enable_cost_tracking:
        cost_tracker.write_telemetry(
            app_config.LIBRARY_DIR,
            variant_index=variant + 1,
        )
        _return_dict["estimated_cost_usd"] = round(cost_tracker.total_usd(), 6)
        _return_dict["cost_tier"] = cost_tracker.cost_tier
        # Full breakdown stored on the row so the summary printer can render it
        _return_dict["cost_breakdown"] = cost_tracker.to_dict()
        _LOG.info(
            "CostTracker | variant %d total=$%.6f tier=%s",
            variant + 1, cost_tracker.total_usd(), cost_tracker.cost_tier,
        )

    return _return_dict


# ---------------------------------------------------------------------------
# 4-tier agentic pipeline (Ancient Knowledge static posts)
# ThemeCurator → Copywriter → VisualDirector ⇄ VisualQA
# ---------------------------------------------------------------------------

def _produce_agentic(
    *,
    resolved_subject: str,
    qty: int,
    skip_image: bool,
    skip_caption: bool,
    page_ctx: PageContext | None,
    post_type: str,
    post_format: str,
    caption_engine: "CaptionEngine | None",
    per_variant_topics: "list[str] | None",
    envelope_base: dict[str, Any],
) -> dict[str, Any]:
    """Run PipelineOrchestrator and map results into a produce() envelope."""
    from core_engine.agentic_pipeline import PipelineOrchestrator
    from core_engine.agentic_pipeline.criteria import MAX_RETRIES as _AK_MAX_RETRIES

    page_id = page_ctx.page_id if page_ctx else app_config.ACTIVE_PAGE
    out_dir = app_config.ASSETS_DIR / "agentic"

    # ── CostTracker — build FIRST and share into the orchestrator so every node
    # self-reports token/image costs accurately (no post-hoc heuristic guessing).
    _cost_tracker: CostTracker | None = None
    _track_on = bool(page_ctx is None or page_ctx.enable_cost_tracking)
    if _track_on:
        try:
            _cost_tracker = CostTracker(
                page_id=page_id or "ancient_knowledge",
                cost_tier=(page_ctx.cost_tier if page_ctx else "nano"),
            )
        except Exception as cost_init_exc:  # noqa: BLE001
            _LOG.warning("CostTracker init failed on agentic path (%s)", cost_init_exc)
            _cost_tracker = None

    orch = PipelineOrchestrator(
        channel_id=page_id or "ancient_knowledge",
        output_dir=out_dir,
        max_retries=_AK_MAX_RETRIES,
        max_workers=min(3, max(1, qty)),
        caption_engine=caption_engine,
        skip_image=skip_image,
        skip_caption=skip_caption,
        post_type=post_type,
        cost_tracker=_cost_tracker,
    )
    envelope = orch.produce_envelope(
        quantity=qty,
        seed_topic=resolved_subject,
        per_slot_topics=per_variant_topics,
        page_id=page_id,
        post_type=post_type,
        post_format=post_format,
    )
    envelope.update({k: v for k, v in envelope_base.items() if k not in envelope})
    envelope["resolved_subject"] = resolved_subject

    items = envelope.get("items") or []
    if not isinstance(items, list):
        items = []

    if _cost_tracker is not None:
        try:
            # Trust the node-reported ledger; just snapshot it into the envelope.
            envelope["final_cost_breakdown"] = _cost_tracker.to_dict()
            envelope["estimated_cost_usd"] = round(_cost_tracker.total_usd(), 6)
            if _track_on:
                _cost_tracker.write_telemetry(
                    app_config.LIBRARY_DIR, variant_index=max(1, len(items)),
                )
            _LOG.info(
                "Agentic CostTracker | ops=%s total=$%.6f tier=%s",
                len(_cost_tracker.to_dict().get("breakdown") or []),
                _cost_tracker.total_usd(),
                _cost_tracker.cost_tier,
            )
        except Exception as cost_exc:  # noqa: BLE001
            _LOG.warning("Agentic cost snapshot failed (%s) — continuing with $0 fallback", cost_exc)
            envelope.setdefault("final_cost_breakdown", {
                "page_id": page_id,
                "cost_tier": page_ctx.cost_tier if page_ctx else "nano",
                "total_estimated_usd": 0.0,
                "breakdown": [],
            })

    key_ib = app_config.IMGBB_API_KEY
    for row in items:
        img = row.get("local_image_path") or ""
        img_path = Path(img) if img else None
        if key_ib and img_path is not None and img_path.is_file():
            try:
                row["imgbb_url"] = upload_image_file_to_imgbb(key_ib, img_path) or ""
            except Exception as up_exc:  # noqa: BLE001
                _LOG.warning("ImgBB upload failed for agentic still (%s)", up_exc)
                row["imgbb_url"] = ""
        else:
            row.setdefault("imgbb_url", "")
        if _cost_tracker is not None:
            row.setdefault("cost_breakdown", _cost_tracker.to_dict())
            row.setdefault("estimated_cost_usd", round(_cost_tracker.total_usd(), 6))
        try:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
            app_config.LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
            durable_abs = app_config.LIBRARY_DIR / f"agentic_{stamp}_v{row.get('variant_index', 0):02d}.json"
            payload = {
                "page_id": page_id,
                "pipeline": "agentic_4tier",
                "post_type": post_type,
                "topic": row.get("theme") or resolved_subject,
                "caption": row.get("caption") or "",
                "overlay_text": row.get("overlay_text") or "",
                "image_prompt": row.get("image_prompt") or "",
                "local_image_path": row.get("local_image_path") or "",
                "imgbb_url": row.get("imgbb_url") or "",
                "qa_status": row.get("qa_status") or "",
                "qa_feedback": row.get("qa_feedback") or "",
                "qa_score": row.get("qa_score") or 0.0,
                "retry_count": row.get("retry_count") or 0,
                "pinterest_title": row.get("pinterest_title") or "",
                "pinterest_description": row.get("pinterest_description") or "",
                "created_utc": datetime.now(timezone.utc).isoformat(),
            }
            if _cost_tracker is not None:
                _cost_tracker.annotate_payload(payload)
            write_atomic_json(durable_abs, payload)
            row["library_json_relative"] = path_under_engine(
                app_config.ENGINE_ROOT, durable_abs,
            )
        except Exception as dur_exc:  # noqa: BLE001
            _LOG.warning("Agentic durable JSON skipped (%s)", dur_exc)

    _persist_agentic_postplanner(
        items=items,
        page_ctx=page_ctx,
        post_type=post_type,
    )

    snippet_lines = []
    for idx, row in enumerate(items):
        cap = row.get("caption")
        if isinstance(cap, str) and cap.strip():
            snippet_lines.append(f"{idx + 1}. {cap}")
    snippet_path = app_config.PAGE_OUTPUTS_DIR / "last_captions_bundle.txt"
    if snippet_lines:
        try:
            snippet_path.parent.mkdir(parents=True, exist_ok=True)
            snippet_path.write_text("\n".join(snippet_lines) + "\n", encoding="utf-8")
        except OSError as snip_exc:
            _LOG.warning("last_captions_bundle.txt write failed (%s)", snip_exc)

    approved = envelope.get("successful") or 0
    print(
        f"\n[agentic] Done | approved={approved}/{qty} | "
        f"images={envelope.get('total_images_generated', 0)} | "
        f"out={out_dir}"
    )
    _LOG.info(
        "AGENTIC PIPELINE DONE | page=%s | approved=%s/%s | dir=%s",
        page_id, approved, qty, out_dir,
    )
    return envelope


def _persist_agentic_postplanner(
    *,
    items: list[dict[str, Any]],
    page_ctx: PageContext | None,
    post_type: str,
) -> None:
    """Write bulk + timestamped PostPlanner xlsx rows for agentic stills."""
    if not items:
        _LOG.info("PostPlanner | agentic persist skipped (no items)")
        return
    run_stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    bulk_path = Path(app_config.POST_PLANNER_XLSX).expanduser().resolve()
    postplanner_dir = Path(app_config.PAGE_OUTPUTS_DIR).expanduser().resolve() / "postplanner"
    template = getattr(app_config, "BULK_POSTS_TEMPLATE_XLSX", None)
    planner_type = "VIDEO" if str(post_type).upper().endswith("REEL") else "IMAGE"
    written = 0
    for row in items:
        caption = row.get("caption") or ""
        if not str(caption).strip():
            continue
        media = (
            row.get("imgbb_url")
            or row.get("local_image_path")
            or row.get("image_path")
            or ""
        )
        variant_ix = max(0, int(row.get("variant_index") or 1) - 1)
        posting_time = scheduled_bulk_post_display(variant_index=variant_ix)
        try:
            planner_row_ix = append_planner_row(
                bulk_path,
                posting_time=posting_time,
                caption=str(caption),
                media_url=str(media),
                post_type_value=planner_type,
                template_path=template,
            )
            row["excel_row"] = planner_row_ix
        except Exception as bulk_exc:  # noqa: BLE001
            _LOG.error(
                "PostPlanner bulk workbook write failed (variant %s) | path=%s | %s",
                row.get("variant_index"), bulk_path, bulk_exc,
            )
        try:
            xlsx_path = append_postplanner_xlsx_row(
                postplanner_dir,
                run_stamp=run_stamp,
                posting_time=posting_time,
                caption=str(caption),
                media_url=str(media),
            )
            row["postplanner_xlsx"] = str(xlsx_path)
            written += 1
        except Exception as xlsx_exc:  # noqa: BLE001
            _LOG.error(
                "PostPlanner timestamped xlsx write failed (variant %s) | dir=%s | %s",
                row.get("variant_index"), postplanner_dir, xlsx_exc,
            )
    _LOG.info(
        "PostPlanner agentic persist | rows=%s | bulk=%s | dir=%s",
        written, bulk_path, postplanner_dir,
    )


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
    render_approval_required: bool = False,
    agentic_pipeline: bool | None = None,
) -> dict[str, Any]:
    qty = max(1, quantity)
    economic = economic_brain_mode if economic_brain_mode is not None else app_config.ECONOMIC_BRAIN_MODE

    # Page-level override: if page_config.py sets ECONOMIC_BRAIN_MODE explicitly,
    # honour it unless the caller explicitly passed economic_brain_mode as non-None.
    if economic_brain_mode is None and page_ctx is not None:
        _page_econ = page_ctx.page_economic_brain_mode
        if _page_econ is not None:
            economic = _page_econ
            _LOG.info(
                "Page-level economic override | page=%s ECONOMIC_BRAIN_MODE=%s",
                page_ctx.page_id, economic,
            )

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
        architect = VisualArchitect(channel=ChannelFactory.from_env())
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
    import random as _rnd
    _page_id_lower = (page_ctx.page_id if page_ctx else "").lower()
    _channel = ChannelFactory.from_env()
    _isolated = ChannelFactory.is_isolated(_page_id_lower or _channel.channel_id)

    resolved_subject = (subject or "").strip()
    _cli_topic = resolved_subject
    if _isolated:
        if resolved_subject and _channel.topic_is_off_niche(resolved_subject):
            _LOG.warning(
                "AK TOPIC HARD-OVERRIDE | discarding leaked/stale topic %r",
                resolved_subject,
            )
            resolved_subject = ""
            _cli_topic = ""
        if not resolved_subject:
            resolved_subject = _channel.pick_niche_topic()
            _LOG.info(
                "AK TOPIC HARD-OVERRIDE | selected niche topic → %r (bank=%d)",
                resolved_subject,
                len(_channel.get_niche_topics()),
            )
    elif not resolved_subject:
        resolved_subject = imagine_subject(corpus)

    # WONDER_FEED + MASTER_MEI reels: ALWAYS pick a fresh topic from the TOPIC_POOL.
    # ancient_knowledge is handled above and never reads the PDF/wellness corpus.
    if (
        (not _isolated)
        and _page_id_lower in ("wonder_feed", "master_mei")
        and post_type in ("ECONOMIC_REEL", "SMART_BAIT")
        and page_ctx is not None
        and page_ctx.topic_pool
    ):
        resolved_subject = _rnd.choice(page_ctx.topic_pool)
        if _page_id_lower == "master_mei":
            from avatar_engine.mei_narrative import episode_theme_meta
            _ep = episode_theme_meta(resolved_subject)
            _LOG.info(
                "%s TOPIC LOCK | episode=%s | theme=%s | topic=%r (pool size=%d)",
                _page_id_lower, _ep["label"], _ep["key"],
                resolved_subject, len(page_ctx.topic_pool),
            )
        else:
            _LOG.info(
                "%s TOPIC LOCK | fresh pool topic selected → %r (pool size=%d)",
                _page_id_lower, resolved_subject, len(page_ctx.topic_pool),
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

    # ── BATCH PLANNER: Angles Matrix (qty > 1) ───────────────────────────────
    # Deconstruct the core topic into N unique non-overlapping sub-angles BEFORE
    # the generation loop. Each worker receives Global Topic DNA + Specific Angle.
    batch_angles: list[BatchAngle] | None = None
    uniqueness_guard: BatchUniquenessGuard | None = None
    global_topic_dna = (_cli_topic or resolved_subject or "").strip()

    # ── PER-VARIANT TOPIC LIST (legacy pool / LLM topics) ────────────────────
    # When qty > 1 and the user did not specify a topic, every variant must
    # receive its own unique, freshly-generated subject.  The strategy is:
    #   1. If the page topic_pool is large enough → shuffle + sample without
    #      replacement (fast, zero API cost).
    #   2. If the pool is too small → generate the remainder via Gemini in one
    #      call using the page-specialised generate_bulk_topics() prompt.
    #   3. Pool absent entirely → call generate_bulk_topics() for all qty slots.
    per_variant_topics: list[str] | None = None
    _seed_for_angles: list[str] | None = None
    if not _cli_topic and qty > 1:
        _pool = page_ctx.topic_pool if page_ctx else []
        if _pool and len(_pool) >= qty:
            _pool_copy = list(_pool)
            _rnd.shuffle(_pool_copy)
            # Anchor variant-0 to the already-resolved_subject so the pre-research
            # phase that runs before the loop still uses a consistent base topic.
            per_variant_topics = [resolved_subject] + [
                t for t in _pool_copy if t != resolved_subject
            ][:qty - 1]
            _seed_for_angles = list(per_variant_topics)
            _LOG.info(
                "Bulk topics | pool-sampled %d unique topics for page=%s",
                len(per_variant_topics), _page_id_lower,
            )
        else:
            _llm_topics = generate_bulk_topics(
                qty,
                page_id=_page_id_lower,
                page_niche=page_ctx.content_niche if page_ctx else "",
            )
            if _llm_topics:
                # Combine pool topics + LLM topics, deduplicate, take qty
                _combined = _llm_topics + list(_pool or [])
                _seen: set[str] = set()
                _deduped: list[str] = []
                for _t in _combined:
                    if _t.lower() not in _seen:
                        _seen.add(_t.lower())
                        _deduped.append(_t)
                per_variant_topics = _deduped[:qty]
                # Ensure the first variant matches the pre-resolved subject
                if per_variant_topics:
                    per_variant_topics[0] = resolved_subject
                _seed_for_angles = list(per_variant_topics)
                _LOG.info(
                    "Bulk topics | LLM-generated %d unique topics for page=%s",
                    len(per_variant_topics), _page_id_lower,
                )
            else:
                _LOG.warning(
                    "Bulk topics | LLM generation returned no topics — all variants "
                    "will use the same resolved_subject: %r", resolved_subject,
                )

    if qty > 1:
        uniqueness_guard = BatchUniquenessGuard()
        # Prefer explicit CLI topic as Global DNA; else resolved subject / niche
        _core = (_cli_topic or "").strip() or global_topic_dna or (
            page_ctx.content_niche if page_ctx else ""
        ) or "Content series"
        global_topic_dna = _core
        batch_angles = plan_angles_matrix(
            _core,
            qty,
            page_id=_page_id_lower,
            page_niche=page_ctx.content_niche if page_ctx else "",
            seed_topics=_seed_for_angles or per_variant_topics,
        )
        # Prefer angles matrix as the authoritative per-variant topic list
        per_variant_topics = [a.combined_topic for a in batch_angles]
        if batch_angles:
            resolved_subject = batch_angles[0].combined_topic
        envelope["batch_angles"] = [
            {
                "index": a.index,
                "angle_title": a.angle_title,
                "hook_style": a.hook_style,
                "visual_focus": a.visual_focus,
                "seo_title_hint": a.seo_title_hint,
            }
            for a in batch_angles
        ]
        envelope["global_topic_dna"] = global_topic_dna
        _LOG.info(
            "BatchPlanner | uniqueness guard armed | angles=%d | core=%r",
            len(batch_angles), global_topic_dna,
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
            caption_engine = CaptionEngine(channel=ChannelFactory.from_env())
            _LOG.info("CaptionEngine online | Gemini text head=`%s`", caption_engine.research_primary_id)
        except Exception as exc:  # noqa: BLE001
            _LOG.error("CaptionEngine init failed: %s", exc, exc_info=True)
            logging.error(
                "FATAL_BEFORE_EXIT | CaptionEngine init | stage=initialization | Gemini_text_head=%s | err=%s",
                bm.research_primary_id,
                exc,
            )
            raise

    if _isolated:
        if _channel.topic_is_off_niche(resolved_subject):
            resolved_subject = _channel.pick_niche_topic()
        if per_variant_topics:
            per_variant_topics = [
                (t if not _channel.topic_is_off_niche(t) else _channel.pick_niche_topic())
                for t in per_variant_topics
            ]
        global_topic_dna = resolved_subject
        _LOG.info(
            "AK TOPIC LOCK FINAL | resolved_subject=%r | variants=%s",
            resolved_subject,
            len(per_variant_topics or [resolved_subject]),
        )

    envelope["resolved_subject"] = resolved_subject

    from core_engine.agentic_pipeline import should_use_agentic_pipeline as _should_agentic

    if _should_agentic(
        page_ctx.page_id if page_ctx else None,
        post_type,
        agentic_pipeline,
    ):
        _LOG.info(
            "AGENTIC PIPELINE | page=%s | qty=%s | ThemeCurator→Copywriter→VisualDirector⇄VisualQA",
            page_ctx.page_id if page_ctx else app_config.ACTIVE_PAGE,
            qty,
        )
        print(
            "\n[agentic] 4-tier pipeline | "
            "ThemeCurator → Copywriter → VisualDirector ⇄ VisualQA "
            f"(MAX_RETRIES=3) | qty={qty}"
        )
        return _produce_agentic(
            resolved_subject=resolved_subject,
            qty=qty,
            skip_image=skip_image,
            skip_caption=skip_caption,
            page_ctx=page_ctx,
            post_type=post_type,
            post_format=post_format,
            caption_engine=caption_engine,
            per_variant_topics=per_variant_topics,
            envelope_base=envelope,
        )

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
        # master_mei: hard-prefer forced avatar.png DNA over cycled avatars
        if (
            page_ctx
            and (page_ctx.page_id or "").lower() == "master_mei"
            and not page_ctx.sequence_force_avatar_off
        ):
            _forced_ref = page_ctx.forced_avatar_reference_path
            if _forced_ref is not None and _forced_ref.is_file():
                effective_ref_path = _forced_ref
        if effective_ref_path is None and page_ctx and page_ctx.avatar_reference_exists:
            # Prefer cycling pool (assets/{page}/avatars) when available
            _cycled = page_ctx.cycle_avatar_reference(0)
            effective_ref_path = _cycled or page_ctx.avatar_reference_png
        elif effective_ref_path is None and page_ctx:
            _cycled = page_ctx.cycle_avatar_reference(0)
            if _cycled is not None:
                effective_ref_path = _cycled
            else:
                effective_ref_path = app_config.REFERENCE_IMAGE_PATH
        elif effective_ref_path is None:
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
    batch_image_counter: list[int] = [0]
    batch_image_lock = threading.Lock()

    _wkw: dict[str, Any] = dict(
        qty=qty,
        slug=slug,
        resolved_subject=resolved_subject,
        per_variant_topics=per_variant_topics,
        batch_angles=batch_angles,
        uniqueness_guard=uniqueness_guard,
        global_topic_dna=global_topic_dna,
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
        batch_image_counter=batch_image_counter,
        batch_image_lock=batch_image_lock,
        render_approval_required=bool(render_approval_required),
    )

    # ── Instantiate CostTracker — one per run, shared across workers ──────────
    # Each worker receives the same tracker; thread-safety is handled internally.
    _cost_tracker: CostTracker | None = None
    if page_ctx is not None and page_ctx.enable_cost_tracking:
        _cost_tracker = CostTracker(
            page_id=page_ctx.page_id,
            cost_tier=page_ctx.cost_tier,
        )
        _LOG.info(
            "CostTracker enabled | page=%s tier=%s",
            page_ctx.page_id, page_ctx.cost_tier,
        )
    _wkw["cost_tracker"] = _cost_tracker

    if qty == 1:
        # Single-variant path: exceptions propagate naturally to cli()
        # so the clean API-error display and logging remain intact.
        raw_results = [_produce_variant_worker(0, **_wkw)]
    else:
        # Multi-variant path: each worker runs concurrently; a failure in
        # one variant is caught per-future and logged, all others complete.
        max_workers = min(qty, 3)
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

    # ── FINAL COST SNAPSHOT: capture the shared tracker AFTER all workers ────
    # Each worker writes its per-variant view of the tracker, but since all workers
    # share the same CostTracker instance, the post-loop snapshot has the full
    # accumulated totals across every variant.  Store it at the envelope level so
    # _print_production_summary always displays the complete picture.
    _total_images_generated = sum(r.get("images_generated", 0) for r in items)
    envelope["total_images_generated"] = _total_images_generated
    if _cost_tracker is not None and page_ctx is not None and page_ctx.enable_cost_tracking:
        envelope["final_cost_breakdown"] = _cost_tracker.to_dict()
        _LOG.info(
            "Final cost snapshot | total_images=%d | total_usd=%.6f | tier=%s",
            _total_images_generated,
            _cost_tracker.total_usd(),
            _cost_tracker.cost_tier,
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


def run_test_images_debug_mode(
    *,
    topic: "str | None",
    page_id: str,
    avatar_mode: str,
    post_format: str,
    n: int,
    economic: bool = True,
) -> None:
    """
    ``--test-images [N]`` debug mode.

    Generates the real production voiceover script and the first ``n`` visual
    image prompts using the SAME prompt-generation code path as a live run
    (``_build_master_mei_script_act_prompts`` for master_mei, the generic
    aligned-visual-block builder for other sequence pages), prints every
    final prompt, generates ``n`` images in parallel
    (``ThreadPoolExecutor(max_workers=5)``).

    Master Mei saves stills (768×1344) to
    ``outputs/master_mei/VisualQA_Agent_Judge/attempts/``.
    Other pages save to ``outputs/<page_id>/test_previews/``.

    Bypasses ElevenLabs voiceover synthesis, MoviePy video compilation, and
    all YouTube / B2 / ImgBB uploads entirely.
    """
    from page_loader import load_page_context
    from avatar_engine.caption_engine import CaptionEngine
    from avatar_engine.providers.model_router import image_model as _route_image_dbg

    n = max(1, int(n))
    page_ctx = load_page_context(page_id, avatar_mode=avatar_mode, post_format=post_format)
    resolved_subject = (topic or "").strip() or page_ctx.display_name or page_id
    is_mm = (page_ctx.page_id or "").lower() == "master_mei"
    base_style = (
        page_ctx.illustration_style.rstrip(" .")
        if page_ctx.illustration_style
        else page_ctx.atmosphere_style.rstrip(" .")
    )

    if page_ctx.uses_plan_scenes_pacing:
        seq_n = len(
            _plan_scenes(
                page_ctx.reel_duration,
                page_ctx.scene_duration,
                progressive_start_s=page_ctx.scene_progressive_start_s,
                progressive_step_every=page_ctx.scene_progressive_step_every,
                progressive_step_s=page_ctx.scene_progressive_step_s,
                progressive_cap_s=page_ctx.scene_progressive_cap_s,
                min_scenes=page_ctx.reel_image_min_count,
                max_scenes=page_ctx.reel_image_count,
            )
        )
    elif page_ctx.reel_use_hook_body_pacing:
        seq_n = _compute_hook_body_act_count(
            page_ctx.reel_duration,
            hook_hold_s=page_ctx.reel_hook_hold_s,
            body_hold_s=page_ctx.reel_body_hold_s,
            min_acts=page_ctx.reel_image_min_count,
            max_acts=page_ctx.reel_image_count,
        )
    else:
        seq_n = _compute_dense_act_count(
            page_ctx.reel_duration,
            seconds_per_act=page_ctx.reel_seconds_per_act,
            min_acts=page_ctx.reel_image_min_count,
            max_acts=page_ctx.reel_image_count,
        )
    n = min(n, seq_n)

    print("\n" + "=" * 60)
    print(f"[DEBUG] --test-images MODE | page={page_id} | subject={resolved_subject!r}")
    print(f"[DEBUG] Requested previews={n} | full production act_count={seq_n}")
    print("=" * 60)

    print("[DEBUG] Generating voiceover script…")
    ce = CaptionEngine(channel=ChannelFactory.from_env())
    script = ce.generate_sequence_voiceover(
        resolved_subject,
        page_niche=page_ctx.content_niche,
        persona_voice=page_ctx.tts_voice_preference or (
            "calm, deep, highly wise, authoritative ancient master"
            if is_mm else "investigative, neutral, immersive"
        ),
        n_acts=seq_n,
        duration_s=page_ctx.reel_duration,
        total_words_target=page_ctx.reel_narration_words,
        economic=economic,
        niche_disclaimer=page_ctx.niche_disclaimer,
        cta_line="",
        narrative_mode=page_ctx.narrative_mode,
    ) or resolved_subject
    print(f"[DEBUG] Script ({len(script.split())} words):\n{script}\n")

    # Build visual prompts for test previews.
    # Master Mei: force core Giger pillars (never Mei likeness).
    #   tech_slavery / warrior_forge / panopticon
    from avatar_engine.mei_visual import (
        MASTER_STYLE_ANCHOR_DEFAULT as _MM_STYLE_DEFAULT,
        TEST_PREVIEW_MODULES as _MM_TEST_MODULES,
        TEST_PREVIEW_MODULES_3 as _MM_TEST_MODULES_3,
        build_test_preview_prompts as _mm_build_test_previews,
    )
    from avatar_engine.style_reader import default_master_mei_ref_folder as _mm_ref_folder

    _mm_pillar_keys: tuple = ()
    if is_mm:
        _mm_fixed_anchor = (
            page_ctx.master_style_anchor or _MM_STYLE_DEFAULT
        ).strip() or _MM_STYLE_DEFAULT
        _mm_style_dir = _mm_ref_folder()
        if n >= 4:
            _mm_pillar_keys = _MM_TEST_MODULES
        else:
            _mm_pillar_keys = _MM_TEST_MODULES_3[:n]
        print(
            f"[DEBUG] VISION_AGENT | style_reference folder → {_mm_style_dir}"
        )
        preview_source = _mm_build_test_previews(
            script=script,
            subject=resolved_subject,
            style_anchor=_mm_fixed_anchor,
            segment_fn=_split_script_into_act_chunks,
            module_keys=_mm_pillar_keys,
            reference_folder=_mm_style_dir,
            use_vision_style=True,
        )
        print(
            "[DEBUG] Master Mei EXCLUDED from test previews | modules: "
            + ", ".join(_mm_pillar_keys)
            + " | resolution 768x1344"
        )
    else:
        mei_slots = set()
        from avatar_engine.prompt_alignment import build_aligned_visual_block

        spoken_snippets = _segment_script_into_act_snippets(script, seq_n)
        act_descriptors = _build_reel_act_descriptors(
            resolved_subject, seq_n, page_id=page_ctx.page_id or ""
        )
        topic_entity_ctx = _extract_topic_visual_entities(resolved_subject)
        topic_entity_prefix = (
            f"VISUAL SUBJECT: {topic_entity_ctx}. " if topic_entity_ctx else ""
        )
        parallax_directive = (
            "DEPTH LAYERS: Foreground — dust particles, stone arch, or human observer. "
            "Background — distant temple façade, starfield, or horizon. Force multi-plane depth."
        )
        lighting_tail = (
            "LIGHTING: Dramatic directional light, volumetric shafts, high-contrast cinematic "
            "look, 35mm film grain, full-bleed, no borders."
        )
        all_prompts = []
        for i in range(seq_n):
            snippet = spoken_snippets[i] if i < len(spoken_snippets) else resolved_subject
            prev_snip = (
                spoken_snippets[i - 1] if i > 0 and (i - 1) < len(spoken_snippets) else ""
            )
            act_desc = (
                act_descriptors[i] if i < len(act_descriptors)
                else f"ACT {i + 1}: {resolved_subject}."
            )
            align = build_aligned_visual_block(
                spoken_snippet=snippet, act_index=i, total_acts=seq_n,
                main_subject=resolved_subject, prev_snippet=prev_snip,
            )
            all_prompts.append(
                f"{topic_entity_prefix}{base_style}. {act_desc} {align} "
                f"{parallax_directive}{lighting_tail}"
            )
        preview_source = all_prompts

    from avatar_engine.providers.together_image import sanitize_prompt_for_flux

    # Sanitize BEFORE printing so the debug output matches EXACTLY what is
    # sent to Together/FLUX (TogetherImageAdapter.generate() re-applies the
    # same idempotent sanitizer right before the API call).
    # For master_mei, preview_source is already exactly the pillar prompt set.
    _take = len(preview_source) if is_mm else n
    test_prompts = [sanitize_prompt_for_flux(p) for p in preview_source[:_take]]
    for idx, p in enumerate(test_prompts, start=1):
        label = ""
        if is_mm and _mm_pillar_keys and idx <= len(_mm_pillar_keys):
            label = f" [{_mm_pillar_keys[idx - 1]}]"
        print(f"[DEBUG] Final Prompt Sent to API [{idx}/{len(test_prompts)}]{label}: {p}")

    # ------------------------------------------------------------------
    # Image generation — N images in parallel (max_workers=5)
    # ------------------------------------------------------------------
    if is_mm:
        out_dir = (
            app_config.OUTPUTS_DIR / page_id / "VisualQA_Agent_Judge" / "attempts"
        )
    else:
        out_dir = app_config.OUTPUTS_DIR / page_id / "test_previews"
    out_dir.mkdir(parents=True, exist_ok=True)

    _page_cost = page_ctx.cost_tier
    _img_route = _route_image_dbg(
        task="image",
        page_cost_tier=_page_cost,
        model_override=page_ctx.image_model_override,
        preferred=(
            app_config.GEMINI_ECONOMIC_IMAGE_MODEL if economic else app_config.GEMINI_IMAGE_MODEL
        ),
        use_premium=True if (_page_cost or "").lower() == "premium" else None,
        log=True,
    )
    img_model_id = app_config.normalize_image_model_id(_img_route.model_id)
    # Routes to RemoteGPUImageAdapter when ENABLE_REMOTE_GPU_WORKFLOWS=true
    adapter = get_image_adapter(
        model_id=img_model_id, page_cost_tier=_page_cost, tier=_img_route.tier,
    )

    # --test-images for master_mei: never attach avatar.png (Mei excluded).
    style_refs = page_ctx.resolve_style_reference_images()
    style_weight = page_ctx.style_reference_weight
    print(
        f"[DEBUG] Loaded {len(style_refs)} style reference assets for {page_id}: "
        f"{[p.name for p in style_refs]} | weight={style_weight}"
    )
    if is_mm:
        print(f"[DEBUG] Draft output dir -> {out_dir}")

    jobs: "dict[int, Any]" = {}
    for idx, p in enumerate(test_prompts):
        _p_lo = (p or "").lower()
        _skip_sref = is_mm and any(
            tok in _p_lo
            for tok in (
                "fitness model physique",
                "glossy led fashion",
                "clean high-fashion",
            )
        )
        jobs[idx] = functools.partial(
            adapter.generate,
            p,
            output_stem=f"attempt_{idx + 1:02d}" if is_mm else f"test_preview_act{idx + 1:02d}",
            output_directory=out_dir,
            reference_image_path=None,
            avatar_mode="OFF",
            reference_image_weight=None,
            style_reference_paths=None if _skip_sref else (style_refs or None),
            style_reference_weight=None if _skip_sref else style_weight,
            draft=bool(is_mm),
        )

    print(
        f"\n[DEBUG] Generating {len(jobs)} test image(s) in parallel "
        f"(ThreadPoolExecutor max_workers=5) → {out_dir}"
    )
    results = _run_acts_parallel(jobs, max_workers=5)

    ok = 0
    for idx in range(len(test_prompts)):
        res = results.get(idx)
        if isinstance(res, Exception):
            print(f"[DEBUG] Image {idx + 1}/{n} FAILED: {res}")
        else:
            print(f"[DEBUG] Image {idx + 1}/{n} OK -> {res}")
            ok += 1

    print(
        f"\n[DEBUG] --test-images complete | {ok}/{len(test_prompts)} succeeded | "
        f"saved to {out_dir}"
    )
    print(
        "[DEBUG] Bypassed: ElevenLabs voiceover synthesis, MoviePy video compilation, "
        "YouTube/B2/ImgBB uploads."
    )


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
        "--agentic-pipeline",
        dest="agentic_pipeline",
        action="store_true",
        default=None,
        help=(
            "Force the 4-tier agentic pipeline "
            "(ThemeCurator → Copywriter → VisualDirector ⇄ VisualQA). "
            "Auto-on for ancient_knowledge static image posts."
        ),
    )
    parser.add_argument(
        "--no-agentic-pipeline",
        dest="agentic_pipeline",
        action="store_false",
        help="Disable the agentic pipeline and use the legacy per-variant worker path.",
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
        choices=[
            "STANDARD_QUOTE",
            "SMART_BAIT",
            "LONG_CAPTION_IMAGE",
            "ECONOMIC_REEL",
            "ECONOMIC_REEL_LOFI",
            "WAN_REEL",
            "CAROUSEL",
            "REFERENCE_BASED_REELS",
        ],
        metavar="POST_TYPE",
        help=(
            "STANDARD_QUOTE (default): long-form educational caption from PDF research. "
            "SMART_BAIT: 4-layer image stack (bg + 20%% mask + bold text + logo) with ultra-short "
            "viral hook + sarcastic one-liner caption. Uses illustration_style for Gemini prompt. "
            "LONG_CAPTION_IMAGE: contextual illustration image with ONLY a logo overlay (no text/mask). "
            "Deep long-form storytelling caption about relationships/character. "
            "ECONOMIC_REEL: graphite image base (same pipeline as SMART_BAIT) compiled into a "
            "vertical 9:16 MP4 reel with ElevenLabs TTS voiceover, dark ambient soundscape, "
            "and cinematic Ken Burns zoom-in. Outputs .mp4 + durable JSON. "
            "ECONOMIC_REEL_LOFI: separate LOFI pipeline (Flux Schnell, no LoRA) — multi-scene "
            "ink/graphic-novel stills, duotone grade, Ken Burns, channel watermark. "
            "Uses --duration (30–38) and --module (relationship|parenting). "
            "Does NOT share state with ECONOMIC_REEL. "
            "WAN_REEL: Flux LoRA stills → Wan2.2 img2vid at fixed scene_duration "
            "(default fixed:7) → concat → F5 narration. Use scripts/run_wan_reel_test.py "
            "for the first cost/quality test render (no publish). "
            "CAROUSEL: generates 3 visually cohesive images (slide_01..03) with distinct "
            "scene viewpoints for a 3-part visual narrative post. "
            "REFERENCE_BASED_REELS: extract a raw-footage clip, overlay an LLM hook text, "
            "blend lullaby ambient audio — no image generation. Designed for momma_circle."
        ),
    )
    parser.add_argument(
        "--duration",
        dest="duration",
        type=int,
        default=None,
        metavar="SECONDS",
        help=(
            "ECONOMIC_REEL_LOFI only: target reel length in seconds (30–38, default 34). "
            "Scene count = round(duration / 4)."
        ),
    )
    parser.add_argument(
        "--module",
        dest="module",
        default=None,
        choices=["relationship", "parenting"],
        metavar="MODULE",
        help=(
            "ECONOMIC_REEL_LOFI only: RAG theme namespace. "
            "relationship (default) for wonder_feed + momma_circle; "
            "parenting only for momma_circle."
        ),
    )
    parser.add_argument(
        "--test-preview",
        dest="test_preview",
        action="store_true",
        default=False,
        help=(
            "ECONOMIC_REEL_LOFI only: single-image aesthetic review (Flux Schnell + "
            "production grading + LOFI caption typography + watermark). "
            "Does NOT run script/validator/video/publish and does NOT write RAG history."
        ),
    )
    parser.add_argument(
        "--prompt",
        dest="prompt",
        default=None,
        metavar="TEXT",
        help=(
            "ECONOMIC_REEL_LOFI --test-preview: optional custom scene description. "
            "If omitted, a fixed seed-aligned baseline prompt is used."
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
        "--publish-youtube",
        "--upload-youtube",
        dest="publish_youtube",
        action="store_true",
        default=False,
        help=(
            "Auto-upload compiled reels to YouTube AFTER generation as SCHEDULED "
            "(Programado): privacyStatus=private + publishAt staggered timestamps. "
            "Never uploads as unlisted/immediate. "
            "Uses isolated OAuth token per page at credentials/tokens/youtube_token_{page}.json. "
            "Alias: --upload-youtube. Overrides ENABLE_YOUTUBE_UPLOAD in .env."
        ),
    )
    parser.add_argument(
        "--yt-privacy",
        dest="yt_privacy",
        default=None,
        choices=["public", "unlisted", "private"],
        metavar="PRIVACY",
        help=(
            "IGNORED when --publish-youtube is set (forced to private + publishAt for Scheduled). "
            "Kept for legacy one-off scripts only."
        ),
    )
    parser.add_argument(
        "--schedule-uploads",
        dest="schedule_uploads",
        action="store_true",
        default=False,
        help=(
            "Legacy flag — scheduling is now ALWAYS ON with --publish-youtube "
            "(private + publishAt). Kept for backward compatibility."
        ),
    )
    parser.add_argument(
        "--interval-hours",
        dest="interval_hours",
        type=float,
        default=12.0,
        metavar="HOURS",
        help=(
            "Base interval in hours between scheduled posts when --publish-youtube "
            "is active. Video i publishes at now + interval_hours*(i+1) (+ random delay). "
            "Default: 12.0."
        ),
    )
    parser.add_argument(
        "--random-delay-max-minutes",
        dest="random_delay_max_minutes",
        type=int,
        default=60,
        metavar="MINUTES",
        help=(
            "Random additional delay in minutes (0 to X) added to each scheduled "
            "publishAt slot. Default: 60."
        ),
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
    parser.add_argument(
        "--clip-duration",
        dest="clip_duration",
        type=float,
        default=None,
        metavar="SECONDS",
        help=(
            "Target clip length in seconds for REFERENCE_BASED_REELS post type. "
            "Clamped to the page config CLIP_DURATION_MIN_S / CLIP_DURATION_MAX_S range. "
            "Default: midpoint of the configured range (e.g. 37 s for 15–60 s)."
        ),
    )
    parser.add_argument(
        "--render-approval-required",
        dest="render_approval_required",
        action="store_true",
        default=False,
        help=(
            "After scene images are saved to "
            "outputs/<page>/assets/<episode_id>/ as scene_01.png, … "
            "pause the pipeline, print that episode assets path, and wait "
            "for [ENTER] before audio/video compilation. Use Ctrl+C to abort. "
            "Default: off (continue automatically)."
        ),
    )
    parser.add_argument(
        "--test-images",
        dest="test_images",
        type=int,
        default=None,
        metavar="N",
        help=(
            "DEBUG MODE: generate the production script + the first N visual image "
            "prompts for --page, print every final prompt, generate N images in "
            "parallel (ThreadPoolExecutor max_workers=5) to "
            "outputs/<page_id>/test_previews/, then exit immediately. Bypasses "
            "ElevenLabs voiceover, MoviePy video compilation, and all "
            "YouTube/B2/ImgBB uploads."
        ),
    )
    parser.add_argument(
        "--resume-youtube-queue",
        dest="resume_youtube_queue",
        action="store_true",
        default=False,
        help=(
            "Read credentials/pending_youtube_uploads.json and attempt to publish "
            "any videos that were queued after a previous run hit YouTube's daily "
            "upload limit (~20 videos/channel/day). Scoped to --page when explicitly "
            "passed on the CLI, otherwise resumes queued uploads for ALL pages. "
            "Exits after resuming — does not generate new content."
        ),
    )
    from model_api_flows import list_preset_names as _list_flow_presets

    parser.add_argument(
        "--model-api-flow",
        dest="model_api_flow",
        type=str,
        default=None,
        metavar="PRESET",
        help=(
            "Select a model_api_flows preset (provider/infra per media type). "
            "Always overrides .env ENABLE_REMOTE_GPU_WORKFLOWS in either direction. "
            f"Presets: {', '.join(_list_flow_presets())}."
        ),
    )
    parser.add_argument(
        "--img-production",
        dest="img_production",
        type=str,
        default=None,
        metavar="PROVIDER[/MODEL]",
        help=(
            "Per-media image override (wins over --model-api-flow for image only). "
            "Examples: together/black-forest-labs/FLUX.1-schnell | remote_gpu | "
            "remote_gpu/flux_dev_lora_txt_to_img.json"
        ),
    )
    parser.add_argument(
        "--audio-production",
        dest="audio_production",
        type=str,
        default=None,
        metavar="PROVIDER",
        help=(
            "Per-media audio override (wins over --model-api-flow for audio only). "
            "Examples: elevenlabs | remote_gpu | remote_gpu/F5TTS_txt_to_audio.json"
        ),
    )
    parser.add_argument(
        "--video-production",
        dest="video_production",
        type=str,
        default=None,
        metavar="PROVIDER",
        help=(
            "Per-media video override (wins over --model-api-flow for video only). "
            "Examples: moviepy | remote_gpu | remote_gpu/wan2_2_img_to_video.json"
        ),
    )
    parser.add_argument(
        "--video-length",
        dest="video_length",
        type=float,
        default=None,
        metavar="SECONDS",
        help=(
            "Target reel duration in seconds for the shared scene pacing engine "
            "(plan_scenes). Wins over channels_config REEL_DURATION. "
            "Each channel/post_type supplies its own default when omitted "
            "(e.g. ancient_knowledge ~80)."
        ),
    )
    parser.add_argument(
        "--scene-duration",
        dest="scene_duration",
        type=str,
        default=None,
        metavar="SPEC",
        help=(
            "Scene pacing spec for plan_scenes (CLI > channel > factory equal). "
            "Forms: fixed:4 | progressive | "
            "progressive:start=4,step_every=3,step=1,cap=7.5 | equal"
        ),
    )
    args = parser.parse_args()

    # ── model_api_flows: resolve + apply before any generation ──────────────
    # Precedence: per-media flags > --model-api-flow > .env default.
    from model_api_flows import (
        apply_production_flow as _apply_flow,
        bootstrap_validate_all_presets as _validate_flow_presets,
        resolve_production_flow as _resolve_flow,
    )

    try:
        _validate_flow_presets()
        _flow_explicit = any(
            [
                getattr(args, "model_api_flow", None),
                getattr(args, "img_production", None),
                getattr(args, "audio_production", None),
                getattr(args, "video_production", None),
            ]
        )
        _resolved_flow = _resolve_flow(
            preset_name=getattr(args, "model_api_flow", None),
            img_production=getattr(args, "img_production", None),
            audio_production=getattr(args, "audio_production", None),
            video_production=getattr(args, "video_production", None),
        )
        _apply_flow(_resolved_flow, explicit=_flow_explicit)
        args.resolved_model_api_flow = _resolved_flow
        # Fail fast: ElevenLabs Key ID masquerading as secret (api_key_id_used_as_api_key)
        if getattr(_resolved_flow.audio, "provider", "") == "elevenlabs":
            try:
                app_config.assert_elevenlabs_api_key_usable()
            except RuntimeError as _el_exc:
                raise SystemExit(f"[elevenlabs] {_el_exc}") from _el_exc
    except (ValueError, FileNotFoundError) as _flow_exc:
        raise SystemExit(f"[model_api_flows] {_flow_exc}") from _flow_exc

    if args.resume_youtube_queue:
        _explicit_page = any(a == "--page" or a.startswith("--page=") for a in sys.argv[1:])
        _resume_page = args.page if _explicit_page else None
        print(
            "[YouTube] --resume-youtube-queue: resuming pending upload queue"
            + (f" for page='{_resume_page}' …" if _resume_page else " for ALL pages …")
        )
        _yt_resume_pending_queue(page_name=_resume_page)
        return

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
        if _wf_pt == "ECONOMIC_REEL_LOFI":
            args.post_format = "DYNAMIC_REEL"
            args.draw_style = "SKETCH"

    # Isolated channel (ancient_knowledge): visual rules + format come from
    # the adapter — never the hardcoded page_id sketch/photoreal branch.
    _boot_channel = ChannelFactory.from_env()
    if ChannelFactory.is_isolated(_boot_channel.channel_id):
        _ak_pt = getattr(args, "post_type", "").upper()
        if _ak_pt == "ECONOMIC_REEL":
            args.post_format = "DYNAMIC_REEL"
        args.draw_style = str(
            (_boot_channel.get_visual_rules() or {}).get("draw_style") or "NATURAL"
        )

    # ── MASTER_MEI (SUPER) STYLE & FORMAT LOCK ─────────────────────────────
    # Cinematic martial photorealism + avatar ON; sequence reel for ECONOMIC_REEL.
    if getattr(args, "page", "").lower() == "master_mei":
        _mm_pt = getattr(args, "post_type", "").upper()
        if _mm_pt == "ECONOMIC_REEL":
            args.post_format = "DYNAMIC_REEL"
        args.draw_style = "NATURAL"
        # Default avatar ON unless CLI explicitly set --avatar
        if getattr(args, "avatar", None) is None:
            args.avatar = "ON"

    # ── MOMMA_CIRCLE FORMAT LOCK ────────────────────────────────────────────
    # Reference-based pipeline by default; ECONOMIC_REEL_LOFI is an explicit escape hatch.
    if getattr(args, "page", "").lower() == "momma_circle":
        _mc_pt = getattr(args, "post_type", "").upper()
        if _mc_pt == "ECONOMIC_REEL_LOFI":
            args.post_format = "DYNAMIC_REEL"
            args.draw_style = "SKETCH"
        else:
            args.post_type = "REFERENCE_BASED_REELS"
            args.post_format = "REFERENCE_BASED_REELS"
            args.draw_style = "NATURAL"

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
        from page_loader import (  # noqa: PLC0415
            _CHANNELS_CONFIG_ROOT,
            _LEGACY_PAGES_CONFIG_ROOT,
            _load_page_config,
        )
        _cfg_dir = _CHANNELS_CONFIG_ROOT / page_id
        if not _cfg_dir.is_dir():
            _legacy = _LEGACY_PAGES_CONFIG_ROOT / page_id
            if _legacy.is_dir():
                _cfg_dir = _legacy
        _tmp_page_cfg = _load_page_config(_cfg_dir, page_id)
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
    if getattr(args, "post_type", "").upper() == "ECONOMIC_REEL_LOFI":
        post_format = "DYNAMIC_REEL"
    if getattr(args, "post_type", "").upper() == "WAN_REEL":
        post_format = "DYNAMIC_REEL"
    # REFERENCE_BASED_REELS uses its own engine path — keep the format as-is.
    if getattr(args, "post_type", "").upper() == "REFERENCE_BASED_REELS":
        post_format = "REFERENCE_BASED_REELS"

    try:
        page_ctx = load_page_context(page_id, avatar_mode=avatar_mode, post_format=post_format)
    except ValueError as ve:
        raise SystemExit(str(ve)) from ve

    # Scene pacing overrides — CLI > channels_config > factory default.
    if getattr(args, "video_length", None) is not None:
        page_ctx.page_cfg["VIDEO_LENGTH_OVERRIDE"] = float(args.video_length)
        page_ctx.page_cfg["REEL_DURATION"] = float(args.video_length)
        print(
            f"[bootstrap] video_length={float(args.video_length):.0f}s "
            "(CLI override → plan_scenes)"
        )
    if getattr(args, "scene_duration", None):
        page_ctx.page_cfg["SCENE_DURATION"] = str(args.scene_duration).strip()
        print(
            f"[bootstrap] scene_duration={page_ctx.page_cfg['SCENE_DURATION']!r} "
            "(CLI override → plan_scenes)"
        )

    economic_choice: bool | None
    if args.premium:
        economic_choice = False
    elif args.economic:
        economic_choice = True
    else:
        economic_choice = None

    econ_resolved = economic_choice if economic_choice is not None else app_config.ECONOMIC_BRAIN_MODE
    # Page-level override: respect ECONOMIC_BRAIN_MODE from page_config.py when
    # the CLI did not explicitly pass --economic or --premium.
    # This ensures that ancient_knowledge (ECONOMIC_BRAIN_MODE=True) always gets
    # the nano/flash image model, regardless of the global app_config default.
    if economic_choice is None and _tmp_page_cfg.get("ECONOMIC_BRAIN_MODE") is not None:
        econ_resolved = bool(_tmp_page_cfg["ECONOMIC_BRAIN_MODE"])
        _LOG.info(
            "Page-level economic override | page=%s ECONOMIC_BRAIN_MODE=%s (applied before model verification)",
            page_id, econ_resolved,
        )

    if args.test_images is not None:
        run_test_images_debug_mode(
            topic=(args.topic or None),
            page_id=page_id,
            avatar_mode=avatar_mode,
            post_format=post_format,
            n=args.test_images,
            economic=bool(econ_resolved),
        )
        return

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
    _flow_for_log = getattr(args, "resolved_model_api_flow", None)
    _active_post_type_early = args.post_type.upper() if hasattr(args, "post_type") else ""
    if _flow_for_log is not None:
        logging.getLogger(__name__).info(
            "MODEL_API_FLOW | %s", _flow_for_log.summary_line(),
        )
        if _active_post_type_early == "ECONOMIC_REEL_LOFI":
            # Clarify: LOFI image gen hard-bypasses this preset (Together Schnell, no LoRA).
            print(
                f"[bootstrap] MODEL_API_FLOW (global env preset, NOT used for "
                f"ECONOMIC_REEL_LOFI images) | {_flow_for_log.summary_line()}"
            )
            print(
                "[bootstrap] ECONOMIC_REEL_LOFI image provider FORCED | "
                "together.ai / black-forest-labs/FLUX.1-schnell / lora=OFF "
                "(ignores ENABLE_REMOTE_GPU_WORKFLOWS / MODEL_API_FLOW)"
            )
        else:
            print(f"[bootstrap] MODEL_API_FLOW | {_flow_for_log.summary_line()}")

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
        and _active_post_type in ("SMART_BAIT", "LONG_CAPTION_IMAGE", "ECONOMIC_REEL", "CAROUSEL")
    ):
        args.draw_style = "SKETCH"

    envelope: dict[str, Any] | None = None

    # ── ECONOMIC_REEL_LOFI early dispatch (isolated from ECONOMIC_REEL) ───
    if _active_post_type == "ECONOMIC_REEL_LOFI":
        # Aesthetic still review — never enters production / publish path.
        if getattr(args, "test_preview", False):
            try:
                from core_engine.economic_reel_lofi.test_preview import (
                    run_lofi_test_preview,
                )

                _preview = run_lofi_test_preview(
                    page_id=page_id,
                    prompt=getattr(args, "prompt", None),
                )
                print(f"\n[ECONOMIC_REEL_LOFI test-preview] {_preview.get('output_png')}")
            except Exception as prev_exc:  # noqa: BLE001
                if isinstance(prev_exc, KeyboardInterrupt):
                    raise
                _LOG.error("LOFI test-preview failed: %s", prev_exc, exc_info=True)
                print(
                    f"[ECONOMIC_REEL_LOFI test-preview] ERROR: "
                    f"{type(prev_exc).__name__}: {prev_exc}"
                )
                sys.exit(1)
            return

        try:
            import core_engine.economic_reel_lofi  # noqa: F401 — registers runner
            from core_engine.post_type_registry import get_post_type_runner

            _lofi_runner = get_post_type_runner("ECONOMIC_REEL_LOFI")
            if _lofi_runner is None:
                raise SystemExit(
                    "ECONOMIC_REEL_LOFI runner missing from POST_TYPE_REGISTRY"
                )
            _lofi_module = getattr(args, "module", None) or "relationship"
            _lofi_duration = getattr(args, "duration", None)
            envelope = _lofi_runner(
                page_id=page_id,
                quantity=args.quantity,
                duration=_lofi_duration,
                module=_lofi_module,
                # Reuse existing per-page tree: outputs/<page>/{clips,assets}/
                outputs_dir=page_ctx.outputs_dir,
            )
        except Exception as lofi_exc:  # noqa: BLE001
            if isinstance(lofi_exc, KeyboardInterrupt):
                raise
            _LOG.error("ECONOMIC_REEL_LOFI failed: %s", lofi_exc, exc_info=True)
            print(f"[ECONOMIC_REEL_LOFI] ERROR: {type(lofi_exc).__name__}: {lofi_exc}")
            sys.exit(1)

        if envelope:
            print(
                f"\n[ECONOMIC_REEL_LOFI] Completed "
                f"{envelope.get('successful', 0)}/{args.quantity} reel(s)."
            )
            for _it in envelope.get("items") or []:
                if _it.get("video_path"):
                    print(f"  Video : {_it['video_path']}")
                if _it.get("meta_path"):
                    print(f"  Meta  : {_it['meta_path']}")
        return

    # ── WAN_REEL early dispatch (test render — no publish) ────────────────
    # Flux LoRA stills → Wan2.2 @ fixed scene_duration → concat → F5 VO.
    if _active_post_type == "WAN_REEL":
        try:
            from core_engine.wan_reel_engine import run_wan_reel_test

            _wan_len = float(
                getattr(args, "video_length", None)
                or page_ctx.page_cfg.get("WAN_REEL_DURATION", 70)
                or 70
            )
            _wan_scene = str(
                getattr(args, "scene_duration", None)
                or page_ctx.page_cfg.get("WAN_SCENE_DURATION", "fixed:7")
                or "fixed:7"
            ).strip()
            if not _wan_scene.lower().startswith("fixed:"):
                raise SystemExit(
                    "WAN_REEL requires --scene-duration fixed:N "
                    f"(got {_wan_scene!r}). Progressive is ECONOMIC_REEL-only."
                )
            _wan_topic = (topic_raw or "").strip() or (
                "Göbekli Tepe — the 12,000-year-old temple that rewrote human history"
            )
            _wan_report = run_wan_reel_test(
                _wan_topic,
                page_id=page_id,
                video_length_s=_wan_len,
                scene_duration=_wan_scene,
                max_scenes=None,
            )
            print(f"\n[WAN_REEL] Output : {_wan_report.output_mp4}")
            print(
                f"[WAN_REEL] GPU    : {_wan_report.total_gpu_seconds:.1f}s "
                f"(≈ ${_wan_report.total_gpu_usd:.4f}) | "
                f"Together Wan2.7 ref @ $0.10/s × {_wan_report.video_length_s:.0f}s "
                f"= ${_wan_report.together_wan27_usd_at_0_10_per_s:.2f}"
            )
        except Exception as wan_exc:  # noqa: BLE001
            if isinstance(wan_exc, KeyboardInterrupt):
                raise
            _LOG.error("WAN_REEL failed: %s", wan_exc, exc_info=True)
            print(f"[WAN_REEL] ERROR: {type(wan_exc).__name__}: {wan_exc}")
            sys.exit(1)
        return

    # ── REFERENCE_BASED_REELS early dispatch ──────────────────────────────
    # Bypasses the full Gemini-image produce() pipeline — uses raw footage clips.
    if _active_post_type == "REFERENCE_BASED_REELS":
        try:
            from core_engine.reference_reel_engine import ReferenceReelEngine
            _ref_engine = ReferenceReelEngine(
                page_ctx,
                outputs_dir=page_ctx.outputs_dir,
                api_key_gemini=app_config.GEMINI_API_KEY,
                api_key_elevenlabs=app_config.ELEVENLABS_API_KEY,
            )
            postplanner_dir = page_ctx.outputs_dir / "postplanner"
            envelope = _ref_engine.run(
                quantity=args.quantity,
                topic=topic_raw or None,
                clip_duration=getattr(args, "clip_duration", None),
                postplanner_dir=postplanner_dir,
                run_stamp=ts_token,
                posting_slot_display="",
                publish_to_b2=True,
            )
        except Exception as ref_exc:  # noqa: BLE001
            if isinstance(ref_exc, KeyboardInterrupt):
                raise
            _LOG.error("REFERENCE_BASED_REELS failed: %s", ref_exc, exc_info=True)
            print(f"[refReel] ERROR: {type(ref_exc).__name__}: {ref_exc}")
            sys.exit(1)

        # Print lightweight summary and skip the YT block if no videos
        if envelope:
            _items = envelope.get("items") or []
            print(f"\n[refReel] Completed {envelope.get('successful', 0)}/{args.quantity} reel(s).")
            for _it in _items:
                if _it.get("video_path"):
                    print(f"  Video : {_it['video_path']}")
                if _it.get("b2_url"):
                    print(f"  B2 URL: {_it['b2_url']}")
        return  # skip produce() + YT block for this post type

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
            render_approval_required=bool(
                getattr(args, "render_approval_required", False)
            ),
            agentic_pipeline=getattr(args, "agentic_pipeline", None),
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

    # ── PHASE YT: YouTube Shorts auto-publish with per-page token isolation ──
    # Triggered by --publish-youtube / --upload-youtube OR ENABLE_YOUTUBE_UPLOAD.
    # Each --page uses credentials/tokens/youtube_token_{page}.json exclusively.
    #
    # STRICT: When publishing is active, uploads are ALWAYS Scheduled (Programado):
    #   privacyStatus = "private" + status.publishAt = staggered ISO timestamp.
    # Never upload as unlisted/public immediate when --publish-youtube is set.
    _should_publish_yt = getattr(args, "publish_youtube", False) or app_config.ENABLE_YOUTUBE_UPLOAD
    # Force continuous schedule queue whenever publishing (CLI flag becomes redundant but honored)
    _schedule_uploads = bool(_should_publish_yt) or bool(getattr(args, "schedule_uploads", False))
    if getattr(args, "schedule_uploads", False) and not _should_publish_yt:
        print(
            "[YouTube] --schedule-uploads requires --publish-youtube "
            "(or ENABLE_YOUTUBE_UPLOAD=true). Skipping schedule."
        )
        _schedule_uploads = False
    if _should_publish_yt and envelope and envelope.get("mode") != "test":
        _yt_privacy = "private"  # Scheduled / Programado requires private + publishAt
        _interval_h = float(getattr(args, "interval_hours", 12.0) or 12.0)
        if _interval_h <= 0:
            _interval_h = 12.0
        _rand_max_m = int(getattr(args, "random_delay_max_minutes", 60) or 0)
        if _rand_max_m < 0:
            _rand_max_m = 0

        _yt_rows = [
            row for row in (envelope.get("items") or [])
            if row.get("video_path")
        ]
        if not _yt_rows:
            print("[YouTube] No compiled video found in this run — skipping upload.")
        else:
            _yt_secrets = app_config.YOUTUBE_CLIENT_SECRETS
            _yt_token_dir = app_config.YOUTUBE_TOKEN_DIR
            _yt_token_file = _yt_token_path(page_id, _yt_token_dir)
            print(
                f"[YouTube] Page '{page_id}' → token file: {_yt_token_file}"
            )

            _yt_client = None
            try:
                _yt_creds = _yt_build_credentials(
                    page_id,
                    _yt_secrets,
                    _yt_token_dir,
                )
                _yt_client = _yt_build_client(_yt_creds)
                _yt_verify_channel(_yt_client, page_name=page_id)
            except Exception as _auth_exc:
                _LOG.warning(
                    "YT auth/init failed (%s) — uploads will retry per-row auth.",
                    _auth_exc,
                )

            _sched_anchor = datetime.now(timezone.utc)
            print(
                f"[YouTube Scheduler] Scheduling {len(_yt_rows)} video(s) as Programado | "
                f"privacy=private + publishAt | interval={_interval_h:g} h | "
                f"random delay=0–{_rand_max_m} min | "
                f"anchor={_sched_anchor.strftime('%Y-%m-%d %H:%M:%S')} UTC"
            )
            _LOG.info(
                "YT schedule-uploads FORCED | page=%s videos=%d interval_h=%s rand_max_m=%d",
                page_id,
                len(_yt_rows),
                _interval_h,
                _rand_max_m,
            )

            for _i, _yt_row in enumerate(_yt_rows):
                _extra_min = random.randint(0, _rand_max_m) if _rand_max_m > 0 else 0
                _publish_at = _sched_anchor + timedelta(
                    hours=_interval_h * (_i + 1),
                    minutes=_extra_min,
                )
                # YouTube API: Scheduled = private + publishAt (never unlisted)
                _privacy_for_row = "private"
                _iso = _publish_at.strftime("%Y-%m-%dT%H:%M:%SZ")
                print(
                    f"[YouTube Scheduler] Video {_i + 1}/{len(_yt_rows)} "
                    f"'{Path(_yt_row.get('video_path', '?')).name}' → "
                    f"SCHEDULED {_publish_at.strftime('%Y-%m-%d %H:%M:%S')} UTC "
                    f"(privacyStatus=private, publishAt={_iso}, +{_extra_min} min random)"
                )

                try:
                    _yt_vid_id, _yt_url, _yt_pa = _yt_upload_short(
                        row=_yt_row,
                        page_name=page_id,
                        privacy_status=_privacy_for_row,
                        publish_at=_publish_at,
                        client_secrets_path=_yt_secrets,
                        token_dir=_yt_token_dir,
                        youtube=_yt_client,
                    )
                    if _yt_vid_id:
                        _yt_row["youtube_video_id"] = _yt_vid_id
                        _yt_row["youtube_url"] = _yt_url
                        if _yt_pa:
                            _yt_row["youtube_scheduled_at"] = _yt_pa.strftime(
                                "%Y-%m-%d %H:%M UTC"
                            )
                            print(
                                f"[YouTube] ✓ Scheduled release confirmed → "
                                f"{_yt_pa.strftime('%Y-%m-%d %H:%M:%S')} UTC | {_yt_url}"
                            )
                        _LOG.info(
                            "YouTube upload OK | page=%s  id=%s  url=%s  publish_at=%s",
                            page_id,
                            _yt_vid_id,
                            _yt_url,
                            _yt_pa.strftime("%Y-%m-%d %H:%M UTC") if _yt_pa else "immediate",
                        )
                except YouTubeQuotaExceededError as _yt_quota_exc:
                    _LOG.warning(
                        "[YouTube] Daily upload limit (20 videos) reached for this channel."
                    )
                    _remaining_rows = _yt_rows[_i:]
                    print(
                        "[YouTube] Daily upload limit (20 videos) reached for this channel. "
                        f"Queuing remaining {len(_remaining_rows)} video(s) → "
                        "credentials/pending_youtube_uploads.json"
                    )
                    for _r_offset, _pending_row in enumerate(_remaining_rows):
                        _pending_i = _i + _r_offset
                        _pending_extra_min = (
                            random.randint(0, _rand_max_m) if _rand_max_m > 0 else 0
                        )
                        _pending_publish_at = _sched_anchor + timedelta(
                            hours=_interval_h * (_pending_i + 1),
                            minutes=_pending_extra_min,
                        )
                        _yt_queue_pending_upload(
                            row=_pending_row,
                            page_name=page_id,
                            privacy_status="private",
                            publish_at=_pending_publish_at,
                            reason="daily_upload_limit_exceeded",
                        )
                    _LOG.info(
                        "YT quota reached | page=%s queued=%d | %s",
                        page_id, len(_remaining_rows), _yt_quota_exc,
                    )
                    # Stop attempting further uploads this run — the pipeline
                    # completes normally; queued videos resume via
                    # --resume-youtube-queue once the daily cap rolls over.
                    break
                except Exception as _yt_exc:  # noqa: BLE001
                    _LOG.error(
                        "YouTube upload failed for '%s': %s",
                        _yt_row.get("video_path", "?"),
                        _yt_exc,
                        exc_info=True,
                    )
                    print(
                        f"[YouTube] Upload FAILED for "
                        f"{Path(_yt_row.get('video_path', '?')).name}: "
                        f"{type(_yt_exc).__name__}: {_yt_exc}"
                    )


if __name__ == "__main__":
    cli()

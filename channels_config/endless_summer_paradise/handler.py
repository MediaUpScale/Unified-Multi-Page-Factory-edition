# -*- coding: utf-8 -*-
"""
Endless Summer Paradise — YouTube library-ingest handler.

Responsibilities
----------------
1. Scan ``SOURCE_DIRECTORY`` for master videos (prefer ``*_ULTIMATE_MASTER.mp4``).
2. Keep only files with runtime **strictly greater than** ``MIN_DURATION_SECONDS`` (40).
3. Map each kept file against ``global_video_library.json`` (filename / stem key).
4. Videos > 40s with no library match → ``needs_metadata`` staging queue.
5. Build tropical SEO title / description / tags packs for schedule-ready rows.

Does not upload by itself — produces manifests consumed by the YouTube
publisher / ``esp_main.py schedule`` path.
"""
from __future__ import annotations

import os
import json
import logging
import re
import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

_LOG = logging.getLogger(__name__)

_CHANNEL_DIR = Path(__file__).resolve().parent
_ENGINE_ROOT = _CHANNEL_DIR.parents[1]
_CHANNEL_ID = "endless_summer_paradise"
_ENV_OUTPUT = os.getenv("OUTPUT_PATH")
_OUTPUTS = Path(_ENV_OUTPUT) / _CHANNEL_ID if _ENV_OUTPUT else _ENGINE_ROOT / "outputs" / _CHANNEL_ID
_NEEDS_METADATA_DIR = _OUTPUTS / "needs_metadata"
_SCAN_PATH = _OUTPUTS / "esp_asset_map.json"
_QUEUE_PATH = _OUTPUTS / "esp_schedule_queue.json"
_SCHEDULE_STATE_PATH = _OUTPUTS / "esp_schedule_state.json"
_NEEDS_META_INDEX = _OUTPUTS / "needs_metadata" / "index.json"

_VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".m4v", ".webm"}
_SKIP_DIR_NAMES = {"processed", "tmp", "temp", ".tmp", "needs_metadata", "__pycache__"}
_SKIP_NAME_TOKENS = (
    "_VIDEO",
    "_BAKED",
    "_soundscape",
    "_sfx",
    "_music",
    "_preview",
    "_thumb",
)
_META_SKIP_KEYS = {"__caption_engine_state"}
_YT_TITLE_MAX = 100


@dataclass
class VideoAsset:
    filename: str
    path: str
    duration_s: float
    library_key: str = ""
    has_library_entry: bool = False
    title: str = ""
    description: str = ""
    tags: list[str] = field(default_factory=list)
    pin_title: str = ""
    caption: str = ""
    hashtags: str = ""
    b2_url: str = ""
    status: str = "ready"  # ready | needs_metadata | skipped_short
    skip_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ScanReport:
    source_dir: str
    library_path: str
    min_duration_s: float
    scanned: int = 0
    ready: list[VideoAsset] = field(default_factory=list)
    needs_metadata: list[VideoAsset] = field(default_factory=list)
    skipped_short: list[VideoAsset] = field(default_factory=list)
    skipped_other: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_dir": self.source_dir,
            "library_path": self.library_path,
            "min_duration_s": self.min_duration_s,
            "scanned": self.scanned,
            "ready": [a.to_dict() for a in self.ready],
            "needs_metadata": [a.to_dict() for a in self.needs_metadata],
            "skipped_short": [a.to_dict() for a in self.skipped_short],
            "skipped_other": self.skipped_other,
            "counts": {
                "ready": len(self.ready),
                "needs_metadata": len(self.needs_metadata),
                "skipped_short": len(self.skipped_short),
                "skipped_other": len(self.skipped_other),
            },
        }


def _load_page_config() -> Any:
    import importlib.util

    path = _CHANNEL_DIR / "page_config.py"
    spec = importlib.util.spec_from_file_location("esp_page_config", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load page_config at {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_duration(video_path: str | Path) -> float:
    """Return duration in seconds. Tries moviepy, then OpenCV, then ffprobe."""
    path = str(video_path)
    try:
        from moviepy import VideoFileClip

        with VideoFileClip(path) as clip:
            return float(clip.duration or 0.0)
    except Exception:
        pass
    try:
        from moviepy.editor import VideoFileClip  # type: ignore

        with VideoFileClip(path) as clip:
            return float(clip.duration or 0.0)
    except Exception:
        pass
    try:
        import cv2

        cap = cv2.VideoCapture(path)
        fps = cap.get(cv2.CAP_PROP_FPS) or 1.0
        frames = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0.0
        cap.release()
        if fps > 0 and frames > 0:
            return float(frames) / float(fps)
    except Exception:
        pass
    try:
        import subprocess

        proc = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                path,
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return float(proc.stdout.strip())
    except Exception:
        pass
    _LOG.warning("Cannot read duration for %s — treating as 0 (will skip).", Path(path).name)
    return 0.0


def load_global_library(library_path: Optional[str | Path] = None) -> dict[str, Any]:
    cfg = _load_page_config()
    path = Path(library_path or getattr(cfg, "GLOBAL_VIDEO_LIBRARY_PATH", ""))
    if not path.is_file():
        _LOG.warning("global_video_library.json missing at %s", path)
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        _LOG.warning("Failed to parse library %s (%s)", path, exc)
        return {}
    return data if isinstance(data, dict) else {}


def _library_lookup(library: dict[str, Any], filename: str) -> tuple[str, dict[str, Any]]:
    """Match by exact filename, stem, or basename key."""
    if not library:
        return "", {}
    if filename in library and isinstance(library[filename], dict):
        return filename, library[filename]
    stem = Path(filename).stem
    for key, val in library.items():
        if key in _META_SKIP_KEYS or not isinstance(val, dict):
            continue
        if key == stem or Path(str(key)).name == filename or Path(str(key)).stem == stem:
            return str(key), val
        local = str(val.get("local_path") or val.get("filename") or "")
        if local and (Path(local).name == filename or Path(local).stem == stem):
            return str(key), val
    return "", {}


def to_us_english(text: str) -> str:
    """Force US English spelling standards (color, center, visualizing, …)."""
    out = text or ""
    replacements = (
        ("colour", "color"),
        ("Colour", "Color"),
        ("COLOUR", "COLOR"),
        ("centre", "center"),
        ("Centre", "Center"),
        ("visualise", "visualize"),
        ("Visualise", "Visualize"),
        ("visualising", "visualizing"),
        ("Visualising", "Visualizing"),
        ("visualisation", "visualization"),
        ("Visualisation", "Visualization"),
        ("organise", "organize"),
        ("Organise", "Organize"),
        ("behaviour", "behavior"),
        ("Behaviour", "Behavior"),
        ("favourite", "favorite"),
        ("Favourite", "Favorite"),
        ("honour", "honor"),
        ("Honour", "Honor"),
        ("theatre", "theater"),
        ("Theatre", "Theater"),
    )
    for old, new in replacements:
        out = out.replace(old, new)
    return out


def _humanize_stem(stem: str) -> str:
    clean = stem or ""
    clean = re.sub(r"(?i)_ULTIMATE_MASTER$", "", clean)
    clean = re.sub(r"(?i)_V\d+_LIVE", "", clean)
    clean = re.sub(r"_\d{8,}.*$", "", clean)
    clean = re.sub(r"\d{8,}", "", clean)
    clean = clean.replace("_", " ").replace("-", " ")
    clean = re.sub(r"\s+", " ", clean).strip(" .|_")
    return to_us_english(clean.title() if clean else "Retro Dreamscape Escape")


def _strip_emoji_and_icons(text: str) -> str:
    return re.sub(
        r"[\U0001F300-\U0001FAFF\U00002700-\U000027BF\u2600-\u26FF"
        r"⚠⚠️✅❌⭐🌟✨💫🔥💯▶►◆◇•·]+",
        "",
        text or "",
    )


def _extract_hook_from_sources(
    *,
    filename: str,
    pin_title: str,
    caption: str,
) -> str:
    """Prefer a clean evocative hook from caption / stem; avoid raw master names."""
    stem_hook = _humanize_stem(Path(filename).stem)

    # Caption first line often starts with the episode name.
    if caption:
        first = to_us_english(caption.splitlines()[0].strip())
        first = re.sub(r"\s*[—–-]\s*.*$", "", first).strip(" .")
        # If caption opens with the episode name phrase, use stem_hook.
        if stem_hook and stem_hook.lower() in first.lower():
            return stem_hook
        # Short observational openers → keep stem as the title hook.
        if len(first) > 70 or first.lower().startswith(
            ("something about", "there's a", "there is a", "nobody", "the physics",
             "the silence inside", "anna")
        ):
            return stem_hook
        if "anna" in first.lower() or "protocol" in first.lower():
            return stem_hook
        if 8 <= len(first) <= 48 and not re.search(r"\d{6,}", first):
            return first.title() if first.islower() else first

    if pin_title:
        pin = to_us_english(_strip_emoji_and_icons(pin_title))
        # Strip generic library pin templates to recover nothing useful → stem.
        if re.search(r"1950s\s+Surreal\s+Jell-?O\s+Waterpark", pin, re.I):
            return stem_hook
        if re.search(r"ULTIMATE\s*MASTER|\bV\d+\s*LIVE\b|\d{8,}", pin, re.I):
            return stem_hook
        pin = re.sub(r"\s*[|—–-].*$", "", pin).strip()
        if 4 <= len(pin) <= 48:
            return pin

    return stem_hook or "Retro Dreamscape Escape"


def _pick_subtheme(hook: str, page_cfg: Any) -> str:
    """Always brand with the channel name for high-CTR US title consistency."""
    del hook  # sub-themes reserved; brand wins for RPM title template
    return str(getattr(page_cfg, "YOUTUBE_TITLE_BRAND", "Endless Summer Paradise"))


def format_esp_title(
    *,
    filename: str,
    library_entry: dict[str, Any],
    page_cfg: Any,
) -> str:
    """
    High-CTR US title standard:

        [Evocative Visual Hook] — 1950s Surreal Jell-O Waterpark | [Sub-theme/Brand]
    """
    anchor = str(
        getattr(page_cfg, "YOUTUBE_TITLE_CORE_ANCHOR", "1950s Surreal Jell-O Waterpark")
    )
    pin_title = str(library_entry.get("pin_title") or "").strip()
    caption = str(library_entry.get("caption") or "").strip()
    hook = _extract_hook_from_sources(
        filename=filename, pin_title=pin_title, caption=caption
    )
    hook = to_us_english(_strip_emoji_and_icons(hook)).strip(" —-|")
    tail = _pick_subtheme(hook, page_cfg)

    # Fit within 100 chars: shrink hook first, then fall back to brand-only tail.
    def _compose(h: str, t: str) -> str:
        return f"{h} — {anchor} | {t}"

    title = _compose(hook, tail)
    if len(title) > _YT_TITLE_MAX:
        brand = str(getattr(page_cfg, "YOUTUBE_TITLE_BRAND", "Endless Summer Paradise"))
        budget = _YT_TITLE_MAX - len(f" — {anchor} | {brand}")
        hook_trim = hook[: max(12, budget)].rstrip(" —-|")
        title = _compose(hook_trim, brand)
    if len(title) > _YT_TITLE_MAX:
        title = title[:_YT_TITLE_MAX].rstrip(" —-|")
    return to_us_english(title)


def _extract_hashtag_tags(hashtags: str, defaults: list[str]) -> list[str]:
    found = re.findall(r"#([A-Za-z0-9_]+)", hashtags or "")
    tags: list[str] = []
    for raw in found + list(defaults):
        t = str(raw).strip().lstrip("#")
        if not t:
            continue
        # Keep common acronyms intact before camelCase splits.
        t_norm = re.sub(r"(?i)^AI(?=[A-Z])", "AI ", t)
        t_norm = re.sub(r"(?i)^4K(?=[A-Z])", "4K ", t_norm)
        spaced = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", t_norm)
        spaced = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", spaced)
        spaced = spaced.replace("_", " ").strip().lower()
        spaced = re.sub(r"\s+", " ", spaced)
        spaced = re.sub(r"\ba i\b", "ai", spaced)
        candidate = spaced or t.lower()
        if candidate and candidate not in tags:
            tags.append(candidate)
    return tags[:30]


def ensure_disclaimer(description: str, disclaimer_line: str) -> str:
    """Append a single plain-text disclaimer at the bottom (no emojis/icons)."""
    body = to_us_english(_strip_emoji_and_icons(description or "")).rstrip()
    line = to_us_english(_strip_emoji_and_icons(disclaimer_line or "")).strip()
    if not line:
        return body
    if line.lower() in body.lower():
        # Ensure disclaimer is the final line only once.
        body_wo = re.sub(
            re.escape(line), "", body, flags=re.IGNORECASE
        ).rstrip()
        return f"{body_wo}\n\n{line}".strip()
    return f"{body}\n\n{line}".strip()


def format_esp_description(
    *,
    filename: str,
    library_entry: dict[str, Any],
    page_cfg: Any,
    title: str = "",
) -> str:
    """
    Description architecture for search SEO + ad placement:

    1. Top 3 lines (above the fold) — visual + high-value US keywords
    2. Middle — dense 2-3 paragraph concept summary
    3. Bottom — playlist, subscribe CTA, hashtags, plain-text disclaimer
    """
    caption = to_us_english(str(library_entry.get("caption") or "").strip())
    full_text = to_us_english(str(library_entry.get("full_text") or "").strip())
    hashtags = str(library_entry.get("hashtags") or "").strip()
    disclaimer = str(getattr(page_cfg, "DISCLAIMER_LINE", "") or "")
    playlist = str(getattr(page_cfg, "YOUTUBE_PLAYLIST_TITLE", "") or "")
    hook_tmpl = str(getattr(page_cfg, "YOUTUBE_DESCRIPTION_HOOK_TEMPLATE", "") or "")
    context = str(getattr(page_cfg, "YOUTUBE_DESCRIPTION_CONTEXT", "") or "")
    cta_tmpl = str(getattr(page_cfg, "YOUTUBE_DESCRIPTION_CTA", "") or "")
    hash_line = str(getattr(page_cfg, "YOUTUBE_HASHTAG_LINE", "") or "")

    hook_name = _extract_hook_from_sources(
        filename=filename,
        pin_title=str(library_entry.get("pin_title") or ""),
        caption=caption,
    )
    if caption:
        hook_line = caption.splitlines()[0].strip()
        if len(hook_line) < 12:
            hook_line = (
                f"{hook_name} unfolds as 1950s mid-century architecture "
                f"rendered in vintage aesthetic 4K visual art."
            )
    else:
        hook_line = (
            f"{hook_name} unfolds as 1950s mid-century architecture "
            f"rendered in vintage aesthetic 4K visual art."
        )
    hook_line = to_us_english(_strip_emoji_and_icons(hook_line))

    if hook_tmpl:
        top = hook_tmpl.format(hook_line=hook_line, title=title or hook_name)
    else:
        top = (
            f"{hook_line}\n"
            "A cinematic study of 1950s mid-century architecture, vintage aesthetic "
            "design, and retro dreamscape atmosphere rendered as 4K visual art.\n"
            "Explore translucent Jell-O waterpark geometry, mid-century modern color, "
            "and cinematic surrealism built for visual relaxation."
        )

    # Optional sensory bridge from library caption (lines 2–3) without duplicating top.
    caption_extra = ""
    if caption:
        lines = [ln.strip() for ln in caption.splitlines() if ln.strip()]
        extra_lines = [ln for ln in lines[1:3] if ln.lower() not in top.lower()]
        if extra_lines:
            caption_extra = "\n".join(extra_lines)

    mid_parts = [context] if context else []
    if full_text and not caption:
        body = full_text.split("\n\n#")[0].strip()
        if body and body.lower() not in (context or "").lower():
            mid_parts.insert(0, body)

    if cta_tmpl:
        bottom_cta = cta_tmpl.format(playlist=playlist or "Endless Summer Paradise")
    else:
        bottom_cta = (
            "Subscribe to Endless Summer Paradise for more 1950s surreal architecture "
            "and vintage aesthetic 4K visual art.\n\n"
            f"Playlist: {playlist}"
        )

    sections = [top.strip()]
    if caption_extra:
        sections.append(caption_extra)
    if mid_parts:
        sections.append("\n\n".join(p for p in mid_parts if p).strip())
    sections.append(bottom_cta.strip())
    if hash_line:
        sections.append(hash_line.strip())
    elif hashtags:
        sections.append(hashtags)

    description = "\n\n".join(s for s in sections if s)
    return ensure_disclaimer(description, disclaimer)


def build_seo_pack(
    *,
    filename: str,
    library_entry: dict[str, Any],
    page_cfg: Any,
) -> dict[str, Any]:
    """Map library assets → US high-RPM YouTube title / description / tags."""
    from agents.posting.youtube_publisher import sanitize_youtube_tags

    defaults = list(getattr(page_cfg, "YOUTUBE_DEFAULT_TAGS", []) or [])
    anchors = list(getattr(page_cfg, "YOUTUBE_KEYWORD_ANCHORS", []) or [])
    hashtags = str(library_entry.get("hashtags") or "").strip()

    title = format_esp_title(
        filename=filename, library_entry=library_entry, page_cfg=page_cfg
    )
    description = format_esp_description(
        filename=filename,
        library_entry=library_entry,
        page_cfg=page_cfg,
        title=title,
    )
    tags = sanitize_youtube_tags(_extract_hashtag_tags(hashtags, defaults + anchors))

    return {
        "title": title,
        "description": description,
        "tags": tags,
        "pin_title": str(library_entry.get("pin_title") or "").strip(),
        "caption": str(library_entry.get("caption") or "").strip(),
        "hashtags": hashtags,
        "category_id": str(getattr(page_cfg, "YOUTUBE_CATEGORY_ID", "24")),
        "default_language": str(
            getattr(page_cfg, "YOUTUBE_DEFAULT_LANGUAGE", "en-US")
        ),
        "default_audio_language": str(
            getattr(page_cfg, "YOUTUBE_DEFAULT_AUDIO_LANGUAGE", "en-US")
        ),
    }


def _iter_candidate_videos(source_dir: Path, master_suffix: str) -> Iterable[Path]:
    if not source_dir.is_dir():
        return []
    masters: list[Path] = []
    fallback: list[Path] = []
    for path in source_dir.rglob("*"):
        if not path.is_file():
            continue
        if any(part.lower() in _SKIP_DIR_NAMES for part in path.parts):
            continue
        if path.suffix.lower() not in _VIDEO_EXTS:
            continue
        name = path.name
        if any(tok.lower() in name.lower() for tok in _SKIP_NAME_TOKENS):
            continue
        if name.endswith(master_suffix) or name.upper().endswith(
            master_suffix.upper()
        ):
            masters.append(path)
        else:
            # Prefer episode subfolder masters; skip root-level clutter.
            if path.parent.resolve() != source_dir.resolve():
                fallback.append(path)
    return masters if masters else sorted(fallback)


def stage_needs_metadata(asset: VideoAsset, staging_dir: Path) -> Path:
    """Copy (or hardlink-fallback copy) a file into the needs_metadata queue."""
    staging_dir.mkdir(parents=True, exist_ok=True)
    dest = staging_dir / asset.filename
    src = Path(asset.path)
    if dest.resolve() == src.resolve():
        return dest
    if dest.exists():
        return dest
    try:
        shutil.copy2(src, dest)
    except OSError as exc:
        _LOG.warning("Could not stage %s → %s (%s)", src, dest, exc)
        # Still record the index entry with source path.
    return dest


def _write_json(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def scan_and_ingest(
    *,
    source_dir: Optional[str | Path] = None,
    library_path: Optional[str | Path] = None,
    min_duration_s: Optional[float] = None,
    stage_missing: bool = True,
    persist: bool = True,
) -> ScanReport:
    """
    Scan production folder, apply duration > 40 gate, map library metadata.

    Returns a ``ScanReport`` with ready / needs_metadata / skipped_short buckets.
    """
    cfg = _load_page_config()
    root = Path(source_dir or getattr(cfg, "SOURCE_DIRECTORY"))
    lib_path = Path(library_path or getattr(cfg, "GLOBAL_VIDEO_LIBRARY_PATH"))
    min_dur = float(
        min_duration_s
        if min_duration_s is not None
        else getattr(cfg, "MIN_DURATION_SECONDS", 40.0)
    )
    master_suffix = str(getattr(cfg, "MASTER_FILENAME_SUFFIX", "_ULTIMATE_MASTER.mp4"))

    library = load_global_library(lib_path)
    report = ScanReport(
        source_dir=str(root),
        library_path=str(lib_path),
        min_duration_s=min_dur,
    )

    if not root.is_dir():
        report.skipped_other.append(f"SOURCE_DIR_MISSING: {root}")
        if persist:
            _write_json(_SCAN_PATH, report.to_dict())
        return report

    candidates = list(_iter_candidate_videos(root, master_suffix))
    report.scanned = len(candidates)

    for path in candidates:
        size = path.stat().st_size
        if size <= 0:
            report.skipped_other.append(f"EMPTY: {path}")
            continue

        # Prefer library duration when the same filename is already indexed;
        # probe disk only when the entry is missing or duration is unset.
        key_pre, entry_pre = _library_lookup(library, path.name)
        duration = 0.0
        if entry_pre and entry_pre.get("duration_s") is not None:
            try:
                duration = float(entry_pre.get("duration_s") or 0.0)
            except (TypeError, ValueError):
                duration = 0.0
        if duration <= 0:
            duration = get_duration(path)

        # Strictly greater than 40 — skip 40.0 and below.
        if duration <= min_dur:
            report.skipped_short.append(
                VideoAsset(
                    filename=path.name,
                    path=str(path),
                    duration_s=round(duration, 2),
                    status="skipped_short",
                    skip_reason=f"duration {duration:.2f}s <= {min_dur:g}s",
                )
            )
            continue

        key, entry = key_pre, entry_pre
        if not entry:
            key, entry = _library_lookup(library, path.name)
        asset = VideoAsset(
            filename=path.name,
            path=str(path),
            duration_s=round(duration, 2),
            library_key=key,
            has_library_entry=bool(entry),
            b2_url=str(entry.get("b2_url") or "") if entry else "",
        )

        if not entry:
            asset.status = "needs_metadata"
            asset.skip_reason = "no matching global_video_library.json entry"
            if stage_missing:
                staged = stage_needs_metadata(asset, _NEEDS_METADATA_DIR)
                asset.path = str(staged)
            report.needs_metadata.append(asset)
            continue

        pack = build_seo_pack(filename=path.name, library_entry=entry, page_cfg=cfg)
        asset.title = pack["title"]
        asset.description = pack["description"]
        asset.tags = list(pack["tags"])
        asset.pin_title = pack["pin_title"]
        asset.caption = pack["caption"]
        asset.hashtags = pack["hashtags"]
        asset.status = "ready"
        report.ready.append(asset)

    if persist:
        _write_json(_SCAN_PATH, {"updated_at": _utc_now(), **report.to_dict()})
        _write_json(
            _NEEDS_META_INDEX,
            {
                "updated_at": _utc_now(),
                "count": len(report.needs_metadata),
                "items": [a.to_dict() for a in report.needs_metadata],
            },
        )
        _write_json(
            _QUEUE_PATH,
            {
                "channel_id": _CHANNEL_ID,
                "updated_at": _utc_now(),
                "min_duration_s": min_dur,
                "ready_count": len(report.ready),
                "items": [
                    {
                        "video_path": a.path,
                        "filename": a.filename,
                        "duration_s": a.duration_s,
                        "title": a.title,
                        "description": a.description,
                        "tags": a.tags,
                        "youtube_category_id": str(
                            getattr(cfg, "YOUTUBE_CATEGORY_ID", "1")
                        ),
                        "default_language": str(
                            getattr(cfg, "YOUTUBE_DEFAULT_LANGUAGE", "en-US")
                        ),
                        "default_audio_language": str(
                            getattr(cfg, "YOUTUBE_DEFAULT_AUDIO_LANGUAGE", "en-US")
                        ),
                        "playlist_title": str(
                            getattr(cfg, "YOUTUBE_PLAYLIST_TITLE", "")
                        ),
                        "page": _CHANNEL_ID,
                    }
                    for a in report.ready
                ],
            },
        )

    return report


def print_scan_report(report: ScanReport) -> None:
    print(f"[ESP] Source : {report.source_dir}")
    print(f"[ESP] Library: {report.library_path}")
    print(f"[ESP] Gate   : duration > {report.min_duration_s:g}s")
    print(f"[ESP] Scanned: {report.scanned}")
    print(f"[ESP] Ready  : {len(report.ready)}")
    print(f"[ESP] Needs metadata: {len(report.needs_metadata)}")
    print(f"[ESP] Skipped (<= {report.min_duration_s:g}s): {len(report.skipped_short)}")
    for a in report.ready[:12]:
        print(f"  OK {a.duration_s:6.1f}s  {a.filename}")
        print(f"      title: {a.title[:90]}")
    if len(report.ready) > 12:
        print(f"  ... +{len(report.ready) - 12} more ready")
    for a in report.needs_metadata[:8]:
        print(f"  ?? {a.duration_s:6.1f}s  {a.filename} -> needs_metadata")
    for a in report.skipped_short[:8]:
        print(f"  -- {a.duration_s:6.1f}s  {a.filename} ({a.skip_reason})")


def load_schedule_queue() -> dict[str, Any]:
    if not _QUEUE_PATH.is_file():
        return {"channel_id": _CHANNEL_ID, "items": []}
    try:
        data = json.loads(_QUEUE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"channel_id": _CHANNEL_ID, "items": []}
    return data if isinstance(data, dict) else {"channel_id": _CHANNEL_ID, "items": []}


def _parse_iso_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def load_schedule_state() -> dict[str, Any]:
    if not _SCHEDULE_STATE_PATH.is_file():
        return {"channel_id": _CHANNEL_ID, "last_publish_at": None}
    try:
        data = json.loads(_SCHEDULE_STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"channel_id": _CHANNEL_ID, "last_publish_at": None}
    return data if isinstance(data, dict) else {"channel_id": _CHANNEL_ID, "last_publish_at": None}


def save_last_publish_at(publish_at: datetime, *, dry_run: bool = False) -> None:
    """Persist the latest assigned slot so sequential ``--limit 1`` runs stagger."""
    if publish_at.tzinfo is None:
        publish_at = publish_at.replace(tzinfo=timezone.utc)
    payload = {
        "channel_id": _CHANNEL_ID,
        "updated_at": _utc_now(),
        "last_publish_at": publish_at.astimezone(timezone.utc).isoformat(),
        "dry_run": bool(dry_run),
    }
    _write_json(_SCHEDULE_STATE_PATH, payload)


def resolve_next_publish_slot(
    youtube,
    *,
    interval: timedelta,
) -> datetime:
    """Pick the next slot from max(YouTube future publishAt, local state) + interval."""
    from agents.posting.youtube_publisher import get_next_publish_slot

    yt_slot = get_next_publish_slot(
        youtube,
        interval=interval,
        initial_offset=interval,
    )
    local_last = _parse_iso_dt(load_schedule_state().get("last_publish_at"))
    if local_last is None:
        return yt_slot
    local_next = (local_last + interval).replace(second=0, microsecond=0)
    chosen = max(yt_slot, local_next)
    if chosen > yt_slot:
        print(
            f"[ESP] Local schedule state advances slot past YouTube max: "
            f"{yt_slot.strftime('%Y-%m-%d %H:%M')} -> "
            f"{chosen.strftime('%Y-%m-%d %H:%M')} UTC"
        )
    return chosen


def schedule_ready_uploads(
    *,
    interval_hours: float | str | None = 84.0,
    dry_run: bool = True,
    limit: Optional[int] = None,
) -> dict[str, Any]:
    """
    Upload ready queue items via youtube_publisher (private + publishAt).

    Slot algorithm (survives sequential ``--limit 1`` runs):
      1. Query channel for max future ``publishAt`` (+ local state fallback)
      2. ``next = max_existing + interval`` (default interval = 84h)
      3. After each successful assignment, advance the local cursor by ``interval``

    Successful uploads are removed from ``esp_schedule_queue.json``; remaining
    items stay untouched for the next run. Stops after ``MAX_DAILY_UPLOADS``
    (default 20) successes unless ``limit`` overrides.

    Requires ``credentials/tokens/youtube_token_endless_summer_paradise.json``.
    """
    import config as app_config
    from agents.posting.youtube_publisher import (
        DailyUploadSafetyGate,
        YouTubeQuotaExceededError,
        advance_slot,
        build_credentials,
        build_youtube_client,
        parse_interval_spec,
        resolve_youtube_token_path,
        sanitize_youtube_tags,
        upload_short,
        verify_authorized_channel,
    )

    interval = parse_interval_spec(interval_hours, default_hours=84.0)
    interval_h = interval.total_seconds() / 3600.0
    gate = DailyUploadSafetyGate(limit=limit)
    queue = load_schedule_queue()
    items = list(queue.get("items") or [])
    cfg = _load_page_config()
    library = load_global_library()

    result: dict[str, Any] = {
        "channel_id": _CHANNEL_ID,
        "dry_run": dry_run,
        "attempted": 0,
        "uploaded": [],
        "errors": [],
        "deferred": [],
        "limit": gate.limit,
        "safety_cap_hit": False,
        "interval": str(interval),
        "interval_hours": interval_h,
    }
    if not items:
        print("[ESP] Schedule queue empty — run scan first.")
        return result

    token = resolve_youtube_token_path(_CHANNEL_ID, app_config.YOUTUBE_TOKEN_DIR)
    print(f"[ESP] Token -> {token}")
    print(
        f"[ESP] Scheduling {len(items)} video(s) | interval={interval} "
        f"({interval_h:g}h) | safety cap={gate.limit} | dry_run={dry_run}"
    )

    creds = build_credentials(
        _CHANNEL_ID,
        app_config.YOUTUBE_CLIENT_SECRETS,
        app_config.YOUTUBE_TOKEN_DIR,
    )
    youtube = build_youtube_client(creds)
    verify_authorized_channel(youtube, page_name=_CHANNEL_ID)

    # Dynamic slot: max(YouTube future publishAt, local state) + interval.
    next_slot = resolve_next_publish_slot(youtube, interval=interval)
    print(
        f"[ESP] First publish slot -> {next_slot.strftime('%Y-%m-%d %H:%M')} UTC "
        f"(from max existing publishAt + interval)"
    )

    remaining_items = list(items)
    uploaded_paths: set[str] = set()
    for i, row in enumerate(items):
        if not gate.can_upload():
            result["safety_cap_hit"] = True
            gate.notify_halt()
            result["deferred"] = [
                {
                    "video_path": str(r.get("video_path") or ""),
                    "title": str(r.get("title") or ""),
                }
                for r in remaining_items
            ]
            break

        result["attempted"] += 1
        vpath = Path(str(row.get("video_path") or ""))
        # Refresh high-RPM metadata from page_config + library when possible so
        # stale tropical / dirty tag payloads from older scans cannot poison uploads.
        _key, entry = _library_lookup(library, vpath.name)
        if entry:
            pack = build_seo_pack(
                filename=vpath.name, library_entry=entry, page_cfg=cfg
            )
            title = str(pack.get("title") or row.get("title") or vpath.stem)[:_YT_TITLE_MAX]
            description = str(pack.get("description") or row.get("description") or "")
            tags = sanitize_youtube_tags(pack.get("tags") or row.get("tags") or [])
            category_id = str(
                pack.get("category_id")
                or row.get("youtube_category_id")
                or getattr(cfg, "YOUTUBE_CATEGORY_ID", "24")
            )
            default_language = str(
                pack.get("default_language")
                or row.get("default_language")
                or "en-US"
            )
            default_audio_language = str(
                pack.get("default_audio_language")
                or row.get("default_audio_language")
                or "en-US"
            )
        else:
            title = str(row.get("title") or vpath.stem)[:_YT_TITLE_MAX]
            description = str(row.get("description") or "")
            tags = sanitize_youtube_tags(row.get("tags") or [])
            category_id = str(
                row.get("youtube_category_id")
                or getattr(cfg, "YOUTUBE_CATEGORY_ID", "24")
            )
            default_language = str(row.get("default_language") or "en-US")
            default_audio_language = str(
                row.get("default_audio_language") or "en-US"
            )
        # Persist cleaned tags back onto the in-memory row for queue rewrite.
        row["title"] = title
        row["description"] = description
        row["tags"] = tags
        row["youtube_category_id"] = category_id
        row["default_language"] = default_language
        row["default_audio_language"] = default_audio_language
        row["playlist_title"] = str(
            getattr(cfg, "YOUTUBE_PLAYLIST_TITLE", "") or row.get("playlist_title") or ""
        )
        publish_at = next_slot
        print(
            f"[ESP] [{i + 1}/{len(items)}] {vpath.name} -> "
            f"{publish_at.strftime('%Y-%m-%d %H:%M')} UTC | {title[:70]}"
        )
        if dry_run:
            result["uploaded"].append(
                {
                    "video_path": str(vpath),
                    "title": title,
                    "publish_at": publish_at.isoformat(),
                    "dry_run": True,
                    "tags": tags,
                }
            )
            gate.record_success()
            uploaded_paths.add(str(vpath))
            remaining_items = [
                r for r in remaining_items
                if str(r.get("video_path") or "") != str(vpath)
            ]
            save_last_publish_at(publish_at, dry_run=True)
            next_slot = advance_slot(publish_at, interval=interval)
            continue
        if not vpath.is_file():
            result["errors"].append({"video_path": str(vpath), "error": "missing file"})
            continue
        try:
            vid_id, url, pa = upload_short(
                video_path=str(vpath),
                title=title,
                description=description,
                tags=tags,
                privacy_status="private",
                publish_at=publish_at,
                category_id=category_id,
                page_name=_CHANNEL_ID,
                playlist_title=str(row.get("playlist_title") or "") or None,
                youtube=youtube,
                preserve_title=True,
                default_language=default_language,
                default_audio_language=default_audio_language,
            )
            used_slot = pa or publish_at
            result["uploaded"].append(
                {
                    "video_path": str(vpath),
                    "title": title,
                    "youtube_video_id": vid_id,
                    "youtube_url": url,
                    "publish_at": used_slot.isoformat() if used_slot else publish_at.isoformat(),
                }
            )
            gate.record_success()
            uploaded_paths.add(str(vpath))
            remaining_items = [
                r for r in remaining_items
                if str(r.get("video_path") or "") != str(vpath)
            ]
            save_last_publish_at(used_slot, dry_run=False)
            # Advance cursor from the slot we actually used so bulk runs stay spaced.
            next_slot = advance_slot(used_slot, interval=interval)
        except YouTubeQuotaExceededError as exc:
            result["safety_cap_hit"] = True
            gate.notify_halt()
            _LOG.warning("ESP YouTube quota exceeded: %s", exc)
            result["deferred"] = [
                {
                    "video_path": str(r.get("video_path") or ""),
                    "title": str(r.get("title") or ""),
                }
                for r in remaining_items
            ]
            break
        except Exception as exc:  # noqa: BLE001
            # Keep row in remaining_items so invalidTags / transient failures
            # can be retried on the next schedule run without losing the queue.
            err_txt = str(exc)
            _LOG.exception("ESP upload failed for %s", vpath)
            result["errors"].append({"video_path": str(vpath), "error": err_txt})
            if "invalidTags" in err_txt or "invalidVideoKeywords" in err_txt:
                print(
                    f"[ESP] invalidTags on {vpath.name} — tags sanitized for next retry; "
                    "row kept in queue."
                )
                row["tags"] = sanitize_youtube_tags(tags)
            # Do NOT advance next_slot on failure — retry keeps the same open slot.
    # State integrity: only remove successfully uploaded rows; leave the rest.
    if not dry_run or uploaded_paths:
        # Persist remaining queue for next run (dry-run keeps original queue).
        if not dry_run:
            _write_json(
                _QUEUE_PATH,
                {
                    "channel_id": _CHANNEL_ID,
                    "updated_at": _utc_now(),
                    "min_duration_s": queue.get("min_duration_s"),
                    "ready_count": len(remaining_items),
                    "items": remaining_items,
                },
            )
            print(
                f"[ESP] Queue updated | uploaded={len(result['uploaded'])} | "
                f"remaining={len(remaining_items)}"
            )

    _write_json(
        _OUTPUTS / "esp_schedule_result.json",
        {"updated_at": _utc_now(), **result},
    )
    return result


def reschedule_existing_conflicts(
    *,
    interval_hours: float | str | None = 84.0,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Scan YouTube for overlapping ``publishAt`` values and re-index with ``interval``.

    Temporary utility for cleaning an existing queue that landed on identical
    timestamps. Updates via ``youtube.videos().update(part='status', ...)``.
    """
    import config as app_config
    from agents.posting.youtube_publisher import (
        build_credentials,
        build_youtube_client,
        parse_interval_spec,
        reschedule_conflicting_publish_slots,
        verify_authorized_channel,
    )

    interval = parse_interval_spec(interval_hours, default_hours=84.0)
    creds = build_credentials(
        _CHANNEL_ID,
        app_config.YOUTUBE_CLIENT_SECRETS,
        app_config.YOUTUBE_TOKEN_DIR,
    )
    youtube = build_youtube_client(creds)
    verify_authorized_channel(youtube, page_name=_CHANNEL_ID)
    result = reschedule_conflicting_publish_slots(
        youtube,
        interval=interval,
        dry_run=dry_run,
        page_name=_CHANNEL_ID,
    )
    # Keep local cursor aligned with the last rewritten slot.
    updated = list(result.get("updated") or [])
    if updated and not dry_run:
        lasts = [
            _parse_iso_dt(row.get("new_publish_at"))
            for row in updated
            if not row.get("error")
        ]
        lasts = [dt for dt in lasts if dt is not None]
        if lasts:
            save_last_publish_at(max(lasts), dry_run=False)
    elif result.get("first_slot") and dry_run:
        # Preview only — do not mutate local state.
        pass
    _write_json(
        _OUTPUTS / "esp_reschedule_result.json",
        {"updated_at": _utc_now(), **result},
    )
    return result

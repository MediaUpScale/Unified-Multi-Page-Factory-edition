# -*- coding: utf-8 -*-
"""
pinterest_engine.inventory
---------------------------
Master Inventory -- the single source of truth for all posts.

Merges outputs/library/*.json (individual post records) and
outputs/content_library.json (legacy UUID-indexed library) into one
canonical file: outputs/master_inventory.json.

Every entry carries:
  - Original content fields (original_caption, raw_fact_sheet, imgbb_url ...)
  - Pinterest sales fields (pinterest_title, pinterest_caption, visual_hook, target_url)
  - Multi-platform publication_status object
  - Resolved local_image_path (workspace-relative path repair)
  - channel_id (with legacy page_id read fallback)

This module is imported by scheduler.py, publisher.py, and pinterest_main.py.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
MASTER_INVENTORY_FILENAME = "master_inventory.json"
SCHEMA_VERSION = "2.0"


def _target_url() -> str:
    from pinterest_engine import config as cfg  # noqa: PLC0415
    return cfg.TARGET_URL


def _default_topic() -> str:
    from pinterest_engine import config as cfg  # noqa: PLC0415
    return cfg.DEFAULT_TOPIC


_SOCIAL_CTA_RE = re.compile(
    r"(?:"
    r"[Cc]omment\s+[A-Z]+(?:\s+below)?(?:\s+and\s+I[^.!?]*)?"
    r"|[Tt]ype\s+[A-Z]+\s+(?:below|in\s+the\s+comments)[^.!?]*"
    r"|(?:[Ss]end\s+(?:me\s+)?a\s+(?:DM|message)|DM\s+me)[^.!?]*"
    r"|[Dd]rop\s+a\s+comment[^.!?]*"
    r")[.!?]?",
    re.MULTILINE,
)

# Matches system/researcher bracket tags that precede un-humanized drafts.
# e.g. "[Caption from researcher output - humanizer skipped]"
#      "[Researcher draft   humanizer unavailable]"
_RAW_DRAFT_TAG_RE = re.compile(
    r"^\s*\["
    r"(?:Caption from researcher|Researcher draft|humanizer|RAW FACT SHEET)[^\]]*"
    r"\]\s*$",
    re.IGNORECASE | re.MULTILINE,
)

# Markdown section headers used inside fact-sheet blocks:
# "**RAW FACT SHEET**", "**RAW FACT SHEET: Benefits of Celtic Salt**",
# "**1. Verified Mechanisms:**", etc.
_FACT_SHEET_HEADER_RE = re.compile(
    r"^\s*(?:"
    r"\*\*RAW FACT SHEET[^*]*\*\*:?"     # **RAW FACT SHEET** or **RAW FACT SHEET: Topic**
    r"|\*\*\d+\.\s+[^*]+\*\*:?"          # **1. Numbered Section Header:**
    r"|RAW FACT SHEET(?:[:\s].*)?"        # plain RAW FACT SHEET line (no stars)
    r")\s*$",
    re.IGNORECASE | re.MULTILINE,
)

_BANNED_WORDS = ["unlock", "discover", "elevate", "game-changer",
                 "harness", "revolutionary", "dive"]

def _channel_cta_variants() -> list[str]:
    """
    Channel-scoped CTA templates from config pack / env.

    Empty list means: do not inject engine CTAs — keep inventory metadata as-is.
    """
    from pinterest_engine import config as cfg  # noqa: PLC0415
    return list(cfg.CTA_VARIANTS or [])

# ---------------------------------------------------------------------------
# Publication status factory
# ---------------------------------------------------------------------------

def _empty_publication_status() -> dict:
    return {
        "posted_on_pinterest": False,
        "pinterest_pin_id": None,
        "pinterest_post_date": None,
        "posted_on_instagram": False,
        "instagram_post_id": None,
        "instagram_post_date": None,
        "posted_on_facebook": False,
        "facebook_post_id": None,
        "facebook_post_date": None,
    }


# ---------------------------------------------------------------------------
# Caption utilities (re-exported for use by other modules)
# ---------------------------------------------------------------------------

def has_social_cta(text: str) -> bool:
    """Return True if the text contains a social engagement CTA."""
    return bool(_SOCIAL_CTA_RE.search(text))


def is_raw_draft(text: str) -> bool:
    """
    Return True if the text is (or starts with) an un-humanized researcher draft.
    Catches patterns like:
      [Caption from researcher output - humanizer skipped]
      [Researcher draft   humanizer unavailable]
      **RAW FACT SHEET**
    """
    if not text:
        return False
    head = text.strip()[:300]
    return bool(_RAW_DRAFT_TAG_RE.search(head) or _FACT_SHEET_HEADER_RE.search(head))


def strip_draft_headers(text: str) -> str:
    """
    Strip system/researcher bracket tags and markdown fact-sheet wrappers.
    Preserves the underlying science content (bullet points, sentences).

    Examples of what gets removed:
      [Caption from researcher output - humanizer skipped]
      **RAW FACT SHEET**
      **1. Verified Mechanisms:**

    Returns cleaned text, or empty string if nothing useful remains.
    """
    if not text:
        return ""
    lines = text.splitlines()
    clean: list[str] = []
    for line in lines:
        stripped = line.strip()
        # Skip system bracket tags
        if _RAW_DRAFT_TAG_RE.match(line):
            continue
        # Skip standalone "RAW FACT SHEET" / numbered markdown headers
        if _FACT_SHEET_HEADER_RE.match(line):
            continue
        clean.append(line)

    result = "\n".join(clean)
    result = re.sub(r"\n{3,}", "\n\n", result).strip()
    return result


def clean_social_ctas(text: str) -> str:
    """Strip all social engagement CTA phrases from the text."""
    cleaned = _SOCIAL_CTA_RE.sub("", text)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def inject_sales_cta(text: str, variant_idx: int = 0) -> str:
    """
    Append a rotating channel CTA when the active channel defines variants.

    If the channel pack has no CTA templates, return *text* unchanged so
    inventory/post metadata remains the sole source of caption content.
    """
    from pinterest_engine import config as cfg  # noqa: PLC0415

    variants = _channel_cta_variants()
    if not variants:
        return text.rstrip()
    template = variants[variant_idx % len(variants)]
    cta = cfg.format_cta_variant(template, _target_url())
    if not cta.strip():
        return text.rstrip()
    return f"{text.rstrip()}\n\n{cta}"


def build_caption_regex(source: str, variant_idx: int = 0) -> str:
    """
    Fast regex-based caption transformation (no API call needed).

    Automatically strips raw researcher draft headers before processing,
    so this function is safe to call on any source regardless of whether
    the humanizer ran. CTAs/URLs come only from the active channel pack.
    """
    url = _target_url()
    variants = _channel_cta_variants()
    if variants:
        from pinterest_engine import config as cfg  # noqa: PLC0415
        fallback = cfg.format_cta_variant(variants[0], url).strip()
    elif url:
        fallback = url
    else:
        fallback = ""

    if not source or source.strip() in ("PENDING_CAPTION", "pending", ""):
        return fallback

    # Strip any researcher draft wrapper before cleaning CTAs
    if is_raw_draft(source):
        source = strip_draft_headers(source)
        if not source.strip():
            return fallback

    cleaned = clean_social_ctas(source)
    result = inject_sales_cta(cleaned, variant_idx)
    for word in _BANNED_WORDS:
        result = re.sub(rf"\b{re.escape(word)}\b", "", result, flags=re.IGNORECASE)
    return re.sub(r"  +", " ", result).strip()


def build_title_template(topic: str) -> str:
    """Generate a keyword-rich SEO title (max 100 chars) from the topic."""
    base = topic.strip().title()
    for sfx in [
        " | Practical Guide",
        " | Complete Overview",
        " Tips | Quick Reference",
        " — Key Insights",
    ]:
        if len(base + sfx) <= 100:
            return base + sfx
    return base[:100]


def build_visual_hook(topic: str) -> str:
    """Short 5-word ALL-CAPS hook for the image overlay."""
    return " ".join(topic.upper().split()[:5])


def validate_caption_safe(caption: str) -> tuple[bool, str]:
    """
    Check that the caption is Pinterest-safe for live publishing.
    Returns (is_valid: bool, reason: str).

    Fails on:
      - Social engagement CTAs ("Comment X", "DM me", etc.)
      - Missing target / sales URL (when TARGET_URL is configured)
      - Un-humanized researcher draft content (raw fact-sheet headers)
    """
    if is_raw_draft(caption):
        return False, "Contains un-humanized researcher draft header"
    if has_social_cta(caption):
        return False, "Contains social engagement CTA ('Comment', 'DM me', etc.)"
    url = _target_url()
    if url and url not in caption:
        return False, f"Missing target URL: {url}"
    return True, "OK"


# ---------------------------------------------------------------------------
# Image path resolution
# ---------------------------------------------------------------------------

def resolve_image_path(record: dict) -> str:
    """
    Try to find the actual image file for this record.
    Checks: local_image_path -> image_relative / image_path relative to
    the local workspace root (ENGINE_ROOT). Returns empty string if missing.
    """
    from pinterest_engine import config as cfg  # noqa: PLC0415

    # 1. Already resolved
    existing = record.get("local_image_path", "")
    if existing and Path(existing).is_file():
        return str(Path(existing).resolve())

    # 2. image_relative / image_path relative to local workspace root
    for rel_field in ("image_relative", "image_path"):
        rel = record.get(rel_field, "")
        if not rel:
            continue
        candidate = (cfg.ENGINE_ROOT / rel).resolve()
        if candidate.is_file():
            return str(candidate)
        # Also try relative to outputs/ (common inventory layout)
        out_cand = (cfg.OUTPUTS_DIR / rel).resolve()
        if out_cand.is_file():
            return str(out_cand)

    return ""


_VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".webm"}


def resolve_video_path(record: dict) -> str:
    """
    Resolve a local video assigned to this library record.

    Checks ``local_video_path`` / ``video_path``, then ``clips/`` matches
    by subject slug or basename.
    """
    from pinterest_engine import config as cfg  # noqa: PLC0415

    for field in ("local_video_path", "video_path"):
        raw = record.get(field, "")
        if not raw:
            continue
        candidate = Path(raw)
        if not candidate.is_absolute():
            for base in (cfg.ENGINE_ROOT, cfg.OUTPUTS_DIR):
                trial = (base / raw).resolve()
                if trial.is_file():
                    return str(trial)
        elif candidate.is_file():
            return str(candidate.resolve())

    slug = str(record.get("subject_slug") or "").strip()
    clips_dir = cfg.OUTPUTS_DIR / "clips"
    if slug and clips_dir.is_dir():
        needle = slug.replace("-", "_").lower()
        for clip in clips_dir.iterdir():
            if clip.suffix.lower() not in _VIDEO_EXTS:
                continue
            if needle and needle in clip.stem.lower():
                return str(clip.resolve())
    return ""


def _title_from_clip_name(filename: str) -> str:
    stem = Path(filename).stem
    stem = re.sub(r"^reel_", "", stem, flags=re.IGNORECASE)
    stem = re.sub(r"_v\d+$", "", stem, flags=re.IGNORECASE)
    words = [w for w in re.split(r"[_\-]+", stem) if w]
    topic = " ".join(words).strip() or "Ancient Mysteries"
    return topic[:80]


def extract_video_still(video_path: Path, dest: Path) -> str:
    """Extract a mid-clip JPEG still. Returns dest path or empty string."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_file() and dest.stat().st_size > 0:
        return str(dest.resolve())

    try:
        import subprocess  # noqa: PLC0415
        cmd = [
            "ffmpeg", "-y", "-ss", "2", "-i", str(video_path),
            "-frames:v", "1", "-q:v", "3", str(dest),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if proc.returncode == 0 and dest.is_file():
            return str(dest.resolve())
    except Exception as exc:  # noqa: BLE001
        log.debug("ffmpeg still extract failed for %s: %s", video_path.name, exc)

    try:
        from moviepy.editor import VideoFileClip  # noqa: PLC0415
        clip = VideoFileClip(str(video_path))
        t = min(2.0, max(0.1, (clip.duration or 2.0) * 0.2))
        clip.save_frame(str(dest), t)
        clip.close()
        if dest.is_file():
            return str(dest.resolve())
    except Exception as exc:  # noqa: BLE001
        log.debug("moviepy still extract failed for %s: %s", video_path.name, exc)
    return ""


# ---------------------------------------------------------------------------
# Claude AI generation (optional, called when use_ai=True)
# ---------------------------------------------------------------------------

_AI_USER_TPL = """\
TASK: Transform this post into Pinterest sales content.

TOPIC: {topic}

SOURCE CAPTION:
{caption}

RULES:
1. Remove every "Comment [KEYWORD]", "Type [KEYWORD]", "DM me" CTA entirely.
2. End with a humble, non-pushy line{url_rule}.
3. The pinterest_title must be a keyword-rich search phrase, max 100 chars.
4. visual_hook is 5-8 words in ALL CAPS for overlay text.

OUTPUT: Valid JSON only, no markdown:
{{"pinterest_title": "...", "pinterest_caption": "...", "visual_hook": "..."}}
"""


def _generate_ai(
    topic: str,
    caption: str,
    fact_sheet: str,
    client,
    model: str,
    variant_idx: int,
) -> dict:
    from pinterest_engine import config as cfg  # noqa: PLC0415

    # No channel persona/prompt configured → skip AI (use inventory/templates).
    if not cfg.AI_SYSTEM_PROMPT:
        return {}

    source = caption if caption not in ("PENDING_CAPTION", "") else fact_sheet[:500]
    url = cfg.TARGET_URL
    url_rule = (
        f" pointing to: {url}\n   Example: \"Full details are linked in this pin.\""
        if url else
        " inviting the reader to learn more from the pin link"
    )
    user_msg = _AI_USER_TPL.format(topic=topic, caption=source, url_rule=url_rule)
    try:
        resp = client.messages.create(
            model=model,
            max_tokens=900,
            system=cfg.AI_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
        )
        raw = resp.content[0].text.strip()
        raw = re.sub(r"^```[a-z]*\n?", "", raw).rstrip("```").strip()
        data = json.loads(raw)
        return {
            "pinterest_title": str(data.get("pinterest_title", ""))[:100],
            "pinterest_caption": str(data.get("pinterest_caption", "")),
            "visual_hook": str(data.get("visual_hook", "")),
        }
    except Exception as exc:  # noqa: BLE001
        log.warning("AI generation failed for '%s': %s", topic, exc)
        return {}


# ---------------------------------------------------------------------------
# MasterInventory
# ---------------------------------------------------------------------------

class MasterInventory:
    """
    Manages the master_inventory.json file.

    Usage:
        inv = MasterInventory()
        data = inv.build(force_regenerate=False)   # first run
        data = inv.load()                          # subsequent runs
        unposted = inv.get_unposted(data)
        inv.mark_posted(data, post_id, pin_id)
        inv.save(data)
    """

    def __init__(self, outputs_dir: Path | None = None) -> None:
        from pinterest_engine import config as cfg  # noqa: PLC0415

        self.outputs_dir: Path = outputs_dir or cfg.OUTPUTS_DIR
        self.library_dir: Path = (
            self.outputs_dir / "library" if outputs_dir is not None else cfg.LIBRARY_DIR
        )
        self.inventory_path: Path = self.outputs_dir / MASTER_INVENTORY_FILENAME
        self.content_library_path: Path = self.outputs_dir / "content_library.json"

    # ------------------------------------------------------------------
    # Load / Save

    def load(self) -> dict:
        """Load master_inventory.json. Returns empty structure if not found."""
        if self.inventory_path.is_file():
            try:
                return json.loads(self.inventory_path.read_text(encoding="utf-8"))
            except Exception as exc:  # noqa: BLE001
                log.error("Failed to load master_inventory: %s", exc)
        return {"schema_version": SCHEMA_VERSION, "entries": []}

    def save(self, data: dict) -> None:
        """Atomically save master_inventory.json."""
        data["last_updated_utc"] = datetime.now(timezone.utc).isoformat()
        tmp = self.inventory_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.inventory_path)
        log.debug("Master inventory saved (%d entries).", len(data.get("entries", [])))

    # ------------------------------------------------------------------
    # Build

    def build(
        self,
        use_ai: bool = True,
        ai_delay_sec: float = 0.8,
        force_regenerate: bool = False,
        limit: int | None = None,
        dry_run: bool = False,
    ) -> dict:
        """
        Build (or update) master_inventory.json.

        - Merges all library/*.json + content_library.json
        - Resolves local image paths
        - Generates pinterest_title / pinterest_caption / visual_hook
        - Preserves existing publication_status (never resets posted entries)

        Returns the inventory dict.
        """
        log.info("Building master inventory...")

        # Load existing inventory to preserve publication_status
        existing = self.load()
        existing_map: dict[str, dict] = {
            e["post_id"]: e for e in existing.get("entries", [])
        }

        # Load content_library for cross-reference (UUID + platforms)
        cl_map = self._load_content_library_map()
        log.info("  content_library entries: %d", len(cl_map))

        # Load AI client if requested
        client, model = (None, None)
        if use_ai:
            client, model = self._load_ai_client()
            if not client:
                use_ai = False

        # Load all library JSON records
        lib_files = sorted(self.library_dir.glob("post_*.json"))
        log.info("  library JSON files: %d", len(lib_files))

        entries: list[dict] = []
        generated = 0

        for lib_path in lib_files:
            if limit is not None and generated >= limit:
                break
            try:
                raw = json.loads(lib_path.read_text(encoding="utf-8"))
            except Exception as exc:  # noqa: BLE001
                log.warning("Cannot read %s: %s", lib_path.name, exc)
                continue

            from pinterest_engine import config as cfg  # noqa: PLC0415

            post_id = lib_path.stem
            topic = raw.get("topic") or _default_topic()
            subject_slug = raw.get("subject_slug", "")
            variant_idx = raw.get("variant_index", 0)
            source_caption = raw.get("humanized_caption", "")
            raw_fact = raw.get("raw_fact_sheet", "")

            # Cross-reference with content_library
            cl_key = f"{subject_slug}_{variant_idx}"
            cl_entry = cl_map.get(cl_key, {})

            # Pull existing entry to preserve publication_status + pinterest fields
            prev = existing_map.get(post_id, {})
            pub_status = prev.get("publication_status") or _empty_publication_status()

            # channel_id preferred; fall back to legacy page_id on read
            channel_id = (
                cfg.get_record_channel_id(prev)
                or cfg.get_record_channel_id(raw)
                or cfg.CHANNEL_ID
                or ""
            )

            # Resolve local image / video paths (always re-check so new paths are found)
            combined = {**raw, **({"image_path": cl_entry.get("image_path", "")} if cl_entry else {})}
            local_img = resolve_image_path(combined)
            local_vid = resolve_video_path(combined)

            # Determine if Pinterest metadata needs to be generated
            has_pinterest_meta = bool(
                prev.get("pinterest_title") and prev.get("pinterest_caption")
            )
            needs_generation = force_regenerate or not has_pinterest_meta

            entry: dict = {
                "post_id": post_id,
                "channel_id": channel_id,
                "content_library_id": cl_entry.get("id", ""),
                "topic": topic,
                "subject_slug": subject_slug,
                "variant_index": variant_idx,
                "original_caption": source_caption,
                "raw_fact_sheet": raw_fact,
                "imgbb_url": raw.get("imgbb_url", ""),
                "image_relative": raw.get("image_relative", ""),
                "local_image_path": local_img,
                "local_video_path": local_vid,
                "media_kind": "image",
                "created_utc": raw.get("created_utc", ""),
                "target_url": _target_url(),
                "pinterest_title": prev.get("pinterest_title", ""),
                "pinterest_caption": prev.get("pinterest_caption", ""),
                "visual_hook": prev.get("visual_hook", ""),
                "publication_status": pub_status,
            }

            if needs_generation:
                if use_ai and client:
                    ai_result = _generate_ai(
                        topic, source_caption, raw_fact, client, model, variant_idx
                    )
                    if ai_result:
                        entry.update(ai_result)
                        log.info("  AI generated: %s (v%s)", topic, variant_idx)
                        generated += 1
                        time.sleep(ai_delay_sec)
                    else:
                        self._fill_templates(entry, source_caption, variant_idx)
                        generated += 1
                else:
                    self._fill_templates(entry, source_caption, variant_idx)
                    generated += 1

                # Final safety: strip social CTAs even after AI generation
                if entry.get("pinterest_caption"):
                    if has_social_cta(entry["pinterest_caption"]):
                        entry["pinterest_caption"] = build_caption_regex(
                            entry["pinterest_caption"], variant_idx
                        )

            entries.append(entry)   # always accumulate in memory

        recycled = self._recycle_unpaired_videos(entries, existing_map)
        if recycled:
            log.info("  recycled unpaired video clips: %d", recycled)

        from pinterest_engine import config as cfg  # noqa: PLC0415

        data = {
            "schema_version": SCHEMA_VERSION,
            "channel_id": cfg.CHANNEL_ID or "",
            "generated_utc": datetime.now(timezone.utc).isoformat(),
            "total_entries": len(entries),
            "entries": entries,
        }

        if not dry_run:
            self.save(data)
            log.info(
                "Master inventory built: %d entries (%d new metadata generated).",
                len(entries), generated,
            )
        else:
            log.info(
                "[DRY RUN] Would write %d entries (%d with generated metadata).",
                len(entries), generated,
            )

        return data

    # ------------------------------------------------------------------
    # Query helpers

    def get_unposted(self, data: dict) -> list[dict]:
        """Return all entries where posted_on_pinterest == False."""
        return [
            e for e in data.get("entries", [])
            if not e.get("publication_status", {}).get("posted_on_pinterest", False)
            and (
                e.get("local_image_path")
                or e.get("imgbb_url")
                or e.get("local_video_path")
            )
            and e.get("pinterest_title")
            and e.get("pinterest_caption")
        ]

    def get_entry(self, data: dict, post_id: str) -> dict | None:
        """Find an entry by post_id."""
        for e in data.get("entries", []):
            if e["post_id"] == post_id:
                return e
        return None

    def mark_posted(
        self,
        data: dict,
        post_id: str,
        pinterest_pin_id: str,
    ) -> None:
        """Update publication_status after a successful Pinterest publish."""
        entry = self.get_entry(data, post_id)
        if entry is None:
            log.warning("mark_posted: post_id '%s' not found.", post_id)
            return
        status = entry.setdefault("publication_status", _empty_publication_status())
        status["posted_on_pinterest"] = True
        status["pinterest_pin_id"] = pinterest_pin_id
        status["pinterest_post_date"] = datetime.now(timezone.utc).isoformat()

    def count_verified_images(self, data: dict) -> int:
        """Count entries with a verified local image path on disk."""
        return sum(
            1 for e in data.get("entries", [])
            if e.get("local_image_path") and Path(e["local_image_path"]).is_file()
        )

    # ------------------------------------------------------------------
    # Initialise history ledger

    def init_history_ledger(self) -> Path:
        """Create outputs/pinterest_history.json if it does not exist."""
        path = self.outputs_dir / "pinterest_history.json"
        if not path.is_file():
            path.write_text(
                json.dumps({"published": [], "errors": []}, indent=2),
                encoding="utf-8",
            )
            log.info("pinterest_history.json initialised (empty).")
        return path

    # ------------------------------------------------------------------
    # Pre-flight readiness check

    def check_readiness(self) -> dict[str, object]:
        """
        Run the pre-flight checklist.

        Returns a dict with individual check results and an overall 'ready' bool.
        Prints a formatted report to stdout.
        """
        import os  # noqa: PLC0415

        checks: dict[str, object] = {}

        # 1. Token
        token = os.getenv("PINTEREST_ACCESS_TOKEN", "")
        checks["token_set"] = bool(token)
        checks["token_preview"] = (token[:12] + "...") if token else "(not set)"

        # 2. Board ID or auto-create name
        from pinterest_engine import config as cfg  # noqa: PLC0415
        board_id = os.getenv("PINTEREST_BOARD_ID", "")
        board_name = os.getenv("PINTEREST_BOARD_NAME") or getattr(cfg, "BOARD_NAME", "")
        checks["board_id_set"] = bool(board_id or board_name)
        checks["board_id"] = board_id or f"(will auto-create: {board_name or '?'})"

        # 3. Master inventory exists
        inv_exists = self.inventory_path.is_file()
        checks["master_inventory_exists"] = inv_exists

        # 4. Verified images
        verified_count = 0
        total_entries = 0
        unposted_count = 0
        if inv_exists:
            data = self.load()
            total_entries = len(data.get("entries", []))
            verified_count = self.count_verified_images(data)
            unposted_count = len(self.get_unposted(data))
        checks["total_entries"] = total_entries
        checks["verified_images"] = verified_count
        checks["unposted_queue"] = unposted_count
        checks["has_20_verified"] = verified_count >= 20

        # 5. History ledger
        history_path = self.init_history_ledger()
        checks["history_initialized"] = history_path.is_file()

        # 6. Overall
        critical = ["token_set", "board_id_set", "master_inventory_exists",
                    "has_20_verified", "history_initialized"]
        checks["ready"] = all(checks[k] for k in critical)

        # Print report
        status = lambda ok: "OK " if ok else "FAIL"  # noqa: E731
        print("\n=== Pinterest Engine Pre-Flight Check ===")
        print(f"  [{status(checks['token_set'])}] Pinterest access token: {checks['token_preview']}")
        print(f"  [{status(checks['board_id_set'])}] Pinterest board ID: {checks['board_id']}")
        print(f"  [{status(checks['master_inventory_exists'])}] master_inventory.json exists")
        print(f"  [{status(checks['has_20_verified'])}] Verified G: Drive images: {verified_count} (need >= 20)")
        print(f"  [ OK] Total entries in inventory: {total_entries}")
        print(f"  [ OK] Unposted queue: {unposted_count} pins ready")
        print(f"  [{status(checks['history_initialized'])}] pinterest_history.json initialized")
        print(f"\n  {'[GREEN LIGHT] Ready to publish!' if checks['ready'] else '[RED] Fix the FAIL items above before publishing.'}")
        print()

        return checks

    # ------------------------------------------------------------------
    # Private helpers

    def _load_content_library_map(self) -> dict[str, dict]:
        """Return {subject_slug_variantN: entry} from content_library.json.
        Tries UTF-8, UTF-16, and UTF-8-sig to handle Google Drive encoding issues.
        """
        if not self.content_library_path.is_file():
            return {}
        raw_bytes = self.content_library_path.read_bytes()
        text = None
        for enc in ("utf-8-sig", "utf-16", "utf-8", "latin-1"):
            try:
                text = raw_bytes.decode(enc)
                break
            except (UnicodeDecodeError, ValueError):
                continue
        if text is None:
            log.warning("content_library.json: could not decode with any encoding.")
            return {}
        try:
            entries = json.loads(text)
            if not isinstance(entries, list):
                return {}
            result = {
                f"{e.get('subject_slug', '')}_{e.get('variant_index', 0)}": e
                for e in entries
                if isinstance(e, dict)
            }
            log.debug("content_library loaded: %d entries", len(result))
            return result
        except Exception as exc:  # noqa: BLE001
            log.warning("content_library.json parse failed: %s", exc)
            return {}

    def _load_ai_client(self) -> tuple:
        try:
            import anthropic  # noqa: PLC0415
            from pinterest_engine import config as cfg  # noqa: PLC0415

            if not cfg.ANTHROPIC_API_KEY:
                return None, None
            client = anthropic.Anthropic(api_key=cfg.ANTHROPIC_API_KEY)
            model = cfg.get_best_claude_model(client)
            log.info("AI client ready (model: %s)", model)
            return client, model
        except Exception as exc:  # noqa: BLE001
            log.debug("AI client init failed: %s", exc)
            return None, None

    def _fill_templates(
        self, entry: dict, source_caption: str, variant_idx: int
    ) -> None:
        """
        Fill pinterest fields using fast regex/template mode.
        Uses truthiness check (not setdefault) to overwrite empty strings.
        Falls back to raw_fact_sheet if source_caption is an un-humanized draft.
        """
        topic = entry["topic"]

        if not entry.get("pinterest_title"):
            entry["pinterest_title"] = build_title_template(topic)

        if not entry.get("pinterest_caption"):
            # If the humanized caption is a raw draft, try the fact sheet instead
            source = source_caption
            if is_raw_draft(source):
                log.debug(
                    "  source_caption is raw draft for '%s' -- using raw_fact_sheet.", topic
                )
                fact = entry.get("raw_fact_sheet", "")
                source = strip_draft_headers(fact) if fact else ""
            entry["pinterest_caption"] = build_caption_regex(source, variant_idx)

        if not entry.get("visual_hook"):
            entry["visual_hook"] = build_visual_hook(topic)

    def _recycle_unpaired_videos(
        self,
        entries: list[dict],
        existing_map: dict[str, dict],
    ) -> int:
        """
        Add inventory rows for clips/ videos not already linked to a library post.
        Extracts a still so the image transformer / image-pin fallback still works.
        """
        from pinterest_engine import config as cfg  # noqa: PLC0415

        clips_dir = self.outputs_dir / "clips"
        if not clips_dir.is_dir():
            return 0

        linked = {
            Path(e["local_video_path"]).name
            for e in entries
            if e.get("local_video_path")
        }
        added = 0

        for clip in sorted(clips_dir.iterdir()):
            if clip.suffix.lower() not in _VIDEO_EXTS:
                continue
            if clip.name in linked:
                continue
            post_id = f"recycle_{clip.stem}"[:80]
            prev = existing_map.get(post_id, {})
            topic = prev.get("topic") or _title_from_clip_name(clip.name)
            entry = {
                "post_id": post_id,
                "channel_id": cfg.CHANNEL_ID or "",
                "content_library_id": "",
                "topic": topic,
                "subject_slug": clip.stem,
                "variant_index": 0,
                "original_caption": prev.get("original_caption") or topic,
                "raw_fact_sheet": "",
                "imgbb_url": "",
                "image_relative": "",
                "local_image_path": prev.get("local_image_path", ""),
                "local_video_path": str(clip.resolve()),
                "media_kind": "video",
                "created_utc": prev.get("created_utc", ""),
                "target_url": _target_url(),
                "pinterest_title": prev.get("pinterest_title", ""),
                "pinterest_caption": prev.get("pinterest_caption", ""),
                "visual_hook": prev.get("visual_hook", ""),
                "publication_status": prev.get("publication_status")
                or _empty_publication_status(),
            }
            if not entry["pinterest_title"] or not entry["pinterest_caption"]:
                self._fill_templates(entry, topic, 0)
            entries.append(entry)
            added += 1
        return added

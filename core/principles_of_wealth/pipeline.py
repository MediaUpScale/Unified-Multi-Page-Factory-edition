# -*- coding: utf-8 -*-
"""Orchestrate scan -> uniqueness pass -> independent scheduled publish.

Longs and Shorts are separate CLI runs:
  * longs  — Tuesday and Thursday (2 per week), private + publishAt
  * shorts — one per day at 18:00 America/New_York; relatedVideoId is set when a
    parent long_video_id exists, otherwise the Short still uploads.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from agents.media.content_library import append_entry
from core.principles_of_wealth.catalog import (
    CHANNEL_ID,
    EPISODES,
    PLAYLISTS,
    build_long_description,
    build_long_title,
    build_short_description,
    build_short_title,
    default_tags,
    episode_by_number,
    parse_episode_list,
    playlist_for_episode,
    resolve_processed_directory,
)
from core.principles_of_wealth.fingerprint import (
    already_processed,
    default_processed_path,
    process_pair,
)
from core.utils.fingerprint_engine import apply_video_uniqueness
from core.principles_of_wealth.scanner import (
    ScanResult,
    clip_index_from_path,
    scan_source_directory,
    short_hook_from_path,
    write_scan_snapshot,
)
from core.principles_of_wealth.schedule import (
    advance_slot,
    cadence_label,
    format_slot_pair,
    latest_future_scheduled_at,
    max_anchor,
    resolve_first_slot,
)

_LOG = logging.getLogger(__name__)
from utils.pipeline_paths import page_outputs_dir

_ENGINE_ROOT = Path(__file__).resolve().parents[2]
_OUTPUTS = page_outputs_dir(CHANNEL_ID)
_STATE_PATH = _OUTPUTS / "wealth_publish_state.json"
_SCAN_PATH = _OUTPUTS / "wealth_asset_map.json"
_LIBRARY_PATH = _OUTPUTS / "content_library.json"

_PAGE = CHANNEL_ID


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_state() -> dict[str, Any]:
    if not _STATE_PATH.is_file():
        return {"channel_id": CHANNEL_ID, "updated_at": "", "episodes": {}}
    try:
        data = json.loads(_STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        _LOG.warning("Could not read %s (%s) — starting empty state.", _STATE_PATH, exc)
        return {"channel_id": CHANNEL_ID, "updated_at": "", "episodes": {}}
    if not isinstance(data, dict):
        return {"channel_id": CHANNEL_ID, "updated_at": "", "episodes": {}}
    data.setdefault("channel_id", CHANNEL_ID)
    data.setdefault("episodes", {})
    return data


def _save_state(state: dict[str, Any]) -> None:
    state["updated_at"] = _utc_now()
    _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _STATE_PATH.write_text(
        json.dumps(state, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _ep_state(state: dict[str, Any], episode: int) -> dict[str, Any]:
    episodes = state.setdefault("episodes", {})
    key = str(episode)
    row = episodes.get(key)
    if not isinstance(row, dict):
        row = {"episode": episode}
        episodes[key] = row
    row["episode"] = episode
    return row


def _selected(scan: ScanResult, episodes: Optional[list[int]]) -> list[int]:
    available = sorted(scan.matches)
    if episodes is None:
        return available
    return [n for n in episodes if n in scan.matches]


_PROTECTED_EPISODE = 1
_SHORT_DURATION_CEILING_S = 60


def _sign_video(
    src: str,
    dest_dir: Path,
    *,
    skip_existing: bool,
    options: dict[str, Any],
) -> str:
    """Write a signed MP4 via the generic uniqueness engine."""
    dest = default_processed_path(Path(src), dest_dir, kind="long")
    if skip_existing and already_processed(dest):
        print(f"[Wealth] Skip existing: {dest.name}")
        return str(dest)
    print(f"[Wealth] Processing (fast uniqueness): {Path(src).name}")
    return apply_video_uniqueness(str(src), str(dest), options)


def _short_ids_from_row(row: dict[str, Any]) -> list[str]:
    ids = _as_str_list(row.get("short_video_ids")) or _as_str_list(
        row.get("short_video_id")
    )
    for item in row.get("short_uploads") or []:
        if isinstance(item, dict):
            vid = str(item.get("video_id") or "").strip()
            if vid:
                ids.append(vid)
    # Preserve order, drop blanks/dupes
    seen: set[str] = set()
    out: list[str] = []
    for vid in ids:
        if vid and vid not in seen:
            seen.add(vid)
            out.append(vid)
    return out


def run_scan(
    *,
    source_dir: Optional[str | Path] = None,
    persist: bool = True,
) -> ScanResult:
    scan = scan_source_directory(source_dir)
    print(f"[Wealth] Source: {scan.source_dir}")
    print(f"[Wealth] Matched episodes: {len(scan.matches)}")
    for n, match in sorted(scan.matches.items()):
        spec = episode_by_number(n)
        flags = []
        if match.has_long:
            flags.append("LONG")
        if match.has_short:
            flags.append(f"SHORTS {match.short_count}/10")
        if match.has_thumbnail:
            flags.append("THUMB")
        print(
            f"  Ep {n:02d} [{spec.act_label}] {spec.title_core}  "
            f"({' + '.join(flags) or 'empty'})"
        )
        for i, short_path in enumerate(match.shorts, start=1):
            clip = clip_index_from_path(short_path) or i
            print(f"           [{clip:02d}] {Path(short_path).name}")
    if scan.missing_episodes:
        print(f"[Wealth] Longs still missing: {scan.missing_episodes}")
    if scan.skipped_empty:
        print(f"[Wealth] Empty files skipped: {len(scan.skipped_empty)}")
    extra_n = len(scan.unmatched)
    if extra_n:
        print(
            f"[Wealth] Unmatched library files (chapters / extras, not auto-assigned): {extra_n}"
        )
    if persist:
        write_scan_snapshot(scan, _SCAN_PATH)
        print(f"[Wealth] Asset map -> {_SCAN_PATH}")
    return scan


_RAW_VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".m4v", ".webm"}
_RAW_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


def _collect_process_jobs(
    scan: ScanResult,
    wanted: list[int],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Every raw long, Short, and thumbnail for *wanted*, plus leftover media."""
    jobs: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _add(episode: int, kind: str, path: str) -> None:
        if not path or path in seen:
            return
        seen.add(path)
        jobs.append({"episode": episode, "kind": kind, "path": path})

    for n in wanted:
        m = scan.matches[n]
        if m.has_long:
            _add(n, "long", m.long_path)
        for short_path in m.shorts:
            _add(n, "short", short_path)
        if m.has_thumbnail:
            _add(n, "thumb", m.thumbnail_path)

    extras: list[dict[str, Any]] = []
    for raw in scan.unmatched:
        path = Path(str(raw))
        if not path.is_file() or path.stat().st_size <= 0:
            continue
        if path.stem.lower().endswith("_signed"):
            continue
        suffix = path.suffix.lower()
        if suffix in _RAW_VIDEO_EXTS:
            extras.append({"episode": 0, "kind": "extra_video", "path": str(path)})
        elif suffix in _RAW_IMAGE_EXTS:
            extras.append({"episode": 0, "kind": "extra_thumb", "path": str(path)})
    return jobs, extras


def run_process(
    *,
    source_dir: Optional[str | Path] = None,
    processed_dir: Optional[str | Path] = None,
    episodes: Optional[str] = None,
    skip_existing: bool = True,
    hwaccel: bool = True,
    hw_encode: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    scan = scan_source_directory(source_dir)
    src = Path(scan.source_dir)
    dest_dir = resolve_processed_directory(src, processed_dir)
    wanted = _selected(scan, parse_episode_list(episodes))
    state = _load_state()
    jobs, extras = _collect_process_jobs(scan, wanted)

    n_long = sum(1 for j in jobs if j["kind"] == "long")
    n_short = sum(1 for j in jobs if j["kind"] == "short")
    n_thumb = sum(1 for j in jobs if j["kind"] == "thumb")
    print(f"[Wealth] Processed folder: {dest_dir}")
    print(
        f"[Wealth] Raw inventory: {n_long} long(s), {n_short} Short(s), "
        f"{n_thumb} thumbnail(s) across {len(wanted)} episode(s)"
        + (f" + {len(extras)} unmatched media" if extras else "")
    )

    if dry_run:
        for n in wanted:
            m = scan.matches[n]
            print(
                f"  [dry-run] Ep {n:02d}  long={bool(m.has_long)} "
                f"shorts={m.short_count}/10 thumb={bool(m.has_thumbnail)}"
            )
            for i, short_path in enumerate(m.shorts, start=1):
                clip = clip_index_from_path(short_path) or i
                print(f"            [{clip:02d}] {Path(short_path).name}")
        for extra in extras:
            print(f"  [dry-run] extra {extra['kind']}: {Path(extra['path']).name}")
        return {
            "processed_dir": str(dest_dir),
            "episodes": wanted,
            "jobs": len(jobs) + len(extras),
            "dry_run": True,
        }

    dest_dir.mkdir(parents=True, exist_ok=True)
    options = {"hwaccel": hwaccel, "hw_encode": hw_encode}
    signed_shorts: dict[int, list[str]] = {}

    for job in jobs:
        n = int(job["episode"])
        kind = str(job["kind"])
        path = str(job["path"])
        row = _ep_state(state, n)
        spec = episode_by_number(n)
        if kind == "long":
            print(f"[Wealth] Uniqueness pass - Ep {n:02d} LONG {spec.short_title}")
            row["source_long"] = path
            try:
                row["processed_long"] = _sign_video(
                    path, dest_dir, skip_existing=skip_existing, options=options
                )
            except (OSError, RuntimeError) as exc:
                _LOG.warning("Long skip Ep %02d %s: %s", n, Path(path).name, exc)
                print(f"[Wealth] Long skip Ep {n:02d} {Path(path).name}: {exc}")
        elif kind == "short":
            clip = clip_index_from_path(path) or (len(signed_shorts.get(n) or []) + 1)
            print(f"[Wealth] Uniqueness pass - Ep {n:02d} SHORT {clip:02d} {Path(path).name}")
            row["source_shorts"] = list(scan.matches[n].shorts)
            try:
                signed = _sign_video(
                    path, dest_dir, skip_existing=skip_existing, options=options
                )
            except (OSError, RuntimeError) as exc:
                _LOG.warning("Short skip Ep %02d %s: %s", n, Path(path).name, exc)
                print(f"[Wealth] Short skip Ep {n:02d} {Path(path).name}: {exc}")
                signed = ""
            if signed:
                signed_shorts.setdefault(n, []).append(signed)
                row["processed_shorts"] = list(signed_shorts[n])
        elif kind == "thumb":
            print(f"[Wealth] Uniqueness pass - Ep {n:02d} THUMB {Path(path).name}")
            row["source_thumbnail"] = path
            try:
                row["processed_thumbnail"] = process_pair(
                    path,
                    dest_dir,
                    kind="thumb",
                    skip_existing=skip_existing,
                )
            except OSError as exc:
                _LOG.warning("Thumbnail skip Ep %02d %s: %s", n, Path(path).name, exc)
                print(f"[Wealth] Thumbnail skip Ep {n:02d} {Path(path).name}: {exc}")
        _save_state(state)

    for extra in extras:
        path = str(extra["path"])
        kind = str(extra["kind"])
        print(f"[Wealth] Uniqueness pass - extra {kind}: {Path(path).name}")
        try:
            if kind == "extra_video":
                _sign_video(path, dest_dir, skip_existing=skip_existing, options=options)
            else:
                process_pair(
                    path,
                    dest_dir,
                    kind="thumb",
                    skip_existing=skip_existing,
                )
        except (OSError, RuntimeError) as exc:
            _LOG.warning("Extra skip %s: %s", Path(path).name, exc)
            print(f"[Wealth] Extra skip {Path(path).name}: {exc}")

    _save_state(state)
    print(
        f"[Wealth] Uniqueness complete: {n_long} long(s), {n_short} Short(s), "
        f"{n_thumb} thumbnail(s) signed into {dest_dir}"
    )
    return {
        "processed_dir": str(dest_dir),
        "episodes": wanted,
        "signed_longs": n_long,
        "signed_shorts": n_short,
        "signed_thumbs": n_thumb,
        "extras": len(extras),
        "state": str(_STATE_PATH),
    }


def _as_str_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(v) for v in value if v]
    if isinstance(value, str) and value.strip():
        return [value]
    return []


_TITLE_NOISE_RE = re.compile(
    r"\b(ray\s*dalio|principles\s+of\s+wealth|principlesofwealth)\b",
    re.IGNORECASE,
)
_EP_NUM_RE = re.compile(r"\bep(?:isode)?\s*0*(\d{1,2})\b", re.IGNORECASE)
_NEEDLE_MIN = 12


def _normalize_title(text: str) -> str:
    raw = (text or "").replace("\u2014", " ").replace("\u2013", " ").replace("-", " ")
    raw = _TITLE_NOISE_RE.sub(" ", raw)
    raw = re.sub(r"[|#:.,;!?()\[\]'\"/]+", " ", raw)
    return re.sub(r"\s+", " ", raw).strip().lower()


def _long_title_needles(spec: Any) -> list[str]:
    from core.principles_of_wealth.seo_catalog import long_pack

    pack_title = str(long_pack(spec.episode).get("title") or "")
    raws = [spec.title_core, pack_title, *list(spec.match_keywords or ())]
    needles: list[str] = []
    for raw in raws:
        needle = _normalize_title(str(raw))
        if len(needle) >= _NEEDLE_MIN and needle not in needles:
            needles.append(needle)
    return needles


def match_long_episode_from_title(title: str) -> Optional[int]:
    """Map a YouTube title onto catalog episode 1-30, or None if unmatched/Short-like."""
    raw = title or ""
    low = raw.lower()
    if "#shorts" in low:
        return None
    numbered = _EP_NUM_RE.search(raw)
    if numbered:
        n = int(numbered.group(1))
        if 1 <= n <= 30:
            return n
    norm = _normalize_title(raw)
    if not norm:
        return None
    best_n: Optional[int] = None
    best_score = 0
    for spec in EPISODES:
        for needle in _long_title_needles(spec):
            if needle in norm and len(needle) > best_score:
                best_score = len(needle)
                best_n = spec.episode
    if best_score < _NEEDLE_MIN:
        return None
    return best_n


def _is_scheduled_long_meta(meta: dict[str, Any]) -> bool:
    """True for a pending scheduled long (publishAt and/or still private)."""
    if meta.get("publish_at"):
        return True
    return str(meta.get("privacy") or "").lower() == "private"


def _looks_like_short(meta: dict[str, Any]) -> bool:
    title = str(meta.get("title") or "")
    desc = str(meta.get("description") or "")
    duration_s = int(meta.get("duration_s") or 0)
    if duration_s and duration_s < _SHORT_DURATION_CEILING_S:
        return True
    blob = f"{title}\n{desc}".lower()
    return "#shorts" in blob or bool(re.search(r"(?m)^#shorts\b", blob))


def _collect_all_short_ids(state: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    for row in (state.get("episodes") or {}).values():
        if isinstance(row, dict):
            ids.extend(_short_ids_from_row(row))
    seen: set[str] = set()
    unique: list[str] = []
    for vid in ids:
        if vid not in seen:
            seen.add(vid)
            unique.append(vid)
    return unique


def run_cleanup(
    *,
    from_episode: int = 2,
    longs_only: bool = True,
    dry_run: bool = False,
    episodes: Optional[str] = None,
    source_dir: Optional[str | Path] = None,  # noqa: ARG001 — CLI common flag
) -> dict[str, Any]:
    """Delete scheduled Ep 2+ long-form YouTube videos. Never touch Ep 1 or Shorts."""
    _ = source_dir
    if not longs_only:
        print("[Wealth] Cleanup is longs-only - Shorts will not be deleted.")
        longs_only = True

    floor = max(int(from_episode or _PROTECTED_EPISODE + 1), _PROTECTED_EPISODE + 1)
    if int(from_episode or 0) < floor:
        print(
            f"[Wealth] Episode {_PROTECTED_EPISODE} is protected - "
            f"clamping --from-episode to {floor}."
        )

    wanted = parse_episode_list(episodes)
    state = _load_state()
    episodes_map = state.get("episodes") or {}
    if not isinstance(episodes_map, dict):
        episodes_map = {}

    ep1_row = episodes_map.get(str(_PROTECTED_EPISODE)) or {}
    if not isinstance(ep1_row, dict):
        ep1_row = {}
    protected_long_id = str(ep1_row.get("long_video_id") or "")
    all_short_ids = _collect_all_short_ids(state)
    short_id_set = set(all_short_ids)
    if protected_long_id:
        short_id_set.add(protected_long_id)

    print(
        f"[Wealth] Cleanup scope: scheduled longs from Ep {floor}+ "
        f"({'dry-run' if dry_run else 'live'})"
    )
    print(
        f"[Wealth] Guardrails: Episode {_PROTECTED_EPISODE} long + "
        f"{len(all_short_ids)} Short ID(s) across all episodes are untouchable."
    )

    from agents.posting.youtube_publisher import (
        build_youtube_client_for_page,
        fetch_videos_metadata,
        list_channel_upload_videos,
    )

    youtube = build_youtube_client_for_page(_PAGE, enforce_channel=True)
    uploads = list_channel_upload_videos(youtube, max_pages=20)
    print(f"[Wealth] YouTube uploads scanned: {len(uploads)}")

    targets: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    def _consider(
        episode: int,
        video_id: str,
        meta: dict[str, Any],
        *,
        source: str,
    ) -> None:
        nonlocal protected_long_id
        if not video_id or video_id in seen_ids:
            return
        if video_id in short_id_set:
            skipped.append(
                {
                    "episode": episode,
                    "reason": "ID is Episode 1 long or a Short - refused",
                    "video_id": video_id,
                }
            )
            seen_ids.add(video_id)
            return
        if _looks_like_short(meta):
            skipped.append(
                {
                    "episode": episode or 0,
                    "reason": "YouTube duration/title looks like a Short - refused",
                    "video_id": video_id,
                }
            )
            seen_ids.add(video_id)
            return
        if episode == _PROTECTED_EPISODE or episode < floor:
            if episode == _PROTECTED_EPISODE:
                if not protected_long_id:
                    protected_long_id = video_id
                short_id_set.add(video_id)
            skipped.append(
                {
                    "episode": episode or _PROTECTED_EPISODE,
                    "reason": "protected episode",
                    "video_id": video_id,
                }
            )
            seen_ids.add(video_id)
            return
        if wanted is not None and episode not in wanted:
            skipped.append(
                {
                    "episode": episode,
                    "reason": "outside --episodes filter",
                    "video_id": video_id,
                }
            )
            seen_ids.add(video_id)
            return
        if not _is_scheduled_long_meta(meta) and source == "youtube":
            skipped.append(
                {
                    "episode": episode,
                    "reason": "public / not scheduled - left on channel",
                    "video_id": video_id,
                }
            )
            seen_ids.add(video_id)
            return
        seen_ids.add(video_id)
        targets.append(
            {
                "episode": episode,
                "video_id": video_id,
                "meta": meta,
                "source": source,
            }
        )

    for item in uploads:
        video_id = str(item.get("video_id") or "").strip()
        title = str(item.get("title") or "")
        episode = match_long_episode_from_title(title)
        if episode is None:
            if _looks_like_short(item) or not _is_scheduled_long_meta(item):
                continue
            skipped.append(
                {
                    "episode": 0,
                    "reason": f"unmatched scheduled title: {title[:80]}",
                    "video_id": video_id,
                }
            )
            continue
        _consider(episode, video_id, item, source="youtube")

    # State IDs are a fallback when YouTube title matching missed a recorded long.
    state_ids: list[tuple[int, str]] = []
    for key, row in episodes_map.items():
        if not str(key).isdigit() or not isinstance(row, dict):
            continue
        n = int(key)
        video_id = str(row.get("long_video_id") or "").strip()
        if video_id:
            state_ids.append((n, video_id))
    missing_state = [vid for _, vid in state_ids if vid not in seen_ids]
    extra_meta = fetch_videos_metadata(youtube, missing_state) if missing_state else {}
    for n, video_id in state_ids:
        if video_id in seen_ids:
            continue
        meta = extra_meta.get(video_id) or {"title": "", "duration_s": 0, "privacy": "private"}
        if n < floor:
            _consider(n, video_id, meta, source="state")
            continue
        _consider(n, video_id, meta, source="state")

    to_delete = list(targets)
    print(f"[Wealth] Matched scheduled longs to delete: {len(to_delete)}")

    deleted: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []

    if dry_run:
        for item in to_delete:
            title = (item.get("meta") or {}).get("title") or ""
            print(
                f"  [dry-run] DELETE LONG Ep {item['episode']:02d}  "
                f"{item['video_id']}  {title}"
            )
    else:
        from agents.posting.youtube_publisher import delete_youtube_video

        for item in to_delete:
            title = (item.get("meta") or {}).get("title") or ""
            try:
                row = _ep_state(state, int(item["episode"]))
                row["long_video_id"] = item["video_id"]
                delete_youtube_video(youtube, item["video_id"])
                row["long_video_id"] = ""
                deleted.append(
                    {
                        "episode": item["episode"],
                        "video_id": item["video_id"],
                        "title": title,
                    }
                )
                print(
                    f"  [deleted] LONG Ep {item['episode']:02d}  "
                    f"{item['video_id']}  {title}"
                )
                _save_state(state)
            except Exception as exc:  # noqa: BLE001
                failed.append(
                    {
                        "episode": item["episode"],
                        "video_id": item["video_id"],
                        "error": str(exc),
                    }
                )
                print(
                    f"  [FAILED]  LONG Ep {item['episode']:02d}  "
                    f"{item['video_id']}: {exc}"
                )

    if not dry_run:
        _save_state(state)

    # Re-read so the verification block reflects disk.
    verify = _load_state() if not dry_run else state
    verify_map = verify.get("episodes") or {}
    verify_ep1 = verify_map.get(str(_PROTECTED_EPISODE)) or {}
    verify_ep1_long = str((verify_ep1 or {}).get("long_video_id") or "")
    verify_shorts = _collect_all_short_ids(verify)

    print("")
    print("[Wealth] Deleted long video IDs:")
    if dry_run:
        if to_delete:
            for item in to_delete:
                print(f"  (would delete) Ep {item['episode']:02d}  {item['video_id']}")
        else:
            print("  (none)")
    elif deleted:
        for item in deleted:
            print(f"  Ep {item['episode']:02d}  {item['video_id']}")
    else:
        print("  (none)")

    print("")
    print("[Wealth] Verification - untouched:")
    print(
        f"  Episode {_PROTECTED_EPISODE} long_video_id = "
        f"{verify_ep1_long or protected_long_id or '(empty)'}"
    )
    print(f"  Short records preserved: {len(verify_shorts)}")
    for vid in verify_shorts:
        print(f"    SHORT  {vid}")
    if skipped:
        print("[Wealth] Skipped (guardrail):")
        for item in skipped:
            print(
                f"  Ep {int(item['episode']):02d}  {item.get('video_id') or '-'}  "
                f"{item.get('reason')}"
            )

    reported_ids = {
        str(item["video_id"])
        for item in (to_delete if dry_run else deleted)
        if item.get("video_id")
    }
    ep1_ok = (not protected_long_id) or (protected_long_id not in reported_ids)
    shorts_ok = set(verify_shorts) == set(all_short_ids)
    if ep1_ok and shorts_ok:
        print(
            "[Wealth] Confirmed: Episode 1 long and all Shorts records are untouched."
        )
    else:
        print(
            "[Wealth] WARNING: post-cleanup state mismatch for Episode 1 or Shorts."
        )

    return {
        "from_episode": floor,
        "longs_only": True,
        "dry_run": dry_run,
        "deleted": deleted if not dry_run else [],
        "would_delete": [item["video_id"] for item in to_delete] if dry_run else [],
        "failed": failed,
        "skipped": skipped,
        "protected_ep1_long": protected_long_id,
        "protected_short_ids": all_short_ids,
        "ep1_untouched": ep1_ok,
        "shorts_untouched": shorts_ok,
        "state": str(_STATE_PATH),
    }


def _resolve_upload_path(row: dict[str, Any], *, kind: str, use_processed: bool) -> str:
    if use_processed:
        processed = str(row.get(f"processed_{kind}") or "")
        if processed and Path(processed).is_file() and Path(processed).stat().st_size > 0:
            return processed
    source = str(row.get(f"source_{kind}") or "")
    return source if source and Path(source).is_file() else ""


def _resolve_short_upload_paths(row: dict[str, Any], *, use_processed: bool) -> list[str]:
    processed = _as_str_list(row.get("processed_shorts"))
    sources = _as_str_list(row.get("source_shorts")) or _as_str_list(row.get("source_short"))
    if use_processed and processed:
        out: list[str] = []
        for i, src in enumerate(sources or processed):
            cand = processed[i] if i < len(processed) else ""
            if cand and Path(cand).is_file() and Path(cand).stat().st_size > 0:
                out.append(cand)
            elif src and Path(src).is_file():
                out.append(src)
        return out
    return [p for p in sources if Path(p).is_file()]


def _hydrate_state_from_scan(
    state: dict[str, Any],
    scan: ScanResult,
    episodes: list[int],
) -> None:
    for n in episodes:
        m = scan.matches[n]
        row = _ep_state(state, n)
        if m.has_long:
            row.setdefault("source_long", m.long_path)
        if m.has_short:
            row["source_shorts"] = list(m.shorts)
        if m.has_thumbnail:
            row.setdefault("source_thumbnail", m.thumbnail_path)


def _print_schedule_table(rows: list[dict[str, Any]]) -> None:
    """Type | Episode | Title | Scheduled (UTC/EST) | URL"""
    if not rows:
        print("[Wealth] No uploads in this run.")
        return
    headers = ("Type", "Episode", "Title", "Scheduled (UTC / EST)", "URL")
    table: list[tuple[str, str, str, str, str]] = [headers]
    for row in rows:
        title = str(row.get("title") or "").replace("\u2014", "-").replace("\u2013", "-")
        if len(title) > 48:
            title = title[:45] + "..."
        table.append(
            (
                str(row.get("type") or ""),
                str(row.get("episode") or ""),
                title,
                str(row.get("scheduled") or ""),
                str(row.get("url") or ""),
            )
        )
    widths = [max(len(r[i]) for r in table) for i in range(5)]
    def _line(cells: tuple[str, ...]) -> str:
        return " | ".join(cells[i].ljust(widths[i]) for i in range(5))
    print()
    print(_line(headers))
    print("-+-".join("-" * w for w in widths))
    for cells in table[1:]:
        print(_line(cells))
    print()


def _append_library(
    *,
    spec,
    kind: str,
    video_path: str,
    video_id: str,
    title: str,
    description: str,
) -> None:
    try:
        append_entry(
            _LIBRARY_PATH,
            {
                "topic": spec.title_core,
                "title": title,
                "final_caption": description,
                "caption": description,
                "video_path": video_path,
                "youtube_video_id": video_id,
                "youtube_url": f"https://youtu.be/{video_id}" if video_id else "",
                "kind": kind,
                "episode": spec.episode,
                "act": spec.act,
                "timestamp": _utc_now(),
            },
        )
    except Exception as exc:  # noqa: BLE001
        _LOG.warning("content_library append failed for ep %s: %s", spec.episode, exc)


def run_publish(
    *,
    source_dir: Optional[str | Path] = None,
    mode: str = "longs",
    episodes: Optional[str] = None,
    schedule: bool = True,
    start_date: Optional[str] = None,
    time_utc: Optional[str] = None,
    privacy_long: str = "private",
    privacy_short: str = "private",
    use_processed: bool = True,
    process_first: bool = False,
    skip_existing_uploads: bool = True,
    dry_run: bool = False,
    hwaccel: bool = True,
    hw_encode: bool = False,
    limit: Optional[int] = None,
) -> dict[str, Any]:
    """Independent scheduled upload for longs (weekly) or Shorts (daily)."""
    from agents.posting.youtube_publisher import (
        DailyUploadSafetyGate,
        YouTubeQuotaExceededError,
        build_youtube_client_for_page,
        link_short_to_related_long,
        list_channel_upload_videos,
        list_future_scheduled_videos,
        queue_pending_upload,
        set_video_thumbnail,
        upload_short,
    )

    mode_n = (mode or "longs").strip().lower()
    if mode_n not in {"longs", "shorts"}:
        raise ValueError("mode must be longs or shorts (independent executions)")

    gate = DailyUploadSafetyGate(limit=limit)

    if process_first and not dry_run:
        run_process(
            source_dir=source_dir,
            episodes=episodes,
            hwaccel=hwaccel,
            hw_encode=hw_encode,
        )

    scan = scan_source_directory(source_dir)
    wanted = _selected(scan, parse_episode_list(episodes))
    state = _load_state()
    _hydrate_state_from_scan(state, scan, wanted)

    last_key = "last_long_scheduled_at" if mode_n == "longs" else "last_short_scheduled_at"
    youtube = None
    yt_last = None
    try:
        youtube = build_youtube_client_for_page(_PAGE, enforce_channel=True)
        scheduled_rows = list_future_scheduled_videos(youtube, max_pages=12)
        if not any(int(row.get("duration_s") or 0) for row in scheduled_rows):
            scheduled_rows = list_channel_upload_videos(youtube, max_pages=8)
        yt_last = latest_future_scheduled_at(scheduled_rows, kind=mode_n)
        if yt_last:
            print(
                f"[Wealth] Latest scheduled {mode_n} on YouTube: {format_slot_pair(yt_last)}"
            )
        else:
            print(
                f"[Wealth] No future scheduled {mode_n} on YouTube - "
                f"first slot is tomorrow or next Tue/Thu."
            )
    except Exception as exc:  # noqa: BLE001
        if not dry_run:
            raise
        print(f"[Wealth] YouTube schedule lookup skipped ({exc})")
        youtube = None
    if dry_run:
        youtube = None
    cursor = None
    if schedule:
        cursor = resolve_first_slot(
            kind=mode_n,
            start_date=start_date,
            last_scheduled_at=max_anchor(state.get(last_key), yt_last),
            time_utc=time_utc,
        )
        print(
            f"[Wealth] Schedule {mode_n} from {format_slot_pair(cursor)} "
            f"({cadence_label(mode_n)})"
        )
        if dry_run:
            print("[Wealth] Dry-run: previewing publishAt dates only (no YouTube upload).")

    table_rows: list[dict[str, Any]] = []
    uploaded_longs: list[int] = []
    uploaded_shorts: list[int] = []
    print(f"[Wealth] Global upload safety cap this run: {gate.limit}")

    if mode_n == "longs":
        for n in wanted:
            if not gate.can_upload():
                gate.notify_halt()
                break
            spec = episode_by_number(n)
            row = _ep_state(state, n)
            if skip_existing_uploads and row.get("long_video_id"):
                print(f"[Wealth] Ep {n:02d} long already uploaded -> {row['long_video_id']}")
                continue
            path = _resolve_upload_path(row, kind="long", use_processed=use_processed)
            if not path:
                print(f"[Wealth] Ep {n:02d} - no long-form file, skip.")
                continue
            title = build_long_title(spec)
            description = build_long_description(spec)
            tags = default_tags(episode=n)
            slot = cursor
            if dry_run:
                table_rows.append(
                    {
                        "type": "LONG",
                        "episode": f"{n:02d}",
                        "title": title,
                        "scheduled": format_slot_pair(slot),
                        "url": "(dry-run)",
                    }
                )
                gate.record_success()
                if schedule and cursor is not None:
                    cursor = advance_slot(cursor, "longs")
                continue
            privacy = "private" if schedule else privacy_long
            print(f"[Wealth] Upload LONG Ep {n:02d}: {title}")
            try:
                video_id, url, used_slot = upload_short(
                    video_path=path,
                    title=title,
                    description=description,
                    tags=tags,
                    privacy_status=privacy,
                    publish_at=slot if schedule else None,
                    page_name=_PAGE,
                    youtube=youtube,
                    skip_playlist=True,
                    preserve_title=True,
                )
            except YouTubeQuotaExceededError as exc:
                queue_pending_upload(
                    page_name=_PAGE,
                    video_path=path,
                    title=title,
                    description=description,
                    tags=tags,
                    privacy_status=privacy,
                    publish_at=slot if schedule else None,
                )
                gate.notify_halt()
                print(f"[Wealth] Quota hit on long Ep {n:02d} - queued. {exc}")
                break
            gate.record_success()
            row["long_video_id"] = video_id
            row["long_url"] = url
            row["long_title"] = title
            if used_slot is not None:
                row["long_publish_at"] = used_slot.isoformat()
                state["last_long_scheduled_at"] = used_slot.isoformat()
                cursor = advance_slot(used_slot, "longs")
            thumb = _resolve_upload_path(row, kind="thumbnail", use_processed=use_processed)
            if thumb:
                try:
                    set_video_thumbnail(youtube, video_id, thumb)
                    row["thumbnail_uploaded"] = True
                except Exception as exc:  # noqa: BLE001
                    _LOG.warning("Thumbnail set failed for ep %s: %s", n, exc)
            _append_library(
                spec=spec,
                kind="long",
                video_path=path,
                video_id=video_id,
                title=title,
                description=description,
            )
            table_rows.append(
                {
                    "type": "LONG",
                    "episode": f"{n:02d}",
                    "title": title,
                    "scheduled": format_slot_pair(used_slot if schedule else None),
                    "url": url,
                }
            )
            uploaded_longs.append(n)
            _save_state(state)
        if not dry_run and uploaded_longs and youtube is not None:
            _sync_playlists(youtube, state, uploaded_longs)

    if mode_n == "shorts":
        quota_hit = False
        for n in wanted:
            if quota_hit or not gate.can_upload():
                if not gate.can_upload() and not quota_hit:
                    gate.notify_halt()
                break
            spec = episode_by_number(n)
            row = _ep_state(state, n)
            short_paths = _resolve_short_upload_paths(row, use_processed=use_processed)
            if not short_paths:
                print(f"[Wealth] Ep {n:02d} - no Short files, skip.")
                continue
            long_id = str(row.get("long_video_id") or "").strip()
            if long_id:
                print(
                    f"[Wealth] Ep {n:02d} parent long {long_id} "
                    f"(published or scheduled) -> relatedVideoId"
                )
            else:
                print(
                    f"[Wealth] No parent long ID found for Ep {n} — "
                    f"publishing Short without relatedVideoId"
                )
            uploaded_meta: list[dict[str, Any]] = [
                item for item in (row.get("short_uploads") or []) if isinstance(item, dict)
            ]
            for i, path in enumerate(short_paths, start=1):
                if not gate.can_upload():
                    gate.notify_halt()
                    quota_hit = True
                    break
                prior = next(
                    (
                        item
                        for item in uploaded_meta
                        if isinstance(item, dict)
                        and item.get("path") == path
                        and item.get("video_id")
                    ),
                    None,
                )
                if skip_existing_uploads and prior:
                    print(
                        f"[Wealth] Ep {n:02d} Short {i}/{len(short_paths)} already "
                        f"uploaded -> {prior['video_id']}"
                    )
                    continue
                hook = short_hook_from_path(path, spec.short_title)
                clip = clip_index_from_path(path) or i
                title = build_short_title(spec, hook=hook, clip_index=clip)
                description = build_short_description(
                    spec,
                    long_video_id=long_id,
                    clip_index=clip,
                    hook=hook,
                )
                tags = default_tags(episode=n, clip=clip, hook=hook)
                slot = cursor
                ep_label = f"{n:02d}.{clip}"
                if dry_run:
                    table_rows.append(
                        {
                            "type": "SHORT",
                            "episode": ep_label,
                            "title": title,
                            "scheduled": format_slot_pair(slot),
                            "url": "(dry-run)",
                        }
                    )
                    gate.record_success()
                    if schedule and cursor is not None:
                        cursor = advance_slot(cursor, "shorts")
                    continue
                privacy = "private" if schedule else privacy_short
                print(
                    f"[Wealth] Upload SHORT Ep {n:02d} [{clip}/{len(short_paths)}] "
                    f"{Path(path).name} -> related={long_id or 'none'}"
                )
                try:
                    video_id, url, used_slot = upload_short(
                        video_path=path,
                        title=title,
                        description=description,
                        tags=tags,
                        privacy_status=privacy,
                        publish_at=slot if schedule else None,
                        page_name=_PAGE,
                        youtube=youtube,
                        skip_playlist=True,
                        preserve_title=True,
                        related_video_id=long_id or None,
                    )
                except YouTubeQuotaExceededError as exc:
                    queue_pending_upload(
                        page_name=_PAGE,
                        video_path=path,
                        title=title,
                        description=description,
                        tags=tags,
                        privacy_status=privacy,
                        publish_at=slot if schedule else None,
                    )
                    print(
                        f"[Wealth] Quota hit on Ep {n:02d} Short {i}/{len(short_paths)} "
                        f"- queued. {exc}"
                    )
                    gate.notify_halt()
                    quota_hit = True
                    break
                gate.record_success()
                uploaded_meta = [item for item in uploaded_meta if item.get("path") != path]
                uploaded_meta.append(
                    {
                        "clip": clip,
                        "path": path,
                        "video_id": video_id,
                        "url": url,
                        "title": title,
                        "publish_at": used_slot.isoformat() if used_slot else "",
                    }
                )
                row["short_uploads"] = uploaded_meta
                row["short_video_ids"] = [
                    item.get("video_id") for item in uploaded_meta if item.get("video_id")
                ]
                if long_id:
                    row["related_video_id"] = long_id
                if used_slot is not None:
                    state["last_short_scheduled_at"] = used_slot.isoformat()
                    cursor = advance_slot(used_slot, "shorts")
                if long_id:
                    try:
                        link_short_to_related_long(youtube, video_id, long_id)
                    except Exception as exc:  # noqa: BLE001
                        _LOG.warning(
                            "relatedVideoId patch failed for short %s: %s", video_id, exc
                        )
                _append_library(
                    spec=spec,
                    kind="short",
                    video_path=path,
                    video_id=video_id,
                    title=title,
                    description=description,
                )
                table_rows.append(
                    {
                        "type": "SHORT",
                        "episode": ep_label,
                        "title": title,
                        "scheduled": format_slot_pair(used_slot if schedule else None),
                        "url": url,
                    }
                )
                uploaded_shorts.append(n)
                _save_state(state)
            if quota_hit:
                break

    _save_state(state)
    _print_schedule_table(table_rows)
    return {
        "mode": mode_n,
        "uploaded_longs": uploaded_longs,
        "uploaded_shorts": uploaded_shorts,
        "scheduled": table_rows,
        "state": str(_STATE_PATH),
    }


def _sync_playlists(youtube, state: dict[str, Any], episodes: list[int]) -> dict[str, str]:
    from agents.posting.youtube_publisher import (
        add_video_to_playlist,
        get_or_create_playlist,
        update_playlist_snippet,
    )

    ids: dict[str, str] = {}
    stored = state.setdefault("playlists", {})
    for pl in PLAYLISTS:
        existing = stored.get(str(pl.act)) if isinstance(stored.get(str(pl.act)), dict) else {}
        pl_id = str((existing or {}).get("id") or "")
        if pl_id:
            update_playlist_snippet(
                youtube,
                pl_id,
                pl.title,
                pl.description,
            )
        else:
            pl_id = get_or_create_playlist(
                youtube,
                pl.title,
                playlist_description=pl.description,
                page_name=_PAGE,
            )
        ids[pl.title] = pl_id
        stored[str(pl.act)] = {
            "title": pl.title,
            "id": pl_id,
        }

    for n in sorted(episodes):
        row = _ep_state(state, n)
        long_id = str(row.get("long_video_id") or "")
        if not long_id:
            continue
        pl = playlist_for_episode(n)
        pl_id = ids[pl.title]
        add_video_to_playlist(
            youtube,
            long_id,
            pl_id,
            position=pl.position_for(n),
        )
        row["playlist_id"] = pl_id
        row["playlist_title"] = pl.title
        row["playlist_position"] = pl.position_for(n)
        print(
            f"[Wealth] Playlist {pl.title} <- Ep {n:02d} "
            f"(position {pl.position_for(n)}) {long_id}"
        )
    return ids


def run_playlists(
    *,
    source_dir: Optional[str | Path] = None,
    episodes: Optional[str] = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    state = _load_state()
    scan = scan_source_directory(source_dir)
    wanted = _selected(scan, parse_episode_list(episodes))
    if not wanted:
        wanted = sorted(int(k) for k in state.get("episodes", {}) if str(k).isdigit())
    if dry_run:
        for n in wanted:
            spec = episode_by_number(n)
            pl = playlist_for_episode(n)
            print(
                f"  [dry-run] Ep {n:02d} -> {pl.title} pos={pl.position_for(n)}  {spec.title_core}"
            )
        return {"episodes": wanted, "dry_run": True}

    from agents.posting.youtube_publisher import build_youtube_client_for_page

    youtube = build_youtube_client_for_page(_PAGE, enforce_channel=True)
    ids = _sync_playlists(youtube, state, wanted)
    _save_state(state)
    return {"playlists": ids, "episodes": wanted}


def run_status(*, source_dir: Optional[str | Path] = None) -> dict[str, Any]:
    scan = scan_source_directory(source_dir)
    state = _load_state()
    print(f"[Wealth] State file: {_STATE_PATH}")
    print(f"[Wealth] Source: {scan.source_dir}")
    print(f"[Wealth] last_long_scheduled_at:  {state.get('last_long_scheduled_at') or '-'}")
    print(f"[Wealth] last_short_scheduled_at: {state.get('last_short_scheduled_at') or '-'}")
    rows = []
    for spec in EPISODES:
        match = scan.matches.get(spec.episode)
        row = (state.get("episodes") or {}).get(str(spec.episode), {})
        rec = {
            "episode": spec.episode,
            "act": spec.act_label,
            "title": spec.title_core,
            "has_long": bool(match and match.has_long),
            "short_count": int(match.short_count) if match else 0,
            "has_thumb": bool(match and match.has_thumbnail),
            "processed_long": bool(row.get("processed_long")),
            "processed_shorts": len(_as_str_list(row.get("processed_shorts"))),
            "long_video_id": row.get("long_video_id") or "",
            "short_video_ids": _as_str_list(row.get("short_video_ids"))
            or _as_str_list(row.get("short_video_id")),
        }
        rows.append(rec)
        mark_long = rec["long_video_id"] or ("file" if rec["has_long"] else "-")
        uploaded_n = len(rec["short_video_ids"])
        short_n = rec["short_count"]
        print(
            f"  Ep {spec.episode:02d} {spec.act_label:<6}  "
            f"long={mark_long:<12} shorts={uploaded_n}/{short_n} uploaded  {spec.short_title}"
        )
    return {"rows": rows, "state": str(_STATE_PATH), "scan": str(_SCAN_PATH)}


_SEO_JSON_PATH = _OUTPUTS / "youtube_seo_pack.json"


def _write_seo_json() -> Path:
    from core.principles_of_wealth.seo_catalog import export_pack, validate_pack

    errors = validate_pack()
    if errors:
        for err in errors:
            _LOG.warning("SEO pack validation: %s", err)
    payload = export_pack()
    _SEO_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
    _SEO_JSON_PATH.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return _SEO_JSON_PATH


def run_metadata(
    *,
    apply: bool = False,
    dry_run: bool = True,
    mode: str = "all",
    episodes: Optional[str] = None,
    source_dir: Optional[str | Path] = None,
) -> dict[str, Any]:
    """Audit, export, and optionally patch YouTube snippets from the SEO pack."""
    from core.principles_of_wealth.seo_catalog import long_pack, short_pack
    from core.principles_of_wealth.scanner import clip_index_from_path, short_hook_from_path

    mode_n = (mode or "all").strip().lower()
    if mode_n not in {"all", "longs", "shorts", "playlists"}:
        raise ValueError("mode must be all, longs, shorts, or playlists")

    json_path = _write_seo_json()
    print(f"[Wealth] SEO pack exported -> {json_path}")

    wanted = parse_episode_list(episodes)
    state = _load_state()
    jobs: list[dict[str, Any]] = []

    if mode_n in {"all", "longs"}:
        for spec in EPISODES:
            if wanted is not None and spec.episode not in wanted:
                continue
            row = (state.get("episodes") or {}).get(str(spec.episode), {})
            video_id = str(row.get("long_video_id") or "")
            pack = long_pack(spec.episode)
            jobs.append(
                {
                    "kind": "LONG",
                    "episode": spec.episode,
                    "clip": 0,
                    "video_id": video_id,
                    "old_title": str(row.get("long_title") or ""),
                    "title": pack["title"],
                    "description": pack["description"],
                    "tags": pack["tags"],
                    "live": bool(video_id),
                }
            )

    if mode_n in {"all", "shorts"}:
        for spec in EPISODES:
            if wanted is not None and spec.episode not in wanted:
                continue
            row = (state.get("episodes") or {}).get(str(spec.episode), {})
            long_id = str(row.get("long_video_id") or "")
            uploads = [
                item for item in (row.get("short_uploads") or []) if isinstance(item, dict)
            ]
            if uploads:
                for item in uploads:
                    path = str(item.get("path") or "")
                    clip = int(item.get("clip") or clip_index_from_path(path) or 0)
                    hook = short_hook_from_path(path, spec.short_title)
                    pack = short_pack(
                        spec.episode, clip, hook=hook, long_video_id=long_id
                    )
                    jobs.append(
                        {
                            "kind": "SHORT",
                            "episode": spec.episode,
                            "clip": clip,
                            "video_id": str(item.get("video_id") or ""),
                            "old_title": str(item.get("title") or ""),
                            "title": pack["title"],
                            "description": pack["description"],
                            "tags": pack["tags"],
                            "live": bool(item.get("video_id")),
                        }
                    )
                continue
            shorts = _as_str_list(row.get("source_shorts")) or _as_str_list(
                row.get("processed_shorts")
            )
            for i, path in enumerate(shorts, start=1):
                clip = clip_index_from_path(path) or i
                hook = short_hook_from_path(path, spec.short_title)
                pack = short_pack(
                    spec.episode, clip, hook=hook, long_video_id=long_id
                )
                jobs.append(
                    {
                        "kind": "SHORT",
                        "episode": spec.episode,
                        "clip": clip,
                        "video_id": "",
                        "old_title": "",
                        "title": pack["title"],
                        "description": pack["description"],
                        "tags": pack["tags"],
                        "live": False,
                    }
                )

    if mode_n in {"all", "playlists"}:
        for pl in PLAYLISTS:
            stored = (state.get("playlists") or {}).get(str(pl.act), {})
            jobs.append(
                {
                    "kind": "PLAYLIST",
                    "episode": pl.act,
                    "clip": 0,
                    "video_id": str((stored or {}).get("id") or ""),
                    "old_title": str((stored or {}).get("title") or ""),
                    "title": pl.title,
                    "description": pl.description,
                    "tags": [],
                    "live": bool((stored or {}).get("id")),
                }
            )

    for job in jobs:
        ep = f"{job['episode']:02d}"
        if job["kind"] == "SHORT" and job.get("clip"):
            ep = f"{job['episode']:02d}.{job['clip']}"
        flag = "LIVE" if job["live"] else "PACK"
        print(
            f"  [{job['kind']:<8}] {ep} {flag}  {str(job['title']).replace(chr(8212), '-')}"
        )
        if job.get("old_title") and job["old_title"] != job["title"]:
            print(
                f"             was: {str(job['old_title']).replace(chr(8212), '-')}"
            )

    live_jobs = [j for j in jobs if j["live"] and j["kind"] != "PLAYLIST"]
    live_playlists = [j for j in jobs if j["live"] and j["kind"] == "PLAYLIST"]
    pack_only = [j for j in jobs if not j["live"]]
    print(
        f"[Wealth] Jobs: {len(jobs)} total | {len(live_jobs)} videos to patch | "
        f"{len(live_playlists)} playlists to patch | {len(pack_only)} pack-only (not uploaded)"
    )

    if dry_run or not apply:
        print("[Wealth] Dry-run only. Pass --apply (without --dry-run) to patch YouTube.")
        return {
            "jobs": len(jobs),
            "live_videos": len(live_jobs),
            "live_playlists": len(live_playlists),
            "json": str(json_path),
            "dry_run": True,
        }

    from agents.posting.youtube_publisher import (
        build_youtube_client_for_page,
        update_playlist_snippet,
        update_video_metadata,
    )

    youtube = build_youtube_client_for_page(_PAGE, enforce_channel=True)
    patched = 0
    failed: list[str] = []

    for job in live_jobs:
        label = f"{job['kind']} ep{job['episode']}"
        if job["kind"] == "SHORT":
            label += f".{job['clip']}"
        try:
            update_video_metadata(
                youtube,
                job["video_id"],
                title=job["title"],
                description=job["description"],
                tags=job["tags"],
            )
            patched += 1
            print(f"[Wealth] Patched {label} -> {job['video_id']}")
            row = _ep_state(state, int(job["episode"]))
            if job["kind"] == "LONG":
                row["long_title"] = job["title"]
            else:
                for item in row.get("short_uploads") or []:
                    if isinstance(item, dict) and item.get("video_id") == job["video_id"]:
                        item["title"] = job["title"]
            _save_state(state)
        except Exception as exc:  # noqa: BLE001
            failed.append(f"{label} {job['video_id']}: {exc}")
            print(f"[Wealth] FAILED {label} {job['video_id']}: {exc}")

    for job in live_playlists:
        try:
            update_playlist_snippet(
                youtube,
                job["video_id"],
                job["title"],
                job["description"],
            )
            patched += 1
            state.setdefault("playlists", {})[str(job["episode"])] = {
                "title": job["title"],
                "id": job["video_id"],
            }
            _save_state(state)
        except Exception as exc:  # noqa: BLE001
            failed.append(f"PLAYLIST act{job['episode']} {job['video_id']}: {exc}")
            print(f"[Wealth] FAILED playlist act {job['episode']}: {exc}")

    print(f"[Wealth] Patched {patched} YouTube objects. Failures: {len(failed)}")
    return {
        "patched": patched,
        "failed": failed,
        "json": str(json_path),
        "dry_run": False,
    }

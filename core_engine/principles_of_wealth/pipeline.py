# -*- coding: utf-8 -*-
"""Orchestrate scan → uniqueness pass → long upload → Short upload → playlists.

Factory order is mandatory:
  1. Process longs + Shorts + thumbs (optional skip)
  2. Upload longs as private/unlisted and store video IDs
  3. Upload Shorts, injecting the matching long ``relatedVideoId``
  4. Insert videos into ACT playlists in chronological episode order
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from avatar_engine.content_library import append_entry
from core_engine.principles_of_wealth.catalog import (
    CHANNEL_ID,
    EPISODES,
    PLAYLISTS,
    ScanResult,
    build_long_description,
    build_long_title,
    build_short_description,
    build_short_title,
    default_tags,
    episode_by_number,
    parse_episode_list,
    playlist_for_episode,
    resolve_processed_directory,
    resolve_source_directory,
    scan_source_directory,
    write_scan_snapshot,
)
from core_engine.principles_of_wealth.fingerprint import process_pair

_LOG = logging.getLogger(__name__)
_ENGINE_ROOT = Path(__file__).resolve().parents[2]
_OUTPUTS = _ENGINE_ROOT / "outputs" / CHANNEL_ID
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
            flags.append("SHORT")
        if match.has_thumbnail:
            flags.append("THUMB")
        print(
            f"  Ep {n:02d} [{spec.act_label}] {spec.title_core}  "
            f"({' + '.join(flags) or 'empty'})"
        )
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

    print(f"[Wealth] Processed folder: {dest_dir}")
    if dry_run:
        for n in wanted:
            m = scan.matches[n]
            print(
                f"  [dry-run] Ep {n:02d}  long={bool(m.has_long)} "
                f"short={bool(m.has_short)} thumb={bool(m.has_thumbnail)}"
            )
        return {"processed_dir": str(dest_dir), "episodes": wanted, "dry_run": True}

    dest_dir.mkdir(parents=True, exist_ok=True)
    for n in wanted:
        m = scan.matches[n]
        row = _ep_state(state, n)
        spec = episode_by_number(n)
        print(f"[Wealth] Uniqueness pass — Ep {n:02d} {spec.short_title}")
        if m.has_long:
            row["source_long"] = m.long_path
            row["processed_long"] = process_pair(
                m.long_path,
                dest_dir,
                kind="long",
                skip_existing=skip_existing,
                hwaccel=hwaccel,
                hw_encode=hw_encode,
            )
        if m.has_short:
            row["source_short"] = m.short_path
            row["processed_short"] = process_pair(
                m.short_path,
                dest_dir,
                kind="short",
                skip_existing=skip_existing,
                hwaccel=hwaccel,
                hw_encode=hw_encode,
            )
        if m.has_thumbnail:
            row["source_thumbnail"] = m.thumbnail_path
            row["processed_thumbnail"] = process_pair(
                m.thumbnail_path,
                dest_dir,
                kind="thumb",
                skip_existing=skip_existing,
            )
        _save_state(state)

    _save_state(state)
    return {"processed_dir": str(dest_dir), "episodes": wanted, "state": str(_STATE_PATH)}


def _resolve_upload_path(row: dict[str, Any], *, kind: str, use_processed: bool) -> str:
    if use_processed:
        processed = str(row.get(f"processed_{kind}") or "")
        if processed and Path(processed).is_file() and Path(processed).stat().st_size > 0:
            return processed
    source = str(row.get(f"source_{kind}") or "")
    return source if source and Path(source).is_file() else ""


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
            row.setdefault("source_short", m.short_path)
        if m.has_thumbnail:
            row.setdefault("source_thumbnail", m.thumbnail_path)


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
    mode: str = "all",
    episodes: Optional[str] = None,
    privacy_long: str = "unlisted",
    privacy_short: str = "unlisted",
    use_processed: bool = True,
    process_first: bool = False,
    skip_existing_uploads: bool = True,
    dry_run: bool = False,
    hwaccel: bool = True,
    hw_encode: bool = False,
) -> dict[str, Any]:
    """Upload longs first, then Shorts linked to those IDs, then playlists."""
    from avatar_engine.publishers.youtube_publisher import (
        YouTubeQuotaExceededError,
        build_youtube_client_for_page,
        link_short_to_related_long,
        queue_pending_upload,
        set_video_thumbnail,
        upload_short,
    )

    mode_n = (mode or "all").strip().lower()
    if mode_n not in {"longs", "shorts", "all"}:
        raise ValueError("mode must be longs | shorts | all")

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
    tags = default_tags()

    if dry_run:
        for n in wanted:
            spec = episode_by_number(n)
            row = _ep_state(state, n)
            long_path = _resolve_upload_path(row, kind="long", use_processed=use_processed)
            short_path = _resolve_upload_path(row, kind="short", use_processed=use_processed)
            print(
                f"  [dry-run] Ep {n:02d} {build_long_title(spec)}\n"
                f"            long={long_path or '(none)'}  short={short_path or '(none)'}"
            )
        return {"mode": mode_n, "episodes": wanted, "dry_run": True}

    youtube = build_youtube_client_for_page(_PAGE, enforce_channel=True)
    uploaded_longs: list[int] = []
    uploaded_shorts: list[int] = []

    if mode_n in {"longs", "all"}:
        for n in wanted:
            spec = episode_by_number(n)
            row = _ep_state(state, n)
            if skip_existing_uploads and row.get("long_video_id"):
                print(f"[Wealth] Ep {n:02d} long already uploaded -> {row['long_video_id']}")
                continue
            path = _resolve_upload_path(row, kind="long", use_processed=use_processed)
            if not path:
                print(f"[Wealth] Ep {n:02d} — no long-form file, skip.")
                continue
            title = build_long_title(spec)
            description = build_long_description(spec)
            print(f"[Wealth] Upload LONG Ep {n:02d}: {title}")
            try:
                video_id, url, _ = upload_short(
                    video_path=path,
                    title=title,
                    description=description,
                    tags=tags,
                    privacy_status=privacy_long,
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
                    privacy_status=privacy_long,
                )
                print(f"[Wealth] Quota hit on long Ep {n:02d} — queued. {exc}")
                break
            row["long_video_id"] = video_id
            row["long_url"] = url
            row["long_title"] = title
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
            uploaded_longs.append(n)
            _save_state(state)

    if mode_n in {"shorts", "all"}:
        for n in wanted:
            spec = episode_by_number(n)
            row = _ep_state(state, n)
            if skip_existing_uploads and row.get("short_video_id"):
                print(f"[Wealth] Ep {n:02d} short already uploaded -> {row['short_video_id']}")
                continue
            path = _resolve_upload_path(row, kind="short", use_processed=use_processed)
            if not path:
                print(f"[Wealth] Ep {n:02d} — no Short file, skip.")
                continue
            long_id = str(row.get("long_video_id") or "")
            if not long_id:
                print(
                    f"[Wealth] Ep {n:02d} Short waiting — upload the long first "
                    f"so relatedVideoId can be set."
                )
                continue
            title = build_short_title(spec)
            description = build_short_description(spec, long_video_id=long_id)
            print(f"[Wealth] Upload SHORT Ep {n:02d} -> related={long_id}")
            try:
                video_id, url, _ = upload_short(
                    video_path=path,
                    title=title,
                    description=description,
                    tags=tags + ["shorts"],
                    privacy_status=privacy_short,
                    page_name=_PAGE,
                    youtube=youtube,
                    skip_playlist=True,
                    preserve_title=True,
                    related_video_id=long_id,
                )
            except YouTubeQuotaExceededError as exc:
                queue_pending_upload(
                    page_name=_PAGE,
                    video_path=path,
                    title=title,
                    description=description,
                    tags=tags + ["shorts"],
                    privacy_status=privacy_short,
                )
                print(f"[Wealth] Quota hit on short Ep {n:02d} — queued. {exc}")
                break
            row["short_video_id"] = video_id
            row["short_url"] = url
            row["short_title"] = title
            row["related_video_id"] = long_id
            try:
                link_short_to_related_long(youtube, video_id, long_id)
            except Exception as exc:  # noqa: BLE001
                _LOG.warning("relatedVideoId patch failed for short %s: %s", video_id, exc)
            _append_library(
                spec=spec,
                kind="short",
                video_path=path,
                video_id=video_id,
                title=title,
                description=description,
            )
            uploaded_shorts.append(n)
            _save_state(state)

    playlist_ids = _sync_playlists(youtube, state, wanted)
    _save_state(state)
    return {
        "mode": mode_n,
        "uploaded_longs": uploaded_longs,
        "uploaded_shorts": uploaded_shorts,
        "playlists": playlist_ids,
        "state": str(_STATE_PATH),
    }


def _sync_playlists(youtube, state: dict[str, Any], episodes: list[int]) -> dict[str, str]:
    from avatar_engine.publishers.youtube_publisher import (
        add_video_to_playlist,
        get_or_create_playlist,
    )

    ids: dict[str, str] = {}
    for pl in PLAYLISTS:
        pl_id = get_or_create_playlist(
            youtube,
            pl.title,
            playlist_description=pl.description,
            page_name=_PAGE,
        )
        ids[pl.title] = pl_id
        state.setdefault("playlists", {})[str(pl.act)] = {
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

    from avatar_engine.publishers.youtube_publisher import build_youtube_client_for_page

    youtube = build_youtube_client_for_page(_PAGE, enforce_channel=True)
    ids = _sync_playlists(youtube, state, wanted)
    _save_state(state)
    return {"playlists": ids, "episodes": wanted}


def run_status(*, source_dir: Optional[str | Path] = None) -> dict[str, Any]:
    scan = scan_source_directory(source_dir)
    state = _load_state()
    print(f"[Wealth] State file: {_STATE_PATH}")
    print(f"[Wealth] Source: {scan.source_dir}")
    rows = []
    for spec in EPISODES:
        match = scan.matches.get(spec.episode)
        row = (state.get("episodes") or {}).get(str(spec.episode), {})
        rec = {
            "episode": spec.episode,
            "act": spec.act_label,
            "title": spec.title_core,
            "has_long": bool(match and match.has_long),
            "has_short": bool(match and match.has_short),
            "has_thumb": bool(match and match.has_thumbnail),
            "processed_long": bool(row.get("processed_long")),
            "long_video_id": row.get("long_video_id") or "",
            "short_video_id": row.get("short_video_id") or "",
        }
        rows.append(rec)
        mark_long = rec["long_video_id"] or ("file" if rec["has_long"] else "—")
        mark_short = rec["short_video_id"] or ("file" if rec["has_short"] else "—")
        print(
            f"  Ep {spec.episode:02d} {spec.act_label:<6}  "
            f"long={mark_long:<12} short={mark_short:<12}  {spec.short_title}"
        )
    return {"rows": rows, "state": str(_STATE_PATH), "scan": str(_SCAN_PATH)}

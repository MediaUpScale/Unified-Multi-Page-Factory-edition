# -*- coding: utf-8 -*-
"""Orchestrate scan -> uniqueness pass -> independent scheduled publish.

Longs and Shorts are separate CLI runs:
  * longs  — one per week, private + publishAt
  * shorts — one per day, private + publishAt, linked to the parent long_video_id
"""
from __future__ import annotations

import json
import logging
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
from core.principles_of_wealth.fingerprint import process_pair, process_shorts
from core.principles_of_wealth.scanner import (
    ScanResult,
    clip_index_from_path,
    scan_source_directory,
    short_hook_from_path,
    write_scan_snapshot,
)
from core.principles_of_wealth.schedule import (
    advance_slot,
    format_slot_pair,
    resolve_first_slot,
)

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
                f"shorts={m.short_count}/10 thumb={bool(m.has_thumbnail)}"
            )
            for i, short_path in enumerate(m.shorts, start=1):
                clip = clip_index_from_path(short_path) or i
                print(f"            [{clip:02d}] {Path(short_path).name}")
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
            row["source_shorts"] = list(m.shorts)
            row["processed_shorts"] = process_shorts(
                list(m.shorts),
                dest_dir,
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


def _as_str_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(v) for v in value if v]
    if isinstance(value, str) and value.strip():
        return [value]
    return []


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
    cursor = None
    if schedule:
        cursor = resolve_first_slot(
            kind=mode_n,
            start_date=start_date,
            last_scheduled_at=state.get(last_key),
            time_utc=time_utc,
        )
        print(
            f"[Wealth] Schedule {mode_n} from {format_slot_pair(cursor)} "
            f"({'weekly' if mode_n == 'longs' else 'daily'})"
        )

    table_rows: list[dict[str, Any]] = []
    youtube = None if dry_run else build_youtube_client_for_page(_PAGE, enforce_channel=True)
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
            long_id = str(row.get("long_video_id") or "")
            if not long_id:
                print(
                    f"[Wealth] Ep {n:02d} Shorts waiting - upload the long first "
                    f"so relatedVideoId can be set (private/scheduled longs are OK)."
                )
                continue
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
                    f"{Path(path).name} -> related={long_id}"
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
                        related_video_id=long_id,
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
                row["related_video_id"] = long_id
                if used_slot is not None:
                    state["last_short_scheduled_at"] = used_slot.isoformat()
                    cursor = advance_slot(used_slot, "shorts")
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

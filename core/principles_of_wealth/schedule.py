# -*- coding: utf-8 -*-
"""Independent drip schedules for Principles of Wealth longs and Shorts.

Longs: two per week on Tuesday and Thursday (18:00 America/New_York).
Shorts: one per day at 18:00 America/New_York (6:00 PM local).

The first slot is the next cadence point after the latest of:
  * the newest future ``publishAt`` already on YouTube
  * ``last_*_scheduled_at`` in local state
  * tomorrow (current UTC date + 1 day)
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from zoneinfo import ZoneInfo

_EASTERN = ZoneInfo("America/New_York")
TUESDAY = 1
THURSDAY = 3
LONG_WEEKDAYS = (TUESDAY, THURSDAY)

SHORT_INTERVAL = timedelta(days=1)
DEFAULT_LONG_TIME_LOCAL = "18:00"
DEFAULT_SHORT_TIME_LOCAL = "18:00"
_SHORT_DURATION_CEILING_S = 60


def parse_hhmm(raw: Optional[str], default: str) -> tuple[int, int]:
    text = (raw or default).strip()
    hour_s, minute_s = text.split(":", 1)
    hour, minute = int(hour_s), int(minute_s)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"Invalid --time-utc '{text}' (expected HH:MM).")
    return hour, minute


def parse_ymd(raw: str) -> date:
    return datetime.strptime(raw.strip(), "%Y-%m-%d").date()


def parse_state_datetime(raw: object) -> Optional[datetime]:
    if isinstance(raw, datetime):
        dt = raw
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    text = str(raw or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        try:
            return datetime.strptime(text[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def tomorrow_utc() -> date:
    return datetime.now(timezone.utc).date() + timedelta(days=1)


def next_long_weekday_on_or_after(day: date) -> date:
    for offset in range(8):
        cand = day + timedelta(days=offset)
        if cand.weekday() in LONG_WEEKDAYS:
            return cand
    return day


def next_long_weekday_after(day: date) -> date:
    return next_long_weekday_on_or_after(day + timedelta(days=1))


def combine_local(day: date, hour: int, minute: int) -> datetime:
    local = datetime(day.year, day.month, day.day, hour, minute, tzinfo=_EASTERN)
    return local.astimezone(timezone.utc)


def combine_utc(day: date, hour: int, minute: int) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=timezone.utc)


def _place(day: date, hour: int, minute: int, *, use_utc: bool) -> datetime:
    return combine_utc(day, hour, minute) if use_utc else combine_local(day, hour, minute)


def latest_future_scheduled_at(
    items: list[dict[str, Any]],
    *,
    kind: str,
) -> Optional[datetime]:
    """Newest future publishAt among *items*, filtered to longs or Shorts."""
    kind_n = kind.strip().lower()
    now = datetime.now(timezone.utc)
    latest: Optional[datetime] = None
    for item in items:
        dt = parse_state_datetime(item.get("publish_at"))
        if dt is None or dt <= now:
            continue
        duration_s = int(item.get("duration_s") or 0)
        is_short = bool(duration_s) and duration_s < _SHORT_DURATION_CEILING_S
        if kind_n == "longs" and is_short:
            continue
        if kind_n == "shorts" and duration_s and not is_short:
            continue
        if latest is None or dt > latest:
            latest = dt
    return latest


def max_anchor(*raws: object) -> Optional[datetime]:
    found = [dt for dt in (parse_state_datetime(raw) for raw in raws) if dt is not None]
    return max(found) if found else None


def ensure_future(slot: datetime, kind: str) -> datetime:
    floor = datetime.now(timezone.utc) + timedelta(minutes=5)
    while slot <= floor:
        slot = advance_slot(slot, kind)
    return slot


def resolve_first_slot(
    *,
    kind: str,
    start_date: Optional[str],
    last_scheduled_at: object,
    time_utc: Optional[str],
) -> datetime:
    """Return the first publishAt for this independent longs/shorts run."""
    kind_n = kind.strip().lower()
    if kind_n == "longs":
        default_local = DEFAULT_LONG_TIME_LOCAL
    elif kind_n == "shorts":
        default_local = DEFAULT_SHORT_TIME_LOCAL
    else:
        raise ValueError("kind must be longs or shorts")

    use_utc = bool(time_utc)
    hour, minute = parse_hhmm(time_utc, default_local)
    last = parse_state_datetime(last_scheduled_at)

    if start_date:
        day = parse_ymd(start_date)
        if kind_n == "longs":
            day = next_long_weekday_on_or_after(day)
    elif last is not None:
        if kind_n == "longs":
            day = next_long_weekday_after(last.astimezone(_EASTERN).date())
        else:
            day = last.astimezone(_EASTERN).date() + timedelta(days=1)
    else:
        day = tomorrow_utc()
        if kind_n == "longs":
            day = next_long_weekday_on_or_after(day)

    slot = _place(day, hour, minute, use_utc=use_utc)
    return ensure_future(slot, kind_n)


def advance_slot(slot: datetime, kind: str) -> datetime:
    kind_n = kind.strip().lower()
    local = slot.astimezone(_EASTERN)
    if kind_n == "longs":
        nxt = next_long_weekday_after(local.date())
        return local.replace(
            year=nxt.year, month=nxt.month, day=nxt.day
        ).astimezone(timezone.utc)
    nxt = local.date() + SHORT_INTERVAL
    return local.replace(
        year=nxt.year, month=nxt.month, day=nxt.day
    ).astimezone(timezone.utc)


def cadence_label(kind: str) -> str:
    if kind.strip().lower() == "longs":
        return "Tue/Thu 18:00 America/New_York (2 per week)"
    return "daily 18:00 America/New_York"


def format_slot_pair(slot: Optional[datetime]) -> str:
    """``2026-09-08 22:00 UTC / 18:00 EDT``."""
    if slot is None:
        return "immediate"
    utc = slot.astimezone(timezone.utc)
    eastern = slot.astimezone(_EASTERN)
    return (
        f"{utc.strftime('%Y-%m-%d %H:%M')} UTC / "
        f"{eastern.strftime('%Y-%m-%d %H:%M %Z')}"
    )


def iso_z(slot: datetime) -> str:
    return slot.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

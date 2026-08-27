# -*- coding: utf-8 -*-
"""Independent drip schedules for Principles of Wealth longs and Shorts.

Longs: one per week (default Thursday 18:00 UTC = 2:00 PM Eastern).
Shorts: one per day (default 16:00 UTC = 12:00 PM Eastern).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

_EASTERN = ZoneInfo("America/New_York")
THURSDAY = 3  # datetime.weekday()

LONG_INTERVAL = timedelta(days=7)
SHORT_INTERVAL = timedelta(days=1)
DEFAULT_LONG_TIME_UTC = "18:00"
DEFAULT_SHORT_TIME_UTC = "16:00"


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


def next_thursday_on_or_after(day: date) -> date:
    offset = (THURSDAY - day.weekday()) % 7
    return day + timedelta(days=offset)


def combine_utc(day: date, hour: int, minute: int) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=timezone.utc)


def ensure_future(slot: datetime, step: timedelta) -> datetime:
    floor = datetime.now(timezone.utc) + timedelta(minutes=5)
    while slot <= floor:
        slot = slot + step
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
        default_time = DEFAULT_LONG_TIME_UTC
        step = LONG_INTERVAL
        snap_thursday = True
    elif kind_n == "shorts":
        default_time = DEFAULT_SHORT_TIME_UTC
        step = SHORT_INTERVAL
        snap_thursday = False
    else:
        raise ValueError("kind must be longs or shorts")

    hour, minute = parse_hhmm(time_utc, default_time)
    last = parse_state_datetime(last_scheduled_at)

    if start_date:
        day = parse_ymd(start_date)
    elif last is not None:
        day = (last + step).date()
    else:
        day = tomorrow_utc()
        if snap_thursday:
            day = next_thursday_on_or_after(day)

    slot = combine_utc(day, hour, minute)
    # Keep the same clock time when we have to skip a past slot.
    return ensure_future(slot, step)


def advance_slot(slot: datetime, kind: str) -> datetime:
    step = LONG_INTERVAL if kind == "longs" else SHORT_INTERVAL
    return slot + step


def format_slot_pair(slot: Optional[datetime]) -> str:
    """``2026-08-27 18:00 UTC / 14:00 EDT``."""
    if slot is None:
        return "immediate"
    utc = slot.astimezone(timezone.utc)
    eastern = slot.astimezone(_EASTERN)
    return (
        f"{utc.strftime('%Y-%m-%d %H:%M')} UTC / "
        f"{eastern.strftime('%H:%M %Z')}"
    )


def iso_z(slot: datetime) -> str:
    return slot.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

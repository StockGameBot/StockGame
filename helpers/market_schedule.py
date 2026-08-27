"""NYSE-aligned update schedule helpers (weekday ET times)."""

from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

NY_TZ = ZoneInfo("America/New_York")


def is_weekday_et(when: datetime | None = None) -> bool:
    """True when ``when`` (default now ET) is Monday–Friday."""
    dt = when or datetime.now(NY_TZ)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=NY_TZ)
    else:
        dt = dt.astimezone(NY_TZ)
    return dt.weekday() < 5


def market_update_times() -> list[time]:
    """9:15 pre-open, 9:30–16:00 every 15 min, 16:15 post-close (ET)."""
    times: list[time] = [
        time(9, 15, tzinfo=NY_TZ),
        time(9, 30, tzinfo=NY_TZ),
    ]
    hour, minute = 9, 45
    while (hour, minute) <= (16, 0):
        times.append(time(hour, minute, tzinfo=NY_TZ))
        minute += 15
        if minute >= 60:
            hour += 1
            minute -= 60
    times.append(time(16, 15, tzinfo=NY_TZ))
    return times


def update_kind_for_time(when: datetime | None = None) -> str:
    """Return ``pre_open``, ``market``, or ``post_close`` for a scheduled update."""
    dt = when or datetime.now(NY_TZ)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=NY_TZ)
    else:
        dt = dt.astimezone(NY_TZ)
    t = dt.time().replace(tzinfo=NY_TZ)
    if t.hour == 9 and t.minute == 15:
        return "pre_open"
    if t.hour == 16 and t.minute == 15:
        return "post_close"
    return "market"


def should_apply_corporate_actions(when: datetime | None = None) -> bool:
    """True at the 9:30 ET open update when staged CA should be applied."""
    dt = when or datetime.now(NY_TZ)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=NY_TZ)
    else:
        dt = dt.astimezone(NY_TZ)
    return dt.hour == 9 and dt.minute == 30


def market_open_datetime(trade_date: date) -> datetime:
    """NYSE regular session open (9:30 ET) on ``trade_date``."""
    return datetime.combine(trade_date, time(9, 30), tzinfo=NY_TZ)

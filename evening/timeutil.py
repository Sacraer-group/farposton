from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Vladivostok")


def now_vl() -> datetime:
    return datetime.now(TZ)


def today_vl() -> date:
    return now_vl().date()


def parse_hhmm(value: str) -> time:
    try:
        return datetime.strptime(value, "%H:%M").time()
    except Exception as exc:
        raise ValueError("invalid time") from exc


def in_window_full(meal_start: time, meal_end: time, open_start: time, open_end: time) -> bool:
    """True when place is open for the whole [meal_start, meal_end) interval."""
    # treat open_end == 00:00 as end-of-day for simplicity in seed
    if open_end == time(0, 0):
        open_end_cmp = time(23, 59, 59)
        meal_end_cmp = meal_end if meal_end != time(0, 0) else time(23, 59, 59)
        return open_start <= meal_start and meal_end_cmp <= open_end_cmp
    return open_start <= meal_start and meal_end <= open_end


def event_local_date(starts_at: datetime) -> date:
    return starts_at.astimezone(TZ).date()


def event_local_time(starts_at: datetime) -> time:
    return starts_at.astimezone(TZ).timetz().replace(tzinfo=None)


def iso_vl(dt: datetime) -> str:
    return dt.astimezone(TZ).isoformat(timespec="seconds")


def combine_local(d: date, t: time) -> datetime:
    return datetime.combine(d, t, tzinfo=TZ)

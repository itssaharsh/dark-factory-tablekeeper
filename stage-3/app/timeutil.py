"""Local/UTC resolution and formatting, including DST (spec §9)."""
from __future__ import annotations

import datetime as dt
import re
from zoneinfo import ZoneInfo

WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
LOCAL_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})$")
DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")


def weekday_key(date: dt.date) -> str:
    return WEEKDAYS[date.weekday()]


def parse_local(value: str) -> dt.datetime | None:
    """Parse a bare `YYYY-MM-DDTHH:MM` string into a naive datetime, or None."""
    m = LOCAL_RE.match(value)
    if not m:
        return None
    year, month, day, hour, minute = (int(g) for g in m.groups())
    try:
        return dt.datetime(year, month, day, hour, minute)
    except ValueError:
        return None


def parse_date(value: str) -> dt.date | None:
    m = DATE_RE.match(value)
    if not m:
        return None
    year, month, day = (int(g) for g in m.groups())
    try:
        return dt.date(year, month, day)
    except ValueError:
        return None


def resolve_local(naive: dt.datetime, tz: ZoneInfo) -> dt.datetime | None:
    """Resolve a naive local datetime to an aware UTC instant.

    Returns None when the local time falls in a spring-forward gap. When it
    falls in a fall-back repeated hour, resolves to the earlier (pre-change)
    occurrence, per spec §9.
    """
    found: dict[int, dt.datetime] = {}
    for fold in (0, 1):
        aware = naive.replace(tzinfo=tz, fold=fold)
        utc = aware.astimezone(dt.timezone.utc)
        back = utc.astimezone(tz).replace(tzinfo=None)
        if back == naive:
            found[fold] = utc
    if not found:
        return None
    return found.get(0, found.get(1))


def format_rfc3339(instant_utc: dt.datetime, tz: ZoneInfo) -> str:
    return instant_utc.astimezone(tz).isoformat(timespec="seconds")


def minutes_of(hhmm: str) -> int:
    hour, minute = hhmm.split(":")
    return int(hour) * 60 + int(minute)


def hhmm_of(total_minutes: int) -> str:
    return f"{total_minutes // 60:02d}:{total_minutes % 60:02d}"

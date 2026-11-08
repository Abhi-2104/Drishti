"""Freshness canary: a row count is not proof of life.

NSE's old insider endpoint returns 20 rows, all from May 2022; a blank-checking provider would
call that a success. Every provider that returns dated rows passes them through check_fresh, so
blank or stale data becomes a typed error (EMPTY / STALE) and never a quiet success.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Iterable

from .envelope import DrishtiError, ErrorKind

# Fixed offset, not zoneinfo: Windows has no tz database unless the tzdata package is installed.
IST = timezone(timedelta(hours=5, minutes=30))

# Formats seen in NSE/BSE/FRED payloads (ported from drishti_local_probe._newest).
_FORMATS = (
    "%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M", "%d-%b-%Y",
    "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d",
    "%d-%m-%Y", "%d/%m/%Y", "%Y%m%d",
    "%d %b %Y",                       # BSE corporate actions / results calendar: '09 Nov 2026'
)


def today_ist() -> date:
    return datetime.now(IST).date()


def parse_date(value: Any) -> date | None:
    """Best-effort date from a date/datetime/string; None if it can't be read."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    s = str(value if value is not None else "").strip()
    if not s:
        return None
    s = s.title()  # NSE sometimes shouts: "06-NOV-2026" -> "06-Nov-2026"
    for fmt in _FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s).date()
    except ValueError:
        return None


def check_fresh(
    rows: Iterable[Any] | None,
    date_key: str | Callable[[Any], Any],
    max_age_days: int,
    label: str,
    *,
    today: date | None = None,
) -> date:
    """Return the newest record date, or raise a typed error.

    EMPTY      no rows at all.
    PARSE_FAIL rows exist but none has a readable date under `date_key`.
    STALE      the newest record is more than `max_age_days` calendar days old (relative to
               `today`, default today in IST). Callers pick the window: allow for weekends and
               holidays, and for quarterly or monthly cadence.
    `date_key` is a dict key, or a function row -> date value.
    """
    rows = list(rows or [])
    if not rows:
        raise DrishtiError(ErrorKind.EMPTY, f"{label}: no rows (silent blank)")
    get = date_key if callable(date_key) else (lambda r: r.get(date_key))
    newest: date | None = None
    for r in rows:
        try:
            d = parse_date(get(r))
        except (KeyError, AttributeError, TypeError):
            d = None
        if d is not None and (newest is None or d > newest):
            newest = d
    if newest is None:
        name = getattr(date_key, "__name__", date_key)
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: {len(rows)} rows but no parseable date in {name!r}")
    age = ((today or today_ist()) - newest).days
    if age > max_age_days:
        raise DrishtiError(
            ErrorKind.STALE,
            f"{label}: newest record {newest.isoformat()} is {age}d old (limit {max_age_days}d, {len(rows)} rows)",
        )
    return newest

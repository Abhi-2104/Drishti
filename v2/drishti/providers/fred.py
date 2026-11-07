"""FRED series as CSV (no API key): US dollar index and Fed balance sheet, plus any series given an explicit freshness window.

Values are as published and are revised by the Fed; "." marks a day with no value and is skipped (never zero-filled).
The freshness canary tests the newest *observation* date, not the file size.
"""
from __future__ import annotations

import re
from datetime import date

from ..core.canary import check_fresh
from ..core.envelope import DrishtiError, ErrorKind, Result, guard
from ..core.fetch import get
from . import _util as u
from .profiles import FRED

SOURCE = "fred"
URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={}"
# series -> max age (calendar days) of the newest observation. Verified against live files on 2026-11-07.
SERIES: dict[str, int] = {
    "DTWEXBGS": 14,  # Nominal Broad US Dollar Index, daily, published with about a week's lag
    "WALCL": 12,     # Fed total assets (Wednesday level), weekly
}
_ID = re.compile(r"^[A-Z0-9_]{1,30}$")
_DATE_COLS = ("observation_date", "DATE")


def parse_fred(body: bytes, series_id: str, *, min_rows: int = 100) -> list[dict]:
    label = f"fred_{series_id}"
    text = u.decode(body)
    first = text.split("\n", 1)[0].split(",")[0].strip()
    if first not in _DATE_COLS:
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: first column is {first!r}, expected one of {_DATE_COLS}")
    raw = u.csv_rows(text, [first, series_id], label)
    out, prev = [], ""
    for r in raw:
        d = u.iso_date(r[first], f"{label}.date")
        if d <= prev:
            raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: dates not strictly increasing at {d}")
        prev = d
        v = r[series_id].strip()
        if v in (".", ""):
            continue
        val = u.num(v, f"{label}.{d}")
        if val is None:
            raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: unreadable value {v!r} on {d}")
        out.append({"date": d, "value": val})
    u.require_rows(out, label, min_rows)
    return out


@guard
def fred_series(series_id: str, *, max_age_days: int | None = None, tail: int = 520, today: date | None = None, store=None) -> Result:
    if not _ID.match(series_id):
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"fred: bad series id {series_id!r}")
    if max_age_days is None:
        if series_id not in SERIES:
            raise DrishtiError(ErrorKind.NOT_IMPLEMENTED, f"fred: {series_id} has no verified freshness window; pass max_age_days")
        max_age_days = SERIES[series_id]
    got = get(URL.format(series_id), FRED, expect="csv")
    rows = parse_fred(got.body, series_id)
    newest = check_fresh(rows, "date", max_age_days, f"fred_{series_id}", today=today)
    if store is not None:
        store.put_blob(f"fred_{series_id}", newest, got, SOURCE)
    data = {"series_id": series_id, "observations": rows[-tail:], "latest": rows[-1], "n_total": len(rows)}
    return Result(data=data, as_of=newest.isoformat(), sources=[got.source(SOURCE)])

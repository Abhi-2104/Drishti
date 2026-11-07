"""CFTC Commitments of Traders, Traders in Financial Futures (futures-only), weekly.

Positions as of Tuesday, released the following Friday. Four trader groups: dealers, asset managers, leveraged funds
(the hedge-fund proxy), other reportables; plus the non-reportable small traders. `net` = long - short, spreading
excluded. The file has no header row, so the layout is pinned by position and verified on every row: the as-of date
appears twice and must agree, and the long/short components must add up to the reported totals.
"""
from __future__ import annotations

import csv
import io
from datetime import date, datetime

from ..core.canary import check_fresh
from ..core.envelope import DrishtiError, ErrorKind, Result, guard
from ..core.fetch import get
from . import _util as u
from .profiles import CFTC

SOURCE = "cftc_tff"
URL = "https://www.cftc.gov/dea/newcot/FinFutWk.txt"
NFIELDS = 87
# contracts worth watching from India: dollar, US equity risk, vol, rates, yen/euro, EM equity
WATCH = {
    "098662": "USD index", "13874A": "E-mini S&P 500", "209742": "Nasdaq mini", "1170E1": "VIX futures",
    "043602": "UST 10Y", "042601": "UST 2Y", "097741": "Japanese yen", "099741": "Euro FX", "244042": "MSCI EM index",
}
_GROUPS = {"dealer": 8, "asset_mgr": 11, "lev_money": 14, "other_rept": 17}  # index of each group's long column; short, spread follow
_CHG = 17  # weekly-change columns sit 17 to the right of the level columns


def _i(f: list[str], idx: int, label: str) -> int:
    try:
        return int(f[idx])
    except ValueError:
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: field {idx} is {f[idx]!r}, not an integer") from None


def _chg(f: list[str], idx: int, label: str) -> int | None:
    """Weekly-change column. '.' = no prior week (a newly listed contract): None, never 0."""
    return None if f[idx] == "." else _i(f, idx, label)


def _close(a: int, b: int) -> bool:
    return abs(a - b) <= max(10, 0.0001 * max(abs(a), abs(b)))  # CFTC's own consolidated rows are off by 1


def _row(f: list[str], label: str) -> dict:
    if len(f) != NFIELDS:
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: {len(f)} fields, expected {NFIELDS}; layout changed? ({f[0][:40]})")
    f = [x.strip() for x in f]
    as_of = u.iso_date(f[2], f"{label}.date")
    if datetime.strptime(f[1], "%y%m%d").date().isoformat() != as_of:
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: dates disagree within the row ({f[1]} vs {as_of}) for {f[0][:40]}")
    oi = _i(f, 7, label)
    long_all = short_all = 0
    out = {"market": f[0], "code": f[3], "exchange": f[4], "as_of": as_of, "open_interest": oi, "oi_change": _chg(f, 7 + _CHG, label),
           "contract_unit": f[-6].strip("()")}
    for g, i in _GROUPS.items():
        lo, sh, sp = (_i(f, i + k, label) for k in range(3))
        dlo, dsh = _chg(f, i + _CHG, label), _chg(f, i + 1 + _CHG, label)
        out[g] = {"long": lo, "short": sh, "spread": sp, "net": lo - sh,
                  "net_change": None if dlo is None or dsh is None else dlo - dsh}
        long_all += lo + sp
        short_all += sh + sp
    tl, ts, nl, ns = (_i(f, k, label) for k in (20, 21, 22, 23))
    if not (_close(long_all, tl) and _close(short_all, ts)):
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: {f[0][:40]} group sums long {long_all}/short {short_all} != reported totals {tl}/{ts}")
    if not (_close(tl + nl, oi) and _close(ts + ns, oi)):
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: {f[0][:40]} reportable + non-reportable != open interest {oi}")
    out["nonreportable"] = {"long": nl, "short": ns, "net": nl - ns}
    return out


def parse_tff(body: bytes, *, min_rows: int = 40) -> list[dict]:
    label = "cftc_tff"
    rows = [_row(f, label) for f in csv.reader(io.StringIO(u.decode(body))) if f]
    u.require_rows(rows, label, min_rows)
    u.single_date(rows, "as_of", label)
    codes = [r["code"] for r in rows]
    if len(set(codes)) != len(codes):
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: duplicate contract codes")
    return rows


@guard
def cftc_tff(*, codes: dict[str, str] | None = None, today: date | None = None, max_age_days: int = 11, store=None) -> Result:
    want = codes or WATCH
    got = get(URL, CFTC, expect="text")
    rows = parse_tff(got.body)
    newest = check_fresh(rows, "as_of", max_age_days, "cftc_tff", today=today)
    by = {r["code"]: r for r in rows}
    missing = sorted(set(want) - set(by))
    if missing:
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"cftc_tff: watched contracts missing from the file: {missing}")
    if store is not None:
        store.put_blob("cftc_tff", newest, got, SOURCE)
    data = [{**by[c], "label": name} for c, name in want.items()]
    return Result(data=data, as_of=newest.isoformat(), sources=[got.source(SOURCE)])

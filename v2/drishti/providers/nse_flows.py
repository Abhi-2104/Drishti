"""FII/FPI and DII cash-market flows (NSE, provisional, INR crore, one trading day).

NSE's endpoint returns only the latest session, so history exists only if we store each day's raw response
(`store=` does that). The figures are published after the close; before that the endpoint still shows the
previous session, which the freshness canary treats as normal for up to `max_age_days`.
"""
from __future__ import annotations

from datetime import date

from ..core.canary import check_fresh
from ..core.envelope import DrishtiError, ErrorKind, Result, guard
from . import _util as u
from . import nse_live

SOURCE = "nse_fiidii"
_CATS = {"FII/FPI": "fii", "DII": "dii"}
_TOL = 0.02  # NSE rounds each figure to 2 decimals


def parse_fii_dii(body: bytes) -> dict:
    label = "fii_dii_cash"
    rows = nse_live.rows_of(body, label)
    u.require_rows(rows, label)
    out: dict = {}
    dates = set()
    for r in rows:
        key = _CATS.get(str(r.get("category", "")).strip())
        if key is None:
            raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: unknown category {r.get('category')!r}")
        if key in out:
            raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: category {key} appears twice")
        vals = {k: u.num(r.get(f"{k}Value"), f"{label}.{key}.{k}") for k in ("buy", "sell", "net")}
        if any(v is None for v in vals.values()):
            raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: {key} has a blank figure: {r}")
        if vals["buy"] < 0 or vals["sell"] < 0:
            raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: {key} negative buy/sell {r}")
        if abs(vals["buy"] - vals["sell"] - vals["net"]) > _TOL:
            raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: {key} buy {vals['buy']} - sell {vals['sell']} != net {vals['net']}")
        out[key] = vals
        dates.add(u.iso_date(r.get("date"), f"{label}.date"))
    if set(out) != set(_CATS.values()):
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: expected FII/FPI and DII, got {sorted(out)}")
    if len(dates) != 1:
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: expected one session date, found {sorted(dates)}")
    return {"date": dates.pop(), **out, "unit": "INR crore", "provisional": True}


@guard
def fii_dii_cash(*, today: date | None = None, max_age_days: int = 5, store=None) -> Result:
    got = nse_live.api_get("fiidiiTradeReact")
    data = parse_fii_dii(got.body)
    newest = check_fresh([data], "date", max_age_days, "fii_dii_cash", today=today)
    if store is not None:
        store.put_blob("fii_dii_cash", newest, got, SOURCE)
    return Result(data=data, as_of=newest.isoformat(), sources=[got.source(SOURCE)])

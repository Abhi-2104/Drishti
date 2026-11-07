"""BSE end-of-day data. Today: the UDiFF cash bhavcopy (plain CSV from www.bseindia.com/download). BSE's
announcements/PIT API and the bulk/block endpoint join this module in later work packages.

The bhavcopy also feeds the security master: its FinInstrmId is the BSE scrip code, next to the ISIN, which is
what lets BSE-only companies (no NSE listing) be resolved at all.
"""
from __future__ import annotations

from datetime import date

from ..core.envelope import DrishtiError, ErrorKind, Result, guard
from ..core.fetch import get
from . import _util as u
from . import udiff
from .profiles import BSE_WWW

SOURCE = "bse"
BHAVCOPY_URL = "https://www.bseindia.com/download/BhavCopy/Equity/BhavCopy_BSE_CM_0_0_0_{ymd}_F_0000.CSV"
MIN_BSE_ROWS = 2000  # 5,059 on 2026-11-06


def parse_bhavcopy(body: bytes, *, min_rows: int = MIN_BSE_ROWS) -> list[dict]:
    """BSE UDiFF bhavcopy CSV -> one row per scrip. `scrip_code` (e.g. '500325') is the BSE join key beside ISIN."""
    label = "bse_bhavcopy"
    rows = udiff.parse_cash(u.decode(body), label, min_rows)
    seen: set[str] = set()
    for r in rows:
        code = r["instrument_id"]
        if not code.isdigit():
            raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: scrip code {code!r} is not numeric; layout changed?")
        if code in seen:
            raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: scrip code {code} appears twice")
        seen.add(code)
        r["scrip_code"] = code
    return rows


@guard
def bhavcopy(d: date, *, store=None) -> Result:
    url = BHAVCOPY_URL.format(ymd=d.strftime("%Y%m%d"))
    rows, got = u.load_parsed(store, "bse_bhavcopy", d, lambda: get(url, BSE_WWW, expect="csv"), parse_bhavcopy, SOURCE)
    if rows[0]["trade_date"] != d.isoformat():
        raise DrishtiError(ErrorKind.STALE, f"bse_bhavcopy: file for {d} contains data for {rows[0]['trade_date']}")
    return Result(data=rows, as_of=d.isoformat(), sources=[got.source(SOURCE)])

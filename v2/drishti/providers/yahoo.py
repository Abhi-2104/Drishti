"""Yahoo Finance daily bars (chart API). Used where v1's calibration is on Yahoo's instruments: the ICE dollar index
(DX-Y.NYB, which FRED does not carry), US 10Y (^TNX), Brent (BZ=F), USD/INR (INR=X), US VIX (^VIX), India VIX and Nifty
as cross-checks. Daily closes only; the newest bar can be the live (unfinished) session, so `complete_only` drops a bar
dated today while that market may still be open.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote

from ..core.canary import check_fresh, today_ist
from ..core.envelope import DrishtiError, ErrorKind, Result, guard
from ..core.fetch import get
from .profiles import YAHOO

SOURCE = "yahoo"
URL = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range={rng}&interval=1d"
# v1 macroRegime.ts tickers, plus the two Indian cross-checks. max age in calendar days of the newest bar.
SYMBOLS = {"DX-Y.NYB": 5, "^TNX": 5, "BZ=F": 5, "INR=X": 5, "^VIX": 5, "^INDIAVIX": 5, "^NSEI": 5, "GC=F": 5, "HG=F": 5}
# Yahoo answers some symbols under another name (live run 8 Nov: INR=X -> USDINR=X). Same instrument, accepted.
ALIASES = {"INR=X": {"USDINR=X"}}
_RANGES = ("1mo", "3mo", "6mo", "1y", "2y", "5y", "10y", "max")


def parse_chart(body: bytes, symbol: str, *, min_rows: int = 5) -> list[dict]:
    label = f"yahoo_{symbol}"
    try:
        doc = json.loads(body)
    except (ValueError, UnicodeDecodeError) as e:
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: not JSON ({type(e).__name__})") from e
    chart = (doc or {}).get("chart") or {}
    if chart.get("error"):
        raise DrishtiError(ErrorKind.NOT_FOUND, f"{label}: {chart['error']}")
    try:
        res = chart["result"][0]
        meta, ts, q = res["meta"], res["timestamp"], res["indicators"]["quote"][0]
    except (KeyError, IndexError, TypeError) as e:
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: unexpected chart layout ({type(e).__name__})") from e
    got_sym = str(meta.get("symbol", "")).upper()
    if got_sym != symbol.upper() and got_sym not in {a.upper() for a in ALIASES.get(symbol, ())}:
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: response is for {meta.get('symbol')!r}")
    tz = timezone(timedelta(seconds=int(meta.get("gmtoffset") or 0)))
    out, prev = [], ""
    for i, t in enumerate(ts):
        c = q["close"][i]
        if c is None:          # Yahoo leaves holes for non-trading days in some series: skip, never zero-fill
            continue
        d = datetime.fromtimestamp(int(t), tz).date().isoformat()
        if d <= prev:
            if d == prev:      # duplicate bar for the same day (seen on live sessions): keep the later one
                out[-1] = {**out[-1], "close": float(c)}
                continue
            raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: dates out of order at {d}")
        prev = d
        if c <= 0:
            raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: non-positive close {c} on {d}")
        out.append({"date": d, "open": q["open"][i], "high": q["high"][i], "low": q["low"][i], "close": float(c)})
    if len(out) < min_rows:
        raise DrishtiError(ErrorKind.EMPTY if not out else ErrorKind.PARSE_FAIL, f"{label}: only {len(out)} bars")
    return out


def complete_bars(rows: list[dict], today: date) -> list[dict]:
    """Drop a bar dated today (the session may still be live) and weekend bars (live run 8 Nov: INR=X carried a Sunday
    bar whose 'move' was a partial weekend quote)."""
    t = today.isoformat()
    return [r for r in rows if r["date"] < t and date.fromisoformat(r["date"]).weekday() < 5]


@guard
def daily(symbol: str, *, rng: str = "6mo", today: date | None = None, max_age_days: int | None = None, store=None) -> Result:
    if rng not in _RANGES:
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"yahoo: bad range {rng!r}")
    if max_age_days is None:
        if symbol not in SYMBOLS:
            raise DrishtiError(ErrorKind.NOT_IMPLEMENTED, f"yahoo: {symbol} has no freshness window; pass max_age_days")
        max_age_days = SYMBOLS[symbol]
    got = get(URL.format(sym=quote(symbol, safe=""), rng=rng), YAHOO, expect="json")
    rows = complete_bars(parse_chart(got.body, symbol), today or today_ist())
    newest = check_fresh(rows, "date", max_age_days, f"yahoo_{symbol}", today=today or today_ist())
    if store is not None:
        from ..store.datasets import persist_macro, prune_incomplete
        prune_incomplete(store, f"yahoo:{symbol}", (today or today_ist()).isoformat())
        persist_macro(store, f"yahoo:{symbol}", [{"date": r["date"], "value": r["close"]} for r in rows], SOURCE)
    return Result(data=rows, as_of=newest.isoformat(), sources=[got.source(SOURCE)])


# --- US single names (L4-17): daily bars with Yahoo's split/dividend-adjusted close, and a quote from the chart meta --
def parse_us(body: bytes, symbol: str, *, min_rows: int = 1) -> dict:
    """OHLCV + adjusted close per day, and the quote fields Yahoo puts in the chart meta."""
    label = f"yahoo_us_{symbol}"
    bars = parse_chart(body, symbol, min_rows=min_rows)
    doc = json.loads(body)
    res = doc["chart"]["result"][0]
    meta, ts, q = res["meta"], res["timestamp"], res["indicators"]["quote"][0]
    adj = ((res["indicators"].get("adjclose") or [{}])[0] or {}).get("adjclose")
    tz = timezone(timedelta(seconds=int(meta.get("gmtoffset") or 0)))
    extra = {}
    for i, t in enumerate(ts):
        if q["close"][i] is None:
            continue
        d = datetime.fromtimestamp(int(t), tz).date().isoformat()
        extra[d] = {"volume": q.get("volume", [None] * len(ts))[i], "adj_close": adj[i] if adj and i < len(adj) else None}
    for b in bars:
        b.update(extra.get(b["date"], {}))
    t = meta.get("regularMarketTime")
    return {"symbol": str(meta.get("symbol", "")).upper(), "name": meta.get("longName") or meta.get("shortName"),
            "currency": meta.get("currency"), "exchange": meta.get("fullExchangeName") or meta.get("exchangeName"),
            "instrument": meta.get("instrumentType"), "last": meta.get("regularMarketPrice"),
            "prev_close": meta.get("chartPreviousClose"), "day_high": meta.get("regularMarketDayHigh"),
            "day_low": meta.get("regularMarketDayLow"), "volume": meta.get("regularMarketVolume"),
            "year_high": meta.get("fiftyTwoWeekHigh"), "year_low": meta.get("fiftyTwoWeekLow"),
            "market_time": datetime.fromtimestamp(int(t), tz).isoformat(timespec="minutes") if t else None, "bars": bars}


_US_SYMBOL = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")


@guard
def us_daily(symbol: str, *, rng: str = "1y", today: date | None = None, max_age_days: int = 7, store=None) -> Result:
    """Daily bars for a US ticker (AAPL, BRK-B). Stored in us_price when a store is given; the bar dated today is dropped
    (the US session may still be open)."""
    sym = (symbol or "").strip().upper()
    if not _US_SYMBOL.match(sym):
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"yahoo: bad US symbol {symbol!r}")
    if rng not in _RANGES:
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"yahoo: bad range {rng!r}")
    got = get(URL.format(sym=quote(sym, safe=""), rng=rng), YAHOO, expect="json")
    d = parse_us(got.body, sym)
    t = (today or today_ist()).isoformat()
    d["bars"] = [b for b in d["bars"] if b["date"] < t]
    newest = check_fresh(d["bars"], "date", max_age_days, f"yahoo_us_{sym}", today=today or today_ist())
    if store is not None:
        from ..store.datasets import persist_us_prices
        persist_us_prices(store, sym, d["bars"])
    return Result(data=d, as_of=newest.isoformat(), sources=[got.source(SOURCE)])


@guard
def us_quote(symbol: str) -> Result:
    sym = (symbol or "").strip().upper()
    if not _US_SYMBOL.match(sym):
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"yahoo: bad US symbol {symbol!r}")
    got = get(URL.format(sym=quote(sym, safe=""), rng="5d"), YAHOO, expect="json")
    d = parse_us(got.body, sym)
    if not d["last"]:
        raise DrishtiError(ErrorKind.EMPTY, f"yahoo_us_{sym}: no price in the answer")
    d.pop("bars")
    return Result(data=d, as_of=(d["market_time"] or "")[:10] or None, sources=[got.source(SOURCE)])

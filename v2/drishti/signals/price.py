"""L4 single-name price context from the stored daily bars: returns, 52-week range, moving averages, ATR, volume, and
strength against the Nifty. Facts for /entry levels and /exit stops; no verdict here."""
from __future__ import annotations

from datetime import date, timedelta
from statistics import mean

from . import history as h
from .base import Component, Signal, ok, unknown


def _bars(store, isin: str, on, days: int = 400) -> list[dict]:
    if not store.query("SELECT 1 AS x FROM sqlite_master WHERE name='price_eod'"):
        return []
    end = h._d(on)
    start = (date.fromisoformat(end) - timedelta(days=days)).isoformat()
    rows = store.query("SELECT * FROM price_eod WHERE isin=? AND trade_date BETWEEN ? AND ? ORDER BY trade_date, exchange", (isin, start, end))
    by: dict[str, dict] = {}
    for r in rows:                     # one bar per day: NSE if present (EQ preferred), else BSE
        cur = by.get(r["trade_date"])
        rank = (r["exchange"] == "NSE", r["series"] == "EQ")
        if cur is None or rank > (cur["exchange"] == "NSE", cur["series"] == "EQ"):
            by[r["trade_date"]] = r
    return [by[d] for d in sorted(by)]


def atr(bars: list[dict], n: int = 14) -> float | None:
    tr = []
    for prev, b in zip(bars, bars[1:]):
        if b["high"] is None or b["low"] is None:
            continue
        pc = prev["close"]
        tr.append(max(b["high"] - b["low"], abs(b["high"] - pc), abs(b["low"] - pc)))
    return round(mean(tr[-n:]), 4) if len(tr) >= n else None


def price_context(store, isin: str, on=None):
    bars = _bars(store, isin, on)
    if not bars:
        return unknown("price_context", f"no stored prices for {isin}; run `drishti backfill prices`")
    last = bars[-1]
    d0, c = last["trade_date"], last["close"]
    closes = [b["close"] for b in bars]

    def ret(n):
        return round((c / closes[-1 - n] - 1) * 100, 2) if len(closes) > n else None

    def dma(n):
        return round(mean(closes[-n:]), 2) if len(closes) >= n else None

    yr = bars[-250:]
    hi, lo = max(b["high"] or b["close"] for b in yr), min(b["low"] or b["close"] for b in yr)
    a = atr(bars)
    vol20 = mean(b["volume"] or 0 for b in bars[-20:]) if len(bars) >= 20 else None
    nifty = dict(h.index_series(store, "Nifty 50", on, days=120))
    rs = None
    common = [b for b in bars[-64:] if b["trade_date"] in nifty]
    if len(common) >= 60:
        rs = round(((common[-1]["close"] / common[0]["close"]) / (nifty[common[-1]["trade_date"]] / nifty[common[0]["trade_date"]]) - 1) * 100, 2)
    missing = []
    if len(bars) < 200:
        missing.append(f"only {len(bars)} sessions stored: 200-day average and 1-year figures need more history")
    if (date.fromisoformat(h._d(on)) - date.fromisoformat(d0)).days > 5:
        missing.append(f"latest price is from {d0} (suspended, illiquid, or prices not updated)")
    d200, d50, d20 = dma(200), dma(50), dma(20)
    trend = ("uptrend (above 50 and 200-day averages)" if d50 and d200 and c > d50 > d200 else
             "downtrend (below 50 and 200-day averages)" if d50 and d200 and c < d50 < d200 else "mixed trend" if d50 else "unknown trend")
    comps = [Component("close", c, d0, f"{last['exchange']} {last['series']}"),
             Component("return 1m / 3m / 6m / 1y %", [ret(21), ret(63), ret(126), ret(250)], d0),
             Component("52-week high / low", [hi, lo], d0, None, None, f"{(c / hi - 1) * 100:.1f}% from high, {(c / lo - 1) * 100:+.1f}% from low"),
             Component("20 / 50 / 200-day average", [d20, d50, d200], d0),
             Component("ATR(14)", a, d0, "average true range: typical daily move, for stop distances", None,
                       f"{a / c * 100:.2f}% of price" if a else ""),
             Component("avg volume 20d", round(vol20) if vol20 else None, d0),
             Component("strength vs Nifty, ~3 months", rs, d0, "% better (+) or worse (-) than the Nifty over the same sessions")]
    return ok(Signal("price_context", d0, None, trend, comps, missing,
                     "From exchange bhavcopy closes (NSE primary, BSE for BSE-only names). Not adjusted for splits/bonuses yet: check "
                     "corporate actions before reading a long-range return.", "price",
                     details={"isin": isin, "close": c, "atr14": a, "dma": {"20": d20, "50": d50, "200": d200}, "high_52w": hi,
                              "low_52w": lo, "sessions": len(bars), "exchange": last["exchange"]}))

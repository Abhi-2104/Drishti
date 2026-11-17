"""Derived trade levels for one stock: an entry zone, a volatility stop and a position size, from stored prices and the
investor's own limits. Everything here is arithmetic on stored readings, labelled `derived`; there is no price target
(a target needs a valuation view, which Drishti does not compute). Reads the profile, so it lives in the decision layer."""
from __future__ import annotations

from .. import signals
from ..core.envelope import DrishtiError, ErrorKind, Result

ATR_MULT = 2.5          # stop distance in typical daily moves (average true range)
RISK_BUDGET_PCT = 1.0   # of the portfolio, lost if the stop is hit (an assumption, shown in the output; change per call)


def derive(store, isin: str, on=None, *, profile=None, risk_budget_pct: float = RISK_BUDGET_PCT, atr_mult: float = ATR_MULT) -> Result:
    r = signals.compute(store, "price_context", on, isin=isin)
    if not r.ok:
        return Result(error=r.error)
    s = r.data
    d = s["details"]
    close, atr, dma50, dma200, lo52, hi52 = d["close"], d["atr14"], d["dma"]["50"], d["dma"]["200"], d["low_52w"], d["high_52w"]
    if not atr or not close:
        return Result(error=DrishtiError(ErrorKind.EMPTY, "not enough stored prices to measure volatility (need 15+ sessions)"))
    uptrend = bool(dma50 and dma200 and close > dma50 > dma200)
    stop = round(close - atr_mult * atr, 2)
    stop_pct = round((close - stop) / close * 100, 2)
    out = {"as_of": s["as_of"], "close": close, "trend": s["label"], "derived": True,
           "stop": stop, "stop_pct_below_close": stop_pct,
           "stop_basis": f"close minus {atr_mult} x ATR(14) of {atr} (typical daily move); breaching it means the move is bigger than normal noise",
           "levels": {"dma20_50_200": [None, dma50, dma200], "low_52w": lo52, "high_52w": hi52}, "target": None,
           "target_note": "no target derived: a target needs a valuation view, which Drishti does not compute"}
    if uptrend:
        out["entry_zone"] = [round(close - atr, 2), close]
        out["entry_note"] = "uptrend: a pull-back of up to one typical daily move from the close, not a chase above it"
    else:
        out["entry_zone"] = None
        out["reclaim_level"] = dma50
        out["entry_note"] = (f"no entry zone while the trend is '{s['label']}': the trend turns on a close above the 50-day average"
                             + (f" ({dma50})" if dma50 else ""))
    max_pos = profile.risk.max_position_pct if profile is not None else None
    risk_size = round(risk_budget_pct / stop_pct * 100, 2) if stop_pct > 0 else None
    size = min(x for x in (risk_size, max_pos) if x is not None) if (risk_size or max_pos) else None
    out["size_pct_of_portfolio"] = size
    out["size_basis"] = (f"risking {risk_budget_pct}% of the portfolio over a {stop_pct}% stop allows {risk_size}%"
                         + (f"; your limit is {max_pos}% per position" if max_pos is not None else "; no max_position_pct set in your profile"))
    out["assumptions"] = [f"risk budget {risk_budget_pct}% of portfolio per idea (an assumption, not from your profile)",
                          "levels are from price history only: they ignore fundamentals, results dates and gaps"]
    if s.get("missing"):
        out["data_gaps"] = s["missing"]
    return Result(data=out, as_of=s["as_of"])

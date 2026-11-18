"""Weekly new-stock candidates: a plain screen on the liquid NSE stocks you do not already own.

WHAT IT DOES. Takes the liquid universe (`universe.liquid`, top 800 by turnover), keeps ordinary equity only (security master
`instrument_class = 'equity'`: ETFs and fund units never enter, because the study that supports these readings used stocks only),
removes what you hold (holdings are reviewed at the exit gate), then keeps stocks that
  * beat the Nifty over ~3 months (the study's "beating Nifty (3m)" reading), and
  * sit within 5% of their 52-week high (the study's "within 5% of 52-week high" reading),
and drops red flags: an insider selling cluster, promoter pledge above the strong-caution level, net deal selling, turnover under
the liquidity floor, or fewer stored sessions than rules-v1 needs. The survivors are ranked by strength vs the Nifty, top N.

WHAT IT DOES NOT PROVE. The two readings cleared the 2017-21 signal study; the 2022-26 half has not been opened, and ranking
among survivors by strength is an extra choice nobody has tested. The output is labelled UNPROVEN until the rules-v2 hold-out
test and the forward log say otherwise. A reading the stored data cannot give is listed as unknown for that stock: it is never
treated as a pass or a fail silently. No sector cap (decided 19 Nov): a hot market can fill the list with one theme.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date

from .. import signals, universe
from ..core.envelope import DrishtiError, ErrorKind, Result
from . import rules

TOP_N = 15
NEAR_HIGH_PCT = 5.0            # within this % of the 52-week high
LIQUIDITY_FLOOR_INR = 2e7      # average daily turnover over the universe window: Rs 2 crore
STATUS = "UNPROVEN"
PARAMS = {"top_n": TOP_N, "near_high_pct": NEAR_HIGH_PCT, "liquidity_floor_inr": LIQUIDITY_FLOOR_INR, "universe_top": universe.TOP,
          "min_sessions": None, "rank": "strength vs Nifty ~3m, descending", "sector_cap": None, "instruments": "equity only"}


def params_version() -> str:
    p = {**PARAMS, "min_sessions": rules.load()[0]["data"]["min_sessions"]}
    return "candidates-v1+" + hashlib.sha1(json.dumps(p, sort_keys=True).encode()).hexdigest()[:8]


def _sig(store, name, on, **kw):
    r = signals.compute(store, name, on, **kw)
    return r.data if r.ok else None


def _comp(sig, label):
    return next((c["value"] for c in sig["components"] if c["input"] == label), None) if sig else None


def latest_session(store) -> str | None:
    r = store.query("SELECT MAX(trade_date) AS d FROM price_eod WHERE exchange='NSE'") if store.query(
        "SELECT 1 AS x FROM sqlite_master WHERE name='price_eod'") else []
    return r[0]["d"] if r and r[0]["d"] else None


def _avg_turnover(store, isins: list[str], on: str) -> dict[str, float]:
    """Average daily NSE EQ turnover over the same window `universe.liquid` ranks on."""
    from datetime import timedelta
    lo = (date.fromisoformat(on) - timedelta(days=universe.WINDOW_DAYS)).isoformat()
    out: dict[str, float] = {}
    for i in range(0, len(isins), 400):
        chunk = isins[i:i + 400]
        rows = store.query(f"SELECT isin, AVG(turnover) AS t FROM price_eod WHERE exchange='NSE' AND series='EQ' AND trade_date BETWEEN ? AND ? "
                           f"AND isin IN ({','.join('?' * len(chunk))}) GROUP BY isin", (lo, on, *chunk))
        out.update({r["isin"]: r["t"] for r in rows})
    return out


def screen(store, on: str | None = None, *, top_n: int = TOP_N) -> Result:
    on = on or latest_session(store)
    if not on:
        return Result(error=DrishtiError(ErrorKind.EMPTY, "candidates: no stored NSE prices"))
    pool = universe.liquid(store, on)
    if not pool:
        return Result(error=DrishtiError(ErrorKind.EMPTY, f"candidates: no liquid universe as of {on}"))
    eq = {r["isin"]: r for r in store.query(
        f"SELECT isin, name, nse_symbol FROM security WHERE instrument_class='equity' AND isin IN ({','.join('?' * len(pool))})", tuple(pool))}
    if not eq:
        return Result(error=DrishtiError(ErrorKind.EMPTY, "candidates: none of the liquid names is an equity in the security master; run `drishti run security_master`"))
    from ..portfolio import holdings
    held = {h["isin"] for h in holdings.latest_snapshot(store)}
    min_sessions = rules.load()[0]["data"]["min_sessions"]
    turnover = _avg_turnover(store, list(eq), on)
    d = date.fromisoformat(on)
    drops: dict[str, int] = {}

    def drop(why: str) -> None:
        drops[why] = drops.get(why, 0) + 1
    passers = []
    for isin, sec in sorted(eq.items()):
        if isin in held:
            drop("already held")
            continue
        if turnover.get(isin, 0) < LIQUIDITY_FLOOR_INR:
            drop("below liquidity floor")
            continue
        pc = _sig(store, "price_context", d, isin=isin)
        if pc is None or (pc["details"].get("sessions") or 0) < min_sessions:
            drop(f"fewer than {min_sessions} stored sessions")
            continue
        rs = _comp(pc, "strength vs Nifty, ~3 months")
        close, high = pc["details"].get("close"), pc["details"].get("high_52w")
        if rs is None or not high or not close:
            drop("strength or 52-week high unknown")
            continue
        if rs <= 0:
            drop("lagging the Nifty (3m)")
            continue
        gap = (close / high - 1) * 100
        if gap < -NEAR_HIGH_PCT:
            drop(f"more than {NEAR_HIGH_PCT:g}% below the 52-week high")
            continue
        passers.append({"isin": isin, "symbol": sec["nse_symbol"], "name": sec["name"], "close": close, "rs_3m": rs, "gap_to_52w_high_pct": round(gap, 2),
                        "avg_turnover_inr": round(turnover[isin])})
    kept = []
    for c in sorted(passers, key=lambda x: (-x["rs_3m"], x["isin"])):
        unknown, flags = [], []
        ins = _sig(store, "insider_cluster", d, isin=c["isin"], symbol=c["symbol"])
        if ins is None:
            unknown.append("insider_cluster")
        elif any("selling cluster" in f for f in ins["details"].get("flags", [])):
            flags.append("insider selling cluster")
        pl = _sig(store, "pledge", d, isin=c["isin"])
        if pl is None:
            unknown.append("pledge")
        elif any("strong caution" in f for f in pl["details"].get("flags", [])):
            flags.append("promoter pledge above the strong-caution level")
        df = _sig(store, "deal_flow", d, isin=c["isin"], symbol=c["symbol"])
        if df is None:
            unknown.append("deal_flow")
        elif df["label"].startswith("net deal selling"):
            flags.append("net deal selling")
        if flags:
            drop("red flag: " + flags[0])
            continue
        kept.append({**c, "unknown": unknown})
    top = [{"rank": i, **c} for i, c in enumerate(kept[:top_n], 1)]
    return Result(data={"as_of": on, "status": STATUS, "params_version": params_version(), "params": {**PARAMS, "min_sessions": min_sessions},
                        "universe": len(pool), "equities": len(eq), "passed_readings": len(passers), "after_red_flags": len(kept),
                        "dropped": dict(sorted(drops.items(), key=lambda kv: -kv[1])), "candidates": top,
                        "note": "UNPROVEN: both readings cleared the 2017-21 study only; 2022-26 not yet opened; ranking among survivors is untested."},
                  as_of=on)

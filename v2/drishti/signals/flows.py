"""Flow and positioning signals: cash FII/DII, index options (PCR, max pain, OI walls), liquidity, credit spreads.

Several of these have no thresholds decided yet (your brother's v0 guides hold them). Until then they report the
measurements with direction labels and provisional=true, so a model reads them as context, not as calibrated scores.
"""
from __future__ import annotations

from datetime import date

from . import history as h
from .base import Component, Signal, ok, params, unknown


# --- FII / DII cash (L2-1) ------------------------------------------------------------------------------
def fii_dii(store, on=None):
    p, ver = params("fii_dii")
    rows = h.fii_dii_days(store, on, p["long_window"])
    if not rows:
        return unknown("fii_dii", "no FII/DII cash data stored; run `drishti run eod_flows`")
    d0 = rows[0]["trade_date"]

    def streak(key):
        sign = 1 if rows[0][key] > 0 else -1 if rows[0][key] < 0 else 0
        n = 0
        for r in rows:
            if (r[key] > 0) - (r[key] < 0) != sign or sign == 0:
                break
            n += 1
        return sign * n

    def window(key, n):
        return round(sum(r[key] for r in rows[:n]), 2) if len(rows) >= n else None

    fs, ds_ = streak("fii_net"), streak("dii_net")
    f5, f20, d5, d20 = window("fii_net", p["short_window"]), window("fii_net", p["long_window"]), window("dii_net", p["short_window"]), window("dii_net", p["long_window"])
    missing = []
    if f20 is None:
        missing.append(f"{p['long_window']}-session totals (only {len(rows)} sessions stored)")
    if f5 is None:
        missing.append(f"{p['short_window']}-session totals")
    basis = f20 if f20 is not None else f5 if f5 is not None else rows[0]["fii_net"]
    label = ("FII buying" if basis > 0 else "FII selling") + (", DII " + ("buying" if (d20 if d20 is not None else d5 if d5 is not None else rows[0]["dii_net"]) > 0 else "selling"))
    comps = [Component("FII net today", rows[0]["fii_net"], d0, "INR crore"), Component("DII net today", rows[0]["dii_net"], d0, "INR crore"),
             Component("FII streak", fs, d0, "+n = n buying days in a row, -n selling"), Component("DII streak", ds_, d0),
             Component(f"FII {p['short_window']}d", f5, d0, "INR crore"), Component(f"FII {p['long_window']}d", f20, d0, "INR crore"),
             Component(f"DII {p['short_window']}d", d5, d0, "INR crore"), Component(f"DII {p['long_window']}d", d20, d0, "INR crore")]
    return ok(Signal("fii_dii", d0, None, label, comps, missing,
                     "NSE provisional cash-market figures, INR crore. Per scoring.md, institutional flow is confirmation, not thesis.",
                     ver, provisional=True, details={"sessions": len(rows)}))


# --- index options (L2-4) ----------------------------------------------------------------------------------
def max_pain(chain: list[dict]) -> float | None:
    """Strike at which option writers pay out the least at expiry (sum over calls of OI*max(0, K-s) + puts OI*max(0, s-K))."""
    strikes = sorted({r["strike"] for r in chain})
    if not strikes:
        return None
    calls = [(r["strike"], r["oi"] or 0) for r in chain if r["option_type"] == "CE"]
    puts = [(r["strike"], r["oi"] or 0) for r in chain if r["option_type"] == "PE"]
    pain = {k: sum(oi * max(0.0, k - s) for s, oi in calls) + sum(oi * max(0.0, s - k) for s, oi in puts) for k in strikes}
    return min(pain, key=lambda k: (pain[k], k))


def _pcr_near(rows: list[dict], d: str) -> float | None:
    exp = sorted({r["expiry"] for r in rows if r["expiry"] >= d})
    if not exp:
        return None
    near = [r for r in rows if r["expiry"] == exp[0]]
    ce = sum(r["oi"] or 0 for r in near if r["option_type"] == "CE")
    pe = sum(r["oi"] or 0 for r in near if r["option_type"] == "PE")
    return pe / ce if ce and pe else None


def pcr_history(store, symbol: str, before: str, n: int = 250) -> list[float]:
    rows = store.query("SELECT trade_date, expiry, option_type, SUM(oi) AS oi FROM index_option_eod WHERE symbol=? AND trade_date < ? "
                       "AND trade_date >= date(?, '-400 days') GROUP BY trade_date, expiry, option_type", (symbol, before, before))
    by: dict[str, list] = {}
    for r in rows:
        by.setdefault(r["trade_date"], []).append(r)
    out = [x for d in sorted(by)[-n:] if (x := _pcr_near(by[d], d)) is not None]
    return out


def options_flow(store, on=None, symbol: str = "NIFTY"):
    p, ver = params("options_flow")
    d, rows = h.option_chain(store, symbol, on)
    if not rows:
        return unknown(f"options_flow:{symbol}", "no index-option rows stored; run `drishti run eod_bhavcopy` or the fo_index backfill")
    expiries = sorted({r["expiry"] for r in rows if r["expiry"] >= d})
    if not expiries:
        return unknown(f"options_flow:{symbol}", f"no live expiry on {d}")
    near = [r for r in rows if r["expiry"] == expiries[0]]
    ce = sum(r["oi"] or 0 for r in near if r["option_type"] == "CE")
    pe = sum(r["oi"] or 0 for r in near if r["option_type"] == "PE")
    ce_all = sum(r["oi"] or 0 for r in rows if r["option_type"] == "CE" and r["expiry"] >= d)
    pe_all = sum(r["oi"] or 0 for r in rows if r["option_type"] == "PE" and r["expiry"] >= d)
    if not ce or not pe:
        return unknown(f"options_flow:{symbol}", f"empty open interest on one side for {expiries[0]}")
    pcr, pcr_all = round(pe / ce, 3), round(pe_all / ce_all, 3) if ce_all else None
    mp = max_pain(near)
    spot = next((r["underlying"] for r in near if r["underlying"]), None)
    top = lambda t: sorted((r for r in near if r["option_type"] == t), key=lambda r: -(r["oi"] or 0))[:p["walls"]]  # noqa: E731
    walls_ce = [{"strike": r["strike"], "oi": r["oi"], "oi_change": r["oi_change"]} for r in top("CE")]
    walls_pe = [{"strike": r["strike"], "oi": r["oi"], "oi_change": r["oi_change"]} for r in top("PE")]
    label = "put-heavy" if pcr > p["pcr_bullish_above"] else "call-heavy" if pcr < p["pcr_bearish_below"] else "balanced"
    from .market import percentile_of
    hist = pcr_history(store, symbol, d)
    pctl = percentile_of(pcr, hist)
    comps = [Component("PCR (nearest expiry, OI)", pcr, d, f">{p['pcr_bullish_above']} put-heavy, <{p['pcr_bearish_below']} call-heavy (provisional)"),
             Component("PCR percentile vs own history", pctl, d, f"share of the last {len(hist)} sessions at or below today"),
             Component("PCR (all expiries, OI)", pcr_all, d), Component("max pain", mp, d, "nearest expiry"),
             Component("underlying", spot, d), Component("call OI walls (resistance)", [w["strike"] for w in walls_ce], d),
             Component("put OI walls (support)", [w["strike"] for w in walls_pe], d)]
    return ok(Signal(f"options_flow:{symbol}", d, pcr, label, comps, [],
                     f"From the NSE EOD F&O bhavcopy; nearest expiry {expiries[0]}. PCR thresholds are a common convention, not yet "
                     "your brother's v0 numbers.", ver, provisional=bool(p.get("provisional", True)),
                     details={"expiry": expiries[0], "expiries": expiries[:4], "pcr_percentile": pctl, "call_walls": walls_ce, "put_walls": walls_pe,
                              "max_pain_vs_spot_pct": round((mp - spot) / spot * 100, 2) if mp and spot else None}))


# --- liquidity (L1-4) -------------------------------------------------------------------------------------
def liquidity(store, on=None):
    p, ver = params("liquidity")
    walcl = h.macro_points(store, "fred:WALCL", on, p["walcl_change_weeks"] + 1)
    m2 = h.macro_points(store, "fred:M2SL", on, 13)
    comps, missing, dirs = [], [], []
    as_of = None
    if len(walcl) > p["walcl_change_weeks"]:
        chg = (walcl[0][1] / walcl[-1][1] - 1) * 100
        comps.append(Component(f"Fed balance sheet {p['walcl_change_weeks']}w change", round(chg, 2), walcl[0][0], "%"))
        dirs.append(chg)
        as_of = walcl[0][0]
    else:
        missing.append("Fed balance sheet history (WALCL)")
    if len(m2) >= 13:
        yoy = (m2[0][1] / m2[12][1] - 1) * 100
        comps.append(Component("US M2 YoY", round(yoy, 2), m2[0][0], "%"))
        dirs.append(yoy)
        as_of = max(filter(None, [as_of, m2[0][0]]))
    else:
        missing.append("US M2 (M2SL)")
    if not dirs:
        return unknown("liquidity", "neither WALCL nor M2 is stored; run the macro backfill")
    label = "expanding" if all(x > 0 for x in dirs) else "contracting" if all(x < 0 for x in dirs) else "mixed"
    return ok(Signal("liquidity", as_of, None, f"US liquidity {label}", comps, missing,
                     "v0 calls global liquidity the master driver. Direction only until thresholds are decided.", ver, provisional=True))


# --- credit spreads + yield curve (L1-4) ------------------------------------------------------------------
def credit_spreads(store, on=None):
    p, ver = params("credit_spreads")
    hy = h.macro_points(store, "fred:BAMLH0A0HYM2", on, p["window"] + 1)
    curve = h.macro_points(store, "fred:T10Y2Y", on, 1)
    if len(hy) <= p["window"]:
        return unknown("credit_spreads", "too little HY spread history; run the macro backfill")
    level, chg_bp = hy[0][1], round((hy[0][1] - hy[-1][1]) * 100, 1)
    if level >= p["stress_level_pct"] or chg_bp >= p["widening_bp"]:
        label = "credit stress building"
    elif chg_bp <= -p["widening_bp"]:
        label = "credit easing"
    else:
        label = "credit calm"
    comps = [Component("US high-yield OAS", level, hy[0][0], f">= {p['stress_level_pct']}% stress (provisional)"),
             Component(f"{p['window']}-session change", chg_bp, hy[0][0], f"bp; >= +{p['widening_bp']} widening (provisional)")]
    missing = []
    if curve:
        comps.append(Component("US 10Y-2Y", curve[0][1], curve[0][0], "below 0 = inverted"))
    else:
        missing.append("US 10Y-2Y curve")
    return ok(Signal("credit_spreads", hy[0][0], chg_bp, label, comps, missing,
                     "HY spreads cross-check whether a risk-off move is genuine or noise (roadmap Tier 1).", ver, provisional=True))

"""Market-level signals ported from v1 (participant OI, sector rotation, macro regime) plus the Midcap150/Nifty ratio.

Ports are line-for-line in method and thresholds (see params.toml source tags). Differences from v1 are only where
v2 reads end-of-day stored data instead of live quotes, and each such difference is written in the signal's note.
"""
from __future__ import annotations

from statistics import mean

from . import history as h
from .base import Component, Signal, ok, params, unknown

# --- participant OI (v1: participantOi.ts) ------------------------------------------------------------
PLAYERS = ("FII", "DII", "Pro", "Client")
_SEG_COLS = {"Index Futures": ("fut_idx_long", "fut_idx_short"), "Call Options": ("opt_idx_call_long", "opt_idx_call_short"),
             "Put Options": ("opt_idx_put_long", "opt_idx_put_short")}
_SCORE = {"bull-strong": 2, "bull-mild": 1, "neu": 0, "bear-mild": -1, "bear-strong": -2}


def _label(net: float, change: float, seg: str) -> tuple[str, str]:
    # v1: for puts, net SHORT is bullish; for futures/calls, net LONG is bullish.
    bullish_net = net < 0 if seg == "Put Options" else net > 0
    bullish_chg = change < 0 if seg == "Put Options" else change > 0
    if bullish_net and bullish_chg:
        return "Strong Bullish", "bull-strong"
    if bullish_net and not bullish_chg:
        return "Mild Bullish", "bull-mild"
    if not bullish_net and not bullish_chg:
        return "Strong Bearish", "bear-strong"
    return "Mild Bearish", "bear-mild"


def percentile_of(value: float, history: list[float]) -> float | None:
    """Share of past readings at or below `value` (0..100). None with fewer than 20 past readings."""
    if value is None or len(history) < 20:
        return None
    return round(100 * sum(1 for x in history if x <= value) / len(history), 1)


def participant_oi(store, on=None, *, with_history: bool = True):
    p, ver = params("participant_oi")
    days = h.participant_days(store, on, 2)
    if not days:
        return unknown("participant_oi", "no participant-OI data stored up to this date")
    (d0, today), prior = days[0], (days[1] if len(days) > 1 else None)
    missing = [] if prior else ["previous trading day (change is taken as 0, as in v1)"]
    nets = lambda rows, who, seg: (rows[who][_SEG_COLS[seg][0]] - rows[who][_SEG_COLS[seg][1]]) if who in rows else None  # noqa: E731
    stance, scores, comps = {}, {}, []
    for who in PLAYERS:
        if who not in today:
            return unknown("participant_oi", f"{who} row missing on {d0}")
        stance[who], total = [], 0
        for seg in p["segments"]:
            net = nets(today, who, seg)
            prev = nets(prior[1], who, seg) if prior else None
            change = net - (prev if prev is not None else net)
            lab, cls = _label(net, change, seg)
            total += _SCORE[cls]
            stance[who].append({"segment": seg, "net": net, "change": change, "label": lab})
        scores[who] = total
        comps.append(Component(who, total, d0, "sum of 3 segments, each -2..+2",
                               0 if who == "DII" else (-total if who == "Client" else total),
                               "; ".join(f"{s['segment']}: {s['label']}" for s in stance[who])))
    smart = (scores["FII"] + scores["Pro"]) - scores["Client"]
    thr = p["strong_threshold"]
    if smart >= thr:
        interp = f"Strong bullish: institutions (FII+Pro) positioned long while retail (Client) is short (score +{smart})."
        label = "strong bullish"
    elif smart <= -thr:
        interp = f"Strong bearish: institutions positioned short while retail is long (score {smart})."
        label = "strong bearish"
    else:
        interp = f"No strong smart-money vs retail divergence (score {smart})."
        label = "no strong divergence"
    pctl, hist_n = None, 0
    if with_history:   # where today sits in its own past: the read that discriminates when the raw score is persistently one-sided
        past = [r["trade_date"] for r in store.query("SELECT DISTINCT trade_date FROM participant_oi WHERE trade_date < ? "
                                                    "ORDER BY trade_date DESC LIMIT ?", (d0, p.get("history_sessions", 250)))]
        hist = [x.data["score"] for x in (participant_oi(store, d, with_history=False) for d in past) if x.ok]
        hist_n = len(hist)
        pctl = percentile_of(smart, hist)
        comps.append(Component("percentile vs own history", pctl, d0, f"share of the last {hist_n} sessions at or below today's score", None,
                               "unknown (under 20 sessions stored)" if pctl is None else
                               f"today's {smart} is at the {pctl:.0f}th percentile (low = more bearish than usual)"))
    return ok(Signal("participant_oi", d0, smart, label, comps, missing,
                     "FII/Pro = smart money (usually right); Client = retail (contrarian); DII ignored for F&O direction. "
                     "Change vs prior trading day. Score = (FII + Pro) - Client, range -18..+18.",
                     ver, details={"interpretation": interp, "participant_scores": scores, "stance": stance,
                                   "prior_date": prior[0] if prior else None, "percentile": pctl, "history_sessions": hist_n}))


# --- sector rotation (v1: sectorRotation.ts) ------------------------------------------------------------
def sector_rotation(store, on=None):
    p, ver = params("sector_rotation")
    bench = h.index_series(store, p["benchmark"], on, days=p["history_days"])
    if len(bench) < 25:
        return unknown("sector_rotation", f"only {len(bench)} benchmark closes in the last {p['history_days']} days (need 25); run the backfill")
    dates = [d for d, _ in bench]
    bmap = dict(bench)
    w, rows, unavailable = p["ma_window"], [], []
    for name, idx in p["sectors"].items():
        sec = dict(h.index_series(store, idx, on, days=p["history_days"]))
        rs = [sec[d] / bmap[d] for d in dates if d in sec and bmap[d]]
        if len(rs) < p["min_points"]:
            unavailable.append(name)
            continue
        ma = lambda i: mean(rs[i - (w - 1): i + 1])  # noqa: E731
        last = len(rs) - 1
        ma_last, ma_prev = ma(last), ma(last - 1)
        mom = (rs[last] - ma_last) / ma_last * 100
        mom_prev = (rs[last - 1] - ma_prev) / ma_prev * 100
        accel = mom - mom_prev
        quadrant = ("Leading" if accel > 0 else "Weakening") if mom >= 0 else ("Improving" if accel > 0 else "Lagging")
        crossed = any(i >= w and rs[i] > ma(i) and rs[i - 1] <= ma(i - 1) for i in range(last - (p["alert_lookback"] - 1), last + 1))
        rows.append({"name": name, "index": idx, "momentum": round(mom, 2), "acceleration": round(accel, 2),
                     "quadrant": quadrant, "rotation_alert": crossed})
    rows.sort(key=lambda r: -r["momentum"])
    if not rows:
        return unknown("sector_rotation", "no sector had enough history")
    leading = [r["name"] for r in rows if r["quadrant"] == "Leading"]
    improving = [r["name"] for r in rows if r["quadrant"] == "Improving"]
    comps = [Component(r["name"], r["quadrant"], dates[-1], f"RS vs {w}d MA", None,
                       f"momentum {r['momentum']:+.2f}%, accel {r['acceleration']:+.2f}" + (" , fresh cross" if r["rotation_alert"] else ""))
             for r in rows]
    return ok(Signal("sector_rotation", dates[-1], None, f"leading: {', '.join(leading) or 'none'}; improving: {', '.join(improving) or 'none'}",
                     comps, [f"{u} (too little history)" for u in unavailable],
                     "RS = sector index / Nifty 50 (NSE official closes; v1 used Yahoo). Leading = strong+accelerating; Improving = weak but "
                     "turning up (early rotation); Weakening = strong but fading; Lagging = weak+falling. rotation_alert flags a fresh "
                     "cross above the 20-day average.", ver, details={"sectors": rows, "unavailable": unavailable}))


# --- Midcap150 / Nifty ratio (L3-2) ----------------------------------------------------------------------
def midcap_ratio(store, on=None):
    p, ver = params("midcap_ratio")
    num = dict(h.index_series(store, p["numerator"], on, days=200))
    den = h.index_series(store, p["denominator"], on, days=200)
    ratio = [(d, num[d] / c) for d, c in den if d in num and c]
    need = max(p["ma_window"], p["change_window"]) + 1
    if len(ratio) < need:
        return unknown("midcap_ratio", f"{len(ratio)} aligned closes, need {need}; run the backfill")
    d, last = ratio[-1]
    ma = mean(v for _, v in ratio[-p["ma_window"]:])
    vs_ma = (last - ma) / ma * 100
    chg = (last / ratio[-1 - p["change_window"]][1] - 1) * 100
    label = "midcaps outperforming" if vs_ma > 0 and chg > 0 else "midcaps lagging" if vs_ma < 0 and chg < 0 else "mixed"
    return ok(Signal("midcap_ratio", d, round(chg, 2), label,
                     [Component("ratio", round(last, 5), d), Component(f"vs {p['ma_window']}d average", round(vs_ma, 2), d, "% above (+) / below (-)"),
                      Component(f"{p['change_window']}-session change", round(chg, 2), d, "%")], [],
                     "Nifty Midcap 150 / Nifty 50 from NSE closes. Outperforming = above its 20-day average AND up over the trend window.",
                     ver, provisional=True, details={"series_tail": ratio[-p["change_window"]:]}))


# --- macro regime (v1: macroRegime.ts) ------------------------------------------------------------------
# indicator -> (series in macro_series / index_close, how to read it)
MACRO_INPUTS = {"DXY": "yahoo:DX-Y.NYB", "US_10Y": "yahoo:^TNX", "Brent_Crude": "yahoo:BZ=F", "USD_INR": "yahoo:INR=X",
                "US_VIX": "yahoo:^VIX", "Nifty_50": "index:Nifty 50", "India_VIX": "index:India VIX"}


def _point(store, series: str, on, n=2):
    if series.startswith("index:"):
        pts = h.index_series(store, series[6:], on, days=20)
        return list(reversed(pts[-n:]))
    return h.macro_points(store, series, on, n)


def macro_inputs(store, on=None) -> dict:
    """indicator -> {value, change_pct, as_of} using the newest observation <= on (None if absent)."""
    from datetime import date as _date
    p, _ = params("macro_regime")
    ref = h._d(on)
    out = {}
    for k, s in MACRO_INPUTS.items():
        pts = _point(store, s, on)
        if not pts or (_date.fromisoformat(ref) - _date.fromisoformat(pts[0][0])).days > p["max_input_age_days"]:
            out[k] = None
            continue
        v, d = pts[0][1], pts[0][0]
        chg = round((v - pts[1][1]) / pts[1][1] * 100, 2) if len(pts) > 1 and pts[1][1] else None
        out[k] = {"value": round(v, 4), "change_pct": chg, "as_of": d}
    if out.get("India_VIX") is None:           # NSE official close missing: fall back to Yahoo's ^INDIAVIX
        pts = h.macro_points(store, "yahoo:^INDIAVIX", on, 2)
        if pts and (_date.fromisoformat(ref) - _date.fromisoformat(pts[0][0])).days <= p["max_input_age_days"]:
            out["India_VIX"] = {"value": pts[0][1], "change_pct": None, "as_of": pts[0][0], "source": "yahoo fallback"}
    return out


# Yahoo regime input -> (FRED cross-check series, tolerance, unit). Brent futures vs spot legitimately differ a little.
CROSS_CHECKS = {"US_10Y": ("fred:DGS10", 0.10, "pct-pt"), "Brent_Crude": ("fred:DCOILBRENTEU", 4.0, "$"),
                "US_VIX": ("fred:VIXCLS", 1.5, "pt"), "USD_INR": ("fred:DEXINUS", 0.5, "INR")}


def cross_check(store, ind: dict) -> list[dict]:
    """Compare each Yahoo input with FRED's series for the same date (FRED lags, so the newest FRED point on or before the
    Yahoo date). A gap beyond tolerance is reported, never silently averaged away: the filing/official series wins."""
    out = []
    for k, (series, tol, unit) in CROSS_CHECKS.items():
        x = ind.get(k)
        if not x:
            continue
        ref = h.macro_on_or_before(store, series, x["as_of"])          # FRED lags: its newest point on or before the Yahoo date
        if not ref:
            continue
        mine = h.macro_on_or_before(store, MACRO_INPUTS[k], ref[0])    # Yahoo on that same date: compare like with like
        if not mine or mine[0] != ref[0]:
            continue
        gap = round(mine[1] - ref[1], 4)
        out.append({"input": k, "date": ref[0], "yahoo": mine[1], "fred": ref[1], "gap": gap, "unit": unit, "agrees": abs(gap) <= tol})
    return out


def macro_regime(store, on=None):
    p, ver = params("macro_regime")
    ind = macro_inputs(store, on)
    v = lambda k: ind[k]["value"] if ind.get(k) else None  # noqa: E731
    chg = lambda k: ind[k]["change_pct"] if ind.get(k) else None  # noqa: E731
    score, signals, comps = 0, [], []

    def add(name, contrib, text, rule):
        nonlocal score
        score += contrib
        if text:
            signals.append(text)
        comps.append(Component(name, ind[name]["value"] if ind.get(name) else None, ind[name]["as_of"] if ind.get(name) else None,
                               rule, contrib, text))

    def band(name, label, unit, hi, lo, fmt="{}"):
        x = v(name)
        if x is None:
            return
        val = fmt.format(x)
        if x > hi:
            add(name, -1, f"Bearish: {label} elevated at {val}{unit} (>{hi})", f">{hi} bearish, <{lo} bullish")
        elif x < lo:
            add(name, +1, f"Bullish: {label} {'weak' if name == 'DXY' else 'low' if name == 'US_10Y' else 'soft' if name == 'Brent_Crude' else 'complacent'} at {val}{unit} (<{lo})",
                f">{hi} bearish, <{lo} bullish")
        else:
            add(name, 0, "", f">{hi} bearish, <{lo} bullish")

    band("DXY", "DXY (dollar index)", "", p["dxy_bearish_above"], p["dxy_bullish_below"], "{:.2f}")
    band("US_10Y", "US 10Y yield", "%", p["us10y_bearish_above"], p["us10y_bullish_below"], "{:.2f}")
    band("Brent_Crude", "Brent crude", "", p["brent_bearish_above"], p["brent_bullish_below"], "${:.2f}")
    band("India_VIX", "India VIX (volatility index)", "", p["india_vix_bearish_above"], p["india_vix_bullish_below"], "{:.2f}")
    c = chg("USD_INR")
    if c is not None:   # v1: direction, not a stale absolute level
        if c > p["usdinr_bearish_change_pct"]:
            add("USD_INR", -1, f"Bearish: rupee weakening (USD/INR +{c}%)", f"daily change >{p['usdinr_bearish_change_pct']}% bearish")
        elif c < p["usdinr_bullish_change_pct"]:
            add("USD_INR", +1, f"Bullish: rupee strengthening (USD/INR {c}%)", f"daily change <{p['usdinr_bullish_change_pct']}% bullish")
        else:
            add("USD_INR", 0, "", "daily change within +/-0.15%")
    us, iv = v("US_VIX"), v("India_VIX")
    if us is not None and iv is not None:
        if us > iv + p["us_vix_premium_points"]:
            add("US_VIX", -1, "Bearish: US VIX premium over India VIX — global risk not yet priced in India", "US VIX > India VIX + 2")
        else:
            add("US_VIX", 0, "", "US VIX > India VIX + 2 bearish")
    scored = {"DXY", "US_10Y", "Brent_Crude", "India_VIX", "USD_INR", "US_VIX"}
    missing = sorted(k for k in scored if ind.get(k) is None or (k == "USD_INR" and chg(k) is None))
    if len(missing) == len(scored):
        return unknown("macro_regime", "none of the six inputs is stored and fresh; run `drishti run macro_daily` or the macro backfill")
    if not signals:
        signals.append("Neutral: no macro extreme triggered")
    checks = cross_check(store, ind)
    disagree = [c for c in checks if not c["agrees"]]
    for c in disagree:
        missing.append(f"source disagreement on {c['input']} on {c['date']}: Yahoo {c['yahoo']} vs FRED {c['fred']} (gap {c['gap']} {c['unit']})")
    as_of = max(x["as_of"] for x in ind.values() if x)
    label = "bullish backdrop" if score >= 2 else "bearish backdrop" if score <= -2 else "neutral backdrop"
    return ok(Signal("macro_regime", as_of, score, label, comps, missing,
                     "Macro-only regime read, -6..+6. Thresholds are calibration-sensitive heuristics (medium confidence), not precise "
                     "signals. v2 uses end-of-day closes (Yahoo for DXY/US10Y/Brent/USD-INR/US VIX, NSE official India VIX) where v1 used "
                     "live quotes; the USD/INR move is close-to-close. Combine with participant_oi for the full regime picture.",
                     ver, details={"signals": signals, "indicators": ind, "cross_checks": checks}))

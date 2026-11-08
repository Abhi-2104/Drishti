"""L3-3 cycle phase, partial. v1's /macro rules (Late / Contraction / Expansion / Recovery) as explicit conditions.

Method (provisional, mine, not your brother's): each phase lists its v1 conditions; a phase's weight is the share of its
conditions that are met among those we can evaluate; weights are normalised into a probability. Conditions whose inputs
are missing are listed, never assumed. PMI and India inflation are not collected yet and are always named as missing.
"""
from __future__ import annotations

from datetime import date, timedelta

from . import flows, history as h, market
from .base import Component, Signal, ok, params


def _fii_window(store, on, n, key):
    rows = h.fii_dii_days(store, on, n)
    return (sum(r[key] for r in rows), len(rows)) if len(rows) >= min(5, n) else (None, len(rows))


def _rates_bottoming(store, on, n):
    """10Y lower now than n sessions ago (Yahoo ^TNX); India 10Y G-sec index higher (= yields lower) as a second view."""
    us = h.macro_points(store, "yahoo:^TNX", on, n + 1)
    gsec = h.index_series(store, "Nifty 10 yr Benchmark G-Sec", on, days=int(n * 1.6) + 10)
    views = []
    if len(us) > n:
        views.append(us[0][1] < us[-1][1])
    if len(gsec) > n:
        views.append(gsec[-1][1] > gsec[-1 - n][1])
    return (all(views) if views else None), {"us10y": len(us) > n, "india_gsec": len(gsec) > n}


def _india_macro(store, on, p):
    """(cpi_points newest-first, iip_yoy or None, notes). A reading counts only if it was published by `on` and its reference
    month is not older than macro_max_age_days; otherwise it is named in `notes` and left out."""
    im = params("india_macro")[0]
    day = on.isoformat() if isinstance(on, date) else on or date.today().isoformat()
    cutoff = (date.fromisoformat(day) - timedelta(days=p["macro_max_age_days"])).isoformat()
    notes = []

    def fresh(series, lag, n):
        pts = h.macro_released(store, series, on, lag, n)
        if not pts:
            notes.append(f"{series.split(':')[1]}: nothing published yet")
            return []
        if pts[0][0] < cutoff:
            notes.append(f"{series.split(':')[1]}: latest is {pts[0][0][:7]} (older than {p['macro_max_age_days']} days), not used")
            return []
        return pts
    cpi = fresh("mospi:cpi_yoy", im["cpi_release_lag_days"], p["inflation_easing_months"] + 1)
    iip = fresh("mospi:iip_yoy", im["iip_release_lag_days"], 1)
    return cpi, (iip[0][1] if iip else None), notes


def cycle_phase(store, on=None):
    p, ver = params("cycle_phase")
    ind = market.macro_inputs(store, on)
    brent = ind["Brent_Crude"]["value"] if ind.get("Brent_Crude") else None
    us10y = ind["US_10Y"]["value"] if ind.get("US_10Y") else None
    fii, nf = _fii_window(store, on, p["fii_window_days"], "fii_net")
    dii, _ = _fii_window(store, on, p["fii_window_days"], "dii_net")
    mid = market.midcap_ratio(store, on)
    mid_label = mid.data["label"] if mid.ok else None
    rot = market.sector_rotation(store, on)
    quad = {s["name"]: s["quadrant"] for s in rot.data["details"]["sectors"]} if rot.ok else {}
    defs = [quad[x] for x in p["defensives"] if x in quad]
    rates, rate_views = _rates_bottoming(store, on, p["rates_window_days"])
    cpi_pts, iip_yoy, macro_notes = _india_macro(store, on, p)
    cpi = cpi_pts[0][1] if cpi_pts else None
    easing = (cpi_pts[0][1] < cpi_pts[-1][1]) if len(cpi_pts) > p["inflation_easing_months"] else None

    def either(a, b):
        if a is None and b is None:
            return None
        return bool(a) or bool(b)

    cond = {
        "Late": {f"Brent > {p['late_brent_above']} or US 10Y > {p['late_us10y_above']}% or India CPI > {p['late_cpi_above']}%":
                 either(either(None if brent is None else brent > p["late_brent_above"], None if us10y is None else us10y > p["late_us10y_above"]),
                        None if cpi is None else cpi > p["late_cpi_above"])},
        "Contraction": {"FII selling": None if fii is None else fii < 0,
                        "midcaps lagging": None if mid_label is None else mid_label == "midcaps lagging",
                        "defensive rotation (a defensive Leading/Improving)": None if not defs else any(q in ("Leading", "Improving") for q in defs),
                        "IIP growth negative": None if iip_yoy is None else iip_yoy < 0},
        "Expansion": {"midcaps outperforming": None if mid_label is None else mid_label == "midcaps outperforming",
                      "FII buying": None if fii is None else fii > 0,
                      "IIP growth positive": None if iip_yoy is None else iip_yoy > 0},
        "Recovery": {"rates bottoming": rates,
                     "DII support": None if dii is None else dii > 0,
                     "defensives cooling (Weakening/Lagging)": None if not defs else all(q in ("Weakening", "Lagging") for q in defs),
                     f"CPI inflation easing over {p['inflation_easing_months']} releases": easing},
    }
    weights, comps, missing = {}, [], list(p["missing_inputs"]) + [f"India macro: {n}" for n in macro_notes]
    for phase, cs in cond.items():
        known = {k: v for k, v in cs.items() if v is not None}
        weights[phase] = (sum(known.values()) / len(known)) if known else 0.0
        for k, v in cs.items():
            comps.append(Component(f"{phase}: {k}", v, None, "v1 /macro rule", None, "unknown" if v is None else "met" if v else "not met"))
            if v is None:
                missing.append(f"{phase}: {k}")
    total = sum(weights.values())
    probs = {k: round(v / total, 2) for k, v in weights.items()} if total else {k: 0.25 for k in weights}
    best = max(probs, key=probs.get)
    tied = [k for k, v in probs.items() if v == probs[best]]
    label = "no clear phase" if not total or len(tied) > 1 else f"{best} most likely ({probs[best]:.0%})"
    as_of = h.latest_index_date(store, on) or (on.isoformat() if isinstance(on, date) else on or date.today().isoformat())
    if fii is not None and nf < p["fii_window_days"]:
        missing.append(f"FII window uses {nf} of {p['fii_window_days']} sessions")
    return ok(Signal("cycle_phase", as_of, None, label, comps, missing,
                     "Partial cycle read from v1's /macro rules turned into condition counts. Not calibrated; sharpen as PMI and "
                     "inflation inputs land. Favoured sectors per v1: Late = IT/Pharma + large-cap quality; Contraction = defensives, "
                     "cash; Expansion = cyclicals, midcaps, capex; Recovery = rate-sensitives, financials, early cyclicals.",
                     ver, provisional=True, details={"probabilities": probs, "inputs": {"brent": brent, "us10y": us10y, "fii_net": fii,
                                                                                         "dii_net": dii, "midcap": mid_label, "india_cpi_yoy": cpi, "india_iip_yoy": iip_yoy,
                                                                                         "defensives": dict(zip(p["defensives"], defs)),
                                                                                         "rates_views": rate_views}}))

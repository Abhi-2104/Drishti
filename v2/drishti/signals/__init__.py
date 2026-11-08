"""Signals: each one a pure function (store, on_date) -> Result holding a Signal record. Market-wide signals are listed
in MARKET; single-name ones (insider_cluster, pledge) take an ISIN. No signal combines others into a verdict: confluence
is the decision layer's job (blueprint: one signal, one tool, visible seams)."""
from __future__ import annotations

from datetime import date, timedelta

from . import calendar, cycle, disclosure, flows, market, price

MARKET = {
    "seasonality": calendar.seasonality,
    "macro_regime": market.macro_regime,
    "participant_oi": market.participant_oi,
    "sector_rotation": market.sector_rotation,
    "midcap_ratio": market.midcap_ratio,
    "fii_dii": flows.fii_dii,
    "options_flow:NIFTY": lambda s, on=None: flows.options_flow(s, on, "NIFTY"),
    "options_flow:BANKNIFTY": lambda s, on=None: flows.options_flow(s, on, "BANKNIFTY"),
    "liquidity": flows.liquidity,
    "credit_spreads": flows.credit_spreads,
    "cycle_phase": cycle.cycle_phase,
}
SINGLE_NAME = {"insider_cluster": disclosure.insider_cluster, "pledge": disclosure.pledge, "price_context": price.price_context}


def compute(store, name: str, on=None, **kw):
    if name in MARKET:
        return MARKET[name](store, on)
    if name in SINGLE_NAME:
        return SINGLE_NAME[name](store, on=on, **kw)
    raise KeyError(name)


def replay(store, name: str, start: date, end: date) -> list[dict]:
    """Recompute a market signal for every weekday in [start, end] from stored history (for testing behaviour over time).
    Returns compact rows; nothing is written."""
    out, d, seen = [], start, set()
    while d <= end:
        if d.weekday() < 5:
            # the percentile line re-scores ~250 past sessions; a replay only needs the score itself
            r = market.participant_oi(store, d, with_history=False) if name == "participant_oi" else compute(store, name, d)
            if r.ok and r.as_of in seen:      # no new data that day (exchange holiday): the same reading, not a new one
                d += timedelta(days=1)
                continue
            if r.ok:
                seen.add(r.as_of)
            out.append({"date": d.isoformat(), "ok": r.ok, "as_of": r.as_of,
                        "score": r.data["score"] if r.ok else None, "label": r.data["label"] if r.ok else str(r.error.kind)})
        d += timedelta(days=1)
    return out


def summarise(rows: list[dict]) -> dict:
    """Distribution of a replay: how often each label fired and score percentiles (to judge whether a threshold
    discriminates or fires almost every day)."""
    from collections import Counter
    ok = [r for r in rows if r["ok"]]
    scores = sorted(r["score"] for r in ok if isinstance(r["score"], (int, float)))
    pct = lambda q: scores[min(len(scores) - 1, int(q * (len(scores) - 1) + 0.5))] if scores else None  # noqa: E731
    return {"days": len(rows), "ok_days": len(ok), "labels": dict(Counter(r["label"] for r in ok).most_common()),
            "score_p10": pct(0.10), "score_p50": pct(0.50), "score_p90": pct(0.90),
            "score_min": scores[0] if scores else None, "score_max": scores[-1] if scores else None}

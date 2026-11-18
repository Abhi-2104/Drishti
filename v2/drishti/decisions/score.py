"""The scorer: what actually happened after each logged decision, and an honest comparison of the authors.

Method, fixed in advance (change it only by raising SCORE_VERSION):
  * A stock call is judged against the Nifty 50 over the same sessions: excess = stock return - Nifty return, from the stored
    split/bonus-adjusted closes. Fill price is the close of the as_of session (optimistic, but the same for every author).
  * A market call is judged against cash: excess = Nifty return - a cash hurdle (CASH_PCT_PA over the horizon).
  * edge = stance x excess. A bullish call earns the excess, a bearish call earns its opposite, a no-call earns 0.
    ENTER / EXIT / TRIM / SHORTLIST also pay COST_PCT for the trade (edge_net).
  * A decision whose horizon has not run yet is `pending`; one with missing prices is `no_data`. Neither is ever dropped quietly:
    both are counted in every summary.
  * Authors are compared only on cases they both decided and that are scored, with the dates resampled as blocks (calls on one
    date share the same market), and no winner is named unless the interval clears zero on enough cases and dates.
"""
from __future__ import annotations

import random
from collections import defaultdict
from datetime import date, timedelta
from statistics import mean

from ..signals import history as h
from ..signals import price
from ..store import datasets as ds
from . import log

SCORE_VERSION = "1"
COST_PCT = 0.4                 # round trip, percent of the position
CASH_PCT_PA = 6.5              # the hurdle a RISK_ON / RISK_OFF call is judged against
TRADING_DAYS = 250
TRADE_ACTIONS = {"ENTER", "EXIT", "TRIM", "SHORTLIST"}
MIN_PAIRS, MIN_DATES, BOOT = 30, 8, 2000
NIFTY = "Nifty 50"


def _author(d: dict) -> str:
    return f"{d['agent']}/{d['model']}"


def _at_or_before(series: list[tuple[str, float]], day: str) -> tuple[str, float] | None:
    best = None
    for d, v in series:
        if d <= day:
            best = (d, v)
        else:
            break
    return best


def outcome(store, d: dict, today: str | None = None) -> dict:
    """Score one decision. Returns d's key fields plus status, ret_pct, bench_pct, excess_pct, edge_pct, edge_net_pct, hit."""
    base = {"id": d["id"], "author": _author(d), "agent": d["agent"], "model": d["model"], "gate": d["gate"], "as_of": d["as_of"],
            "subject": d["subject"], "symbol": d.get("symbol"), "action": d["action"], "stance": d["stance"], "confidence": d["confidence"],
            "horizon_days": d["horizon_days"]}
    hz = d["horizon_days"]
    on = date.fromisoformat(today) if isinstance(today, str) else (today or date.today())
    back = max(400, (on - date.fromisoformat(d["as_of"])).days + 30)     # reach back to the decision date, however old (was a fixed 2000 days)
    nifty = h.index_series(store, NIFTY, on, days=back)
    after = [x for x in nifty if x[0] > d["as_of"]]
    start = _at_or_before(nifty, d["as_of"])
    if start is None or (date.fromisoformat(d["as_of"]) - date.fromisoformat(start[0])).days > 5:
        return {**base, "status": "no_data", "why": "no stored Nifty 50 closes cover the decision date"}
    if len(after) < hz:
        return {**base, "status": "pending", "why": f"{len(after)} of {hz} sessions have run"}
    end_day = after[hz - 1][0]
    nret = (after[hz - 1][1] / start[1] - 1) * 100
    if d["scope"] == "market":
        ret, bench = nret, CASH_PCT_PA * hz / TRADING_DAYS
    else:
        far = (date.fromisoformat(end_day) - date.fromisoformat(d["as_of"])).days + 10
        bars = price._bars(store, d["isin"], end_day, days=far)
        if not bars:
            return {**base, "status": "no_data", "why": "no stored prices for the stock"}
        bars, _ = price.adjust(bars, [e for e in ds.adjustments_for(store, d["isin"]) if e["ex_date"] <= end_day])
        pre = [b for b in bars if b["trade_date"] <= d["as_of"]]
        post = [b for b in bars if d["as_of"] < b["trade_date"] <= end_day]
        if not pre or (date.fromisoformat(d["as_of"]) - date.fromisoformat(pre[-1]["trade_date"])).days > 5:
            return {**base, "status": "no_data", "why": "no stored price near the decision date"}
        if not post or (date.fromisoformat(end_day) - date.fromisoformat(post[-1]["trade_date"])).days > 5:
            return {**base, "status": "no_data", "why": "stored prices stop before the horizon ends (suspended, delisted, or not updated)"}
        ret, bench = (post[-1]["close"] / pre[-1]["close"] - 1) * 100, nret
    excess = ret - bench
    edge = d["stance"] * excess
    net = edge - (COST_PCT if d["stance"] != 0 and d["action"] in TRADE_ACTIONS and d["scope"] == "stock" else 0.0)
    return {**base, "status": "scored", "end": end_day, "ret_pct": round(ret, 3), "bench_pct": round(bench, 3), "excess_pct": round(excess, 3),
            "edge_pct": round(edge, 3), "edge_net_pct": round(net, 3), "hit": (edge > 0) if d["stance"] != 0 else None}


def score_all(store, **filters) -> list[dict]:
    """Outcome for every current (not superseded) decision matching the filters of log.current()."""
    return [outcome(store, d) for d in log.current(store, **filters)]


def baseline_rows(rows: list[dict]) -> list[dict]:
    """A free benchmark author: 'always long' on every scored stock case (and RISK_ON on every market case)."""
    out, seen = [], set()
    for r in rows:
        k = (r["gate"], r["as_of"], r["subject"], r["horizon_days"])
        if r["status"] != "scored" or k in seen:
            continue
        seen.add(k)
        out.append({**r, "agent": "baseline", "model": "always-long", "author": "baseline/always-long", "stance": 1, "confidence": None,
                    "action": "LONG", "edge_pct": r["excess_pct"], "edge_net_pct": r["excess_pct"], "hit": r["excess_pct"] > 0})
    return out


def summarise(rows: list[dict]) -> list[dict]:
    """One line per (author, gate): counts that never hide the pending / missing, then hit rate, edge and calibration."""
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        groups[(r["author"], r["gate"])].append(r)
    out = []
    for (author, gate), rs in sorted(groups.items()):
        sc = [r for r in rs if r["status"] == "scored"]
        calls = [r for r in sc if r["stance"] != 0]
        conf = [r for r in calls if r["confidence"] is not None]
        brier = mean((r["confidence"] - (1.0 if r["hit"] else 0.0)) ** 2 for r in conf) if conf else None
        buckets = []
        for lo, hi in ((0.5, 0.6), (0.6, 0.7), (0.7, 1.0)):
            b = [r for r in conf if lo <= r["confidence"] < hi or (hi == 1.0 and r["confidence"] == 1.0)]
            if b:
                buckets.append({"said": f"{lo:.0%}-{hi:.0%}", "n": len(b), "actually_right": round(sum(r["hit"] for r in b) / len(b), 3)})
        out.append({"author": author, "gate": gate, "decisions": len(rs), "scored": len(sc), "pending": sum(r["status"] == "pending" for r in rs),
                    "no_data": sum(r["status"] == "no_data" for r in rs), "no_call": len(sc) - len(calls), "calls": len(calls),
                    "hit_rate": round(sum(r["hit"] for r in calls) / len(calls), 3) if calls else None,
                    "mean_edge_pct": round(mean(r["edge_pct"] for r in calls), 3) if calls else None,
                    "mean_edge_net_pct": round(mean(r["edge_net_pct"] for r in calls), 3) if calls else None,
                    "brier": round(brier, 4) if brier is not None else None, "calibration": buckets})
    return out


def compare(rows: list[dict], a: str, b: str, *, gate: str | None = None, seed: int = 7) -> dict:
    """Paired comparison of authors a and b ('agent/model') on the cases both decided and that are scored."""
    def key(r):
        return (r["gate"], r["as_of"], r["subject"], r["horizon_days"])
    pick = {x: {key(r): r for r in rows if r["author"] == x and r["status"] == "scored" and (gate is None or r["gate"] == gate)} for x in (a, b)}
    common = sorted(set(pick[a]) & set(pick[b]))
    n_a, n_b = len(pick[a]), len(pick[b])
    base = {"a": a, "b": b, "gate": gate, "scored_a": n_a, "scored_b": n_b, "pairs": len(common), "version": SCORE_VERSION}
    if not common:
        return {**base, "verdict": "no overlap: the two authors have no scored case in common", "dates": 0}
    diffs = defaultdict(list)
    for k in common:
        diffs[k[1]].append(pick[a][k]["edge_net_pct"] - pick[b][k]["edge_net_pct"])
    dates = sorted(diffs)
    allv = [v for d in dates for v in diffs[d]]
    mean_diff = mean(allv)
    rng = random.Random(seed)
    means = []
    for _ in range(BOOT):
        s = [v for d in (rng.choice(dates) for _ in dates) for v in diffs[d]]
        means.append(mean(s))
    means.sort()
    lo, hi = means[int(0.025 * BOOT)], means[int(0.975 * BOOT) - 1]
    a_better = sum(1 for v in allv if v > 0)
    b_better = sum(1 for v in allv if v < 0)
    if len(common) < MIN_PAIRS or len(dates) < MIN_DATES:
        verdict = f"not enough evidence: need at least {MIN_PAIRS} shared cases on {MIN_DATES} dates (have {len(common)} on {len(dates)})"
    elif lo > 0:
        verdict = f"{a} is better (interval clears zero)"
    elif hi < 0:
        verdict = f"{b} is better (interval clears zero)"
    else:
        verdict = "no clear difference (interval includes zero)"
    return {**base, "dates": len(dates), "mean_edge_diff_pct": round(mean_diff, 3), "ci95_pct": [round(lo, 3), round(hi, 3)],
            "a_better_cases": a_better, "b_better_cases": b_better, "tied_cases": len(allv) - a_better - b_better, "verdict": verdict}

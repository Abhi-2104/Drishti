"""What would a strategy have earned as a portfolio? (`drishti decisions portfolio-sim`)

The scorer judges single calls; this turns the same history cases into money, the number that maps to the 25% XIRR goal.

Model (fixed in advance):
  * Staggered sleeves. Cases are taken every `every` sessions and held for `horizon`, so capital is split into
    K = horizon / every equal sleeves. On each case date one sleeve (the one whose holding just ended) is redeployed.
  * A strategy picks stocks from that date's sampled cases. The sleeve is split equally across its picks, at most
    `max_positions` (the highest-points ones first). No pick: the sleeve holds the fallback (cash at CASH_PCT_PA, or the Nifty).
  * Every stock period pays the round-trip cost (score.COST_PCT). A Nifty period pays none (an index fund costs a few basis
    points a year). Cash earns score.CASH_PCT_PA.
  * Each sleeve is one cash flow out on its first date and one back on its last end date; XIRR is solved over all of them.
    With a lump sum this equals the compound annual growth rate.
Limits, reported with every result: prices are price-only (no dividends: about 1-1.5% a year missing for both stocks and the
Nifty), returns are pre-tax (60-day holds are short-term gains), picks come only from the ~10 stocks SAMPLED per date (not a
screen of the whole market), and there is no intra-period mark, so drawdown is shown as the worst period, not a daily path.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date

from . import score


def xirr(flows: list[tuple[str, float]], lo: float = -0.99, hi: float = 10.0) -> float | None:
    """Annual rate r with sum(cf / (1+r)^(days/365)) = 0, by bisection. None when the flows do not change sign."""
    if not flows or not any(c < 0 for _, c in flows) or not any(c > 0 for _, c in flows):
        return None
    t0 = min(date.fromisoformat(d) for d, _ in flows)
    pts = [((date.fromisoformat(d) - t0).days / 365.0, c) for d, c in flows]

    def npv(r):
        return sum(c / (1 + r) ** t for t, c in pts)
    f_lo, f_hi = npv(lo), npv(hi)
    if f_lo * f_hi > 0:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2
        f = npv(mid)
        if abs(f) < 1e-10:
            break
        if (f > 0) == (f_lo > 0):
            lo, f_lo = mid, f
        else:
            hi = mid
    return (lo + hi) / 2


def _picks_all(rs: list[dict]) -> list[dict]:
    return rs


def _picks_rules_v1(rs: list[dict]) -> list[dict]:
    ent = [r for r in rs if (r.get("rules_v1") or {}).get("action") == "ENTER"]
    return sorted(ent, key=lambda r: (-r["rules_v1"]["points"], r["isin"]))


def _picks_rules_v2(rs: list[dict]) -> list[dict]:
    from . import rules_v2
    out = []
    for r in rs:
        d = rules_v2.score(r.get("price"), r.get("deal_flow"))
        if d is not None and d["action"] == "ENTER":
            out.append((d["points"], r))
    return [r for _, r in sorted(out, key=lambda x: (-x[0], x[1]["isin"]))]


STRATEGIES = {
    "nifty": (None, "nifty"),
    "always-long (all sampled stocks)": (_picks_all, "cash"),
    "rules-v1 ENTER, else cash": (_picks_rules_v1, "cash"),
    "rules-v1 ENTER, else Nifty": (_picks_rules_v1, "nifty"),
    "rules-v2 ENTER, else cash": (_picks_rules_v2, "cash"),
    "rules-v2 ENTER, else Nifty": (_picks_rules_v2, "nifty"),
}


def simulate(rows: list[dict], pick, fallback: str, *, every: int = 10, horizon: int = 60, max_positions: int = 5,
             cost_pct: float = score.COST_PCT, cash_pct_pa: float = score.CASH_PCT_PA) -> dict:
    by_date = defaultdict(list)
    for r in rows:
        if r["status"] == "scored":
            by_date[r["as_of"]].append(r)
    dates = sorted(by_date)
    k = max(1, round(horizon / every))
    if len(dates) <= k:
        return {"error": f"{len(dates)} scored dates; need more than {k}"}
    cash_period = cash_pct_pa * horizon / score.TRADING_DAYS
    sleeves = [{"nav": 1.0, "first": None, "last_end": None} for _ in range(k)]
    periods, stock_periods, picks_n = [], 0, []
    for i, d in enumerate(dates):
        rs = by_date[d]
        nifty = rs[0]["bench_pct"]
        end = max(r["end"] for r in rs)
        chosen = (pick(rs) if pick else [])[:max_positions]
        if chosen:
            ret = sum(r["ret_pct"] for r in chosen) / len(chosen) - cost_pct
            stock_periods += 1
        else:
            ret = nifty if fallback == "nifty" else cash_period
        picks_n.append(len(chosen))
        s = sleeves[i % k]
        s["first"] = s["first"] or d
        s["nav"] *= 1 + ret / 100
        s["last_end"] = end
        periods.append({"as_of": d, "ret_pct": ret, "nifty_pct": nifty, "picks": len(chosen)})
    flows = []
    for s in sleeves:
        if s["first"]:
            flows += [(s["first"], -1.0 / k), (s["last_end"], s["nav"] / k)]
    r = xirr(flows)
    rets = [p["ret_pct"] for p in periods]
    worst = min(periods, key=lambda p: p["ret_pct"])
    return {"xirr_pct": round(r * 100, 2) if r is not None else None, "periods": len(periods), "sleeves": k,
            "periods_in_stocks": stock_periods, "avg_picks_when_in_stocks": round(sum(n for n in picks_n if n) / max(1, stock_periods), 2),
            "mean_period_pct": round(sum(rets) / len(rets), 2), "beat_nifty_periods_pct": round(100 * sum(p["ret_pct"] > p["nifty_pct"] for p in periods) / len(periods), 1),
            "worst_period": {"as_of": worst["as_of"], "ret_pct": round(worst["ret_pct"], 2)},
            "first": dates[0], "last_end": max(s["last_end"] for s in sleeves if s["last_end"])}


def run(rows: list[dict], *, every: int = 10, horizon: int = 60, max_positions: int = 5, halves=None) -> dict:
    from .breakdown import HALVES
    halves = halves or HALVES
    out = {}
    for name, (pick, fb) in STRATEGIES.items():
        res = {"all": simulate(rows, pick, fb, every=every, horizon=horizon, max_positions=max_positions)}
        for hn, lo, hi in halves:
            res[hn] = simulate([r for r in rows if lo <= r["as_of"] <= hi], pick, fb, every=every, horizon=horizon, max_positions=max_positions)
        out[name] = res
    return {"strategies": out, "target_xirr_pct": 25.0, "max_positions": max_positions, "cost_pct_round_trip": score.COST_PCT,
            "cash_pct_pa": score.CASH_PCT_PA,
            "limits": ["price return only: dividends (about 1-1.5% a year) are missing for stocks and the Nifty alike",
                       "pre-tax: 60-session holds are short-term capital gains",
                       "picks come only from the ~10 stocks sampled per date, not a screen of the whole liquid universe",
                       "no daily marks: 'worst_period' is the worst 60-session sleeve return, not a drawdown"]}

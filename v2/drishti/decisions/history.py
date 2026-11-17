"""The long-history test of rules-v1: no model is involved, so nothing can be remembered, and the frozen thresholds are judged on
every year of stored prices, not one regime.

Steps: coverage (what the store holds), register ISINs that traded but are missing from the security master (so delisted names are
not silently dropped, which would flatter the rules), draw seeded cases, decide them with rules-v1, then score per calendar
year against always-long and against the Nifty. Rules read only price, strength and macro for old dates: insider and pledge
history is short, so those readings are UNKNOWN and the rules run on fewer inputs. That limit is reported, not hidden.
"""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from . import cases as cs, rules, score


def coverage(store) -> dict:
    days = cs.sessions(store)
    n_isin = store.query("SELECT COUNT(DISTINCT isin) AS n FROM price_eod WHERE exchange='NSE' AND series='EQ'")[0]["n"]
    missing = store.query("SELECT COUNT(DISTINCT p.isin) AS n FROM price_eod p LEFT JOIN security s ON s.isin=p.isin "
                          "WHERE p.exchange='NSE' AND p.series='EQ' AND s.isin IS NULL")[0]["n"]
    nifty = store.query("SELECT COUNT(*) AS n, MIN(trade_date) AS a FROM index_close WHERE index_name='Nifty 50'")[0]
    return {"sessions": len(days), "first": days[0] if days else None, "last": days[-1] if days else None, "stocks_ever_traded": n_isin,
            "stocks_missing_from_master": missing, "nifty_sessions": nifty["n"], "nifty_first": nifty["a"]}


def register_missing(store) -> int:
    """Add a minimal master row (symbol as name, source 'bhavcopy') for every NSE EQ ISIN that traded but is not in the master."""
    rows = store.query("SELECT p.isin AS isin, MAX(p.symbol) AS symbol FROM price_eod p LEFT JOIN security s ON s.isin=p.isin "
                       "WHERE p.exchange='NSE' AND p.series='EQ' AND s.isin IS NULL GROUP BY p.isin")
    for r in rows:
        store.upsert_security(r["isin"], name=r["symbol"], nse_symbol=r["symbol"], source="bhavcopy")
    return len(rows)


def run(store, *, out: Path, start: str | None = None, end: str | None = None, every: int = 20, n_stocks: int = 30, seed: int = 11,
        min_sessions_before: int = 260) -> dict:
    days = cs.sessions(store)
    if len(days) < min_sessions_before + cs.HORIZON + 1:
        return {"done": False, "why": f"{len(days)} stored sessions; need at least {min_sessions_before + cs.HORIZON + 1}. Backfill more prices first.",
                "coverage": coverage(store)}
    start = start or days[min_sessions_before]
    end = end or days[-1]
    registered = register_missing(store)
    cases = cs.make_cases(store, start=start, end=end, every=every, n_stocks=n_stocks, seed=seed, stock_gates=("entry",), market=True,
                          min_horizon_after=cs.HORIZON)
    cs.save(cases, Path(out), {"history": True, "start": start, "end": end, "every": every, "n_stocks": n_stocks, "seed": seed})
    return {"done": True, "cases": len(cases), "dates": len({c["as_of"] for c in cases}), "registered_missing_isins": registered,
            "rules": cs.run_rules(store, cases), "cases_file": str(out), "coverage": coverage(store)}


def report(store, *, since: str | None = None, until: str | None = None) -> dict:
    """Rules-v1 against always-long per calendar year and overall, for the entry and macro gates, from the stored decisions."""
    _, ver = rules.load()
    author = f"rules/{ver}"
    rows = score.score_all(store, agent=rules.AGENT, model=ver, since=since, until=until)
    rows = rows + score.baseline_rows(rows)
    by_year: dict[str, list] = defaultdict(list)
    for r in rows:
        by_year[r["as_of"][:4]].append(r)
    table = {}
    for y, rs in sorted(by_year.items()):
        table[y] = [{k: s[k] for k in ("author", "gate", "scored", "calls", "hit_rate", "mean_edge_net_pct")} for s in score.summarise(rs)]
    return {"author": author, "per_year": table,
            "overall": [{k: s[k] for k in ("author", "gate", "scored", "pending", "no_data", "calls", "hit_rate", "mean_edge_net_pct", "brier")}
                        for s in score.summarise(rows)],
            "rules_vs_always_long_entry": score.compare(rows, author, "baseline/always-long", gate="entry"),
            "rules_vs_always_long_macro": score.compare(rows, author, "baseline/always-long", gate="macro")}

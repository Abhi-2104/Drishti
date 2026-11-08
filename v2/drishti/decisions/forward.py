"""The forward log: the only test that cannot be contaminated by what a model remembers.

Each run takes the latest stored session as the decision date, builds cases for a FIXED universe of stocks (drawn once, by a
seeded random pick from the most liquid names, then never changed) at horizons of 20 and 60 sessions, records rules-v1 on them,
and exports blind evidence for an author. Nothing here can see prices after the decision date because none exist yet. Scoring
later is the ordinary `drishti decisions score` once the horizons have run.
"""
from __future__ import annotations

import json
import random
from datetime import date, timedelta
from pathlib import Path

from . import cases as cs

HORIZONS = (20, 60)
UNIVERSE_SIZE = 20
MIN_GAP = 5          # sessions between forward dates: about weekly


def universe(store, path: Path, *, as_of: str, size: int = UNIVERSE_SIZE, seed: int = 2026) -> dict:
    """Load the frozen universe, or draw it once (seeded, from the LIQUID_POOL most-traded names as of `as_of`) and save it."""
    path = Path(path)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    lo = (date.fromisoformat(as_of) - timedelta(days=30)).isoformat()
    liquid = store.query("SELECT isin, MAX(symbol) AS symbol FROM price_eod WHERE exchange='NSE' AND series='EQ' AND trade_date BETWEEN ? AND ? "
                         "AND isin IN (SELECT isin FROM price_eod WHERE trade_date=? AND exchange='NSE' AND series='EQ') "
                         "GROUP BY isin ORDER BY AVG(turnover) DESC, isin LIMIT ?", (lo, as_of, as_of, cs.LIQUID_POOL))
    picks = sorted(random.Random(f"{seed}|forward").sample(liquid, min(size, len(liquid))), key=lambda r: r["isin"])
    u = {"drawn_on": as_of, "seed": seed, "size": len(picks), "stocks": [{"isin": p["isin"], "symbol": p["symbol"]} for p in picks]}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(u, indent=1), encoding="utf-8")
    return u


def forward_cases(store, u: dict, as_of: str, horizons=HORIZONS) -> list[dict]:
    have = {r["isin"] for r in store.query("SELECT isin FROM price_eod WHERE trade_date=? AND exchange='NSE' AND series='EQ'", (as_of,))}
    out = []
    for h in horizons:
        for s in u["stocks"]:
            if s["isin"] in have:
                out.append({"case_id": f"entry-{as_of}-{s['isin']}-h{h}", "gate": "entry", "as_of": as_of, "isin": s["isin"], "symbol": s["symbol"], "horizon_days": h})
        out.append({"case_id": f"macro-{as_of}-h{h}", "gate": "macro", "as_of": as_of, "isin": None, "symbol": None, "horizon_days": h})
    return out


def step(store, *, out_dir: Path, universe_path: Path, as_of: str | None = None, horizons=HORIZONS, size: int = UNIVERSE_SIZE, seed: int = 2026,
         min_gap: int = MIN_GAP, profile=None) -> dict:
    """One forward run. Returns what was done, or why it did nothing (too soon after the last forward date, too little history)."""
    out_dir = Path(out_dir)
    days = cs.sessions(store)
    if not days:
        return {"done": False, "why": "no stored prices"}
    as_of = as_of or days[-1]
    if as_of not in days:
        return {"done": False, "why": f"{as_of} is not a stored session"}
    if days.index(as_of) < cs.MIN_SESSIONS:
        return {"done": False, "why": f"fewer than {cs.MIN_SESSIONS} stored sessions before {as_of}"}
    prev = sorted(p.stem.removeprefix("cases-") for p in out_dir.glob("cases-*.json"))
    prev = [d for d in prev if d != as_of and d in days]
    if prev and 0 <= days.index(as_of) - days.index(prev[-1]) < min_gap:
        return {"done": False, "why": f"last forward date {prev[-1]} is under {min_gap} sessions before {as_of}; weekly is enough"}
    u = universe(store, universe_path, as_of=as_of, size=size, seed=seed)
    cases = forward_cases(store, u, as_of, horizons)
    cs.save(cases, out_dir / f"cases-{as_of}.json", {"forward": True, "as_of": as_of, "horizons": list(horizons), "universe": str(universe_path)})
    rules_n = cs.run_rules(store, cases)
    bundles = out_dir / f"bundles-{as_of}"
    ex = cs.export_bundles(store, cases, bundles, blind=True, profile=profile)
    return {"done": True, "as_of": as_of, "universe": u["size"], "cases": len(cases), "rules": rules_n, "bundles": ex["written"],
            "bundles_failed": ex["failed_count"], "bundles_dir": str(bundles), "key": ex["key"]}

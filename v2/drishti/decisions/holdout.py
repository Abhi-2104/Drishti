"""The one-time hold-out for rules-v2: judge frozen v2 on the half of history it was not built on (`drishti decisions holdout`).

Per case it scores three authors on the same stock-dates, exactly as the scorer does (edge = stance x excess return over the
Nifty, minus the 0.4% round trip on an ENTER; AVOID is judged as a short, WAIT as zero): rules-v1, rules-v2 and always-long. The
paired date-block bootstrap is `score.compare`, unchanged. Then the staggered-sleeve portfolio simulation runs for every strategy
on the same window. No model is involved and nothing is tuned here.

Guard: a window that reaches into the hold-out half (from rules_v2.HOLDOUT_FROM) writes `holdout-v2.json` and refuses to run
again while that file exists, so the half is opened once. A dev-half window is free to repeat and writes `holdout-v2-dev.json`.
"""
from __future__ import annotations

from pathlib import Path

from . import log, portfolio, rules_v2, score

HOLDOUT_FILE = "holdout-v2.json"
DEV_FILE = "holdout-v2-dev.json"


def touches_holdout(end: str | None) -> bool:
    return not end or end >= rules_v2.HOLDOUT_FROM


def _row(r: dict, author: str, action: str) -> dict:
    stance = log.GATES["entry"][1][action]
    excess = r["excess_pct"]
    net = stance * excess - (score.COST_PCT if stance != 0 and action in score.TRADE_ACTIONS else 0.0)
    return {"gate": "entry", "as_of": r["as_of"], "subject": r["isin"], "horizon_days": r["horizon_days"], "status": "scored", "scope": "stock",
            "author": author, "agent": author.split("/")[0], "model": author.split("/")[-1], "action": action, "stance": stance,
            "confidence": None, "excess_pct": excess, "edge_pct": stance * excess, "edge_net_pct": net,
            "hit": (stance * excess > 0) if stance else None}


def decisions(rows: list[dict]) -> tuple[list[dict], dict]:
    """Per-case rows for v1 and v2 on every scored case both can decide, plus the always-long baseline on the same cases."""
    _, v1_ver = _v1()
    _, v2_ver = rules_v2.load()
    a1, a2 = f"rules/{v1_ver}", f"rules/{v2_ver}"
    out, skipped = [], {"scored_cases": 0, "v1_no_call": 0, "v2_no_call": 0, "compared": 0}
    for r in rows:
        if r["status"] != "scored":
            continue
        skipped["scored_cases"] += 1
        d1 = r.get("rules_v1")
        d2 = rules_v2.score(r.get("price"), r.get("deal_flow"))
        skipped["v1_no_call"] += d1 is None
        skipped["v2_no_call"] += d2 is None
        if d1 is None or d2 is None:
            continue
        skipped["compared"] += 1
        out.append(_row(r, a1, d1["action"]))
        out.append(_row(r, a2, d2["action"]))
    return out + score.baseline_rows(out), {**skipped, "v1": a1, "v2": a2}


def _v1():
    from . import rules
    return rules.load()


def run(rows: list[dict], *, max_positions: int = 5, every: int = 10, window: tuple[str, str] = ("", "9999")) -> dict:
    lo, hi = window
    rows = [r for r in rows if lo <= r["as_of"] <= hi]
    drows, meta = decisions(rows)
    a1, a2, bl = meta["v1"], meta["v2"], "baseline/always-long"
    comps = {f"{x} vs {y}": score.compare(drows, x, y, gate="entry") for x, y in ((a2, a1), (a2, bl), (a1, bl))}
    summ = [s for s in score.summarise(drows) if s["gate"] == "entry"]
    enter = {a: sum(1 for r in drows if r["author"] == a and r["action"] == "ENTER") for a in (a1, a2)}
    sims = portfolio.run(rows, every=every, max_positions=max_positions, halves=(("window", lo, hi),))
    return {"window": {"start": lo or "first", "end": hi if hi != "9999" else "last", "rows": len(rows)}, "cases": meta, "enter_calls": enter,
            "summary": summ, "paired_compare": comps, "portfolio": sims["strategies"], "target_xirr_pct": 25.0,
            "limits": ["history has no insider, pledge or macro readings, so v2 here is price + deal readings only; v1 is its price points",
                       "cases overlap (10 stocks per date, 60-session horizon): low power; do not over-read one number",
                       "always-long carries no trading cost in the scorer; the portfolio simulation charges every stock period 0.4%",
                       "pre-tax, price-only, picks from the sampled stocks only (not a whole-market screen)",
                       "rules-v1's 2022-26 numbers were already seen on 18 Nov; only what v2 adds is a clean test"],
            "rules": {"v1": a1, "v2": a2}}


def execute(rows_path: Path, out_dir: Path, *, start: str | None, end: str | None, max_positions: int = 5, every: int = 10) -> tuple[dict, Path]:
    """Load rows, enforce the once-only guard, run, write the result. Returns (result, path). Raises FileExistsError if the
    hold-out was already opened."""
    from . import study
    out_dir = Path(out_dir)
    target = out_dir / (HOLDOUT_FILE if touches_holdout(end) else DEV_FILE)
    if touches_holdout(end) and target.exists():
        raise FileExistsError(f"{target} exists: the 2022-26 hold-out was already opened for {rules_v2.load()[1]}. "
                              f"Delete the file yourself only if you are knowingly retesting a changed rule set.")
    rows = study.load_rows(rows_path)
    res = run(rows, max_positions=max_positions, every=every, window=(start or "", end or "9999"))
    import json
    out_dir.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(res, indent=1), encoding="utf-8")
    return res, target

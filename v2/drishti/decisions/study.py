"""Which signal readings carry information about the next 60 sessions? (`drishti decisions signal-study`)

No model and no rules are judged here: every history case is a stock on a date, and the question is only whether the stock's
excess over the Nifty in the next 60 sessions differs by what a signal said that day.

Two steps, so the slow part runs once:
  1. rows: for every entry case of a history case file, the outcome (stock and Nifty return over the horizon) and every
     reading known on the date (price context and rules-v1's own call, delivery_trend, deal_flow, participant_oi,
     midcap_ratio), with the raw numbers. Written as JSON lines, resumable: a case already in the file is not recomputed.
  2. tables: per signal and reading, the number of cases and dates, the stocks' mean excess, and the LIFT with a 95% interval,
     for the whole period and for 2017-2021 and 2022-2026 separately, then a verdict.

Method, fixed before looking at any result:
  * Stock-level readings are judged WITHIN a date: each case's excess minus the mean excess of all scored cases that day. That
    removes market breadth, which otherwise dominates (a reading that fires on good days is not a stock signal).
  * Market-level readings (participant_oi, midcap_ratio) are the same for every stock on a date, so they are judged as the mean
    excess on dates with that reading minus the mean over all dates: a breadth timing signal, not a stock picker.
  * Intervals come from a circular moving-block bootstrap over consecutive case dates, block = horizon / spacing (6 dates for
    60-session outcomes taken every 10 sessions), because neighbouring cases share most of their 60 sessions. Checked on
    simulated noise before use: stock-level 95% intervals held a true zero 94% of the time. Market-level readings are
    persistent (a label lasts weeks), which made the same interval hold zero only 90% of the time, so for them the block is
    doubled and the 99% percentiles are used; that held a true zero 95% of the time. Either way 'ci95' means about 95%.
  * Verdict: 'stable' only if the full-period interval clears zero AND both halves point the same way with enough cases;
    'flips' if the halves disagree; otherwise 'no clear information'. Many readings are tested at once, so about 1 in 20 can
    clear zero by luck: the count expected by chance is printed with the table.
  * UNKNOWN is a reading of its own and is always shown, never dropped.
"""
from __future__ import annotations

import json
import random
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path
from statistics import mean

from .. import signals
from ..signals.base import params
from . import breakdown, rules, score

HALVES = breakdown.HALVES
MIN_CASES, MIN_DATES, MIN_HALF = 30, 8, 15
BOOT = 2000
UNKNOWN = "UNKNOWN"


# --- step 1: rows ---------------------------------------------------------------------------------------------------
def _sig(store, name: str, on: date, **kw) -> dict | None:
    r = signals.compute(store, name, on, **kw)
    return r.data if r.ok else None


def _comp(sig: dict | None, label: str):
    if not sig:
        return None
    return next((c["value"] for c in sig["components"] if c["input"] == label), None)


def market_readings(store, on: date) -> dict:
    po = _sig(store, "participant_oi", on)
    mc = _sig(store, "midcap_ratio", on)
    return {"participant_oi": None if po is None else {
                "score": po["score"], "percentile": po["details"].get("percentile"), "v1_label": po["details"].get("v1_label"),
                "label": po["label"].split(" (v1 rule")[0]},
            "midcap_ratio": None if mc is None else {
                "label": mc["label"], "change_pct": mc["score"], "vs_ma_pct": _comp(mc, f"vs {params('midcap_ratio')[0]['ma_window']}d average")}}


def stock_readings(store, isin: str, symbol: str | None, on: date) -> dict:
    pc = _sig(store, "price_context", on, isin=isin)
    dv = _sig(store, "delivery_trend", on, isin=isin, symbol=symbol)
    df = _sig(store, "deal_flow", on, isin=isin, symbol=symbol)
    dec = rules.decide_stock(store, "entry", isin, symbol, on)
    rets = _comp(pc, "return 1m / 3m / 6m / 1y %") or [None] * 4
    out = {"price": None if pc is None else {
               "close": pc["details"]["close"], "dma": pc["details"]["dma"], "ret_1m": rets[0], "ret_3m": rets[1], "ret_6m": rets[2],
               "ret_1y": rets[3], "rs_3m": _comp(pc, "strength vs Nifty, ~3 months"), "high_52w": pc["details"].get("high_52w"),
               "sessions": pc["details"].get("sessions")},
           "rules_v1": None if dec is None else {"action": dec["action"], "points": dec["points"], "thesis": dec["thesis"]},
           "delivery_trend": None if dv is None else {"label": dv["label"], **{k: dv["details"].get(k) for k in ("change_pp", "price_move_pct", "recent_avg", "baseline_avg")}},
           "deal_flow": None if df is None else {"label": df["label"], **{k: df["details"].get(k) for k in ("deals", "matched_pairs", "net_pct_of_volume", "short_pct_of_volume")}}}
    return out


def build_rows(store, cases: list[dict], out: Path, *, horizon: int | None = None, progress=None) -> dict:
    """Append one row per entry case to `out` (JSON lines). Cases already in the file are skipped, so a stopped run resumes."""
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    done = set()
    if out.exists():
        for line in out.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    done.add(json.loads(line)["case_id"])
                except (ValueError, KeyError):
                    continue
    todo = [c for c in cases if c["gate"] == "entry" and c["case_id"] not in done]
    market: dict[str, dict] = {}
    n = {"written": 0, "already": len(done), "scored": 0, "pending": 0, "no_data": 0, "todo": len(todo)}
    with out.open("a", encoding="utf-8") as fh:
        for i, c in enumerate(todo, 1):
            hz = horizon or c.get("horizon_days") or 60
            d = {"id": c["case_id"], "agent": "study", "model": "-", "gate": "entry", "scope": "stock", "as_of": c["as_of"], "subject": c["isin"],
                 "isin": c["isin"], "symbol": c.get("symbol"), "action": "LONG", "stance": 1, "confidence": None, "horizon_days": hz}
            o = score.outcome(store, d)
            on = date.fromisoformat(c["as_of"])
            if c["as_of"] not in market:
                market[c["as_of"]] = market_readings(store, on)
            row = {"case_id": c["case_id"], "as_of": c["as_of"], "isin": c["isin"], "symbol": c.get("symbol"), "horizon_days": hz,
                   "status": o["status"], "why": o.get("why"), "end": o.get("end"), "ret_pct": o.get("ret_pct"), "bench_pct": o.get("bench_pct"),
                   "excess_pct": o.get("excess_pct"), **stock_readings(store, c["isin"], c.get("symbol"), on), **market[c["as_of"]]}
            fh.write(json.dumps(row, default=str) + "\n")
            fh.flush()
            n["written"] += 1
            n[o["status"]] = n.get(o["status"], 0) + 1
            if progress and (i % 50 == 0 or i == len(todo)):
                progress(f"   signal-study rows: {i}/{len(todo)} ({c['as_of']})")
    return n


def load_rows(path: Path) -> list[dict]:
    return [json.loads(x) for x in Path(path).read_text(encoding="utf-8").splitlines() if x.strip()]


# --- step 2: tables ---------------------------------------------------------------------------------------------------
def _trend(r):
    return breakdown._trend(r["rules_v1"]["thesis"]) if r.get("rules_v1") else UNKNOWN


def _strength(r):
    rs = (r.get("price") or {}).get("rs_3m")
    return UNKNOWN if rs is None else "beating Nifty (3m)" if rs > 0 else "lagging Nifty (3m)"


def _chasing(r):
    m = (r.get("price") or {}).get("ret_1m")
    lim = rules.load()[0]["stock_points"]["chasing_above_pct"]
    return UNKNOWN if m is None else f"up more than {lim:g}% in a month" if m > lim else "not chasing"


def _near_high(r):
    p = r.get("price") or {}
    if not p.get("high_52w") or not p.get("close"):
        return UNKNOWN
    gap = (p["close"] / p["high_52w"] - 1) * 100
    return "within 5% of 52-week high" if gap >= -5 else "5-20% below high" if gap >= -20 else "more than 20% below high"


def _label(key):
    def f(r):
        v = r.get(key)
        return v["label"] if v else UNKNOWN
    return f


def _deal_side(r):
    v = r.get("deal_flow")
    return v["label"].split("; ")[0] if v else UNKNOWN


def _deal_short(r):
    v = r.get("deal_flow")
    return UNKNOWN if not v else "heavy short selling" if "heavy short selling" in v["label"] else "no heavy short selling"


def _po_pct(r):
    v = r.get("participant_oi")
    return v["label"] if v else UNKNOWN


def _po_v1(r):
    v = r.get("participant_oi")
    return (v.get("v1_label") or UNKNOWN) if v else UNKNOWN


def _rules_action(r):
    v = r.get("rules_v1")
    return v["action"] if v else UNKNOWN


# name -> (level, reading function, note). level 'stock' = judged within the date; 'market' = judged across dates.
READINGS = {
    "delivery_trend": ("stock", _label("delivery_trend"), "provisional; not read by rules-v1"),
    "deal_flow (side)": ("stock", _deal_side, "provisional; not read by rules-v1"),
    "deal_flow (short selling)": ("stock", _deal_short, "provisional; not read by rules-v1"),
    "participant_oi (vs own past year)": ("market", _po_pct, "market-wide: breadth timing, not stock picking"),
    "participant_oi (v1 fixed threshold)": ("market", _po_v1, "market-wide"),
    "midcap_ratio": ("market", _label("midcap_ratio"), "market-wide; rules-v1 uses it only at the macro gate"),
    "trend (rules-v1 input)": ("stock", _trend, "context: already in rules-v1"),
    "strength vs Nifty (rules-v1 input)": ("stock", _strength, "context: already in rules-v1"),
    "chasing (rules-v1 input)": ("stock", _chasing, "context: already in rules-v1"),
    "distance from 52-week high": ("stock", _near_high, "context: not in rules-v1"),
    "rules-v1 action": ("stock", _rules_action, "context: what rules-v1 decided"),
}


def _blocks(dates: list[str], block: int, rng: random.Random) -> list[str]:
    """Circular moving blocks: start anywhere, wrap past the end, so the first and last dates are drawn as often as the rest."""
    n = len(dates)
    out = []
    while len(out) < n:
        s = rng.randrange(n)
        out.extend(dates[(s + j) % n] for j in range(min(block, n)))
    return out[:n]


def _stat(per_date: dict, dates: list[str]) -> float | None:
    """per_date[d] = (sum_x_reading, n_reading, sum_x_all, n_all). Lift = mean over reading cases - mean over all cases."""
    sv = nv = sa = na = 0.0
    for d in dates:
        a, b, c, e = per_date[d]
        sv, nv, sa, na = sv + a, nv + b, sa + c, na + e
    if not nv or not na:
        return None
    return sv / nv - sa / na


def _cell(rows: list[dict], level: str, fn, value: str, block: int, seed: int) -> dict:
    by_date = defaultdict(list)
    for r in rows:
        by_date[r["as_of"]].append(r)
    dates = sorted(by_date)
    per_date, n, ex = {}, 0, []
    for d in dates:
        rs = by_date[d]
        dm = mean(r["excess_pct"] for r in rs)
        x = [(r["excess_pct"] - dm) if level == "stock" else r["excess_pct"] for r in rs]
        hit = [xi for r, xi in zip(rs, x) if fn(r) == value]
        per_date[d] = (sum(hit), len(hit), sum(x), len(x))
        n += len(hit)
        ex.extend(r["excess_pct"] for r in rs if fn(r) == value)
    hit_dates = sum(1 for d in dates if per_date[d][1])
    if not n:
        return {"n": 0, "dates": 0}
    lift = _stat(per_date, dates)
    rng = random.Random(f"{seed}|{value}")
    blk, q = (block, 0.025) if level == "stock" else (2 * block, 0.005)      # calibrated, see the module note
    boots = sorted(b for b in (_stat(per_date, _blocks(dates, blk, rng)) for _ in range(BOOT)) if b is not None)
    lo, hi = (boots[int(q * len(boots))], boots[int((1 - q) * len(boots)) - 1]) if len(boots) > 40 else (None, None)
    return {"n": n, "dates": hit_dates, "stocks_vs_nifty": round(mean(ex), 2), "lift": round(lift, 2),
            "ci95": [round(lo, 2), round(hi, 2)] if lo is not None else None}


def _verdict(full: dict, halves: dict) -> str:
    if full["n"] < MIN_CASES or full["dates"] < MIN_DATES:
        return "too few cases"
    hs = [h for h in halves.values() if h.get("n", 0) >= MIN_HALF and h.get("lift") is not None]
    if len(hs) == 2 and (hs[0]["lift"] > 0) != (hs[1]["lift"] > 0):
        return "flips between halves (regime-dependent)"
    ci = full.get("ci95")
    if ci and (ci[0] > 0 or ci[1] < 0):
        if len(hs) < 2:
            return "clears zero, but one half has too few cases"
        return "stable: better than average" if ci[0] > 0 else "stable: worse than average"
    return "no clear information"


def tables(rows: list[dict], *, since: str | None = None, until: str | None = None, block: int = 6, seed: int = 17) -> dict:
    scored = [r for r in rows if r["status"] == "scored" and (not since or r["as_of"] >= since) and (not until or r["as_of"] <= until)]
    if not scored:
        return {"error": "no scored rows in the window"}
    out, cells = {}, 0
    clears = []
    for name, (level, fn, note) in READINGS.items():
        vals = sorted({fn(r) for r in scored})
        sig = {"level": level, "note": note, "readings": {}}
        for v in vals:
            full = _cell(scored, level, fn, v, block, seed)
            halves = {}
            for hn, lo, hi in HALVES:
                part = [r for r in scored if lo <= r["as_of"] <= hi]
                halves[hn] = _cell(part, level, fn, v, block, seed) if part else {"n": 0, "dates": 0}
            verdict = _verdict(full, halves)
            sig["readings"][v] = {"all": full, **halves, "verdict": verdict}
            cells += v != UNKNOWN
            if verdict.startswith("stable"):
                clears.append(f"{name}: {v}")
        sig["coverage_known_pct"] = round(100 * sum(fn(r) != UNKNOWN for r in scored) / len(scored), 1)
        out[name] = sig
    dates = sorted({r["as_of"] for r in scored})
    return {"cases": len(scored), "dates": len(dates), "first": dates[0], "last": dates[-1],
            "not_scored": sum(1 for r in rows if r["status"] != "scored"), "block_dates": block,
            "readings_tested": cells, "expected_false_stable_by_chance": round(0.05 * cells, 1), "stable": clears,
            "how_to_read": ("lift = how much better (+) or worse (-) the stocks with this reading did over the next 60 sessions, in "
                            "percentage points vs Nifty, than the average case (stock level: vs the same day's other stocks; market "
                            "level: vs all dates). stocks_vs_nifty = their raw excess (what buying all of them earned, before costs). "
                            "ci95 = moving-block bootstrap. Only 'stable' readings are candidates for rules-v2."),
            "signals": out}


def run(store, *, cases_path: Path, out_dir: Path, since: str | None = None, until: str | None = None, progress=None) -> dict:
    from . import cases as cs
    cases, meta = cs.load(Path(cases_path))
    out_dir = Path(out_dir)
    rows_path = out_dir / "rows.jsonl"
    built = build_rows(store, cases, rows_path, progress=progress)
    every = int(meta.get("every") or 10)
    hz = cs.HORIZON
    t = tables(load_rows(rows_path), since=since, until=until, block=max(1, round(hz / every)))
    res = {"rows": built, "rows_file": str(rows_path), **t}
    (out_dir / "study.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
    res["written_to"] = str(out_dir / "study.json")
    return res


def _stderr(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)

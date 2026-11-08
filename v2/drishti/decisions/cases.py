"""Test cases for comparing decision-makers: a frozen list of (gate, date, stock), the same evidence for everyone, and a way to
take an agent's answers back into the log without letting it see (or change) which stock it was judging.

A case file is plain JSON and is the contract: rules-v1, Claude and Codex are all run on exactly these cases. Cases are chosen
by a seeded random draw from the most liquid names on each date, so nobody can have picked them to flatter a method.
"""
from __future__ import annotations

import json
import random
from datetime import date, timedelta
from pathlib import Path

from ..core.envelope import DrishtiError, ErrorKind, Result
from . import gather, log, rules

MIN_SESSIONS = 60
HORIZON = rules.load()[0]["entry"]["horizon_days"]      # one horizon for every author, so their calls can be paired
LIQUID_POOL = 300


def sessions(store) -> list[str]:
    return [r["trade_date"] for r in store.query("SELECT DISTINCT trade_date FROM price_eod WHERE exchange='NSE' ORDER BY trade_date")]


def make_cases(store, *, start: str, end: str, every: int = 10, n_stocks: int = 10, seed: int = 11, stock_gates=("entry",), market: bool = True,
               min_horizon_after: int = 0) -> list[dict]:
    """Every `every`-th stored session in [start, end] (needing MIN_SESSIONS of history before it, and `min_horizon_after`
    sessions after it so the call can be scored), `n_stocks` seeded-random picks from the LIQUID_POOL most-traded NSE stocks."""
    days = sessions(store)
    pool_days = [d for i, d in enumerate(days) if i >= MIN_SESSIONS and start <= d <= end and i + min_horizon_after < len(days)]
    out = []
    for d in pool_days[::every]:
        lo = (date.fromisoformat(d) - timedelta(days=30)).isoformat()
        liquid = store.query("SELECT isin, MAX(symbol) AS symbol FROM price_eod WHERE exchange='NSE' AND series='EQ' AND trade_date BETWEEN ? AND ? "
                             "AND isin IN (SELECT isin FROM price_eod WHERE trade_date=? AND exchange='NSE' AND series='EQ') "
                             "GROUP BY isin ORDER BY AVG(turnover) DESC, isin LIMIT ?", (lo, d, d, LIQUID_POOL))
        picks = random.Random(f"{seed}|{d}").sample(liquid, min(n_stocks, len(liquid))) if n_stocks else []
        for g in stock_gates:
            for p in sorted(picks, key=lambda r: r["isin"]):
                out.append({"case_id": f"{g}-{d}-{p['isin']}", "gate": g, "as_of": d, "isin": p["isin"], "symbol": p["symbol"], "horizon_days": HORIZON})
        if market:
            out.append({"case_id": f"macro-{d}", "gate": "macro", "as_of": d, "isin": None, "symbol": None, "horizon_days": HORIZON})
    return out


def save(cases: list[dict], path: Path, meta: dict) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps({"meta": meta, "cases": cases}, indent=1), encoding="utf-8")


def load(path: Path) -> tuple[list[dict], dict]:
    j = json.loads(Path(path).read_text(encoding="utf-8"))
    return j["cases"], j.get("meta", {})


def run_rules(store, cases: list[dict]) -> dict:
    """Decide every case with rules-v1. Counts what was written, what had too little data, and what was already there."""
    n = {"recorded": 0, "skipped_too_little_data": 0, "already_logged": 0, "failed": 0}
    for c in cases:
        r = rules.run(store, c["gate"], c["as_of"], isin=c["isin"], symbol=c["symbol"], horizon_days=c.get("horizon_days"))
        if r.ok:
            n["recorded"] += 1
        elif r.error.kind == ErrorKind.NOT_FOUND:
            n["skipped_too_little_data"] += 1
        elif "append-only" in r.error.detail:
            n["already_logged"] += 1
        else:
            n["failed"] += 1
    return n


def export_bundles(store, cases: list[dict], out_dir: Path, *, blind: bool = True, profile=None) -> dict:
    """One JSON evidence file per case (named by case_id; with blind=True the file names the case by number only) and a
    key file mapping the number back to the case. Agents get the bundles and never the key."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    key, bad = {}, []
    for i, c in enumerate(cases, 1):
        r = gather.gather(store, c["gate"], c["as_of"], isin=c["isin"], blind=blind, profile=profile, horizon_days=c.get("horizon_days"))
        if not r.ok:
            bad.append({"case_id": c["case_id"], "why": r.error.detail[:160]})
            continue
        cid = f"case-{i:05d}" if blind else c["case_id"]
        (out_dir / f"{cid}.json").write_text(json.dumps({"case_id": cid, **r.data}, indent=1, default=str), encoding="utf-8")
        key[cid] = {**c, "bundle_sha": r.data["bundle_sha"]}
    key_path = key_file(out_dir)
    key_path.write_text(json.dumps(key, indent=1), encoding="utf-8")
    return {"written": len(key), "failed": bad[:20], "failed_count": len(bad), "blind": blind, "key": str(key_path)}


def key_file(out_dir: Path) -> Path:
    """The key lives NEXT TO the bundle folder, never inside it: an agent allowed to read the bundles must not be able to read it."""
    out_dir = Path(out_dir)
    return out_dir.parent / f"{out_dir.name}_key.json"


def import_answers(store, key_path: Path, answers_path: Path, *, agent: str, model: str) -> dict:
    """Take an author's answers (one JSON object per line: case_id, action, horizon_days, confidence, thesis, invalidation, signals)
    and append them to the log. The stock comes from the key file, so a blind author never handles identities. Bad answers are
    reported with the reason, not fixed up."""
    key = json.loads(Path(key_path).read_text(encoding="utf-8"))
    n, rejected = 0, []
    for line_no, line in enumerate(Path(answers_path).read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            a = json.loads(line)
            c = key[a["case_id"]]
        except (ValueError, KeyError, TypeError):
            rejected.append({"line": line_no, "why": "not JSON, or case_id not in the key"})
            continue
        if a.get("horizon_days") not in (None, c["horizon_days"]):
            rejected.append({"line": line_no, "case_id": a["case_id"], "why": f"horizon_days must be {c['horizon_days']} for every author on this case"})
            continue
        r = log.record(store, gate=c["gate"], as_of=c["as_of"], action=str(a.get("action", "")), horizon_days=c["horizon_days"], thesis=a.get("thesis", ""),
                       agent=agent, model=model, signals_cited=a.get("signals") or [], isin=c["isin"], symbol=c["symbol"],
                       confidence=a.get("confidence"), invalidation=a.get("invalidation"), bundle_sha=c["bundle_sha"])
        if r.ok:
            n += 1
        else:
            rejected.append({"line": line_no, "case_id": a["case_id"], "why": r.error.detail[:200]})
    return {"recorded": n, "rejected": len(rejected), "rejections": rejected[:30]}

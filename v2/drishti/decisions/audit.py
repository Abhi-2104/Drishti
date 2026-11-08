"""Did holding its ground pay? For every case where a critic suggested a different action than the author's, compare what the
author's call earned with what the critic's suggestion would have earned, and say how often the author held.

Gross edge only (no trade cost), on scored cases only. Pending and no-data cases are counted, not guessed."""
from __future__ import annotations

from statistics import mean

from . import log, score


def _index(store, author: str) -> dict:
    a, m = author.split("/", 1)
    return {(d["gate"], d["as_of"], d["subject"]): d for d in log.current(store, agent=a, model=m)}


def audit(store, key: dict, critiques: dict, solo: str, revised: str | None = None) -> dict:
    S = _index(store, solo)
    R = _index(store, revised) if revised else {}
    rows, skipped = [], {"no_critique": 0, "no_decision": 0, "not_scored": 0}
    for cid, c in key.items():
        crit = critiques.get(cid)
        if crit is None:
            skipped["no_critique"] += 1
            continue
        k = (c["gate"], c["as_of"], c["isin"] or log.MARKET_KEY)
        d = S.get(k)
        if d is None:
            skipped["no_decision"] += 1
            continue
        o = score.outcome(store, d)
        if o["status"] != "scored":
            skipped["not_scored"] += 1
            continue
        sug = str(crit.get("suggested_action", "")).strip().upper()
        valid = sug in log.GATES[d["gate"]][1]
        rev = R.get(k)
        rows.append({"case_id": cid, "verdict": crit.get("verdict"), "action": d["action"], "stance": d["stance"], "suggested": sug if valid else None,
                     "differs": bool(valid and sug != d["action"]), "held": rev is None or rev["action"] == d["action"], "edge": o["edge_pct"],
                     "critic_edge": log.stance_of(d["gate"], sug) * o["excess_pct"] if valid else None})
    calls = [r for r in rows if r["stance"] != 0]
    dis = [r for r in rows if r["differs"]]
    held = [r for r in dis if r["held"]]
    changed = [r for r in dis if not r["held"]]

    def side(rs):
        if not rs:
            return {"n": 0}
        a_better = sum(r["edge"] > r["critic_edge"] for r in rs)
        c_better = sum(r["edge"] < r["critic_edge"] for r in rs)
        return {"n": len(rs), "author_mean_edge_pct": round(mean(r["edge"] for r in rs), 3), "critic_mean_edge_pct": round(mean(r["critic_edge"] for r in rs), 3),
                "author_better": a_better, "critic_better": c_better, "tied": len(rs) - a_better - c_better,
                "author_calls": sum(r["stance"] != 0 for r in rs), "author_calls_right": sum(r["stance"] != 0 and r["edge"] > 0 for r in rs)}
    verdicts: dict[str, int] = {}
    for r in rows:
        verdicts[str(r["verdict"])] = verdicts.get(str(r["verdict"]), 0) + 1
    return {"author": solo, "revised": revised, "scored_cases_with_critique": len(rows), "skipped": skipped, "critic_verdicts": verdicts,
            "author_calls": len(calls), "author_calls_right": sum(r["edge"] > 0 for r in calls),
            "critic_suggested_a_different_action": len(dis), "author_held": side(held), "author_changed": side(changed)}

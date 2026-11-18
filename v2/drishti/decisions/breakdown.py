"""Where does rules-v1's result come from? (`drishti decisions history-breakdown`)

Splits every scored rules-v1 ENTRY case three ways and reports, for each slice, what the stock actually did against the Nifty
(always-long's edge) next to what rules-v1 earned on it:

1. by action: ENTER / WAIT / AVOID. Separates "picked worse stocks" (ENTER cases lag) from "sat out rallies" (WAIT/AVOID cases did
   well) - the question the paired comparison cannot answer, because it scores a WAIT as zero.
2. by the readings rules-v1 used (trend, strength vs Nifty, macro tide, "chasing"), parsed from each decision's own reasons.
3. by breadth at the decision date, two ways:
   - known at the time: the midcap-vs-Nifty signal's label that day, and the share of that day's sampled liquid stocks in an uptrend.
   - hindsight only: the average excess of that day's sampled stocks over the next 60 sessions (a diagnosis, never a trading input).

Every table is also split into two halves (2017-2021 and 2022-2026): a pattern that holds in only one half is not a pattern.
"""
from __future__ import annotations

import re
from collections import defaultdict
from statistics import mean

from .. import signals
from . import rules, score

HALVES = (("2017-2021", "2017", "2021-12-31"), ("2022-2026", "2022", "9999"))


def _trend(thesis: str) -> str:
    if "close above the 50-day and 200-day" in thesis:
        return "uptrend"
    if "close below the 50-day and 200-day" in thesis:
        return "downtrend"
    if "mixed trend" in thesis:
        return "mixed"
    return "short history"


def _strength(thesis: str) -> str:
    m = re.search(r"([+-]\d+(?:\.\d+)?)% against the Nifty", thesis)
    if not m:
        return "unknown"
    return "beating Nifty" if float(m.group(1)) > 0 else "lagging Nifty"


def _macro(thesis: str) -> str:
    m = re.search(r"([+-]\d+) macro tide", thesis)
    return {"+": "macro supportive", "-": "macro hostile"}[m.group(1)[0]] if m else "macro neutral/unknown"


def _row(rs: list[dict]) -> dict:
    """n, the stocks' own excess vs Nifty (= always-long), rules' mean edge per case (WAIT = 0), and the difference."""
    ex = [r["excess_pct"] for r in rs]
    rule = [r["edge_net_pct"] if r["stance"] else 0.0 for r in rs]
    al = mean(ex)
    return {"n": len(rs), "stocks_vs_nifty": round(al, 2), "rules_per_case": round(mean(rule), 2), "rules_minus_always_long": round(mean(rule) - al, 2)}


def _table(rs: list[dict], key) -> dict:
    g = defaultdict(list)
    for r in rs:
        g[key(r)].append(r)
    out = {}
    for k in sorted(g):
        row = {"all": _row(g[k])}
        for name, lo, hi in HALVES:
            part = [r for r in g[k] if lo <= r["as_of"] <= hi]
            row[name] = _row(part) if len(part) >= 10 else {"n": len(part), "note": "too few cases"}
        out[k] = row
    return out


def _tercile(values: dict[str, float]) -> dict[str, str]:
    s = sorted(values.values())
    if len(s) < 3:
        return {k: "n/a" for k in values}
    lo, hi = s[len(s) // 3], s[2 * len(s) // 3]
    return {k: ("weak" if v < lo else "strong" if v >= hi else "middle") for k, v in values.items()}


def run(store, *, since: str | None = None, until: str | None = None) -> dict:
    _, ver = rules.load()
    rows = [r for r in score.score_all(store, agent=rules.AGENT, model=ver, gate="entry", since=since, until=until) if r["status"] == "scored"]
    if not rows:
        return {"error": "no scored rules-v1 entry cases; run `drishti decisions history` first"}
    from . import log
    thesis = {d["id"]: d["thesis"] for d in log.current(store, agent=rules.AGENT, model=ver, gate="entry", since=since, until=until)}
    for r in rows:
        t = thesis.get(r["id"], "")
        r["trend"], r["strength"], r["macro"] = _trend(t), _strength(t), _macro(t)
        r["chasing"] = "chasing (up a lot in a month)" if "already up" in t else "not chasing"
    by_date = defaultdict(list)
    for r in rows:
        by_date[r["as_of"]].append(r)
    hind = _tercile({d: mean(x["excess_pct"] for x in rs) for d, rs in by_date.items()})
    upshare = _tercile({d: sum(x["trend"] == "uptrend" for x in rs) / len(rs) for d, rs in by_date.items()})
    midcap = {}
    for d in by_date:
        res = signals.compute(store, "midcap_ratio", d)
        midcap[d] = res.data["label"] if res.ok else "unknown"
    for r in rows:
        r["breadth_hindsight"] = f"{hind[r['as_of']]} breadth (hindsight)"
        r["uptrend_share"] = f"{upshare[r['as_of']]} share of sampled stocks in uptrend"
        r["midcap_vs_nifty"] = midcap[r["as_of"]]
    return {
        "author": f"rules/{ver}", "cases": len(rows), "dates": len(by_date),
        "how_to_read": ("stocks_vs_nifty = what the stocks did vs the Nifty over 60 sessions (always-long's edge); rules_per_case = rules-v1's "
                        "edge per case with WAIT counted as 0; rules_minus_always_long > 0 means rules-v1 did better on that slice. "
                        "Hindsight rows describe the period; they cannot be traded."),
        "overall": _row(rows),
        "by_action": _table(rows, lambda r: r["action"]),
        "by_trend": _table(rows, lambda r: r["trend"]),
        "by_strength": _table(rows, lambda r: r["strength"]),
        "by_macro": _table(rows, lambda r: r["macro"]),
        "by_chasing": _table(rows, lambda r: r["chasing"]),
        "by_midcap_vs_nifty_known_then": _table(rows, lambda r: r["midcap_vs_nifty"]),
        "by_uptrend_share_known_then": _table(rows, lambda r: r["uptrend_share"]),
        "by_breadth_hindsight": _table(rows, lambda r: r["breadth_hindsight"]),
    }

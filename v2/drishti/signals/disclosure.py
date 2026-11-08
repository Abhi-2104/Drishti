"""▲ Pulled forward from P5 (approved 8 Nov): single-name disclosure signals that /entry and /exit already ask for.

scoring.md ranks promoter/insider trades first ("size buying = strong +; selling cluster = strong -") and treats a rising
pledge as a red flag and >25% pledged as strong caution. The words "size" and "cluster" were never defined, so those two
thresholds are provisional in params.toml. Scores are left to the decision layer; these return labels and flags.
"""
from __future__ import annotations

from datetime import date, timedelta

from .base import Component, Signal, ok, params, unknown


def _on(on) -> date:
    return on if isinstance(on, date) else date.fromisoformat(on) if on else date.today()


def _market_trades(store, isin: str | None, start: str, end: str, p: dict, symbol: str | None = None) -> list[dict]:
    if not store.query("SELECT 1 AS x FROM sqlite_master WHERE name='insider_event'"):
        return []
    where = "isin = ?" if isin else "symbol = ?"
    rows = store.query(f"SELECT * FROM insider_event WHERE {where} AND to_date BETWEEN ? AND ? AND direction IN ('BUY','SELL')",
                       (isin or symbol, start, end))
    cats, modes = {c.lower() for c in p["insider_categories"]}, {m.lower() for m in p["market_modes"]}
    return [r for r in rows if (r["category"] or "").lower() in cats and (r["mode"] or "").lower() in modes]


def _clusters(trades: list[dict], side: str, days: int, min_persons: int) -> list[dict]:
    """Every window of `days` (anchored on a trade date) with >= min_persons distinct insiders trading on `side`."""
    t = sorted((r for r in trades if r["direction"] == side), key=lambda r: r["to_date"])
    out = []
    for i, a in enumerate(t):
        end = (date.fromisoformat(a["to_date"]) + timedelta(days=days)).isoformat()
        group = [r for r in t[i:] if r["to_date"] <= end]
        persons = {(r["person"] or "").strip().upper() for r in group}
        if len(persons) >= min_persons:
            out.append({"from": a["to_date"], "to": group[-1]["to_date"], "persons": sorted(persons),
                        "value_inr": round(sum(r["value_inr"] or 0 for r in group), 2)})
    return out


def insider_cluster(store, isin: str, on=None, *, symbol: str | None = None):
    p, ver = params("insider_cluster")
    d = _on(on)
    if not store.query("SELECT 1 AS x FROM sqlite_master WHERE name='insider_event'"):
        return unknown("insider_cluster", "no insider data stored; run `drishti run insider`")
    start = (d - timedelta(days=p["window_days"])).isoformat()
    trades = _market_trades(store, isin, start, d.isoformat(), p, symbol)
    buys = [r for r in trades if r["direction"] == "BUY"]
    sells = [r for r in trades if r["direction"] == "SELL"]
    bv, sv = sum(r["value_inr"] or 0 for r in buys), sum(r["value_inr"] or 0 for r in sells)
    bc = _clusters(trades, "BUY", p["cluster_days"], p["cluster_min_persons"])
    sc = _clusters(trades, "SELL", p["cluster_days"], p["cluster_min_persons"])
    flags = []
    if sc:
        flags.append("insider selling cluster (scoring.md: strong negative)")
    if bc or bv >= p["size_buy_crore"] * 1e7:
        flags.append("insider size buying (scoring.md: strong positive)")
    label = "conflicting insider activity" if len(flags) == 2 else flags[0] if flags else ("minor insider activity" if trades else "no open-market insider trades")
    comps = [Component("open-market buys", len(buys), d.isoformat(), f"last {p['window_days']} days", None, f"Rs {bv:,.0f} by {len({r['person'] for r in buys})} insiders"),
             Component("open-market sells", len(sells), d.isoformat(), f"last {p['window_days']} days", None, f"Rs {sv:,.0f} by {len({r['person'] for r in sells})} insiders"),
             Component("buy clusters", len(bc), d.isoformat(), f">= {p['cluster_min_persons']} insiders within {p['cluster_days']} days (provisional)"),
             Component("sell clusters", len(sc), d.isoformat(), f">= {p['cluster_min_persons']} insiders within {p['cluster_days']} days (provisional)")]
    newest = store.query("SELECT MAX(filed_at) AS f FROM insider_event")[0]["f"]
    missing = [] if newest and newest[:10] >= (d - timedelta(days=5)).isoformat() else ["insider data is not current (run `drishti run insider`)"]
    return ok(Signal("insider_cluster", d.isoformat(), None, label, comps, missing,
                     "Only open-market trades by promoters, directors, KMP and designated persons count; pledges, gifts, ESOP and "
                     "inter-se transfers do not. Window and cluster definitions are provisional.", ver, provisional=True,
                     details={"isin": isin, "flags": flags, "buy_value_inr": bv, "sell_value_inr": sv, "buy_clusters": bc[:3],
                              "sell_clusters": sc[:3], "trades": trades[:25]}))


def pledge(store, isin: str, on=None):
    p, ver = params("pledge")
    d = _on(on)
    if not store.query("SELECT 1 AS x FROM sqlite_master WHERE name='shp_filing'"):
        return unknown("pledge", "no shareholding filings stored; run the shp backfill")
    rows = store.query("SELECT * FROM shp_filing WHERE isin=? AND as_on <= ? ORDER BY as_on DESC LIMIT 2", (isin, d.isoformat()))
    if not rows:
        return unknown("pledge", f"no shareholding filing stored for {isin}")
    cur, prev = rows[0], (rows[1] if len(rows) > 1 else None)
    flags, missing = [], []
    pl = cur["pledged_pct_of_promoter"] or 0.0
    if cur["no_promoter"]:
        return ok(Signal("pledge", cur["as_on"], None, "no promoter (professionally managed): pledge not applicable",
                         [Component("promoter holding", 0, cur["as_on"])], [], "", ver, details={"isin": isin}))
    if pl > p["strong_caution_pledged_pct"]:
        flags.append(f">{p['strong_caution_pledged_pct']:.0f}% of promoter holding pledged (scoring.md: strong caution)")
    rise = None
    if prev:
        rise = round(pl - (prev["pledged_pct_of_promoter"] or 0.0), 2)
        if rise >= p["rising_min_pp"]:
            flags.append(f"rising pledge, +{rise}pp QoQ (scoring.md: red flag)")
    elif cur["encumbered_shares"]:
        missing.append("previous quarter's filing (needed to judge a rising pledge)")
    enc = cur["encumbered_pct_of_promoter"] or 0.0
    label = "; ".join(flags) if flags else ("no pledge" if not cur["encumbered_shares"] else "pledge present, no flag")
    comps = [Component("pledged % of promoter holding", pl, cur["as_on"], f"> {p['strong_caution_pledged_pct']} strong caution"),
             Component("all encumbrance % of promoter holding", enc, cur["as_on"], "pledge + non-disposal undertaking + other"),
             Component("QoQ change in pledged %", rise, cur["as_on"], f">= +{p['rising_min_pp']}pp rising (provisional)"),
             Component("promoter holding %", cur["promoter_pct"], cur["as_on"])]
    if not cur["flags_consistent"]:
        missing.append("the filing's encumbrance tick-boxes disagree with its share counts; read the filing")
    return ok(Signal("pledge", cur["as_on"], None, label, comps, missing,
                     "From the quarterly shareholding-pattern XBRL; percentages recomputed from share counts. A non-disposal "
                     "undertaking is encumbrance but not a pledge, so it is shown separately.", ver, provisional=False,
                     details={"isin": isin, "flags": flags, "previous_as_on": prev["as_on"] if prev else None}))


def screen(store, on=None, *, limit: int = 30) -> dict:
    """Market-wide discovery for /brief and /screen: names with an insider cluster/size buying, and names with pledge flags."""
    p_i, _ = params("insider_cluster")
    p_p, _ = params("pledge")
    d = _on(on)
    out = {"as_of": d.isoformat(), "insider": [], "pledge": []}
    if store.query("SELECT 1 AS x FROM sqlite_master WHERE name='insider_event'"):
        start = (d - timedelta(days=p_i["cluster_days"])).isoformat()
        for r in store.query("SELECT DISTINCT COALESCE(isin, symbol) AS k, isin, symbol FROM insider_event WHERE to_date BETWEEN ? AND ? "
                             "AND direction IN ('BUY','SELL')", (start, d.isoformat())):
            s = insider_cluster(store, r["isin"], d, symbol=r["symbol"])
            if s.ok and s.data["details"]["flags"]:
                out["insider"].append({"isin": r["isin"], "symbol": r["symbol"], "label": s.data["label"],
                                       "buy_value_inr": s.data["details"]["buy_value_inr"], "sell_value_inr": s.data["details"]["sell_value_inr"]})
    if store.query("SELECT 1 AS x FROM sqlite_master WHERE name='shp_filing'"):
        latest = store.query("SELECT MAX(as_on) AS q FROM shp_filing WHERE as_on <= ?", (d.isoformat(),))[0]["q"]
        for r in store.query("SELECT isin, symbol FROM shp_filing WHERE as_on=? AND encumbered_shares > 0", (latest,)):
            s = pledge(store, r["isin"], d)
            if s.ok and s.data["details"].get("flags"):
                out["pledge"].append({"isin": r["isin"], "symbol": r["symbol"], "label": s.data["label"],
                                      "pledged_pct": s.data["components"][0]["value"]})
    out["insider"].sort(key=lambda x: -(x["buy_value_inr"] + x["sell_value_inr"]))
    out["pledge"].sort(key=lambda x: -(x["pledged_pct"] or 0))
    out["insider"], out["pledge"] = out["insider"][:limit], out["pledge"][:limit]
    return out

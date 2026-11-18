"""Single-name flow signals from exchange files that the decision bundles did not use yet (added 17 Nov 2026, provisional).

deal_flow       bulk and block deals in the stock over a window: who bought vs who sold, net of matched pairs (the same
                quantity at the same price bought and sold on one day is a transfer between related parties, not demand).
delivery_trend  the share of traded quantity taken for delivery, recent vs its own baseline, read together with the price move:
                rising delivery on a rising price reads as accumulation, on a falling price as distribution.

Both return labels and components only (no score): the decision layer weighs them. rules-v1 is frozen and does not read them.
A window that the stored data does not cover is UNKNOWN, never "no deals".
"""
from __future__ import annotations

from datetime import date, timedelta

from .base import Component, Signal, ok, params, unknown


def _on(on) -> date:
    return on if isinstance(on, date) else date.fromisoformat(on) if on else date.today()


def _has(store, table: str) -> bool:
    return bool(store.query("SELECT 1 AS x FROM sqlite_master WHERE type='table' AND name=?", (table,)))


def _net_matched(rows: list[dict]) -> tuple[list[dict], int]:
    """Drop BUY/SELL pairs with the same day, quantity and price (one party sold to a related one). Returns (kept, pairs dropped)."""
    buys: dict[tuple, list[dict]] = {}
    for r in rows:
        if r["side"] == "BUY":
            buys.setdefault((r["trade_date"], r["qty"], r["price"]), []).append(r)
    drop, pairs = set(), 0
    for r in rows:
        if r["side"] != "SELL":
            continue
        match = buys.get((r["trade_date"], r["qty"], r["price"]))
        if match:
            b = match.pop()
            drop.update({id(b), id(r)})
            pairs += 1
    return [r for r in rows if id(r) not in drop], pairs


def deal_flow(store, isin: str | None = None, on=None, *, symbol: str | None = None):
    p, ver = params("deal_flow")
    d = _on(on)
    if not symbol:
        return unknown("deal_flow", "deals are keyed by NSE symbol and none was given")
    if not _has(store, "deal") or not _has(store, "price_eod"):
        return unknown("deal_flow", "no deal data stored; run `drishti backfill deals`")
    start = (d - timedelta(days=p["window_days"])).isoformat()
    first = store.query("SELECT MIN(trade_date) AS f FROM deal WHERE kind IN ('bulk','block')")[0]["f"]
    if not first or first > start:
        return unknown("deal_flow", f"stored deals start {first or 'nowhere'}, after the {p['window_days']}-day window begins")
    rows = store.query("SELECT kind, trade_date, side, qty, price FROM deal WHERE symbol=? AND kind IN ('bulk','block') AND trade_date BETWEEN ? AND ?",
                       (symbol, start, d.isoformat()))
    shorts = store.query("SELECT COALESCE(SUM(qty), 0) AS q FROM deal WHERE symbol=? AND kind='short' AND trade_date BETWEEN ? AND ?",
                         (symbol, start, d.isoformat()))[0]["q"]
    vol = store.query("SELECT COALESCE(SUM(volume), 0) AS v FROM price_eod WHERE exchange='NSE' AND symbol=? AND trade_date BETWEEN ? AND ?",
                      (symbol, start, d.isoformat()))[0]["v"]
    kept, pairs = _net_matched([dict(r) for r in rows])
    bq = sum(r["qty"] for r in kept if r["side"] == "BUY")
    sq = sum(r["qty"] for r in kept if r["side"] == "SELL")
    bv = sum(r["qty"] * (r["price"] or 0) for r in kept if r["side"] == "BUY")
    sv = sum(r["qty"] * (r["price"] or 0) for r in kept if r["side"] == "SELL")
    net_pct = round((bq - sq) / vol * 100, 2) if vol else None
    short_pct = round(shorts / vol * 100, 2) if vol else None
    if not kept:
        label = "no bulk/block deals" if not pairs else "only matched transfers (no net demand)"
    elif net_pct is None:
        label = "deals, but no traded volume stored to size them"
    elif net_pct >= p["net_pct_of_volume"]:
        label = "net deal buying"
    elif net_pct <= -p["net_pct_of_volume"]:
        label = "net deal selling"
    else:
        label = "two-way deals (no clear side)"
    if short_pct is not None and short_pct >= p["short_pct_of_volume"]:
        label += "; heavy short selling"
    w = f"last {p['window_days']} days"
    comps = [Component("bulk/block bought (qty)", bq, d.isoformat(), w, None, f"Rs {bv:,.0f}"),
             Component("bulk/block sold (qty)", sq, d.isoformat(), w, None, f"Rs {sv:,.0f}"),
             Component("net as % of traded volume", net_pct, d.isoformat(), f">= +{p['net_pct_of_volume']}% buying, <= -{p['net_pct_of_volume']}% selling"),
             Component("matched buy/sell pairs removed", pairs, d.isoformat(), "same day, quantity and price: a transfer, not demand"),
             Component("short sales as % of traded volume", short_pct, d.isoformat(), f">= {p['short_pct_of_volume']}% = heavy")]
    return ok(Signal("deal_flow", d.isoformat(), None, label, comps, [],
                     "NSE bulk and block deals (and short sales) by trade date; buyers and sellers are not classified (promoter, "
                     "fund, trader). Thresholds are provisional.", ver, provisional=True,
                     details={"deals": len(kept), "matched_pairs": pairs, "net_pct_of_volume": net_pct, "short_pct_of_volume": short_pct}))


def delivery_trend(store, isin: str | None = None, on=None, *, symbol: str | None = None):
    p, ver = params("delivery_trend")
    d = _on(on)
    if not _has(store, "delivery"):
        return unknown("delivery_trend", "no delivery data stored; run `drishti backfill delivery`")
    key, val = ("isin", isin) if isin else ("symbol", symbol)
    if not val:
        return unknown("delivery_trend", "no stock given")
    rows = store.query(f"SELECT trade_date, deliv_per FROM delivery WHERE {key}=? AND series='EQ' AND trade_date <= ? AND deliv_per IS NOT NULL "
                       "ORDER BY trade_date DESC LIMIT ?", (val, d.isoformat(), p["recent"] + p["baseline"]))
    if len(rows) < p["recent"] + p["min_baseline"]:
        return unknown("delivery_trend", f"only {len(rows)} sessions of delivery data up to this date; need {p['recent'] + p['min_baseline']}")
    if (d - date.fromisoformat(rows[0]["trade_date"])).days > 7:
        return unknown("delivery_trend", f"latest delivery figure is from {rows[0]['trade_date']}")
    recent, base = rows[:p["recent"]], rows[p["recent"]:]
    r_avg = sum(x["deliv_per"] for x in recent) / len(recent)
    b_avg = sum(x["deliv_per"] for x in base) / len(base)
    change = round(r_avg - b_avg, 2)
    px = store.query(f"SELECT trade_date, close FROM price_eod WHERE {key}=? AND exchange='NSE' AND trade_date BETWEEN ? AND ? ORDER BY trade_date",
                     (val, recent[-1]["trade_date"], recent[0]["trade_date"]))
    move = round((px[-1]["close"] / px[0]["close"] - 1) * 100, 2) if len(px) >= 2 and px[0]["close"] else None
    t = p["change_pp"]
    if change >= t and move is not None and move > 0:
        label = "delivery rising on a rising price (accumulation)"
    elif change >= t and move is not None and move < 0:
        label = "delivery rising on a falling price (distribution)"
    elif change >= t:
        label = "delivery rising"
    elif change <= -t:
        label = "delivery falling (more intraday / speculative trading)"
    else:
        label = "delivery steady"
    comps = [Component(f"delivery % avg, last {p['recent']} sessions", round(r_avg, 2), recent[0]["trade_date"]),
             Component(f"delivery % avg, the {len(base)} sessions before", round(b_avg, 2), base[0]["trade_date"]),
             Component("change (percentage points)", change, recent[0]["trade_date"], f"+/-{t}pp = a real shift"),
             Component(f"price move over the last {p['recent']} sessions %", move, recent[0]["trade_date"])]
    return ok(Signal("delivery_trend", d.isoformat(), None, label, comps, [],
                     "NSE security-wise delivery position (EQ series). Delivery % is quantity taken for delivery over quantity traded. "
                     "Thresholds are provisional.", ver, provisional=True,
                     details={"recent_avg": round(r_avg, 2), "baseline_avg": round(b_avg, 2), "change_pp": change, "price_move_pct": move}))

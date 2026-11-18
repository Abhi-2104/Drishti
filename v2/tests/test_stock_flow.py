"""deal_flow and delivery_trend: matched transfers netted out, sizing against traded volume, coverage = UNKNOWN, accumulation vs distribution."""
from __future__ import annotations

from datetime import date, timedelta

from drishti.signals import compute
from drishti.store import Store
from drishti.store import datasets as ds

ON = date(2024, 6, 28)


def _days(n, end=ON):
    out, d = [], end
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d -= timedelta(days=1)
    return sorted(out)


def _store(close_step=0.0, deliv=lambda i: 40.0):
    st = Store()
    ds.ensure(st)
    days = _days(200)
    st.upsert("price_eod", [{"trade_date": d, "isin": "INE1", "exchange": "NSE", "series": "EQ", "symbol": "ABC", "open": 100, "high": 101,
                             "low": 99, "close": 100 * (1 + close_step) ** i, "prev_close": 100, "volume": 100_000, "turnover": 1e7, "trades": 10}
                            for i, d in enumerate(days)], key=["trade_date", "isin", "exchange", "series"])
    st.upsert("delivery", [{"trade_date": d, "symbol": "ABC", "series": "EQ", "isin": "INE1", "ttl_qty": 100_000, "deliv_qty": 1,
                            "deliv_per": deliv(i), "trades": 10} for i, d in enumerate(days)], key=["trade_date", "symbol", "series"])
    return st, days


def _deal(st, kind, day, client, side, qty, price):
    st.upsert("deal", [{"kind": kind, "trade_date": day, "symbol": "ABC", "client": client, "side": side, "qty": qty, "price": price,
                        "company": "ABC LTD", "source": "t"}], key=["kind", "trade_date", "symbol", "client", "side", "qty"])


def test_deal_flow_nets_matched_transfers_and_sizes_against_volume():
    st, days = _store()
    _deal(st, "bulk", days[0], "OLD FUND", "BUY", 1, 1.0)            # coverage marker far back
    _deal(st, "block", days[-10], "FUND A", "SELL", 500_000, 100.0)   # a transfer: same day, qty, price
    _deal(st, "block", days[-10], "FUND B", "BUY", 500_000, 100.0)
    _deal(st, "bulk", days[-5], "FUND C", "BUY", 200_000, 101.0)      # real buying: 200k of ~4.2m traded in the window (~4.8%)
    r = compute(st, "deal_flow", ON, isin="INE1", symbol="ABC")
    assert r.ok and r.data["label"] == "net deal buying"
    assert r.data["details"]["matched_pairs"] == 1 and r.data["details"]["net_pct_of_volume"] > 2


def test_deal_flow_is_unknown_before_the_stored_deals_begin():
    st, days = _store()
    _deal(st, "bulk", days[-3], "X", "BUY", 10, 1.0)
    r = compute(st, "deal_flow", ON, isin="INE1", symbol="ABC")
    assert not r.ok and "UNKNOWN" in r.error.detail


def test_deal_flow_only_transfers_is_not_demand():
    st, days = _store()
    _deal(st, "bulk", days[0], "OLD", "BUY", 1, 1.0)
    _deal(st, "block", days[-2], "P", "SELL", 9, 5.0)
    _deal(st, "block", days[-2], "Q", "BUY", 9, 5.0)
    r = compute(st, "deal_flow", days[-2], isin="INE1", symbol="ABC")
    assert r.ok and r.data["label"] == "only matched transfers (no net demand)"


def test_delivery_rising_on_rising_price_is_accumulation_and_on_falling_is_distribution():
    up, _ = _store(close_step=0.003, deliv=lambda i: 60.0 if i >= 180 else 40.0)
    assert compute(up, "delivery_trend", ON, isin="INE1").data["label"].endswith("(accumulation)")
    down, _ = _store(close_step=-0.003, deliv=lambda i: 60.0 if i >= 180 else 40.0)
    assert compute(down, "delivery_trend", ON, isin="INE1").data["label"].endswith("(distribution)")
    flat, _ = _store()
    assert compute(flat, "delivery_trend", ON, isin="INE1").data["label"] == "delivery steady"


def test_delivery_trend_needs_enough_history():
    st, days = _store()
    r = compute(st, "delivery_trend", days[30], isin="INE1")
    assert not r.ok and "need" in r.error.detail

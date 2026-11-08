"""Split / bonus / rights adjustment from the exchange's own adjusted previous close (no fixtures: synthetic bars)."""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from drishti.signals import price
from drishti.store import Store
from drishti.store import datasets as ds

ISIN = "INE002A01018"


def sessions(n: int, start: date = date(2026, 9, 1)) -> list[str]:
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def bar(day, close, prev, isin=ISIN, exchange="NSE", series="EQ", vol=1000):
    return {"trade_date": day, "isin": isin, "series": series, "symbol": "X", "open": close, "high": close * 1.01, "low": close * 0.99,
            "close": close, "prev_close": prev, "volume": vol, "turnover": 1, "trades": 1}


@pytest.fixture
def st():
    s = Store()
    yield s
    s.close()


def put(st, rows, exchange="NSE"):
    by = {}
    for r in rows:
        by.setdefault(r["trade_date"], []).append(r)
    for d in sorted(by):
        ds.persist_prices(st, by[d], exchange)


def split_history(days, ex_i, ratio=10.0):
    """1000 -> split 1:ratio on days[ex_i]: from then the stock trades near 1000/ratio, and NSE's prev_close that day
    is the last close divided by the ratio."""
    rows, last = [], None
    for i, d in enumerate(days):
        c = 1000.0 + i if i < ex_i else (1000.0 + i) / ratio
        prev = last if last is not None else c
        if i == ex_i:
            prev = last / ratio
        rows.append(bar(d, c, prev))
        last = c
    return rows


def test_a_split_is_found_from_the_adjusted_previous_close(st):
    days = sessions(30)
    put(st, split_history(days, 20))
    r = ds.rebuild_adjustments(st)
    assert r["events"] == 1 and not r["gap_days"]
    e = ds.adjustments_for(st, ISIN)[0]
    assert e["ex_date"] == days[20] and e["factor"] == pytest.approx(0.1) and e["kind"] is None


def test_price_context_reads_through_the_split(st):
    days = sessions(30)
    put(st, split_history(days, 20))
    ds.rebuild_adjustments(st)
    sig = price.price_context(st, ISIN, on=days[-1]).data
    one_month = sig["components"][1]["value"][0]
    assert one_month == pytest.approx((1029 / 1008 - 1) * 100, abs=0.05)       # not -90%
    assert sig["details"]["high_52w"] < 110                                       # the pre-split 1000s are scaled down
    assert sig["details"]["adjustments"][0]["factor"] == pytest.approx(0.1)
    assert any(c["input"] == "adjusted for" for c in sig["components"])


def test_dividends_and_ordinary_days_are_not_adjustments(st):
    days = sessions(10)
    rows, last = [], 500.0
    for d in days:
        rows.append(bar(d, last - 5, last))          # prev_close == the stored last close, even on an ex-dividend day
        last -= 5
    put(st, rows)
    assert ds.rebuild_adjustments(st)["events"] == 0


def test_a_missing_session_is_not_mistaken_for_splits(st):
    days = sessions(6)
    rows = []
    for k in range(60):
        isin = f"INE{k:03d}A01010"
        last = None
        for i, d in enumerate(days):
            if i == 3:
                continue                               # the store never got this day
            c = 100.0 + i * 2
            prev = (100.0 + (i - 1) * 2) if i else c   # the exchange's prev close is the real previous day's
            rows.append(bar(d, c, prev, isin=isin))
    put(st, rows)
    r = ds.rebuild_adjustments(st)
    assert r["events"] == 0 and [g["date"] for g in r["gap_days"]] == [days[4]]


def test_events_are_labelled_from_stored_corporate_actions_and_incremental_rebuild(st):
    days = sessions(30)
    put(st, split_history(days, 20, ratio=2.0))
    ds.persist_corp_actions(st, [{"symbol": "X", "isin": ISIN, "company": "X", "series": "EQ", "subject": "Bonus 1:1", "kind": "bonus",
                                  "ex_date": days[20], "record_date": None, "bc_start": None, "bc_end": None, "face_value": 10.0}])
    ds.rebuild_adjustments(st, since=days[15])
    e = ds.adjustments_for(st, ISIN)
    assert len(e) == 1 and e[0]["kind"] == "bonus" and e[0]["factor"] == pytest.approx(0.5)


def test_adjustment_applies_only_to_its_exchange_and_series():
    bars = [{"trade_date": "2026-10-01", "exchange": "BSE", "series": "B", "open": 10, "high": 10, "low": 10, "close": 10, "volume": 10},
            {"trade_date": "2026-10-02", "exchange": "NSE", "series": "EQ", "open": 10, "high": 10, "low": 10, "close": 10, "volume": 10}]
    ev = [{"ex_date": "2026-10-03", "exchange": "NSE", "series": "EQ", "factor": 0.5, "kind": "split", "subject": None}]
    out, used = price.adjust(bars, ev)
    assert out[0]["close"] == 10 and out[1]["close"] == 5 and out[1]["volume"] == 20 and used == []


def test_jobs_rebuild_adjustments_after_the_bhavcopies():
    from drishti import jobs
    steps = [s.dataset for s in jobs.JOBS["eod_bhavcopy"].steps]
    assert steps[-1] == "price_adjustments" and steps.index("cm_bhavcopy") < steps.index("price_adjustments")
    from drishti.jobs import backfill as bf
    assert {"corp_actions", "adjustments"} <= set(bf.TARGETS)


def test_corp_actions_backfill_resumes_and_relabels(st, monkeypatch):
    from drishti.core.envelope import DrishtiError, ErrorKind, Result
    from drishti.jobs import backfill as bf
    days = sessions(30)
    put(st, split_history(days, 20, ratio=5.0))
    ds.rebuild_adjustments(st)
    calls = []
    act = {"symbol": "X", "isin": ISIN, "company": "X", "series": "EQ", "subject": "Face Value Split (Sub-Division) - From Rs 10/- To Rs 2/-",
           "kind": "split", "ex_date": days[20], "record_date": None, "bc_start": None, "bc_end": None, "face_value": 2.0}
    monkeypatch.setattr(bf.nse_corp, "corporate_actions", lambda **k: calls.append("nse") or Result(data=[act], as_of="x"))
    monkeypatch.setattr(bf.bse, "corporate_actions", lambda **k: calls.append("bse") or Result(error=DrishtiError(ErrorKind.EMPTY, "none")))
    rep = bf.backfill_corp_actions(st, days=90, window=45, today=date(2026, 11, 8))
    assert rep.done == 4 and not rep.failed and calls.count("nse") == 2
    assert ds.adjustments_for(st, ISIN)[0]["kind"] == "split"
    rep2 = bf.backfill_corp_actions(st, days=90, window=45, today=date(2026, 11, 8))
    assert rep2.skipped == 4 and len(calls) == 4


def test_index_valuation_places_pe_in_its_own_history(st):
    from drishti.signals import market
    days = sessions(300)
    ds.ensure(st, "index_close")
    rows = [{"trade_date": d, "index_name": "Nifty 50", "open": 1, "high": 1, "low": 1, "close": 1, "change_pct": 0,
             "pe": 18.0 + i * 0.01, "pb": 3.0, "div_yield": 1.4} for i, d in enumerate(days)]
    st.upsert("index_close", rows, ("trade_date", "index_name"))
    r = market.index_valuation(st, on=days[-1])
    assert r.ok and r.data["details"]["Nifty 50"]["pe_percentile"] == 100.0 and "Nifty 50 rich vs its history" in r.data["label"]
    assert not r.data["provisional"] and any("Midcap 150: no PE" in m for m in r.data["missing"])
    short = market.index_valuation(st, on=days[100])
    assert short.data["provisional"] and "less than a year" in " ".join(short.data["missing"])

"""Split / bonus adjustment from the corporate action's terms, verified against the stored price step, with ISIN succession.

Built from what the live NSE data showed on 8 Nov 2026 (synthetic bars, no fixtures):
  * prev_close in the bhavcopy is NOT adjusted on the ex-date (CONCORDBIO bonus 1:1: close 655.5, prev_close 1227.9);
  * a face-value split gives the stock a new ISIN from the ex-date (KMEW INE0CJD01029 -> INE0CJD01037, ratio 0.208 for 5 -> 1);
  * the ISIN NSE lists on the action can be a stale one (KMEW: INE0CJD01011), so actions are matched by symbol.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from drishti.signals import price
from drishti.store import Store
from drishti.store import datasets as ds

OLD, NEW = "INE0CJD01029", "INE0CJD01037"
SPLIT_5_TO_1 = "Face Value Split (Sub-Division) - From Rs 5/- Per Share To Rs 1/- Per Share"


def sessions(n: int, start: date = date(2026, 9, 1)) -> list[str]:
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def bar(day, close, prev, isin, symbol="KMEW", exchange="NSE", series="EQ", vol=1000):
    return {"trade_date": day, "isin": isin, "series": series, "symbol": symbol, "open": close, "high": close * 1.01, "low": close * 0.99,
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


def action(symbol, ex_date, subject, kind, isin="INE0CJD01011", fv=None):
    return {"symbol": symbol, "isin": isin, "company": symbol, "series": "EQ", "subject": subject, "kind": kind, "ex_date": ex_date,
            "record_date": None, "bc_start": None, "bc_end": None, "face_value": fv}


def split_history(days, ex_i, ratio, move=1.0, symbol="KMEW", old=OLD, new=NEW):
    """Trades near 1000 under `old`; from days[ex_i] near 1000/ratio under `new`. prev_close stays UNADJUSTED, as on NSE."""
    rows, last = [], None
    for i, d in enumerate(days):
        c = (1000.0 + i) if i < ex_i else (1000.0 + i) / ratio * (move if i == ex_i else 1.0)
        rows.append(bar(d, c, last if last is not None else c, old if i < ex_i else new, symbol))
        last = c
    return rows


# --- reading the terms ---------------------------------------------------------------------------------------------
@pytest.mark.parametrize("kind,subject,want", [
    ("split", "Face Value Split (Sub-Division) - From Rs 5/- Per Share To Rs 1/- Per Share", 0.2),
    ("split", "Face Value Split (Sub-Division) - From Rs 10/- Per Share To Rs 2/- Per Share", 0.2),
    ("split", "Stock Split From Rs.10/- to Rs.2/-", 0.2),
    ("split", "Face Value Split (Sub-Division) - From Rs 10/- Per Share To Re 1/- Per Share", 0.1),
    ("split", "Consolidation From Rs 2/- Per Share To Rs 10/- Per Share", 5.0),
    ("bonus", "Bonus 1:1", 0.5),
    ("bonus", "Bonus issue 1:1", 0.5),
    ("bonus", "Bonus 3:2", 0.4),
    ("bonus", "Bonus 1:4", 0.8),
])
def test_expected_factor_from_wording(kind, subject, want):
    assert ds.expected_factor(kind, subject) == pytest.approx(want)


@pytest.mark.parametrize("kind,subject", [("split", "Split"), ("bonus", "Bonus"), ("split", "Bonus 1:1 and Split From Rs 10/- To Rs 2/-"),
                                          ("bonus", "Bonus 0:0"), ("dividend", "Dividend - Rs 5 Per Share"), ("rights", "Rights 3:8 @ Premium Re 0.50/-")])
def test_unreadable_or_unadjusted_wording_gives_no_factor(kind, subject):
    assert ds.expected_factor(kind, subject) is None


# --- events --------------------------------------------------------------------------------------------------------
def test_a_split_that_changes_the_isin_is_verified_and_chained(st):
    days = sessions(30)
    put(st, split_history(days, 20, 4.8))                    # 5 -> 1 gives 0.2; the stock also moved +4% that day: observed 0.208
    ds.persist_corp_actions(st, [action("KMEW", days[20], SPLIT_5_TO_1, "split")])
    r = ds.rebuild_adjustments(st)
    assert r["events"] == 1 and not r["unverified"] and r["successions"] == 1
    e = ds.adjustments_for(st, OLD)[0]
    assert e["ex_date"] == days[20] and e["factor"] == pytest.approx(0.2) and e["isin"] == OLD and e["new_isin"] == NEW
    assert ds.adjustments_for(st, NEW) == ds.adjustments_for(st, OLD)                 # both ISINs see the same event
    assert set(ds.isin_chain(st, NEW)) == {OLD, NEW}


def test_the_stale_isin_on_the_action_does_not_matter(st):
    days = sessions(30)
    put(st, split_history(days, 20, 5.0))
    ds.persist_corp_actions(st, [action("KMEW", days[20], SPLIT_5_TO_1, "split", isin="INE0CJD01011")])      # neither trading ISIN
    assert ds.rebuild_adjustments(st)["events"] == 1


def test_a_bonus_with_the_same_isin_uses_the_close_to_close_step_not_prev_close(st):
    days = sessions(30)
    put(st, split_history(days, 20, 2.0, move=1.07, old=OLD, new=OLD))       # 1:1 bonus, +7% on the day; prev_close is the old close
    ds.persist_corp_actions(st, [action("KMEW", days[20], "Bonus 1:1", "bonus")])
    r = ds.rebuild_adjustments(st)
    e = ds.adjustments_for(st, OLD)
    assert r["events"] == 1 and r["successions"] == 0 and len(e) == 1 and e[0]["factor"] == pytest.approx(0.5) and e[0]["new_isin"] is None


def test_an_action_the_prices_do_not_confirm_is_kept_but_not_applied(st):
    days = sessions(30)
    put(st, split_history(days, 20, 1.01, old=OLD, new=OLD))                   # no step at all
    ds.persist_corp_actions(st, [action("KMEW", days[20], SPLIT_5_TO_1, "split")])
    r = ds.rebuild_adjustments(st)
    assert r["events"] == 0 and len(r["unverified"]) == 1 and "not close to the expected" in r["unverified"][0]["reason"]
    assert ds.adjustments_for(st, OLD) == []
    row = st.query("SELECT verified, factor, expected_factor FROM price_adjustment")[0]
    assert row["verified"] == 0 and row["factor"] is None and row["expected_factor"] == pytest.approx(0.2)


def test_a_small_expected_factor_needs_a_step_nearer_to_it_than_to_no_change(st):
    days = sessions(30)
    put(st, split_history(days, 20, 1.01, old=OLD, new=OLD))                   # bonus 1:9 expects 0.9; the price barely moved
    ds.persist_corp_actions(st, [action("KMEW", days[20], "Bonus 1:9", "bonus")])
    r = ds.rebuild_adjustments(st)
    assert r["events"] == 0 and "closer to no change" in r["unverified"][0]["reason"]


def test_a_wrong_sized_step_is_not_applied(st):
    days = sessions(30)
    put(st, split_history(days, 20, 3.0, old=OLD, new=OLD))                    # expected 0.2, observed 0.33
    ds.persist_corp_actions(st, [action("KMEW", days[20], SPLIT_5_TO_1, "split")])
    r = ds.rebuild_adjustments(st)
    assert r["events"] == 0 and "not close to the expected" in r["unverified"][0]["reason"]


def test_dividends_rights_and_upcoming_actions_are_not_events(st):
    days = sessions(30)
    put(st, split_history(days, 29, 1.0))
    ds.persist_corp_actions(st, [action("KMEW", days[10], "Dividend - Rs 5 Per Share", "dividend"),
                                 action("KMEW", days[12], "Rights 3:8 @ Premium Re 0.50/-", "rights"),
                                 action("KMEW", "2026-12-30", SPLIT_5_TO_1, "split")])               # ex-date not reached yet
    r = ds.rebuild_adjustments(st)
    assert r["events"] == 0 and r["checked"] == 0 and not r["unverified"]


def test_unreadable_terms_are_reported_not_guessed(st):
    days = sessions(30)
    put(st, split_history(days, 20, 5.0))
    ds.persist_corp_actions(st, [action("KMEW", days[20], "Split", "split")])
    r = ds.rebuild_adjustments(st)
    assert r["events"] == 0 and r["unparsed"] == [{"symbol": "KMEW", "ex_date": days[20], "subject": "Split"}]


def test_incremental_rebuild_only_touches_recent_events(st):
    days = sessions(40)
    rows = split_history(days, 10, 2.0, old=OLD, new=OLD, symbol="AAA") + split_history(days, 30, 2.0, old="INE111A01011", new="INE111A01011", symbol="BBB")
    put(st, rows)
    ds.persist_corp_actions(st, [action("AAA", days[10], "Bonus 1:1", "bonus"), action("BBB", days[30], "Bonus 1:1", "bonus", isin="INE111A01011")])
    assert ds.rebuild_adjustments(st)["events"] == 2
    ds.rebuild_adjustments(st, since=days[20])
    assert len(st.query("SELECT 1 FROM price_adjustment")) == 2          # the older AAA event survived, the BBB one was recomputed


# --- succession seen only in prices --------------------------------------------------------------------------------
def test_isin_change_without_an_action_is_chained_from_prices(st):
    days = sessions(20)
    put(st, split_history(days, 10, 1.0, symbol="ZZZ", old="INE222A01011", new="INE222A01029"))
    r = ds.rebuild_adjustments(st)
    assert r["events"] == 0 and r["successions"] == 1
    assert set(ds.isin_chain(st, "INE222A01029")) == {"INE222A01011", "INE222A01029"}


def test_a_reused_symbol_months_later_is_not_chained(st):
    rows = [bar(d, 100.0, 100.0, "INE333A01011", "REUSE") for d in sessions(5, date(2026, 1, 5))]
    rows += [bar(d, 50.0, 50.0, "INE444A01011", "REUSE") for d in sessions(5, date(2026, 8, 3))]
    put(st, rows)
    assert ds.rebuild_adjustments(st)["successions"] == 0


def test_two_instruments_trading_together_under_one_symbol_are_not_a_succession(st):
    days = sessions(10)
    rows = []
    for d in days:
        rows += [bar(d, 100.0, 100.0, "INE555A01011", "TWIN"), bar(d, 1000.0, 1000.0, "INE555A07018", "TWIN", series="N1")]
    put(st, rows)
    assert ds.rebuild_adjustments(st)["successions"] == 0


# --- reading through the event -------------------------------------------------------------------------------------
def test_price_context_reads_through_a_split_that_changed_the_isin(st):
    days = sessions(30)
    put(st, split_history(days, 20, 5.0))
    ds.persist_corp_actions(st, [action("KMEW", days[20], SPLIT_5_TO_1, "split")])
    ds.rebuild_adjustments(st)
    sig = price.price_context(st, NEW, on=days[-1]).data          # asked under the NEW isin: the old history is still found
    assert sig["details"]["sessions"] == 30
    one_month = sig["components"][1]["value"][0]
    assert one_month == pytest.approx((1029 / 5 / (1008 / 5) - 1) * 100, abs=0.05)       # not -80%
    assert sig["details"]["high_52w"] < 250                         # the pre-split ~1000s are scaled to ~200
    assert sig["details"]["adjustments"][0]["factor"] == pytest.approx(0.2)
    assert any(c["input"] == "adjusted for" for c in sig["components"])


def test_adjust_scales_every_bar_before_the_ex_date_whatever_the_venue():
    bars = [{"trade_date": "2026-10-01", "exchange": "BSE", "series": "B", "open": 10, "high": 10, "low": 10, "close": 10, "volume": 10},
            {"trade_date": "2026-10-02", "exchange": "NSE", "series": "EQ", "open": 10, "high": 10, "low": 10, "close": 10, "volume": 10},
            {"trade_date": "2026-10-05", "exchange": "NSE", "series": "EQ", "open": 5, "high": 5, "low": 5, "close": 5, "volume": 20}]
    ev = [{"ex_date": "2026-10-05", "factor": 0.5, "kind": "split", "subject": None}]
    out, used = price.adjust(bars, ev)
    assert [b["close"] for b in out] == [5, 5, 5] and out[0]["volume"] == 20 and used == ev


# --- old layout ----------------------------------------------------------------------------------------------------
def test_the_first_price_adjustment_layout_is_replaced(st):
    st.ensure_table("CREATE TABLE IF NOT EXISTS price_adjustment (isin TEXT NOT NULL, exchange TEXT NOT NULL, series TEXT NOT NULL, "
                    "ex_date TEXT NOT NULL, factor REAL NOT NULL, prev_close REAL, last_close REAL, last_date TEXT, kind TEXT, subject TEXT, "
                    "PRIMARY KEY (isin, exchange, series, ex_date))")
    st.upsert("price_adjustment", [{"isin": "X", "exchange": "NSE", "series": "EQ", "ex_date": "2026-01-01", "factor": 0.5, "prev_close": 1.0,
                                    "last_close": 2.0, "last_date": "2025-12-31", "kind": None, "subject": None}], ("isin", "exchange", "series", "ex_date"))
    days = sessions(30)
    put(st, split_history(days, 20, 5.0))
    ds.persist_corp_actions(st, [action("KMEW", days[20], SPLIT_5_TO_1, "split")])
    assert ds.rebuild_adjustments(st)["events"] == 1
    assert [r["isin"] for r in st.query("SELECT isin FROM price_adjustment")] == [OLD]


# --- wiring ----------------------------------------------------------------------------------------------------------
def test_jobs_rebuild_adjustments_after_the_bhavcopies():
    from drishti import jobs
    steps = [s.dataset for s in jobs.JOBS["eod_bhavcopy"].steps]
    assert steps[-1] == "price_adjustments" and steps.index("cm_bhavcopy") < steps.index("price_adjustments")
    from drishti.jobs import backfill as bf
    assert {"corp_actions", "adjustments"} <= set(bf.TARGETS)


def test_corp_actions_backfill_resumes_rebuilds_and_does_not_fake_bse_history(st, monkeypatch):
    from drishti.core.envelope import DrishtiError, ErrorKind, Result
    from drishti.jobs import backfill as bf
    days = sessions(30)
    put(st, split_history(days, 20, 5.0))
    calls, msgs = [], []
    monkeypatch.setattr(bf.nse_corp, "corporate_actions", lambda **k: calls.append("nse") or Result(data=[action("KMEW", days[20], SPLIT_5_TO_1, "split")], as_of="x"))
    monkeypatch.setattr(bf.bse, "corporate_actions", lambda **k: calls.append("bse") or Result(error=DrishtiError(ErrorKind.EMPTY, "none")))
    rep = bf.backfill_corp_actions(st, days=90, window=45, today=date(2026, 11, 8), progress=msgs.append)
    assert calls.count("nse") == 2 and calls.count("bse") == 1          # BSE is asked only for the current window
    assert any("no history" in m for m in msgs) and not rep.failed
    assert ds.adjustments_for(st, NEW)[0]["kind"] == "split"
    n = len(calls)
    rep2 = bf.backfill_corp_actions(st, days=90, window=45, today=date(2026, 11, 8))
    assert len(calls) == n and rep2.skipped >= 3


def test_bse_that_ignores_its_date_window_is_a_typed_error(monkeypatch):
    from drishti.core.envelope import ErrorKind
    from drishti.providers import bse
    rows = [{"scrip_code": "531171", "bse_symbol": "X", "company": "X", "subject": "Bonus issue 1:1", "kind": "bonus", "ex_date": "2026-11-13",
             "record_date": None, "bc_start": None, "bc_end": None, "payment_date": None}]

    class Got:
        body = b"{}"

        def source(self, name):
            return {"name": name}
    monkeypatch.setattr(bse, "get", lambda *a, **k: Got())
    monkeypatch.setattr(bse, "parse_corporate_actions", lambda body: rows)
    past = bse.corporate_actions(back_days=89, ahead_days=0, today=date(2026, 8, 10))
    assert not past.ok and past.error.kind == ErrorKind.PARSE_FAIL and "ignores its date range" in past.error.detail
    now = bse.corporate_actions(back_days=7, ahead_days=30, today=date(2026, 11, 8))
    assert now.ok and now.data == rows


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

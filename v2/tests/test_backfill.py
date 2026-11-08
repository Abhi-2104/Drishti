"""Backfill engine on fakes: resume, holidays, abort on repeated blocks, time budget, and the SHP two-quarter plan."""
from __future__ import annotations

from datetime import date

import pytest

from drishti.core.envelope import DrishtiError, ErrorKind, Result
from drishti.jobs import backfill as bf
from drishti.store import Store
from drishti.store import datasets as ds

FRI = date(2026, 11, 6)


@pytest.fixture
def st():
    s = Store()
    yield s
    s.close()


def idx_result(d):
    return Result(data=[{"trade_date": d.isoformat(), "index_name": "Nifty 50", "close": 100.0}], as_of=d.isoformat())


def test_daily_backfill_fills_skips_and_counts_holidays(st, monkeypatch):
    calls = []

    def fake(d, store=None):
        calls.append(d)
        if d == date(2026, 11, 4):
            return Result(error=DrishtiError(ErrorKind.NOT_FOUND, "404"))     # a holiday
        return idx_result(d)
    monkeypatch.setattr(bf.nse_archive, "index_closes", fake)
    rep = bf.backfill_index_closes(st, days=14, start=FRI)
    assert rep.done == 9 and rep.holidays == 1 and rep.ok and rep.newest == "2026-11-06" and rep.oldest == "2026-10-26"
    assert st.query("SELECT COUNT(*) AS n FROM index_close")[0]["n"] == 9
    calls.clear()
    rep2 = bf.backfill_index_closes(st, days=14, start=FRI)                    # resume: stored days are not fetched again
    assert rep2.skipped == 9 and rep2.done == 0 and calls == [date(2026, 11, 4)]


def test_daily_backfill_aborts_on_a_run_of_blocks(st, monkeypatch):
    monkeypatch.setattr(bf.nse_archive, "participant_oi", lambda d, store=None: Result(error=DrishtiError(ErrorKind.BLOCKED, "403")))
    rep = bf.backfill_participant_oi(st, days=60, start=FRI)
    assert not rep.ok and rep.stopped.startswith("aborted") and len(rep.failed) == bf.MAX_CONSECUTIVE_FAILURES


def test_daily_backfill_time_budget(st, monkeypatch):
    t = iter(range(0, 10_000, 100))
    monkeypatch.setattr(bf.nse_archive, "index_closes", lambda d, store=None: idx_result(d))
    rep = bf._daily(st, "index_closes", "index_close", lambda d: idx_result(d), lambda s, r: ds.persist_index_closes(s, r.data),
                    days=60, budget_s=350, start=FRI, clock=lambda: next(t))
    assert "time budget" in rep.stopped and rep.done == 3 and rep.ok   # stopping on budget is a clean stop, resumable


def test_fo_index_keeps_only_index_rows(st, monkeypatch):
    rows = [{"trade_date": "2026-11-06", "instrument_type": "IDO", "symbol": "NIFTY", "expiry": "2026-11-11", "strike": 25000.0, "option_type": "CE",
             "oi": 5, "oi_change": 1, "volume": 1, "close": 1.0, "settle": 1.0, "underlying": 25030.0, "lot_size": 75},
            {"trade_date": "2026-11-06", "instrument_type": "IDF", "symbol": "NIFTY", "expiry": "2026-11-25", "strike": None, "option_type": None,
             "oi": 9, "oi_change": 1, "volume": 1, "close": 25100.0, "settle": 25100.0, "underlying": 25030.0, "lot_size": 75},
            {"trade_date": "2026-11-06", "instrument_type": "STO", "symbol": "RELIANCE", "expiry": "2026-11-25", "strike": 1300.0, "option_type": "PE",
             "oi": 9, "oi_change": 1, "volume": 1, "close": 1.0, "settle": 1.0, "underlying": 1320.0, "lot_size": 500}]
    monkeypatch.setattr(bf.nse_archive, "fo_bhavcopy", lambda d, store=None: Result(data=rows, as_of=d.isoformat()) if d == FRI
                        else Result(error=DrishtiError(ErrorKind.NOT_FOUND, "x")))
    rep = bf.backfill_fo_index(st, days=3, start=FRI)
    assert rep.done == 1 and st.query("SELECT COUNT(*) AS n FROM index_option_eod")[0]["n"] == 1
    assert st.query("SELECT COUNT(*) AS n FROM index_future_eod")[0]["n"] == 1
    assert st.query("SELECT COUNT(*) AS n FROM raw_blob")[0]["n"] == 0          # the 40k-row zip is not kept for backfilled days


def test_macro_backfill_reports_each_series(st, monkeypatch):
    monkeypatch.setattr(bf.fred, "fred_series", lambda sid, store=None, today=None: Result(data={"n_total": 10}, as_of="2026-11-05")
                        if sid != "M2SL" else Result(error=DrishtiError(ErrorKind.STALE, "old")))
    monkeypatch.setattr(bf.yahoo, "daily", lambda sym, rng="5y", store=None, today=None: Result(data=[{}] * 5, as_of="2026-11-06"))
    rep = bf.backfill_macro(st)
    assert rep.done == len(bf.fred.SERIES) - 1 + len(bf.yahoo.SYMBOLS) and rep.failed[0]["date"] == "M2SL"


def master_row(isin, as_on, broadcast, url):
    return {"record_id": url, "symbol": isin[-4:], "company": "c", "isin": isin, "isin_valid": True, "as_on": as_on,
            "broadcast": broadcast, "revised": False, "promoter_pct": 50.0, "public_pct": 50.0, "xbrl_url": url, "remarks": None}


def filing(isin, as_on, enc):
    return {"isin": isin, "isin_valid": True, "as_on": as_on, "symbol": "X", "company": "c", "total_shares": 100, "promoter_shares": 50,
            "promoter_pct": 50.0, "public_pct": 50.0, "pledged_shares": enc, "ndu_shares": 0, "other_encumbered_shares": 0,
            "encumbered_shares": enc, "encumbered_pct_of_promoter": enc * 2.0, "pledged_pct_of_promoter": enc * 2.0,
            "encumbered_pct_of_total": enc * 1.0, "flags_consistent": True, "no_promoter": False}


def test_shp_backfill_current_for_all_previous_only_for_pledged(st, monkeypatch):
    a, b, c = "INE002A01018", "INE101A01026", "INE111A01011"
    rows = [master_row(i, "2026-09-30", "2026-10-20T10:00:00", f"{i}-q2") for i in (a, b, c)] + \
           [master_row(i, "2026-06-30", "2026-07-20T10:00:00", f"{i}-q1") for i in (a, b, c)] + \
           [master_row(a, "2026-09-30", "2026-10-25T10:00:00", f"{a}-q2-rev")]          # a revision broadcast later wins
    monkeypatch.setattr(bf.nse_shp, "shp_master_rows", lambda days, today=None: Result(data=rows, as_of="2026-09-30"))
    fetched = []

    def fake_filing(url):
        fetched.append(url)
        isin, q = url.split("-")[0], url.split("-")[1]
        return Result(data=filing(isin, "2026-09-30" if q == "q2" else "2026-06-30", 10 if isin == a else 0), as_of="x")
    monkeypatch.setattr(bf.nse_shp, "shp_filing", fake_filing)
    rep = bf.backfill_shp(st, today=FRI)
    assert sorted(fetched) == sorted([f"{a}-q2-rev", f"{b}-q2", f"{c}-q2", f"{a}-q1"])   # previous quarter only for the pledged company
    assert rep.done == 4 and rep.ok and (rep.newest, rep.oldest) == ("2026-09-30", "2026-06-30")
    fetched.clear()
    assert bf.backfill_shp(st, today=FRI).done == 0 and fetched == []                  # resumes: nothing left to fetch


def test_insider_backfill_windows_and_resume(st, monkeypatch):
    seen = []

    def fake(window, *, today, skip_app_ids=(), **k):
        seen.append((today, len(skip_app_ids)))
        ev = {"app_id": f"a{today}", "filed_at": f"{today}T10:00:00", "symbol": "X", "isin": "INE002A01018", "scrip_code": None, "company": "c",
              "category": "Promoter", "person": f"P{today}", "direction": "BUY", "txn_type": "Buy", "mode": "Market Purchase", "quantity": 1,
              "value_inr": 1.0, "held_pre_shares": 0, "held_post_shares": 1, "held_pre_pct": 0.0, "held_post_pct": 0.0, "holding_reconciles": True,
              "from_date": str(today), "to_date": str(today), "intimation_date": str(today), "filing_date": str(today), "exchange": "NSE",
              "revised": False, "form_version": "2.0"}
        return Result(data={"events": [ev], "filings": 1, "failed": [], "duplicates_dropped": 0}, as_of=str(today))
    monkeypatch.setattr(bf.nse_pit, "insider_trades", fake)
    msgs = []
    rep = bf.backfill_insider(st, days=90, window=30, today=FRI, progress=msgs.append)
    assert len(msgs) == 3 and "window ending" in msgs[0]
    assert rep.done == 3 and [s[1] for s in seen] == [0, 1, 2] and st.query("SELECT COUNT(*) AS n FROM insider_event")[0]["n"] == 3


def test_cli_backfill_unknown_target(tmp_path, monkeypatch, capsys):
    from drishti.cli import main
    monkeypatch.setenv("DRISHTI_HOME", str(tmp_path))
    assert main(["backfill", "nope"]) == 2 and "unknown target" in capsys.readouterr().err
    assert main(["signal", "list"]) == 0 and "macro_regime" in capsys.readouterr().out
    assert main(["signal", "seasonality", "--date", "2026-11-08"]) == 0 and "Q2 FY27" in capsys.readouterr().out
    assert main(["signal", "participant_oi"]) == 1 and "UNKNOWN" in capsys.readouterr().err

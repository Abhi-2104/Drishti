"""Batch 3 sources on real saved responses (8 Nov 2026): NSE board meetings, NSE historical bulk/block/short deals,
BSE corporate actions and results calendar, NSDL confirmed FPI flows. Pins parsing, the integrity checks, the
70-row cap handling, persistence (one row per company across exchanges) and the tools on top."""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from drishti.core.envelope import DrishtiError, ErrorKind
from drishti.core.fetch import Fetched
from drishti.providers import bse, nsdl
from drishti.providers import nse_corp as c
from drishti.store import Store
from drishti.store import datasets as ds

FIX = Path(__file__).parent / "fixtures"
TODAY = date(2026, 11, 8)


def raw(*parts) -> bytes:
    return FIX.joinpath(*parts).read_bytes()


def kind(fn, *a, **k) -> ErrorKind:
    with pytest.raises(DrishtiError) as ei:
        fn(*a, **k)
    return ei.value.kind


@pytest.fixture
def st():
    s = Store()
    s.upsert_security("INE062A01012", name="State Bank of India", nse_symbol="SBIN", bse_scrip="500112", source="t")
    s.upsert_security("INE101A01026", name="Mahindra & Mahindra Limited", nse_symbol="M&M", bse_scrip="500520", source="t")
    s.upsert_security("INE0BRK01010", name="Brisk Technovision Ltd", bse_scrip="544101", source="t")
    yield s
    s.close()


def nse_api(monkeypatch, fn):
    """Fake NSE: fn(path, params) -> body, for JSON calls and for CSV downloads (params carry csv='true')."""
    monkeypatch.setattr(c.nse_live, "api_get", lambda path, **p: Fetched("https://www.nseindia.com/api/" + path, fn(path, p), 200))
    monkeypatch.setattr(c.nse_live, "api_get_text", lambda path, expect="csv", **p: Fetched("https://www.nseindia.com/api/" + path,
                                                                                             fn(path, p), 200))


# --- NSE board meetings ---------------------------------------------------------------------------------------------
def test_board_meetings_real():
    rows = c.parse_board_meetings(raw("nse_corp", "board_meetings.json"))
    assert len(rows) == 47 and all(r["exchange"] == "NSE" and r["meeting_date"] and r["announced"] for r in rows)
    sb = [r for r in rows if r["symbol"] == "SBIN"]
    assert {r["isin"] for r in sb} == {"INE062A01012"} and {r["meeting_date"] for r in sb} == {"2026-11-03"}
    assert {r["kind"] for r in rows} >= {"results", "dividend", "fund_raising"}
    assert c.meeting_kind("Buyback") == "buyback" and c.meeting_kind("Board Meeting Intimation", "to approve Q2 financial results") == "results"


def test_board_meetings_provider_checks_range_and_freshness(monkeypatch):
    body = raw("nse_corp", "board_meetings.json")
    nse_api(monkeypatch, lambda path, p: body)
    r = c.board_meetings(today=TODAY)
    assert r.ok and r.as_of == "2026-11-07" and len(r.data) == 47
    assert c.board_meetings(today=date(2026, 11, 20)).error.kind in (ErrorKind.STALE, ErrorKind.PARSE_FAIL)
    far = json.loads(body)
    for x in far:
        x["bm_date"] = "01-Jan-2026"
    nse_api(monkeypatch, lambda path, p: json.dumps(far).encode())
    assert c.board_meetings(today=TODAY).error.kind == ErrorKind.PARSE_FAIL      # range not honoured: some other list


def test_board_meetings_from_both_exchanges_merge_per_company(st):
    ds.persist_board_meetings(st, c.parse_board_meetings(raw("nse_corp", "board_meetings.json")))
    bse_rows = bse.parse_result_calendar(raw("bse", "result_calendar.json"))
    bse_rows.append({**bse_rows[0], "code": "500112", "symbol": "SBIN", "company": "STATE BANK OF INDIA", "meeting_date": "2026-11-03"})
    ds.persist_board_meetings(st, bse_rows)
    assert st.query("SELECT isin FROM board_meeting WHERE exchange='BSE' AND code='500112'")[0]["isin"] == "INE062A01012"
    m = ds.upcoming_meetings(st, isin="INE062A01012", start="2026-11-01", end="2026-12-31")
    assert len(m) == 1 and m[0]["exchanges"] == ["NSE", "BSE"] and m[0]["kind"] == "results" and m[0]["announced"]
    assert len(m[0]["purposes"]) >= 2                                            # intimation + results, kept, not dropped
    allm = ds.upcoming_meetings(st, start="2026-11-09", end="2026-11-30")
    assert len({(x["isin"] or x["code"], x["meeting_date"]) for x in allm}) == len(allm)


# --- NSE historical deals and the 70-row cap ----------------------------------------------------------------------
def test_hist_deals_real():
    bulk = c.parse_hist_deals(raw("nse_corp", "hist_bulk_deals.json"), "bulk")
    block = c.parse_hist_deals(raw("nse_corp", "hist_block_deals.json"), "block")
    short = c.parse_hist_deals(raw("nse_corp", "hist_short_selling.json"), "short")
    assert (len(bulk), len(block), len(short)) == (70, 56, 70)        # 70 = NSE's cap: those two answers were cut short
    assert block[0] == {"kind": "block", "date": "2026-10-12", "symbol": "JUBLPHARMA", "company": "Jubilant Pharmova Ltd",
                        "client": "ABAKKUS FOUR2EIGHT OPPORTUNITIES FUND", "side": "BUY", "qty": 1100000, "price": 915.0}
    assert short[0]["client"] is None and short[0]["qty"] == 15
    bad = json.loads(raw("nse_corp", "hist_block_deals.json"))
    bad["data"][0]["BD_BUY_SELL"] = "HOLD"
    assert kind(c.parse_hist_deals, json.dumps(bad).encode(), "block") == ErrorKind.PARSE_FAIL


def _fake_deals(per_day: dict[str, int]):
    """NSE stand-in: per_day[date] deals on each day. JSON stops at HIST_CAP rows, oldest first (as NSE does); the CSV
    download (csv='true') has them all. An empty CSV is a zero-byte file."""
    def answer(path, p):
        lo = date(int(p["from"][6:]), int(p["from"][3:5]), int(p["from"][:2]))
        hi = date(int(p["to"][6:]), int(p["to"][3:5]), int(p["to"][:2]))
        rows, lines, d = [], [], lo
        short = p["optionType"] == "short_selling"
        while d <= hi:
            ds_ = d.strftime("%d-%b-%Y").upper()
            for i in range(per_day.get(d.isoformat(), 0)):
                if short:
                    rows.append({"SS_DATE": ds_, "SS_SYMBOL": f"S{i}", "SS_NAME": "x", "SS_QTY": 10 + i})
                    lines.append(f'"{ds_}","S{i}","x","{10 + i}"')
                else:
                    rows.append({"BD_DT_DATE": ds_, "BD_SYMBOL": f"S{i}", "BD_SCRIP_NAME": "x", "BD_CLIENT_NAME": f"C{i}",
                                 "BD_BUY_SELL": "BUY", "BD_QTY_TRD": 1000 + i, "BD_TP_WATP": 10})
                    lines.append(f'"{ds_}","S{i}","x","C{i}","BUY","{1000 + i:,}","10.00","-"')
            d += timedelta(days=1)
        if p.get("csv"):
            if not lines:
                return b""
            head = ('"Date ","Symbol ","Security Name ","Quantity "' if short else
                    '"Date ","Symbol ","Security Name ","Client Name ","Buy / Sell ","Quantity Traded ","Trade Price / Wght. Avg. Price ","Remarks "')
            return ("\ufeff" + head + "\n" + "\n".join(lines)).encode("utf-8")
        return json.dumps({"data": rows[:c.HIST_CAP]}).encode()
    return answer


def test_hist_csv_real_is_not_capped():
    bulk = c.parse_hist_csv(raw("nse_corp", "hist_bulk_deals_1day.csv"), "bulk")
    short = c.parse_hist_csv(raw("nse_corp", "hist_short_selling_1day.csv"), "short")
    assert len(bulk) == 96                                       # = NSE's own large-deals snapshot for 6 Nov and {x["date"] for x in bulk} == {"2026-11-06"}
    assert bulk[0] == {"kind": "bulk", "date": "2026-11-06", "symbol": "AAATECH", "company": "AAA Technologies Limited",
                       "client": "M7 GLOBAL FUND PCC - CELL DEWCAP FUND", "side": "SELL", "qty": 95000, "price": 96.15}
    assert any(x["qty"] == 150500 and x["price"] == 1020.42 for x in bulk)          # Indian digit grouping: 1,50,500
    assert len(short) == 116 and short[-1]["symbol"] == "YESBANK" and short[-1]["qty"] == 3000


def test_block_deals_split_until_under_the_cap(monkeypatch):
    per_day = {f"2026-10-{d:02d}": 20 for d in range(1, 31)}            # 600 deals over 30 days
    nse_api(monkeypatch, _fake_deals(per_day))
    r = c.historical_deals("block", date(2026, 10, 1), date(2026, 10, 30))
    assert r.ok and len(r.data["deals"]) == 600 and r.data["capped_days"] == [] and r.data["requests"] > 1
    assert len({(x["date"], x["symbol"]) for x in r.data["deals"]}) == 600


def test_a_block_day_that_fills_the_cap_is_completed_from_the_csv(monkeypatch):
    nse_api(monkeypatch, _fake_deals({"2026-10-09": 96, "2026-10-12": 5}))
    r = c.historical_deals("block", date(2026, 10, 1), date(2026, 10, 15))
    assert r.ok and r.data["capped_days"] == [] and len(r.data["deals"]) == 96 + 5


def test_a_day_the_csv_cannot_complete_is_named(monkeypatch):
    fake = _fake_deals({"2026-10-09": 96})
    nse_api(monkeypatch, lambda path, p: fake(path, {**p, "csv": None}) if p.get("csv") else fake(path, p))   # 'CSV' answered as JSON
    assert c.historical_deals("block", date(2026, 10, 9), date(2026, 10, 9)).error.kind == ErrorKind.PARSE_FAIL
    # a CSV shorter than the capped JSON must be reported, never used
    nse_api(monkeypatch, lambda path, p: (b'"Date ","Symbol ","Security Name ","Client Name ","Buy / Sell ","Quantity Traded ",'
                                          b'"Trade Price / Wght. Avg. Price "\n"09-OCT-2026","A","x","C","BUY","1","1"')
            if p.get("csv") else fake(path, p))
    r = c.historical_deals("block", date(2026, 10, 9), date(2026, 10, 9))
    assert r.error.kind == ErrorKind.PARSE_FAIL and r.data["capped_days"] == ["2026-10-09"] and len(r.data["deals"]) == 70


def test_bulk_and_short_come_day_by_day_from_the_csv(monkeypatch):
    per_day = {"2026-11-02": 96, "2026-11-03": 0, "2026-11-04": 120}
    calls = []
    fake = _fake_deals(per_day)
    nse_api(monkeypatch, lambda path, p: calls.append(bool(p.get("csv"))) or fake(path, p))
    r = c.historical_deals("bulk", date(2026, 11, 1), date(2026, 11, 4))     # Sunday 1 Nov is not asked for
    assert r.ok and len(r.data["deals"]) == 216 and calls == [True, True, False, True]   # empty CSV confirmed by the JSON
    r = c.historical_deals("short", date(2026, 11, 4), date(2026, 11, 4))
    assert r.ok and len(r.data["deals"]) == 120


def test_an_empty_csv_on_a_day_with_deals_is_an_error(monkeypatch):
    fake = _fake_deals({"2026-11-02": 5})
    nse_api(monkeypatch, lambda path, p: b"" if p.get("csv") else fake(path, p))
    assert c.historical_deals("bulk", date(2026, 11, 2), date(2026, 11, 2)).error.kind == ErrorKind.PARSE_FAIL


def test_historical_deals_refuses_bad_input_and_stray_rows(monkeypatch):
    assert c.historical_deals("odd", TODAY, TODAY).error.kind == ErrorKind.PARSE_FAIL
    assert c.historical_deals("bulk", TODAY, TODAY - timedelta(days=1)).error.kind == ErrorKind.PARSE_FAIL
    nse_api(monkeypatch, lambda path, p: raw("nse_corp", "hist_block_deals.json"))
    assert c.historical_deals("block", date(2026, 11, 1), date(2026, 11, 5)).error.kind == ErrorKind.PARSE_FAIL


def test_backfill_deals_resumes_by_window(st, monkeypatch):
    from drishti.jobs import backfill as bf
    per_day = {(TODAY - timedelta(days=k)).isoformat(): 3 for k in range(1, 61)}
    calls = []
    fake = _fake_deals(per_day)
    nse_api(monkeypatch, lambda path, p: calls.append(p["optionType"]) or fake(path, p))
    assert bf.COMPLETE_HISTORY_KINDS == ("block", "bulk", "short")
    rep = bf.backfill_deals(st, days=60, today=TODAY)
    assert rep.ok and rep.done == 6 and rep.rows > 0 and not rep.failed       # 2 windows x 3 kinds
    n = len(calls)
    rep2 = bf.backfill_deals(st, days=60, today=TODAY)
    assert rep2.skipped == 6 and rep2.done == 0 and len(calls) == n          # nothing fetched again
    assert st.query("SELECT COUNT(*) AS n FROM deal WHERE source='nse_hist'")[0]["n"] == rep.rows


# --- BSE corporate actions and results calendar -------------------------------------------------------------------
def test_backfill_deals_redoes_a_window_with_a_capped_day(st, monkeypatch):
    from drishti.jobs import backfill as bf
    fake = _fake_deals({"2026-11-02": 96})
    broken = {"on": True}
    nse_api(monkeypatch, lambda path, p: (b"" if broken["on"] and p.get("csv") else fake(path, p)))
    rep = bf.backfill_deals(st, days=10, today=TODAY, kinds=("block",))
    assert not rep.ok or rep.failed                                        # empty CSV against a full JSON day
    broken["on"] = False
    rep2 = bf.backfill_deals(st, days=10, today=TODAY, kinds=("block",))
    assert rep2.done == 1 and rep2.skipped == 0 and not rep2.failed
    assert st.query("SELECT COUNT(*) AS n FROM deal WHERE trade_date='2026-11-02'")[0]["n"] == 96


def test_bse_corp_actions_real():
    rows = bse.parse_corporate_actions(raw("bse", "corp_actions.json"))
    assert len(rows) == 92 and rows[0]["scrip_code"] == "544101" and rows[0]["ex_date"] == "2026-11-09"
    assert rows[0]["kind"] == "dividend" and rows[0]["record_date"] == "2026-11-10"
    kinds = {r["kind"] for r in rows}
    assert {"rights", "split", "bonus", "buyback", "distribution"} <= kinds and "other" not in kinds


def test_bse_actions_never_duplicate_nse_ones(st):
    ns = [{"symbol": "SBIN", "isin": "INE062A01012", "company": "SBI", "series": "EQ", "subject": "Dividend - Rs 15.90 Per Share",
           "kind": "dividend", "ex_date": "2026-11-20", "record_date": None, "bc_start": None, "bc_end": None, "face_value": 1.0}]
    bs = [{"scrip_code": "500112", "bse_symbol": "SBIN", "company": "SBI", "subject": "Interim Dividend - Rs. - 15.9000", "kind": "dividend",
           "ex_date": "2026-11-20", "record_date": None, "bc_start": None, "bc_end": None, "payment_date": None},
          {"scrip_code": "544101", "bse_symbol": "BRISK", "company": "Brisk", "subject": "Interim Dividend - Rs. - 2.0000", "kind": "dividend",
           "ex_date": "2026-11-09", "record_date": None, "bc_start": None, "bc_end": None, "payment_date": None}]
    ds.persist_bse_corp_actions(st, bs)                      # BSE first: both stored for now
    assert st.query("SELECT COUNT(*) AS n FROM corp_action")[0]["n"] == 2
    ds.persist_corp_actions(st, ns)                          # NSE's copy replaces BSE's for SBI
    rows = st.query("SELECT symbol, isin FROM corp_action ORDER BY symbol")
    assert rows == [{"symbol": "BSE:544101", "isin": "INE0BRK01010"}, {"symbol": "SBIN", "isin": "INE062A01012"}]
    ds.persist_bse_corp_actions(st, bs)                      # and BSE does not come back
    assert st.query("SELECT COUNT(*) AS n FROM corp_action")[0]["n"] == 2


def test_bse_result_calendar_real_and_provider(monkeypatch):
    rows = bse.parse_result_calendar(raw("bse", "result_calendar.json"))
    assert rows[0]["code"] == "544834" and rows[0]["meeting_date"] == "2026-11-09" and rows[0]["kind"] == "results"
    monkeypatch.setattr(bse, "get", lambda url, prof, **k: Fetched(url, raw("bse", "result_calendar.json"), 200))
    assert bse.result_calendar(today=TODAY).ok
    assert bse.result_calendar(today=date(2026, 12, 31)).error.kind == ErrorKind.STALE
    monkeypatch.setattr(bse, "get", lambda url, prof, **k: Fetched(url, b'[{"scrip_Code": "ABC", "meeting_date": "09 Nov 2026"}]', 200))
    assert bse.result_calendar(today=TODAY).error.kind == ErrorKind.PARSE_FAIL


# --- NSDL FPI ------------------------------------------------------------------------------------------------------
def test_nsdl_fpi_real():
    d = nsdl.parse_latest(raw("nsdl", "latest.html"))
    assert d["date"] == "2026-11-06" and d["usd_inr"] == 94.3996
    assert d["equity_net_cr"] == 16277.99 and d["equity_exchange_net_cr"] == 16168.66
    assert d["total"] == {"gross_buy": 38650.44, "gross_sell": 19221.81, "net": 19428.63, "net_usd_mn": 2058.12}
    debt = next(r for r in d["rows"] if r["category"] == "Debt-General Limit" and r["route"] == "Primary market & others")
    assert debt["net"] == -490.0                                   # '(490.00)' is a net sale
    assert len(d["rows"]) == 24 and len(d["derivatives"]) == 9 and d["derivatives_date"] == "2026-11-06"
    assert d["derivatives"][0] == {"product": "Index Futures", "buy_contracts": 68557, "buy_cr": 11166.18, "sell_contracts": 20004,
                                   "sell_cr": 3281.7, "oi_contracts": 241612, "oi_cr": 39666.3}


@pytest.mark.parametrize("old,new", [
    (b"16168.66", b"16100.66"),             # equity net no longer = buy - sell
    (b"<td>38650.44</td>", b"<td>38000.44</td>"),   # grand total no longer the sum of sub-totals
])
def test_nsdl_numbers_must_add_up(old, new):
    body = raw("nsdl", "latest.html")
    assert old in body
    assert kind(nsdl.parse_latest, body.replace(old, new, 1)) == ErrorKind.PARSE_FAIL


def test_nsdl_blocked_or_changed_page():
    assert kind(nsdl.parse_latest, b"<html><title>Access denied</title></html>") == ErrorKind.BLOCKED_HTML
    assert kind(nsdl.parse_latest, b"oops") == ErrorKind.PARSE_FAIL


def test_nsdl_provider_and_tool(st, monkeypatch):
    from drishti import data_tools as dt
    monkeypatch.setattr(nsdl, "get", lambda url, prof, **k: Fetched(url, raw("nsdl", "latest.html"), 200))
    r = nsdl.fpi_daily(today=TODAY)
    assert r.ok and r.as_of == "2026-11-06"
    assert nsdl.fpi_daily(today=date(2026, 11, 20)).error.kind == ErrorKind.STALE
    assert dt.fpi_flows(st).error.kind == ErrorKind.NOT_FOUND
    ds.persist_fpi(st, r.data)
    f = dt.fpi_flows(st)
    assert f.ok and f.data["days"][0]["equity_net_cr"] == 16277.99 and f.data["days"][0]["total_net_cr"] == 19428.63
    assert f.data["days"][0]["debt_net_cr"] == round(478.54 + 44.12 + 2669.22, 2)
    assert st.query("SELECT COUNT(*) AS n FROM fpi_nsdl_deriv")[0]["n"] == 9


# --- tools, jobs ----------------------------------------------------------------------------------------------------
def test_calendar_tool(st, monkeypatch):
    from drishti import data_tools as dt
    assert dt.calendar(st).error.kind == ErrorKind.NOT_FOUND
    ds.persist_board_meetings(st, c.parse_board_meetings(raw("nse_corp", "board_meetings.json")))
    monkeypatch.setattr(dt, "today_ist", lambda: date(2026, 11, 1))
    r = dt.calendar(st, "SBIN", days=10)
    assert r.ok and r.data["count"] == 1 and r.data["meetings"][0]["meeting_date"] == "2026-11-03"
    assert dt.calendar(st, days=40).data["count"] > 10


def test_jobs_carry_the_new_steps_in_a_safe_order():
    from drishti import jobs
    corp = [s.dataset for s in jobs.JOBS["eod_corporate"].steps]
    assert corp.index("corp_actions") < corp.index("bse_corp_actions")          # NSE first, so BSE copies are skipped
    assert {"board_meetings", "bse_result_calendar"} <= set(corp)
    assert "nsdl_fpi" in [s.dataset for s in jobs.JOBS["premarket"].steps]
    from drishti.jobs import backfill as bf
    assert "deals" in bf.TARGETS and bf.DEFAULT_DAYS["deals"] == 365


def test_mcp_lists_batch3_tools():
    pytest.importorskip("mcp")
    import asyncio
    from drishti import mcp_server as m
    names = {t.name for t in asyncio.run(m.build_server(lambda: Store()).list_tools())}
    assert {"get_calendar", "get_fpi_flows"} <= names


# --- special sessions (Diwali Muhurat trading on Sunday 8 Nov 2026) -------------------------------------------------
def _holidays(st):
    ds.persist_holidays(st, c.parse_holidays(raw("nse_corp", "holiday_master.json"), "CM"))


def test_muhurat_sunday_is_a_session_and_the_next_holiday_is_not(st):
    from datetime import datetime
    from drishti import jobs
    from drishti.core.canary import IST
    _holidays(st)
    assert "2026-11-08" in ds.special_sessions(st)                      # 'Diwali Laxmi Pujan*'
    assert ds.is_holiday(st, "2026-11-08") is False and ds.is_session(st, "2026-11-08")
    assert ds.is_holiday(st, "2026-11-10") is True and not ds.is_session(st, "2026-11-10")   # Balipratipada
    assert not ds.is_session(st, "2026-11-07")                           # an ordinary Saturday
    assert jobs.expected_trading_day(datetime(2026, 11, 8, 20, 0, tzinfo=IST), st) == date(2026, 11, 8)
    assert jobs.expected_trading_day(datetime(2026, 11, 9, 8, 0, tzinfo=IST), st) == date(2026, 11, 8)
    assert jobs.expected_trading_day(datetime(2026, 11, 10, 20, 0, tzinfo=IST), st) == date(2026, 11, 9)
    assert jobs.expected_trading_day(datetime(2026, 11, 8, 20, 0, tzinfo=IST)) == date(2026, 11, 6)  # no store: weekdays


def test_latest_and_backfills_try_a_weekend_special_session():
    from drishti.jobs import backfill as bf
    from drishti.core.envelope import Result
    from drishti.providers import _util as u
    asked = []
    u.latest(lambda d: asked.append(d) or Result(error=DrishtiError(ErrorKind.NOT_FOUND, "x")), date(2026, 11, 8), max_back=2,
             sessions={"2026-11-08"})
    assert asked[0] == date(2026, 11, 8) and date(2026, 11, 7) not in asked
    days = list(bf._weekdays_back(date(2026, 11, 9), 4, {"2026-11-08"}))
    assert days == [date(2026, 11, 9), date(2026, 11, 8), date(2026, 11, 6)]


def test_weekend_bar_of_a_special_session_is_kept(st):
    _holidays(st)
    ds.persist_macro(st, "yahoo:^NSEI", [{"date": "2026-11-06", "value": 1.0}, {"date": "2026-11-07", "value": 2.0},
                                         {"date": "2026-11-08", "value": 3.0}], "t")
    ds.prune_incomplete(st, "yahoo:^NSEI", "2026-11-09")
    assert [r["obs_date"] for r in st.query("SELECT obs_date FROM macro_series ORDER BY obs_date")] == ["2026-11-06", "2026-11-08"]


def test_bulk_history_includes_a_weekend_special_session(monkeypatch):
    nse_api(monkeypatch, _fake_deals({"2026-11-06": 3, "2026-11-08": 4}))
    r = c.historical_deals("bulk", date(2026, 11, 6), date(2026, 11, 8), extra_days={"2026-11-08"})
    assert r.ok and len(r.data["deals"]) == 7
    assert len(c.historical_deals("bulk", date(2026, 11, 6), date(2026, 11, 8)).data["deals"]) == 3

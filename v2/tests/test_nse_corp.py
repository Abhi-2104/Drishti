"""Batch 1 sources on real saved responses: announcements (NSE+BSE), corporate actions, credit ratings, large deals,
IPOs, holidays, expiries, market status / Gift Nifty, and per-stock price history + price context."""
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

import pytest

from drishti.core.canary import IST
from drishti.core.envelope import DrishtiError, ErrorKind, Result
from drishti.core.fetch import Fetched
from drishti.providers import bse, nse_archive, nse_corp as c
from drishti.signals import price
from drishti.store import Store
from drishti.store import datasets as ds

FIX = Path(__file__).parent / "fixtures"
NC = FIX / "nse_corp"
TODAY = date(2026, 11, 8)


def raw(name) -> bytes:
    return (NC / name).read_bytes()


def kind(fn, *a, **k) -> ErrorKind:
    with pytest.raises(DrishtiError) as ei:
        fn(*a, **k)
    return ei.value.kind


@pytest.fixture
def st():
    s = Store()
    yield s
    s.close()


@pytest.fixture
def api(monkeypatch):
    """Fake NSE API: path -> body (or exception)."""
    calls = []

    def install(**bodies):
        def fake(path, **params):
            calls.append((path, params))
            v = bodies[path]
            if isinstance(v, Exception):
                raise v
            return Fetched("https://www.nseindia.com/api/" + path, v, 200)
        monkeypatch.setattr(c.nse_live, "api_get", fake)
        return calls
    return install


# --- announcements ---------------------------------------------------------------------------------------------
def test_nse_announcements_real():
    rows = c.parse_announcements(raw("corporate_announcements.json"))
    assert len(rows) == 20
    r = rows[0]
    assert (r["symbol"], r["isin"], r["published"], r["category"]) == ("ESDS", "INE0DRI01029", "2026-11-08T00:00:16", "General Updates")
    assert r["url"].startswith("https://nsearchives.nseindia.com/corporate/") and r["source"] == "NSE"


def test_nse_announcements_provider_and_layout_change(api):
    calls = api(**{"corporate-announcements": raw("corporate_announcements.json")})
    r = c.announcements(days=2, today=TODAY)
    assert r.ok and r.as_of == "2026-11-08"
    assert calls[0][1] == {"index": "equities", "from_date": "06-11-2026", "to_date": "08-11-2026"}
    assert c.announcements(today=date(2026, 11, 30)).error.kind == ErrorKind.STALE
    broken = json.dumps([{k: v for k, v in json.loads(raw("corporate_announcements.json"))[0].items() if k != "an_dt"}]).encode()
    assert kind(c.parse_announcements, broken) == ErrorKind.PARSE_FAIL


def test_bse_announcements_real_and_paging(monkeypatch):
    rows, total = bse.parse_announcements((FIX / "bse" / "announcements_500325.json").read_bytes())
    assert total == 3 and len(rows) == 3 and rows[0]["scrip_code"] == "500325" and rows[0]["category"] == "Company Update"
    assert rows[0]["url"] == bse.ATTACH + "bab324f6-6fab-42fc-8b79-a3881f4fb7c7.pdf" and rows[0]["published"] == "2026-11-06T19:17:12"
    body = json.loads((FIX / "bse" / "announcements_500325.json").read_bytes())
    body["Table1"] = [{"ROWCNT": 5}]          # BSE says 5, pages hold 3 then nothing: incomplete, refused
    pages = [json.dumps(body).encode(), json.dumps({"Table": [], "Table1": [{"ROWCNT": 5}]}).encode()]
    monkeypatch.setattr(bse, "get", lambda url, profile, expect="json", **k: Fetched(url, pages.pop(0), 200))
    r = bse.announcements("500325", today=TODAY)
    assert r.error.kind == ErrorKind.PARSE_FAIL and "3 of 5" in r.error.detail
    assert kind(bse.parse_announcements, b"<html>blocked</html>") == ErrorKind.BLOCKED_HTML


# --- corporate actions -------------------------------------------------------------------------------------------
def test_corporate_actions_real():
    rows = c.parse_corporate_actions(raw("corporate_actions.json"))
    t = rows[0]
    assert (t["symbol"], t["isin"], t["ex_date"], t["kind"], t["face_value"]) == ("TANLA", "INE483C01032", "2026-11-02", "dividend", 1.0)
    assert t["bc_start"] is None
    kinds = {r["kind"] for r in rows}
    assert {"dividend", "distribution", "split", "bonus", "rights", "interest"} <= kinds


@pytest.mark.parametrize("subject, k", [("Interim Dividend - Rs 6 Per Share", "dividend"), ("Face Value Split (Sub-Division) - From Rs 10 To Rs 2", "split"),
                                        ("Bonus 1:1", "bonus"), ("Buy Back", "buyback"), ("Annual General Meeting", "agm"), ("Something new", "other")])
def test_action_kind(subject, k):
    assert c.action_kind(subject) == k


# --- credit ratings -----------------------------------------------------------------------------------------------
def test_credit_ratings_real():
    rows = c.parse_credit_ratings(raw("credit_ratings.json"))
    r = next(x for x in rows if x["app_id"] == "15092")
    assert (r["agency"], r["rating"], r["outlook"], r["action"], r["rating_date"]) == ("ICRA Limited", "AAA", "Stable", "Reaffirm", "2026-11-06")
    assert r["listed"] is False and r["symbol"] is None and r["previous_rating"] == "AAA" and r["direction_by_scale"] == "unchanged"
    assert any(x["action"] == "Upgrade" for x in rows) and r["broadcast"] == "2026-11-07T10:14:52"


@pytest.mark.parametrize("a, b, d", [("CRISIL AA-", "CRISIL A+", "upgrade"), ("[ICRA]A(CE)", "[ICRA]AA", "downgrade"), ("BBB+", "BBB+", "unchanged")])
def test_rating_scale_direction(a, b, d):
    ra, rb = c.rating_rank(a.replace("[", "").replace("]", "")), c.rating_rank(b.replace("[", "").replace("]", ""))
    assert ra is not None and rb is not None and (("upgrade" if ra > rb else "downgrade" if ra < rb else "unchanged") == d)
    assert c.rating_rank("A1+") is None and c.rating_rank(None) is None     # short-term scale is not ranked, not guessed


# --- large deals ----------------------------------------------------------------------------------------------------
def test_large_deals_real_and_count_check():
    d = c.parse_large_deals(raw("short_selling.json"))
    assert d["as_on"] == "2026-11-06" and (len(d["bulk"]), len(d["block"]), len(d["short"])) == (96, 20, 142)
    b = d["bulk"][0]
    assert (b["symbol"], b["side"], b["qty"], b["price"], b["client"]) == ("AAATECH", "BUY", 84322, 96.19, "VAIBHAV RAJENDRA DOSHI")
    assert d["short"][0]["date"] == "2026-11-05" and d["short"][0]["side"] is None
    doc = json.loads(raw("short_selling.json"))
    doc["SHORT_DEALS_DATA"] = doc["SHORT_DEALS_DATA"][:-1]              # NSE says 142, list has 141: truncated
    assert kind(c.parse_large_deals, json.dumps(doc).encode()) == ErrorKind.PARSE_FAIL


def test_deals_persist_idempotent(st):
    d = c.parse_large_deals(raw("short_selling.json"))
    n = ds.persist_deals(st, d["bulk"] + d["block"] + d["short"], "nse_api")
    assert ds.persist_deals(st, d["bulk"], "nse_api") and st.query("SELECT COUNT(*) AS n FROM deal")[0]["n"] <= n
    assert st.query("SELECT COUNT(*) AS n FROM deal WHERE kind='short'")[0]["n"] > 0


# --- IPOs ---------------------------------------------------------------------------------------------------------------
def test_ipos_real_and_provider(api):
    past = c.parse_ipos(raw("ipo_past.json"), "past")
    v = past[0]
    assert (v["company"], v["open"], v["close"], v["security_type"], v["price_range"]) == ("Vedant Poly Fabs Limited", "2026-11-02", "2026-11-04", "SME", "Rs.88 to Rs.94")
    api(**{"public-past-issues": raw("ipo_past.json"), "ipo-current-issue": raw("ipo_current.json"), "all-upcoming-issues": raw("ipo_upcoming.json")})
    r = c.ipos(today=TODAY)
    assert r.ok and r.data["current"] == [] and r.data["recent"] and r.as_of == "2026-11-04"
    assert c.ipos(today=date(2027, 3, 1)).error.kind == ErrorKind.STALE      # nothing closed for months: frozen feed


def test_ipo_current_shape_is_picked_tolerantly():
    body = json.dumps([{"symbol": "NEWCO", "companyName": "New Co Ltd", "issueStartDate": "10-Nov-2026", "issueEndDate": "12-Nov-2026",
                        "priceBand": "Rs.100 to Rs.105", "issueSize": "1000000", "series": "EQ"}]).encode()
    (r,) = c.parse_ipos(body, "current")
    assert (r["symbol"], r["open"], r["close"], r["price_range"], r["security_type"]) == ("NEWCO", "2026-11-10", "2026-11-12", "Rs.100 to Rs.105", "EQ")


# --- holidays, expiries, market status -------------------------------------------------------------------------------
def test_holidays_real_and_provider(api, st):
    cm = c.parse_holidays(raw("holiday_master.json"), "CM")
    assert cm[0] == {"date": "2026-01-15", "segment": "CM", "description": "Municipal Corporation Election - Maharashtra"}
    assert "2026-10-02" in {r["date"] for r in cm}
    api(**{"holiday-master": raw("holiday_master.json")})
    r = c.holidays(today=TODAY)
    ds.persist_holidays(st, r.data)
    assert ds.is_holiday(st, "2026-10-02") is True and ds.is_holiday(st, "2026-10-01") is False and ds.is_holiday(st, "2027-01-26") is None
    assert c.holidays(today=date(2027, 2, 1)).error.kind == ErrorKind.STALE


def test_expiries_real():
    ex = c.parse_expiries(raw("option_contract_info.json"), TODAY)
    assert ex[:3] == ["2026-11-09", "2026-11-17", "2026-11-23"]
    assert kind(c.parse_expiries, raw("option_contract_info.json"), date(2027, 6, 1)) == ErrorKind.STALE


def test_market_status_and_gift_nifty(api, st):
    d = c.parse_market_status(raw("marketStatus.json"))
    assert d["capital_market"] == "Closed" and d["nifty_close"] == 25030.35 and d["trade_date"] == "2026-11-06"
    g = d["gift_nifty"]
    assert (g["last"], g["expiry"], g["timestamp"]) == (25038.5, "2026-11-23", "2026-11-07T02:44:00")
    assert d["gift_premium_pts"] == round(25038.5 - 25030.35, 2)
    api(**{"marketStatus": raw("marketStatus.json")})
    r = c.market_status(today=TODAY)
    assert r.ok and r.as_of == "2026-11-07"
    ds.persist_gift(st, r.data)
    assert st.query("SELECT premium_pts FROM gift_nifty")[0]["premium_pts"] == d["gift_premium_pts"]
    assert c.market_status(today=date(2026, 11, 20)).error.kind == ErrorKind.STALE
    doc = json.loads(raw("marketStatus.json"))
    doc["giftnifty"] = {}
    calls = api(**{"marketStatus": json.dumps(doc).encode()})
    before = len(calls)
    assert c.market_status(today=TODAY, retry_wait_s=0).error.kind == ErrorKind.EMPTY and len(calls) - before == 2   # asked twice


def test_market_status_asks_again_when_gift_nifty_is_left_out(monkeypatch):
    doc = json.loads(raw("marketStatus.json"))
    bodies = [json.dumps({**doc, "giftnifty": {}}).encode(), raw("marketStatus.json")]
    monkeypatch.setattr(c.nse_live, "api_get", lambda path, **p: Fetched("u", bodies.pop(0), 200))
    r = c.market_status(today=TODAY, retry_wait_s=0)
    assert r.ok and r.data["gift_nifty"]["last"] == 25038.5 and bodies == []


def test_expected_trading_day_uses_stored_holidays(st):
    from drishti import jobs
    ds.persist_holidays(st, c.parse_holidays(raw("holiday_master.json"), "CM"))
    assert jobs.expected_trading_day(datetime(2026, 10, 2, 20, 0, tzinfo=IST), st) == date(2026, 10, 1)   # Gandhi Jayanti
    assert jobs.expected_trading_day(datetime(2026, 10, 2, 20, 0, tzinfo=IST)) == date(2026, 10, 2)       # without the list


# --- prices --------------------------------------------------------------------------------------------------------------
def test_prices_from_real_bhavcopies_nse_primary(st):
    nse = nse_archive.parse_cm_bhavcopy((FIX / "nse_archive" / "BhavCopy_NSE_CM_20261106.csv.zip").read_bytes(), min_rows=1)
    b = bse.parse_bhavcopy((FIX / "bse" / "BhavCopy_BSE_CM_20261106.csv").read_bytes(), min_rows=1)
    n1 = ds.persist_prices(st, nse, "NSE")
    n2 = ds.persist_prices(st, b, "BSE")
    both = {r["isin"] for r in nse} & {r["isin"] for r in b}
    assert n1 > 0 and n2 == len({r["isin"] for r in b} - {r["isin"] for r in nse})   # BSE only where NSE did not trade
    assert both and not st.query(f"SELECT 1 FROM price_eod WHERE exchange='BSE' AND isin IN ({','.join('?' * len(both))})", tuple(both))
    rel = st.query("SELECT close, exchange FROM price_eod WHERE isin='INE002A01018'")
    assert rel and rel[0]["exchange"] == "NSE"


def bars(st, isin, n, start=100.0, step=0.5, exchange="NSE"):
    from datetime import timedelta
    rows, d, i = [], date(2026, 11, 6), 0
    out = []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    for k, day in enumerate(reversed(out)):
        cl = start + step * k
        rows.append({"trade_date": day.isoformat(), "isin": isin, "series": "EQ", "symbol": "X", "open": cl, "high": cl + 1, "low": cl - 1,
                     "close": cl, "prev_close": cl - step, "volume": 1000, "turnover": 1.0, "trades": 1})
    ds.persist_prices(st, rows, exchange)
    return [x.isoformat() for x in reversed(out)]


def test_price_context(st):
    days = bars(st, "INE002A01018", 260)
    ds.persist_index_closes(st, [{"trade_date": d, "index_name": "Nifty 50", "close": 1000.0} for d in days])
    s = price.price_context(st, "INE002A01018", date(2026, 11, 8)).data
    comp = {x["input"]: x["value"] for x in s["components"]}
    assert s["label"].startswith("uptrend") and s["details"]["close"] == 100 + 0.5 * 259 and s["missing"] == []
    assert s["details"]["atr14"] == 2.0                  # high-low = 2, gaps 0.5 smaller: true range 2 every day
    assert comp["52-week high / low"][0] == 100 + 0.5 * 259 + 1 and comp["strength vs Nifty, ~3 months"] > 0
    assert not price.price_context(st, "INE101A01026", date(2026, 11, 8)).ok


def test_price_context_short_history_says_so(st):
    bars(st, "INE002A01018", 30)
    s = price.price_context(st, "INE002A01018", date(2026, 11, 8)).data
    assert s["label"] == "unknown trend" and any("200-day" in m for m in s["missing"])


def test_prices_backfill_keeps_nse_when_bse_fails(st, monkeypatch):
    from drishti.jobs import backfill as bf
    nse = nse_archive.parse_cm_bhavcopy((FIX / "nse_archive" / "BhavCopy_NSE_CM_20261106.csv.zip").read_bytes(), min_rows=1)
    monkeypatch.setattr(bf.nse_archive, "cm_bhavcopy", lambda d, store=None: Result(data=nse, as_of="2026-11-06") if d == date(2026, 11, 6)
                        else Result(error=DrishtiError(ErrorKind.NOT_FOUND, "x")))
    monkeypatch.setattr(bf.bse, "bhavcopy", lambda d, store=None: Result(error=DrishtiError(ErrorKind.BLOCKED, "403")))
    msgs = []
    rep = bf.backfill_prices(st, days=3, start=date(2026, 11, 6), progress=msgs.append)
    assert rep.done == 1 and st.query("SELECT COUNT(*) AS n FROM price_eod WHERE exchange='NSE'")[0]["n"] > 0
    assert any("BSE file failed" in m for m in msgs)


def test_registry_has_batch1_jobs():
    from drishti import jobs
    assert {"eod_corporate", "premarket", "calendars"} <= set(jobs.JOBS)
    for j in jobs.JOBS.values():
        for s in j.steps:
            assert jobs.STEPS[s.dataset] is s


def test_validate_live_corp_group_runs_on_fakes(monkeypatch, tmp_path, capsys, api):
    import importlib.util
    spec = importlib.util.spec_from_file_location("vl", Path(__file__).parents[1] / "phase0" / "scout" / "validate_live.py")
    vl = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(vl)
    monkeypatch.setattr(vl, "OUT", tmp_path / "out.json")
    api(**{"corporate-announcements": raw("corporate_announcements.json"), "corporates-corporateActions": raw("corporate_actions.json"),
           "corporate-credit-rating": raw("credit_ratings.json"), "snapshot-capital-market-largedeal": raw("short_selling.json"),
           "public-past-issues": raw("ipo_past.json"), "ipo-current-issue": raw("ipo_current.json"), "all-upcoming-issues": raw("ipo_upcoming.json"),
           "holiday-master": raw("holiday_master.json"), "option-chain-contract-info": raw("option_contract_info.json"),
           "marketStatus": raw("marketStatus.json")})
    monkeypatch.setattr(vl.bse, "announcements", lambda *a, **k: Result(data=[], as_of="2026-11-08"))
    monkeypatch.setattr(vl, "today_ist", lambda: TODAY)
    monkeypatch.setattr(c, "today_ist", lambda: TODAY)
    vl.main(["--only", "corp"])
    out = capsys.readouterr().out
    assert "date range honoured" in out and "credit_ratings" in out and "Gift Nifty 25038.5" in out

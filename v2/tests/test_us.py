"""US layer: SEC EDGAR submissions and XBRL company facts (real files, 8 Nov 2026), 13F information table (SEC's
documented schema), Yahoo US bars, tracking and the tools on top."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from drishti.core.envelope import DrishtiError, ErrorKind, Result
from drishti.core.fetch import Fetched
from drishti.providers import sec, yahoo
from drishti.store import Store
from drishti.store import datasets as ds

FIX = Path(__file__).parent / "fixtures"
UA = "Test Runner test@example.com"


def raw(*p) -> bytes:
    return FIX.joinpath(*p).read_bytes()


def test_no_user_agent_is_a_typed_error_that_says_what_to_do():
    r = sec.submissions(320193, user_agent=None)
    assert r.error.kind == ErrorKind.NOT_FOUND and "sec_user_agent" in r.error.detail
    assert sec.tickers(user_agent="no email here").error.kind == ErrorKind.NOT_FOUND


def test_submissions_real():
    d = sec.parse_submissions(raw("sec", "submissions_AAPL.json"))
    assert d["cik"] == 320193 and d["tickers"] == ["AAPL"] and d["industry"] == "Electronic Computers"
    k = next(f for f in d["filings"] if f["form"] == "10-K")
    assert k["filed"] == "2026-11-03" and k["report_date"] == "2026-09-26" and k["url"].endswith("aapl-20260926.htm")
    b = sec.parse_submissions(raw("sec", "submissions_BRK.json"))
    assert any(f["form"] == "13F-HR" for f in b["filings"])


def test_companyfacts_real_and_point_in_time():
    d = sec.parse_companyfacts(raw("sec", "companyfacts_AAPL.json"))
    q = d["quality"]
    assert q["latest_annual_period"] == "2026-09-26" and q["net_margin_pct"] == 27.6 and q["debt_to_equity"] == 0.7
    assert d["series"]["revenue"]["annual"][-1]["value"] == 478834000000
    old = sec.parse_companyfacts(raw("sec", "companyfacts_AAPL.json"), as_of="2024-01-01")
    assert old["quality"]["latest_annual_period"] == "2023-09-30"
    assert all(p["filed"] <= "2024-01-01" for s in old["series"].values() for k in ("annual", "quarterly") for p in s[k])
    with pytest.raises(DrishtiError) as e:
        sec.parse_companyfacts(raw("sec", "companyfacts_AAPL.json"), as_of="1990-01-01")
    assert e.value.kind == ErrorKind.EMPTY


INFO = b"""<?xml version="1.0" encoding="UTF-8"?>
<informationTable xmlns="http://www.sec.gov/edgar/document/thirteenf/informationtable">
  <infoTable><nameOfIssuer>APPLE INC</nameOfIssuer><titleOfClass>COM</titleOfClass><cusip>037833100</cusip><value>60000000000</value>
    <shrsOrPrnAmt><sshPrnamt>280000000</sshPrnamt><sshPrnamtType>SH</sshPrnamtType></shrsOrPrnAmt>
    <investmentDiscretion>DFND</investmentDiscretion><votingAuthority><Sole>280000000</Sole><Shared>0</Shared><None>0</None></votingAuthority></infoTable>
  <infoTable><nameOfIssuer>BANK AMER CORP</nameOfIssuer><titleOfClass>COM</titleOfClass><cusip>060505104</cusip><value>30000000000</value>
    <shrsOrPrnAmt><sshPrnamt>680000000</sshPrnamt><sshPrnamtType>SH</sshPrnamtType></shrsOrPrnAmt><putCall>Put</putCall>
    <investmentDiscretion>DFND</investmentDiscretion></infoTable>
</informationTable>"""


def test_13f_information_table():
    rows = sec.parse_13f_infotable(INFO)
    assert rows[0] == {"issuer": "APPLE INC", "class": "COM", "cusip": "037833100", "value_usd": 60000000000, "shares": 280000000,
                       "share_type": "SH", "put_call": None, "discretion": "DFND"}
    assert rows[1]["put_call"] == "Put"
    with pytest.raises(DrishtiError):
        sec.parse_13f_infotable(b"<informationTable/>")
    idx = json.dumps({"directory": {"item": [{"name": "primary_doc.xml"}, {"name": "46994.xml"}, {"name": "0001193125-26-352200.txt"}]}}).encode()
    assert sec.pick_infotable(idx) == "46994.xml"


def test_13f_tool_takes_the_latest_filing(monkeypatch):
    from drishti import data_tools as dt
    st = Store()
    monkeypatch.setattr(dt, "_ua", lambda: UA)
    pages = {"submissions": raw("sec", "submissions_BRK.json"),
             "index.json": json.dumps({"directory": {"item": [{"name": "primary_doc.xml"}, {"name": "infotable.xml"}]}}).encode(),
             "infotable.xml": INFO}
    monkeypatch.setattr(sec, "get", lambda url, prof, **k: Fetched(url, next(v for key, v in pages.items() if key in url or
                                                                              (key == "submissions" and "submissions" in url)), 200))
    r = dt.us_13f(st, "1067983")
    assert r.ok and r.data["filer"].upper().startswith("BERKSHIRE") and r.data["period"] and r.data["total_usd"] == 90000000000


def test_us_bars_and_tracking(monkeypatch):
    from drishti import jobs
    body = json.loads(raw("yahoo", "nsei_6mo.json"))
    body["chart"]["result"][0]["meta"]["symbol"] = "AAPL"
    b = json.dumps(body).encode()
    d = yahoo.parse_us(b, "AAPL")
    assert d["symbol"] == "AAPL" and d["bars"] and "adj_close" in d["bars"][-1] and "volume" in d["bars"][-1]
    monkeypatch.setattr(yahoo, "get", lambda url, prof, **k: Fetched(url, b, 200))
    st = Store()
    r = yahoo.us_daily("AAPL", store=st, today=date(2099, 1, 1), max_age_days=100000)
    assert r.ok and ds.tracked_us_symbols(st) == ["AAPL"]
    assert yahoo.us_daily("bad sym!").error.kind == ErrorKind.PARSE_FAIL
    rep = jobs._us_daily(st, date(2099, 1, 1))        # the job keeps the 7-day freshness rule: an old fixture fails loudly
    assert rep.error is not None and rep.error.kind == ErrorKind.STALE


def test_us_tools_are_registered():
    pytest.importorskip("mcp")
    import asyncio
    from drishti import mcp_server as m
    names = {t.name for t in asyncio.run(m.build_server(lambda: Store()).list_tools())}
    assert {"get_us_quote", "get_us_fundamentals", "get_us_filings", "get_us_13f", "get_index_valuation"} <= names

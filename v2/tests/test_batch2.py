"""Batch 2 sources on real saved responses: Screener fundamentals + documents, NSE quote, all-indices, option chain v3,
BSE scrip header. Live behaviour (market hours) is verified on Monday; these pin the parsing and the integrity checks."""
from __future__ import annotations

import json
import re
from datetime import date, datetime
from pathlib import Path

import pytest

from drishti.core.canary import IST
from drishti.core.envelope import DrishtiError, ErrorKind
from drishti.core.fetch import Fetched
from drishti.providers import nse_quote as q
from drishti.providers import screener as s
from drishti.store import Store
from drishti.store import datasets as ds

FIX = Path(__file__).parent / "fixtures"
PAGE = (FIX / "screener" / "reliance_consolidated.html").read_bytes()
QF = FIX / "nse_quote"


def kind(fn, *a, **k) -> ErrorKind:
    with pytest.raises(DrishtiError) as ei:
        fn(*a, **k)
    return ei.value.kind


# --- Screener -------------------------------------------------------------------------------------------------
def test_screener_reliance_page():
    d = s.parse_company(PAGE)
    assert (d["company"], d["basis"], d["nse_symbol"], d["bse_scrip"], d["latest_quarter"]) == \
           ("Reliance Industries Ltd", "consolidated", "RELIANCE", "500325", "2026-09-30")
    assert d["ratios"]["Market Cap"] == 1786295.0 and d["ratios"]["Stock P/E"] == 21.8 and d["ratios"]["High / Low"] == [1612.0, 1152.0]
    t = d["tables"]
    assert t["quarters"]["periods"][-1] == "2026-09-30" and t["profit-loss"]["periods"][-1] == "TTM"
    assert t["profit-loss"]["rows"]["Sales"][-2] == 1055780.0 and "OPM %" in t["quarters"]["rows"]
    assert set(t["quarterly-shp"]["rows"]) >= {"Promoters", "FIIs", "DIIs", "Public"}
    docs = d["documents"]
    assert docs["concalls"][0] == {"month": "Nov 2026", "kind": "Transcript",
                                   "url": "https://www.bseindia.com/xml-data/corpfiling/AttachHis/8e997741-4d35-4188-933a-cf020fb9c816.pdf"}
    assert docs["annual_reports"][0]["label"] == "Annual Report 2026" and len(docs["concalls"]) > 20


def test_screener_quality_metrics():
    m = s.quality(s.parse_company(PAGE))
    assert m["roce_pct"] == 10.0 and m["roe_pct"] == 8.91 and m["debt_to_equity"] == 0.43 and m["pe"] == 21.8
    assert m["ocf_to_net_profit_3y"] is not None and m["ocf_to_net_profit_3y"] > 1 and m["sales_cagr_3y_pct"] == 6.4


def test_screener_layout_changes_are_refused():
    text = PAGE.decode()
    assert kind(s.parse_company, text.replace('id="balance-sheet"', 'id="bs-new"')) == ErrorKind.PARSE_FAIL
    assert kind(s.parse_company, "<html><title>Just a moment...</title>captcha</html>") == ErrorKind.BLOCKED_HTML
    broken = re.sub(r'(<section id="ratios".*?<tbody>\s*<tr[^>]*>\s*<td class="text">[^<]*</td>)\s*<td[^>]*>[^<]*</td>', r"\1", text, count=1, flags=re.S)
    assert kind(s.parse_company, broken) == ErrorKind.PARSE_FAIL           # a row with a missing cell would shift every year


def test_screener_provider(monkeypatch):
    urls = []

    def fake(url, profile, expect="html", **k):
        urls.append(url)
        if url.endswith("/consolidated/"):
            raise DrishtiError(ErrorKind.NOT_FOUND, "404")
        return Fetched(url, PAGE, 200)
    monkeypatch.setattr(s, "get", fake)
    r = s.company("RELIANCE", today=date(2026, 11, 8))
    assert r.ok and urls == ["https://www.screener.in/company/RELIANCE/consolidated/", "https://www.screener.in/company/RELIANCE/"]
    assert s.company("RELIANCE", today=date(2027, 9, 1)).error.kind == ErrorKind.STALE
    assert s.company("../etc").error.kind == ErrorKind.PARSE_FAIL


def test_fundamentals_snapshots_are_point_in_time():
    with Store() as st:
        d = s.parse_company(PAGE)
        ds.persist_fundamentals(st, "INE002A01018", "RELIANCE", d, "2026-11-08T10:00:00")
        old = {**d, "latest_quarter": "2026-06-30"}
        ds.persist_fundamentals(st, "INE002A01018", "RELIANCE", old, "2026-08-10T10:00:00")
        assert ds.latest_fundamentals(st, "INE002A01018")["latest_quarter"] == "2026-09-30"
        assert ds.latest_fundamentals(st, "INE002A01018", "2026-09-01")["latest_quarter"] == "2026-06-30"   # what was known then
        assert ds.latest_fundamentals(st, "INE002A01018", "2026-07-01") is None


# --- NSE quote, all indices, option chain, BSE header -----------------------------------------------------------
def test_nse_quote_real():
    d = q.parse_quote((QF / "quote_reliance.json").read_bytes())
    assert (d["symbol"], d["isin"], d["last"], d["prev_close"], d["delivery_pct"]) == ("RELIANCE", "INE002A01018", 1320.0, 1313.4, 66.83)
    assert d["price_band"] == [1182.1, 1444.7] and d["industry"] == "Refineries & Marketing" and d["year_high"] == 1611.8


def test_nse_quote_provider_checks_symbol(monkeypatch):
    body = (QF / "quote_reliance.json").read_bytes()
    monkeypatch.setattr(q.nse_live, "api_get", lambda path, **p: Fetched("u", body, 200))
    r = q.quote("reliance", now=datetime(2026, 11, 9, 11, 0, tzinfo=IST))
    assert r.ok and r.data["live"] is True
    assert q.quote("RELIANCE", now=datetime(2026, 11, 8, 11, 0, tzinfo=IST)).data["live"] is False     # Sunday
    assert q.quote("TCS").error.kind == ErrorKind.PARSE_FAIL                                            # answer for another symbol


def test_all_indices_real():
    d = q.parse_all_indices((QF / "allIndices.json").read_bytes())
    n = next(x for x in d["indices"] if x["index"] == "NIFTY 50")
    assert d["timestamp"] == "2026-11-06T15:30" and n["last"] == 25030.35 and n["pe"] == 20.65
    assert d["breadth"] == {"advances": 4761, "declines": 4752, "unchanged": 74}


def test_option_chain_real():
    d = q.parse_option_chain((QF / "option_chain_v3_nifty.json").read_bytes())
    assert d["expiry"] == "2026-11-09" and d["underlying"] == 25030.35 and d["timestamp"] == "2026-11-06T15:40"
    assert {r["option_type"] for r in d["rows"]} == {"CE", "PE"} and all(24000 <= r["strike"] <= 26000 for r in d["rows"])
    doc = json.loads((QF / "option_chain_v3_nifty.json").read_bytes())
    doc["records"]["data"] = []
    assert kind(q.parse_option_chain, json.dumps(doc).encode()) == ErrorKind.EMPTY


def test_bse_header_real():
    d = q.parse_bse_header((QF / "scrip_header_500325.json").read_bytes())
    assert (d["scrip_code"], d["last"], d["prev_close"], d["as_on"]) == ("500325", 1318.1, 1313.35, "2026-11-06T16:00")
    assert kind(q.parse_bse_header, b"<html>blocked</html>") == ErrorKind.BLOCKED_HTML


@pytest.mark.parametrize("when, live", [((2026, 11, 9, 9, 14), False), ((2026, 11, 9, 9, 15), True), ((2026, 11, 9, 15, 31), False)])
def test_market_open(when, live):
    assert q.market_open(datetime(*when, tzinfo=IST)) is live


# --- data tools (store first, live when missing) ---------------------------------------------------------------
@pytest.fixture
def st():
    s_ = Store()
    s_.upsert_security("INE002A01018", name="Reliance Industries Limited", nse_symbol="RELIANCE", bse_scrip="500325", bse_symbol="RELIANCE", source="t")
    s_.upsert_security("INE00Q901014", name="S. M. Gold Limited", bse_scrip="542034", bse_symbol="SMGOLD", source="t")
    yield s_
    s_.close()


@pytest.mark.parametrize("href", ["symbol=M%26M", "symbol=M&amp;M", "symbol=M%26M&amp;series=EQ"])
def test_screener_symbol_with_ampersand(href):
    page = PAGE.replace(b"get-quotes/equity?symbol=RELIANCE", b"get-quotes/equity?" + href.encode())
    assert s.parse_company(page)["nse_symbol"] == "M&M"


def _without_quarters(page: bytes) -> bytes:
    """Screener's page for a half-yearly (SME) filer: the quarterly table has no period columns."""
    i = page.index(b'<section id="quarters"')
    a, b = page.index(b"<table", i), page.index(b"</table>", i) + len(b"</table>")
    return page[:a] + b'<table class="data-table"><thead><tr><th class="text"></th></tr></thead><tbody></tbody></table>' + page[b:]


def test_screener_half_yearly_filer_has_no_quarters_but_still_parses(monkeypatch):
    d = s.parse_company(_without_quarters(PAGE))
    assert d["latest_quarter"] is None and d["latest_annual"] and d["tables"]["quarters"]["periods"] == [] and d["notes"]
    assert s.quality(d)["roce_pct"] == 10.0
    monkeypatch.setattr(s, "get", lambda url, prof, **k: Fetched(url, _without_quarters(PAGE), 200))
    r = s.company("SMEX", today=date(2026, 11, 8))
    assert r.ok and r.as_of == d["latest_annual"]
    assert s.company("SMEX", today=date(2028, 6, 1)).error.kind == ErrorKind.STALE


def test_screener_other_empty_tables_still_refused():
    i = PAGE.index(b'<section id="profit-loss"')
    a, b = PAGE.index(b"<table", i), PAGE.index(b"</table>", i) + len(b"</table>")
    page = PAGE[:a] + b'<table class="data-table"><thead><tr><th class="text"></th></tr></thead><tbody></tbody></table>' + PAGE[b:]
    assert kind(s.parse_company, page) == ErrorKind.PARSE_FAIL


def test_lender_quality_does_not_report_meaningless_metrics():
    d = s.parse_company(PAGE)
    assert s.quality(d)["lender"] is False and s.is_lender(d) is False
    d["tables"]["quarters"]["rows"]["Financing Profit"] = [1.0] * len(d["tables"]["quarters"]["periods"])
    qd = s.quality(d)
    assert qd["lender"] is True and qd["roce_pct"] is None and qd["debt_to_equity"] is None and qd["ocf_to_net_profit_3y"] is None
    assert qd["roe_pct"] == 8.91 and "GNPA" in qd["note"] and qd["pb"] and qd["pb"] > 0


def test_fundamentals_tool_fetches_once_then_serves_store(st, monkeypatch):
    from drishti import data_tools as dt
    from drishti.core.envelope import Result
    calls = []
    monkeypatch.setattr(dt.screener, "company", lambda slug, **k: calls.append(slug) or Result(data=s.parse_company(PAGE), as_of="2026-09-30"))
    r1 = dt.fundamentals(st, "reliance")
    r2 = dt.fundamentals(st, "INE002A01018")
    assert r1.ok and r2.ok and calls == ["RELIANCE"] and r2.data["quality"]["roce_pct"] == 10.0
    monkeypatch.setattr(dt.screener, "company", lambda slug, **k: Result(error=DrishtiError(ErrorKind.BLOCKED, "403")))
    r3 = dt.fundamentals(st, "RELIANCE", refresh=True)
    assert r3.error.kind == ErrorKind.STALE and r3.data["quality"]["pe"] == 21.8      # older copy served, loudly


def test_quote_tool_nse_with_bse_cross_check_and_bse_only(st, monkeypatch):
    from drishti import data_tools as dt
    from drishti.core.envelope import Result
    nq, bq = q.parse_quote((QF / "quote_reliance.json").read_bytes()), q.parse_bse_header((QF / "scrip_header_500325.json").read_bytes())
    monkeypatch.setattr(dt.nse_quote, "quote", lambda sym, **k: Result(data=dict(nq), as_of="2026-11-06"))
    monkeypatch.setattr(dt.nse_quote, "bse_quote", lambda code, **k: Result(data=dict(bq, scrip_code=code), as_of="2026-11-06"))
    r = dt.quote(st, "RELIANCE")
    assert r.data["last"] == 1320.0 and r.data["bse_cross_check"]["gap_pct"] == round((1318.1 / 1320 - 1) * 100, 3)
    assert dt.quote(st, "542034").data["scrip_code"] == "542034"


def test_quote_tool_uses_the_sme_series_and_falls_back_to_bse(st, monkeypatch):
    from drishti import data_tools as dt
    from drishti.core.envelope import Result
    st.upsert_security("INE0SME01010", name="Small Co Limited", nse_symbol="SMALLCO", nse_series="SM", bse_scrip="543999", source="t")
    seen = []
    monkeypatch.setattr(dt.nse_quote, "quote", lambda sym, series="EQ", **k: seen.append(series) or Result(data={"last": 90.0}, as_of="2026-11-06"))
    assert dt.quote(st, "SMALLCO").ok and seen == ["SM"]
    monkeypatch.setattr(dt.nse_quote, "quote", lambda sym, **k: Result(error=DrishtiError(ErrorKind.NOT_FOUND, "404")))
    monkeypatch.setattr(dt.nse_quote, "bse_quote", lambda code, **k: Result(data={"last": 90.07, "scrip_code": code}, as_of="2026-11-06"))
    r = dt.quote(st, "SMALLCO")
    assert r.ok and r.data["scrip_code"] == "543999" and "BSE shown instead" in r.data["note"]


def test_nse_quote_refuses_odd_series():
    assert q.quote("RELIANCE", series="E Q").error.kind == ErrorKind.PARSE_FAIL


def test_announcements_tool_merges_both_exchanges(st, monkeypatch):
    from drishti import data_tools as dt
    from drishti.core.envelope import Result
    from drishti.providers import bse as bse_p
    rows, _ = bse_p.parse_announcements((FIX / "bse" / "announcements_500325.json").read_bytes())
    nse_row = {**rows[0], "source": "NSE", "id": "n1", "isin": "INE002A01018", "symbol": "RELIANCE", "scrip_code": None}   # same filing on NSE
    monkeypatch.setattr(dt.nse_corp, "announcements", lambda **k: Result(data=[nse_row], as_of="2026-11-08"))
    monkeypatch.setattr(dt.bse, "announcements", lambda code, **k: Result(data=rows, as_of="2026-11-08"))
    monkeypatch.setattr(dt, "today_ist", lambda: date(2026, 11, 8))
    r = dt.announcements(st, "RELIANCE", days=30)
    d = r.data
    assert r.ok and len(d["announcements"]) == len(rows) and d["only_on_bse"] == len(rows) - 1 and d["only_on_nse"] == 0
    monkeypatch.setattr(dt.nse_corp, "announcements", lambda **k: Result(error=DrishtiError(ErrorKind.BLOCKED, "x")))
    monkeypatch.setattr(dt.bse, "announcements", lambda code, **k: Result(error=DrishtiError(ErrorKind.BLOCKED, "x")))
    r = dt.announcements(st, "RELIANCE", days=30)
    assert r.error.kind == ErrorKind.BLOCKED and r.data["announcements"]       # both live feeds failed: stored rows + a loud error


def test_market_today_needs_premarket(st):
    from drishti import data_tools as dt
    assert dt.market_today(st).error.kind == ErrorKind.NOT_FOUND


def test_mcp_lists_the_data_tools():
    pytest.importorskip("mcp")
    import asyncio
    from drishti import mcp_server as m
    srv = m.build_server(lambda: Store())
    names = {t.name for t in asyncio.run(srv.list_tools())}
    assert {"get_fundamentals", "get_quote", "get_announcements", "get_corporate_actions", "get_credit_ratings", "get_deals",
            "get_market_today", "get_price_context", "get_option_chain"} <= names


# --- real pages saved 8 Nov 2026: a bank, an NBFC, an '&' symbol, an SME (half-yearly) filer -----------------------
def _page(name):
    return (FIX / "screener" / f"{name}.html").read_bytes()


@pytest.mark.parametrize("name,symbol,pb,roe", [("hdfcbank_consolidated", "HDFCBANK", 2.02, 13.6),
                                                ("bajfinance_consolidated", "BAJFINANCE", 5.49, 18.2)])
def test_real_lender_pages(name, symbol, pb, roe):
    d = s.parse_company(_page(name))
    q = s.quality(d)
    assert d["nse_symbol"] == symbol and d["latest_quarter"] == "2026-09-30" and "Financing Profit" in d["tables"]["quarters"]["rows"]
    assert q["lender"] is True and q["pb"] == pb and q["roe_pct"] == roe
    assert q["roce_pct"] is None and q["debt_to_equity"] is None and q["ocf_to_net_profit_3y"] is None


def test_real_m_and_m_page():
    d = s.parse_company(_page("m_m_consolidated"))
    q = s.quality(d)
    assert d["nse_symbol"] == "M&M" and q["lender"] is False and q["roce_pct"] == 15.0 and q["debt_to_equity"] == 1.57


def test_real_sme_falls_back_to_standalone_annual_figures(monkeypatch):
    assert kind(s.parse_company, _page("bcssl_consolidated")) == ErrorKind.PARSE_FAIL      # Screener's empty consolidated page
    pages = {True: _page("bcssl_consolidated"), False: _page("bcssl_standalone")}
    monkeypatch.setattr(s, "_fetch", lambda slug, cons: Fetched("u", pages[cons], 200))
    r = s.company("BCSSL", today=date(2026, 11, 8))
    assert r.ok and r.data["basis"] == "standalone" and r.data["latest_quarter"] is None and r.as_of == "2026-03-31"
    assert r.data["notes"] and s.quality(r.data)["roe_pct"] == 29.9

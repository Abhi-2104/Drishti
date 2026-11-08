"""Security master (EQUITY_L + BSE + NSE ETFs joined on ISIN) and the resolver on top of it."""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import pytest

from drishti import resolve as rs
from drishti.core.envelope import DrishtiError, ErrorKind, Result
from drishti.core.fetch import Fetched
from drishti.providers import bse, nse_archive
from drishti.providers import security_master as sm
from drishti.store import Store

FIX = Path(__file__).parent / "fixtures"
EQL = (FIX / "nse_archive" / "EQUITY_L.csv").read_bytes()


def kind(fn, *a, **k) -> ErrorKind:
    with pytest.raises(DrishtiError) as ei:
        fn(*a, **k)
    return ei.value.kind


def bse_rows():
    return bse.parse_bhavcopy((FIX / "bse" / "BhavCopy_BSE_CM_20261106.csv").read_bytes(), min_rows=1)


def nse_cm_rows():
    return nse_archive.parse_cm_bhavcopy((FIX / "nse_archive" / "BhavCopy_NSE_CM_20261106.csv.zip").read_bytes(), min_rows=1)


def merged():
    return sm.merge_master(sm.parse_equity_l(EQL, min_rows=1), bse_rows(), nse_cm_rows())


# --- EQUITY_L ---------------------------------------------------------------------------------------
def test_equity_l_real_rows():
    rows = {r["nse_symbol"]: r for r in sm.parse_equity_l(EQL, min_rows=1)}
    r = rows["20MICRONS"]
    assert (r["isin"], r["name"], r["nse_series"], r["listed_on"], r["face_value"]) == ("INE144J01027", "20 Microns Limited", "EQ", "2008-10-06", 5.0)
    assert rows["21STCENMGM"]["nse_series"] == "BE"            # trade-for-trade series are kept, with their series


def test_equity_l_integrity():
    assert kind(sm.parse_equity_l, EQL) == ErrorKind.PARSE_FAIL                      # 78 rows: truncated vs the real minimum
    text = EQL.decode()
    assert kind(sm.parse_equity_l, text.replace("SYMBOL,", "TICKER,", 1).encode(), min_rows=1) == ErrorKind.PARSE_FAIL
    dup = text.rstrip("\n") + "\n" + text.splitlines()[1] + "\n"
    assert kind(sm.parse_equity_l, dup.encode(), min_rows=1) == ErrorKind.PARSE_FAIL  # same symbol twice
    bad = text.replace("INE144J01027", "INE144J01028")                               # one bad check digit in 78 rows
    assert kind(sm.parse_equity_l, bad.encode(), min_rows=1) == ErrorKind.PARSE_FAIL   # >1% invalid is refused
    assert kind(sm.parse_equity_l, b"<html>blocked</html>", min_rows=1) in (ErrorKind.PARSE_FAIL, ErrorKind.EMPTY)
    many = "\n".join(text.splitlines()[:1] + text.splitlines()[1:] * 1) + "\n"
    assert sm.parse_equity_l(many.encode(), min_rows=1)


def test_instrument_class_from_isin_shape():
    c = sm.instrument_class
    assert c("INE002A01018") == "equity" and c("INE532F07FO0") == "debt" and c("INE306N08AB1") == "debt"
    assert c("INF732E01268") == "fund" and c("IN0020200104") == "sovereign" and c("IN9100A01010") == "other"


# --- merge ------------------------------------------------------------------------------------------
def test_merge_on_fixtures():
    m = merged()
    by = {s["isin"]: s for s in m["securities"]}
    both = [s for s in by.values() if s["listing"] == "both"]
    assert both and all(s["nse_symbol"] and s["bse_scrip"] and s["instrument_class"] == "equity" for s in both)
    only_bse = by["INE714B01016"]                                                  # ANDHRAPET: on BSE, not on NSE
    assert (only_bse["listing"], only_bse["nse_symbol"], only_bse["bse_scrip"], only_bse["bse_series"]) == ("bse", None, "500012", "X")
    etf = by["INF579M01BB5"]                                                       # GOLD360: ETF traded on NSE, picked up from the bhavcopy
    assert (etf["instrument_class"], etf["nse_symbol"], etf["listing"]) == ("fund", "GOLD360", "nse")
    assert not any(i.startswith("IN0") for i in by)                                # sovereign gold bonds are not part of this master
    st = m["stats"]
    assert st["skipped_from_bse"] and st["securities"] == len(by) and st["rejected"] == 0 and st["conflicts"] == 0
    assert st["by_listing"]["both"] == len(both)


def test_merge_reports_what_it_does_not_trust():
    eq = [{"nse_symbol": "VIRAT", "name": "Brahm Virat", "nse_series": "EQ", "isin": "INE467D01017", "listed_on": "2000-01-01", "face_value": 1.0}]
    row = lambda sym, code, isin, series="A": {"symbol": sym, "scrip_code": code, "isin": isin, "series": series, "name": "x"}  # noqa: E731
    m = sm.merge_master(eq, [row("BRAHMVIRAT", "500001", "INE467D01017"), row("BRAHMVIRAT2", "500002", "INE467D01017"),
                             row("BADISIN", "500003", "INE467D01018"), row("NCD1", "939999", "INE532F07FO0", "F")])
    (s,) = m["securities"]
    assert (s["nse_symbol"], s["bse_symbol"], s["bse_scrip"], s["listing"]) == ("VIRAT", "BRAHMVIRAT", "500001", "both")   # rename: ISIN still joins
    assert m["conflicts"] == [{"isin": "INE467D01017", "kept": "500001", "ignored": "500002"}]                           # not silently picked
    assert m["rejected"][0]["scrip"] == "500003" and m["stats"]["skipped_from_bse"] == {"debt": 1} and m["stats"]["symbol_differs"] == 1


# --- provider ---------------------------------------------------------------------------------------
def fake_inputs(monkeypatch, *, eq=None, bse_res=None, nse_res=None, scrips=None):
    monkeypatch.setattr(sm, "get", lambda url, profile, expect="text", **k: Fetched(url, eq if eq is not None else EQL, 200))
    ok = lambda rows, d="2026-11-06": Result(data=rows, as_of=d)  # noqa: E731
    monkeypatch.setattr(sm.bse, "bhavcopy", lambda d, store=None: bse_res or ok(bse_rows()))
    monkeypatch.setattr(sm.nse_archive, "cm_bhavcopy", lambda d, store=None: nse_res or ok(nse_cm_rows()))
    down = Result(error=DrishtiError(ErrorKind.NETWORK, "scrip list not available in this test"))
    monkeypatch.setattr(sm.bse, "scrip_list", lambda: scrips or down)       # never the live endpoint


def overlapping_eq() -> bytes:
    """EQUITY_L whose ISINs are all in the BSE fixture, so the cross-listing sanity check can pass."""
    isins = {r["isin"] for r in bse_rows() if r["isin"].startswith("INE") and r["isin"][7:9] == "01"}
    lines = EQL.decode().splitlines()
    return "\n".join([lines[0]] + [ln for ln in lines[1:] if ln.split(",")[6].strip() in isins]).encode()


def test_equity_list_freshness(monkeypatch):
    fake_inputs(monkeypatch)
    assert sm.equity_list(today=date(2026, 11, 8), min_rows=1).ok
    assert sm.equity_list(today=date(2027, 11, 8), min_rows=1).error.kind == ErrorKind.STALE      # no listing for a year: the feed stopped


def test_build_writes_to_store_and_checks_the_join(monkeypatch):
    fake_inputs(monkeypatch, eq=overlapping_eq(), scrips=Result(data=[], as_of="2026-11-08"))
    with Store() as st:
        r = sm.build_security_master(st, today=date(2026, 11, 8), min_equity_rows=1, max_listing_age_days=400)
        assert r.ok and r.as_of == "2026-11-06" and r.data["stats"]["overlap"] == 1.0 and r.data["stats"]["warnings"] == []
        assert st.get_security("INE714B01016")["bse_scrip"] == "500012" and st.get_security("INE714B01016")["instrument_class"] == "equity"
        n = st.query("SELECT COUNT(*) AS n FROM security")[0]["n"]
        assert n == r.data["securities"]
        assert sm.build_security_master(st, today=date(2026, 11, 8), min_equity_rows=1, max_listing_age_days=400).ok and st.query("SELECT COUNT(*) AS n FROM security")[0]["n"] == n  # idempotent


def test_build_adds_scrips_the_bhavcopy_did_not_show_and_warns_when_the_list_is_down(monkeypatch):
    extra = [{"scrip_code": "599999", "isin": "INE467D01017", "symbol": "NEWCO", "group": "B", "name": "Newco"}]
    fake_inputs(monkeypatch, eq=overlapping_eq(), scrips=Result(data=extra, as_of="2026-11-08"))
    with Store() as st:
        r = sm.build_security_master(st, today=date(2026, 11, 8), min_equity_rows=1, max_listing_age_days=400)
        assert r.ok and r.data["stats"]["warnings"] == []
    fake_inputs(monkeypatch, eq=overlapping_eq())                                  # list unavailable: loud warning, build still succeeds
    with Store() as st:
        r = sm.build_security_master(st, today=date(2026, 11, 8), min_equity_rows=1, max_listing_age_days=400)
        assert r.ok and "scrip list unavailable" in r.data["stats"]["warnings"][0] and "NETWORK" in r.data["stats"]["warnings"][0]


def test_build_refuses_when_nse_and_bse_do_not_line_up(monkeypatch):
    other = [{**r, "isin": "INE000A01012"} for r in bse_rows()[:1]]      # a BSE file whose ISINs match nothing on the NSE side
    fake_inputs(monkeypatch, bse_res=Result(data=other, as_of="2026-11-06"))
    with Store() as st:
        r = sm.build_security_master(st, today=date(2026, 11, 8), min_equity_rows=1, max_listing_age_days=400)
        assert r.error.kind == ErrorKind.PARSE_FAIL and "ISIN join" in r.error.detail
        assert st.query("SELECT COUNT(*) AS n FROM security")[0]["n"] == 0     # nothing half-written


def test_build_degrades_loudly_without_nse_bhavcopy(monkeypatch):
    fake_inputs(monkeypatch, eq=overlapping_eq(), nse_res=Result(error=DrishtiError(ErrorKind.BLOCKED, "403")))
    with Store() as st:
        r = sm.build_security_master(st, today=date(2026, 11, 8), min_equity_rows=1, max_listing_age_days=400)
        assert r.ok and "ETFs" in r.data["stats"]["warnings"][0]
        assert st.get_security("INF579M01BB5") is None


def test_build_passes_through_typed_failures(monkeypatch):
    fake_inputs(monkeypatch, bse_res=Result(error=DrishtiError(ErrorKind.STALE, "old")))
    with Store() as st:
        assert sm.build_security_master(st, today=date(2026, 11, 8), min_equity_rows=1, max_listing_age_days=400).error.kind == ErrorKind.STALE


# --- resolver ---------------------------------------------------------------------------------------
@pytest.fixture
def st():
    s = Store()
    m = merged()
    for x in m["securities"]:
        s.upsert_security(x["isin"], name=x["name"], nse_symbol=x["nse_symbol"], bse_scrip=x["bse_scrip"], bse_symbol=x["bse_symbol"],
                          source="t", instrument_class=x["instrument_class"], nse_series=x["nse_series"], bse_series=x["bse_series"])
    yield s
    s.close()


def test_resolve_by_every_handle(st):
    both = next(x for x in merged()["securities"] if x["listing"] == "both")
    for q, k in ((both["isin"], "isin"), (both["isin"].lower(), "isin"), (both["nse_symbol"], "symbol"), (both["nse_symbol"].lower(), "symbol"),
                 (both["bse_symbol"], "symbol"), (both["bse_scrip"], "bse_scrip")):
        r = rs.resolve(st, q)
        assert r.ok and r.data["kind"] == k and [m["isin"] for m in r.data["matches"]] == [both["isin"]] and not r.data["ambiguous"], q
    r = rs.resolve(st, "500012")                      # BSE-only name resolves by scrip code
    assert r.data["matches"][0]["isin"] == "INE714B01016" and r.data["matches"][0]["nse_symbol"] is None


def test_resolve_failures_are_typed_and_helpful(st):
    assert rs.resolve(st, "INE714B01017").error.kind == ErrorKind.PARSE_FAIL            # ISIN-shaped, bad check digit: say so
    assert rs.resolve(st, "INE000A01012").error.kind == ErrorKind.NOT_FOUND             # valid-looking ISIN we do not have
    assert rs.resolve(st, "").error.kind == ErrorKind.NOT_FOUND
    assert rs.resolve(st, "999999").error.kind == ErrorKind.NOT_FOUND
    e = rs.resolve(st, "Andhra Petro").error
    assert e.kind == ErrorKind.NOT_FOUND and "ANDHRA PETROCHEMICALS" in e.detail.upper().replace("LTD.", "") or "did you mean" in e.detail


def test_resolve_never_picks_between_two_companies(st):
    st.upsert_security("INE111A01011", nse_symbol="CLASH", name="First", source="t")
    st.upsert_security("INE222A01010", bse_symbol="CLASH", bse_scrip="511111", name="Second", source="t")
    r = rs.resolve(st, "clash")
    assert r.ok and r.data["ambiguous"] and [m["isin"] for m in r.data["matches"]] == ["INE111A01011", "INE222A01010"]


def test_search_escapes_like_wildcards(st):
    assert st.search_securities("%") == [] and st.search_securities("_") == []

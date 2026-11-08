"""MoSPI eSankhyiki: CPI, WPI, IIP and RBI reserves. Real captures (8 Nov 2026) drive the happy path; corruption and staleness are typed."""
from __future__ import annotations

import json
import re
from datetime import date, datetime
from pathlib import Path

import pytest

from drishti import jobs
from drishti.core.envelope import DrishtiError, ErrorKind
from drishti.core.fetch import Fetched
from drishti.providers import mospi
from drishti.signals import history as h
from drishti.store import Store
from drishti.store import datasets as ds

FIX = Path(__file__).parent / "fixtures" / "mospi"
CPI = (FIX / "cpi_2024_20261108.json").read_bytes()
CPI12 = (FIX / "cpi_2012_20261108.json").read_bytes()
WPI = (FIX / "wpi_20261108.json").read_bytes()
IIP = (FIX / "iip_20261108.json").read_bytes()
RBI = (FIX / "rbi_fx_reserves_20261108.json").read_bytes()
TODAY = date(2026, 11, 8)


def kind(fn, *a, **k) -> ErrorKind:
    with pytest.raises(DrishtiError) as ei:
        fn(*a, **k)
    return ei.value.kind


NO_DATA = json.dumps({"data": [], "meta_data": {"page": 1, "totalRecords": 0}, "msg": "No Data Found", "statusCode": True}).encode()


def doc(rows, **extra):
    return json.dumps({"data": rows, "meta_data": {"page": 1}, "msg": "Data fetched successfully", "statusCode": True, **extra}).encode()


def fake(monkeypatch, by_year: dict[int, bytes]):
    asked = []

    def _get(url, profile, expect="text", **k):
        y = int(re.search(r"[?&]year=(\d+)", url).group(1))
        asked.append(y)
        return Fetched(url, by_year.get(y, NO_DATA), 200)             # a year with nothing yet answers "No Data Found"
    monkeypatch.setattr(mospi, "get", _get)
    return asked


@pytest.fixture
def st():
    s = Store()
    yield s
    s.close()


# --- CPI ---------------------------------------------------------------------------------------------------
def test_cpi_real_capture():
    rows = mospi.parse_cpi(CPI)
    assert len(rows) == 10 and {r["period"] for r in rows} == {"2026-09"} and rows[0]["obs_date"] == "2026-09-30"
    h_ = mospi.cpi_headline(rows)
    assert h_["period"] == "2026-09" and h_["obs_date"] == "2026-09-30"
    assert (h_["combined"]["index"], h_["combined"]["inflation"]) == (109.49, 5.54)
    assert (h_["rural"]["inflation"], h_["urban"]["inflation"], h_["food"]["inflation"]) == (6.08, 4.88, 7.36)


def test_cpi_provider(monkeypatch):
    fake(monkeypatch, {2026: CPI})
    r = mospi.cpi(today=TODAY)
    assert r.ok and r.as_of == "2026-09-30" and r.data["latest"]["cpi_yoy"] == 5.54 and r.data["latest"]["food_yoy"] == 7.36
    assert r.data["series"]["mospi:cpi_yoy"] == [{"date": "2026-09-30", "value": 5.54}]


def test_cpi_goes_stale_with_the_month_named(monkeypatch):
    fake(monkeypatch, {2026: CPI})
    e = mospi.cpi(today=date(2027, 1, 20)).error
    assert e.kind == ErrorKind.STALE and "2026-09-30" in e.detail


def test_cpi_early_january_falls_back_to_last_year(monkeypatch):
    empty = NO_DATA
    dec = CPI.replace(b'"September"', b'"December"')
    asked = fake(monkeypatch, {2027: empty, 2026: dec})
    r = mospi.cpi(today=date(2027, 1, 5))
    assert asked == [2027, 2026] and r.ok and r.as_of == "2026-12-31"


def test_cpi_headline_row_missing_is_an_error_not_a_guess():
    rows = [r for r in mospi.parse_cpi(CPI) if r["division"] != "CPI (General)"]
    assert kind(mospi.cpi_headline, rows) == ErrorKind.PARSE_FAIL


def test_cpi_other_months_never_leak_into_the_headline():
    rows = mospi.parse_cpi(CPI)
    older = [{**r, "period": "2026-08", "obs_date": "2026-08-31", "inflation": 1.0} for r in rows]
    assert mospi.cpi_headline(rows + older)["combined"]["inflation"] == 5.54


@pytest.mark.parametrize("mutate,why", [
    (lambda r: {**r, "month": "Smarch"}, "unknown month"),
    (lambda r: {**r, "year": "1066"}, "implausible year"),
    (lambda r: {**r, "inflation": "5400"}, "impossible inflation"),
    (lambda r: {**r, "index": "abc"}, "non-numeric index"),
])
def test_cpi_row_corruption(mutate, why):
    j = json.loads(CPI)
    j["data"][2] = mutate(j["data"][2])
    assert kind(mospi.parse_cpi, json.dumps(j).encode()) == ErrorKind.PARSE_FAIL, why


def test_cpi_missing_column_and_wrong_documents():
    j = json.loads(CPI)
    del j["data"][0]["inflation"]
    assert kind(mospi.parse_cpi, json.dumps(j).encode()) == ErrorKind.PARSE_FAIL
    assert kind(mospi.parse_cpi, b"<html>blocked</html>") == ErrorKind.PARSE_FAIL
    assert kind(mospi.parse_cpi, b'{"hello": 1}') == ErrorKind.PARSE_FAIL
    assert kind(mospi.parse_cpi, doc([{"x": 1}], statusCode=False)) == ErrorKind.PARSE_FAIL
    assert kind(mospi.parse_cpi, b'{"data": {"a": 1}, "statusCode": true}') == ErrorKind.PARSE_FAIL


def test_old_base_cpi_is_not_mistaken_for_the_current_layout():
    assert kind(mospi.parse_cpi, CPI12) == ErrorKind.PARSE_FAIL          # base-2012 rows say `baseyear`/`subgroup`; they need their own reader


# --- WPI -------------------------------------------------------------------------------------------------------------
def test_wpi_real_capture():
    rows = mospi.parse_wpi(WPI)
    assert len(rows) == 10 and rows[0]["major_group"] == "Wholesale Price Index" and rows[0]["index"] == 112.5
    head = mospi.wpi_headline(rows)
    assert (head["period"], head["obs_date"], head["index"]) == ("2026-09", "2026-09-30", 112.5)
    assert [r["index"] for r in rows if r["item"] == "Wheat"] == [119.2]


def test_wpi_provider_and_staleness(monkeypatch):
    fake(monkeypatch, {2026: WPI})
    r = mospi.wpi(today=TODAY)
    assert r.ok and r.data["series"]["mospi:wpi_index"] == [{"date": "2026-09-30", "value": 112.5}]
    assert mospi.wpi(today=date(2027, 2, 1)).error.kind == ErrorKind.STALE


def test_wpi_headline_missing():
    rows = [r for r in mospi.parse_wpi(WPI) if r["major_group"] != "Wholesale Price Index"]
    assert kind(mospi.wpi_headline, rows) == ErrorKind.PARSE_FAIL


# --- IIP -------------------------------------------------------------------------------------------------------------
def test_iip_real_capture_and_stale_feed(monkeypatch):
    rows = mospi.parse_iip(IIP)
    assert [(r["period"], r["index"], r["growth"]) for r in rows] == [("2026-03", 173.2, 4.1), ("2026-02", 158.8, 5.1), ("2026-01", 169.9, 5.1)]
    assert mospi.iip_headline(rows)["period"] == "2026-03"
    fake(monkeypatch, {2026: IIP})
    e = mospi.iip(today=TODAY).error                                   # the feed ends in March: 7 months old on 8 Nov
    assert e.kind == ErrorKind.STALE and "2026-03-31" in e.detail
    r = mospi.iip(today=date(2026, 4, 20))                             # fresh when it was fresh
    assert r.ok and len(r.data["series"]["mospi:iip_yoy"]) == 3 and r.data["latest"]["iip_yoy"] == 4.1


# --- RBI reserves ------------------------------------------------------------------------------------------------------
def test_rbi_empty_year_is_typed_and_previous_year_is_tried(monkeypatch):
    assert kind(mospi.parse_rbi_reserves, RBI) == ErrorKind.EMPTY
    june = doc([{"indicator": " Foreign Exchange Reserve Monthly", "year": "2025", "month": "June", "foreign_exchange_reserve_type": "Total",
                 "foreign_exchange_reserve_currency": "US $ Millions", "value": "696654.00", "unit": None},
                {"indicator": " Foreign Exchange Reserve Monthly", "year": "2025", "month": "June", "foreign_exchange_reserve_type": "SDRs",
                 "foreign_exchange_reserve_currency": "US $ Millions", "value": "18700.00", "unit": None}])
    asked = fake(monkeypatch, {2026: RBI, 2025: june})
    r = mospi.rbi_reserves(today=TODAY)
    assert asked == [2026, 2025] and r.error.kind == ErrorKind.STALE and "2025-06-30" in r.error.detail      # ends June 2025: never passed off as current
    ok = mospi.rbi_reserves(today=date(2025, 8, 15))
    assert ok.ok and ok.data["latest"]["fx_reserves_usd_mn"] == 696654.0


def test_rbi_total_row_required():
    only_sdr = doc([{"year": "2025", "month": "June", "foreign_exchange_reserve_type": "SDRs",
                     "foreign_exchange_reserve_currency": "US $ Millions", "value": "1.0"}])
    assert kind(mospi.reserves_headline, mospi.parse_rbi_reserves(only_sdr)) == ErrorKind.PARSE_FAIL


# --- persistence, release lag, jobs ------------------------------------------------------------------------------------
def test_persist_and_release_lag(st, monkeypatch):
    fake(monkeypatch, {2026: CPI})
    r = mospi.cpi(today=TODAY)
    assert ds.persist_macro_bundle(st, r.data) == 3
    assert ds.persist_macro_bundle(st, r.data) == 3                                  # idempotent
    assert st.query("SELECT COUNT(*) AS n FROM macro_series WHERE series LIKE 'mospi:cpi%'")[0]["n"] == 3
    assert h.macro_released(st, "mospi:cpi_yoy", "2026-10-11", 12, 1) == []          # September CPI is not out until the 12th
    assert h.macro_released(st, "mospi:cpi_yoy", "2026-10-12", 12, 1) == [("2026-09-30", 5.54)]
    assert h.macro_released(st, "mospi:cpi_yoy", "2026-11-08", 12, 5) == [("2026-09-30", 5.54)]
    assert h.macro_released(Store(), "mospi:cpi_yoy", "2026-11-08", 12) == []        # no table yet: nothing, not an error


def test_macro_india_job(monkeypatch):
    job = jobs.JOBS["macro_india"]
    assert [s.dataset for s in job.steps] == ["mospi_cpi", "mospi_wpi", "mospi_iip", "mospi_rbi_reserves"]
    with Store() as s:
        monkeypatch.setattr(mospi, "get", lambda url, profile, expect="text", **k: Fetched(url, WPI if "wpi" in url else CPI if "cpi" in url else IIP if "iip" in url else RBI, 200))
        rep = jobs.run_job(s, "macro_india", now=datetime(2026, 11, 8, 18, 10, tzinfo=jobs.IST))
        by = {x.dataset: x for x in rep.steps}
        assert by["mospi_cpi"].ok and by["mospi_wpi"].ok and by["mospi_cpi"].rows == 3
        assert by["mospi_iip"].error_kind == "STALE" and by["mospi_rbi_reserves"].error_kind in ("EMPTY", "STALE")      # honest, not hidden
        assert s.query("SELECT COUNT(*) AS n FROM macro_series WHERE series='mospi:wpi_index'")[0]["n"] == 1


def test_india_macro_tool(st, monkeypatch):
    from drishti import data_tools as dt
    assert dt.india_macro(st).error.kind == ErrorKind.EMPTY
    fake(monkeypatch, {2026: CPI})
    ds.persist_macro_bundle(st, mospi.cpi(today=TODAY).data)
    r = dt.india_macro(st, as_of="2026-11-08")
    assert r.ok and r.data["cpi_yoy"] == {"value": 5.54, "unit": "%", "reference_month": "2026-09", "age_days": 39, "stale": False}
    assert r.data["iip_yoy"]["value"] is None
    assert dt.india_macro(st, as_of="2026-10-05").error.kind == ErrorKind.EMPTY           # before the September release
    assert dt.india_macro(st, as_of="2027-02-01").data["cpi_yoy"]["stale"] is True        # old but shown as old


def test_mcp_exposes_india_macro_tool():
    pytest.importorskip("mcp")
    import asyncio

    from drishti import mcp_server as m

    class _K:
        def __init__(self, s):
            self.s = s

        def __enter__(self):
            return self.s

        def __exit__(self, *a):
            return False
    s = Store()
    try:
        assert "get_india_macro" in [t.name for t in asyncio.run(m.build_server(lambda: _K(s)).list_tools())]
    finally:
        s.close()

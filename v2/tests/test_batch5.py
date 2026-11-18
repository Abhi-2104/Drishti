"""Batch 5: NSE delivery %, the full BSE scrip list, and its merge into the security master. Real captures drive the happy path."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from drishti import jobs
from drishti.core.envelope import DrishtiError, ErrorKind
from drishti.jobs import backfill
from drishti.providers import bse, nse_archive
from drishti.providers import security_master as sm
from drishti.store import Store
from drishti.store import datasets as ds

FIX = Path(__file__).parent / "fixtures"
DELIV = (FIX / "nse_archive" / "sec_bhavdata_full_06112026.csv").read_bytes()
SCRIPS = (FIX / "bse" / "bse_scrip_list_20261108.json").read_bytes()
HEAD = "SYMBOL, SERIES, DATE1, PREV_CLOSE, OPEN_PRICE, HIGH_PRICE, LOW_PRICE, LAST_PRICE, CLOSE_PRICE, AVG_PRICE, TTL_TRD_QNTY, TURNOVER_LACS, NO_OF_TRADES, DELIV_QTY, DELIV_PER"


def kind(fn, *a, **k) -> ErrorKind:
    with pytest.raises(DrishtiError) as ei:
        fn(*a, **k)
    return ei.value.kind


@pytest.fixture
def st():
    s = Store()
    yield s
    s.close()


def line(sym="AAA", series="EQ", day="06-Nov-2026", ttl=1000, trades=50, dq="400", dp="40.00"):
    return f"{sym}, {series}, {day}, 10, 10, 11, 9, 10, 10, 10, {ttl}, 1, {trades}, {dq}, {dp}"


def csv(*lines, head=HEAD):
    return ("\n".join([head, *lines]) + "\n").encode()


# --- delivery: the real file ------------------------------------------------------------------------------------
def test_delivery_real_file():
    rows = nse_archive.parse_delivery(DELIV)
    assert len(rows) == 3559 and {r["trade_date"] for r in rows} == {"2026-11-06"}
    rel = [r for r in rows if r["symbol"] == "RELIANCE"]
    assert rel == [{"trade_date": "2026-11-06", "symbol": "RELIANCE", "series": "EQ", "ttl_qty": 22768301, "deliv_qty": 15216640,
                    "deliv_per": 66.83, "trades": 327475}]
    na = [r for r in rows if r["deliv_per"] is None]
    assert na and {r["series"] for r in na} <= {"BE", "BZ", "SM", "ST", "IL", "W3"} and all(r["deliv_qty"] is None for r in na)   # '-' is "not applicable", kept
    assert all(r["deliv_qty"] <= r["ttl_qty"] for r in rows if r["deliv_qty"] is not None)


def test_delivery_small_file_and_not_applicable_rows():
    rows = nse_archive.parse_delivery(csv(line("AAA"), line("BBB", "BE", dq="-", dp="-")), min_rows=1)
    assert [(r["symbol"], r["deliv_per"], r["deliv_qty"]) for r in rows] == [("AAA", 40.0, 400), ("BBB", None, None)]


# --- delivery: corruption is typed ---------------------------------------------------------------------------------
@pytest.mark.parametrize("body,why", [
    (csv(line("AAA"), line("AAA")), "duplicate (symbol, series)"),
    (csv(line("AAA", dq="2000", dp="200.00")), "impossible percentage"),
    (csv(line("AAA", dq="2000", dp="40.00")), "delivered more than traded"),
    (csv(line("AAA", dq="400", dp="-")), "quantity without percentage"),
    (csv(line("AAA"), line("BBB", day="05-Nov-2026")), "mixed dates"),
    (csv(line("AAA"), head=HEAD.replace("DELIV_PER", "DELIVERY")), "column renamed"),
    (b"<html><body>Access denied</body></html>", "HTML instead of a file"),
    (csv(line("AAA", dp="x")), "non-numeric"),
])
def test_delivery_corruption(body, why):
    assert kind(nse_archive.parse_delivery, body, min_rows=1) in (ErrorKind.PARSE_FAIL, ErrorKind.EMPTY, ErrorKind.BLOCKED_HTML), why


def test_delivery_truncated_file_is_refused():
    assert kind(nse_archive.parse_delivery, csv(line("AAA"))) in (ErrorKind.PARSE_FAIL, ErrorKind.EMPTY)    # fewer rows than a real session


def test_delivery_missing_for_most_equity_rows_is_refused():
    lines = [line(f"S{i}", dq="-", dp="-") for i in range(20)] + [line("OK")]
    assert kind(nse_archive.parse_delivery, csv(*lines), min_rows=1) == ErrorKind.PARSE_FAIL


def test_delivery_provider_checks_the_file_date(monkeypatch):
    from drishti.core.fetch import Fetched
    monkeypatch.setattr(nse_archive, "get", lambda url, profile, expect="text", **k: Fetched(url, csv(line("AAA")), 200))
    monkeypatch.setattr(nse_archive, "parse_delivery", _parse_small)
    assert nse_archive.delivery(date(2026, 11, 6)).ok
    assert nse_archive.delivery(date(2026, 11, 9)).error.kind == ErrorKind.STALE                 # the file for the 9th holds the 6th


_real_parse = nse_archive.parse_delivery


def _parse_small(body):
    return _real_parse(body, min_rows=1)


def test_delivery_url_uses_the_dmy_stamp():
    assert nse_archive.DELIVERY_URL.format(dmy="06112026").endswith("/sec_bhavdata_full_06112026.csv")


# --- delivery: persistence ----------------------------------------------------------------------------------------
def price(sym, isin, series="EQ", day="2026-11-06"):
    return {"trade_date": day, "isin": isin, "symbol": sym, "series": series, "open": 10, "high": 11, "low": 9, "close": 10,
            "prev_close": 10, "volume": 1000, "turnover": 10000, "trades": 5}


def drow(sym, series="EQ", dp=40.0, day="2026-11-06"):
    return {"trade_date": day, "symbol": sym, "series": series, "ttl_qty": 1000, "deliv_qty": None if dp is None else int(dp * 10),
            "deliv_per": dp, "trades": 5}


def test_persist_delivery_resolves_isin_from_the_same_session(st):
    ds.persist_prices(st, [price("AAA", "INE000A01010"), price("BBB", "INE000B01018", series="BE")], "NSE")
    assert ds.persist_delivery(st, [drow("AAA"), drow("BBB", "BE", None)]) == 2
    got = {(r["symbol"], r["series"]): r for r in st.query("SELECT * FROM delivery")}
    assert got[("AAA", "EQ")]["isin"] == "INE000A01010" and got[("BBB", "BE")]["isin"] == "INE000B01018"
    assert got[("BBB", "BE")]["deliv_per"] is None
    assert ds.unresolved_delivery(st, "2026-11-06") == 0


def test_persist_delivery_falls_back_to_the_security_master_and_never_drops(st):
    st.upsert_security("INE000C01016", name="C", nse_symbol="CCC", source="t")
    st.upsert_security("INE000D01014", name="D1", nse_symbol="DDD", source="t")
    st.upsert_security("INE000D01022", name="D2", nse_symbol="DDD", source="t")          # same symbol twice: ambiguous
    assert ds.persist_delivery(st, [drow("CCC"), drow("DDD"), drow("ZZZ")]) == 3
    got = {r["symbol"]: r["isin"] for r in st.query("SELECT symbol, isin FROM delivery")}
    assert got == {"CCC": "INE000C01016", "DDD": None, "ZZZ": None}                        # unknown or ambiguous stays NULL, row kept
    assert ds.unresolved_delivery(st, "2026-11-06") == 2


def test_persist_delivery_is_idempotent_and_replaces_a_revised_file(st):
    ds.persist_prices(st, [price("AAA", "INE000A01010")], "NSE")
    ds.persist_delivery(st, [drow("AAA", dp=40.0)])
    ds.persist_delivery(st, [drow("AAA", dp=55.0)])
    rows = st.query("SELECT deliv_per FROM delivery")
    assert [r["deliv_per"] for r in rows] == [55.0]
    assert ds.persist_delivery(st, []) == 0


def test_persist_the_real_file_end_to_end(st):
    rows = nse_archive.parse_delivery(DELIV)
    assert ds.persist_delivery(st, rows) == len(rows)
    assert st.query("SELECT COUNT(*) AS n FROM delivery")[0]["n"] == 3559
    assert ds.unresolved_delivery(st, "2026-11-06") > 0          # no prices or master loaded: honestly unresolved, not guessed


# --- delivery: wiring ------------------------------------------------------------------------------------------------
def test_delivery_is_part_of_the_evening_flows_job_and_the_backfill():
    assert "delivery" in [s.dataset for s in jobs.JOBS["eod_flows"].steps]
    assert "delivery" in backfill.TARGETS


# --- BSE scrip list ------------------------------------------------------------------------------------------------------
def test_scrip_list_real_file():
    rows = bse.parse_scrip_list(SCRIPS)
    assert len(rows) == 5146 and len({r["scrip_code"] for r in rows}) == 5146
    assert all(r["scrip_code"].isdigit() for r in rows)
    assert sum(1 for r in rows if r["isin"] and r["isin"].startswith("INE")) > 4000


def scrip(code="500001", isin="INE467D01017", sym="ABC", group="A", name="Abc Ltd", status="Active"):
    return {"SCRIP_CD": code, "Scrip_Name": name, "Status": status, "GROUP": group, "ISIN_NUMBER": isin, "scrip_id": sym}


def js(rows):
    return json.dumps(rows).encode()


def test_scrip_list_small_ok_and_corruption():
    assert bse.parse_scrip_list(js([scrip()]), min_rows=1)[0]["symbol"] == "ABC"
    assert kind(bse.parse_scrip_list, js([scrip()])) in (ErrorKind.PARSE_FAIL, ErrorKind.EMPTY)                  # fewer rows than a real list
    assert kind(bse.parse_scrip_list, b"<html>x</html>", min_rows=1) == ErrorKind.PARSE_FAIL
    assert kind(bse.parse_scrip_list, js({"Table": []}), min_rows=1) == ErrorKind.PARSE_FAIL
    assert kind(bse.parse_scrip_list, js([scrip(), scrip()]), min_rows=1) == ErrorKind.PARSE_FAIL                  # same code twice
    assert kind(bse.parse_scrip_list, js([scrip(code="ABC")]), min_rows=1) == ErrorKind.PARSE_FAIL
    bad = scrip()
    del bad["ISIN_NUMBER"]
    assert kind(bse.parse_scrip_list, js([bad]), min_rows=1) == ErrorKind.PARSE_FAIL


# --- security master with the scrip list -------------------------------------------------------------------------------------
def eq_row(sym, isin, name="X"):
    return {"nse_symbol": sym, "name": name, "nse_series": "EQ", "isin": isin, "listed_on": "2000-01-01", "face_value": 1.0}


def srow(code, isin, sym, group="A", name="X"):
    return {"scrip_code": code, "isin": isin, "symbol": sym, "group": group, "name": name}


def test_scrip_list_fills_scrips_the_bhavcopy_did_not_show():
    eq = [eq_row("VIRAT", "INE467D01017")]
    m = sm.merge_master(eq, [], (), bse_scrips=[srow("500001", "INE467D01017", "BRAHMVIRAT", "B")])
    (s,) = m["securities"]
    assert (s["nse_symbol"], s["bse_scrip"], s["bse_symbol"], s["bse_series"], s["listing"]) == ("VIRAT", "500001", "BRAHMVIRAT", "B", "both")


def test_scrip_list_adds_bse_only_equities_and_skips_debt():
    m = sm.merge_master([], [], (), bse_scrips=[srow("500012", "INE714B01016", "ANDHRAPET"), srow("939999", "INE532F07FO0", "NCD1", "F")])
    (s,) = m["securities"]
    assert (s["isin"], s["listing"], s["nse_symbol"]) == ("INE714B01016", "bse", None) and m["stats"]["skipped_from_bse"] == {"debt": 1}


def test_bhavcopy_wins_over_the_scrip_list_and_conflicts_are_reported():
    eq = [eq_row("VIRAT", "INE467D01017")]
    bh = [{"symbol": "BRAHMVIRAT", "scrip_code": "500001", "isin": "INE467D01017", "series": "A", "name": "x"}]
    m = sm.merge_master(eq, bh, (), bse_scrips=[srow("500001", "INE467D01017", "OTHER", "B"), srow("500002", "INE467D01017", "TWIN")])
    (s,) = m["securities"]
    assert (s["bse_scrip"], s["bse_symbol"], s["bse_series"]) == ("500001", "BRAHMVIRAT", "A")                 # the day's file is not overwritten
    assert m["conflicts"] == [{"isin": "INE467D01017", "kept": "500001", "ignored": "500002"}]


def test_scrip_list_rejects_bad_isins_visibly():
    m = sm.merge_master([], [], (), bse_scrips=[srow("500003", "INE467D01018", "BAD")])
    assert m["securities"] == [] and m["rejected"] == [{"source": "bse_scrips", "symbol": "BAD", "scrip": "500003", "isin": "INE467D01018"}]


def test_real_scrip_list_merges_without_conflict_or_rejects_beyond_the_known_two():
    m = sm.merge_master([], [], (), bse_scrips=bse.parse_scrip_list(SCRIPS))
    st = m["stats"]
    assert st["securities"] > 4000 and st["conflicts"] == 0 and st["rejected"] == 2 and m["stats"]["by_listing"] == {"bse": st["securities"]}


# --- the delivery tool ---------------------------------------------------------------------------------------------------
from drishti import data_tools as dt   # noqa: E402
from drishti.core.canary import today_ist   # noqa: E402


def recent(n):
    from datetime import timedelta
    return [(today_ist() - timedelta(days=i)).isoformat() for i in range(n)]


def loaded(st, *, isin="INE000A01010", days=3, dp0=50.0):
    st.upsert_security(isin, name="Aaa Ltd", nse_symbol="AAA", source="t")
    for i, d in enumerate(recent(days)):
        ds.persist_prices(st, [price("AAA", isin, day=d)], "NSE")
        ds.persist_delivery(st, [drow("AAA", dp=dp0 - i, day=d)])


def test_delivery_tool_latest_and_mean(st):
    loaded(st, days=3)
    r = dt.delivery(st, "AAA")
    assert r.ok and r.data["latest"]["deliv_per"] == 50.0 and r.data["mean_20_sessions"] == 49.0 and r.data["sessions_in_mean"] == 3
    assert [x["deliv_per"] for x in r.data["rows"]] == [50.0, 49.0, 48.0] and r.as_of == recent(1)[0]


def test_delivery_tool_follows_a_split_that_changed_the_isin(st):
    loaded(st, days=2)
    old = recent(4)[3]
    ds.persist_prices(st, [price("AAA", "INE000A01028", day=old)], "NSE")     # same symbol, other ISIN, earlier day
    ds.persist_delivery(st, [{**drow("AAA", dp=30.0, day=old)}])
    assert len(dt.delivery(st, "AAA").data["rows"]) == 3                       # symbol keeps the series together across the ISIN change


def test_delivery_tool_typed_failures(st):
    st.upsert_security("INE000B01018", name="Bse Only", bse_scrip="500001", bse_symbol="BONLY", source="t")
    assert dt.delivery(st, "BONLY").error.kind == ErrorKind.NOT_FOUND          # NSE only
    st.upsert_security("INE000A01010", name="Aaa Ltd", nse_symbol="AAA", source="t")
    r = dt.delivery(st, "AAA")
    assert r.error.kind == ErrorKind.EMPTY and "backfill delivery" in r.error.detail     # nothing stored yet: said, not blank
    assert dt.delivery(st, "NOSUCHNAME").error.kind in (ErrorKind.NOT_FOUND, ErrorKind.EMPTY)


def test_delivery_tool_old_rows_fall_outside_the_window(st):
    loaded(st, days=3)
    assert len(dt.delivery(st, "AAA", days=1).data["rows"]) <= 2


def test_mcp_exposes_get_delivery():
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
        srv = m.build_server(lambda: _K(s))
        names = [t.name for t in asyncio.run(srv.list_tools())]
        assert "get_delivery" in names
    finally:
        s.close()


# --- delivery: a few unusable rows are dropped, many are refused ---------------------------------------------------
def test_delivery_drops_one_impossible_row_but_keeps_the_rest():
    good = [line(f"S{i}") for i in range(30)]
    rows = nse_archive.parse_delivery(csv(*good, line("BAD", "ST", ttl=2561000, dq="2566000", dp="99.92")), min_rows=1)   # seen: WTICAB/ST, 19 Feb 2024
    assert len(rows) == 30 and "BAD" not in {r["symbol"] for r in rows}


def test_delivery_refuses_a_file_with_many_unusable_rows():
    good = [line(f"S{i}") for i in range(30)]
    bad = [line(f"B{i}", dq="2000", dp="200.00") for i in range(nse_archive.MAX_BAD_DELIVERY_ROWS + 1)]
    assert kind(nse_archive.parse_delivery, csv(*good, *bad), min_rows=1) == ErrorKind.PARSE_FAIL


def test_delivery_record_cut_by_a_lone_carriage_return():
    good = [line(f"S{i}") for i in range(30)]
    body = ("\n".join([HEAD, *good[:10], "SPLIT, EQ, 06-Nov-2026, 10, 10, 11\r9, 10, 10, 10, 1000, 1, 50, 400, 40.00", *good[10:]]) + "\n").encode()
    rows = nse_archive.parse_delivery(body, min_rows=1)       # both halves of the cut record are dropped, nothing else is lost
    assert len(rows) == 30

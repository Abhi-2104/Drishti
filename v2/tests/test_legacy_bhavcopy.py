"""Pre-2024 daily price files: parsers on real rows (15 Sep 2021 and 15 Jun 2023), corruptions, and the backfill fallback."""
from __future__ import annotations

import io
import zipfile
from datetime import date

import pytest

from drishti.core.envelope import DrishtiError, ErrorKind, Result
from drishti.jobs import backfill as bf
from drishti.providers import bse as b
from drishti.providers import nse_archive as n
from drishti.store import Store

NSE_HEAD = "SYMBOL,SERIES,OPEN,HIGH,LOW,CLOSE,LAST,PREVCLOSE,TOTTRDQTY,TOTTRDVAL,TIMESTAMP,TOTALTRADES,ISIN,\n"
NSE_ROWS = ("20MICRONS,EQ,54.8,64.8,54.65,64.8,64.8,54,967425,59809069.55,15-SEP-2021,7197,INE144J01027,\n"
            "3MINDIA,EQ,25999.9,25999.9,25259.5,25337.85,25320,25705.25,2531,64411524.15,15-SEP-2021,1785,INE470A01017,\n"
            "RELIANCE,EQ,2368.5,2395.75,2368.5,2378.3,2379.4,2368.45,4186300,9965045382.75,15-SEP-2021,168130,INE002A01018,\n")
BSE_HEAD = "SC_CODE,SC_NAME,SC_GROUP,SC_TYPE,OPEN,HIGH,LOW,CLOSE,LAST,PREVCLOSE,NO_TRADES,NO_OF_SHRS,NET_TURNOV,TDCLOINDI,ISIN_CODE,TRADING_DATE,FILLER2,FILLER3\n"
BSE_ROWS = ("500002,ABB LTD.    ,A ,Q,1915.00,1932.00,1894.00,1903.45,1894.00,1906.65,929,6279,12021056.00,,INE117A01022,15-Sep-21,,\n"
            "500325,RELIANCE    ,A ,Q,2372.00,2395.25,2368.85,2378.95,2378.95,2368.30,6054,76028,181086413.00,,INE002A01018,15-Sep-21,,\n"
            "532712,REL COM LTD ,B ,Q,3.15,3.26,3.11,3.26,3.26,3.11,6380,9392863,30196311.00,,INE330H01018,15-Sep-21,,\n")


def zipped(text: str, name="x.csv") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(name, text)
    return buf.getvalue()


def test_nse_legacy_rows_match_the_new_file_shape():
    rows = n.parse_cm_bhavcopy_legacy(zipped(NSE_HEAD + NSE_ROWS), min_rows=1)
    rel = next(r for r in rows if r["symbol"] == "RELIANCE")
    assert rel["isin"] == "INE002A01018" and rel["trade_date"] == "2021-09-15" and rel["close"] == 2378.3 and rel["prev_close"] == 2368.45
    assert rel["volume"] == 4186300 and rel["trades"] == 168130 and rel["series"] == "EQ"
    assert set(rel) >= {"trade_date", "symbol", "series", "isin", "open", "high", "low", "close", "prev_close", "volume", "turnover", "trades"}


def test_nse_legacy_refuses_changed_layout_truncation_and_shifted_columns():
    with pytest.raises(DrishtiError) as e:
        n.parse_cm_bhavcopy_legacy(zipped(NSE_HEAD.replace("PREVCLOSE", "PRV") + NSE_ROWS), min_rows=1)
    assert e.value.kind == ErrorKind.PARSE_FAIL
    with pytest.raises(DrishtiError) as e:
        n.parse_cm_bhavcopy_legacy(zipped(NSE_HEAD + NSE_ROWS))          # 3 rows against the default minimum
    assert e.value.kind == ErrorKind.PARSE_FAIL
    shifted = NSE_HEAD + NSE_ROWS.replace("54.8,64.8,54.65,64.8", "64.8,54.8,64.8,54.65")   # high below low: columns drifted
    shifted = shifted * 1
    bad = NSE_HEAD + "".join(NSE_ROWS.replace("RELIANCE", f"S{i}").replace("20MICRONS", f"A{i}").replace("3MINDIA", f"B{i}").replace("54.8,64.8,54.65,64.8", "64.8,54.8,64.8,54.65") for i in range(40))
    with pytest.raises(DrishtiError) as e:
        n.parse_cm_bhavcopy_legacy(zipped(bad), min_rows=1)
    assert e.value.kind == ErrorKind.PARSE_FAIL
    with pytest.raises(DrishtiError) as e:
        n.parse_cm_bhavcopy_legacy(b"<html>blocked</html>", min_rows=1)
    assert e.value.kind == ErrorKind.BLOCKED_HTML


def test_bse_legacy_rows_have_isin_scrip_code_and_no_symbol():
    rows = b.parse_bhavcopy_legacy(zipped(BSE_HEAD + BSE_ROWS), min_rows=1)
    rel = next(r for r in rows if r["scrip_code"] == "500325")
    assert rel["isin"] == "INE002A01018" and rel["trade_date"] == "2021-09-15" and rel["symbol"] is None and rel["series"] == "A"
    assert rel["close"] == 2378.95 and rel["volume"] == 76028 and rel["trades"] == 6054
    dup = BSE_HEAD + BSE_ROWS + BSE_ROWS.splitlines()[0] + "\n"
    with pytest.raises(DrishtiError) as e:
        b.parse_bhavcopy_legacy(zipped(dup), min_rows=1)
    assert e.value.kind == ErrorKind.PARSE_FAIL


def test_price_backfill_falls_back_to_legacy_files_for_old_dates(monkeypatch):
    st = Store()
    old, new = date(2023, 6, 15), date(2026, 11, 6)

    def row(d, isin="INE002A01018"):
        return {"trade_date": d.isoformat(), "symbol": "RELIANCE", "series": "EQ", "isin": isin, "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5,
                "prev_close": 1.4, "volume": 10, "turnover": 1.0, "trades": 1}
    used = []

    def nse_new(d, store=None):
        used.append(("nse_new", d))
        return Result(data=[row(d)], as_of=d.isoformat()) if d == new else Result(error=DrishtiError(ErrorKind.NOT_FOUND, "404"))

    def nse_old(d, store=None):
        used.append(("nse_old", d))
        return Result(data=[row(d)], as_of=d.isoformat()) if d == old else Result(error=DrishtiError(ErrorKind.NOT_FOUND, "404"))
    monkeypatch.setattr(bf.nse_archive, "cm_bhavcopy", nse_new)
    monkeypatch.setattr(bf.nse_archive, "cm_bhavcopy_legacy", nse_old)
    monkeypatch.setattr(bf.bse, "bhavcopy", lambda d, store=None: Result(data=[], as_of=d.isoformat()))
    monkeypatch.setattr(bf.bse, "bhavcopy_legacy", lambda d, store=None, isin_map=None: Result(data=[], as_of=d.isoformat()))
    r_new = bf.backfill_prices(st, days=1, start=new)
    assert r_new.done == 1 and ("nse_old", new) not in used          # the new file existed: the old reader is not touched
    r_old = bf.backfill_prices(st, days=1, start=old)
    assert r_old.done == 1 and ("nse_old", old) in used and ("nse_new", old) not in used     # before 2024 the new file is not even tried
    assert {x["trade_date"] for x in st.query("SELECT trade_date FROM price_eod")} == {old.isoformat(), new.isoformat()}
    r_hol = bf.backfill_prices(st, days=1, start=date(2023, 6, 14))   # missing in both: a holiday, not a failure
    assert r_hol.holidays == 1 and not r_hol.failed


def test_bse_legacy_name_with_a_comma_does_not_shift_the_columns():
    line = "500999,TIAA NC, INDIA ,A ,Q,10.00,11.00,9.50,10.50,10.50,10.00,5,100,1050.00,,INE000A01011,15-Sep-21,,\n"
    rows = b.parse_bhavcopy_legacy(zipped(BSE_HEAD + BSE_ROWS + line), min_rows=1)
    r = next(x for x in rows if x["scrip_code"] == "500999")
    assert r["isin"] == "INE000A01011" and r["close"] == 10.5 and r["volume"] == 100 and "TIAA" in r["name"]


# --- the two older BSE layouts (real rows from Jan 2017 and Jan 2016) --------------------------------------------
BSE_2017_HEAD = "SC_CODE,SC_NAME,SC_GROUP,SC_TYPE,OPEN,HIGH,LOW,CLOSE,LAST,PREVCLOSE,NO_TRADES,NO_OF_SHRS,NET_TURNOV,TDCLOINDI,ISIN_CODE,FILLER1,FILLER2,FILLER3\n"
BSE_2017_ROWS = ("500002,ABB LTD.    ,A ,Q,1125.00,1139.95,1118.05,1124.45,1124.45,1130.85,1147,6274,7074418.00,,INE117A01022,,,\n"
                 "500003,AEGIS LOGIS ,B ,Q,140.70,140.70,134.60,135.65,135.65,138.80,744,28961,3941843.00,,INE208C01025,,,\n")
BSE_2016_HEAD = "SC_CODE,SC_NAME,SC_GROUP,SC_TYPE,OPEN,HIGH,LOW,CLOSE,LAST,PREVCLOSE,NO_TRADES,NO_OF_SHRS,NET_TURNOV,TDCLOINDI\n"
BSE_2016_ROWS = ("500002,ABB LTD.    ,A ,Q,1015.00,1035.00,1002.05,1015.05,1015.05,1013.10,531,4473,4562659.00,\n"
                 "500003,AEGIS LOGIS ,B ,Q,97.75,100.00,89.00,90.05,90.05,94.95,775,48029,4596545.00,XD\n")


def test_bse_2017_layout_without_a_date_column_takes_the_requested_date():
    rows = b.parse_bhavcopy_legacy(zipped(BSE_2017_HEAD + BSE_2017_ROWS), day=date(2017, 1, 16), min_rows=1)
    abb = next(r for r in rows if r["scrip_code"] == "500002")
    assert abb["trade_date"] == "2017-01-16" and abb["isin"] == "INE117A01022" and abb["close"] == 1124.45


def test_bse_2016_layout_has_no_isin_and_needs_a_date():
    rows = b.parse_bhavcopy_legacy(zipped(BSE_2016_HEAD + BSE_2016_ROWS), day=date(2016, 1, 15), min_rows=1)
    assert all(r["isin"] is None for r in rows) and rows[0]["trade_date"] == "2016-01-15" and rows[1]["close"] == 90.05
    with pytest.raises(DrishtiError) as e:
        b.parse_bhavcopy_legacy(zipped(BSE_2016_HEAD + BSE_2016_ROWS), min_rows=1)
    assert e.value.kind == ErrorKind.PARSE_FAIL


class _Got:
    def __init__(self, body):
        self.body = body

    def source(self, name):
        from drishti.core.envelope import Source
        return Source(name=name, url="x", fetched_at="t")


def test_bse_legacy_falls_back_to_the_old_name_and_maps_scrip_codes(monkeypatch):
    calls = []

    def fake_get(url, profile, expect="text"):
        calls.append(url)
        if "ISINCODE" in url:
            raise DrishtiError(ErrorKind.BLOCKED_HTML, "HTML where zip expected")
        return _Got(zipped(BSE_2016_HEAD + BSE_2016_ROWS))
    monkeypatch.setattr(b, "get", fake_get)
    monkeypatch.setattr(b, "LEGACY_MIN_ROWS", 1)
    r = b.bhavcopy_legacy(date(2016, 1, 15), isin_map={"500002": "INE117A01022"})
    assert r.ok and len(calls) == 2 and "EQ150116_CSV.ZIP" in calls[1]
    by = {x["scrip_code"]: x["isin"] for x in r.data}
    assert by == {"500002": "INE117A01022", "500003": None}            # unknown code: left without an ISIN, never guessed


def test_bse_web_page_for_both_names_is_not_found_not_a_block(monkeypatch):
    def fake_get(url, profile, expect="text"):
        raise DrishtiError(ErrorKind.BLOCKED_HTML, "HTML where zip expected")
    monkeypatch.setattr(b, "get", fake_get)
    r = b.bhavcopy_legacy(date(2016, 8, 15))
    assert not r.ok and r.error.kind == ErrorKind.NOT_FOUND and "web page" in r.error.detail


def test_bse_a_real_block_is_still_a_block(monkeypatch):
    def fake_get(url, profile, expect="text"):
        raise DrishtiError(ErrorKind.BLOCKED, "HTTP 403")
    monkeypatch.setattr(b, "get", fake_get)
    r = b.bhavcopy_legacy(date(2016, 1, 15))
    assert not r.ok and r.error.kind == ErrorKind.BLOCKED


def test_scrip_map_prefers_the_earliest_isin_and_bse_prices_fills_only_missing_days(monkeypatch):
    from drishti.store import datasets as ds
    st = Store()
    ds.record_bse_scrips(st, [{"scrip_code": "500002", "isin": "INE_NEW", "trade_date": "2020-01-01"},
                              {"scrip_code": "500002", "isin": "INE_OLD", "trade_date": "2017-01-02"}])
    assert ds.bse_isin_map(st)["500002"] == "INE_OLD"

    def bar(d, isin, ex_series="EQ"):
        return {"trade_date": d, "isin": isin, "series": ex_series, "symbol": "X", "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0,
                "prev_close": 1.0, "volume": 1, "turnover": 1.0, "trades": 1}
    ds.persist_prices(st, [bar("2016-01-14", "INE_A")], "NSE")                 # NSE only: BSE missing
    ds.persist_prices(st, [bar("2016-01-15", "INE_A")], "NSE")
    ds.persist_prices(st, [bar("2016-01-15", "INE_B", "B")], "BSE")             # already has BSE: left alone
    asked = []

    def fake_legacy(d, store=None, isin_map=None):
        asked.append(d.isoformat())
        rows = [{**bar(d.isoformat(), None, "B"), "scrip_code": "500002"}, {**bar(d.isoformat(), None, "B"), "scrip_code": "599999"}]
        for r in rows:
            r["isin"] = (isin_map or {}).get(r["scrip_code"])
        return Result(data=rows, as_of=d.isoformat())
    monkeypatch.setattr(bf.bse, "bhavcopy_legacy", fake_legacy)
    rep = bf.backfill_bse_prices(st, days=4000, start=date(2016, 1, 20))
    assert asked == ["2016-01-14"] and rep.done == 1 and rep.rows == 1
    got = st.query("SELECT isin FROM price_eod WHERE trade_date='2016-01-14' AND exchange='BSE'")
    assert [x["isin"] for x in got] == ["INE_OLD"]
    again = bf.backfill_bse_prices(st, days=4000, start=date(2016, 1, 20))
    assert again.done == 0                                                     # filled days no longer qualify


def test_bse_spliced_records_are_dropped_not_misread():
    # real lines from 15 Jul 2022: two records joined on one line, and the tail of another on the next line
    spliced = ("531780,KACL        ,XT,Q,107.85,107.85,107.85,107.85,107.85,113.50,56,2154,2541269,CHEMFABALKA ,B ,Q,170.00,170.00,166.20,"
               "166.70,166.30,169.20,34,303,50542.00,,INE783X01023,15-Jul-22,,\n")
    tail = "9.09,8.82,9.00,9.00,8.94,54,6197,55583.00,,INE204N01013,15-Jul-22,,\n"
    head = BSE_HEAD.replace("15-Sep-21", "15-Jul-22")
    good = BSE_ROWS.replace("15-Sep-21", "15-Jul-22")
    rows = b.parse_bhavcopy_legacy(zipped(head + good + spliced + tail), min_rows=1)
    assert {r["scrip_code"] for r in rows} == {"500002", "500325", "532712"}        # the spliced pair and the fragment are gone
    assert not any(r["isin"] in ("INE783X01023", "INE204N01013") for r in rows)


def test_bse_many_broken_lines_is_a_layout_change():
    bad = "".join(f"9.0{i},1,2\n" for i in range(b.MAX_BROKEN_LINES + 1))
    with pytest.raises(DrishtiError) as e:
        b.parse_bhavcopy_legacy(zipped(BSE_HEAD + BSE_ROWS + bad), min_rows=1)
    assert e.value.kind == ErrorKind.PARSE_FAIL and "wrong number of fields" in e.value.detail


def test_previous_day_file_on_a_holiday_counts_as_a_holiday_not_a_failure():
    from drishti.core.envelope import DrishtiError as E
    ok = bf._served_previous_day(E(ErrorKind.STALE, "delivery: file for 2026-10-02 contains data for 2026-10-01"), date(2026, 10, 2))
    later = bf._served_previous_day(E(ErrorKind.STALE, "x: file for 2023-04-11 contains data for 2023-11-04"), date(2023, 4, 11))
    old = bf._served_previous_day(E(ErrorKind.STALE, "x: file for 2026-10-20 contains data for 2026-09-01"), date(2026, 10, 20))
    assert ok and not later and not old


def test_index_file_with_day_and_month_swapped_is_read_as_the_requested_day():
    from drishti.providers import nse_archive as na
    assert na._swapped("2023-11-04", date(2023, 4, 11)) and not na._swapped("2023-04-04", date(2023, 4, 4))
    assert not na._swapped("2023-11-05", date(2023, 4, 11))

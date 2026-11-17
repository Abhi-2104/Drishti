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
    monkeypatch.setattr(bf.bse, "bhavcopy_legacy", lambda d, store=None: Result(data=[], as_of=d.isoformat()))
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

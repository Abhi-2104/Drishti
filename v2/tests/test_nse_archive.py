"""NSE archive providers: parsers on saved real files (06 Nov 2026), corruptions, fetch + store flow."""
from __future__ import annotations

import io
import zipfile
from datetime import date
from functools import partial
from pathlib import Path

import pytest

from drishti.core import canary
from drishti.core.envelope import DrishtiError, ErrorKind, Result
from drishti.core.fetch import Fetched
from drishti.core.isin import is_valid_isin
from drishti.providers import _util as u
from drishti.providers import nse_archive as n
from drishti.store import Store

FIX = Path(__file__).parent / "fixtures" / "nse_archive"
D = date(2026, 11, 6)


def raw(name: str) -> bytes:
    return (FIX / name).read_bytes()


def zip_text(body: bytes) -> tuple[str, str]:
    with zipfile.ZipFile(io.BytesIO(body)) as z:
        return z.namelist()[0], z.read(z.namelist()[0]).decode("utf-8")


def make_zip(text: str, member: str = "x.csv") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(member, text)
    return buf.getvalue()


def edit_zip(body: bytes, fn) -> bytes:
    member, text = zip_text(body)
    return make_zip(fn(text), member)


def kind(fn, *a, **k) -> ErrorKind:
    with pytest.raises(DrishtiError) as ei:
        fn(*a, **k)
    return ei.value.kind


CM = partial(n.parse_cm_bhavcopy, min_rows=1)
FO = partial(n.parse_fo_bhavcopy, min_rows=1)


@pytest.fixture
def cm_zip():
    return raw("BhavCopy_NSE_CM_20261106.csv.zip")


@pytest.fixture
def fo_zip():
    return raw("BhavCopy_NSE_FO_20261106.csv.zip")


# --- CM bhavcopy ----------------------------------------------------------------------------------
def test_cm_parses_real_file(cm_zip):
    rows = CM(cm_zip)
    assert len(rows) == 181 and {r["trade_date"] for r in rows} == {"2026-11-06"}
    ril = next(r for r in rows if r["symbol"] == "RELIANCE" and r["series"] == "EQ")
    assert (ril["isin"], ril["open"], ril["high"], ril["low"], ril["close"], ril["prev_close"]) == \
           ("INE002A01018", 1295.0, 1320.0, 1295.0, 1320.0, 1313.4)
    assert ril["volume"] == 22768301 and ril["trades"] == 327475 and ril["turnover"] == 29865210015.3
    assert {"EQ", "BE", "SM", "GB"} <= {r["series"] for r in rows}  # not just equities
    assert all(is_valid_isin(r["isin"]) or r["isin"] is None for r in rows)  # every ISIN in the real file has a good check digit


def test_cm_default_minimum_rejects_a_truncated_file(cm_zip):
    assert kind(n.parse_cm_bhavcopy, cm_zip) == ErrorKind.PARSE_FAIL


def test_cm_block_page_where_zip_expected():
    assert kind(CM, b"<html><head><title>Access Denied</title></head></html>") == ErrorKind.BLOCKED_HTML


def test_cm_garbage_and_wrong_zip_shapes(cm_zip):
    assert kind(CM, b"not a zip at all") == ErrorKind.PARSE_FAIL
    two = io.BytesIO()
    with zipfile.ZipFile(two, "w") as z:
        z.writestr("a.csv", "x"), z.writestr("b.csv", "y")
    assert kind(CM, two.getvalue()) == ErrorKind.PARSE_FAIL


def test_cm_header_only_is_empty(cm_zip):
    assert kind(CM, edit_zip(cm_zip, lambda t: t.splitlines()[0] + "\n")) == ErrorKind.EMPTY


def test_cm_missing_column_is_parse_fail(cm_zip):
    assert kind(CM, edit_zip(cm_zip, lambda t: t.replace("TckrSymb", "Ticker", 1))) == ErrorKind.PARSE_FAIL


def test_cm_shifted_price_columns_are_caught(cm_zip):
    """If NSE swaps High and Low, nearly every traded row violates low<=close<=high: refuse the file."""
    def swap(t):
        head, rest = t.split("\n", 1)
        return head.replace("HghPric", "@").replace("LwPric", "HghPric").replace("@", "LwPric") + "\n" + rest
    assert kind(CM, edit_zip(cm_zip, swap)) == ErrorKind.PARSE_FAIL


def test_cm_garbage_number_is_parse_fail(cm_zip):
    assert kind(CM, edit_zip(cm_zip, lambda t: t.replace("1295.00", "12x5.00", 1))) == ErrorKind.PARSE_FAIL


def test_cm_two_trade_dates_rejected(cm_zip):
    def two(t):
        lines = t.splitlines()
        lines[1] = lines[1].replace("2026-11-06", "2026-11-05", 1)
        return "\n".join(lines) + "\n"
    assert kind(CM, edit_zip(cm_zip, two)) == ErrorKind.PARSE_FAIL


# --- FO bhavcopy ----------------------------------------------------------------------------------
def test_fo_parses_real_file(fo_zip):
    rows = FO(fo_zip)
    assert len(rows) == 301 and {r["trade_date"] for r in rows} == {"2026-11-06"}
    fut = next(r for r in rows if r["instrument_type"] == "IDF" and r["symbol"] == "BANKNIFTY")
    assert fut["strike"] is None and fut["option_type"] is None and fut["expiry"] == "2026-11-23"
    assert (fut["oi"], fut["oi_change"], fut["lot_size"], fut["underlying"]) == (2176290, -104610, 30, 59086.0)  # negative OI change
    opt = next(r for r in rows if r["name"] == "ABCAPITAL26DEC400PE")
    assert (opt["strike"], opt["option_type"], opt["oi"], opt["volume"], opt["turnover"], opt["lot_size"]) == \
           (400.0, "PE", 24800, 1, 642475.0, 1550)
    assert {r["option_type"] for r in rows} == {None, "CE", "PE"} and {"IDF", "IDO", "STF", "STO"} >= {r["instrument_type"] for r in rows}


def test_fo_untraded_contracts_do_not_trip_the_ohlc_check(fo_zip):
    rows = FO(fo_zip)
    assert any(r["volume"] == 0 and r["open"] == 0.0 and r["close"] > 0 for r in rows)  # real: O/H/L 0, close from prev settle


def test_fo_bad_option_type_and_min_rows(fo_zip):
    assert kind(FO, edit_zip(fo_zip, lambda t: t.replace(",CE,", ",XX,", 1))) == ErrorKind.PARSE_FAIL
    assert kind(n.parse_fo_bhavcopy, fo_zip) == ErrorKind.PARSE_FAIL


# --- participant OI -------------------------------------------------------------------------------
def test_participant_oi_parses_real_file():
    rows = n.parse_participant_oi(raw("fao_participant_oi_06112026.csv"))
    assert [r["client_type"] for r in rows] == ["Client", "DII", "FII", "Pro", "TOTAL"]
    fii = next(r for r in rows if r["client_type"] == "FII")
    assert fii["trade_date"] == "2026-11-06" and fii["fut_idx_long"] == 48656 and fii["fut_idx_short"] == 149072
    assert fii["opt_idx_put_long"] == 1379216 and fii["total_long"] == 6591329 and fii["total_short"] == 5256468
    assert all(isinstance(v, int) for k, v in fii.items() if k not in ("trade_date", "client_type"))


def test_participant_oi_tolerates_nses_off_by_one_totals():
    """Real file: Client/FII short totals are 1 below the sum of their columns. That must parse."""
    text = raw("fao_participant_oi_06112026.csv").decode()
    fii = [l for l in text.splitlines() if l.startswith("FII,")][0]
    cols = [int(x) for x in fii.split(",")[1:13]]
    assert sum(cols[i] for i in (1, 3, 6, 7, 10, 11)) == 5256469 != 5256468  # documents the quirk
    n.parse_participant_oi(text.encode())


def test_participant_oi_catches_a_wrong_column():
    text = raw("fao_participant_oi_06112026.csv").decode()
    bad = text.replace("FII,48656,", "FII,98656,")  # +50,000 in one column: totals no longer add up
    assert kind(n.parse_participant_oi, bad.encode()) == ErrorKind.PARSE_FAIL


def test_participant_oi_participants_must_add_to_total():
    text = raw("fao_participant_oi_06112026.csv").decode()
    lines = text.splitlines()
    dii = lines.index(next(l for l in lines if l.startswith("DII,")))
    parts = lines[dii].split(",")
    parts[1], parts[-2] = str(int(parts[1]) + 1000), str(int(parts[-2]) + 1000)  # consistent row, but TOTAL is now off
    lines[dii] = ",".join(parts)
    assert kind(n.parse_participant_oi, "\n".join(lines).encode()) == ErrorKind.PARSE_FAIL


def test_participant_oi_bad_title_and_blank_cell():
    text = raw("fao_participant_oi_06112026.csv").decode()
    assert kind(n.parse_participant_oi, text.replace("as on Nov 06, 2026", "").encode()) == ErrorKind.PARSE_FAIL
    assert kind(n.parse_participant_oi, text.replace("DII,54004,", "DII,,").encode()) == ErrorKind.PARSE_FAIL
    assert kind(n.parse_participant_oi, b"") == ErrorKind.PARSE_FAIL


def test_participant_oi_line_endings_do_not_matter():
    text = raw("fao_participant_oi_06112026.csv").decode().replace("\r\n", "\n")
    assert len(n.parse_participant_oi(text.encode())) == 5


# --- index closes ---------------------------------------------------------------------------------
def test_index_closes_parse_real_file():
    rows = n.parse_index_closes(raw("ind_close_all_06112026.csv"))
    assert len(rows) == 167
    nifty = next(r for r in rows if r["index_name"] == "Nifty 50")
    assert (nifty["trade_date"], nifty["close"], nifty["change_pct"], nifty["pe"], nifty["volume"]) == \
           ("2026-11-06", 25030.35, 2.01, 20.65, 490973304)
    vix = next(r for r in rows if r["index_name"] == "India VIX")
    assert vix["close"] == 17.47 and vix["volume"] is None and vix["pe"] is None  # '-' is None, not 0


def test_index_closes_truncated_and_html():
    head = "\n".join(raw("ind_close_all_06112026.csv").decode().splitlines()[:10]).encode()
    assert kind(n.parse_index_closes, head) == ErrorKind.PARSE_FAIL
    assert kind(n.parse_index_closes, b"") == ErrorKind.EMPTY


# --- F&O ban --------------------------------------------------------------------------------------
def test_ban_list_real_file_and_empty_list():
    assert n.parse_fo_ban(raw("fo_secban_06112026.csv")) == ("2026-11-06", ["KAYNES"])
    assert n.parse_fo_ban(b"Securities in Ban For Trade Date 07-NOV-2026:\n") == ("2026-11-07", [])  # genuine empty list


def test_ban_list_rejects_foreign_content():
    assert kind(n.parse_fo_ban, b"<html>Access Denied</html>") == ErrorKind.PARSE_FAIL
    assert kind(n.parse_fo_ban, b"") == ErrorKind.PARSE_FAIL
    assert kind(n.parse_fo_ban, b"Securities in Ban For Trade Date 06-NOV-2026:\n1,KAYNES\nnonsense here\n") == ErrorKind.PARSE_FAIL


def test_ban_list_multiple_and_ampersand_symbols():
    body = b"Securities in Ban For Trade Date 06-NOV-2026:\n1,KAYNES\n2,m&m\n3,BAJAJ-AUTO\n"
    assert n.parse_fo_ban(body)[1] == ["KAYNES", "M&M", "BAJAJ-AUTO"]


# --- bulk / block ---------------------------------------------------------------------------------
def test_bulk_and_block_parse_real_files():
    bulk, block = n.parse_bulk_deals(raw("bulk.csv")), n.parse_block_deals(raw("block.csv"))
    assert (len(bulk), len(block)) == (96, 20)
    assert bulk[0] == {"trade_date": "2026-11-06", "symbol": "AAATECH", "name": "AAA Technologies Limited",
                       "client": "VAIBHAV RAJENDRA DOSHI", "side": "BUY", "quantity": 84322, "price": 96.19, "remarks": None}
    assert {r["side"] for r in bulk + block} == {"BUY", "SELL"}
    assert all("NORGES BANK" not in r["client"] or "," not in r["client"] for r in block)


def test_deals_header_only_is_empty_and_bad_side_rejected():
    head = raw("block.csv").decode().splitlines()[0]
    assert kind(n.parse_block_deals, (head + "\n").encode()) == ErrorKind.EMPTY
    assert kind(n.parse_block_deals, raw("block.csv").replace(b",BUY,", b",HOLD,", 1)) == ErrorKind.PARSE_FAIL


def test_deals_quoted_client_names_with_commas():
    body = ('Date,Symbol,Security Name,Client Name,Buy/Sell,Quantity Traded,Trade Price / Wght. Avg. Price\n'
            '06-NOV-2026,ABC,Abc Ltd,"SMITH, JOHN & CO",SELL,"1,500",10.5\n').encode()
    r = n.parse_block_deals(body)[0]
    assert r["client"] == "SMITH, JOHN & CO" and r["quantity"] == 1500 and r["side"] == "SELL"


# --- shared helpers -------------------------------------------------------------------------------
def test_num_and_integer_helpers():
    assert u.num("1,234.5") == 1234.5 and u.num("-") is None and u.num("") is None and u.num(None) is None
    assert u.integer("12") == 12 and u.integer("12.0") == 12 and u.integer("-5") == -5
    assert kind(u.num, "abc") == ErrorKind.PARSE_FAIL and kind(u.integer, "1.5") == ErrorKind.PARSE_FAIL
    assert u.iso_date("06-NOV-2026") == "2026-11-06" and u.iso_date("2026-11-06") == "2026-11-06"
    assert kind(u.iso_date, "garbage") == ErrorKind.PARSE_FAIL and kind(u.iso_date, None) == ErrorKind.PARSE_FAIL


# --- provider flow with a fake network ------------------------------------------------------------
class FakeNet:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def __call__(self, url, profile, *, expect="text", **kw):
        self.calls.append((url, profile.name, expect))
        for needle, body in self.routes.items():
            if needle in url:
                if isinstance(body, DrishtiError):
                    raise body
                return Fetched(url, body, 200)
        raise DrishtiError(ErrorKind.NOT_FOUND, f"HTTP 404 {url}")


@pytest.fixture
def net(monkeypatch):
    def install(routes):
        f = FakeNet(routes)
        monkeypatch.setattr(n, "get", f)
        return f
    monkeypatch.setattr(n, "parse_cm_bhavcopy", CM)
    monkeypatch.setattr(n, "parse_fo_bhavcopy", FO)
    return install


def test_cm_bhavcopy_result_and_url(net, cm_zip):
    f = net({"BhavCopy_NSE_CM_0_0_0_20261106_F_0000.csv.zip": cm_zip})
    r = n.cm_bhavcopy(D)
    assert r.ok and r.as_of == "2026-11-06" and len(r.data) == 181 and r.sources[0].name == "nse_archive"
    assert f.calls == [("https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_20261106_F_0000.csv.zip", "nse_archive", "zip")]


def test_other_daily_urls(net, fo_zip):
    f = net({"fo/BhavCopy": fo_zip, "fao_participant_oi_06112026": raw("fao_participant_oi_06112026.csv"),
             "ind_close_all_06112026": raw("ind_close_all_06112026.csv"), "fo_secban_06112026": raw("fo_secban_06112026.csv")})
    assert n.fo_bhavcopy(D).ok and n.participant_oi(D).ok and n.index_closes(D).ok
    ban = n.fo_ban_list(D)
    assert ban.ok and ban.data == ["KAYNES"] and ban.as_of == "2026-11-06"
    urls = [c[0] for c in f.calls]
    assert any("/content/nsccl/fao_participant_oi_06112026.csv" in x for x in urls)
    assert any("/content/indices/ind_close_all_06112026.csv" in x for x in urls)
    assert any("/archives/fo/sec_ban/fo_secban_06112026.csv" in x for x in urls)


def test_file_for_another_date_is_stale_not_success(net, cm_zip):
    net({"20261105": cm_zip})  # NSE serves the 06 Nov data under the 05 Nov name
    r = n.cm_bhavcopy(date(2026, 11, 5))
    assert not r.ok and r.error.kind == ErrorKind.STALE


@pytest.mark.parametrize("err", [ErrorKind.NOT_FOUND, ErrorKind.BLOCKED, ErrorKind.BLOCKED_HTML, ErrorKind.TIMEOUT])
def test_network_errors_come_back_typed(net, err):
    net({"BhavCopy_NSE_CM": DrishtiError(err, "x")})
    r = n.cm_bhavcopy(D)
    assert not r.ok and r.error.kind == err and r.data is None


def test_parse_errors_come_back_typed(net):
    net({"BhavCopy_NSE_CM": b"PK-not-really-a-zip"})
    r = n.cm_bhavcopy(D)
    assert not r.ok and r.error.kind == ErrorKind.PARSE_FAIL


def test_store_fetches_once_and_serves_later_from_disk(net, cm_zip):
    f = net({"BhavCopy_NSE_CM": cm_zip})
    with Store() as st:
        a, b = n.cm_bhavcopy(D, store=st), n.cm_bhavcopy(D, store=st)
        assert a.ok and b.ok and a.data == b.data
        assert len(f.calls) == 1 and st.has_blob("cm_bhavcopy", D)


def test_a_bad_download_is_never_stored(net, cm_zip):
    with Store() as st:
        net({"BhavCopy_NSE_CM": b"<html><head>blocked</head></html>"})
        assert not n.cm_bhavcopy(D, store=st).ok
        assert not st.has_blob("cm_bhavcopy", D)  # so the next run tries the network again
        f = net({"BhavCopy_NSE_CM": cm_zip})
        assert n.cm_bhavcopy(D, store=st).ok and len(f.calls) == 1


def test_ban_list_provider_with_empty_list(net):
    net({"fo_secban": b"Securities in Ban For Trade Date 06-NOV-2026:\n"})
    r = n.fo_ban_list(D)
    assert r.ok and r.data == [] and r.as_of == "2026-11-06"


def test_bulk_deals_fresh_and_stale(net, monkeypatch):
    net({"equities/bulk.csv": raw("bulk.csv")})
    monkeypatch.setattr(canary, "today_ist", lambda: date(2026, 11, 8))
    with Store() as st:
        r = n.bulk_deals(store=st)
        assert r.ok and r.as_of == "2026-11-06" and len(r.data) == 96
        assert st.has_blob("bulk_deals", "2026-11-06")
    monkeypatch.setattr(canary, "today_ist", lambda: date(2026, 12, 30))
    r = n.bulk_deals()
    assert not r.ok and r.error.kind == ErrorKind.STALE  # a frozen file must not look like success


def test_block_deals_provider(net, monkeypatch):
    net({"equities/block.csv": raw("block.csv")})
    monkeypatch.setattr(canary, "today_ist", lambda: date(2026, 11, 8))
    assert n.block_deals().ok


# --- latest() walk-back ---------------------------------------------------------------------------
def test_latest_walks_back_over_weekend_and_missing_days():
    seen = []

    def fn(d):
        seen.append(d)
        return Result(data=[1], as_of=d.isoformat()) if d == date(2026, 11, 5) else Result(error=DrishtiError(ErrorKind.NOT_FOUND, "404"))

    r = u.latest(fn, start=date(2026, 11, 8))  # Sunday
    assert r.ok and r.as_of == "2026-11-05"
    assert seen == [date(2026, 11, 6), date(2026, 11, 5)]  # Sat/Sun skipped


def test_latest_stops_on_non_notfound_errors():
    r = u.latest(lambda d: Result(error=DrishtiError(ErrorKind.BLOCKED, "403")), start=date(2026, 11, 6))
    assert r.error.kind == ErrorKind.BLOCKED


def test_latest_gives_up_with_not_found():
    calls = []
    r = u.latest(lambda d: (calls.append(d), Result(error=DrishtiError(ErrorKind.NOT_FOUND, "404")))[1], start=date(2026, 11, 6), max_back=3)
    assert r.error.kind == ErrorKind.NOT_FOUND and len(calls) == 3


def test_participant_oi_reads_the_old_title_variants():
    # real title lines: 19 Aug 2024, 9 Apr 2020, 5 May 2020, 15 May 2020
    text = raw("fao_participant_oi_06112026.csv").decode("utf-8-sig")
    body = text.splitlines()[1:]
    for title, want in (('Participant wise Open Interest (no. of contracts) in Equity Derivatives as on Aug 19,"2024""""",,,,,,,,,,,,,', "2024-08-19"),
                        ("Participant wise Open Interest (no. of contracts) in Equity Derivatives as on Apr 09 2020,,,,,,,,,,,,,,", "2020-04-09"),
                        ('"Participant wise Open Interest (no. of contracts) in Equity Derivatives as on May 05,2020",,,,,,,,,,,,,,', "2020-05-05"),
                        ('""Participant wise Open Interest (no. of contracts) in Equity Derivatives as on May 15,2020"",,,,,,,,,,,,,,', "2020-05-15")):
        rows = n.parse_participant_oi("\n".join([title, *body]).encode())
        assert rows[0]["trade_date"] == want


def test_ban_list_nil_on_the_title_line_is_an_empty_list():
    d, syms = n.parse_fo_ban(b"Securities in Ban For Trade Date 17-NOV-2026: NIL\n")
    assert d == "2026-11-17" and syms == []
    with pytest.raises(DrishtiError):
        n.parse_fo_ban(b"Securities in Ban For Trade Date 17-NOV-2026: NIL\n1,ABC\n")


# --- participant OI: the title in NSE's own files is sometimes misspelt, and two 2018 files hold averaged counts --------
def test_participant_oi_title_misspelt_month_and_averaged_counts():
    text = raw("fao_participant_oi_06112026.csv").decode("utf-8-sig")
    body = text.splitlines()[1:]
    t = "Participant wise Open Interest (no. of contracts) in Equity Derivatives as on Decemebr 06, 2018,,,,,,,,,,,,,,"
    assert n.parse_participant_oi("\n".join([t, *body]).encode())[0]["trade_date"] == "2018-12-06"
    t = "Participant wise Open Interest (no. of contracts) in Equity Derivatives for 13-Jun-2018,,,,,,,,,,,,,,"
    assert n.parse_participant_oi("\n".join([t, *body]).encode())[0]["trade_date"] == "2018-06-13"
    assert n._whole("216533.6667", "x") == 216534 and n._whole("5", "x") == 5 and kind(n._whole, "abc", "x") == ErrorKind.PARSE_FAIL


def _oi_lines():
    return raw("fao_participant_oi_06112026.csv").decode("utf-8-sig").splitlines()


def test_participant_oi_title_without_a_year_uses_the_day_asked_for():
    lines = _oi_lines()
    t = "Participant wise Open Interest (no. of contracts) in Equity Derivatives as on Nov 06,,,,,,,,,,,,,,"       # seen 8 Jun 2021
    assert n.parse_participant_oi("\n".join([t, *lines[1:]]).encode(), day=date(2026, 11, 6))[0]["trade_date"] == "2026-11-06"
    assert kind(n.parse_participant_oi, "\n".join([t, *lines[1:]]).encode()) == ErrorKind.PARSE_FAIL        # no day given: cannot guess the year


def test_participant_oi_file_with_no_title_line_takes_the_day_asked_for():
    lines = _oi_lines()
    body = "\n".join(lines[1:]).encode()                                                                  # seen 13 Jun 2018
    assert n.parse_participant_oi(body, day=date(2018, 6, 13))[0]["trade_date"] == "2018-06-13"
    assert kind(n.parse_participant_oi, body) == ErrorKind.PARSE_FAIL


def test_participant_oi_wrong_total_column_is_repaired_only_when_the_components_prove_it():
    lines = _oi_lines()
    i = next(k for k, l in enumerate(lines) if l.startswith("Client,"))
    parts = lines[i].split(",")
    true_short = parts[-1]
    parts[-1] = str(int(true_short) + 2_000_000)                       # seen 2017: a Total column that is nowhere near its components
    lines[i] = ",".join(parts)
    j = next(k for k, l in enumerate(lines) if l.startswith("TOTAL,"))
    tp = lines[j].split(",")
    tp[-1] = str(int(tp[-1]) + 2_000_000)                              # ... in the TOTAL row too
    lines[j] = ",".join(tp)
    rows = n.parse_participant_oi("\n".join(lines).encode())
    client = next(r for r in rows if r["client_type"] == "Client")
    assert client["total_short"] == int(true_short) or abs(client["total_short"] - int(true_short)) <= 2   # rebuilt from its own columns
    parts = lines[i].split(",")
    parts[1] = str(int(parts[1]) + 50_000)                             # a wrong COMPONENT is not repairable: participants no longer add to TOTAL
    lines[i] = ",".join(parts)
    assert kind(n.parse_participant_oi, "\n".join(lines).encode()) == ErrorKind.PARSE_FAIL


# --- the older delivery file (MTO) ---------------------------------------------------------------------------------
def _mto(rows=900, *, declared=None, extra=()):
    body = ["Security Wise Delivery Position - Compulsory Rolling Settlement", f"10,MTO,15092016,386902432,{(declared or rows):07d}",
            "Trade Date <15-SEP-2016>,Settlement Type <N>,Settlement No <2016174>,Settlement Date <19-SEP-2016>",
            "Record Type,Sr No,Name of Security,Quantity Traded,Deliverable Quantity(gross across client level),% of Deliverable Quantity to Traded Quantity"]
    body += [f"20,{i},SYM{i},EQ,1000,{400 + i % 500},{(400 + i % 500) / 10:.2f}" for i in range(1, rows + 1)]
    return "\n".join([*body, *extra]).encode()


def test_mto_parses_into_the_delivery_shape():
    rows = n.parse_delivery_mto(_mto())
    assert len(rows) == 900 and rows[0] == {"trade_date": "2016-09-15", "symbol": "SYM1", "series": "EQ", "ttl_qty": 1000, "deliv_qty": 401,
                                            "deliv_per": 40.1, "trades": None}


def test_mto_refuses_truncated_wrong_or_untrustworthy_files():
    assert kind(n.parse_delivery_mto, _mto(declared=950)) == ErrorKind.PARSE_FAIL                       # says 950 records, holds 900
    assert kind(n.parse_delivery_mto, b"<html>Access denied</html>") == ErrorKind.PARSE_FAIL
    assert kind(n.parse_delivery_mto, _mto(rows=100)) in (ErrorKind.PARSE_FAIL, ErrorKind.EMPTY)       # far fewer stocks than a real session
    bad = [f"20,{i},BAD{i},EQ,1000,2000,200.00" for i in range(950, 950 + n.MAX_BAD_DELIVERY_ROWS + 1)]
    assert kind(n.parse_delivery_mto, _mto(declared=900 + len(bad), extra=bad)) == ErrorKind.PARSE_FAIL
    one = n.parse_delivery_mto(_mto(declared=901, extra=["20,901,BAD,EQ,1000,2000,200.00"]))              # one impossible row is dropped
    assert len(one) == 900


def test_delivery_falls_back_to_the_old_file_only_when_the_new_one_is_missing_or_wrong(monkeypatch):
    from drishti.core.fetch import Fetched
    d = date(2016, 9, 15)

    def fake_get(url, profile, expect="text", **k):
        if "MTO_" in url:
            return Fetched(url, _mto(), 200)
        raise DrishtiError(ErrorKind.NOT_FOUND, "404")
    monkeypatch.setattr(n, "get", fake_get)
    r = n.delivery(d)
    assert r.ok and len(r.data) == 900 and r.data[0]["trade_date"] == "2016-09-15"
    monkeypatch.setattr(n, "get", lambda url, profile, expect="text", **k: (_ for _ in ()).throw(DrishtiError(ErrorKind.NOT_FOUND, "404")))
    assert n.delivery(d).error.kind == ErrorKind.NOT_FOUND                                              # both missing: a holiday, reported as before
    monkeypatch.setattr(n, "get", lambda url, profile, expect="text", **k: (_ for _ in ()).throw(DrishtiError(ErrorKind.BLOCKED_HTML, "x")))
    assert n.delivery(d).error.kind == ErrorKind.BLOCKED_HTML                                            # a block is never worked around


def test_mto_without_a_trade_date_line_takes_the_date_from_the_header_record():
    lines = _mto().decode().splitlines()
    body = "\n".join(l for l in lines if not l.startswith("Trade Date")).encode()          # seen: six 2017-2019 files
    assert n.parse_delivery_mto(body)[0]["trade_date"] == "2016-09-15"
    assert kind(n.parse_delivery_mto, "\n".join(l for l in lines if not l.startswith(("Trade Date", "10,"))).encode()) == ErrorKind.PARSE_FAIL


def test_mto_record_count_off_by_one_is_tolerated_but_a_cut_file_is_not():
    assert len(n.parse_delivery_mto(_mto(declared=899))) == 900                                  # seen 12 Oct 2018: declared 1739, held 1740
    assert kind(n.parse_delivery_mto, _mto(declared=901)) == ErrorKind.PARSE_FAIL


def test_delivery_uses_the_old_file_when_the_new_one_cannot_be_read(monkeypatch):
    from drishti.core.fetch import Fetched
    d = date(2022, 8, 8)
    monkeypatch.setattr(n, "get", lambda url, profile, expect="text", **k: Fetched(url, _mto() if "MTO_" in url else b"garbage\n", 200))
    monkeypatch.setattr(n, "parse_delivery_mto", lambda b, **k: [{"trade_date": "2022-08-08", "symbol": "A", "series": "EQ", "ttl_qty": 1, "deliv_qty": 1,
                                                              "deliv_per": 100.0, "trades": None}])
    r = n.delivery(d)
    assert r.ok and r.data[0]["symbol"] == "A"


_OI_2017 = {   # the real rows of three 2017 files whose Total columns are wrong (probe 18 Nov 2026)
    "2017-09-22": ["Client,156638,182987,1038556,304222,608187,662233,645511,897799,324024,108730,236420,115879,2898368,5165307",
                   "DII,17488,27895,24520,713982,124754,54906,0,0,158,0,0,0,221826,963703",
                   "FII,157881,127448,365783,477810,268057,444621,219144,191751,28488,26400,30573,22624,1291230,2337956",
                   "Pro,39022,32699,165435,98280,169064,224989,305407,297198,53261,57013,138938,53640,708784,1581306",
                   "TOTAL,371029,371029,1594294,1594294,1170062,1386749,1170062,1386748,405931,192143,405931,192143,5120208,10048272"],
    "2017-05-29": ["Client,114578,242614,889480,240439,313804,522443,543974,658571,135173,59467,105246,67718,1721141,1858562",
                   "DII,16752,21492,28715,495886,142834,14453,0,0,415,0,20,0,60335,517398",
                   "FII,219833,38437,406245,653821,235204,273032,166387,62136,15479,12219,15267,10054,926808,946102",
                   "Pro,19570,68190,126017,60311,184717,160133,166198,249355,27960,33058,58494,26972,366738,629520",
                   "TOTAL,370733,370733,1450457,1450457,876559,970061,876559,970062,179027,104744,179027,104744,3075022,3951582"],
    "2017-01-24": ["Client,136476,207659,727318,238930,495404,767946,636577,837423,212351,144091,170011,110946,2483586,1364123",
                   "DII,20963,41806,11709,452561,143817,11239,0,0,104,0,14,0,187832,494381",
                   "FII,217489,102668,533014,612886,287371,429578,235085,175749,29669,22951,31370,26853,1520072,1008862",
                   "Pro,24157,46952,121437,89101,195099,191186,250029,386778,52617,36090,93346,65333,620586,544761",
                   "TOTAL,399085,399085,1393478,1393478,1121691,1399949,1121691,1399950,294741,203132,294741,203132,4812076,3412127"],
}


@pytest.mark.parametrize("day", sorted(_OI_2017))
def test_participant_oi_real_2017_files_with_wrong_totals_are_read_from_their_components(day):
    head = "Client Type," + ",".join(n._OI_COLS.keys())
    title = f"Participant wise Open Interest (no. of contracts) in Equity Derivatives as on {date.fromisoformat(day):%b %d, %Y},,,,,,,,,,,,,,"
    rows = n.parse_participant_oi("\n".join([f'"{title}"', head, *_OI_2017[day]]).encode())
    assert rows[0]["trade_date"] == day
    for r in rows:
        assert r["total_long"] == sum(r[k] for k in n._LONGS) and r["total_short"] == sum(r[k] for k in n._SHORTS)
    tot = next(r for r in rows if r["client_type"] == "TOTAL")
    assert abs(tot["total_long"] - tot["total_short"]) <= 3                      # a market's longs and shorts match: the repair makes that true

"""BSE UDiFF bhavcopy provider on a saved real file (06 Nov 2026)."""
from __future__ import annotations

from datetime import date
from functools import partial
from pathlib import Path

import pytest

from drishti.core.envelope import DrishtiError, ErrorKind
from drishti.core.fetch import Fetched
from drishti.core.isin import is_valid_isin
from drishti.providers import bse, nse_archive
from drishti.store import Store

FIX = Path(__file__).parent / "fixtures"
BODY = (FIX / "bse" / "BhavCopy_BSE_CM_20261106.csv").read_bytes()
D = date(2026, 11, 6)
PARSE = partial(bse.parse_bhavcopy, min_rows=1)


def kind(fn, *a, **k) -> ErrorKind:
    with pytest.raises(DrishtiError) as ei:
        fn(*a, **k)
    return ei.value.kind


def test_parses_real_file():
    rows = PARSE(BODY)
    assert len(rows) == 88 and {r["trade_date"] for r in rows} == {"2026-11-06"}
    ril = next(r for r in rows if r["symbol"] == "RELIANCE")
    assert (ril["scrip_code"], ril["isin"], ril["series"], ril["name"]) == ("500325", "INE002A01018", "A", "RELIANCE INDUSTRIES LTD.")
    assert (ril["open"], ril["high"], ril["low"], ril["close"], ril["prev_close"]) == (1297.5, 1318.1, 1295.0, 1318.1, 1313.35)
    assert ril["volume"] == 768236 and ril["trades"] == 18320
    assert {"A", "B", "X"} <= {r["series"] for r in rows}  # BSE groups, not just A
    assert all(is_valid_isin(r["isin"]) for r in rows)


def test_isin_joins_nse_and_bse_for_the_same_company():
    nse = nse_archive.parse_cm_bhavcopy((FIX / "nse_archive" / "BhavCopy_NSE_CM_20261106.csv.zip").read_bytes(), min_rows=1)
    nse_by_isin = {r["isin"]: r for r in nse if r["series"] == "EQ"}
    both = [r for r in PARSE(BODY) if r["isin"] in nse_by_isin]
    assert {"INE002A01018", "INE467B01029"} <= {r["isin"] for r in both}  # RELIANCE, TCS appear on both exchanges
    ril_bse = next(r for r in both if r["isin"] == "INE002A01018")
    assert ril_bse["scrip_code"] == "500325" and nse_by_isin["INE002A01018"]["symbol"] == "RELIANCE"
    # the same stock closes within a percent on both exchanges: a layout slip would break this
    assert abs(ril_bse["close"] / nse_by_isin["INE002A01018"]["close"] - 1) < 0.01


def test_default_minimum_rejects_truncated_file():
    assert kind(bse.parse_bhavcopy, BODY) == ErrorKind.PARSE_FAIL


def test_duplicate_or_non_numeric_scrip_codes_rejected():
    lines = BODY.decode().splitlines()
    dup = "\n".join([lines[0], lines[1], lines[1]]) + "\n"
    assert kind(PARSE, dup.encode()) == ErrorKind.PARSE_FAIL
    bad = BODY.decode().replace(",500325,", ",ABC325,", 1)
    assert kind(PARSE, bad.encode()) == ErrorKind.PARSE_FAIL


def test_html_and_empty_and_column_changes():
    assert kind(PARSE, b"<html><body>Access denied</body></html>") == ErrorKind.PARSE_FAIL
    assert kind(PARSE, (BODY.decode().splitlines()[0] + "\n").encode()) == ErrorKind.EMPTY
    assert kind(PARSE, BODY.replace(b"ClsPric", b"Close", 1)) == ErrorKind.PARSE_FAIL


class FakeNet:
    def __init__(self, body):
        self.body, self.calls = body, []

    def __call__(self, url, profile, *, expect="text", **kw):
        self.calls.append((url, profile.name, expect))
        if isinstance(self.body, DrishtiError):
            raise self.body
        return Fetched(url, self.body, 200)


@pytest.fixture
def net(monkeypatch):
    monkeypatch.setattr(bse, "parse_bhavcopy", PARSE)

    def install(body):
        f = FakeNet(body)
        monkeypatch.setattr(bse, "get", f)
        return f
    return install


def test_provider_result_and_url(net):
    f = net(BODY)
    r = bse.bhavcopy(D)
    assert r.ok and r.as_of == "2026-11-06" and len(r.data) == 88 and r.sources[0].name == "bse"
    assert f.calls == [("https://www.bseindia.com/download/BhavCopy/Equity/BhavCopy_BSE_CM_0_0_0_20261106_F_0000.CSV", "bse_www", "csv")]


def test_wrong_date_is_stale(net):
    net(BODY)
    r = bse.bhavcopy(date(2026, 11, 5))
    assert not r.ok and r.error.kind == ErrorKind.STALE


@pytest.mark.parametrize("err", [ErrorKind.NOT_FOUND, ErrorKind.BLOCKED_HTML, ErrorKind.BLOCKED, ErrorKind.TIMEOUT])
def test_errors_are_typed(net, err):
    net(DrishtiError(err, "x"))
    assert bse.bhavcopy(D).error.kind == err


def test_store_once_and_bad_download_not_stored(net):
    with Store() as st:
        net(b"<html><head></head></html>")
        assert not bse.bhavcopy(D, store=st).ok and not st.has_blob("bse_bhavcopy", D)
        f = net(BODY)
        assert bse.bhavcopy(D, store=st).ok and bse.bhavcopy(D, store=st).ok
        assert len(f.calls) == 1 and st.has_blob("bse_bhavcopy", D)

"""Cash FII/DII flows, FRED series and CFTC TFF positions: parsers on saved real files, providers on a fake network."""
from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

import pytest

from drishti.core.envelope import DrishtiError, ErrorKind
from drishti.core.fetch import Fetched
from drishti.providers import cftc, fred, nse_flows
from drishti.store import Store

FIX = Path(__file__).parent / "fixtures"


def kind(fn, *a, **k) -> ErrorKind:
    with pytest.raises(DrishtiError) as ei:
        fn(*a, **k)
    return ei.value.kind


class Net:
    def __init__(self, body=None, error=None):
        self.body, self.error, self.urls = body, error, []

    def api_get(self, path, **params):
        self.urls.append(path)
        if self.error:
            raise self.error
        return Fetched("https://www.nseindia.com/api/" + path, self.body, 200)

    def get(self, url, profile, *, expect="text", **kw):
        self.urls.append((url, profile.name, expect))
        if self.error:
            raise self.error
        return Fetched(url, self.body, 200)


# --- FII / DII cash ---------------------------------------------------------------------------------
def fiidii() -> bytes:
    return (FIX / "nse_flows" / "fiidiiTradeReact.json").read_bytes()


def test_fii_dii_real_payload():
    d = nse_flows.parse_fii_dii(fiidii())
    assert d["date"] == "2026-11-06" and d["unit"] == "INR crore"
    assert d["fii"] == {"buy": 30455.63, "sell": 16705.81, "net": 13749.82}
    assert d["dii"] == {"buy": 18084.32, "sell": 24053.02, "net": -5968.7}


def edit(**repl):
    rows = json.loads(fiidii())
    for r in rows:
        r.update({k: v for k, v in repl.items()} if r["category"] == "DII" else {})
    return json.dumps(rows).encode()


def test_fii_dii_integrity_checks():
    assert kind(nse_flows.parse_fii_dii, edit(netValue="-5000.0")) == ErrorKind.PARSE_FAIL           # buy - sell != net
    assert kind(nse_flows.parse_fii_dii, edit(buyValue="")) == ErrorKind.PARSE_FAIL                   # blank figure
    assert kind(nse_flows.parse_fii_dii, edit(date="05-Nov-2026")) == ErrorKind.PARSE_FAIL            # two session dates
    assert kind(nse_flows.parse_fii_dii, edit(category="FII/FPI")) == ErrorKind.PARSE_FAIL            # duplicate category
    assert kind(nse_flows.parse_fii_dii, edit(category="PRO")) == ErrorKind.PARSE_FAIL                # unknown category
    assert kind(nse_flows.parse_fii_dii, json.dumps(json.loads(fiidii())[:1]).encode()) == ErrorKind.PARSE_FAIL  # one side missing
    assert kind(nse_flows.parse_fii_dii, b"[]") == ErrorKind.EMPTY
    assert kind(nse_flows.parse_fii_dii, b"<html>blocked</html>") == ErrorKind.PARSE_FAIL


def test_fii_dii_provider_freshness_and_store(monkeypatch):
    n = Net(fiidii())
    monkeypatch.setattr(nse_flows.nse_live, "api_get", n.api_get)
    with Store() as st:
        r = nse_flows.fii_dii_cash(today=date(2026, 11, 8), store=st)
        assert r.ok and r.as_of == "2026-11-06" and st.has_blob("fii_dii_cash", "2026-11-06")
    stale = nse_flows.fii_dii_cash(today=date(2026, 11, 20))
    assert stale.error.kind == ErrorKind.STALE                      # newest session date is 14 days old: refuse, don't serve
    monkeypatch.setattr(nse_flows.nse_live, "api_get", Net(error=DrishtiError(ErrorKind.BLOCKED, "403")).api_get)
    assert nse_flows.fii_dii_cash().error.kind == ErrorKind.BLOCKED


# --- FRED ------------------------------------------------------------------------------------------
def fred_file(sid="DTWEXBGS") -> bytes:
    return (FIX / "fred" / f"{sid}.csv").read_bytes()


def test_fred_real_files():
    d = fred.parse_fred(fred_file(), "DTWEXBGS")
    assert len(d) == 384 and d[-1]  # 400 file rows; the 16 holiday rows marked "." are skipped, not zero-filled
    assert d[-1] == {"date": "2026-10-30", "value": 121.6656}
    w = fred.parse_fred(fred_file("WALCL"), "WALCL")
    assert w[-1] == {"date": "2026-11-04", "value": 6748145.0}


def test_fred_missing_values_are_skipped_not_zeroed():
    lines = fred_file().decode().splitlines()
    lines[10] = lines[10].split(",")[0] + ",."
    d = fred.parse_fred(("\n".join(lines) + "\n").encode(), "DTWEXBGS")
    assert len(d) == 383 and all(r["value"] > 0 for r in d)


def test_fred_layout_drift_is_typed():
    good = fred_file().decode()
    assert kind(fred.parse_fred, good.replace("observation_date", "when").encode(), "DTWEXBGS") == ErrorKind.PARSE_FAIL
    assert kind(fred.parse_fred, good.encode(), "WALCL") == ErrorKind.PARSE_FAIL                      # wrong series column
    assert fred.parse_fred(good.replace("observation_date", "DATE").encode(), "DTWEXBGS")             # old header still fine
    lines = good.splitlines()
    assert kind(fred.parse_fred, "\n".join(lines[:5]).encode(), "DTWEXBGS") == ErrorKind.PARSE_FAIL  # truncated
    lines[20], lines[21] = lines[21], lines[20]
    assert kind(fred.parse_fred, "\n".join(lines).encode(), "DTWEXBGS") == ErrorKind.PARSE_FAIL      # dates out of order
    lines = good.splitlines()
    lines[7] = lines[7].split(",")[0] + ",abc"
    assert kind(fred.parse_fred, "\n".join(lines).encode(), "DTWEXBGS") == ErrorKind.PARSE_FAIL


def test_fred_provider(monkeypatch):
    n = Net(fred_file())
    monkeypatch.setattr(fred, "get", n.get)
    r = fred.fred_series("DTWEXBGS", today=date(2026, 11, 8), tail=5)
    assert r.ok and r.as_of == "2026-10-30" and len(r.data["observations"]) == 5 and r.data["n_total"] == 384
    assert n.urls == [("https://fred.stlouisfed.org/graph/fredgraph.csv?id=DTWEXBGS", "fred", "csv")]
    assert fred.fred_series("DTWEXBGS", today=date(2026, 12, 1)).error.kind == ErrorKind.STALE
    assert fred.fred_series("NOTVERIFIED").error.kind == ErrorKind.NOT_IMPLEMENTED               # no freshness window known
    assert fred.fred_series("../x", max_age_days=5).error.kind == ErrorKind.PARSE_FAIL             # id is validated, never interpolated blindly
    assert fred.fred_series("DTWEXBGS", max_age_days=30, today=date(2026, 11, 8)).ok


# --- CFTC ------------------------------------------------------------------------------------------
def tff() -> bytes:
    return (FIX / "cftc" / "FinFutWk.txt").read_bytes()


def test_cftc_real_file_every_row_reconciles():
    rows = cftc.parse_tff(tff())
    assert len(rows) == 96 and {r["as_of"] for r in rows} == {"2026-11-03"}
    cad = next(r for r in rows if r["code"] == "090741")
    assert cad["open_interest"] == 348660 and cad["oi_change"] == -12281 and cad["contract_unit"].startswith("CONTRACTS OF CAD")
    new = next(r for r in rows if r["code"] == "168DC1")      # a newly listed contract has no prior week: change fields are "."
    assert new["oi_change"] is None and new["dealer"]["net_change"] is None and new["open_interest"] == 25030
    assert cad["dealer"] == {"long": 154570, "short": 86816, "spread": 1177, "net": 67754, "net_change": -21503 - 6552}
    assert cad["lev_money"]["net"] == 25334 - 111636 and cad["nonreportable"] == {"long": 26239, "short": 33848, "net": -7609}


def test_cftc_layout_and_integrity_failures():
    body = tff().decode()
    first = body.splitlines()[0]
    assert kind(cftc.parse_tff, body.replace(first, first.replace("154570", "164570", 1), 1).encode()) == ErrorKind.PARSE_FAIL  # components != totals
    assert kind(cftc.parse_tff, body.replace(first, first.replace("261103", "261104", 1), 1).encode()) == ErrorKind.PARSE_FAIL  # two dates disagree
    assert kind(cftc.parse_tff, body.replace(first, first.rsplit(",", 1)[0], 1).encode()) == ErrorKind.PARSE_FAIL                # field count
    assert kind(cftc.parse_tff, "\n".join(body.splitlines()[:10]).encode()) == ErrorKind.PARSE_FAIL                               # truncated
    assert kind(cftc.parse_tff, b"<html>blocked</html>") == ErrorKind.PARSE_FAIL
    assert kind(cftc.parse_tff, b"") == ErrorKind.EMPTY
    other = re.sub(r"2026-11-03", "2026-10-27", body.splitlines()[5]).replace("261103", "261027")
    assert kind(cftc.parse_tff, (body.replace(body.splitlines()[5], other)).encode()) == ErrorKind.PARSE_FAIL                   # mixed weeks


def test_cftc_provider(monkeypatch):
    n = Net(tff())
    monkeypatch.setattr(cftc, "get", n.get)
    with Store() as st:
        r = cftc.cftc_tff(today=date(2026, 11, 8), store=st)
        assert r.ok and r.as_of == "2026-11-03" and st.has_blob("cftc_tff", "2026-11-03")
    by = {x["code"]: x for x in r.data}
    assert set(by) == set(cftc.WATCH) and by["098662"]["label"] == "USD index" and by["098662"]["open_interest"] == 49091
    assert cftc.cftc_tff(today=date(2026, 12, 1)).error.kind == ErrorKind.STALE
    assert cftc.cftc_tff(codes={"ZZZZZZ": "nope"}, today=date(2026, 11, 8)).error.kind == ErrorKind.PARSE_FAIL  # watched contract vanished

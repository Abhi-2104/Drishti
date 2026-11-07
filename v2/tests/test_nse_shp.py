"""Shareholding patterns: promoter holding and encumbrance, on real filings (Sep-2026 quarter)."""
from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

import pytest

from drishti.core.envelope import DrishtiError, ErrorKind
from drishti.core.fetch import Fetched
from drishti.providers import nse_shp as s
from drishti.store import Store

FIX = Path(__file__).parent / "fixtures" / "nse_shp"
TODAY = date(2026, 11, 8)


def xml(sym: str) -> bytes:
    return (FIX / f"shp_{sym}.xml").read_bytes()


def master() -> bytes:
    return (FIX / "shp_master.json").read_bytes()


def kind(fn, *a, **k) -> ErrorKind:
    with pytest.raises(DrishtiError) as ei:
        fn(*a, **k)
    return ei.value.kind


def set_fact(body: bytes, ctx: str, name: str, value: str) -> bytes:
    pat = re.compile(rb'(<in-bse-shp:' + name.encode() + rb' contextRef="' + ctx.encode() + rb'"[^>]*>)[^<]*(</)')
    out, n = pat.subn(rb"\g<1>" + value.encode() + rb"\g<2>", body)
    assert n == 1, (ctx, name, n)
    return out


PROM = "ShareholdingOfPromoterAndPromoterGroup_ContextI"
TOTAL = "ShareholdingPattern_ContextI"
MAIN = "MainI"


# --- filings --------------------------------------------------------------------------------------
def test_alps_partial_pledge():
    f = s.parse_shp_filing(xml("ALPSINDUS"))
    assert (f["symbol"], f["isin"], f["isin_valid"], f["as_on"], f["form_version"]) == ("ALPSINDUS", "INE093B01015", True, "2026-09-30", "1.2")
    assert (f["total_shares"], f["promoter_shares"]) == (39114100, 8686018)
    assert f["promoter_pct"] == pytest.approx(22.21, abs=0.01)
    assert (f["pledged_shares"], f["ndu_shares"], f["other_encumbered_shares"], f["encumbered_shares"]) == (4019018, 0, 0, 4019018)
    assert f["encumbered_pct_of_promoter"] == pytest.approx(46.27, abs=0.01)   # 4,019,018 of the promoters' 8,686,018
    assert f["encumbered_pct_of_total"] == pytest.approx(10.28, abs=0.01)      # ...but only a tenth of the whole company
    assert f["pledged_pct_of_promoter"] == f["encumbered_pct_of_promoter"]
    assert (f["declared_pledged"], f["declared_ndu"], f["declared_other"], f["flags_consistent"]) == (True, False, False, True)


def test_alps_per_holder_rows():
    f = s.parse_shp_filing(xml("ALPSINDUS"))
    hs = f["holders"]
    assert len(hs) == 19 and f["holders_reconcile"] is True        # the 19 named promoters add up to the promoter total
    top = hs[0]
    assert top["encumbered_shares"] == 647460 and top["encumbered_pct_of_holding"] == pytest.approx(56.43, abs=0.01) and top["name"]
    assert [h["encumbered_shares"] for h in hs] == sorted((h["encumbered_shares"] for h in hs), reverse=True)
    assert any(h["encumbered_shares"] == 0 for h in hs)            # some promoters pledge nothing
    assert sum(h["encumbered_shares"] for h in hs) == f["encumbered_shares"]


def test_arshiya_every_promoter_share_pledged():
    f = s.parse_shp_filing(xml("ARSHIYA"))
    assert f["encumbered_pct_of_promoter"] == 100.0 and f["pledged_shares"] == f["promoter_shares"] == 37871225
    assert f["encumbered_pct_of_total"] == pytest.approx(14.37, abs=0.01)


def test_vedanta_is_fully_encumbered_with_no_pledge_at_all():
    """The case a 'pledge' tag-only signal misses: 100% encumbered through NDU and other encumbrances."""
    f = s.parse_shp_filing(xml("VEDL"))
    assert (f["pledged_shares"], f["ndu_shares"], f["other_encumbered_shares"]) == (0, 107342705, 2032309058)
    assert f["encumbered_shares"] == 2139651763 == f["ndu_shares"] + f["other_encumbered_shares"]
    assert f["pledged_pct_of_promoter"] == 0.0 and f["encumbered_pct_of_promoter"] == pytest.approx(99.995, abs=0.001)
    assert (f["declared_pledged"], f["declared_ndu"], f["declared_other"], f["flags_consistent"]) == (False, True, True, True)
    assert f["promoter_pct"] == pytest.approx(54.72, abs=0.01) and len(f["holders"]) == 30


def test_clean_company_has_explicit_zeros_not_blanks():
    f = s.parse_shp_filing(xml("20MICRONS"))
    assert (f["encumbered_shares"], f["pledged_shares"], f["encumbered_pct_of_promoter"], f["encumbered_pct_of_total"]) == (0, 0, 0.0, 0.0)
    assert f["declared_pledged"] is False and f["flags_consistent"] is True and f["holders_reconcile"] is True
    assert f["promoter_pct"] == pytest.approx(45.04, abs=0.01)


def test_newer_taxonomy_namespace_date_still_parses():
    body = xml("ARSHIYA").replace(b"2025-10-31", b"2027-04-30")
    assert s.parse_shp_filing(body)["encumbered_shares"] == 37871225


def test_pit_namespace_date_is_not_hardcoded_either():
    from drishti.providers import nse_pit
    body = (Path(__file__).parent / "fixtures" / "nse_pit" / "IT_16166_WebXMLFile_20261107_215948860.xml").read_bytes()
    assert nse_pit.parse_pit_filing(body.replace(b"2017-09-15", b"2027-01-01"))[0]["symbol"] == "INDOSTAR"


# --- integrity checks -----------------------------------------------------------------------------
def test_definition_change_is_refused():
    """If NSE's own 'encumbered %' stops matching encumbered/promoter shares, the semantics moved: do not guess."""
    bad = set_fact(xml("ALPSINDUS"), PROM, "EncumberedSharesHeldAsPercentageOfTotalNumberOfShares", "0.1028")
    assert kind(s.parse_shp_filing, bad) == ErrorKind.PARSE_FAIL
    bad = set_fact(xml("ALPSINDUS"), TOTAL, "EncumberedSharesHeldAsPercentageOfTotalNumberOfShares", "0.4627")
    assert kind(s.parse_shp_filing, bad) == ErrorKind.PARSE_FAIL


def test_share_counts_must_reconcile():
    assert kind(s.parse_shp_filing, set_fact(xml("ARSHIYA"), PROM, "NumberOfShares", "47871225")) == ErrorKind.PARSE_FAIL
    assert kind(s.parse_shp_filing, set_fact(xml("ARSHIYA"), TOTAL, "NumberOfShares", "0")) == ErrorKind.PARSE_FAIL


def test_encumbrance_parts_must_add_up_and_not_exceed_promoter_shares():
    assert kind(s.parse_shp_filing, set_fact(xml("VEDL"), PROM, "NumberOfSharesEncumberedUnderNonDisposalUndertaking", "1000")) == ErrorKind.PARSE_FAIL
    body = set_fact(xml("ARSHIYA"), PROM, "NumberOfSharesEncumbered", "99999999999")
    body = set_fact(body, PROM, "NumberOfSharesEncumberedUnderPledged", "99999999999")
    assert kind(s.parse_shp_filing, body) == ErrorKind.PARSE_FAIL


def test_unticked_box_with_encumbered_shares_is_flagged_not_hidden():
    body = set_fact(xml("ARSHIYA"), MAIN, "WhetherAnySharesHeldByPromotersAreEncumberedUnderPledged", "false")
    f = s.parse_shp_filing(body)
    assert f["declared_pledged"] is False and f["encumbered_shares"] > 0 and f["flags_consistent"] is False


def _professionally_managed(sym="20MICRONS") -> bytes:
    """A doctored real filing shaped like Coforge's: no promoter section, public holds everything."""
    body = xml(sym)
    total = re.search(rb'<in-bse-shp:NumberOfShares contextRef="' + TOTAL.encode() + rb'"[^>]*>(\d+)<', body).group(1).decode()
    body = re.sub(rb'<in-bse-shp:\w+ contextRef="(?:' + PROM.encode() + rb'|(?:D_)?(?:IndividualsOrHUF|OthersIndianShareholders)_Context\d+)"[^>]*>[^<]*</in-bse-shp:\w+>', b"", body)
    body = set_fact(body, "PublicShareholding_ContextI", "NumberOfShares", total)
    return set_fact(body, "PublicShareholding_ContextI", "ShareholdingAsAPercentageOfTotalNumberOfShares", "1")


def test_company_without_promoter_is_zero_not_a_parse_failure():
    f = s.parse_shp_filing(_professionally_managed())
    assert f["no_promoter"] is True and f["promoter_shares"] == 0 and f["promoter_pct"] == 0.0 and f["public_pct"] == 100.0
    assert f["encumbered_shares"] == 0 and f["encumbered_pct_of_promoter"] is None and f["holders_reconcile"] is True
    assert s.parse_shp_filing(xml("20MICRONS"))["no_promoter"] is False


def test_missing_promoter_section_is_not_accepted_when_the_register_does_not_add_up():
    """Truncation must not masquerade as 'no promoter': public alone is only 55% of the shares here."""
    body = re.sub(rb'<in-bse-shp:\w+ contextRef="' + PROM.encode() + rb'"[^>]*>[^<]*</in-bse-shp:\w+>', b"", xml("20MICRONS"))
    assert kind(s.parse_shp_filing, body) == ErrorKind.PARSE_FAIL


def test_broken_filings():
    for body in (b"nope", b'<?xml version="1.0"?><!DOCTYPE a [<!ENTITY x "y">]><a/>', b'<?xml version="1.0"?><r/>'):
        assert kind(s.parse_shp_filing, body) == ErrorKind.PARSE_FAIL
    body = xml("ARSHIYA")
    assert kind(s.parse_shp_filing, re.sub(rb'<in-bse-shp:DateOfReport[^>]*>[^<]*</in-bse-shp:DateOfReport>', b"", body)) == ErrorKind.PARSE_FAIL
    assert kind(s.parse_shp_filing, set_fact(body, PROM, "NumberOfShares", "12x")) == ErrorKind.PARSE_FAIL
    no_prom = re.sub(rb'<in-bse-shp:\w+ contextRef="' + PROM.encode() + rb'"[^>]*>[^<]*</in-bse-shp:\w+>', b"", body)
    assert kind(s.parse_shp_filing, no_prom) == ErrorKind.PARSE_FAIL


# --- master list ----------------------------------------------------------------------------------
def test_master_parses_real_rows():
    rows = s.parse_shp_master(master())
    assert len(rows) == 59
    v = next(r for r in rows if r["symbol"] == "VEDL" and r["as_on"] == "2026-09-30")
    assert (v["isin"], v["isin_valid"], v["promoter_pct"], v["public_pct"], v["revised"]) == ("INE205A01017", True, 54.72, 45.12, False)
    assert v["broadcast"] == "2026-10-16T17:21:44" and v["xbrl_url"].startswith("https://nsearchives.nseindia.com/corporate/xbrl/SHP_")
    assert any(r["revised"] for r in rows) and any(r["isin"] is None for r in rows)
    assert next(r for r in rows if r["symbol"] == "KIRIINDUS" and r["as_on"] == "2026-11-07")  # event-dated filing


def test_master_rejects_bad_rows():
    assert kind(s.parse_shp_master, b'[]') == ErrorKind.EMPTY
    assert kind(s.parse_shp_master, b'[{"symbol": "X", "date": "30-SEP-2026"}]') == ErrorKind.PARSE_FAIL
    assert kind(s.parse_shp_master, b"<html>blocked</html>") == ErrorKind.PARSE_FAIL


def test_latest_filing_per_company_with_qoq_change():
    out = {r["symbol"]: r for r in s.latest_filings(s.parse_shp_master(master()))}
    assert len(out) == len({r["isin"] or r["symbol"] for r in s.parse_shp_master(master())})
    v = out["VEDL"]
    assert (v["as_on"], v["prev_as_on"], v["promoter_change_pp"]) == ("2026-09-30", "2026-06-30", 0.0)
    k = out["KIRIINDUS"]  # newest is event-dated 07-Nov; compared with the last quarter-end
    assert (k["as_on"], k["prev_as_on"]) == ("2026-11-07", "2026-09-30")


def _row(sym, as_on, prom, broadcast, revised=False, remarks=None):
    return {"record_id": f"{sym}{as_on}{broadcast}", "symbol": sym, "company": sym, "isin": None, "isin_valid": None, "as_on": as_on,
            "broadcast": broadcast, "revised": revised, "promoter_pct": prom, "public_pct": 100 - prom, "xbrl_url": "https://x/y.xml",
            "remarks": remarks}


def test_revision_wins_and_promoter_change_is_a_subtraction():
    rows = [_row("AAA", "2026-06-30", 50.0, "2026-07-20T10:00:00"), _row("AAA", "2026-09-30", 48.0, "2026-10-20T10:00:00"),
            _row("AAA", "2026-09-30", 47.5, "2026-11-01T10:00:00", revised=True), _row("BBB", "2026-09-30", 60.0, "2026-10-18T09:00:00")]
    out = {r["symbol"]: r for r in s.latest_filings(rows)}
    assert out["AAA"]["promoter_pct"] == 47.5 and out["AAA"]["revised"] is True      # the revision, not the original
    assert out["AAA"]["promoter_change_pp"] == -2.5 and out["AAA"]["prev_as_on"] == "2026-06-30"
    assert out["BBB"]["promoter_change_pp"] is None and out["BBB"]["prev_as_on"] is None  # no earlier quarter to compare


def test_change_flags_mark_artefacts_but_leave_real_moves_alone():
    rows = [
        _row("REAL", "2026-06-30", 50.0, "2026-07-20T10:00:00"), _row("REAL", "2026-09-30", 48.0, "2026-10-20T10:00:00"),
        # OMKAR-like: the Q1 quarter has two contradictory rows, the later broadcast says 95%
        _row("OMK", "2026-06-30", 4.94, "2026-07-10T10:00:00"), _row("OMK", "2026-06-30", 95.0, "2026-07-21T10:00:00", remarks="Clarification Note: ..."),
        _row("OMK", "2026-09-30", 4.94, "2026-10-18T10:00:00"),
        # SABEVENTS-like: promoter wiped out, filer note about suspension / capital reduction
        _row("SAB", "2026-06-30", 26.43, "2026-07-10T10:00:00"), _row("SAB", "2026-09-30", 0.0, "2026-10-18T10:00:00", remarks="ISIN suspended pending capital reduction"),
        _row("BIG", "2026-06-30", 70.0, "2026-07-10T10:00:00"), _row("BIG", "2026-09-30", 55.0, "2026-10-18T10:00:00"),
    ]
    out = {r["symbol"]: r for r in s.latest_filings(rows)}
    assert out["REAL"]["change_flags"] == []
    assert "conflicting_rows_2026-06-30" in out["OMK"]["change_flags"] and "filer_note" in out["OMK"]["change_flags"]
    assert "implausible_swing" in out["OMK"]["change_flags"] or abs(out["OMK"]["promoter_change_pp"]) < s.SWING_PP
    assert set(out["SAB"]["change_flags"]) == {"filer_note", "implausible_swing"}
    assert out["BIG"]["change_flags"] == ["implausible_swing"]
    assert out["REAL"]["promoter_change_pp"] == -2.0   # the number itself is never altered or hidden


def test_quarter_helpers():
    assert s.quarter_end_on_or_before(date(2026, 11, 8)) == date(2026, 9, 30)
    assert s.quarter_end_on_or_before(date(2026, 9, 30)) == date(2026, 9, 30)
    assert s.quarter_end_on_or_before(date(2027, 1, 5)) == date(2026, 12, 31)
    assert s.quarter_end_on_or_before(date(2027, 3, 30)) == date(2026, 12, 31)
    assert s.is_quarter_end(date(2026, 6, 30)) and not s.is_quarter_end(date(2026, 11, 7))


def test_coverage_canary_checks_quarter_end_not_newest_record():
    rows = s.parse_shp_master(master())          # 47 companies for Sep-30, 5 for Jun-30, plus event-dated rows
    assert s.check_coverage(rows, TODAY, min_companies=40) == date(2026, 9, 30)          # 39 days after quarter-end: Sep must be there
    assert kind(s.check_coverage, rows, TODAY) == ErrorKind.STALE                       # real threshold: 47 is nowhere near enough
    assert s.check_coverage(rows, date(2026, 10, 15), min_companies=5) == date(2026, 6, 30)  # still inside the filing window
    assert s.check_coverage(rows, date(2027, 1, 20), min_companies=1) == date(2026, 9, 30)    # Dec-31 window still open: Sep must be there
    assert kind(s.check_coverage, rows, date(2027, 2, 15), min_companies=1) == ErrorKind.STALE  # window closed, Dec-31 never arrived
    event_only = [r for r in rows if r["as_on"] == "2026-11-07"]
    assert kind(s.check_coverage, event_only, TODAY, min_companies=1) == ErrorKind.STALE   # a fresh newest row alone proves nothing


# --- providers ------------------------------------------------------------------------------------
class Net:
    def __init__(self, body=None, error=None):
        self.body, self.error, self.api, self.gets = body, error, [], []

    def api_get(self, path, **params):
        self.api.append((path, params))
        if self.error:
            raise self.error
        return Fetched("https://www.nseindia.com/api/" + path, self.body, 200)

    def get(self, url, profile, *, expect="text", **kw):
        self.gets.append((url, profile.name, expect))
        if self.error:
            raise self.error
        return Fetched(url, self.body, 200)


@pytest.fixture
def net(monkeypatch):
    def install(**kw):
        n = Net(**kw)
        monkeypatch.setattr(s.nse_live, "api_get", n.api_get)
        monkeypatch.setattr(s, "get", n.get)
        return n
    return install


def test_shp_master_provider(net):
    n = net(body=master())
    with Store() as st:
        r = s.shp_master(today=TODAY, store=st, min_companies=40)
        assert r.ok and r.as_of == "2026-09-30" and len(r.data) == len({x["isin"] or x["symbol"] for x in s.parse_shp_master(master())})
        assert st.has_blob("shp_master", TODAY)
    assert n.api == [("corporate-share-holdings-master", {"index": "equities", "from_date": "01-07-2026", "to_date": "08-11-2026"})]


def test_shp_master_stale_and_errors_are_typed(net):
    net(body=master())
    assert s.shp_master(today=TODAY).error.kind == ErrorKind.STALE      # default min_companies: 47 is not a quarter
    net(error=DrishtiError(ErrorKind.BLOCKED, "403"))
    assert s.shp_master(today=TODAY).error.kind == ErrorKind.BLOCKED
    net(body=b"<html>")
    assert s.shp_master(today=TODAY).error.kind == ErrorKind.PARSE_FAIL


def test_shp_filing_provider(net):
    n = net(body=xml("VEDL"))
    r = s.shp_filing("https://nsearchives.nseindia.com/corporate/xbrl/SHP_1.xml")
    assert r.ok and r.as_of == "2026-09-30" and r.data["ndu_shares"] == 107342705
    assert n.gets == [("https://nsearchives.nseindia.com/corporate/xbrl/SHP_1.xml", "nse_archive", "xml")]
    net(error=DrishtiError(ErrorKind.BLOCKED_HTML, "html"))
    assert s.shp_filing("https://x/y.xml").error.kind == ErrorKind.BLOCKED_HTML
    net(body=b"garbage")
    assert s.shp_filing("https://x/y.xml").error.kind == ErrorKind.PARSE_FAIL


# --- the live validation script (so its first real run cannot die on a typo) -----------------------
def _load_validate_live():
    import importlib.util
    path = Path(__file__).resolve().parents[1] / "phase0" / "scout" / "validate_live.py"
    spec = importlib.util.spec_from_file_location("validate_live", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_summarise_shp_on_fixtures():
    vl = _load_validate_live()
    filings = [s.parse_shp_filing(xml(x)) for x in ("ALPSINDUS", "ARSHIYA", "VEDL", "20MICRONS")]
    summ = vl.summarise_shp(filings)
    assert (summ["parsed"], summ["with_encumbrance"], summ["pledge_only"], summ["ndu_or_other"]) == (4, 3, 2, 1)
    assert summ["flags_inconsistent"] == [] and summ["no_promoter"] == [] and summ["versions"] == {"1.2": 4}
    assert summ["worst_encumbered_pct_of_promoter"][0][0] == "ARSHIYA"


def test_validate_live_runs_end_to_end_on_fakes(monkeypatch, tmp_path, capsys):
    from drishti.core.envelope import Result
    vl = _load_validate_live()
    monkeypatch.setattr(vl, "OUT", tmp_path / "out.json")
    ok = Result(data=[{"x": 1}], as_of="2026-11-06")
    monkeypatch.setattr(vl.u, "latest", lambda fn, **k: ok)
    for mod, names in ((vl.nse_archive, ("bulk_deals", "block_deals")),):
        for n in names:
            monkeypatch.setattr(mod, n, lambda **k: ok)
    rows = s.latest_filings(s.parse_shp_master(master()))
    monkeypatch.setattr(vl.nse_shp, "shp_master", lambda **k: Result(data=rows, as_of="2026-09-30"))
    monkeypatch.setattr(vl.nse_shp, "shp_filing", lambda url: Result(data=s.parse_shp_filing(xml("ALPSINDUS")), as_of="2026-09-30"))
    ev = [{"direction": "BUY", "category": "Director", "holding_reconciles": True, "isin_valid": True, "form_version": "2.0"}]
    monkeypatch.setattr(vl.nse_pit, "insider_trades", lambda d: Result(
        data={"events": ev, "filings": 1, "failed": [], "duplicates_dropped": 0}, as_of="2026-11-07"))
    monkeypatch.setattr(vl.nse_flows, "fii_dii_cash", lambda **k: Result(
        data={"date": "2026-11-06", "fii": {"net": 1.0}, "dii": {"net": -1.0}}, as_of="2026-11-06"))
    monkeypatch.setattr(vl.fred, "fred_series", lambda sid, **k: Result(
        data={"n_total": 3, "latest": {"value": 1.5}}, as_of="2026-10-30"))
    row = {"label": "USD index", "open_interest": 10, "lev_money": {"net": 5, "net_change": 1}, "asset_mgr": {"net": -5}}
    monkeypatch.setattr(vl.cftc, "cftc_tff", lambda **k: Result(data=[row], as_of="2026-11-03"))
    assert vl.main(["--shp-sample", "3"]) == 0
    out = capsys.readouterr().out
    assert "fii_dii_cash" in out and "cftc_tff" in out and "fred_WALCL" in out
    assert "OK   shp_master" in out and "shp_filings" in out and "insider_trades_7d" in out and "0 failed" in out
    assert json.loads((tmp_path / "out.json").read_text())["report"]


def test_validate_live_reports_failures_and_exits_nonzero(monkeypatch, tmp_path, capsys):
    from drishti.core.envelope import Result
    vl = _load_validate_live()
    monkeypatch.setattr(vl, "OUT", tmp_path / "out.json")
    rows = s.latest_filings(s.parse_shp_master(master()))
    monkeypatch.setattr(vl.nse_shp, "shp_master", lambda **k: Result(data=rows, as_of="2026-09-30"))
    monkeypatch.setattr(vl.nse_shp, "shp_filing", lambda url: Result(error=DrishtiError(ErrorKind.PARSE_FAIL, "no promoter context")))
    monkeypatch.setattr(vl, "save_failure", lambda *a: "(not saved in tests)")   # never touch the network from a unit test
    assert vl.main(["--only", "shp", "--shp-sample", "3"]) == 1
    out = capsys.readouterr().out
    assert "FAIL shp_filings" in out and "no promoter context" in out

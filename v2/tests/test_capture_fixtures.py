"""Offline test of phase0/scout/capture_fixtures.py: drives the whole orchestrator with a fake network."""
import importlib.util
import json
from datetime import date
from pathlib import Path

import pytest

from drishti.core import canary, fetch
from drishti.core.envelope import DrishtiError, ErrorKind

SCRIPT = Path(__file__).resolve().parents[1] / "phase0" / "scout" / "capture_fixtures.py"
TODAY = date(2026, 11, 8)  # Sunday after the 6 Nov close

SHP = ('<xbrli:xbrl><in-bse-shp:WhetherAnySharesHeldByPromotersAreEncumberedUnderPledged contextRef="MainI">{p}'
       '</in-bse-shp:WhetherAnySharesHeldByPromotersAreEncumberedUnderPledged>'
       '<in-bse-shp:WhetherAnySharesHeldByPromotersAreEncumberedUnderNonDisposalUndertaking contextRef="MainI">{n}'
       '</in-bse-shp:WhetherAnySharesHeldByPromotersAreEncumberedUnderNonDisposalUndertaking>'
       '<in-bse-shp:EncumberedSharesHeldAsPercentageOfTotalNumberOfShares contextRef="ShareholdingOfPromoterAndPromoterGroup_ContextI">0.25'
       '</in-bse-shp:EncumberedSharesHeldAsPercentageOfTotalNumberOfShares></xbrli:xbrl>')


@pytest.fixture
def cap(monkeypatch):
    spec = importlib.util.spec_from_file_location("capture_fixtures", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "today_ist", lambda: TODAY)
    monkeypatch.setattr(canary, "today_ist", lambda: TODAY)
    return mod


def install(monkeypatch, routes):
    """routes: [(predicate(url), body|DrishtiError)]; first match wins; no match = 403 BLOCKED."""
    calls = []

    def fake_get(url, profile, *, expect="text", headers=None, ttl=0, on_blocked=None):
        calls.append(url)
        for pred, body in routes:
            if pred(url):
                if isinstance(body, DrishtiError):
                    raise body
                return fetch.Fetched(url, body if isinstance(body, bytes) else body.encode(), 200)
        raise DrishtiError(ErrorKind.BLOCKED, f"HTTP 403 {url}")

    monkeypatch.setattr(fetch, "get", fake_get)
    return calls


def has(sub):
    return lambda u: sub in u


def manifest(out):
    m = json.loads((out / "MANIFEST.json").read_text(encoding="utf-8"))
    return m, {i["name"]: i for i in m["items"]}


def test_archive_and_nse_live_end_to_end(cap, monkeypatch, tmp_path):
    nf = DrishtiError(ErrorKind.NOT_FOUND, "HTTP 404")
    pit = {"data": [{"symbol": f"S{i}", "broadcastDateTime": "06-Nov-2026 10:00:00",
                     "ixbrl": f"https://nsearchives.nseindia.com/corporate/ixbrl/IT_{i}_x.html",
                     "xmlFileName": f"https://nsearchives.nseindia.com/corporate/ixbrl/IT_{i}_x.xml"} for i in range(5)]}
    master = [{"symbol": s, "date": "30-Sep-2026", "xbrl": f"https://nsearchives.nseindia.com/xbrl/SHP_{s}.xml"}
              for s in ("AAA", "BBB", "CCC", "DDD")]
    # an event filing (new listing) with an odd as-on date must not be mistaken for "the latest quarter"
    master.append({"symbol": "ZZZ", "date": "07-NOV-2026", "xbrl": "https://nsearchives.nseindia.com/xbrl/SHP_ZZZ.xml"})
    routes = [
        (has("BhavCopy_NSE_CM_0_0_0_20261106"), nf),                       # Friday not published yet -> walk back
        (has("BhavCopy_NSE_CM_0_0_0_20261105"), b"PK-cm"),
        (has("BhavCopy_NSE_FO_0_0_0_20261105"), b"PK-fo"),
        (has("fao_participant_oi_05112026"), "Client,x\n"),
        (has("ind_close_all_05112026"), "Index Name,x\n"),
        (has("/content/fo/fo_secban"), nf),                                # first ban-list candidate is wrong
        (has("/sec_ban/fo_secban_05112026"), "KAYNES\n"),
        (has("equities/bulk.csv"), "Date,Symbol\n"),
        (has("EQUITY_L.csv"), "SYMBOL,ISIN\n"),
        (lambda u: u == "https://www.nseindia.com/", DrishtiError(ErrorKind.BLOCKED, "HTTP 403")),  # homepage 403 is cosmetic
        (lambda u: u.endswith("/option-chain"), "<html>ok</html>"),
        (has("fiidiiTradeReact"), json.dumps([{"date": "06-Nov-2026", "category": "FII/FPI"}])),
        (has("corporates-pit-gg"), json.dumps(pit)),
        (has("corporate/ixbrl/"), "<html>filing</html>"),
        (has("symbol=VEDL"), json.dumps([{"date": "30-Sep-2026", "xbrl": "https://nsearchives.nseindia.com/xbrl/SHP_VEDL.xml"}])),
        (lambda u: "share-holdings-master" in u and "from_date" in u, json.dumps(master)),
        (has("SHP_VEDL"), SHP.format(p="false", n="true")),
        (has("SHP_AAA"), SHP.format(p="true", n="false")),
        (has("SHP_BBB"), SHP.format(p="false", n="false")),
        (has("SHP_CCC"), SHP.format(p="false", n="true")),
        (has("SHP_DDD"), SHP.format(p="true", n="true")),
        (has("option-chain-contract-info"), json.dumps({"expiryDates": ["10-Nov-2026", "17-Nov-2026"]})),
        (has("option-chain-v3"), json.dumps({"records": {"data": []}})),
    ]
    calls = install(monkeypatch, routes)

    rc = cap.main(["--date", "20261108", "--outdir", str(tmp_path), "--only", "archive,nse_live,shp", "--shp-scan", "10"])
    assert rc == 0
    m, items = manifest(tmp_path)

    # walk-back across a weekend and an unpublished Friday, anchor date shared by the other dated files
    assert m["trade_date"] == "20261105"
    assert items["cm_bhavcopy"]["ok"] and "walked back from 20261106" in items["cm_bhavcopy"]["note"]
    assert (tmp_path / "nse_archive" / "BhavCopy_NSE_CM_20261105.csv.zip").read_bytes() == b"PK-cm"
    assert items["fo_bhavcopy"]["ok"] and items["participant_oi"]["ok"] and items["ind_close_all"]["ok"]
    # ban-list: the wrong path guess is recorded (tells us which path is right), the second one wins
    assert items["fo_secban_cand1"]["error_kind"] == "NOT_FOUND" and items["fo_secban_cand2"]["ok"]
    # failure of one item (block.csv has no route) is recorded, not fatal
    assert not items["block_deals"]["ok"] and items["block_deals"]["error_kind"] == "BLOCKED"
    # PIT + freshness canary + filing documents
    assert items["pit_gg_7d"]["freshness"].startswith("OK newest 2026-11-06")
    assert items["fii_dii"]["freshness"].startswith("OK")
    assert sum(1 for n in items if n.startswith("pit_filing_") and n.endswith("_ixbrl")) == 3
    # contract info -> nearest expiry -> v3 chain
    assert "expiry=10-Nov-2026" in items["option_chain_v3_nifty"]["url"]
    # encumbrance scan: pledged examples + one clean control saved; NDU-only company is neither; temp file removed
    shp = tmp_path / "shp"
    assert (shp / "shp_vedl_latest.xml").exists()
    assert (shp / "shp_pledged_AAA_20260930.xml").exists() and (shp / "shp_pledged_DDD_20260930.xml").exists()
    assert (shp / "shp_clean_BBB_20260930.xml").exists()
    assert not (shp / "shp_pledged_CCC_20260930.xml").exists() and not (shp / "_scan.tmp").exists()
    assert "WhetherAnySharesHeldByPromotersAreEncumberedUnderPledged[MainI]=true" in items["shp_pledged_AAA"]["note"]
    assert any("EMPTY JAR" in n for n in m["notes"])  # fake network sets no cookies: the warning must show
    assert len(calls) > 30
    # scan used the most common as-on date (30-SEP), ignored the 07-NOV outlier, and reports the base rate
    assert not any("SHP_ZZZ" in c for c in calls)
    assert any(n.startswith("pledged in sample: 2 of 4 scanned (quarter 2026-09-30") for n in m["notes"])
    # a partial rerun merges into the manifest instead of replacing it
    assert cap.main(["--date", "20261108", "--outdir", str(tmp_path), "--only", "shp", "--shp-scan", "10"]) == 0
    m2, items2 = manifest(tmp_path)
    assert "cm_bhavcopy" in items2 and "fii_dii" in items2 and m2["trade_date"] == "20261105"


def test_every_failure_is_recorded_and_nothing_aborts(cap, monkeypatch, tmp_path):
    fred_ok = "observation_date,DTWEXBGS\n2026-11-05,120.1\n2026-11-06,.\n"
    fred_old = "observation_date,WALCL\n2026-01-01,6500000\n"
    install(monkeypatch, [(has("id=DTWEXBGS"), fred_ok), (has("id=WALCL"), fred_old)])
    rc = cap.main(["--date", "20261106", "--outdir", str(tmp_path), "--only", "bse,macro,sec,screener"])
    assert rc == 0
    m, items = manifest(tmp_path)
    assert items["fred_DTWEXBGS"]["ok"] and items["fred_DTWEXBGS"]["freshness"].startswith("OK newest 2026-11-05")
    assert items["fred_WALCL"]["freshness"].startswith("STALE")          # saved, but flagged
    for name in ("bse_bhavcopy_udiff", "bse_scrip_header", "bse_announcements", "cftc_finfut", "screener_reliance"):
        assert not items[name]["ok"] and items[name]["error_kind"] == "BLOCKED", name
    assert not any(n.startswith("sec_") for n in items)                 # skipped without --sec-ua
    assert any("sec group skipped" in n for n in m["notes"])


def test_unknown_group_is_rejected(cap, tmp_path):
    with pytest.raises(SystemExit):
        cap.main(["--outdir", str(tmp_path), "--only", "nope"])

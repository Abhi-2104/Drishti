"""Insider-trade (PIT) parsing and provider flow, on real filings from 2026-11-06/07."""
from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

import pytest

from drishti.core import canary
from drishti.core.envelope import DrishtiError, ErrorKind
from drishti.core.fetch import Fetched
from drishti.providers import nse_pit as p

FIX = Path(__file__).parent / "fixtures" / "nse_pit"
KRONOX_NEW = "IT_22833_WebXMLFile_20261107_230727135.xml"
KRONOX_OLD = "IT_22833_WebXMLFile_20261107_230425023.xml"
INDOSTAR = "IT_16166_WebXMLFile_20261107_215948860.xml"
TODAY = date(2026, 11, 8)


def xml(name: str) -> bytes:
    return (FIX / name).read_bytes()


def kind(fn, *a, **k) -> ErrorKind:
    with pytest.raises(DrishtiError) as ei:
        fn(*a, **k)
    return ei.value.kind


# --- filing index ---------------------------------------------------------------------------------
def test_list_parses_real_index():
    rows = p.parse_pit_list((FIX / "pit_gg_list.json").read_bytes())
    assert len(rows) == 12
    r = rows[0]
    assert (r["app_id"], r["symbol"], r["company"], r["regulation"], r["submission_type"]) == \
           ("4250", "KRONOX", "KRONOX LAB SCIENCES LIMITED", "Regulation 7 (2)", "Original")
    assert r["broadcast"] == "2026-11-07T23:07:27" and r["prev_app_id"] is None
    assert r["xml_url"].endswith(KRONOX_NEW) and r["ixbrl_url"].endswith("_WEB.html")


def test_list_rejects_incomplete_or_empty():
    assert kind(p.parse_pit_list, b'{"data": []}') == ErrorKind.EMPTY
    assert kind(p.parse_pit_list, b'{"data": [{"appId": "1", "symbol": "X"}]}') == ErrorKind.PARSE_FAIL
    assert kind(p.parse_pit_list, b"<html>blocked</html>") == ErrorKind.PARSE_FAIL


# --- one filing -----------------------------------------------------------------------------------
def test_kronox_promoter_pledge():
    (e,) = p.parse_pit_filing(xml(KRONOX_NEW))
    assert (e["symbol"], e["scrip_code"], e["isin"], e["isin_valid"]) == ("KRONOX", "544187", "INE0ATZ01017", True)
    assert (e["category"], e["person"], e["txn_type"], e["direction"]) == ("Promoter", "INDO BORAX & CHEMICALS LIMITED", "Pledge", "PLEDGE")
    assert (e["quantity"], e["value_inr"], e["held_pre_shares"], e["held_post_shares"]) == (5196310, 536363118.0, 0, 23844000)
    assert e["held_post_pct"] == 64.26 and e["held_pre_pct"] == 0.0  # XML fraction 0.6426 == 64.26% (checked against the HTML)
    assert (e["from_date"], e["to_date"], e["intimation_date"], e["filing_date"]) == ("2026-11-03", "2026-11-03", "2026-11-05", "2026-11-06")
    assert (e["mode"], e["exchange"], e["regulation"], e["revised"], e["form_version"]) == ("Pledge Creation", "NA", "Regulation 7 (2)", False, "2.0")
    assert e["holding_reconciles"] is None  # a pledge is not a change in holding


def test_indostar_director_buy_reconciles():
    (e,) = p.parse_pit_filing(xml(INDOSTAR))
    assert (e["category"], e["person"], e["direction"], e["quantity"], e["value_inr"]) == ("Director", "Hemant Kaul", "BUY", 998, 194219.0)
    assert (e["held_pre_shares"], e["held_post_shares"], e["exchange"], e["mode"]) == (7002, 8000, "NSE", "Market Purchase")
    assert e["holding_reconciles"] is True
    assert e["isin"] == "INE896L01010" and e["isin_valid"]


def test_holding_mismatch_is_flagged_not_dropped():
    bad = xml(INDOSTAR).replace(b">8000<", b">9000<")
    (e,) = p.parse_pit_filing(bad)
    assert e["holding_reconciles"] is False and e["held_post_shares"] == 9000


def test_sell_reconciliation():
    t = xml(INDOSTAR).replace(b">Buy<", b">Sell<").replace(b">7002<", b">8998<").replace(b">8000<", b">8000<")
    (e,) = p.parse_pit_filing(t)
    assert e["direction"] == "SELL" and e["holding_reconciles"] is True  # 8998 - 998 == 8000


def _two_disclosures() -> bytes:
    t = xml(INDOSTAR).decode()
    facts = re.findall(r"<in-bse-co:\w+ contextRef=\"Disclosure1\".*?</in-bse-co:\w+>", t)
    clone = "".join(f.replace('contextRef="Disclosure1"', 'contextRef="Disclosure2"').replace("Hemant Kaul", "Jane Doe")
                    .replace(">Director<", ">Promoter Group<") for f in facts)
    return t.replace("</xbrli:xbrl>", clone + "</xbrli:xbrl>").encode()


def test_one_filing_with_several_persons():
    evs = p.parse_pit_filing(_two_disclosures())
    assert [(e["person"], e["category"]) for e in evs] == [("Hemant Kaul", "Director"), ("Jane Doe", "Promoter Group")]
    assert all(e["symbol"] == "INDOSTAR" for e in evs)


def test_unknown_transaction_type_is_kept_as_other():
    (e,) = p.parse_pit_filing(xml(INDOSTAR).replace(b">Buy<", b">Gift Received<"))
    assert e["direction"] == "OTHER" and e["txn_type"] == "Gift Received"


def test_invalid_isin_is_flagged_not_fatal():
    (e,) = p.parse_pit_filing(xml(INDOSTAR).replace(b"INE896L01010", b"INE896L01011"))
    assert e["isin_valid"] is False and e["isin"] is None and e["isin_raw"] == "INE896L01011"  # never join on a bad ISIN


@pytest.mark.parametrize("body,needle", [
    (b"not xml at all", "not valid XML"),
    (b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><x>&a;</x>', "DTD"),
    (b'<?xml version="1.0"?><root/>', "MainI"),
])
def test_broken_filings_are_parse_fail(body, needle):
    with pytest.raises(DrishtiError, match=needle) as ei:
        p.parse_pit_filing(body)
    assert ei.value.kind == ErrorKind.PARSE_FAIL


def test_filing_without_disclosures_or_person_or_with_garbage_number():
    t = xml(INDOSTAR)
    no_disc = re.sub(rb'<in-bse-co:\w+ contextRef="Disclosure1".*?</in-bse-co:\w+>', b"", t)
    assert kind(p.parse_pit_filing, no_disc) == ErrorKind.PARSE_FAIL
    assert kind(p.parse_pit_filing, t.replace(b"Hemant Kaul", b"")) == ErrorKind.PARSE_FAIL
    assert kind(p.parse_pit_filing, t.replace(b">998<", b">9x8<")) == ErrorKind.PARSE_FAIL


# --- dedupe ---------------------------------------------------------------------------------------
def test_same_trade_filed_twice_is_one_event_latest_filing_wins():
    a = p.parse_pit_filing(xml(KRONOX_OLD))[0] | {"filed_at": "2026-11-07T23:04:25", "app_id": "4249"}
    b = p.parse_pit_filing(xml(KRONOX_NEW))[0] | {"filed_at": "2026-11-07T23:07:27", "app_id": "4250"}
    out = p.dedupe_events([a, b])
    assert len(out) == 1 and out[0]["app_id"] == "4250"
    assert len(p.dedupe_events([b, a])) == 1 and p.dedupe_events([b, a])[0]["app_id"] == "4250"


def test_different_trades_are_kept():
    k = p.parse_pit_filing(xml(KRONOX_NEW))[0]
    other = dict(k, quantity=1)
    assert len(p.dedupe_events([k, other])) == 2


# --- provider flow --------------------------------------------------------------------------------
def listing(rows=3, **patch) -> bytes:
    data = json.loads((FIX / "pit_gg_list.json").read_bytes())["data"][:rows]
    for r in data:
        r.update(patch.get(r["appId"], {}))
    return json.dumps({"data": data}).encode()


class Net:
    def __init__(self, list_body, xmls=None, list_error=None, xml_errors=None):
        self.list_body, self.list_error = list_body, list_error
        self.xmls = {k: xml(k) for k in (KRONOX_NEW, KRONOX_OLD, INDOSTAR)} if xmls is None else xmls
        self.xml_errors, self.api_calls, self.xml_calls = xml_errors or {}, [], []

    def api_get(self, path, **params):
        self.api_calls.append((path, params))
        if self.list_error:
            raise self.list_error
        return Fetched("https://www.nseindia.com/api/" + path, self.list_body, 200)

    def get(self, url, profile, *, expect="text", **kw):
        name = url.rsplit("/", 1)[-1]
        self.xml_calls.append((name, profile.name, expect))
        if name in self.xml_errors:
            raise self.xml_errors[name]
        if name not in self.xmls:
            raise DrishtiError(ErrorKind.NOT_FOUND, f"HTTP 404 {url}")
        return Fetched(url, self.xmls[name], 200)


@pytest.fixture
def net(monkeypatch):
    monkeypatch.setattr(canary, "today_ist", lambda: TODAY)

    def install(*a, **k):
        n = Net(*a, **k)
        monkeypatch.setattr(p.nse_live, "api_get", n.api_get)
        monkeypatch.setattr(p, "get", n.get)
        return n
    return install


def test_insider_trades_end_to_end(net):
    n = net(listing(3))
    r = p.insider_trades(7, today=TODAY)
    assert r.ok and r.as_of == "2026-11-07"
    d = r.data
    assert d["filings"] == 3 and d["failed"] == [] and d["duplicates_dropped"] == 1
    assert sorted((e["symbol"], e["direction"]) for e in d["events"]) == [("INDOSTAR", "BUY"), ("KRONOX", "PLEDGE")]
    kr = next(e for e in d["events"] if e["symbol"] == "KRONOX")
    assert kr["app_id"] == "4250" and kr["filed_at"] == "2026-11-07T23:07:27"  # the later of the two duplicate filings
    assert n.api_calls == [("corporates-pit-gg", {"index": "equities", "from_date": "01-11-2026", "to_date": "08-11-2026"})]
    assert {c[1:] for c in n.xml_calls} == {("nse_archive", "xml")}


def test_skip_known_filings_and_superseded_ones(net):
    n = net(listing(3, **{"4250": {"prevAppId": "4249", "typeOfSubmission": "Revision"}}))
    r = p.insider_trades(7, today=TODAY, skip_app_ids={"4240"})
    assert [c[0] for c in n.xml_calls] == [KRONOX_NEW]  # 4249 superseded by the revision, 4240 already stored
    assert r.ok and r.data["filings"] == 1 and len(r.data["events"]) == 1


def test_partial_failure_is_reported_and_too_many_failures_is_an_error(net):
    net(listing(12))  # only 3 of the 12 filings have saved XML; the rest 404
    r = p.insider_trades(7, today=TODAY, max_failed_frac=1.0)
    assert r.ok and len(r.data["failed"]) == 9 and {f["kind"] for f in r.data["failed"]} == {"NOT_FOUND"}
    r = p.insider_trades(7, today=TODAY)  # default: >20% failed
    assert not r.ok and r.error.kind == ErrorKind.NOT_FOUND and "9/12" in r.error.detail
    assert len(r.data["events"]) == 2  # what did parse is still handed back


def test_blocked_xml_counts_as_failed_with_its_kind(net):
    net(listing(3), xml_errors={INDOSTAR: DrishtiError(ErrorKind.BLOCKED_HTML, "html page")})
    r = p.insider_trades(7, today=TODAY)
    assert not r.ok and r.error.kind == ErrorKind.BLOCKED_HTML
    assert r.data["failed"][0]["symbol"] == "INDOSTAR"


def test_frozen_index_is_stale_not_zero_trades(net):
    net(listing(3))
    r = p.insider_trades(7, today=date(2026, 12, 30))
    assert not r.ok and r.error.kind == ErrorKind.STALE


def test_index_errors_are_typed(net):
    net(b"", list_error=DrishtiError(ErrorKind.BLOCKED, "403"))
    assert p.insider_trades(today=TODAY).error.kind == ErrorKind.BLOCKED
    net(b'{"data": []}')
    assert p.insider_trades(today=TODAY).error.kind == ErrorKind.EMPTY
    net(b"<html>")
    assert p.insider_trades(today=TODAY).error.kind == ErrorKind.PARSE_FAIL


def test_pledge_revoke_and_invoke_are_not_other():
    """NSE writes 'Pledge Revoke' / 'Pledge Invoke' (live run: 9 events landed in OTHER before this was fixed)."""
    d = p._direction
    assert [d(x) for x in ("Buy", "Sell", "Pledge", "Pledge Revoke", "Pledge Invoke", "Revocation of Pledge", "Invocation")] == \
        ["BUY", "SELL", "PLEDGE", "REVOKE", "INVOKE", "REVOKE", "INVOKE"]
    assert d("Gift") == "OTHER"


def test_placeholder_isin_does_not_become_a_join_key():
    body = re.sub(rb'(ISINCode[^>]*>)[^<]*', rb"\1ZZZ999Z99999", xml(KRONOX_NEW))
    (e,) = p.parse_pit_filing(body)
    assert e["isin"] is None and e["isin_raw"] == "ZZZ999Z99999" and e["isin_valid"] is False and e["symbol"] == "KRONOX"

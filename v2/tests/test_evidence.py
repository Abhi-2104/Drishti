"""Evidence layer E1: verbatim-quote check, point-in-time queries, typed refusals."""
from __future__ import annotations

from drishti import evidence as ev
from drishti.core.envelope import ErrorKind
from drishti.store import Store

CALL = ("Moderator: Good evening and welcome to the Q2 FY27 earnings call. "
        "CFO: Our revenue grew 14% year on year. However, given the slowdown in export markets, we are revising our full-year revenue "
        "growth guidance to 8–10% from the earlier 12–15%. Margins should stay in the 18% range. "
        "Analyst: What about capex? CFO: We will defer the “phase two” expansion by about two quarters. ") * 2


def _src(st, published="2026-10-30", text=CALL):
    r = ev.add_source(st, text=text, kind="concall", title="Q2 FY27 call", published_at=published, isin="INE000A01011")
    assert r.ok
    return r.data["source_id"]


def test_quote_must_be_verbatim_after_normalising_quotes_dashes_and_spaces():
    st = Store()
    sid = _src(st)
    ok = ev.validate_claim(st, sid, 'revising our full-year revenue growth guidance to 8-10% from the earlier 12-15%')
    assert ok.ok and ok.data["found"] and ok.data["occurrences"] == 2
    curly = ev.validate_claim(st, sid, 'We will defer the "phase two"   expansion by about two quarters')
    assert curly.data["found"]
    para = ev.validate_claim(st, sid, "we are cutting our full-year revenue growth guidance to 8-10%")
    assert para.ok and not para.data["found"]
    case = ev.validate_claim(st, sid, "OUR REVENUE GREW 14% YEAR ON YEAR. HOWEVER")
    assert not case.data["found"] and "capitalisation" in case.data["why"]
    short = ev.validate_claim(st, sid, "revenue")
    assert not short.ok and short.error.kind == ErrorKind.REJECTED


def test_record_refuses_a_paraphrase_and_stores_a_real_quote():
    st = Store()
    sid = _src(st)
    bad = ev.record_evidence(st, source_id=sid, claim="Guidance was cut", quote="we have cut guidance sharply this year",
                             topic="guidance", polarity=-1, author="claude/test")
    assert not bad.ok and bad.error.kind == ErrorKind.REJECTED
    good = ev.record_evidence(st, source_id=sid, claim="Management cut FY revenue growth guidance from 12-15% to 8-10%",
                              quote="revising our full-year revenue growth guidance to 8-10% from the earlier 12-15%",
                              topic="guidance", polarity=-1, author="claude/test")
    assert good.ok
    again = ev.record_evidence(st, source_id=sid, claim="Management cut FY revenue growth guidance from 12-15% to 8-10%",
                               quote="revising our full-year revenue growth guidance to 8-10% from the earlier 12-15%",
                               topic="guidance", polarity=-1, author="claude/test")
    assert again.data["claim_id"] == good.data["claim_id"]
    assert len(st.query("SELECT * FROM evidence_claim")) == 1
    for kw in ({"topic": "vibes"}, {"polarity": 2}, {"author": " "}):
        args = dict(source_id=sid, claim="Margins guided to stay near 18%", quote="Margins should stay in the 18% range",
                    topic="margins", polarity=0, author="claude/test")
        args.update(kw)
        assert ev.record_evidence(st, **args).error.kind == ErrorKind.REJECTED


def test_query_is_point_in_time_and_same_text_is_one_source():
    st = Store()
    old = _src(st, published="2026-07-30", text=CALL.replace("Q2", "Q1"))
    new = _src(st, published="2026-10-30")
    assert _src(st, published="2026-10-30") == new                   # same document twice: one source
    ev.record_evidence(st, source_id=old, claim="Margins guided to stay around 18 percent", quote="Margins should stay in the 18% range",
                       topic="margins", polarity=0, author="claude/test")
    ev.record_evidence(st, source_id=new, claim="Phase two capex deferred by about two quarters",
                       quote='We will defer the "phase two" expansion by about two quarters', topic="capex", polarity=-1, author="claude/test")
    before = ev.query_evidence(st, isin="INE000A01011", on="2026-09-01")
    assert [c["topic"] for c in before.data["claims"]] == ["margins"] and before.data["sources_on_file"] == 1
    after = ev.query_evidence(st, isin="INE000A01011")
    assert [c["topic"] for c in after.data["claims"]] == ["capex", "margins"]          # newest document first
    assert ev.query_evidence(st, isin="INE000A01011", topic="capex").data["count"] == 1


def test_source_checks():
    st = Store()
    assert ev.add_source(st, text="too short", kind="concall", title="x", published_at="2026-01-01").error.kind == ErrorKind.REJECTED
    assert ev.add_source(st, text=CALL, kind="tweet", title="x", published_at="2026-01-01").error.kind == ErrorKind.REJECTED
    assert ev.add_source(st, text=CALL, kind="concall", title="x", published_at="soon").error.kind == ErrorKind.REJECTED
    assert ev.validate_claim(st, "src-nope", "a quote that is long enough").error.kind == ErrorKind.NOT_FOUND

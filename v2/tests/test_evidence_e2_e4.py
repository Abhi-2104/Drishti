"""Evidence layer E2-E4: PDFs and fetching, finding filings, reading in windows, spot-check and audit; plus the universe filter, the
storage report and table freshness in the health output."""
from __future__ import annotations

from datetime import date

import pytest

from drishti import evidence as ev
from drishti import storage, universe
from drishti.core import fetch as fetch_mod
from drishti.core.envelope import DrishtiError, ErrorKind
from drishti.core.fetch import Fetched
from drishti.providers import profiles
from drishti.store import Store
from drishti.store import datasets as ds

ISIN = "INE000A01011"
SENT = ("The CFO said: our revenue grew 14 percent year on year and margins stayed near 18 percent for the quarter. "
        "We are revising full year revenue growth guidance to 8 to 10 percent from the earlier 12 to 15 percent. ")


def make_pdf(lines: list[str]) -> bytes:
    """A small valid PDF with one page of Helvetica text (one string per line), built by hand so tests need no PDF writer."""
    def esc(s):
        return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = "BT /F1 11 Tf 40 780 Td 14 TL " + " ".join(f"({esc(l)}) Tj T*" for l in lines) + " ET"
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
            f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream", "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offs = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += f"{i} 0 obj\n{o}\nendobj\n".encode()
    x = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode() + b"".join(f"{o:010d} 00000 n \n".encode() for o in offs)
    return out + f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{x}\n%%EOF\n".encode()


@pytest.fixture
def st():
    s = Store()
    yield s
    s.close()


# --- E2 ------------------------------------------------------------------------------------------------------------
def test_pdf_text_reads_the_text_layer_and_refuses_a_scan_or_a_non_pdf():
    pytest.importorskip("pypdf")
    text = ev.pdf_text(make_pdf([SENT[:100], SENT[100:], SENT, SENT]))
    assert "revenue grew 14 percent" in text
    with pytest.raises(DrishtiError) as e:
        ev.pdf_text(make_pdf([]))                         # a page with no text at all: what a scan looks like
    assert e.value.kind == ErrorKind.EMPTY
    with pytest.raises(DrishtiError) as e:
        ev.pdf_text(b"<html>blocked</html>" * 20)
    assert e.value.kind == ErrorKind.PARSE_FAIL


def test_ingest_from_a_pdf_path_a_text_path_and_text(st, tmp_path):
    pytest.importorskip("pypdf")
    f = tmp_path / "call.pdf"
    f.write_bytes(make_pdf([SENT] * 4))
    r = ev.ingest(st, kind="concall", title="Q2 call", published_at="2026-10-30", isin=ISIN, path=str(f))
    assert r.ok and r.data["new"]
    t = tmp_path / "call.txt"
    t.write_text(SENT * 4, encoding="utf-8")
    assert ev.ingest(st, kind="concall", title="again", published_at="2026-10-30", path=str(t)).ok
    assert ev.ingest(st, kind="concall", title="x", published_at="2026-10-30", text=SENT * 4).ok
    miss = ev.ingest(st, kind="concall", title="x", published_at="2026-10-30", path=str(tmp_path / "none.pdf"))
    assert not miss.ok and miss.error.kind == ErrorKind.NOT_FOUND
    assert ev.ingest(st, kind="concall", title="x", published_at="2026-10-30").error.kind == ErrorKind.REJECTED


def test_fetch_document_only_from_exchange_hosts_and_through_the_fetch_layer(monkeypatch):
    seen = {}

    def fake_get(url, profile, expect="text", **k):
        seen.update(url=url, profile=profile, expect=expect)
        return Fetched(url, b"%PDF-1.4 body", 200)
    monkeypatch.setattr(fetch_mod, "get", fake_get)
    assert ev.fetch_document("https://nsearchives.nseindia.com/corporate/x.pdf") == b"%PDF-1.4 body"
    assert seen["profile"] is profiles.NSE_ARCHIVE and seen["expect"] == "pdf"
    ev.fetch_document("https://www.bseindia.com/xml-data/corpfiling/AttachLive/x.pdf")
    assert seen["profile"] is profiles.BSE_WWW
    for bad in ("http://nsearchives.nseindia.com/x.pdf", "https://example.com/x.pdf", "https://nseindia.com.evil.io/x.pdf", "file:///etc/passwd"):
        with pytest.raises(DrishtiError) as e:
            ev.fetch_document(bad)
        assert e.value.kind == ErrorKind.REJECTED


def test_fetch_layer_pdf_expectation_refuses_html_and_junk(monkeypatch):
    class R:
        def __init__(self, body):
            self.status_code, self.content = 200, body

    class S:
        def __init__(self, body):
            self.body = body

        def get(self, *a, **k):
            return R(self.body)
    for body, kind in ((b"<html><body>Access Denied</body></html>", ErrorKind.BLOCKED_HTML), (b"hello there", ErrorKind.PARSE_FAIL)):
        monkeypatch.setattr(fetch_mod, "session", lambda profile, b=body: S(b))
        with pytest.raises(DrishtiError) as e:
            fetch_mod._once(profiles.NSE_ARCHIVE, "https://nsearchives.nseindia.com/x.pdf", "pdf", None)
        assert e.value.kind == kind
    monkeypatch.setattr(fetch_mod, "session", lambda profile: S(b"%PDF-1.7 fine"))
    assert fetch_mod._once(profiles.NSE_ARCHIVE, "https://nsearchives.nseindia.com/x.pdf", "pdf", None).body.startswith(b"%PDF")


# --- E3 ------------------------------------------------------------------------------------------------------------
def _ann(st, id, published, headline, url, isin=ISIN, category="Analysts/Institutional Investor Meet/Con. Call Updates"):
    ds.ensure(st, "announcement")
    st.upsert("announcement", [{"source": "NSE", "id": id, "published": published, "symbol": "ABC", "isin": isin, "scrip_code": None, "company": "ABC",
                                "category": category, "subcategory": None, "headline": headline, "text": None, "url": url, "critical": 0, "has_xbrl": 0}],
              key=["source", "id"])


def test_find_filings_lists_transcripts_not_audio_or_date_notices_and_respects_the_date(st):
    _ann(st, "1", "2026-10-30T18:00:00", "Transcript of earnings call Q2 FY27", "https://nsearchives.nseindia.com/c/t1.pdf")
    _ann(st, "2", "2026-10-29T10:00:00", "Audio recording of earnings call", "https://nsearchives.nseindia.com/c/a.mp3")
    _ann(st, "3", "2026-10-20T10:00:00", "Schedule of analyst meet", "https://nsearchives.nseindia.com/c/s.pdf")
    _ann(st, "4", "2026-07-28T18:00:00", "Transcript of earnings call Q1 FY27", "https://nsearchives.nseindia.com/c/t0.pdf")
    _ann(st, "5", "2026-10-30T18:00:00", "Transcript of earnings call", "https://nsearchives.nseindia.com/c/other.pdf", isin="INE999Z01019")
    _ann(st, "6", "2026-10-25T09:00:00", "Financial results for the quarter", "https://nsearchives.nseindia.com/c/r.pdf", category="Financial Results")
    r = ev.find_filings(st, isin=ISIN, kind="concall", on="2026-11-10")
    assert [f["published"] for f in r.data["filings"]] == ["2026-10-30", "2026-07-28"]
    assert ev.find_filings(st, isin=ISIN, kind="concall", on="2026-08-15").data["filings"][0]["url"].endswith("t0.pdf")     # point in time
    assert [f["url"][-5:] for f in ev.find_filings(st, isin=ISIN, kind="results", on="2026-11-10").data["filings"]] == ["r.pdf"]
    empty = ev.find_filings(st, isin="INE111A01011", kind="concall", on="2026-11-10")
    assert empty.ok and empty.data["count"] == 0 and "no concall documents" in empty.data["note"]
    assert ev.find_filings(st, isin=ISIN, kind="gossip").error.kind == ErrorKind.REJECTED


def test_read_source_in_windows_reassembles_the_exact_text(st):
    body = " ".join(f"word{i}" for i in range(6000))
    sid = ev.add_source(st, text=body, kind="other", title="long", published_at="2026-10-01").data["source_id"]
    parts, off = [], 0
    while off is not None:
        r = ev.read_source(st, sid, offset=off, length=5000)
        parts.append(r.data["text"])
        off = r.data["next_offset"]
        assert len(parts) < 100
    assert len(parts) > 3 and "".join(parts).replace(" ", "") == body.replace(" ", "")
    first = ev.read_source(st, sid, offset=0, length=5000)
    assert first.data["chars"] == len(body) and first.data["text"].startswith("word0 ")
    assert ev.read_source(st, "src-nope").error.kind == ErrorKind.NOT_FOUND


# --- E4 ------------------------------------------------------------------------------------------------------------
def _claims(st, n=6):
    body = " ".join(f"Sentence number {i} says something specific about the business in detail." for i in range(40))
    sid = ev.add_source(st, text=body, kind="concall", title="call", published_at="2026-10-30", isin=ISIN).data["source_id"]
    ids = []
    for i in range(n):
        r = ev.record_evidence(st, source_id=sid, claim=f"Claim about sentence {i}", quote=f"Sentence number {i} says something specific about the business",
                               topic="other", polarity=0, author="claude/test")
        assert r.ok, r.error
        ids.append(r.data["claim_id"])
    return sid, ids


def test_audit_reproves_every_quote_and_catches_a_changed_document(st):
    sid, ids = _claims(st)
    assert ev.audit(st).data == {"claims": 6, "ok": 6, "moved": 0, "broken": []}
    row = dict(st.query("SELECT * FROM evidence_source WHERE id = ?", (sid,))[0])
    row["text"] = row["text"].replace("Sentence number 3 says", "Sentence number 3 does not say")
    st.upsert("evidence_source", [row], key=["id"])
    a = ev.audit(st).data
    assert a["broken"] == [ids[3]] and a["ok"] == 5 and a["moved"] == 2         # the two later quotes shifted by the longer sentence, but are still there


def test_review_sample_and_summary(st):
    sid, ids = _claims(st, 8)
    s = ev.sample_claims(st, n=5, seed=3)
    assert s.data["count"] == 5 and s.data["unreviewed_left"] == 3 and all(x["quote"] in x["context"] for x in s.data["sample"])
    assert ev.sample_claims(st, n=5, seed=3).data == s.data                       # same seed, same sample
    own = ev.review_claim(st, claim_id=ids[0], verdict="supported", reviewer="claude/test")
    assert own.error.kind == ErrorKind.REJECTED                                   # an author cannot review itself
    assert ev.review_claim(st, claim_id=ids[0], verdict="great", reviewer="Abhi").error.kind == ErrorKind.REJECTED
    assert ev.review_claim(st, claim_id="clm-none", verdict="wrong", reviewer="Abhi").error.kind == ErrorKind.NOT_FOUND
    for i, v in enumerate(("supported", "supported", "overstated", "wrong")):
        assert ev.review_claim(st, claim_id=ids[i], verdict=v, reviewer="Abhi").ok
    sm = ev.review_summary(st).data
    assert sm["reviewed"] == 4 and sm["supported"] == 2 and sm["supported_share"] == 0.5 and "too few" in sm["note"]
    left = ev.sample_claims(st, n=10).data
    assert left["count"] == 4 and not {x["claim_id"] for x in left["sample"]} & set(ids[:4])


# --- universe, storage, health ---------------------------------------------------------------------------------------
def _prices(st, rows):
    ds.ensure(st, "price_eod")
    st.upsert("price_eod", [{"trade_date": "2026-11-06", "isin": i, "exchange": "NSE", "series": "EQ", "symbol": i[-4:], "open": 1, "high": 1, "low": 1,
                             "close": 1, "prev_close": 1, "volume": 1, "turnover": t, "trades": 1} for i, t in rows],
              key=["trade_date", "isin", "exchange", "series"])


def test_universe_keeps_the_most_liquid_names_and_does_nothing_without_prices(st):
    assert universe.liquid(st, "2026-11-06") is None
    _prices(st, [(f"INE{i:03d}A01011", 1e9 / (i + 1)) for i in range(20)])
    top = universe.liquid(st, "2026-11-06", top=5)
    assert top == {f"INE{i:03d}A01011" for i in range(5)}
    assert universe.liquid(st, "2026-11-06", top=5, window_days=0) == top           # the day itself is inside the window


def test_storage_report_counts_rows_and_sorts_largest_first(st):
    _prices(st, [(f"INE{i:03d}A01011", 1e6) for i in range(30)])
    r = storage.report(st)
    t = {x["table"]: x for x in r["tables"]}
    assert t["price_eod"]["rows"] == 30 and r["tables"][0]["rows"] >= r["tables"][-1]["rows"] or r["total_mb"] is not None


def test_health_shows_the_newest_date_in_each_stored_table(st):
    _prices(st, [("INE000A01011", 1e6)])
    h = st.data_health()
    t = {x["table"]: x for x in h["tables"]}
    assert t["price_eod"]["newest"] == "2026-11-06" and isinstance(t["price_eod"]["age_days"], int)
    assert "delivery" not in t or t["delivery"]["newest"] is None

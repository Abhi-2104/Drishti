"""Task 001 evidence layer — offline, fixture-based tests.

Each test maps to an acceptance criterion or a Codex failure case. No network.
"""
from datetime import datetime, timezone, timedelta
from pathlib import Path

from evidence import (
    Claim, DocumentClass, Evidence, EvidenceType, Locator, Publisher, Source,
    SourceType, SpeakerRole, AvailProvenance, EvidenceStore, validate, statement_is_extractive,
)
from evidence.ingest import (
    ingest_bse_filing_row, ingest_screener_metric, ingest_transcript_excerpt,
)

FX = Path(__file__).parent / "fixtures"
IST = timezone(timedelta(hours=5, minutes=30))
UTC = timezone.utc


def jul(day, hh=9, mm=0):
    return datetime(2026, 7, day, hh, mm, tzinfo=IST)


# --- good ingests + full store passes validation (criteria 1-6, 10, 12) --------
def build_good_store():
    st = EvidenceStore()
    st.add(ingest_bse_filing_row(str(FX / "infy_bse_filing.json"),
           "https://bse/ann", "500209", retrieved_at=jul(18, 17), available_at=jul(18, 16, 30)))
    st.add(ingest_screener_metric(str(FX / "infy_screener.txt"), "https://screener/INFY",
           statement="Operating margin was 21%", excerpt="Operating margin was 21%",
           retrieved_at=jul(18, 17), available_at=jul(18), period_date="2026-06-30"))
    st.add(ingest_transcript_excerpt(str(FX / "infy_transcript.txt"), "https://bse/transcript",
           statement="We expect demand to remain resilient through the year",
           excerpt="We expect demand to remain resilient through the year",
           speaker="CEO", speaker_role=SpeakerRole.MANAGEMENT,
           retrieved_at=jul(18, 17), available_at=jul(18)))
    return st


def test_good_store_is_valid():
    assert validate(build_good_store()) == []


def test_provenance_is_complete():
    st = build_good_store()
    s = st.sources[0]
    assert s.isin == "INE009A01021" and s.url and s.content_sha256 and s.available_at
    e = st.evidence[0]
    assert e.source_id and e.locator and e.excerpt


# --- point-in-time: publication date, not period date (C1, C2, criterion 3) ----
def test_point_in_time_excludes_future_filing():
    st = build_good_store()
    # filing became public 18 Jul 16:30 IST
    before = st.query(SourceType.FILING, as_of=jul(10))
    assert before.evidence == []                      # not yet public on 10 Jul
    after = st.query(SourceType.FILING, as_of=jul(20))
    assert len(after.evidence) == 1                   # public by 20 Jul


def test_same_day_before_publication_is_excluded():
    st = build_good_store()
    # cutoff 18 Jul 09:00 IST, filing public 18 Jul 16:30 IST -> excluded
    r = st.query(SourceType.FILING, as_of=jul(18, 9))
    assert r.evidence == []


# --- failure is a value, not a swallowed [] (H9-H11, criterion 11) -------------
def test_empty_is_no_data():
    o = ingest_bse_filing_row(str(FX / "infy_bse_empty.json"), "u", "500209", jul(18), jul(18))
    assert o.status.value == "NO_DATA" and o.response_meta.structure_valid


def test_garbled_is_parse_failed_not_no_data():
    o = ingest_bse_filing_row(str(FX / "infy_bse_garbled.txt"), "u", "500209", jul(18), jul(18))
    assert o.status.value == "PARSE_FAILED"


def test_wrong_scrip_is_parse_failed():
    o = ingest_bse_filing_row(str(FX / "infy_bse_wrongscrip.json"), "u", "500209", jul(18), jul(18))
    assert o.status.value == "PARSE_FAILED" and o.response_meta.echo_matches is False


def test_query_surfaces_failure_so_it_cant_read_as_no_concerns():
    st = EvidenceStore()
    st.add(ingest_bse_filing_row(str(FX / "infy_bse_garbled.txt"), "u", "500209", jul(18), jul(18)))
    r = st.query(SourceType.FILING, as_of=jul(20))
    assert r.evidence == []
    assert r.outcomes and r.outcomes[0].status.value == "PARSE_FAILED"  # visible, not silent


# --- extractive tier catches the negation/flip cases (C4, criterion 6/10) ------
def test_extractive_check_rejects_dropped_negation():
    assert statement_is_extractive("we do not expect a material improvement",
                                   "on margins, we do not expect a material improvement in the near term")
    assert not statement_is_extractive("management expects a material improvement in margins",
                                       "we do not expect a material improvement in the near term")


def test_negated_management_claim_fails_validation():
    st = build_good_store()
    tsrc = next(s for s in st.sources if s.id == "src-transcript")
    text = Path(tsrc.raw_ref).read_text()
    span = "we do not expect a material improvement in the near term"
    i = text.find(span)
    bad = Evidence(id="ev-bad", evidence_type=EvidenceType.MANAGEMENT_CLAIM,
                   statement="Management expects a material improvement in margins",
                   excerpt=span, source_id=tsrc.id,
                   locator=Locator("char", i, i + len(span)),
                   speaker="CEO", speaker_role=SpeakerRole.MANAGEMENT)
    st.evidence.append(bad)
    errs = validate(st)
    assert any(x.startswith("[6]") for x in errs)


# --- excerpt bound to raw; hash binds source (H5, M12, new-5, criterion 12) ----
def test_tampered_excerpt_fails_binding():
    st = build_good_store()
    st.evidence[0].excerpt = "FABRICATED HEADLINE"
    assert any(x.startswith("[5]") for x in validate(st))


def test_mutated_raw_hash_mismatch():
    st = build_good_store()
    st.sources[0].content_sha256 = "0" * 64
    assert any(x.startswith("[12]") for x in validate(st))


# --- inference: multi-source, DAG, same-entity, temporal (C3, H6, blocker 2) ---
def _inference_store():
    st = build_good_store()
    fact_ids = [e.id for e in st.evidence if e.evidence_type == EvidenceType.FACT]
    inf = Evidence(id="ev-inf", evidence_type=EvidenceType.INFERENCE,
                   statement="Margins held up despite modest revenue growth",
                   excerpt="", derived_from=fact_ids, recorded_at=jul(19))
    st.evidence.append(inf)
    return st, inf


def test_valid_inference_passes():
    st, _ = _inference_store()
    assert validate(st) == []


def test_inference_from_future_parent_fails():
    st, inf = _inference_store()
    inf.recorded_at = jul(18, 0)  # before the filing became available (18 Jul 16:30)
    assert any(x.startswith("[4]") for x in validate(st))


def test_inference_self_reference_fails():
    st, inf = _inference_store()
    inf.derived_from = [inf.id]
    assert any(x.startswith("[4]") for x in validate(st))


def test_fact_may_not_have_derived_from():
    st = build_good_store()
    st.evidence[0].derived_from = ["ev-screener"]
    assert any(x.startswith("[3]") for x in validate(st))


# --- management role, claim links, unknown availability ------------------------
def test_analyst_span_cannot_be_management_claim():
    st = build_good_store()
    e = next(e for e in st.evidence if e.id == "ev-transcript")
    e.speaker_role = SpeakerRole.ANALYST
    assert any(x.startswith("[7]") for x in validate(st))


def test_claim_evidence_cannot_be_both_sides():
    st = build_good_store()
    st.add_claim(Claim(id="c1", statement="x", supporting=["ev-screener"], contradicting=["ev-screener"]))
    assert any(x.startswith("[11]") for x in validate(st))


def test_unknown_availability_must_have_no_timestamp():
    st = build_good_store()
    st.sources[0].avail_provenance = AvailProvenance.UNKNOWN  # but available_at still set
    assert any(x.startswith("[2]") for x in validate(st))


# --- serialization round-trips and re-validates (M17, criterion 12) ------------
def test_serialize_roundtrip_revalidates():
    st = build_good_store()
    st2 = EvidenceStore.from_json(st.to_json())
    assert validate(st2) == []
    assert len(st2.sources) == len(st.sources) and len(st2.evidence) == len(st.evidence)

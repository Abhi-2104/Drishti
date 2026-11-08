"""Ingest adapters: raw captured payload -> RetrievalOutcome. Pure, offline, deterministic.

One adapter per (source_type x format). Each locates its excerpt as an exact character
span of the stored raw, so the excerpt is provably extractable from the hashed bytes.
Failure is a returned value (NO_DATA / PARSE_FAILED / PARTIAL), never a swallowed [].
"""
from __future__ import annotations

import json
from datetime import datetime

from .models import (
    AvailProvenance, DocumentClass, Evidence, EvidenceType, Locator, Publisher,
    ResponseMeta, RetrievalOutcome, RetrievalRequest, RetrievalStatus, Source,
    SourceType, SpeakerRole,
)
from .rawio import canonical_text, normalize_isin, raw_bytes, sha256_hex


def _locate(text: str, excerpt: str) -> Locator:
    i = text.find(excerpt)
    if i < 0:
        raise ValueError("excerpt not found verbatim in raw payload")
    return Locator(unit="char", char_start=i, char_end=i + len(excerpt))


# --- FILING: a single BSE AnnSubCategoryGetData row (JSON) ----------------------
def ingest_bse_filing_row(raw_ref: str, url: str, requested_scrip: str,
                          retrieved_at: datetime, available_at: datetime) -> RetrievalOutcome:
    req = RetrievalRequest(isin=normalize_isin(requested_scrip) or requested_scrip,
                           source_type=SourceType.FILING, attempted_id=requested_scrip,
                           attempted_at=retrieved_at, document_class=DocumentClass.EXCHANGE_FILING)
    raw = raw_bytes(raw_ref)
    text = canonical_text(raw_ref)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as ex:
        return RetrievalOutcome(req, RetrievalStatus.PARSE_FAILED,
                                ResponseMeta(structure_valid=False, echo_matches=False),
                                detail=f"non-JSON response: {ex}")
    rows = data.get("Table")
    if not isinstance(rows, list):
        return RetrievalOutcome(req, RetrievalStatus.PARSE_FAILED,
                                ResponseMeta(structure_valid=False, echo_matches=False),
                                detail="missing 'Table' array")
    # echo check: does the response concern the scrip we asked for?
    echo_ok = all(str(r.get("SCRIP_CD")) == str(requested_scrip) for r in rows) if rows else True
    if not rows:
        return RetrievalOutcome(req, RetrievalStatus.NO_DATA,
                                ResponseMeta(structure_valid=True, echo_matches=echo_ok),
                                detail="validated empty: well-formed response, no filings")
    if not echo_ok:
        return RetrievalOutcome(req, RetrievalStatus.PARSE_FAILED,
                                ResponseMeta(structure_valid=True, echo_matches=False),
                                detail="response scrip does not match requested scrip")

    row = rows[0]
    headline = row["HEADLINE"]
    src = Source(
        id="src-bse-filing", isin=normalize_isin(str(row["SCRIP_CD"])), ticker="INFY", url=url,
        publisher=Publisher.BSE, document_class=DocumentClass.EXCHANGE_FILING,
        raw_ref=raw_ref, content_sha256=sha256_hex(raw), retrieved_at=retrieved_at,
        available_at=available_at, avail_provenance=AvailProvenance.EXACT,
        period_date=row.get("period_date"), disclosure_group_id=row.get("group"),
    )
    ev = Evidence(id="ev-bse-filing", evidence_type=EvidenceType.FACT,
                  statement=headline, excerpt=headline, source_id=src.id,
                  locator=_locate(text, headline), as_of=row.get("period_date"))
    return RetrievalOutcome(req, RetrievalStatus.OK,
                            ResponseMeta(structure_valid=True, echo_matches=True),
                            sources=[src], evidence=[ev])


# --- FUNDAMENTALS: a Screener metric line (text) --------------------------------
def ingest_screener_metric(raw_ref: str, url: str, statement: str, excerpt: str,
                           retrieved_at: datetime, available_at: datetime,
                           period_date: str) -> RetrievalOutcome:
    req = RetrievalRequest(isin="INE009A01021", source_type=SourceType.FUNDAMENTALS,
                           attempted_id="INFY", attempted_at=retrieved_at,
                           document_class=DocumentClass.QUOTE)
    raw = raw_bytes(raw_ref)
    text = canonical_text(raw_ref)
    src = Source(id="src-screener", isin="INE009A01021", ticker="INFY", url=url,
                 publisher=Publisher.SCREENER, document_class=DocumentClass.QUOTE,
                 raw_ref=raw_ref, content_sha256=sha256_hex(raw), retrieved_at=retrieved_at,
                 available_at=available_at, avail_provenance=AvailProvenance.EXACT,
                 period_date=period_date)
    ev = Evidence(id="ev-screener", evidence_type=EvidenceType.FACT,
                  statement=statement, excerpt=excerpt, source_id=src.id,
                  locator=_locate(text, excerpt), as_of=period_date)
    return RetrievalOutcome(req, RetrievalStatus.OK,
                            ResponseMeta(structure_valid=True, echo_matches=True),
                            sources=[src], evidence=[ev])


# --- TRANSCRIPT: a concall excerpt (text), with speaker role --------------------
def ingest_transcript_excerpt(raw_ref: str, url: str, statement: str, excerpt: str,
                              speaker: str, speaker_role: SpeakerRole,
                              retrieved_at: datetime, available_at: datetime) -> RetrievalOutcome:
    req = RetrievalRequest(isin="INE009A01021", source_type=SourceType.TRANSCRIPT,
                           attempted_id="INFY", attempted_at=retrieved_at,
                           document_class=DocumentClass.TRANSCRIPT)
    raw = raw_bytes(raw_ref)
    text = canonical_text(raw_ref)
    src = Source(id="src-transcript", isin="INE009A01021", ticker="INFY", url=url,
                 publisher=Publisher.BSE, document_class=DocumentClass.TRANSCRIPT,
                 raw_ref=raw_ref, content_sha256=sha256_hex(raw), retrieved_at=retrieved_at,
                 available_at=available_at, avail_provenance=AvailProvenance.EXACT)
    ev = Evidence(id="ev-transcript", evidence_type=EvidenceType.MANAGEMENT_CLAIM,
                  statement=statement, excerpt=excerpt, source_id=src.id,
                  locator=_locate(text, excerpt), speaker=speaker, speaker_role=speaker_role)
    return RetrievalOutcome(req, RetrievalStatus.OK,
                            ResponseMeta(structure_valid=True, echo_matches=True),
                            sources=[src], evidence=[ev])

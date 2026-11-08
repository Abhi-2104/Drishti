"""Evidence-layer data model (Task 001, proposal v3).

Plain dataclasses + enums, stdlib only. The types only *hold* data; every integrity
rule lives in `validate.py` so "has a field" never gets confused with "is enforced".
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from enum import Enum

IST = timezone(timedelta(hours=5, minutes=30))


class SourceType(str, Enum):
    FILING = "FILING"
    TRANSCRIPT = "TRANSCRIPT"
    FUNDAMENTALS = "FUNDAMENTALS"
    RATING = "RATING"
    QUOTE = "QUOTE"
    NEWS = "NEWS"


class DocumentClass(str, Enum):
    EXCHANGE_FILING = "EXCHANGE_FILING"
    EARNINGS_RELEASE = "EARNINGS_RELEASE"
    TRANSCRIPT = "TRANSCRIPT"
    INVESTOR_PRESENTATION = "INVESTOR_PRESENTATION"
    RATING_ACTION = "RATING_ACTION"
    ANNUAL_REPORT = "ANNUAL_REPORT"
    QUOTE = "QUOTE"
    MEDIA = "MEDIA"


class Publisher(str, Enum):
    NSE = "NSE"
    BSE = "BSE"
    SCREENER = "SCREENER"
    CRISIL = "CRISIL"
    ICRA = "ICRA"
    CARE = "CARE"
    SEBI = "SEBI"
    MEDIA = "MEDIA"


class AvailProvenance(str, Enum):
    EXACT = "EXACT"            # from the source's own publication timestamp
    INFERRED_EOD = "INFERRED_EOD"  # only a *publication* date known -> end-of-day IST
    UNKNOWN = "UNKNOWN"        # no availability -> excluded from point-in-time queries


class SpeakerRole(str, Enum):
    MANAGEMENT = "MANAGEMENT"
    ANALYST = "ANALYST"
    OPERATOR = "OPERATOR"
    OTHER = "OTHER"


class EvidenceType(str, Enum):
    FACT = "FACT"
    MANAGEMENT_CLAIM = "MANAGEMENT_CLAIM"
    INFERENCE = "INFERENCE"


class RetrievalStatus(str, Enum):
    OK = "OK"
    PARTIAL = "PARTIAL"
    NO_DATA = "NO_DATA"
    FETCH_FAILED = "FETCH_FAILED"
    PARSE_FAILED = "PARSE_FAILED"


# publisher x document_class compatibility (invariant 8)
PUBLISHER_CLASS_OK: dict[DocumentClass, set[Publisher]] = {
    DocumentClass.EXCHANGE_FILING: {Publisher.NSE, Publisher.BSE},
    DocumentClass.EARNINGS_RELEASE: {Publisher.NSE, Publisher.BSE},
    DocumentClass.TRANSCRIPT: {Publisher.NSE, Publisher.BSE},
    DocumentClass.ANNUAL_REPORT: {Publisher.NSE, Publisher.BSE},
    DocumentClass.INVESTOR_PRESENTATION: {Publisher.NSE, Publisher.BSE},
    DocumentClass.RATING_ACTION: {Publisher.CRISIL, Publisher.ICRA, Publisher.CARE},
    DocumentClass.QUOTE: {Publisher.NSE, Publisher.BSE, Publisher.SCREENER},
    DocumentClass.MEDIA: {Publisher.MEDIA},
}


@dataclass
class Locator:
    """A char range into canonical_text(raw_ref). Binds an excerpt to the exact bytes."""
    unit: str          # "char" | "page:char" etc. (v1 uses "char")
    char_start: int
    char_end: int


@dataclass
class Source:
    id: str
    isin: str                       # canonical entity identity (never the ticker)
    ticker: str
    url: str
    publisher: Publisher
    document_class: DocumentClass
    raw_ref: str                    # path to the stored raw payload
    content_sha256: str
    retrieved_at: datetime          # tz-aware; audit only
    available_at: datetime | None   # tz-aware; None iff avail_provenance==UNKNOWN
    avail_provenance: AvailProvenance
    period_date: str | None = None  # ISO date; descriptive, never a point-in-time key
    supersedes: str | None = None
    disclosure_group_id: str | None = None


@dataclass
class Evidence:
    id: str
    evidence_type: EvidenceType
    statement: str
    excerpt: str
    source_id: str | None = None            # required iff FACT|MANAGEMENT_CLAIM
    locator: Locator | None = None          # required iff source-backed
    derived_from: list[str] = field(default_factory=list)  # non-empty iff INFERENCE
    recorded_at: datetime | None = None     # tz-aware; required iff INFERENCE
    speaker: str | None = None
    speaker_role: SpeakerRole | None = None  # must be MANAGEMENT for MANAGEMENT_CLAIM
    as_of: str | None = None                # ISO date; descriptive


@dataclass
class Claim:
    id: str
    statement: str
    supporting: list[str] = field(default_factory=list)
    contradicting: list[str] = field(default_factory=list)
    # no confidence in v1 (dropped: unsupported assertion)


@dataclass
class RetrievalRequest:
    isin: str
    source_type: SourceType
    attempted_id: str            # scrip / symbol / url actually requested
    attempted_at: datetime
    window_from: str | None = None
    window_to: str | None = None
    document_class: DocumentClass | None = None


@dataclass
class ResponseMeta:
    structure_valid: bool
    echo_matches: bool           # did the response echo back the requested id?


@dataclass
class RetrievalOutcome:
    request: RetrievalRequest
    status: RetrievalStatus
    response_meta: ResponseMeta
    sources: list[Source] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)
    completeness: dict | None = None   # {expected_units, parsed_units} for PARTIAL
    detail: str = ""


@dataclass
class QueryResult:
    """What consumers get. Outcomes are ALWAYS included so a failure can't read as 'no data'."""
    evidence: list[Evidence]
    outcomes: list[RetrievalOutcome]

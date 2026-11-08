"""EvidenceStore: hold sources/evidence/claims/outcomes, query, and (de)serialize.

Query returns a QueryResult that ALWAYS carries the matching retrieval outcomes, so a
failed fetch can never silently read as "no data" downstream.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime

from .models import (
    AvailProvenance, AvailProvenance as _AP, Claim, DocumentClass, Evidence, Locator,
    Publisher, QueryResult, ResponseMeta, RetrievalOutcome, RetrievalRequest,
    RetrievalStatus, Source, SourceType, SpeakerRole, EvidenceType,
)


class EvidenceStore:
    def __init__(self):
        self.sources: list[Source] = []
        self.evidence: list[Evidence] = []
        self.claims: list[Claim] = []
        self.outcomes: list[RetrievalOutcome] = []

    def add(self, outcome: RetrievalOutcome) -> None:
        """Record everything from one retrieval attempt, success OR failure."""
        self.outcomes.append(outcome)
        self.sources.extend(outcome.sources)
        self.evidence.extend(outcome.evidence)

    def add_claim(self, claim: Claim) -> None:
        self.claims.append(claim)

    def _source(self, sid: str | None) -> Source | None:
        return next((s for s in self.sources if s.id == sid), None)

    def query(
        self,
        source_type: SourceType,
        as_of: datetime | None = None,
        document_class: DocumentClass | None = None,
    ) -> QueryResult:
        """Point-in-time query.

        Filters evidence on its SOURCE's `available_at` (never period_date/as_of), drops
        UNKNOWN-availability sources, and returns every outcome whose request overlaps the
        asked scope so failures/not-attempted are visible, not silently absent.
        """
        ev: list[Evidence] = []
        for e in self.evidence:
            src = self._source(e.source_id)
            if src is None:
                continue
            # scope by source_type via the owning outcome's request
            if not self._source_type_matches(src, source_type):
                continue
            if document_class is not None and src.document_class != document_class:
                continue
            if src.avail_provenance == AvailProvenance.UNKNOWN or src.available_at is None:
                continue  # not known-available -> excluded from point-in-time
            if as_of is not None and src.available_at > as_of:
                continue  # not yet public as of the cutoff
            ev.append(e)

        outs = [o for o in self.outcomes if o.request.source_type == source_type]
        return QueryResult(evidence=ev, outcomes=outs)

    def _source_type_matches(self, src: Source, st: SourceType) -> bool:
        for o in self.outcomes:
            if src in o.sources:
                return o.request.source_type == st
        return False

    # --- serialization (invariant 12: round-trips, then re-validates) ----------
    def to_json(self) -> str:
        return json.dumps({
            "sources": [self._enc(asdict(s)) for s in self.sources],
            "evidence": [self._enc(asdict(e)) for e in self.evidence],
            "claims": [asdict(c) for c in self.claims],
            "outcomes": [self._enc(asdict(o)) for o in self.outcomes],
        }, indent=2)

    @staticmethod
    def _enc(d: dict):
        def conv(v):
            if isinstance(v, datetime):
                return {"__dt__": v.isoformat()}
            if isinstance(v, dict):
                return {k: conv(x) for k, x in v.items()}
            if isinstance(v, list):
                return [conv(x) for x in v]
            return v
        return {k: conv(v) for k, v in d.items()}

    @classmethod
    def from_json(cls, text: str) -> "EvidenceStore":
        raw = json.loads(text)
        st = cls()
        st.sources = [_mk_source(d) for d in raw["sources"]]
        st.evidence = [_mk_evidence(d) for d in raw["evidence"]]
        st.claims = [Claim(**d) for d in raw["claims"]]
        st.outcomes = [_mk_outcome(d) for d in raw["outcomes"]]
        return st


def _dt(v):
    if isinstance(v, dict) and "__dt__" in v:
        return datetime.fromisoformat(v["__dt__"])
    return v


def _mk_source(d: dict) -> Source:
    return Source(
        id=d["id"], isin=d["isin"], ticker=d["ticker"], url=d["url"],
        publisher=Publisher(d["publisher"]), document_class=DocumentClass(d["document_class"]),
        raw_ref=d["raw_ref"], content_sha256=d["content_sha256"],
        retrieved_at=_dt(d["retrieved_at"]), available_at=_dt(d["available_at"]),
        avail_provenance=AvailProvenance(d["avail_provenance"]),
        period_date=d.get("period_date"), supersedes=d.get("supersedes"),
        disclosure_group_id=d.get("disclosure_group_id"),
    )


def _mk_evidence(d: dict) -> Evidence:
    loc = d.get("locator")
    return Evidence(
        id=d["id"], evidence_type=EvidenceType(d["evidence_type"]),
        statement=d["statement"], excerpt=d["excerpt"], source_id=d.get("source_id"),
        locator=Locator(**loc) if loc else None, derived_from=d.get("derived_from", []),
        recorded_at=_dt(d.get("recorded_at")),
        speaker=d.get("speaker"),
        speaker_role=SpeakerRole(d["speaker_role"]) if d.get("speaker_role") else None,
        as_of=d.get("as_of"),
    )


def _mk_outcome(d: dict) -> RetrievalOutcome:
    r = d["request"]
    req = RetrievalRequest(
        isin=r["isin"], source_type=SourceType(r["source_type"]),
        attempted_id=r["attempted_id"], attempted_at=_dt(r["attempted_at"]),
        window_from=r.get("window_from"), window_to=r.get("window_to"),
        document_class=DocumentClass(r["document_class"]) if r.get("document_class") else None,
    )
    m = d["response_meta"]
    return RetrievalOutcome(
        request=req, status=RetrievalStatus(d["status"]),
        response_meta=ResponseMeta(structure_valid=m["structure_valid"], echo_matches=m["echo_matches"]),
        sources=[_mk_source(s) for s in d.get("sources", [])],
        evidence=[_mk_evidence(e) for e in d.get("evidence", [])],
        completeness=d.get("completeness"), detail=d.get("detail", ""),
    )

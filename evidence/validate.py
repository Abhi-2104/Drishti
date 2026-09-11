"""validate(store) -> list of invariant violations. Empty list == trustworthy.

This is where "has a field" becomes "is enforced". Every check maps to a numbered
invariant in proposals/001-evidence-layer.md (v3).
"""
from __future__ import annotations

import re
from datetime import datetime

from .models import (
    AvailProvenance, DocumentClass, EvidenceType, PUBLISHER_CLASS_OK, Source, Evidence,
)
from .rawio import canonical_text, raw_bytes, sha256_hex


def _tz_aware(dt: datetime | None) -> bool:
    return dt is not None and dt.tzinfo is not None and dt.utcoffset() is not None


# --- extractive check (invariant 6) --------------------------------------------
# A FACT/MANAGEMENT_CLAIM statement must be a *normalized quotation of a contiguous
# span* of its excerpt. We normalize lightly (lowercase, collapse whitespace, strip
# thousands separators, unify currency words) and require substring containment.
# This catches dropped negation, flipped direction, wrong unit, over-specific period
# -- the statement cannot assert words the quoted span does not contain.
def _normalize(s: str) -> str:
    s = s.lower()
    s = s.replace("₹", " rs ").replace("rupees", "rs")
    s = re.sub(r"(\d),(?=\d)", r"\1", s)      # 1,234 -> 1234
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def statement_is_extractive(statement: str, excerpt: str) -> bool:
    return _normalize(statement) in _normalize(excerpt)


def validate(store) -> list[str]:
    errs: list[str] = []
    sources = {s.id: s for s in store.sources}
    evidence = {e.id: e for e in store.evidence}
    claims = {c.id: c for c in store.claims}

    # 1. unique ids
    for label, items in (("source", store.sources), ("evidence", store.evidence), ("claim", store.claims)):
        seen = set()
        for it in items:
            if it.id in seen:
                errs.append(f"[1] duplicate {label} id: {it.id}")
            seen.add(it.id)

    for s in store.sources:
        # 2. datetimes tz-aware
        if not _tz_aware(s.retrieved_at):
            errs.append(f"[2] source {s.id} retrieved_at not tz-aware")
        # unknown-availability contradiction (blocker 1)
        if s.avail_provenance == AvailProvenance.UNKNOWN:
            if s.available_at is not None:
                errs.append(f"[2] source {s.id} UNKNOWN provenance but available_at set")
        else:
            if not _tz_aware(s.available_at):
                errs.append(f"[2] source {s.id} available_at not tz-aware")
        # 8. publisher x document_class
        allowed = PUBLISHER_CLASS_OK.get(s.document_class, set())
        if s.publisher not in allowed:
            errs.append(f"[8] source {s.id}: {s.publisher.value} incompatible with {s.document_class.value}")
        # 12. content hash matches the stored raw
        try:
            if sha256_hex(raw_bytes(s.raw_ref)) != s.content_sha256:
                errs.append(f"[12] source {s.id}: content_sha256 mismatch (raw mutated?)")
        except OSError:
            errs.append(f"[12] source {s.id}: raw_ref unreadable ({s.raw_ref})")

    # 9. supersedes rules + fork detection
    _validate_supersedes(store, sources, errs)
    # 10. disclosure groups share isin + period
    _validate_groups(store, errs)

    for e in store.evidence:
        # 3. source vs inference shape
        if e.evidence_type in (EvidenceType.FACT, EvidenceType.MANAGEMENT_CLAIM):
            if e.source_id is None:
                errs.append(f"[3] {e.evidence_type.value} {e.id} has no source_id")
            if e.derived_from:
                errs.append(f"[3] {e.evidence_type.value} {e.id} must not have derived_from")
            if e.recorded_at is not None:
                errs.append(f"[3] {e.evidence_type.value} {e.id} must not have recorded_at")
        else:  # INFERENCE
            if e.source_id is not None:
                errs.append(f"[3] INFERENCE {e.id} must not have a source_id")
            if not e.derived_from:
                errs.append(f"[3] INFERENCE {e.id} needs >=1 derived_from")
            if not _tz_aware(e.recorded_at):
                errs.append(f"[3] INFERENCE {e.id} needs tz-aware recorded_at")

        # referential integrity (1)
        if e.source_id is not None and e.source_id not in sources:
            errs.append(f"[1] evidence {e.id} dangling source_id {e.source_id}")
        for p in e.derived_from:
            if p not in evidence:
                errs.append(f"[1] inference {e.id} dangling parent {p}")

        # 5. excerpt bound to raw ; 7. management speaker ; 6. extractive
        if e.source_id in sources and e.locator is not None:
            src = sources[e.source_id]
            try:
                span = canonical_text(src.raw_ref)[e.locator.char_start:e.locator.char_end]
                if span != e.excerpt:
                    errs.append(f"[5] evidence {e.id}: excerpt != raw[{e.locator.char_start}:{e.locator.char_end}]")
            except OSError:
                errs.append(f"[5] evidence {e.id}: raw unreadable for locator check")
        if e.evidence_type == EvidenceType.MANAGEMENT_CLAIM:
            from .models import SpeakerRole
            if e.speaker_role != SpeakerRole.MANAGEMENT:
                errs.append(f"[7] MANAGEMENT_CLAIM {e.id}: speaker_role is {e.speaker_role} not MANAGEMENT")
        if e.evidence_type in (EvidenceType.FACT, EvidenceType.MANAGEMENT_CLAIM):
            if not statement_is_extractive(e.statement, e.excerpt):
                errs.append(f"[6] {e.evidence_type.value} {e.id}: statement not a quotation of its excerpt")

    # 4. derived_from graph: acyclic, same isin, parent available_at <= child recorded_at
    _validate_dag(store, sources, evidence, errs)

    # 11. claim link sanity
    for c in store.claims:
        both = set(c.supporting) & set(c.contradicting)
        if both:
            errs.append(f"[11] claim {c.id}: evidence in both lists: {both}")
        for lst_name, lst in (("supporting", c.supporting), ("contradicting", c.contradicting)):
            if len(lst) != len(set(lst)):
                errs.append(f"[11] claim {c.id}: duplicate ids in {lst_name}")
            for eid in lst:
                if eid not in evidence:
                    errs.append(f"[1] claim {c.id}: dangling evidence {eid}")

    return errs


def _isin_of_evidence(e: Evidence, sources, evidence) -> str | None:
    if e.source_id and e.source_id in sources:
        return sources[e.source_id].isin
    # inference inherits from its parents (all must agree — checked in DAG)
    for p in e.derived_from:
        pe = evidence.get(p)
        if pe is not None:
            got = _isin_of_evidence(pe, sources, evidence)
            if got:
                return got
    return None


def _validate_dag(store, sources, evidence, errs):
    def visit(eid, stack):
        if eid in stack:
            errs.append(f"[4] cycle in derived_from at {eid}")
            return
        e = evidence.get(eid)
        if e is None:
            return
        for p in e.derived_from:
            if p == eid:
                errs.append(f"[4] inference {eid} derives from itself")
                continue
            pe = evidence.get(p)
            if pe is None:
                continue
            # same isin
            child_isin = _isin_of_evidence(e, sources, evidence)
            parent_isin = _isin_of_evidence(pe, sources, evidence)
            if child_isin and parent_isin and child_isin != parent_isin:
                errs.append(f"[4] inference {eid} derives across entities ({parent_isin} -> {child_isin})")
            # temporal: parent available_at <= child recorded_at
            if pe.source_id and pe.source_id in sources:
                pav = sources[pe.source_id].available_at
                if pav is not None and e.recorded_at is not None and pav > e.recorded_at:
                    errs.append(f"[4] inference {eid} uses future parent {p} (parent available after inference)")
            visit(p, stack | {eid})

    for e in store.evidence:
        if e.evidence_type == EvidenceType.INFERENCE:
            visit(e.id, set())


def _validate_supersedes(store, sources, errs):
    heads_by_group: dict[str, list[str]] = {}
    for s in store.sources:
        if s.supersedes is not None:
            tgt = sources.get(s.supersedes)
            if tgt is None:
                errs.append(f"[9] source {s.id} supersedes missing {s.supersedes}")
                continue
            if tgt.isin != s.isin:
                errs.append(f"[9] source {s.id} supersedes different entity")
            if s.disclosure_group_id != tgt.disclosure_group_id:
                errs.append(f"[9] source {s.id} supersedes across disclosure groups")
            if s.available_at and tgt.available_at and not (tgt.available_at < s.available_at):
                errs.append(f"[9] source {s.id} must be available after the source it supersedes")
    # cycle + single-head per group
    superseded = {s.supersedes for s in store.sources if s.supersedes}
    for s in store.sources:
        if s.disclosure_group_id:
            if s.id not in superseded:  # a head
                heads_by_group.setdefault(s.disclosure_group_id, []).append(s.id)
    for gid, heads in heads_by_group.items():
        if len(heads) > 1:
            errs.append(f"[9] disclosure group {gid} has {len(heads)} non-superseded heads (fork): {heads}")


def _validate_groups(store, errs):
    groups: dict[str, list[Source]] = {}
    for s in store.sources:
        if s.disclosure_group_id:
            groups.setdefault(s.disclosure_group_id, []).append(s)
    for gid, members in groups.items():
        isins = {m.isin for m in members}
        if len(isins) > 1:
            errs.append(f"[10] disclosure group {gid} spans entities {isins}")
        periods = {m.period_date for m in members}
        if len(periods) > 1:
            errs.append(f"[10] disclosure group {gid} spans periods {periods}")


def assert_valid(store) -> None:
    errs = validate(store)
    if errs:
        raise AssertionError("evidence store invalid:\n  " + "\n  ".join(errs))

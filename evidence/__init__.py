"""Drishti evidence layer — provenance-complete, point-in-time-safe evidence for one company.

See .agents/proposals/001-evidence-layer.md (v3) for the design and its named limits.
"""
from .models import (  # noqa: F401
    AvailProvenance, Claim, DocumentClass, Evidence, EvidenceType, Locator, Publisher,
    QueryResult, ResponseMeta, RetrievalOutcome, RetrievalRequest, RetrievalStatus,
    Source, SourceType, SpeakerRole,
)
from .store import EvidenceStore  # noqa: F401
from .validate import validate, assert_valid, statement_is_extractive  # noqa: F401

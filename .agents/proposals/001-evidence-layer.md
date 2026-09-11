# Proposal — Evidence Layer v2 (Task 001)

**Author:** Claude · **Status:** revised after Codex review · **Company:** INFY

v1 is in git history. This v2 responds to `../reviews/001-evidence-layer.md`.

---

## Review response (disposition of every finding)

| Codex finding | Disposition | Addressed by (this doc) |
|---|---|---|
| C1 `document_date` conflates period vs availability | **Accept** | Split into `period_date` (descriptive, never a key) + `available_at` (the only point-in-time key) |
| C2 `retrieved_at` ≠ availability; no tz | **Accept** | `available_at` = source's own publication timestamp, tz-aware; `retrieved_at` is audit-only; naive datetimes rejected |
| C3 one `source_id` contradicts multi-source inference | **Accept** | FACT/CLAIM → exactly one `source_id`; INFERENCE → no source, `derived_from` ≥1 |
| C4 excerpt may not support statement | **Accept concern; scope the fix** | FACT/CLAIM must be **extractive** (token/number-overlap checked); all paraphrase/causality forced into INFERENCE. Full entailment not mechanized — residual gated at review time (stated honestly) |
| H5 URL ≠ reproducible provenance | **Accept** | Store raw captured payload + `content_sha256`; fixtures ARE the raw payloads; stale source → hash-mismatch test |
| H6 `derived_from` unchecked | **Accept (enforceable subset)** | `validate()`: exists, no self, DAG, parent `available_at` ≤ child, same-entity. Relevance = residual |
| H7 same evidence supports contradictory claims | **Accept** | Evidence can't be in both lists of one claim; FACT must be self-contained (comparison basis in the statement), interpretation → INFERENCE |
| H8 destructive dedupe | **Accept; redesign** | No collapsing. `disclosure_group_id` (NSE+BSE copies = one independent signal) + `supersedes` (corrections retain both) |
| H9 no partial success | **Accept** | Add `PARTIAL` status carrying valid evidence + failure detail |
| H10 failure disappears at query | **Accept** | `query()` returns `{evidence, outcomes}`; outcomes for the scope are mandatory — a consumer can't get `[]` without seeing failures |
| H11 `NO_DATA` ambiguous | **Accept (partial)** | `NO_DATA` requires a *validated* empty (structure valid + request echo matches); else `PARSE_FAILED`. Wrong-scrip test. Residual: silent upstream change needs a canary (future) |
| M12 no document location | **Accept** | `locator` on Evidence (page/section/char-range) |
| M13 role/authority not encoded; publisher free text | **Accept** | `publisher` + `document_class` become enums encoding authority |
| M14 `confidence` unsupported | **Accept** | **Removed from v1.** Claim stays (criteria 7/8); confidence returns later when computable |
| M15 one adapter per type too coarse | **Accept** | Adapters keyed by (source_type × format) |
| M16 test plan omits false-confidence cases | **Accept** | Test plan expanded to exactly that list |
| M17 round-trip ≠ validation | **Accept** | Standalone `validate()` invariant-checker, separate from serialization |

**Scope-honesty note (the one place I push back on framing, not substance):** C4 and part of H6 demand proof that an excerpt *semantically supports* a statement and that derived evidence is *relevant*. Full natural-language entailment is AI-complete and will not be "solved" inside a dataclass layer — claiming otherwise would be the exact dishonesty this task exists to prevent. v2 instead **removes the pathway from the checkable tier** (FACT/CLAIM must be extractive; anything interpretive is INFERENCE with parents) and adds a mechanical token-overlap gate that catches gross drift. The residual semantic check is an explicit review gate at deep-dive time, named as a known limit — not pretended away.

**Ponytail scope discipline:** v2 adds fields, enums, and one `validate()` function — cheap. It does **not** build auto-correction-detection, auto-disclosure-grouping, or a canary system now; the *structure* to represent supersession/grouping/partial exists and is validated, but auto-population stays thin for one company (INFY). Structure prevents misrepresentation; extraction sophistication grows in later tasks.

---

## OBJECTIVE

Unchanged from v1: a normalized, provenance-complete evidence store for one company that downstream research reasons over. v2 makes provenance and point-in-time integrity **enforced by `validate()` and tests**, not merely represented by populated fields.

---

## DATA MODEL (v2)

Enums (controlled vocab):
```
SourceType      = FILING | TRANSCRIPT | FUNDAMENTALS | RATING | QUOTE | NEWS
DocumentClass   = EXCHANGE_FILING | EARNINGS_RELEASE | TRANSCRIPT | INVESTOR_PRESENTATION
                | RATING_ACTION | ANNUAL_REPORT | QUOTE | MEDIA     # encodes authority
Publisher       = NSE | BSE | SCREENER | CRISIL | ICRA | CARE | SEBI | MEDIA
AvailProvenance = EXACT | INFERRED_EOD | UNKNOWN
EvidenceType    = FACT | MANAGEMENT_CLAIM | INFERENCE
RetrievalStatus = OK | PARTIAL | NO_DATA | FETCH_FAILED | PARSE_FAILED
```

```
Source
  id
  entity                 # ISIN preferred, else ticker — the store is single-entity
  url
  publisher              # Publisher enum
  source_type            # SourceType
  document_class         # DocumentClass (authority tier)
  period_date            # date | None   — WHAT the content is about (qtr-end). Descriptive.
                         #                 NEVER used as a point-in-time key.
  available_at           # datetime, tz-aware (UTC) — WHEN it became public. The ONLY PIT key.
  avail_provenance       # EXACT (source's own timestamp) | INFERRED_EOD (only a date known ->
                         #   end-of-day IST, conservative) | UNKNOWN (excluded from PIT queries)
  retrieved_at           # datetime, tz-aware — when WE fetched. Audit only, never a PIT key.
  content_sha256         # hash of the raw captured payload (immutable identity)
  raw_ref                # pointer to the stored raw payload (fixture path / blob id)
  supersedes             # source_id | None   — a corrective filing points at the original
  disclosure_group_id    # same underlying disclosure across venues (NSE+BSE) share this

Evidence
  id
  evidence_type          # EvidenceType
  source_id              # required iff FACT|MANAGEMENT_CLAIM ; None iff INFERENCE
  derived_from           # [evidence_id] : non-empty iff INFERENCE ; empty otherwise
  statement              # normalized one-line claim; FACT/CLAIM must be EXTRACTIVE + self-contained
  excerpt                # verbatim span from the source backing `statement`
  locator                # page/section/row/char-range within the source (audit trail)
  as_of                  # date | None : the date the statement speaks to

Claim
  id
  statement
  supporting             # [evidence_id]
  contradicting          # [evidence_id]   (may be non-empty with supporting; disjoint from it)
  # confidence REMOVED in v1

RetrievalOutcome
  source_ref             # what was attempted (url / scrip / identifier)
  status                 # RetrievalStatus
  evidence               # [Evidence]  (present for OK and PARTIAL)
  detail                 # why NO_DATA / what failed / which fraction parsed
```

## INVARIANTS enforced by `validate(store)`

1. **IDs** unique; every `source_id` / `derived_from` / claim-link resolves (referential integrity).
2. **Datetimes** all tz-aware; naive → invalid.
3. **Point-in-time key discipline:** PIT queries filter on `available_at` only; records with `avail_provenance==UNKNOWN` excluded by default. `period_date` is never a filter key.
4. **Source vs inference:** FACT/CLAIM → exactly one `source_id`, empty `derived_from`. INFERENCE → `source_id is None`, `derived_from` ≥1.
5. **derived_from graph:** no self-reference, acyclic (DAG), every parent `available_at` ≤ child `available_at`, every parent same `entity`.
6. **Extractive FACT/CLAIM:** numbers, dates, and named entities in `statement` must appear in `excerpt` (token/number overlap). Paraphrase/causality is not FACT/CLAIM — it must be INFERENCE.
7. **Claim link sanity:** `supporting ∩ contradicting == ∅`; no duplicate IDs within a list.
8. **Content identity:** re-hashing `raw_ref` equals `content_sha256` (stale/mutated source detected).

## FLOW (v2)

```
raw captured payload (fixture = the stored raw + its hash)
  -> adapter[(source_type, format)]  (pure)
  -> RetrievalOutcome { OK|PARTIAL, [Evidence] }  |  { NO_DATA|FETCH_FAILED|PARSE_FAILED, detail }
  -> EvidenceStore.add(outcome)        (records Sources, Evidence, AND non-OK outcomes)
  -> validate(store)                   (all invariants above)
  -> QueryResult = store.query(type=, as_of<=cutoff, ...) -> { evidence:[...], outcomes:[...] }
                                        (outcomes for the scope are ALWAYS returned)
  -> Claim.link(supporting, contradicting)
  -> (Task 003) /deep-dive reasons over QueryResult, and MUST surface outcomes + contradictions
```

Adapters for INFY (per source_type × format): `bse_announcement_row`, `nse_filing_pdf`,
`screener_section`, `concall_pdf`, `rating_snippet`, `quote_dict`.

## ASSUMPTIONS

- Python 3.14 (>=3.12), stdlib only; sole dev dep `pytest`.
- Live fetching stays in `nse-mcp` / `india_data_client.py`; this layer ingests their output. Fixtures are the captured raw payloads, so tests are offline + reproducible.
- IST→UTC handled explicitly; `INFERRED_EOD` uses 23:59:59 IST for date-only sources (conservative).
- One entity (INFY), JSON-on-filesystem store, no DB.

## FAILURE MODES still open after v2 (named, not hidden)

- **Semantic entailment** (excerpt truly supports statement) — mechanized only as token-overlap; full check is a review gate. Residual.
- **Relevance of derived_from** — structural checks pass; topical relevance is review-gated. Residual.
- **Silent upstream API change** returning valid-looking empty — `NO_DATA` validation catches wrong-scrip/malformed, not a genuinely-changed-but-well-formed feed; needs a known-nonempty canary (future task).
- **Auto-detection** of supersession/disclosure-grouping — structure exists; auto-population is later. v1 sets these when the adapter knows them, else leaves them null.

## TEST PLAN (v2)

Fixtures (real INFY raw payloads + hashes, checked in): Screener section, BSE filing row, concall excerpt, quote, rating snippet, news item; plus failure fixtures: empty-but-valid BSE response, garbled non-JSON, wrong-scrip echo.

Tests — the false-confidence cases Codex named, each an assertion:
1. Each (type×format) adapter → Source+Evidence with full provenance (criteria 1–6, 12).
2. No Evidence without a Source for FACT/CLAIM; INFERENCE has no source but ≥1 `derived_from` (C3, criterion 10).
3. `period_date=30-Jun` with `available_at=17-Jul` is **excluded** from a 10-Jul PIT query (C1).
4. Same-day filing published 16:30 IST is excluded from a 09:00 IST same-day cutoff (C2).
5. Empty-but-valid → `NO_DATA`; garbled → `PARSE_FAILED`; wrong-scrip echo → `PARSE_FAILED` not `NO_DATA` (H11, criterion 11).
6. Partial transcript parse → `PARTIAL` with the valid excerpts + failure detail (H9).
7. `query("latest rating")` on a failed rating fetch returns `{evidence:[], outcomes:[FETCH_FAILED]}` (H10).
8. Mutated raw payload → `content_sha256` mismatch flagged (H5).
9. NSE+BSE copies share `disclosure_group_id`; counted as one independent signal, both retained (H8).
10. Corrective filing `supersedes` original; both retained; current-view picks the correction (H8).
11. INFERENCE with self-ref / cycle / future-parent / cross-entity parent → `validate()` fails (H6).
12. Evidence in both `supporting` and `contradicting` of one claim → `validate()` fails (H7).
13. FACT whose numbers/entities are absent from `excerpt` → `validate()` fails (C4).
14. Store serialize→deserialize→`validate()` still passes; naive datetime or dangling ref → fails (M17).

## Definition of done (v2 addition)

Beyond the 12 criteria: define what an auditable `/deep-dive` must display when the layer
reports a gap — (a) surface the `outcomes` (a failed/`NO_DATA` source is shown, never silently
omitted); (b) show "unavailable as of <cutoff>" for records not yet public; (c) surface
contradictions rather than silently picking a side. This is the acceptance bar for the
"can it back an auditable INFY deep-dive?" experiment.

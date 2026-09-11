# Proposal — Evidence Layer v3 (Task 001)

**Author:** Claude · **Status:** revised after Codex round 2; **escalated to human** · **Company:** INFY

v1/v2 are in git history. This v3 responds to `../reviews/001-evidence-layer-round2.md`.
Per the max-3-cycles rule, this is the final agent cycle; the boundary decision at the end
is the human's.

---

## Round-2 response (disposition)

**Two blockers — accepted, fixed structurally:**
- **Unknown availability:** `available_at` is now `Optional[datetime]`, and is `None` **iff**
  `avail_provenance == UNKNOWN`. UNKNOWN sources are excluded from every point-in-time query by
  construction. No invented timestamps.
- **Inference availability:** `Evidence.recorded_at` (tz-aware) is added, **required for
  INFERENCE**. An inference's effective availability *is* its `recorded_at`, and `validate()`
  requires `recorded_at ≥ max(parent.available_at)`. The DAG rule is now implementable.

**The mechanical win that closes H5 / M12 / new-5 together — excerpt bound to raw:**
- `locator` becomes structured: `(unit, char_start, char_end)` into the **canonical text** of
  `raw_ref`. `validate()` asserts `canonical_text(raw_ref)[char_start:char_end] == excerpt`.
  The excerpt is now provably extractable from the exact hashed bytes at the stated offsets. A
  swapped payload changes the hash; a fabricated excerpt/locator fails the substring check.

**C4 / new-4 (negation, direction, unit, period) — strengthened, not hand-waved:**
- A FACT/CLAIM `statement` must be a **normalized quotation of a contiguous span of the
  excerpt** — verbatim, or a numeric/date normalization of tokens *present in that span* only.
  `validate()` checks the statement reconstructs from the span. "Management expects improvement"
  cannot be a FACT/CLAIM over a span that says "do **not** expect improvement" — the negation is
  in the span and not in the statement, so it fails. Direction/unit/period flips fail the same
  way. **Interpretation, comparison, and causality are not FACT/CLAIM — they are INFERENCE**
  (labeled, sourced by `derived_from`, review-gated).

**Accepted + fixed (cheap, correct):** outcome `request` descriptor + `response_meta` (H10/H11/#8);
`MANAGEMENT_CLAIM` gains `speaker`+`speaker_role`, validated `MANAGEMENT` (M13/transcript hole);
`publisher × document_class` compatibility matrix (M13/#10); `supersedes` DAG + fork validation
(#6); ISIN as canonical identity + adapter normalization (#9); `PARTIAL` completeness denominator
(H9); news adapter added (M15); expanded tests (M16).

**Held as permanent residual — review-gated, NOT blockers (this is the escalation):**
No schema decides these; naming them honestly is the correct engineering, and treating them as
blockers means never shipping:
1. Whether an INFERENCE's reasoning is *relevant and true* (`derived_from` is structurally
   validated — exists, DAG, same-entity, temporally valid; topical relevance + logical soundness
   are a review gate).
2. Whether a `disclosure_group` assignment is *correct* (grouped sources are validated to share
   ISIN + period; correctness of "same underlying disclosure" is review). **Therefore v1 does no
   independence/corroboration counting at all** — confidence is already dropped, so the miscount
   danger (#7) cannot materialize in v1.
3. Whether a chosen authority class *matches the real-world artifact* beyond enum-compatibility
   (raw is captured, hashed, and locator-bound so a human can audit; automated truth = no).
4. `INFERRED_EOD` safety depends on the date being a *publication* date — so the adapter must
   assert `date_kind == PUBLICATION`; a board/period date may **not** be used for EOD inference
   (new-3). Where only a non-publication date exists → `UNKNOWN`, excluded from PIT.

---

## DATA MODEL (v3 — final)

```
SourceType      = FILING | TRANSCRIPT | FUNDAMENTALS | RATING | QUOTE | NEWS
DocumentClass   = EXCHANGE_FILING | EARNINGS_RELEASE | TRANSCRIPT | INVESTOR_PRESENTATION
                | RATING_ACTION | ANNUAL_REPORT | QUOTE | MEDIA
Publisher       = NSE | BSE | SCREENER | CRISIL | ICRA | CARE | SEBI | MEDIA
AvailProvenance = EXACT | INFERRED_EOD | UNKNOWN
SpeakerRole     = MANAGEMENT | ANALYST | OPERATOR | OTHER
EvidenceType    = FACT | MANAGEMENT_CLAIM | INFERENCE
RetrievalStatus = OK | PARTIAL | NO_DATA | FETCH_FAILED | PARSE_FAILED

Source
  id
  isin                    # CANONICAL entity identity (INFY = INE009A01021); required
  ticker                  # display only, never an identity key
  url
  publisher               # Publisher enum
  document_class          # DocumentClass — must be compatible with publisher (matrix)
  period_date             # date|None — WHAT it's about; descriptive; never a PIT key
  available_at            # datetime tz-aware | None  (None iff avail_provenance==UNKNOWN)
  avail_provenance        # EXACT | INFERRED_EOD (requires a PUBLICATION date) | UNKNOWN
  retrieved_at            # datetime tz-aware — audit only
  content_sha256          # hash of the raw captured payload
  raw_ref                 # pointer to stored raw payload; canonical_text(raw_ref) is defined
  supersedes              # source_id | None
  disclosure_group_id     # descriptive; validated to share isin+period; no counting in v1

Evidence
  id
  evidence_type
  source_id               # required iff FACT|MANAGEMENT_CLAIM ; None iff INFERENCE
  derived_from            # [evidence_id] : non-empty iff INFERENCE ; empty otherwise
  recorded_at             # datetime tz-aware : required iff INFERENCE (its effective availability)
  statement               # FACT/CLAIM: normalized quotation of the excerpt span (validated)
  excerpt                 # verbatim; == canonical_text(raw_ref)[locator.start:locator.end]
  locator                 # (unit, char_start, char_end) into canonical_text(raw_ref)
  speaker                 # str|None ; speaker_role required iff MANAGEMENT_CLAIM
  speaker_role            # SpeakerRole|None ; must be MANAGEMENT for a MANAGEMENT_CLAIM
  as_of                   # date|None : the date the statement speaks to (descriptive)

Claim
  id ; statement ; supporting[] ; contradicting[]      # disjoint; no confidence in v1

RetrievalOutcome
  request                 # {isin, source_type, document_class?, window_from, window_to,
                          #  attempted_id, attempted_at}   — defines scope + not-attempted
  status                  # RetrievalStatus
  response_meta           # {structure_valid: bool, echo_matches: bool}
  evidence                # [Evidence] for OK|PARTIAL
  completeness            # {expected_units, parsed_units} | {"denominator": "unknown"} for PARTIAL
  detail
```

## INVARIANTS enforced by `validate(store)`

1. Unique IDs; all references resolve (source_id, derived_from, claim links, supersedes).
2. All datetimes tz-aware. PIT queries filter on `available_at` **only**; `avail_provenance==UNKNOWN`
   (⇒ `available_at is None`) excluded. `period_date` and `as_of` are never filter keys. *(C1, blocker1)*
3. FACT/CLAIM → exactly one `source_id`, empty `derived_from`, `recorded_at is None`.
   INFERENCE → `source_id is None`, `derived_from` ≥1, `recorded_at` present. *(C3)*
4. `derived_from` graph: no self, acyclic, every parent same `isin`, every parent
   `available_at ≤ child.recorded_at`. *(blocker2, H6-structural)*
5. Excerpt binding: `canonical_text(raw_ref)[locator.start:locator.end] == excerpt`. *(H5, M12, new5)*
6. Extractive tier: FACT/CLAIM `statement` reconstructs as a normalized quotation of a contiguous
   span of `excerpt` (numbers/dates normalized, nothing added/negated). *(C4, new4)*
7. `MANAGEMENT_CLAIM` requires `speaker_role == MANAGEMENT`. *(M13, transcript-speaker)*
8. `publisher × document_class` in the compatibility matrix (e.g. EXCHANGE_FILING ⇒ NSE|BSE;
   publisher MEDIA ⇒ document_class MEDIA). *(M13, #10)*
9. `supersedes`: same isin, same disclosure_group, `target.available_at < this.available_at`,
   acyclic; exactly one non-superseded head per group else FORK error. *(#6)*
10. `disclosure_group_id`: grouped sources share isin + period_date. (No counting in v1.) *(#7 bound)*
11. Claim: `supporting ∩ contradicting == ∅`; no duplicate IDs in a list. *(H7-structural)*
12. `content_sha256` == hash(raw_ref). *(H5)*
13. `NO_DATA` requires `response_meta.structure_valid and response_meta.echo_matches`; else the
    outcome must be `PARSE_FAILED`/`FETCH_FAILED`. *(H11)*

## FLOW

```
raw captured payload (fixture = stored raw + sha256)
  -> adapter[(source_type, format)]  (pure; normalizes isin; emits locator offsets)
  -> RetrievalOutcome { OK|PARTIAL, [Evidence] } | { NO_DATA|FETCH_FAILED|PARSE_FAILED, response_meta }
  -> EvidenceStore.add(outcome)          (records Sources, Evidence, AND every outcome w/ request)
  -> validate(store)                     (invariants 1-13)
  -> QueryResult = store.query(source_type, document_class?, as_of<=cutoff)
        -> { evidence:[available_at<=cutoff, provenance!=UNKNOWN], outcomes:[scope-matched] }
           # scope = request.isin+source_type+window overlapping the query; distinguishes
           #         failed / unavailable-as-of / not-attempted
  -> Claim.link(supporting, contradicting)
  -> (Task 003) /deep-dive reasons over QueryResult; MUST surface outcomes + contradictions + as-of gaps
```

Adapters (source_type × format): `bse_announcement_row`, `nse_filing_pdf`, `screener_section`,
`concall_pdf`, `rating_snippet`, `quote_dict`, **`news_item`**.

## TEST PLAN (v3)

Fixtures (real INFY raw + sha256): screener section, bse filing row, concall excerpt, quote,
rating snippet, news item; failure fixtures: empty-but-valid, garbled non-JSON, wrong-scrip echo.
Tests — each a concrete assertion:
1. Each adapter → full provenance incl. isin, available_at, locator-bound excerpt.
2. FACT/CLAIM single-source empty-derived; INFERENCE no-source ≥1-parent with recorded_at.
3. `period_date=30-Jun, available_at=17-Jul` excluded from a 10-Jul PIT query.
4. 16:30 IST filing excluded from a same-day 09:00 IST cutoff (tz-correct, not just tz-aware).
5. INFERRED_EOD refused when the known date is a board/period (non-publication) date → UNKNOWN.
6. empty-valid→NO_DATA; garbled→PARSE_FAILED; wrong-scrip echo→PARSE_FAILED.
7. partial transcript → PARTIAL with expected/parsed denominator.
8. query on a failed rating fetch → `{evidence:[], outcomes:[{status:FETCH_FAILED, request:{...rating window}}]}`;
   not-attempted source distinguishable from failed.
9. mutated raw → sha256 mismatch; fabricated locator/excerpt → invariant-5 failure.
10. statement with dropped negation / flipped direction / wrong unit / wrong period → invariant-6 failure.
11. MANAGEMENT_CLAIM on an ANALYST-role span → invariant-7 failure.
12. incompatible publisher×document_class → invariant-8 failure.
13. NSE+BSE copies grouped (share isin+period); correction supersedes original; two corrections of
    one original → FORK error.
14. inference self-ref / cycle / future-parent (parent.available_at > child.recorded_at) / cross-isin → fail.
15. evidence in both claim lists → fail; serialize→deserialize→validate stable; naive datetime → fail.

## Definition of done — unchanged from v2
The 12 task criteria, plus: an auditable `/deep-dive` must surface `outcomes` (failed/unavailable/
not-attempted), show "unavailable as of <cutoff>", and surface contradictions rather than pick a side.

---

## HUMAN ESCALATION (the boundary decision is yours)

v3 mechanically closes both blockers and the "manually asserted vs mechanically connected" gap
for: point-in-time integrity, excerpt↔raw binding, the extractive tier (incl. negation/unit/
period), inference timing, outcome scope, supersession, and enum authority-compatibility.

What remains is **permanently not schema-decidable** and is by design a review gate, not a bug:
inference relevance/truth, disclosure-group *correctness*, and authority-class-matches-artifact.
An adversarial reviewer with no ship-constraint will keep surfacing this residual; that does not
make it a blocker. Your call:

- **(A) Implement v3** — build it, with the residuals as named review gates. *(Claude's recommendation.)*
- **(B) One more mechanization** — pick a specific residual to push further before coding.
- **(C) Rescope** — decide any of the residuals is out of Task 001 entirely.

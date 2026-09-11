# Proposal — Evidence Layer v1 (Task 001)

**Author:** Claude · **Status:** review · **Company:** INFY

---

## OBJECTIVE

A normalized, provenance-complete evidence store for one company's research.

Every record carries where it came from, when, and what kind of statement it is. The
store distinguishes **fact** from **management claim** from **analyst inference**,
lets contradictory evidence coexist, and represents *missing data* and *retrieval
failure* as explicit values — never as silence or a fabricated blank. Downstream
research reasons over this store instead of over raw model memory.

Non-goal: fetching. This layer ingests already-retrieved payloads so it stays
testable offline. Live wiring is a later task.

---

## ARCHITECTURE

Python, stdlib-only core (matches `india_data_client.py`; trivial fixtures/tests).

```
evidence/
  models.py     # dataclasses + enums: Source, Evidence, Claim, RetrievalOutcome
  ingest.py     # pure adapters: raw source payload -> RetrievalOutcome
  store.py      # collect, query, JSON (de)serialize
fixtures/       # real INFY payloads captured once, checked in (incl. failure cases)
tests/          # pytest, offline, deterministic
```

Design stance: the layer is **source-agnostic and pure**. An ingest adapter takes a
raw payload (a Screener section string, a BSE filing dict, a transcript excerpt, a
quote dict) and returns records — no network, no clock reads except an injected
`retrieved_at`. That purity is what makes criterion 12 (reproducibility) hold.

Deliberately minimal: four types, no factories, no ORM, no base classes. If a type
doesn't earn its place against the 12 criteria, it isn't here.

---

## DATA MODEL

Enums:
```
SourceType      = FILING | TRANSCRIPT | FUNDAMENTALS | RATING | QUOTE | NEWS
DateProvenance  = EXACT | INFERRED | UNKNOWN
EvidenceType    = FACT | MANAGEMENT_CLAIM | INFERENCE
RetrievalStatus = OK | NO_DATA | FETCH_FAILED | PARSE_FAILED
```

```
Source
  id
  url
  publisher          # NSE | BSE | Screener | CRISIL | ...
  source_type        # SourceType
  document_date      # date | None       (the filing/call/period date)
  date_provenance    # DateProvenance    (EXACT if from source; INFERRED if computed;
                     #                     UNKNOWN if absent — an inferred date can
                     #                     NEVER pass as sourced)
  retrieved_at       # datetime (injected, not read from wall clock in ingest)

Evidence
  id
  source_id          # -> Source (mandatory; no evidence without a source)
  evidence_type      # EvidenceType
  statement          # normalized one-line claim text
  excerpt            # verbatim span from the source backing `statement`
  as_of              # date | None   (the date the statement speaks to, e.g. qtr-end)
  derived_from       # [evidence_id]  MUST be non-empty iff evidence_type==INFERENCE,
                     #                and MUST be empty otherwise

Claim
  id
  statement
  supporting         # [evidence_id]
  contradicting      # [evidence_id]   (may be non-empty alongside supporting)
  confidence         # LOW | MEDIUM | HIGH | None

RetrievalOutcome     # what every ingest attempt returns
  source_ref         # url / identifier attempted
  status             # RetrievalStatus
  evidence           # [Evidence]  (only when status==OK)
  detail             # human string: why NO_DATA / why FETCH_FAILED / parse error
```

### The three invariants that make this trustworthy

1. **Provenance-at-construction.** `Source` cannot exist without `url`,
   `source_type`, `retrieved_at`. `document_date=None` is allowed only with
   `date_provenance=UNKNOWN`. A computed date (e.g. the old "period-end + 45 days"
   hack) is stored `INFERRED` and is **excluded from point-in-time queries by
   default** — killing the look-ahead-bias trap.
2. **Inference cannot masquerade as fact.** `INFERENCE` evidence must list the
   `derived_from` evidence IDs it reasons from and may **not** cite a source document
   as if the document stated the inference. `FACT` / `MANAGEMENT_CLAIM` must have
   empty `derived_from`. This structurally blocks the "cites a filing, invents the
   causality" failure.
3. **Failure is a value.** Ingest never returns `[]` on error. `NO_DATA` (source
   genuinely empty) and `FETCH_FAILED` / `PARSE_FAILED` (something broke) are
   different statuses — directly fixing the current BSE-tool defect where a garbled
   response silently becomes "no filings".

---

## FLOW

```
raw payload (fixture, or later a live pull)
      -> ingest adapter (pure)
      -> RetrievalOutcome { OK, [Evidence] }  |  { NO_DATA | FETCH_FAILED | PARSE_FAILED, detail }
      -> EvidenceStore.add(outcome)           (records Sources + Evidence; logs non-OK)
      -> store.query(type=, as_of<=, exclude inferred-dates)
      -> Claim.link(supporting=[...], contradicting=[...])
      -> (Task 003) /deep-dive reasons over the store
```

For INFY, six ingest adapters, one per source type: FILING (BSE announcement row),
TRANSCRIPT (concall excerpt), FUNDAMENTALS (Screener section), RATING (CRISIL
snippet), QUOTE (price/valuation dict), NEWS (whitelist item).

---

## ASSUMPTIONS

- Python 3.14 (target >=3.12); stdlib only; sole dev dep `pytest`.
- Live fetching stays in `nse-mcp` / `india_data_client.py`; this layer ingests their
  output. A live-adapter bridge is a later task, out of scope here.
- JSON-on-filesystem store for v1 (no DB). One company (INFY).
- `retrieved_at` is injected by the caller, so tests are deterministic.

---

## FAILURE MODES (self-attacked — Codex, push harder on these)

- **Inferred dates leaking into point-in-time filtering** → mitigated by
  `DateProvenance` + default exclusion. *Open:* is default-exclude the right default,
  or should the query force an explicit choice?
- **INFERENCE posing as FACT** → mitigated by the `derived_from` invariant. *Open:*
  nothing stops a careless caller from mislabeling a FACT — the invariant checks
  structure, not truth of the label.
- **Excerpt drift** — `excerpt` may not actually contain `statement` (paraphrase, or
  wrong span). v1 stores both but cannot mechanically prove the excerpt supports the
  statement. *Flagged as an open weakness*, not solved.
- **Duplicate evidence across the NSE+BSE merge** — same filing from both exchanges.
  Proposed dedupe key = hash(publisher-agnostic statement + as_of); *unsure this is
  right* — a near-duplicate with different wording slips through. Want Codex's view.
- **Over-abstraction** — four types may still be one too many; is `Claim` needed in
  v1, or does it belong to Task 002 (research packet)? Argue it either way.

---

## TEST PLAN

Fixtures (real INFY payloads, captured once, checked in):
- one each: Screener fundamentals section, BSE filing row, transcript excerpt, quote,
  rating snippet, news item.
- failure fixtures: an **empty** BSE response, and a **garbled non-JSON** response.

Tests (all offline, deterministic):
1. Each source type ingests to a Source + Evidence with complete provenance (criteria 1–6).
2. Invariant: no Evidence without a Source (criterion 10).
3. Invariant: `INFERENCE` requires `derived_from`; `FACT`/`CLAIM` forbid it (criterion 6/10).
4. Empty response → `NO_DATA`; garbled response → `PARSE_FAILED`, **not** `NO_DATA`
   (criterion 11 — the BSE-bug regression test).
5. Inferred date → `date_provenance=INFERRED` and excluded from a point-in-time query.
6. A claim with both supporting and contradicting evidence round-trips (criteria 7–8).
7. Store serialize → deserialize is stable (criterion 12).

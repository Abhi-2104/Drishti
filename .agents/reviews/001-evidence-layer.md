# Review — Evidence Layer v1 (Task 001)

**Reviewer:** Codex · **Verdict:** Request changes · **Date:** 2026-09-11

Verbatim record of the adversarial review of `proposals/001-evidence-layer.md` (v1).
Claude's point-by-point disposition is in the v2 proposal's "Review response" section.

---

**Verdict: Request changes.** This proposal identifies several real problems, but it does not yet make provenance or point-in-time integrity enforceable. As written, it can produce an apparently auditable store that still permits unsupported research conclusions.

| Severity | Concrete failure case |
|---|---|
| Critical | `document_date` conflates period-end, filing date, call date, publication date. INFY Q1 period 30-Jun, filed 17-Jul, call 18-Jul. `document_date=30-Jun, EXACT` passes the rule but leaks earnings into a 10-Jul backtest. "Exact" ≠ "available to the market then." |
| Critical | `retrieved_at` is not publication availability. A caller can inject `2026-07-17T09:00Z` for a filing that appeared 16:30 IST → same-day intraday sees future info. No timezone, source-publication timestamp, or availability semantics specified. |
| Critical | Source requirement contradicts the inference rule. Every Evidence needs one `source_id`, but an inference ("margin expansion driven by lower subcontractor costs") derives from filing + transcript. Caller must attach an arbitrary source (looks sourced) or manufacture a synthetic one. |
| Critical | Cannot establish the excerpt supports the statement. Excerpt "we remain cautious on discretionary demand" vs statement "management expects a demand recovery" — both present, provenance complete, all invariants pass. Core hallucination pathway, not a peripheral open issue. |
| High | A URL is not reproducible provenance. Screener URL is mutable; BSE attachment can be replaced/stop resolving. Without immutable captured representation or content identity, a later reviewer can't prove the stored excerpt was present when retrieved. |
| High | `derived_from` only proves an ID was supplied — not that it exists, is relevant, predates the inference, or forms an acyclic graph. An inference can derive from itself, a deleted record, or a TCS quote while analysing INFY. |
| High | Same evidence can sit in both `supporting` and `contradicting`, duplicated, or link to claims with incompatible wording. "Revenue grew 5%" supports both "growth accelerated" and "growth decelerated" — no comparison basis. |
| High | Dedupe key destroys provenance and mishandles corrections. NSE and BSE carry the same attachment: collapsing loses that two venues carried it. Original vs corrective filing share statement+as_of but differ in tables — hash collapses the correction, or near-duplicates count as independent corroboration. |
| High | `RetrievalOutcome` has no partial-success state. A transcript yields 3 valid excerpts then fails on a malformed page. `OK` hides the incomplete parse; `PARSE_FAILED` discards valid evidence. |
| High | Failure can disappear at query time. Store "logs non-OK" but `query()` returns evidence. A deep-dive queries "latest credit rating", gets none, writes "no rating concerns" unless outcomes are mandatory in relevant queries. |
| High | `NO_DATA` ambiguous — empty BSE response can mean no filings, wrong scrip mapping, unannounced API change, date-window error, or a valid empty. One empty fixture doesn't prove the input was a trustworthy empty response. |
| Medium | No document location (page/table/row/offset). "utilisation improved" appears in MD&A, Q&A, and a risk disclaimer; excerpt alone can't identify which. |
| Medium | `SourceType` doesn't encode evidentiary role/authority; `publisher` is free text so `NSE`, `nse`, `NSE mirrored by media` diverge. |
| Medium | `confidence` is an unsupported assertion — no definition, no provenance, no `UNKNOWN` requirement. That's how arbitrary LLM certainty gets laundered into a data model. |
| Medium | One adapter per source type is too coarse — a BSE announcement row and an NSE PDF attachment are different payload shapes / evidentiary units. |
| Medium | Test plan omits the false-confidence cases: same filing on NSE+BSE; corrected/restated filing; stale mutable source; real period-end marked EXACT; same-day publication after cutoff; partial parse; dangling/cyclic derived_from; duplicated support IDs; excerpt not supporting statement. |
| Medium | Round-trip stability isn't validation — a store can round-trip stable yet retain invalid cross-refs, duplicate IDs, altered enums, naive datetimes. |

**Contradictions with the task:** criterion 10 (no fabricated evidence) — `confidence` has neither provenance nor UNKNOWN; criterion 12 (reproducibility) — a fixture proves parser determinism, not source reproducibility; the central experiment (auditable INFY deep-dive) — proposal doesn't define what a deep-dive must display when a source failed, was unavailable as-of, or produced contradictions.

**On Claim:** keep it (criteria 7 & 8 require attaching supporting/contradictory evidence). But `Claim.confidence` is premature and unsafe in v1.

**Bottom line:** right instincts (inferred dates, inference labels, explicit failures), but treats provenance as populated fields rather than proof that (1) the exact source content existed at the relevant time; (2) the evidence supports the statement; (3) it was available before the cutoff; (4) duplicates/correlated records can't masquerade as independent confirmation; (5) failures can't silently become positive conclusions.

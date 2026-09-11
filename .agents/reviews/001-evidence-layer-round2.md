# Review Round 2 — Evidence Layer v2 (Task 001)

**Reviewer:** Codex · **Verdict:** Request changes · **Date:** 2026-09-11

Verbatim record of Codex's second adversarial pass on proposal v2. Claude's disposition
is in the v3 proposal's "Round-2 response" section. This is the final review cycle before
human escalation (per the max-3-cycles rule in `../tasks/001-evidence-layer.md`).

## Two new blocking schema contradictions
1. `available_at` is mandatory + tz-aware, yet `AvailProvenance.UNKNOWN` exists — a source
   with no publication date cannot be represented without inventing a timestamp.
2. An INFERENCE has no source and no availability field, yet the DAG rule compares parent
   availability with the child's — the child has no such timestamp. The invariant is
   unimplementable as written.

## Re-check of v1 findings (v2 result)
- C1 period leaks: partial — query API still says `as_of<=cutoff` while `Evidence.as_of` is a
  separate field; an implementer can filter the economic period date and recreate the leak.
- C2 tz: partial — tz-aware ≠ tz-correct; a 16:30 IST stamp parsed as UTC still passes.
- C3 multi-source inference: partial — inference has no availability field (see blocker 2).
- C4 excerpt↔statement: open — token overlap misses negation ("declined 100bps" vs "did not
  decline 100bps" share all tokens).
- H5 URL provenance: partial — hashing a local capture detects mutation of that capture, not
  that it came from the stated URL; a caller can swap payload + update hash.
- H6 derived graph: partial — temporal validity unimplementable (blocker 2); relevance unchecked.
- H7 conflicting claims: partial — same fact can support separately-authored contradictory
  claims; no comparison baseline enforced.
- H8 dedupe: partial — grouping/supersession manually populated, no invariant validates them.
- H9 partial parse: partial — no completeness denominator; a parse that stops after favourable
  pages can be labelled PARTIAL and feed a bullish read.
- H10 failures at query: partial — outcome lacks entity/type/window/attempted-at, so the store
  can't mechanically decide which failures belong "in scope".
- H11 NO_DATA: partial — outcome has no request echo / response structure to prove validated-empty.
- M12 locator: partial — free-form, not validated against raw_ref; "page 14" can be fabricated.
- M13 publisher/authority: partial — no compatibility rule; MANAGEMENT_CLAIM has no speaker/role,
  so an analyst question can be mislabelled as management.
- M14 confidence: closed.
- M15 adapters: partial — NEWS enum + fixture promised but no news adapter listed.
- M16 tests: partial — omits wrong-tz, ambiguous as_of, supersession forks, mis-grouping,
  fabricated locators, extractive false positives via negation, partial parse dropping bad news.
- M17 validation: mostly closed — validator doesn't yet cover supersedes, groups, entity
  consistency, publisher/class compatibility, outcome scope metadata.

## New holes introduced by v2
3. INFERRED_EOD can fabricate historical availability if the known date is a board/period date,
   not a publication date (record dated 17 Jul, PDF public 18 Jul → treated as available pre-18).
4. Extractive token check gamed by negation/direction/unit/period ("revenue fell 10%" vs "grew
   10%", "₹10 crore" vs "₹10 lakh", "FY26" vs "Q1 FY26").
5. Hash checks raw_ref, not that the excerpt/locator are extractable from the hashed raw.
6. supersedes has no temporal/graph rules — cycles, two corrections of one original, undefined
   "current view".
7. Disclosure groups are a manual confidence lever — wrong grouping hides disagreement or invents
   independent confirmation; structure doesn't establish correlation.
8. Outcomes don't define coverage — failed vs unavailable vs not-attempted indistinguishable
   without a request descriptor.
9. Entity identity unstable — "ISIN preferred else ticker" allows INFY / INFY.NS / INE009A01021
   to diverge or wrongly merge.
10. Enum combinations assert false authority — publisher=NSE + document_class=EXCHANGE_FILING on a
    captured media article; controlled vocab alone doesn't prove the class matches the artifact.

## Bottom line
v2 closes confidence outright and improves the rest, but still confuses *having fields for
integrity* with *having enough information to enforce integrity*. Resolve the unknown-availability
and inference-availability contradictions, and prove raw/locator/excerpt/outcomes/supersession/
grouping are mechanically connected, before implementation.

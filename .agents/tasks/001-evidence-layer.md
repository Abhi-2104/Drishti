# Task 001 — Evidence Layer v1

**Status:** proposal under review
**Owner (implementer):** Claude
**Reviewer (adversarial):** Codex
**Created:** 2026-09-11

## Goal

Make one company's research evidence **auditable and reproducible**. Build a
normalized evidence store that every downstream command (`/deep-dive`, `/bull-bear`,
`/entry`) will later reason *over* — so the language model stops being the database.

This is the foundation. Nothing else (research packet, thesis ledger, forecasting)
is built until this works end-to-end for one company.

## Scope

- **In:** the evidence data model, ingest adapters that normalize already-retrieved
  source payloads into evidence records, a store with query + serialize, and
  fixture-based offline tests — for **one** representative company.
- **Out (this task):** live orchestration of fetches, multi-company universe,
  forecasting/valuation, the `/deep-dive` rewrite itself, any UI. Those come later.

## Chosen company

**INFY (Infosys Ltd)** — representative because it is dual-listed (exercises the
NSE+BSE merge), holds quarterly earnings calls in English (transcript evidence),
has clean multi-year fundamentals, a credit rating, and regular exchange filings.
One company, whole pipeline, end to end.

## Acceptance criteria

1. Source URL retained.
2. Source type retained.
3. Document / filing date retained.
4. Retrieval timestamp retained.
5. Relevant excerpt retained (verbatim from the source).
6. Evidence classified as `FACT`, `MANAGEMENT_CLAIM`, or `INFERENCE`.
7. Supporting evidence can be attached to a claim.
8. Contradictory evidence can coexist on the same claim.
9. Missing / unavailable information is explicitly represented (never blank-filled).
10. No fabricated evidence — every field is sourced or explicitly unknown.
11. Retrieval **failure** is distinguishable from genuine **"no data"**.
12. Fixture-based tests make the result reproducible **without live endpoints**.

## Definition of done

- All 12 criteria demonstrably met by passing tests over checked-in fixtures.
- Fixtures include at least one **failure** case and one **no-data** case, proving
  criterion 11.
- Then the key experiment: can this evidence layer back a genuinely auditable
  `/deep-dive` for INFY? If no, fix the layer before moving to Task 002.

## Workflow (human-mediated, no orchestrator yet)

1. Claude writes a proposal (`../proposals/001-evidence-layer.md`) — design only.
2. Codex attacks the proposal (see `../reviews/`). Attack first; do not redesign.
3. Claude accepts + revises, or rejects with an explicit technical reason.
4. Only then: implement.
5. Fixtures + tests.
6. Codex reviews the implementation.
7. Human approves.

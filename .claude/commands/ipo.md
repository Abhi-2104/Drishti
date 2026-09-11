---
description: IPO application analysis on ONE public issue -> APPLY / SKIP / NEUTRAL
argument-hint: [IPO NAME or symbol]
---
Analyse the IPO $ARGUMENTS for an application decision. This is the primary-market cousin
of /deep-dive: same discipline (every claim source-backed, bear case stated hard, primary
sources outrank commentary), adapted to a company with no trading history.

PRIMARY SOURCE = the prospectus (DRHP / RHP). Get it, read it, cite it. Steps:
- Use nse-mcp get_ipos (status current/upcoming, or query by name) for the particulars and
  to confirm dates/price band. A failed feed is UNKNOWN, not "no IPO".
- Fetch the offer document (DRHP/RHP): from the IPO's offer-document link, else SEBI
  (sebi.gov.in -> Filings -> Public Issues) or the NSE/BSE IPO page. curl the PDF to the
  scratchpad and Read it (poppler). This is the document the analysis rests on.

GMP (grey-market premium) is EXCLUDED — unofficial, unregulated, easily manipulated. Never
drive a verdict from it. It is the same category as the tip-channel noise the framework bans.

Sections (cite prospectus page/section per claim):
1. WHAT IT DOES — business, segments, revenue mix, market position (prospectus + /deep-dive lens).
2. THE ISSUE — total size; FRESH issue vs OFFER-FOR-SALE split (fresh = money to the company;
   OFS = existing holders cashing out, company gets nothing); price band, lot size, dates.
3. OBJECTS OF THE ISSUE — what the fresh money funds. Growth capex/expansion = constructive;
   debt repayment = neutral-to-weak; "general corporate purposes" heavy = weak; pure OFS = the
   company raises nothing, promoters/PE exit.
4. FINANCIALS — restated 3-yr revenue, profit, margins, ROCE/ROE, debt, cash flow (trend, not a
   snapshot). Quality of growth; any pre-IPO margin/one-off flattering.
5. VALUATION — asking P/E, P/B, EV/EBITDA at the price band vs LISTED PEERS (prospectus has a
   "Basis for Issue Price" + peer-comparison section). Is it priced to leave anything on the table,
   or maxed out?
6. PROMOTER / SELLING-SHAREHOLDER INTENT — who is selling in the OFS and how much; pre- vs
   post-issue promoter holding; PE/anchor exit; lock-ins. Heavy insider/PE cash-out into the IPO
   is a red flag; skin retained is constructive.
7. RISK FACTORS — the prospectus risk section, plus litigation, contingent liabilities,
   related-party transactions, customer/supplier concentration, regulatory dependence.
8. SUBSCRIPTION (secondary, only if open) — QIB / NII / retail demand from get_ipos/live data, as
   a demand signal, not a thesis. Anchor-investor quality if disclosed.

VERDICT: APPLY / SKIP / NEUTRAL + one-line reason + the single biggest risk. State what would
change the call (e.g. final price at the lower band, weak QIB demand). If the prospectus could
not be read, say so and do NOT fake a verdict — that is a data gap, not a SKIP.

Append: "Decision support, not financial advice. Not a licensed adviser (SEBI RIA). You own the
decision. Verify against the RHP and your broker before applying."

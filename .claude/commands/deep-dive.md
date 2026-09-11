---
description: Full research on ONE name (Loop 2)
argument-hint: [TICKER]
---
Deep research on $ARGUMENTS. Expensive loop — thorough, one name. Cite sources per section:
1. WHAT IT DOES — business/segments/revenue mix (nse-mcp get_screener).
2. FUNDAMENTALS — growth, margins, ROCE/ROE, debt, cash flow over 3+ yrs (trend not snapshot);
   valuation vs own history + peers. Use nse-mcp get_screener (resolves NSE symbol, BSE code, or
   name; covers BSE-only names; cached). Fall back to Screener.in WebFetch only if the tool errors.
3. DOCUMENTS — read last 3-4 concalls; extract what CHANGED in how management talks about
   demand/margins/capex/guidance (language-shift read). Order book if any.
   HOW: get_screener returns documents.concalls[] as direct PDF URLs. curl the latest 2-3 to
   the scratchpad dir, then Read them (needs poppler-utils installed). Read the LATEST fully;
   skim priors for the shift. Don't guess §3 from numbers anymore — read the actual transcripts.
4. FLOWS & DISCLOSURE — FII/DII trend, insider trades, bulk/block, pledge trend, corporate
   actions. Insider trades/bulk-block/corp-actions: nse-mcp (get_insider_trading, get_bulk_deals,
   get_corporate_actions) — NSE-only, always call regardless of listing.
   Promoter-HOLDING trend (stake sell-down signal): get_screener.shareholding (NSE AND BSE) —
   note this is holding %, NOT pledge.
   FILINGS FEED — call get_nse_announcements AND get_bse_announcements for EVERY name, not
   just BSE-only ones (most large/midcaps list on both exchanges, and the two feeds aren't
   always identical — a filing can land on one before/instead of the other). Merge the two
   lists, dedupe by date+headline, note if one exchange carries something the other doesn't.
   For a BSE-only name, get_nse_announcements returns empty — that's expected, not an error.
   PLEDGE %: not in Screener; read it from the merged feed filtered to Pledge/SAST/Encumbrance
   category, or from concall/AR.
5. NEWS — Tier 1/2 whitelist items material to the name.
6. RATINGS — latest CRISIL/ICRA/CARE action.
7. DRIVERS & RISKS — even-handed. End: 3-sentence synthesis + suggest /bull-bear then /entry.

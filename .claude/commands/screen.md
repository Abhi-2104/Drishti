---
description: Find candidate stocks by criteria
argument-hint: [criteria]
---
Screen for: $ARGUMENTS
1. Fundamentals filter via get_screener (resolves NSE symbol / BSE code / name); respect
   config/scoring.md quality floor unless overridden here.
2. Market-driven discovery when the criteria are momentum- or flow-shaped: get_sector_rotation
   (which sectors are Leading or Improving — hunt candidates in rotating-in sectors),
   get_top_gainers / get_top_losers / get_most_active (movers by % and value), get_nifty_indices,
   get_top_bulk_buys / get_top_bulk_sells (what large clients are accumulating or distributing).
3. Ranked shortlist (max 10): ticker, 1-line what-it-does, the 2-3 metrics that earned it,
   any immediate red flag — pledge / insider-sell / rating — from get_insider_trading +
   get_bse_announcements + get_screener (shareholding section).
4. No deep-dive. Suggest the top 1-3 for /deep-dive. Every number cites source + date.

---
description: Cheap daily monitoring digest across the watchlist (Loop 1)
---
Run the MONITORING loop cheaply. No deep analysis here — scan, dedupe, escalate.
1. Read config/watchlist.md + config/news-sources.md.
2. Market context (one cheap sweep): get_market_status (open/closed) + get_nifty_indices for
   index and sector tone; get_top_gainers / get_top_losers / get_most_active to catch which
   watchlist names moved or spiked on volume today.
3. Watchlist flow + disclosure deltas via nse-mcp: get_insider_trading, get_bulk_deals,
   get_block_deals, get_corporate_actions, get_fii_dii_activity (market-level context only,
   not per-stock), plus new items from get_nse_announcements + get_bse_announcements
   (pledge / SAST (substantial acquisition of shares and takeovers) changes included).
4. Smart-money sweep (discovery): get_top_bulk_buys / get_top_bulk_sells / get_latest_bulk_deals
   — the largest single-client and institutional trades today. Flag accumulation or
   distribution in a watchlist name, or a strong off-watchlist name as a /screen candidate.
5. Dedupe against the latest briefs/ file — skip anything already reported.
6. One line per item: [TICKER] source-dated fact -> why it might matter.
7. Flag ENTRY triggers (new catalyst) or EXIT triggers (thesis-break for a name in theses/)
   as **ESCALATE: /deep-dive TICKER** or **ESCALATE: /exit TICKER**.
8. Save to briefs/YYYY-MM-DD.md and show it. Terse. Escalate, don't analyse.

---
description: Cheap daily monitoring digest across the watchlist (Loop 1)
---
Run the MONITORING loop cheaply. No deep analysis here.
1. Read config/watchlist.md + config/news-sources.md.
2. For watchlist names pull ONLY: today's flow/disclosure deltas via nse-mcp (insider,
   bulk/block deals, pledge changes, new announcements) + Tier 1/2 whitelist items dated today.
3. Dedupe against the latest briefs/ file — skip anything already reported.
4. One line per item: [TICKER] source-dated fact -> why it might matter.
5. Flag ENTRY triggers (new catalyst) or EXIT triggers (thesis-break for a name in theses/)
   as **ESCALATE: /deep-dive TICKER** or **ESCALATE: /exit TICKER**.
6. Save to briefs/YYYY-MM-DD.md and show it. Terse. Escalate, don't analyse.

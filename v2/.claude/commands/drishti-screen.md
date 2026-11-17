---
description: Find candidates by criteria from stored data (disclosure flags, trend, quality). Usage /drishti-screen strong trend with no insider selling
argument-hint: <criteria in plain words>
allowed-tools: mcp__drishti__data_health, mcp__drishti__screen_disclosures, mcp__drishti__get_price_context, mcp__drishti__get_fundamentals, mcp__drishti__get_sector_rotation, mcp__drishti__get_deals, mcp__drishti__get_pledge, mcp__drishti__get_insider_cluster
---
Screen for: $ARGUMENTS.
Start with ONE plan line before any tool call: what you are about to pull and why, in plain words at the active level (the first sentence the user sees).
1. screen_disclosures for names with insider or pledge flags (positive or negative), and get_sector_rotation for which sectors are Leading or Improving; hunt in rotating-in sectors when the criteria are momentum-shaped.
2. Narrow to at most 10 names, then get_price_context for each, and get_fundamentals for only the final 3 to 5 (it is the slow, rate-limited source).
3. Ranked table (Rank | Name | Why it earned its place | Trend | Quality floor (ROCE, ROE, debt-to-equity) | Red flags | Source). Quality is context; say it is not scored.
4. Suggest the top 1 to 3 for /drishti-entry. Do not give calls here. Every number needs its date and source.

Format: follow the Drishti answer shape (and the active level, if one is loaded; otherwise use the investor level). Headline (**VERDICT** · subject · horizon vs yardstick · logged id or "not logged" · confidence), the read, then an evidence table (Signal | Reading | Against what | Visual | As of | Source | Says; 🟢 for, 🔴 against, 🟡 mixed, ⚪ unknown; unknowns as rows), with text visuals (bars █░, range gauge ├──●──┤, sparklines ▁▃▇, ▲▼ deltas) where they add information; for calls a scenario table (Case | Trigger | What the data would show | Call becomes), a track-record table when the log has related calls, a blind-spots table (Missing | Why it matters | How to fill it), and a final "Sources:" line naming each provider (NSE bhavcopy, NSE participant OI, NSE insider-trading disclosures, NSE shareholding filings, FRED, Yahoo Finance, MoSPI, CFTC, BSE, NSDL) with its as-of date. Every figure needs its date, its source and a yardstick; differences are computed. Compare = a column per stock; brief = Area | Reading | Against what | Visual | Source | Lean; review = Call | Date | Outcome | vs yardstick.

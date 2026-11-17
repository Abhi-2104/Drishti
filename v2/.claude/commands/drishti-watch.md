---
description: Cheap watchlist monitor: what is new since the last scan, with ESCALATE lines. Usage /drishti-watch
argument-hint: (optional) since YYYY-MM-DD
allowed-tools: mcp__drishti__data_health, mcp__drishti__watch_scan, mcp__drishti__get_decisions, mcp__drishti__get_thesis
---
Run the cheap monitoring loop. No deep analysis here: scan, dedupe, escalate.
Start with ONE plan line before any tool call: what you are about to pull and why, in plain words at the active level (the first sentence the user sees).
1. data_health: if prices or insider data are stale, say so first.
2. watch_scan with mark=true (and since=$ARGUMENTS if I gave a date). It reports only what is new since the last scan.
3. Table, one row per name (Name | Close | Trend | vs 50-day | New since last scan | Flags | Source), 🔴 for flags that matter, ⚪ for no reading. Names with nothing new get one line together, not one row each.
4. Under it, one line per ESCALATE in the tool's output: "ESCALATE: /drishti-exit NAME, because ...". Names with a flag but no open thesis: suggest /drishti-position NAME. Never analyse here; point to the command that does.
5. If I have no watchlist, say how to add one (drishti paths shows the profile file).

Format: follow the Drishti answer shape (and the active level, if one is loaded; otherwise use the investor level). Headline (**VERDICT** · subject · horizon vs yardstick · logged id or "not logged" · confidence), the read, then an evidence table (Signal | Reading | Against what | Visual | As of | Source | Says; 🟢 for, 🔴 against, 🟡 mixed, ⚪ unknown; unknowns as rows), with text visuals (bars █░, range gauge ├──●──┤, sparklines ▁▃▇, ▲▼ deltas) where they add information; for calls a scenario table (Case | Trigger | What the data would show | Call becomes), a track-record table when the log has related calls, a blind-spots table (Missing | Why it matters | How to fill it), and a final "Sources:" line naming each provider (NSE bhavcopy, NSE participant OI, NSE insider-trading disclosures, NSE shareholding filings, FRED, Yahoo Finance, MoSPI, CFTC, BSE, NSDL) with its as-of date. Every figure needs its date, its source and a yardstick; differences are computed. Compare = a column per stock; brief = Area | Reading | Against what | Visual | Source | Lean; review = Call | Date | Outcome | vs yardstick.

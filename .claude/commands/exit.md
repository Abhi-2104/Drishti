---
description: Exit / thesis-break monitor -> HOLD / TRIM / EXIT + trigger
argument-hint: [TICKER]
---
Apply the EXIT framework (CLAUDE.md) to $ARGUMENTS.
1. Read theses/<TICKER>.md — logged thesis, entry, stop, target, "what must stay true".
2. Pull current via nse-mcp: get_insider_trading (insider-sell cluster), get_short_selling
   (rising short interest = pressure), get_screener (shareholding) + get_bse_announcements
   (pledge rise / SAST / rating action), get_corporate_actions, latest concall/results &
   guidance (get_screener concall links), price vs stop/target. search_by_symbol = one-call
   shortcut for the deal/insider/announcement sweep.
3. Test triggers: THESIS BREAK (downgrade/pledge rise/insider-sell cluster/guidance cut/
   order-book or margin deterioration), TARGET HIT, STOP HIT.
Output: VERDICT (HOLD/TRIM/EXIT) + which trigger fired (source+date) or "no trigger — HOLD
because...". Propose updated thesis line if it evolved. Price alone != thesis break. Not-advice line.

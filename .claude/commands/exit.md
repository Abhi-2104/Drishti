---
description: Exit / thesis-break monitor -> HOLD / TRIM / EXIT + trigger
argument-hint: [TICKER]
---
Apply the EXIT framework (CLAUDE.md) to $ARGUMENTS.
1. Read theses/<TICKER>.md — logged thesis, entry, stop, target, "what must stay true".
2. Pull current: flow/disclosure deltas (nse-mcp), latest concall/results & guidance, rating
   actions, price vs stop/target.
3. Test triggers: THESIS BREAK (downgrade/pledge rise/insider-sell cluster/guidance cut/
   order-book or margin deterioration), TARGET HIT, STOP HIT.
Output: VERDICT (HOLD/TRIM/EXIT) + which trigger fired (source+date) or "no trigger — HOLD
because...". Propose updated thesis line if it evolved. Price alone != thesis break. Not-advice line.

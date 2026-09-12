---
description: Entry framework -> GO / WAIT / PASS + level + stop
argument-hint: [TICKER]
---
Apply the ENTRY framework (CLAUDE.md) to $ARGUMENTS. Assume /deep-dive + /bull-bear (or
/panel) context; if missing, pull the minimum needed and say so.
Report each: THESIS - QUALITY/VALUATION - DISCLOSURE CHECK - LEVEL - RISK.
DISCLOSURE CHECK pulls: get_insider_trading (no insider-sell cluster), get_screener
(shareholding) + get_bse_announcements (no rising pledge / SAST), get_short_selling (no spike
in short interest against the entry), latest rating action. search_by_symbol is the one-call
shortcut for the bulk/block/insider/announcement sweep.
Output: VERDICT (GO/WAIT/PASS) + ENTRY LEVEL/ZONE + STOP + suggested size + (if WAIT) what's
missing and what flips it + THESIS TO LOG for theses/<TICKER>.md. Append not-advice line.

---
description: Entry framework -> GO / WAIT / PASS + level + stop
argument-hint: [TICKER]
---
Apply the ENTRY framework (CLAUDE.md) to $ARGUMENTS. Assume /deep-dive + /bull-bear (or
/panel) context; if missing, pull the minimum needed and say so.
TIMING CONTEXT (don't buy into a hostile tape): get_macro_regime (regime score) +
get_participant_oi (smart-money score) + get_sector_rotation (is the name's sector rotating in
or out). A strongly negative regime/smart-money read argues for WAIT or a lower entry, even
when the name itself passes — note it in the LEVEL/RISK gates.
Report each: THESIS - QUALITY/VALUATION - DISCLOSURE CHECK - LEVEL - RISK.
DISCLOSURE CHECK pulls: get_insider_trading (no insider-sell cluster), get_screener
(shareholding) + get_bse_announcements (no rising pledge / SAST), get_short_selling (no spike
in short interest against the entry), latest rating action. search_by_symbol is the one-call
shortcut for the bulk/block/insider/announcement sweep.
Output: VERDICT (GO/WAIT/PASS) + ENTRY LEVEL/ZONE + STOP + suggested size + (if WAIT) what's
missing and what flips it + THESIS TO LOG for theses/<TICKER>.md. Append not-advice line.

---
description: Read an IPO prospectus (RHP/DRHP) and lay out the facts that decide whether it is worth subscribing. Usage /drishti-ipo "Company" <pdf link or file path>
argument-hint: <company> <prospectus link or path>
allowed-tools: mcp__drishti__add_evidence_source, mcp__drishti__read_evidence_source, mcp__drishti__record_evidence, mcp__drishti__query_evidence, mcp__drishti__get_calendar
---
$ARGUMENTS holds the company and a link (an NSE or BSE PDF) or a local path to its prospectus.

1. add_evidence_source with kind=prospectus, that link or path, no query (it is not listed yet), and published_at = the filing date on its first pages (ask me if you cannot find it).
2. Read the WHOLE document with read_evidence_source, following next_offset to the end.
3. Record 12 to 25 claims with record_evidence and exact quotes on: issue size and split (fresh issue or offer for sale, and who is selling), use of proceeds (repaying debt or growth), valuation basis and peers, promoter holding after the issue, customer or supplier concentration, related-party dealings, litigation and regulatory actions, debt and contingent liabilities, auditor remarks or restatements, and any growth claim that rests on a single year. author = your model id.
4. get_calendar for the issue dates and price band if the IPO is listed there.
5. Answer: a headline; a table Area | Fact | Quote | Good or bad; the three biggest risks; what would change the view. Whether to subscribe is my decision: lay out the evidence and do not make the call.

Format: follow the Drishti answer shape and the active level, if one is loaded.

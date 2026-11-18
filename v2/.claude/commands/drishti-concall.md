---
description: Read a company's latest earnings-call transcript and record what management said, with verbatim quotes. Usage /drishti-concall TCS
argument-hint: <stock name, symbol or ISIN>
allowed-tools: mcp__drishti__find_filings, mcp__drishti__add_evidence_source, mcp__drishti__read_evidence_source, mcp__drishti__record_evidence, mcp__drishti__validate_claim, mcp__drishti__query_evidence
---
Read the latest earnings-call transcript of $ARGUMENTS and record what matters.

1. find_filings with query=$ARGUMENTS and kind=concall. If nothing is listed, say so and ask me for a link; do not go looking elsewhere.
2. add_evidence_source with the newest transcript's link, kind=concall, the headline as the title, published_at = its published date, query = the stock.
3. Read the WHOLE document with read_evidence_source, following next_offset until it is null.
4. Record 8 to 20 claims with record_evidence. Each needs an EXACT quote copied from the text you read (20 to 800 characters), a topic (guidance, demand, margins, capex, debt, orders, pricing, competition, governance, management, risk, accounting, regulation) and a polarity (+1 good for the stock, -1 bad, 0 neutral); author = your model id. If a quote is refused, read the passage again and copy it exactly, or drop the claim. Never paraphrase inside a quote. Favour what CHANGED (guidance raised or cut, a new risk, an answer that dodged a question) over routine remarks.
5. If an earlier transcript is already stored (query_evidence), say what moved since.
6. Answer in plain words: a headline, the five claims that matter most (claim, quote, date), what management avoided, what to watch next quarter, and the source ids. This is evidence read from a document, not a prediction; say so.

Format: follow the Drishti answer shape and the active level, if one is loaded.

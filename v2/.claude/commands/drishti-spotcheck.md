---
description: Check a sample of recorded evidence claims against their quotes. Usage /drishti-spotcheck 20
argument-hint: <number of claims, default 20>
allowed-tools: mcp__drishti__spot_check_evidence, mcp__drishti__review_evidence
---
Call spot_check_evidence with n = $ARGUMENTS (20 if empty). Report the integrity result first: any broken quote is a serious problem. Then go through the sample with me, one claim at a time: the claim, the quote and its context, your own reading (supported, overstated, wrong or unclear) and ask for mine. Record MY verdict with review_evidence (reviewer = my name, never yours; a claim cannot be reviewed by its author). End with the review score so far and whether the sample is large enough to trust (under 20 reviews is not).

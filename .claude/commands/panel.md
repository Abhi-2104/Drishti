---
description: Multi-persona panel on ONE name (ai-hedge-fund style), adapted to India
argument-hint: [TICKER]
---
Run an investor-persona panel on $ARGUMENTS, mirroring virattt/ai-hedge-fund's
alpha-model -> signal -> synthesis pattern, on Indian data (nse-mcp flows/disclosure + nse-mcp
get_screener for fundamentals).
Personas (each -> BUY/HOLD/SELL + conviction -1..+1 + 2-line reasoning, source-backed):
- Buffett lens: durable moat, ROCE, pricing power, fair price.
- Graham lens: margin of safety, balance-sheet strength, cheapness.
- Lynch lens: understandable growth, PEG, "what does it do".
- Druckenmiller lens: macro/sector tailwind, asymmetry, liquidity.
- Jhunjhunwala lens (India): quality management + secular India growth theme + patience.
Then a PORTFOLIO-MANAGER synthesis: weigh convictions + disagreements into one view, note
where personas DISAGREE (the disagreement is itself signal), and hand off to /entry.
Append the standard not-advice line. (If reference/ai-hedge-fund exists, align outputs with
its Signal schema so results can later feed its backtester.)

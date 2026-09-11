---
description: Clone reference repos and adapt their patterns to the Indian market
argument-hint: [optional: repo name to focus on]
---
Goal: mine the open-source recommendation architectures and port PATTERNS (not performance
claims) to Drishti's Indian data layer. Read config/reference-repos.md first.

Steps:
1. Ensure reference repos are cloned (install.sh does this; else `git clone` into reference/).
   Focus: $ARGUMENTS (default: ai-hedge-fund).
2. For virattt/ai-hedge-fund specifically:
   a. Read hedge_fund/data/protocol.py — the DataClient Protocol.
   b. Draft `india_data_client.py` implementing that protocol against our sources:
      prices via Yahoo (.NS/.BO), financial_metrics via Screener/nse-bse, insider_trades +
      company_news via nse-mcp. HONOR the point-in-time contract (only data filed by end_date)
      and RAISE on infra failure (never silently return empty).
   c. Verify the persona alpha models in hedge_fund/signals/ run unchanged against it.
   d. Sketch reuse of risk/limits.py + portfolio/construction.py + backtesting/engine.py.
   e. Propose an India persona (Jhunjhunwala lens) as a new alpha model.
3. From TradingAgents / Nova-TradingAgent: extract the bull/bear debate structure and the
   regional data-swap approach; feed into /bull-bear and the port above.
4. From Vibe-Trading: note backtest/optimizer ideas we can borrow.
5. Write findings + a concrete port plan to reference/PORT_NOTES.md and update MASTER.md
   section 10 (current state). Do NOT copy code wholesale; adapt, and keep MIT attribution.
Honesty: ignore any "X% win rate" claims. We port structure, not promises.

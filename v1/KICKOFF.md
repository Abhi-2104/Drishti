# Drishti — Kickoff prompt

Open this folder in Claude Code, then paste the prompt below to have it set itself up.

---

You're working in my Drishti project (an India NSE/BSE investment research agent).

1. Read MASTER.md in full, then CLAUDE.md and config/reference-repos.md. MASTER.md is the
   complete project context — every decision and why, and section 10 has the current state
   and next steps. Confirm you've understood the goal, the two-loop architecture, the data
   layer, the news whitelist, and the honest constraints (personal tool, not a money printer,
   SEBI RIA line).

2. Run the setup. Execute ./install.sh (make it executable if needed). It should: place the
   project at /home/abhi/Abhijith/Drishti, clone the reference repos into reference/
   (ai-hedge-fund, TradingAgents, Vibe-Trading, NSE-MCP), and build NSE-MCP. If anything
   fails (Node version, network, a repo), tell me exactly what and how to fix it — don't
   silently skip. Show me the resulting reference/ tree.

3. Verify wiring. Check .mcp.json paths are correct (nse-mcp should point at the built
   reference/NSE-MCP/dist/index.js). Tell me which MCP servers are ready and which still
   need me (the nse-bse server choice, and the optional Zerodha Kite login).

4. Run the port. Execute the /port-patterns workflow focused on virattt/ai-hedge-fund: read
   its hedge_fund/data/protocol.py DataClient protocol, then draft reference/india_data_client.py
   implementing that protocol against our sources (prices via Yahoo .NS/.BO, financial_metrics
   via Screener/nse-bse, insider_trades + company_news via nse-mcp), honoring the point-in-time
   contract and raising on infra failure. Confirm the persona alpha models in hedge_fund/signals/
   would run against it unchanged, and sketch reuse of risk/limits.py, portfolio/construction.py,
   and the backtester. Write your findings + a concrete port plan to reference/PORT_NOTES.md and
   update MASTER.md section 10.

5. Stop and report. Give me a short status: what's working, what's stubbed, what you need from
   me next (starting with my watchlist names for config/watchlist.md). Don't place any trades,
   don't do anything irreversible, and don't commit or push without asking. Keep every claim
   sourced and flag anything you're unsure about rather than guessing.

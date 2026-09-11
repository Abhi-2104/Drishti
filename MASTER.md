# Drishti — Master Context & Handoff
> Drishti (दृष्टि) — "vision / sight": the agent watches (monitoring loop) and sees ahead (foresight).

> Single source of truth for this project. A fresh Claude Code session should read this
> first, then CLAUDE.md. It captures the goal, every design decision and WHY, the honest
> constraints, the reference repos to mine, and the current state / next steps.

Location: /home/abhi/Abhijith/Drishti  (WSL)
Owner: Abhijith. Personal research tool for Indian markets (NSE/BSE).

---

## 1. Goal
An agent that pulls relevant data cheaply, researches, analyses, and gives reasoned
ENTRY and EXIT recommendations (with explicit triggers + price levels) for Indian stocks.
It structures the decision — Abhijith makes the call. Not a predictor; not advice.

## 2. Hard constraints (these shaped every choice)
- **India-only** (NSE/BSE), rupee, SEBI context.
- **Personal use only.** Publishing or reselling calls => SEBI RIA registration territory;
  finfluencer rules even bar educators from using <3-month-lagged live prices. Keep it private.
- **Cheap.** Two-loop design; cheap model for monitoring, strong model only for deep work.
- **Verifiable.** Every number cites a named source. No black-box scores trusted blindly.

## 3. The honest reality (do not oversell this to the user)
- Most accessible data is PUBLIC => largely priced into large, liquid, covered stocks.
- The famous open-source "AI hedge fund" repos are NOT money printers. Consensus of 2026
  reviews: real live returns need info advantages retail lacks, or luck in a good window.
  Their honest value is structured reasoning + learning, not printing money. Treat any
  "X% win rate" repo as a red flag.
- Realistic edge lives in FOUR places, so the agent is designed around them:
  1. Processing consistency & breadth (read everything, every quarter).
  2. Text-at-scale: NLP on concall language shifts across quarters (the one genuinely new
     capability an LLM adds).
  3. Under-covered small/midcaps (efficiency follows analyst attention).
  4. Thesis MAINTENANCE > thesis discovery — catching WHY a thesis breaks early = clean
     exits. Most retail money dies holding too long, not on the buy.

## 4. Data layer — decisions and WHY
CHOSEN (assembled, not one aggregator — split by trust):
- **nse-mcp** (github: manitgupta/NSE-MCP) — NSE flows & disclosures: FII/DII, insider
  (SEBI PIT), bulk/block deals, pledges, corporate actions, announcements, short-selling,
  Nifty indices. Self-hosted, transparent source (NSE public API), honest cache disclosure
  (5min/60s), "pure data layer — no scoring". Quotes via Yahoo (.NS/.BO).
- **nse-bse MCP** — adds BSE coverage + fundamentals + all Nifty/Sensex indices. Candidates:
  parthashirolkar/stock-analysis-mcp (Yahoo-backed, transparent, has news) or
  anuragkrishna/Indian-Stock-Exchange-MCP, GirishKumarDV/Live-NSE-BSE-MCP (IndianAPI key),
  bshada/nse-bse-mcp, or free REST 0xramm/Indian-Stock-Market-API. Most are Yahoo-backed
  (~15-min delayed) => choose on coverage + transparency, not data edge.
- **Screener.in** — the ACTUAL financial documents: statements, results, annual reports,
  CONCALL TRANSCRIPTS. No API => access by browsing (Claude in Chrome / scheduled fetch).
  This is the fuel for the concall language-shift edge.
- **Zerodha Kite MCP** (official, free) OR Groww MCP (official, free) — user's own holdings
  + verified live quotes. Optional. Both free for existing account holders.

REJECTED / DEMOTED:
- **Tapetide MCP** — advertises "real-time" but unverifiable; ~0 Product Hunt upvotes, tiny
  npm downloads, no independent reviews; empty/seeded r/Tapetide. OK as a convenience to
  spot-check, NOT a source of truth.
- Satellite / credit-card panels — institutional, expensive, poor ROI even for funds. Skip.

BROKER NOTE: Abhijith's portfolio is on **SBI Securities**, which has NO public API/MCP.
Options: keep SBI + manual CSV export of holdings (zero friction, fine for research), OR
port demat to Zerodha/Groww (few days, no tax event, unlocks free official MCP). Not yet decided.

## 5. Macro/micro layer (India's underused free edge)
Monthly high-frequency public data: GST collections, UPI volumes (NPCI), e-way bills,
auto sales (FADA/SIAM), PMI, IIP — reliable enough that NSO folds GST/e-Vahan/UPI into GDP.
Great for sector-demand reads (autos/cement/FMCG/logistics). Fetch on schedule; only when a
thesis needs it. Micros = company fundamentals (MCPs + Screener).

## 6. News / "buzz" layer — reliability by whitelist, NOT open search
Defined trusted feeds, polled on schedule, scoped to watchlist. See config/news-sources.md.
- Tier 1 (primary, highest weight): NSE + BSE corporate announcements, SEBI, credit ratings
  (CRISIL/ICRA/CARE/India Ratings — often LEAD price).
- Tier 2 (context): Moneycontrol, ET Markets, Business Standard, LiveMint, Investing.com
  India (RSS+econ calendar), Reuters/Bloomberg.
- EXCLUDE: stock-tip blogs, Telegram pump feeds. Whitelist is auditable; open search isn't.
Rule baked into agent: Tier-1 filing beats Tier-2 take on conflict; never upgrade conviction
on media/buzz alone.

## 7. Architecture — two loops
- **Loop 1 Monitoring (/brief)**: cheap, frequent. RSS/announcements + flow deltas + macro
  -> LLM filter for watchlist-material -> short digest to briefs/ -> flag ESCALATE triggers.
- **Loop 2 Deep (/screen /deep-dive /bull-bear /panel /entry /exit)**: expensive, one name,
  only on trigger. Full sourced workup -> adversarial bull/bear -> entry/exit call -> log thesis.
Why split: deep-running everything is costly + noisy. Cheap-monitor + escalate = sustainable.

## 8. Entry / Exit frameworks
ENTRY -> GO/WAIT/PASS + level + stop (see CLAUDE.md). EXIT -> HOLD/TRIM/EXIT on thesis-break
/ target / stop. Every position gets a written thesis at entry (theses/<TICKER>.md: what must
stay true, entry, stop, target, break-triggers). /exit re-checks it each cycle. Exit when the
thesis breaks, not when price wobbles.

## 9. Reference repos to MINE (Claude Code: run /port-patterns) — full map in config/reference-repos.md
The recommendation-engine pattern is already solved open-source; adapt to India, don't reinvent.
- **virattt/ai-hedge-fund** (MIT, ~50-63k stars, ACTIVELY maintained) — CLOSEST match.
  Investor-persona "alpha models" (Buffett, Graham, Munger, Lynch, Druckenmiller + quant
  PEAD/regime) each -> Signal(conviction in [-1,+1] + reasoning); Risk Manager sets limits;
  Portfolio Manager synthesizes. Has a backtester + event study. Includes a Jhunjhunwala
  persona in some versions (India-relevant). KEY: `hedge_fund/data/protocol.py` defines a
  `DataClient` Protocol (get_prices/get_financial_metrics/get_insider_trades/company_news...).
  ==> INDIA PORT = implement that protocol against NSE-MCP/Yahoo(.NS)/Screener, and the whole
  persona+risk+portfolio machinery runs on Indian tickers. Personas live in hedge_fund/signals/,
  risk in hedge_fund/risk/limits.py, portfolio in hedge_fund/portfolio/construction.py.
  Clean separation: alpha models form a VIEW; portfolio construction handles sizing/timing.
- **TauricResearch/TradingAgents** (~80-99k stars; note: was quiet ~1 month) — models a full
  trading firm: analyst agents -> bull/bear researcher DEBATE rounds -> trader -> risk ->
  portfolio manager. Mine the structured-debate design for /bull-bear.
- **Nova-TradingAgent** — an A-share (China) fork of TradingAgents. Proof-of-pattern for a
  regional data-layer swap; template for how to regionalize.
- **HKUDS/Vibe-Trading** (~31k, Univ. Hong Kong) — pip-installable agent harness + backtest
  engine + portfolio optimizer + MCP server. Mine the backtest/optimizer patterns.
- Meta-list to browse: **georgezouq/awesome-ai-in-finance**.
India tooling also seen: ajeeshworkspace/indian-trading-skills, Bhala-Srinivash/nse-trading-skills,
tradermonty/claude-trading-skills (US, the upstream the India skill forks came from — good
"trader memory core" idea-lifecycle pattern).

## 10. Current state / NEXT STEPS for Claude Code

### DONE
- Scaffold (CLAUDE.md, .mcp.json, commands, config, thesis template, this MASTER).
- **install.sh run (2026-09-10).** Cloned into reference/: ai-hedge-fund, TradingAgents,
  NSE-MCP. **NSE-MCP built + smoke-tested** (`reference/NSE-MCP/dist/index.js` exists, starts).
  Vibe-Trading clone FAILED (network `early EOF`, github.com/HKUDS unreliable from this box)
  — retrying; non-blocking (ai-hedge-fund covers the backtester).
- **/port-patterns run (ai-hedge-fund).** Full findings + plan in `reference/PORT_NOTES.md`.
  - ai-hedge-fund is **v2** (restructured). `DataClient` = `@runtime_checkable Protocol`,
    8 methods, structural typing. Verified.
  - The 5 value/quality LLM personas (buffett/graham/lynch/munger/druckenmiller) are
    **prompt-only** and touch the data layer through just TWO calls, both in
    `features/snapshot.py`: `get_financial_metrics` + `get_company_facts`.
  - **Drafted `reference/india_data_client.py` (`DrishtiDataClient`)** — implements the
    protocol; `isinstance(DrishtiDataClient(), DataClient)` returns **True** against the
    real upstream Protocol. Working: prices/company_facts/market_cap (Yahoo .NS/.BO),
    insider_trades + news (NSE public API, same endpoints as NSE-MCP). Self-check:
    `python reference/india_data_client.py` → "shape check OK".
  - `risk/limits.py` + `portfolio/construction.py` = pure arithmetic → **reuse as-is**.
  - `backtesting/engine.py` = **reusable** once fundamentals are point-in-time (below).

### KNOWN GAPS (stated, not hidden — see PORT_NOTES §3)
1. **Point-in-time fundamentals.** `get_financial_metrics` needs real filing dates + deep
   history. Yahoo gives ~4–8 quarters, no filing date. Screener.in has both but no API
   (must browse). Draft uses a `report_period + 45d` placeholder → OK for **live "buy now"**,
   **mild lookahead in backtests** until a Screener fetcher lands.
2. **PEAD quant persona does NOT port** — needs consensus EPS estimates (no free Indian
   feed). Draft raises `NotImplementedError` on `get_earnings*`. LLM sleeve ports; quant
   sleeve doesn't.

### DECISIONS (2026-09-10, Abhijith)
- **Data = ensemble, not fallback.** Where multiple sources can answer, pull from
  ALL, reconcile (filing wins on conflict), surface disagreements. See PORT_NOTES §2a.
- **Watchlist filled** — 12-name starter research universe (growth driver + sector),
  style = mix. `config/watchlist.md`.
- Zerodha port coming → wire Kite MCP into the ensemble (prices + holdings) then.
- Ticket size undisclosed → default large/midcap, liquid names only.

### TODO (suggested order)
1. **Abhijith: pick the nse-bse MCP** (`.mcp.json` currently guesses `uvx stock-analysis-mcp`)
   — or skip it; the ensemble already has Yahoo + NSE + Screener. Decide Kite login (later, post-Zerodha).
   NOTE: `india_data_client.py` is a Python module for the ported engine, NOT an MCP server —
   correct as-is. A `drishti` MCP wrapper around the ensemble-merge layer is DEFERRED
   (PORT_NOTES §8): nothing to wrap until the merge helper exists; revisit after real /panel use.
3. `pip install yfinance requests pydantic` in a Drishti venv; vendor the ~8 `hedge_fund/`
   files we use (avoid the langchain dep tree) OR `pip install -e reference/ai-hedge-fund`.
4. Build the Screener fetcher + disk cache (mirror `data/cached.py`); filing dates from
   NSE "Financial Results" announcements → real point-in-time. Until then: live-only.
5. Wire `/panel`: per persona `build_snapshot` → prompt → `Signal`; then `blend_signals` +
   `apply_limits`; print table + PM synthesis + disagreement. Add Jhunjhunwala prompt
   (PORT_NOTES §5).
6. Upgrade `/bull-bear` to multi-round rebuttal (TradingAgents pattern, PORT_NOTES §6).
7. Test Loop 2 by hand on 2–3 names Abhijith knows; compare to his own view BEFORE trusting
   sizing.
8. Vendor `BacktestEngine` → accuracy record (only after step 4).
9. Only then wire Loop 1 automation (schedule + Telegram/email digest).

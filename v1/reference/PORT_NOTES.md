# PORT_NOTES — porting virattt/ai-hedge-fund (v2) to Drishti / India

Produced by `/port-patterns` on 2026-09-10. Source repo cloned at
`reference/ai-hedge-fund` (MIT, branch `main`, commit `fc1bf25`). Port PATTERNS,
not performance claims.

---

## 1. What ai-hedge-fund v2 actually is

The cloned repo is **v2** — restructured since MASTER.md was written. The map in
`config/reference-repos.md` is directionally right but the paths/names changed:

| MASTER.md said | actually (v2) |
|---|---|
| `hedge_fund/data/protocol.py` `DataClient` | ✅ same path. 8 methods (below). |
| `hedge_fund/signals/` personas w/ `base.py` `AlphaModel` ABC | ✅ `signals/base.py` = `AlphaModel` ABC; `signals/llm_agent.py` = `LLMAgent` base; `buffett.py` etc. are **prompt-only** subclasses. |
| `hedge_fund/risk/limits.py` | ✅ `apply_limits(weights, RiskLimits) -> RiskResult`. Pure arithmetic. |
| `hedge_fund/portfolio/construction.py` | ✅ `blend_signals(...) -> BlendResult`. Pure arithmetic. |
| `hedge_fund/backtesting/engine.py` | ✅ `BacktestEngine.run_alpha(...)`. Also new: `pipeline/run_cycle.py` (one fund tick), `backtesting/fund.py`, `event_study/`, `validation/` (combinatorial purged CV). |
| Jhunjhunwala persona "in some versions" | not in this checkout. Add as new `LLMAgent`. |

### The data contract (`hedge_fund/data/protocol.py`)

`@runtime_checkable Protocol` — structural typing, **no inheritance needed**.
Methods:

```
get_prices(ticker, start_date, end_date, **kwargs)      -> list[Price]
get_financial_metrics(ticker, end_date, period="ttm", limit=10) -> list[FinancialMetrics]   # POINT-IN-TIME
get_news(ticker, end_date, start_date=None, limit=1000) -> list[CompanyNews]
get_insider_trades(ticker, end_date, start_date=None, limit=1000) -> list[InsiderTrade]
get_company_facts(ticker)                               -> CompanyFacts | None
get_earnings(ticker)                                    -> Earnings | None
get_earnings_history(ticker, limit=12)                  -> list[EarningsRecord]
get_market_cap(ticker, end_date)                        -> float | None
```

Contract (verbatim intent): **empty/None = data genuinely absent; infra failure
must RAISE.** `get_financial_metrics` filters on `filing_date` (when the doc went
public), not `report_period` (fiscal-period end) — otherwise a backtest leaks the
future.

### How the pieces connect (verified by reading the code)

```
run_cycle / BacktestEngine
  └─ for each ticker: AlphaModel.predict(ticker, date, data_client) -> Signal(value∈[-1,1], reasoning)
       ├─ QuantModel (pead.py): calls data_client.get_earnings_history + get_prices
       └─ LLMAgent  (buffett/graham/lynch/munger/druckenmiller):
            build_snapshot(ticker, date, data_client)      <-- features/snapshot.py
              ├─ data_client.get_financial_metrics(ticker, as_of, "ttm", limit=20)   [REQUIRED, ≥4 rows]
              └─ data_client.get_company_facts(ticker)                               [sector/industry only]
            -> FundamentalsSnapshot.render() -> LLM -> {signal, confidence, reasoning} -> Signal
  └─ blend_signals(signals, model_weights, gross_target)  -> target weights   [PURE]
  └─ apply_limits(weights, RiskLimits)                    -> clamped weights  [PURE]
  └─ build_orders(...) -> broker                          [execution]
```

**Key finding:** the 5 value/quality personas touch the data layer through
**exactly two calls** (`get_financial_metrics`, `get_company_facts`), both inside
`features/snapshot.py`. Nail those two and every LLM persona runs unchanged on
Indian tickers.

---

## 2. India source mapping

| Protocol method | Drishti source | Status | Notes |
|---|---|---|---|
| `get_prices` | Yahoo Finance `<SYM>.NS` / `.BO` (yfinance) | **works** | Prices are inherently historical → point-in-time-safe. |
| `get_company_facts` | Yahoo `.info` (name/sector/industry/mktcap) | **works** | Latest-only; snapshot.py only uses sector/industry (slow-moving, accepted PIT approx). |
| `get_market_cap` | Yahoo `.info.marketCap` | **works** | Latest-only = lookahead in backtests; snapshot.py already avoids it (reads mktcap off the PIT metrics row). |
| `get_insider_trades` | **nse-mcp** `get_insider_trading` → NSE `/api/corporates-pit` (SEBI PIT) | **works, thin** | NSE serves only ~last 12 months. `intimDt` → `filing_date` (good PIT key). buy/sell inferred from shares before/after. |
| `get_news` | **nse-mcp** `get_nse_announcements` → NSE `/api/corporate-announcements` | **works, thin** | These are Tier-1 *filings*, the highest-value feed. Media whitelist RSS (config/news-sources.md) is a *separate* layer, not this method. |
| `get_financial_metrics` | Yahoo quarterly financials (shallow) **+ Screener.in (deep)** | **PARTIAL** | ⚠ see §3. |
| `get_earnings`, `get_earnings_history` | — | **NOT PORTABLE cheaply** | ⚠ see §3. |

`.mcp.json` `nse-mcp` already points at `reference/NSE-MCP/dist/index.js` (built,
smoke-tested OK). But note: **NSE-MCP has no historical-price and no
financial-statement tool** — it is flows/disclosures/quote only. Prices and
fundamentals do *not* come from it.

### 2a. Ensemble, not fallback (owner's call, 2026-09-10)

Abhijith: *"don't use it as fallback, use all the data points and evaluate from
all of those to give me a combined view."*

So for any method where >1 source exists (`get_prices`, `get_financial_metrics`,
`get_market_cap`, `get_company_facts`), the client fans out to **every available
source**, then reconciles:
- numeric conflict → primary filing / exchange source wins (CLAUDE.md dir. 3);
- the mismatch is **recorded and surfaced** (`metadata["source_disagreement"]`) —
  a cross-source gap is a data-quality signal, not noise to swallow;
- raise `DataClientError` only when **every** source fails.

Single-source data (NSE insider / flows / announcements) has no cross-check and
passes through unchanged. Kite MCP joins the ensemble for prices/holdings once
Abhijith ports to Zerodha. Implementation: wrap each method in a
`_merge([...sources])` helper; ~1 day. Not done in the draft (draft = one source
per method).

---

## 3. The two real problems (do not paper over)

### 3a. Point-in-time fundamentals — the load-bearing gap

`get_financial_metrics` must return rows tagged with the date they became public.
- **Yahoo** gives ~4–8 recent quarters, **no filing date**. `snapshot.py` needs
  `MIN_PERIODS=4` and asks for `limit=20`. So Yahoo alone barely clears the floor
  and gives the LLM a 1–2 year window, not the ~5 year history the personas
  assume.
- **Screener.in** has 10+ years of quarterly/annual data **and** roughly dateable
  results (board-meeting / results-declared dates via NSE announcements), but
  **no API** — it must be browsed/scraped.
- The draft (`india_data_client.py`) currently stamps `filing_date = report_period
  + 45 days` as a placeholder. Fine for **live "as of today"** use. For
  **backtests it introduces mild lookahead** (real Indian filing lag varies
  30–75 days).

**Fix path (in order):**
1. Ship live-only now with the 45-day-lag approximation. Value personas work
   today for "should I buy X *now*".
2. Add a Screener fetcher: pull the quarterly/annual tables + the results-date
   from NSE `/api/corporate-announcements` (subject: "Financial Results"),
   cache to disk (mirror `data/cached.py`). That gives real filing dates and
   deep history → honest backtests.
3. Only then trust `BacktestEngine` numbers for the LLM personas.

### 3b. PEAD persona — not portable

`pead.py` needs `eps_surprise ∈ {BEAT, MISS}`, i.e. **consensus EPS estimates**.
India has no free consensus feed (Trendlyne/consensus data is paid). Options:
drop PEAD, or later buy an estimates feed. Draft raises `NotImplementedError` on
`get_earnings*` rather than fake it. **The quant sleeve of ai-hedge-fund does not
port; the LLM sleeve does.**

---

## 4. What ports unchanged vs. what needs work

| Component | Verdict |
|---|---|
| `signals/base.py`, `signals/llm_agent.py` | **unchanged** — persona = name + system prompt. |
| `signals/buffett|graham|lynch|munger|druckenmiller.py` | **unchanged** — pure prompts, reason over `FundamentalsSnapshot`. Run on Indian tickers the moment `get_financial_metrics` + `get_company_facts` return data. |
| `features/snapshot.py` | **unchanged** — but its output quality is capped by §3a. |
| `signals/pead.py` | **drop** (no Indian estimates). |
| `risk/limits.py` (`apply_limits`) | **unchanged** — pure arithmetic, currency-agnostic. Set `RiskLimits(max_position_pct, max_gross_exposure)` from `config/scoring.md`. |
| `portfolio/construction.py` (`blend_signals`) | **unchanged** — pure arithmetic. Feed it `model_weights` from `config/scoring.md` signal rubric. |
| `backtesting/engine.py` | **reusable** — needs `get_prices` (have it) + the alpha model. Trustworthy for LLM personas only after §3a step 2. |
| `pipeline/run_cycle.py` + `brokers/` | **later** — only needed for paper/live portfolio simulation, not for `/panel` `/entry` `/exit`. |
| `llm/` (Anthropic client, PromptCache) | **reusable** — or swap for Claude-Code-native calls in the skill. The snapshot `content_hash` cache-key idea is worth keeping: re-reason only when a new filing changes the snapshot. |
| `validation/` (combinatorial purged CV) | **borrow later** for the accuracy record. |

---

## 5. Jhunjhunwala persona (new alpha model)

Drop-in `LLMAgent` subclass — no new machinery:

```python
# reference/ai-hedge-fund/hedge_fund/signals/jhunjhunwala.py  (or Drishti-local)
from hedge_fund.signals.llm_agent import LLMAgent

class JhunjhunwalaAgent(LLMAgent):
    @property
    def name(self) -> str: return "jhunjhunwala"
    def get_system_prompt(self) -> str:
        return """You are a big-picture Indian equity investor in the Rakesh
Jhunjhunwala mould. Judge one company as a decade-long owner riding India's
structural growth.
Checklist: (1) secular India tailwind (formalisation, capex cycle, financialisation,
consumption, import-substitution/defence)? (2) management integrity + skin in the
game — promoter holding stable/rising, no pledge creep, clean related-party record?
(3) return on capital and its trend; can it compound book value? (4) balance-sheet
survivability through a downturn. (5) valuation vs the runway — pay up for quality,
never for hope. (6) would you hold this through a 40% drawdown without selling?
Signal: bullish = quality compounder on a real theme at a payable price;
bearish = weak franchise, governance flag, or priced for perfection;
neutral = great business badly priced, or mixed evidence.
Reason ONLY from the data provided; treat the latest filing date as today.
Respond JSON only: {"signal": "...", "confidence": <0-100>, "reasoning": "<2-4 sentences>"}"""
```

Add `"jhunjhunwala"` to the strategy's `model_weights`. Note: with only the
fundamentals snapshot it can't *see* the macro theme — feed sector context via a
`build_user_prompt` override, or accept it reasons theme-from-numbers (same
honest limitation `druckenmiller.py` documents for itself).

---

## 6. TradingAgents — the debate pattern (for `/bull-bear`)

Cloned at `reference/TradingAgents`. Heavier (LangGraph) than we need; take the
shape:

- `agents/utils/agent_states.py::InvestDebateState` — `{bull_history, bear_history,
  history, current_response, judge_decision, count}`.
- `graph/conditional_logic.py::should_continue_debate` — alternate Bull/Bear until
  `count >= 2 * max_debate_rounds`, then hand to a **Research Manager** (judge).
- `agents/researchers/bull_researcher.py` — each turn the researcher gets the full
  reports + the *opponent's last argument* and must rebut it, not just restate.
- Separate 3-way **risk debate** (aggressive / conservative / neutral) → Portfolio
  Manager. Maps to our `config/scoring.md` veto rules.

Our `bull-bear.md` already encodes this (BULL → BEAR → ARBITER). One upgrade
worth making: **N rounds of rebuttal** (bull sees bear's counter and responds)
before the arbiter, instead of one shot each. Nova-TradingAgent (A-share fork)
not cloned — its lesson (swap the `dataflows/` layer for a regional one, keep the
graph) is exactly what §2 does here.

---

## 7. Vibe-Trading

Clone **failed repeatedly** (`fatal: early EOF` / broken branch — network to
`github.com/HKUDS/Vibe-Trading` is unreliable from this box). Retrying. Not
blocking: ai-hedge-fund already provides a backtester + portfolio blend, which is
what we'd have mined it for. Revisit for its MCP-native harness + optimizer once
the clone succeeds.

---

## 8. Concrete port plan

1. **[done]** Clone repos, build NSE-MCP, draft `india_data_client.py`
   (`DrishtiDataClient`), verify `isinstance(DrishtiDataClient(), DataClient) is
   True` against the real upstream Protocol.
2. **Dependencies**: `pip install yfinance requests pydantic` in a Drishti venv.
   Decide: vendor a trimmed copy of `hedge_fund/` (signals + risk + portfolio +
   features + backtesting + data models) into Drishti, or `pip install -e
   reference/ai-hedge-fund` and import it. Recommend vendoring the ~8 files we
   actually use — smaller surface, no langchain dependency tree.
3. **Wire `get_financial_metrics` properly**: build the Screener fetcher + disk
   cache (mirror `data/cached.py`); source filing dates from NSE
   "Financial Results" announcements. Until then, run **live-only**.
4. **`/panel`**: for each persona, `build_snapshot` → persona prompt → `Signal`;
   then `blend_signals` + `apply_limits`; print the table + PM synthesis +
   disagreement callout. Add the Jhunjhunwala prompt. Align output to the `Signal`
   schema so it can later feed the backtester.
5. **`/bull-bear`**: add multi-round rebuttal per §6.
6. **Backtest** (after step 3): vendor `BacktestEngine`, run the 5 personas over
   2–3 names Abhijith knows, compare calls to his own view *before* trusting
   sizing (MASTER.md §10.4).
7. **`RiskLimits` / `model_weights`**: lift concrete numbers from
   `config/scoring.md`.
8. **(deferred) `drishti` MCP server**: once step 3's ensemble-merge helper
   exists, wrap it as a one-tool MCP server — fan out to Yahoo + NSE + Screener
   (+ Kite later), reconcile, return one cross-checked payload with
   disagreements flagged. Value: Claude Code calls one tool during /panel /deep-dive
   instead of 3 + hand-reconciling. NOT NOW — nothing to wrap until the merge
   layer is built, and hand-reconciliation may prove fine in practice. Additive,
   no rework when added. Revisit after a week of real /panel use.

---

## 9. Honesty guardrail check

No performance claims imported. What we're actually porting: the views-vs-
positions separation, the point-in-time discipline, the persona=prompt structure,
the risk/portfolio arithmetic, the debate loop, the snapshot-hash LLM cache. All
structure, zero promises. The point-in-time gap (§3a) is real and is stated, not
hidden.

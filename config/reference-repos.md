# Reference repos to mine (used by /port-patterns). Adapt to India; do not reinvent.

## PRIMARY: virattt/ai-hedge-fund  (MIT — porting allowed with attribution)
Clone: https://github.com/virattt/ai-hedge-fund
Why: closest to our entry/exit recommendation goal. Multi-persona -> signal -> risk ->
portfolio synthesis, with backtesting.
Accurate map (verified):
- hedge_fund/data/protocol.py  -> `DataClient` Protocol. THE swap point. Methods incl.
  get_prices, get_financial_metrics (point-in-time!), get_insider_trades, company_news.
  Contract: raise on infra failure; empty only means "genuinely no data".
- hedge_fund/signals/  -> persona "alpha models": buffett.py, graham.py, munger.py,
  lynch.py, druckenmiller.py + quant pead.py; base.py = AlphaModel ABC -> predict() ->
  Signal(conviction in [-1,+1] + reasoning).
- hedge_fund/risk/limits.py  -> position limits.
- hedge_fund/portfolio/construction.py -> turns views into positions (sizing/timing).
- hedge_fund/backtesting/engine.py -> backtest loop + event study.
INDIA PORT PLAN:
1. Write DrishtiDataClient implementing the DataClient protocol, backed by our nse-mcp
   data + Yahoo(.NS/.BO) + Screener concalls. Respect the point-in-time contract.
2. Reuse the persona alpha models unchanged (they reason over the protocol's data).
   Add an India lens (e.g. a Jhunjhunwala-style value+growth persona).
3. Reuse risk/limits + portfolio/construction as-is to start.
4. Reuse the backtester -> our accuracy record over time.
Keep separation: alpha models = VIEW only; construction = positions.

## TauricResearch/TradingAgents  (~80-99k stars; check freshness — was quiet ~1mo)
Clone: https://github.com/TauricResearch/TradingAgents
Mine: the structured bull/bear DEBATE rounds + analyst->researcher->trader->risk->PM
pipeline. Feed ideas into our /bull-bear command. Heavier than we need; take the pattern.

## Nova-TradingAgent  (A-share fork of TradingAgents)
Mine: how a regional fork swaps the data layer for a non-US market. Template for India.

## HKUDS/Vibe-Trading  (~31k, Univ. Hong Kong)
Clone: https://github.com/HKUDS/Vibe-Trading
Mine: backtest engine + portfolio optimizer patterns; MCP-native design.

## Meta-list
https://github.com/georgezouq/awesome-ai-in-finance  — browse for more.

## India Claude-skills (lighter, direct-fit)
- ajeeshworkspace/indian-trading-skills, Bhala-Srinivash/nse-trading-skills
- tradermonty/claude-trading-skills (US upstream; steal the "trader memory core"
  idea-lifecycle/journaling pattern for our theses/).

## Honesty guardrail
None of these are money printers. Port PATTERNS (structured disagreement, risk/portfolio
separation, backtesting, thesis journaling) — not performance claims. Ignore any "X% win
rate" marketing.

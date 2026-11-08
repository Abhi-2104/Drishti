# Using Drishti from Claude (MCP)

`drishti mcp` is a stdio MCP server over the local database. Market-wide and history tools read only what the jobs
stored. A few single-company tools also fetch live (quote, option chain, announcements, and fundamentals when the stored
copy is older than 7 days); each says so in its `sources`.

| Group | Tools |
|---|---|
| Health and lookup | `data_health` (start here), `resolve_security` (ambiguous matches are returned as such, never guessed) |
| Macro and market regime | `get_macro_regime`, `get_cycle_phase`, `get_seasonality`, `get_liquidity`, `get_credit_spreads`, `get_midcap_ratio`, `get_sector_rotation`, `get_india_macro` (CPI, WPI, IIP, RBI reserves: reading, month, age, stale flag) |
| Flows and derivatives | `get_fii_dii` (NSE provisional), `get_fpi_flows` (NSDL confirmed, a day later), `get_participant_oi`, `get_options_flow`, `get_option_chain` |
| Disclosures | `insider_trades`, `get_insider_cluster`, `get_pledge`, `screen_disclosures` |
| One company | `get_fundamentals`, `get_quote`, `get_price_context`, `get_announcements`, `get_corporate_actions`, `get_credit_ratings`, `get_deals`, `get_delivery` (delivery % per session), `get_news` (headlines from your own feeds) |
| Calendar | `get_calendar` (board meetings / results dates, NSE + BSE), `get_market_today` (Gift Nifty, IPOs, expiries, holidays) |

Signals accept `as_of` (YYYY-MM-DD) and then read only data up to that date.

Every reply is `{data, as_of, sources, error}`. A stale answer carries `error.kind = STALE` together with the data.

Known gaps: BSE bulk/block deals (BSE's page is script-rendered; NSE's deals cover most names), NSE's financial-results
filing list (its date filter did not work when probed), event-calendar (a subset of board meetings, not used).

## Register in Claude Desktop (Windows)

Add this under `mcpServers` in `%APPDATA%\Claude\claude_desktop_config.json`, then restart Claude Desktop
(adjust the path if the project lives elsewhere):

```json
{
  "mcpServers": {
    "drishti": {
      "command": "uv",
      "args": ["run", "--project", "C:\\Users\\abhij\\Documents\\Drishti\\v2", "drishti", "mcp"]
    }
  }
}
```

Check by asking Claude "run drishti data_health". Fill the database first with `uv run drishti run <job>` (see `drishti jobs`).
This registration edits a file outside the project, so it is done by you, not by the build.

## Adding tools

Write a plain function of a `Store` in `drishti/mcp_server.py` that returns `Result.to_dict()`, test it without the SDK,
then register it in `build_server`. Tools stay profile-neutral; anything that needs a portfolio goes through the
profile layer, which only decisions and tools may read.

## Decision tools (see docs/DECISIONS.md)
`gather_evidence` (evidence bundle for a gate and date, with hash), `record_decision` (append one call to the log),
`get_decisions` (the log), `score_decisions` (how calls turned out vs the Nifty / cash, per author, with an optional paired comparison).

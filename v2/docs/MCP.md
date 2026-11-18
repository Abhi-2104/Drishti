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

## Register in Claude Code (CLI)

One line, from any folder (user scope = available in every project; use `-s project` to share it through a `.mcp.json` instead):

```powershell
claude mcp add drishti -s user -- uv run --project C:\Users\abhij\Documents\Drishti\v2 drishti mcp
```

Check it: `claude mcp list` (should show `drishti` as connected), or type `/mcp` inside Claude Code. Then ask, for example:
"run drishti data_health", "what does drishti say about RELIANCE, price and insider signals", or
"gather entry evidence for TCS as of today and tell me ENTER, WAIT or AVOID with the thesis, then record it".
Remove with `claude mcp remove drishti -s user`. Restart Claude Code after changing the database path or upgrading drishti.

The same server works in `omp` (put the same command in the profile's `mcp.json`), Cursor, Codex CLI and Gemini CLI.

## Slash commands

The server also publishes prompts, which Claude Code shows as slash commands (type `/` and `drishti`):

| Command | What it does |
|---|---|
| `/mcp__drishti__entry TCS` | gathers evidence, decides ENTER / WAIT / AVOID, logs it |
| `/mcp__drishti__exit TCS` | HOLD / TRIM / EXIT on a holding, logs it |
| `/mcp__drishti__macro` | RISK_ON / NEUTRAL / RISK_OFF for the market, logs it |
| `/mcp__drishti__health` | plain-words data freshness |
| `/mcp__drishti__scorecard` | how logged calls are doing, by author |

Restart Claude Code after upgrading drishti so it re-reads the list. Other clients show the same prompts in their own way.

## Check the whole chain

`uv run drishti selftest` runs settings, store, data health, the MCP server and its tools, a signal, a blind evidence bundle,
a rules decision (dry run) and the decision log and scorer, against your own database. Read-only and offline.

## Adding tools

Write a plain function of a `Store` in `drishti/mcp_server.py` that returns `Result.to_dict()`, test it without the SDK,
then register it in `build_server`. Tools stay profile-neutral; anything that needs a portfolio goes through the
profile layer, which only decisions and tools may read.

## Decision tools (see docs/DECISIONS.md)
`gather_evidence` (evidence bundle for a gate and date, with hash), `record_decision` (append one call to the log),
`get_decisions` (the log), `score_decisions` (how calls turned out vs the Nifty / cash, per author, with an optional paired comparison).

## Evidence layer (E1, 17 Nov 2026)

| Tool | Does |
|---|---|
| `add_evidence_source` | Store a document (transcript, annual report, announcement, results, prospectus, rating report) with the date it became public. Same text twice = one source. |
| `validate_claim` | Is this quote word for word in the document? (Spaces, curly quotes and dashes are normalised; nothing else.) |
| `record_evidence` | Store one claim + its quote + topic + polarity + author. Refused (`REJECTED`) unless the quote is verbatim in the source. |
| `query_evidence` | Claims with quotes and documents, newest first; `as_of` keeps it point-in-time (only documents public by then). |

Extraction is done by whoever calls `record_evidence` (Claude in the session, or a person); the store guarantees only that every
quote exists in its document.

### E2 to E4 (18 Nov 2026)

| Tool / prompt | Does |
|---|---|
| `add_evidence_source` (extended) | Also takes a local `.pdf` path or a PDF `url` on an NSE or BSE host (https only; any other host is `REJECTED`). A PDF with no text layer (a scan) is `EMPTY`; reading PDFs needs `pypdf` (in `pyproject.toml`; `uv run` installs it). `query` may be empty for an unlisted company (an IPO). |
| `find_filings` | A company's concall transcripts / results / annual reports / presentations from the announcements already stored: date, headline, PDF link. Audio links and date notices are left out. |
| `read_evidence_source` | A stored document in windows (up to 30,000 characters); follow `next_offset`. A quote must be copied exactly from this text. |
| `spot_check_evidence` | Re-proves every stored quote against its document (`broken` = the words are gone; `moved` = still there, shifted) and returns a random sample of claims with their quotes in context. |
| `review_evidence` | A person's verdict on a claim: `supported`, `overstated`, `wrong`, `unclear`. A claim's author cannot review it. The share judged supported is the extraction's precision (under 20 reviews = too few to say). |
| prompts `/concall`, `/ipo`, `/spotcheck` | Read the latest earnings-call transcript, read an IPO prospectus, or run the spot-check. Slash-command versions: `/drishti-concall`, `/drishti-ipo`, `/drishti-spotcheck`. |

Claims also reach decisions: a non-blind `gather_evidence` bundle for a stock carries up to 15 recent claims (point-in-time, each with its
quote) under `stock.evidence_claims`, and carries nothing when there are none, so existing bundle hashes do not change. Blind and masked
tests never carry documents (a title and a date would identify the case).

CLI: `drishti evidence audit | sample | summary`, `drishti storage` (rows and size per table).

## Tradeable universe

`drishti/universe.py`: the 800 most liquid NSE names by 45-day average turnover. `screen_disclosures` drops names outside it and reports
`universe.excluded_illiquid`; the watchlist and holdings are never filtered. `data_health` now also lists the newest date in each
stored table (prices, delivery, participant OI, index closes, deals, announcements, insider filings) with `stale` true past 4 days.

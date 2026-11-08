# Drishti v2 — P1 build plan (foundation + v1 parity)

*Written 26 Sep 2026, end of Phase 0; revised 7 Nov 2026 (cross-platform; built in the Project, not Claude Code). This is the execution spec for P1. It is self-contained: anyone picking up the build should be able to work from this file alone. Architecture rationale lives in `drishti-mcp-rebuild-plan.md`; data-source evidence lives in `v2-phase0-data-bakeoff-plan.md` (both in the claude.ai Project "Calibrate Drishti Investor Copilot").*

## 1. Who does what
- **Plan and build both happen in the claude.ai Project** ("Calibrate Drishti Investor Copilot"). Claude writes and edits code directly in the repo through the desktop app's folder connection; Abhijith / brother run it and report results back. No Claude Code. Structural changes to this plan are made here; the Project docs are the source of truth.
- **Repo:** GitHub `Abhi-2104/Drishti`, cloned anywhere on any OS (currently Abhijith's Windows `Documents\Drishti`; the brother's machine is separate). `v1/` = the TypeScript v1 (reference only — do not modify). v2 lives in `v2/`. `v2/phase0/probe.py` already exists and is the seed for `providers/`.
- **Network:** live fetches must run on a normal home/office network on your own machine. Claude's cloud and desktop-bridge sandboxes are proxy-blocked for NSE/BSE/FRED/Yahoo, so code is written and unit-tested there, but data runs happen on your machine — results from the sandboxes are not evidence.

## 2. Locked decisions
- One Python package `drishti` (the investor copilot). Everything — data, signals, decisions, evidence, later portfolio — lives under it. No third-party MCP servers; nse-mcp is retired and re-implemented internally.
- **Python ≥3.11, managed with `uv`** (`pyproject.toml`, `uv.lock`, `uv run`).
- **Cross-platform: Windows, macOS, Linux (WSL is just Linux).** No OS-specific code paths or shell scripts. Rules: `pathlib` for every path; UTF-8 explicitly on every file read/write and console-safe output; data/config dirs from `platformdirs` (overridable in config), never hard-coded `/mnt/...` or `C:\...`; scheduling via the OS's own scheduler (Task Scheduler / cron / launchd) calling `drishti run <job>`; CI-style test run on at least Windows and Linux before calling P1 done.
- **Rewrite, not a port.** Nothing from v1's TS is copied. Re-derive endpoints/logic in Python. v1 is read for URLs, headers and quirks.
- Three entry points: `drishti mcp` (stdio MCP server), `drishti worker` (scheduler), `drishti run <job>` (one job by hand).
- **Engine-agnostic MCP:** all capability exposed as tools (prompts/resources are extras). Must work in Claude Code, Claude Desktop, Cursor, Codex CLI, Gemini CLI.
- **Local SQLite store per install. No data sharing between the two brothers' machines — by design.**
- Public, keyless sources first. No broker account required (optional adapter later).
- **Never a silent blank:** every failure is a typed error, logged.
- **Centralized rate limiting** in `core/fetch.py` — no `sleep()` scattered in providers.
- **User-Agent decided per source**, never a global default (see §5).
- Scope excluded: mutual-fund NAV/flows (redundant with DII), technical indicators (not this copilot's model — tools return macro/smart-money/fundamental scores, not chart patterns).

## 3. Package layout (create under `v2/`)
```
v2/
├── pyproject.toml          # uv; console script: drishti = drishti.cli:main
├── drishti/
│   ├── cli.py              # mcp | worker | run <job> | jobs (list)
│   ├── config.py           # settings file: data dir (platformdirs default), watchlist (symbols + BSE scrip codes)
│   ├── core/
│   │   ├── envelope.py     # Result{data, as_of, sources[], error}; typed ErrorKind
│   │   ├── fetch.py        # the only HTTP entry point (see §4)
│   │   ├── ratelimit.py    # per-host min-interval / token bucket
│   │   └── cache.py        # TTL cache, keyed by URL (+ store-backed for daily files)
│   ├── providers/          # one module per source (see §5)
│   ├── store/              # SQLite: raw blobs, parsed tables, signals, job_log
│   ├── jobs/               # job registry + schedule table
│   ├── signals/            # pure functions over stored data (P2+)
│   ├── decisions/ evidence/ portfolio/ prompts/   # stubs in P1
└── tests/
```

## 4. `core/` spec (build first, before any provider)
- **Result envelope:** every provider call returns `Result(data, as_of, sources=[{name,url,fetched_at}], error)`. `error` is `None` or a typed kind: `TIMEOUT, BLOCKED (401/403/429), BLOCKED_HTML (HTML where data expected), NOT_FOUND, EMPTY, PARSE_FAIL, NETWORK`. Mirror the classification in `probe.py`'s `fetch_once` — it is proven.
- **Rate limiter:** one shared limiter, configured per host. Starting values: `nseindia.com` / `nsearchives.nseindia.com` / `archives.nseindia.com` min 0.5 s between calls; `api.bseindia.com` 0.5 s; `query1.finance.yahoo.com` 0.3 s; `fred.stlouisfed.org` 0.5 s; `sec.gov` / `data.sec.gov` ≤10 req/s (SEC's published limit); others 1 s. On 401/403/429: exponential backoff, and for NSE live re-warm the session before retry.
- **Timeouts per source:** archive files ~15 s; NSE live ~20 s; FRED/Yahoo ~15 s. Always split connect/read timeouts.
- **Cache TTLs matched to publish cadence:** daily archive files — fetch once per trading day after publish, never re-fetch same file; NSE live endpoints (FII/DII, Gift Nifty, short selling, announcements) ~60 s in-memory; Yahoo quotes ~60 s; FRED daily; CFTC weekly; EDGAR ~1 h.
- **Tests:** unit-test the limiter (spacing enforced across threads), the envelope classification (feed it 403, HTML, empty, bad JSON), and the cache (TTL expiry).

## 5. Providers — port logic from `v2/phase0/probe.py`
Phase 0 final run: 28/37 signals confirmed working.

| Module | Source & URL pattern | Signals | Quirks (must keep) |
|---|---|---|---|
| `nse_archive.py` | `nsearchives.nseindia.com/content/fo/BhavCopy_NSE_FO_0_0_0_{YYYYMMDD}_F_0000.csv.zip`, `.../cm/BhavCopy_NSE_CM_0_0_0_{YYYYMMDD}_F_0000.csv.zip`, `.../nsccl/fao_participant_oi_{DDMMYYYY}.csv`, `.../indices/ind_close_all_{DDMMYYYY}.csv`, `.../equities/bulk.csv`, `.../equities/block.csv` | PCR, max pain, OI walls; EOD prices; participant OI; all index closes + India VIX; bulk/block deals | Walk back to latest trading day if today's file 404s. Participant-OI date: split title on `"as on"`. Strongest providers — no session needed. |
| `nse_live.py` | `www.nseindia.com/api/fiidiiTradeReact`, `/api/marketStatus` (Gift Nifty field), `/api/corporate-announcements?index=equities`, `/api/snapshot-capital-market-largedeal` (`SHORT_DEALS_DATA`), `/api/allIndices` | FII/DII cash, Gift Nifty, announcements, short selling, India VIX (fallback) | Needs session warm-up (homepage → market-data page), full Chrome header set, jittered pauses. Homepage itself may 403 while `/api/*` still works — don't treat warm-up 403 as fatal. Add the two guards v1 lacks: non-2xx on warm-up = typed error; empty cookie jar = typed error. |
| `fred_csv.py` | `fred.stlouisfed.org/graph/fredgraph.csv?id={SERIES}` — DTWEXBGS, DGS10, DCOILBRENTEU, VIXCLS, DEXINUS, WALCL, M2SL, BAMLH0A0HYM2, T10Y2Y | All L1 US macro + liquidity + credit + curve | **Use an honest non-browser User-Agent.** A spoofed Chrome UA gets tarpitted (connects, then read-timeout). Verified fix. |
| `yahoo.py` | `query1.finance.yahoo.com/v8/finance/chart/{SYM}?range=..&interval=..` — DX-Y.NYB, ^TNX, BZ=F, INR=X, ^INDIAVIX, ^NSEI, sector indices (e.g. ^CNXIT), GC=F (gold), HG=F (copper), `{SYMBOL}.NS` | Secondary for daily macro; Gold/Copper primary; **sector history via `range=6mo`** (replaces blocked niftyindices.com — verified 129 daily points); stock quote substitute (pending test) | Unofficial API — keep behind limiter; treat as fallback where an official source exists. |
| `bse.py` | `api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w?pageno=1&strCat=-1&strPrevDate={YYYYMMDD}&strScrip={SCRIP}&strSearch=P&strToDate={YYYYMMDD}&strType=C&subcategory=-1` | Announcements, insider/PIT, pledges (L4) | Requires `Origin: https://www.bseindia.com` and `Referer: https://www.bseindia.com/`. **Needs a real scrip code** — blank `strScrip` returns empty. Loop over watchlist scrip codes. |
| `cftc.py` | `www.cftc.gov/dea/newcot/FinFutWk.txt` | COT positioning (weekly) | — |
| `edgar.py` | `data.sec.gov/submissions/CIK{10-digit}.json`, `www.sec.gov/files/company_tickers.json`, `data.sec.gov/api/xbrl/companyfacts/CIK{..}.json` | US filings/13F, US fundamentals | SEC requires a descriptive UA with contact info. |
| `screener.py` | `www.screener.in/company/{SYMBOL}/consolidated/` | Fundamentals, shareholding, concall links | HTML scrape — parse defensively, typed PARSE_FAIL on layout change. |
| `nsdl.py` | `www.fpi.nsdl.co.in/web/Reports/Latest.aspx` | FPI flows (official cross-check) | Flaky (timed out in 2 of 3 runs). Low priority; NSE live FII/DII is primary. |

**Stubs only in P1 (raise a typed `NOT_IMPLEMENTED`, comment pointing to the Phase 0 doc):** `rbi.py` (reserves, MPC stance), `mospi.py` (CPI/WPI/IIP), credit ratings, concall transcripts, `pmi` (manual entry only — no public source exists), L6 CAS import.

**Known gaps, do not chase in P1:**
- NSE live `quote-equity` (403) — pending a market-hours test of Yahoo `RELIANCE.NS` (first test ran pre-market, returned yesterday's close, ~13.7 h old). If fresh during market hours → Yahoo is the quote source; else revisit NSE session.
- NSE live `option-chain-indices` (404) — the defined signal (PCR/max pain/OI walls) is fully covered by the EOD F&O bhavcopy. Intraday option flow only matters if a future `/entry`/`/exit` rule needs it; not designed yet.
- niftyindices.com — blocked; replaced by Yahoo `range=6mo`.

## 6. Store (`store/`)
SQLite file in the configured data dir. Tables: `raw_blob` (source, url, fetched_at, bytes, sha256), one parsed table per provider dataset, `signal` (name, as_of, value JSON), `job_log` (job, started_at, finished_at, status, error_kind, detail). Idempotent upserts keyed by (dataset, trade_date). The job log powers a `data_health` tool.

## 7. Jobs (`jobs/`) and the worker
Build `drishti run <job>` for every job first; the always-on `drishti worker` loop comes after.

| Job | When (IST) | Providers |
|---|---|---|
| `premarket` | ~08:30 | nse_live (Gift Nifty), yahoo, fred |
| `eod_bhavcopy` | ~18:00–19:00, retry until published | nse_archive (FO + CM) |
| `eod_flows` | evening | nse_archive (participant OI, bulk, block, ind_close_all), nse_live (FII/DII, short selling) |
| `sector_history` | evening | yahoo sector indices `range=6mo` (backfill), then daily append |
| `announcements` | a few times a day | bse (watchlist), nse_live |
| `fred_weekly` | weekly | fred_csv |
| `cot_weekly` | Sat (after Fri release) | cftc |

Worker runs via the OS scheduler calling `drishti run <job>` — Windows Task Scheduler, cron (Linux/WSL), or launchd (macOS) — or `drishti worker` as a long-running process. Provide a one-line install recipe for each OS, not a script for one.

## 8. MCP server (`drishti mcp`)
Use the official Python MCP SDK (`mcp`). Tools read the store first, live fetch only if missing/stale, and always return the envelope. P1 parity tools (v1 names kept where sensible): `get_screener`, `get_bse_announcements`, `get_ipos`, `get_macro_regime`, `get_participant_oi`, `get_sector_rotation`, `get_us_quote`, `get_us_fundamentals`, `get_us_filings`, plus `data_health`. Also expose the Phase 0 data now available: FII/DII, bulk/block deals, short selling, options (PCR/max pain/OI walls), Gift Nifty.

## 9. Order of work
0. Commit `v2/phase0/` (probe.py + latest report) if not already committed.
1. `uv init` / `pyproject.toml`, package skeleton, `drishti` CLI with the three subcommands (no-op bodies).
2. `core/` (envelope, fetch, ratelimit, cache) + tests.
3. `store/` schema + tests.
4. Providers in this order: `nse_archive` → `fred_csv` → `yahoo` → `bse` → `nse_live` → `cftc` → `edgar` → `screener` → `nsdl`; stubs for the rest. Each with a parser test on a saved sample file.
5. Jobs + `drishti run <job>` for each; then `drishti worker`.
6. MCP server + parity tools; register it in at least two MCP clients (e.g. Claude Desktop and one other) and test end-to-end.

## 10. Acceptance — P1 is done when
- `uv run drishti run <job>` writes real data to the store for every 🟢/🟡 provider above, on both Windows and a Linux/macOS machine.
- `uv run drishti mcp` serves the parity tools from the store in an MCP client (Claude Desktop at minimum).
- Every induced failure (block, timeout, empty, bad parse) shows up as a typed error in `job_log` — never a blank.
- No provider calls `sleep()` or `requests` directly — all HTTP goes through `core/fetch.py`.
- Tests pass: `uv run pytest`.

## 11. Out of scope for P1
Seasonality and cycle phase (P2); `/macro` synthesis and signal scoring math (P2/P3); evidence layer / LangExtract (P4); prompts and narrative runs (P4); RBI/MoSPI/PMI/ratings/transcripts (P5); portfolio/L6 (deferred).

## 12. Open items carried forward
- Market-hours test of Yahoo `RELIANCE.NS` (still pending as of 7 Nov 2026).
- Which machine stays on in the evenings to run the worker.
- Which recurring narrative runs are wanted, and on which engine.
- **Parked (8 Nov 2026, by the user): registering the scheduled jobs** (`scripts/register-tasks.ps1`, which machine runs them). Do this LAST, once the whole tool (decision layer, date-locked mode, scorer, harness) is built and tested. Until then jobs are run by hand with `drishti run <job>`.

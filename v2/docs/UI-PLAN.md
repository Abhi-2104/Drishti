# Drishti v2 — App, background automation and installer plan (19 Nov 2026)

*Plan only; nothing in Phases 1-8 is built yet. Written at the end of the 18-19 Nov session. Read with
`HANDOFF-2026-11-18.md` (rules, workflow) — its §1 rules apply to every phase here. Audience: the next Claude chat and
Abhijith (and his brother, who runs his own install).*

---

## 0. What we are building, in one paragraph

A **Windows desktop app, "Drishti"**, that runs on each brother's laptop with its own local database. In the background it
keeps 10 years of market data current on a fixed schedule and **catches up automatically whenever the laptop is next on**.
Every Saturday it prepares an evidence pack, and a **Claude scheduled task** reviews the holdings (HOLD / TRIM / EXIT) and a
screened list of new candidates (ENTER / WAIT / AVOID), logging every call so it is scored against the Nifty later. The app
shows the portfolio against the 25% XIRR goal, every signal, every call and its score, and has a **chat panel** that is the
real Claude Code (unmodified, signed in with the user's own subscription) wired to Drishti's data. **Claude Desktop chats**
get the same copilot through Drishti's MCP server. Everything is free beyond the Claude plan each brother already pays for.

## 1. Fixed decisions (from the 18-19 Nov conversation)

| Topic | Decision |
|---|---|
| Order | P1 background automation first (data must accrue daily), then AI hookups, then the app, then the installer |
| Schedule | **Three slots**: Morning 08:30, Evening 20:30, Weekly Sat 10:00. Missed slots run at next logon / next trigger |
| Trigger | Windows Task Scheduler runs `drishti catchup` at logon, 08:30, 20:30, Sat 10:00, with "run as soon as possible after a missed start" |
| Weekly review | A Claude **scheduled task** (Sat ~11:30, retry Sun) reads the weekly pack, writes calls + a report into `private/` |
| AI in Claude Desktop | Drishti MCP server (local, stdio) + Kite MCP (free, hosted, read-only per Zerodha) configured in Claude Desktop |
| AI inside the app | **Embedded terminal running the real Claude Code**, signed in by the user, clearly labelled as Claude Code |
| Not allowed (Anthropic terms) | A chat box that looks native but routes through a Pro/Max login; collecting or storing Claude credentials; Drishti branded as Claude Code |
| Optional chat fallbacks | Pay-per-use API key (not free) or a local model via Ollama (free, weaker) — not planned now |
| Distribution | **Private.** Repo stays private. Installer is a `Drishti-Setup.exe` built locally and shared privately (e.g. Drive), no token baked in |
| Data | Never shipped. Each install builds its own DB from NSE/BSE/RBI/FRED, exactly as the current laptop did. No NSE data in any public place |
| Cost | Zero beyond the existing Claude plans. All libraries open source |
| Recommendations | Holdings review + new-stock candidates. Decision support, not advice: the brothers decide |

## 2. Architecture

```
            Windows (each brother's laptop)
 ┌──────────────────────────────────────────────────────────────────┐
 │ Task Scheduler ──► drishti catchup ──► providers ─► SQLite (AppData) │
 │                                           ▲            ▲   ▲       │
 │ Drishti app (pywebview window)            │            │   │       │
 │   React UI ◄─HTTP/WS(127.0.0.1+token)─► FastAPI (drishti.app)     │
 │   Chat panel (xterm.js) ◄──WS──► ConPTY ─► Claude Code CLI ──MCP──┘ │
 │ Claude Desktop ──MCP (stdio)──► drishti mcp ───────────────────────┘ │
 │ Claude Desktop ──remote MCP──► mcp.kite.trade (read-only, optional)  │
 └──────────────────────────────────────────────────────────────────┘
 Claude scheduled task (Sat) ──bridge──► private/weekly/<date>/ (reads pack, writes calls+report)
```

- **Core stays as is**: `drishti` package, providers, signals, decisions, evidence, store. Every rule in HANDOFF §1 holds
  (all HTTP via `core/fetch.py`, typed errors, ISIN join key, profile-neutral providers/signals).
- **New `drishti.app` subpackage**: FastAPI app, read-mostly. Writes only through existing typed functions
  (`log.record`, holdings import, settings). Binds `127.0.0.1` on a random port, per-launch token, strict CSP. No remote access.
- **Frontend `v2/ui/`**: React + TypeScript + Vite + Tailwind + shadcn/ui; charts: TradingView lightweight-charts (Apache-2.0)
  for prices, ECharts for the rest. **Built in the sandbox and shipped as static files** inside `_decN.tgz`, so the laptops need
  no Node.
- **Window**: pywebview on Microsoft Edge WebView2 (present on Windows 11). `drishti app` opens it; it starts FastAPI itself.
- **Chat panel**: xterm.js in the UI, a WebSocket to the backend, `pywinpty` (ConPTY) running `claude` in
  `%LOCALAPPDATA%\Drishti\workspace`, which holds a `.mcp.json` pointing at `drishti mcp` and a `CLAUDE.md` with the copilot
  brief. Context buttons ("Ask about this stock") type a prompt into the terminal; the user presses Enter.
- **Concurrency**: SQLite in WAL mode with `busy_timeout`, so the catch-up job can write while the app reads. Long jobs never
  run inside the API process; the app starts `drishti catchup` / `drishti run` as child processes and streams their output.

## 3. Phases (each = one or more `_decN` cycles: sandbox build → tgz → bridge → md5 → user pytest → commit as Abhi-2104)

### P1 — Background automation (`_dec40`) — FIRST
1. **`drishti catchup`**: a schedule table in code (slot, time, weekdays, jobs). On each run: for every slot whose latest
   scheduled time ≤ now and that has no successful completion in `job_log` since that time, run its jobs; then a **gap fill**
   (`backfill` with a short `--days` window) for dated datasets: prices, delivery, participant_oi, index_closes, deals.
   File lock against overlap; time budget; exit code 1 if anything failed; one summary line per slot.
   - Morning: macro_daily, us_daily, premarket, insider, shp_master (+ calendars on Mon, security_master on Sun).
   - Evening: eod_bhavcopy, eod_flows, eod_corporate, macro_india, insider, signals_daily, gap fill.
   - Weekly (Sat): macro_weekly, `decisions forward`, **weekly pack** (below), auto-import of answers files.
   - Known loss: FII/DII cash is latest-day-only at NSE; a day the laptop stays off is lost (NSDL FPI covers FPI history).
2. **Holdings in the DB**: tables `holding_txn` (date, isin, qty, price, fees, source) and `holding_snapshot`; importer for
   `private/holdings.csv` (today's format) and the SBI Securities demat statement; later CAS PDF. XIRR from transactions
   (needs buy dates; until then cost-only P&L).
3. **`decisions candidates`**: screen the liquid universe (`universe.liquid`, top 800). Rank by what the 2017-21 study
   supported (strength vs Nifty over 3m, within 5% of the 52-week high); drop red flags (insider selling cluster, pledge strong
   caution, net deal selling, below liquidity floor, < 60 sessions). Top 15. Labelled **unproven** until the rules-v2 hold-out
   test and the forward log say otherwise.
4. **Weekly pack** in `private/weekly/<date>/`: non-blind bundles for holdings (exit gate) and candidates (entry gate), a
   `pack.json` index, `README.md` with the answer format. `catchup` imports `claude-*.jsonl` answer files it finds there
   (agent `claude`, model from the file), once.
5. **Task Scheduler**: rewrite `scripts/register-tasks.ps1` to register ONE task "Drishti catchup" (triggers: at logon,
   08:30, 20:30, Sat 10:00; StartWhenAvailable; RunOnlyIfNetworkAvailable; IgnoreNew; 2 h limit). `-Remove` still works.
   Update `docs/SCHEDULING.md`. The existing test that pins the script to `drishti jobs` is rewritten for the slot table.
6. **Claude scheduled task** (created by Claude with the scheduling tool, needs the laptop and the Drishti folder connected):
   Sat ~11:30 IST, retry Sun. Reads the newest unreviewed pack, checks news per stock, writes
   `claude-<date>.jsonl` (calls with thesis, invalidation, confidence, signals) and `review-<date>.md`. Does nothing if there
   is no new pack. Phone notification on finish.
- **Acceptance**: laptop off for 2 days → next logon fills both evenings' dated data; pytest green on Windows; one Saturday
  pack reviewed end to end with calls imported automatically.

### P2 — AI hookups (an evening, mostly config)
1. `drishti setup claude-desktop`: writes/merges `%APPDATA%\Claude\claude_desktop_config.json` (backup first) with
   `"drishti": {"command": "uv", "args": ["--directory", "<v2 path>", "run", "drishti", "mcp"]}`. Verify the tools list in
   Claude Desktop.
2. Kite MCP: add `https://mcp.kite.trade/mcp` as a custom connector in Claude (no Node needed) or via `npx mcp-remote`.
   Only when a Zerodha account exists. Read-only per Zerodha's reply (verify on first use: no order tools must succeed).
3. A **copilot brief** for Claude Desktop (a claude.ai Project's instructions): how to use the Drishti tools, the decision
   format, honesty rules (UNKNOWN is not neutral, cite signals, never place orders).
- **Acceptance**: in a Claude Desktop chat, "review my holdings" pulls holdings + signals through MCP and answers with calls.

### P3 — App skeleton
`drishti app` command, pywebview window, FastAPI with token, React shell (sidebar, top bar, dark/light), **Home** and
**Data & Jobs** pages, live job output. Backend API tests (pytest + httpx); UI smoke test (vitest). Screenshots reviewed.

### P4 — Portfolio
Holdings table, transactions, XIRR vs 25% goal, allocation (sector, size, stock vs fund), tax lots (short- vs long-term timer,
the ₹1.25 lakh yearly LTCG exemption shown as remaining headroom), latest call per holding with invalidation status
(price vs the invalidation level), import screens (CSV, demat statement).

### P5 — Stock page, Ideas, Market
- **Stock**: price chart (20/50/200-day, 52-week band, deal and insider markers), every stock signal with UNKNOWN shown,
  delivery, deals, insider, pledge, evidence claims with quotes and source links, announcements, all calls on this stock by
  every author and their scores, "Ask copilot" button.
- **Ideas**: this week's candidates with Claude's ENTER/WAIT/AVOID and reasons; filters; add to watchlist.
- **Market**: macro regime, FII/DII and FPI flows, participant OI, sector rotation, index valuation, cycle phase,
  seasonality and event calendar (RBI, results).

### P6 — Decisions, scorecard, reports
Decision log with filters; scorecard (copilot vs rules-v1 vs always-long vs Nifty, paired comparison with intervals, hit rate,
calibration chart); forward log; weekly reports (rendered markdown); study and portfolio-sim results.

### P7 — Chat panel
Docked right drawer with the embedded Claude Code terminal; status (installed? signed in?); workspace with `.mcp.json` and
`CLAUDE.md`; context buttons on Portfolio, Stock and Ideas pages. If Claude Code is missing, the panel shows how to install it
and stays empty — no fallback that imitates it.

### P8 — Installer and updates
- **Inno Setup** script `installer/drishti.iss` (free). `Drishti-Setup.exe` built on the laptop from a release zip
  (`git archive` of a tagged commit + built UI). Shared privately.
- Setup: install `uv` (official installer) if missing; copy app to `%LOCALAPPDATA%\Drishti\app`; `uv sync`; `drishti init`;
  register the catch-up task; Start-menu + desktop shortcuts; optional: write Claude Desktop MCP config.
- **First-run wizard in the app**: profile (goals, risk limits), holdings import, then the 10-year backfill with progress,
  budgeted and resumable (it took hours on this laptop; the wizard can be closed and resumes on next start).
- Updates: run the new setup; the DB in AppData is untouched; schema migrations run at start.
- Uninstall removes the app and the task; asks before deleting the DB.

### P9 — Later / optional
Zerodha Kite Connect direct (holdings and trades with dates for exact XIRR) — Zerodha says Kite Connect is now free; verify
terms and the daily login flow first. Mutual fund NAVs from AMFI. Local-model chat. CAS PDF import. Pledge history backfill.

## 4. Screens (summary)

| Screen | Key content |
|---|---|
| Home | Portfolio value, P&L, XIRR vs 25%, alerts (new insider/pledge flags, invalidation hits, failed jobs), market pulse strip, data freshness |
| Portfolio | Holdings, transactions, allocation, tax lots, calls per holding |
| Stock | Chart, signals, flows, evidence, calls and scores, ask copilot |
| Ideas | Weekly candidates + Claude's verdicts, watchlist |
| Market | Macro, flows, positioning, sectors, valuation, calendar |
| Decisions | Log, scorecard, calibration, forward log |
| Reports | Weekly reviews, study, simulations |
| Data & Jobs | Freshness per table, slot status, job runs, backfill progress, errors |
| Settings | Profile, risk limits, holdings import, AI connections, paths, update |
| Chat (drawer) | Embedded Claude Code with Drishti MCP |

Design: professional, dense but calm (Bloomberg-lite), dark and light themes, Indian number formatting (lakh/crore),
IST everywhere, keyboard shortcuts, every number links to its source and as-of date, UNKNOWN always visible.

## 5. Open questions (ask one at a time when the phase needs it)
1. P1: weekly candidate count (default 15) and whether ETFs/funds are in scope (default: stocks only).
2. P4: risk limits for sizing (max % per stock, per sector) and monthly investable amount — needed before position-size advice.
3. P3: app name/branding and default theme.
4. P2: does a Zerodha account exist yet (Kite MCP only then)?
5. rules-v2 spec sign-off (from 18 Nov) — still pending; it is the benchmark the copilot must beat.

## 6. Risks and honest limits
- **No proven edge yet.** The app makes Drishti easier to use; it does not make the signals better. The forward log and
  the scorecard are how we find out. rules-v2 and the candidate screen are labelled unproven until tested.
- Laptop must be on daily for FII/DII history; everything else catches up.
- Claude scheduled task and chat panel use plan tokens; the weekly review ≈ the size of the 18 Nov review.
- WebView2 / pywinpty are Windows-specific; the brother's laptop must be Windows 10/11.
- Scope creep: each phase ships something usable; stop and reassess after P4.

## 7. Continuation prompt (paste into a new chat in the Project)

> Continue Drishti **v2** (version: v2 only). Read in full: `v2/docs/UI-PLAN.md` (this plan: app, background automation,
> installer) and `v2/HANDOFF-2026-11-18.md` in the connected folder `C:\Users\abhij\Documents\Drishti`, plus the Project docs
> `claude/v2-handoff-2026-11-18.md` and `claude/v2-10yr-findings-2026-11-18.md`. Obey HANDOFF §1 exactly: commits as
> `Abhi-2104 <2022115003@student.annauniv.edu>` with **no Claude trailers**, never the work account, data never in git,
> `private/` holds holdings and is git-ignored, ask before writing Project docs, never disable TLS verification or evade a bot
> block, typed errors only, all HTTP through `core/fetch.py`, ISIN as join key, profile-neutral providers. **Don't start agents
> unless I say so.** Workflow: build in sandbox `/home/claude/v2` (pull code with `git archive HEAD v2` via the bridge and the
> fixtures tgz from `v2/tests/fixtures` if the sandbox is fresh), tar to `/mnt/user-data/outputs/_decN.tgz`, commit to
> `~/mnt/Drishti/v2/.venv/` via the bridge, **verify md5**, give me ONE PowerShell block; commit only after my pytest is green.
> State on 19 Nov: HEAD `23700e2` on `v2-p1-wip` (signal study, portfolio sim, supersedes in import, NO RECORDS fix); 711 tests.
> My holdings are in `private/holdings.csv` (12 stocks, 2 funds) and the first copilot review (17 Nov) is logged next to
> rules-v1's calls. Next: **UI-PLAN Phase P1 (`_dec40`)** — `drishti catchup` with the three slots, holdings tables + importer,
> `decisions candidates`, weekly pack, the single Task Scheduler task, then create the Saturday Claude review task. Then P2
> (Claude Desktop + Kite MCP), then the app phases. Plan briefly first, one question at a time, crisp answers.

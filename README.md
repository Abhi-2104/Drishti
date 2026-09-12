# Drishti — Equity Research Workflow

Decision-support for Indian equities (NSE and BSE). This tool pulls data, researches,
and structures entry and exit decisions with explicit triggers and price levels. It
structures the decision; you make the call.

Not financial advice. Not a SEBI-registered investment adviser. Always verify price
levels against your broker before acting.

---

## 1. How the workflow is organised

Two loops, by design, so effort is spent where it matters:

- **Monitoring loop — cheap, run daily.** One command (`/brief`) scans the whole
  watchlist and reports what changed. This is the habit.
- **Research loop — expensive, run on a trigger.** The remaining commands go deep on
  ONE name at a time, only when the daily scan (or your own judgement) flags something
  worth the effort.

Rule of thumb: the daily scan tells you *where to look*; the research commands tell you
*what to do about it*.

---

## 2. Methodology

Every command follows the same research discipline, regardless of which one is run.

**Every claim traces to a named source** (filing and date, earnings call and quarter,
rating action, or data print) — or is flagged low-confidence, never stated as fact on
general knowledge alone.

**Primary sources outrank commentary.** On any conflict between a filing and media
coverage, the filing wins; media is used for context only.

**One signal is not a conclusion.** A single data point is treated as noise until a
second, independent signal corroborates it.

**The unfavourable case is stated as plainly as the favourable one.** A red flag is
never softened or buried under the positive case.

**Where data comes from, what it's used for, and the technical route it's retrieved by:**

| Source | Used for | Retrieved via |
|---|---|---|
| NSE market and disclosure data | Price, valuation, insider trades, bulk/block deals, corporate actions, daily FII/DII | NSE's public data API, called through the `nse-mcp` connector (`manitgupta/NSE-MCP`) |
| BSE filings system | Company filings feed; the only disclosure source for BSE-only companies; merged with NSE filings for dual-listed ones | BSE's public filings API (`api.bseindia.com`), called directly |
| Screener.in | Multi-year fundamentals (revenue, margins, ROCE/ROE, debt, cash flow), promoter-shareholding trend | Screener.in's company pages, fetched and read directly (symbol/BSE-code/name resolved to the right company first) |
| Company-filed transcript PDFs | Earnings-call transcripts, read in full for the `/deep-dive` language-shift read | The transcript PDF linked from the company's Screener.in page, downloaded and read directly |
| CRISIL / ICRA / CARE, SEBI | Credit-rating actions, regulatory disclosures | Each agency's own public ratings/disclosure pages, fetched directly |

**Effort is spent only where a trigger justifies it.** This is the two-loop model from
Section 1 in practice: the cheap monitoring loop runs on the whole watchlist routinely,
and the expensive research loop is only ever pointed at one name at a time, on a
specific reason to look closer — never run wholesale across a list "just to check."

**A recommendation is a structured decision, not an instruction.** Every entry or exit
call ends with the required disclosure that this is decision support from a tool, not
advice from a licensed adviser, and that the decision — and the responsibility for it —
stays with you.

---

## 3. Data layer

| Layer | What it provides | Coverage |
|---|---|---|
| Live market data | Price, valuation multiples, 52-week range, market cap, traded volume | NSE and BSE |
| Company fundamentals | Multi-year revenue, profit, margins, return on capital, debt, cash flow, and ratios | NSE and BSE, including companies listed only on BSE |
| Promoter shareholding | Quarter-by-quarter promoter stake — a sell-down or accumulation signal | NSE and BSE |
| Exchange disclosures | Insider trades, bulk/block deals, corporate actions (dividends, splits, buybacks) | NSE |
| Company filings feed | Results, board-meeting outcomes, insider/large-shareholder disclosures, investor-meet notices | Both exchanges are pulled and merged for every name (most companies list on both, and the two do not always disclose identically); for a BSE-only company, this is its only filings channel |
| Earnings-call transcripts | The transcript is read in full, not inferred from the numbers, to catch a genuine shift in management's tone on demand, margins, or guidance | Wherever the company files one |
| Institutional flows | Net daily buying/selling by foreign and domestic institutions | Market-wide, see note below |
| Credit ratings | Latest rating action from CRISIL / ICRA / CARE and peers | Rated companies |
| IPOs (primary market) | Open and upcoming public issues with their particulars, and the prospectus (DRHP/RHP) read in full for `/ipo` | NSE public-issue feed + the offer document filed with SEBI/the exchanges |
| Market regime | Macro backdrop, derivatives smart-money positioning (participant open interest), sector rotation, cycle phase — for `/macro` and entry timing | Yahoo Finance (macro, sectors) + NSE participant-OI archive |
| US equities | Live US quotes, restated annual financials, and filings (10-K/10-Q, 8-K, insider Form 4) for `/deep-dive` on US names | Yahoo Finance + SEC EDGAR |
| News | A curated, tiered source list — not open-ended web search | Scoped to the watchlist |

### A note on institutional flows (FII/DII)

The FII/DII figure pulled each day is a **market-wide aggregate** — one net-buy/net-sell
number for the whole exchange, not a per-company breakdown. That is the nature of the
underlying data release itself, not a gap in how it is read: there is no stock-level
FII/DII number to filter down to a watchlist. It is useful as macro context ("is the
market as a whole seeing institutional buying or selling today"), not as a signal on any
one name.

The nearest **stock-level** proxy for "who is buying or selling this name in size" is
**bulk and block deal data**, which is already reported per company and can be scoped to
the watchlist. Read institutional flow at two levels: FII/DII for the market mood,
bulk/block deals for the individual name.

### A note on news

News is read from a fixed, tiered whitelist, never an open web search:

- **Tier 1 (primary, weighted highest):** exchange announcements and disclosures, the
  regulator, and credit-rating actions. These win on any conflict with media reporting.
- **Tier 2 (reputable media):** established financial press, used for context and to
  catch what a filing does not say outright.
- **Tier 3 (per-company alerts):** a keyword alert per watchlist name, for anything the
  first two tiers miss.
- **Excluded, always:** stock-tip blogs and pump/promotion channels. Treated as noise at
  best and manipulation at worst.

**Where to edit the whitelist:** `config/news-sources.md`. Add or remove a source under
its tier by editing that file directly — no code change needed. A source added there is
picked up the next time the daily scan runs.

---

## 4. Where the watchlist lives

The watchlist is the research universe scanned by the daily digest and used as the
default pool for candidate screening. It is **not** a buy list — a name earns its place
by what the research shows, not by being listed.

**File:** `config/watchlist.md`

**To add a name:** add one line under the relevant sector heading, in the form
`TICKER — one-line note on why it's on the list`. Create a new sector heading if none
fits. To remove a name, delete its line. Changes take effect immediately — the next
`/brief` picks up the current file as-is.

Keep the one-line note honest and specific ("why this name, why now") — it is what
turns a list of tickers into a research aid rather than a bookmark folder.

---

## 5. The commands

### `/brief` — daily monitoring digest
**Cost: low. Scope: the whole watchlist.**

The daily habit. Scans every name on the watchlist for price/flow moves and whitelist
news, skips what has already been reported, and returns a short digest with a shortlist
of names worth a closer look.

**Run it:** every morning, or whenever you want a pulse check before deciding whether
anything needs deeper attention.

**Example:**
```
/brief
```

---

### `/screen <criteria>` — find candidates
**Cost: moderate. Scope: discovery, not a single name.**

Turns a plain description of what you're looking for into a ranked list of candidates,
each with the reason it matched. Used to widen the funnel before committing research
time to a specific name.

**Example use-cases:**
- Sourcing ideas around a theme you believe in but haven't picked a name for yet.
  ```
  /screen capital-goods names with a multi-year order book and low debt
  ```
- Widening beyond the current watchlist when nothing on it fits a new thesis.
  ```
  /screen private banks with improving asset quality and reasonable valuation
  ```
- A first, cheap filter before `/deep-dive` on the names that pass.
  ```
  /screen cement companies benefiting from the infrastructure capex cycle
  ```

---

### `/macro` — market regime read
**Cost: moderate. Scope: the whole market, not one name.**

The top-down context command. Pulls the macro backdrop (dollar index, US yields, crude, India
VIX), the derivatives **smart-money positioning** (are foreign institutions and prop desks
positioned with or against retail), and **sector rotation** (which sectors are leading or
rotating in) — then places them on the economic cycle and the Indian seasonal calendar to give a
**risk-on / neutral / risk-off** read. Use it to time entries: a name can pass on its own merits
but still warrant waiting if the regime is hostile.

**Run it when:** starting a session, or before committing capital, to know which way the tape
leans and which sectors to favour.

**Example:**
```
/macro
```

---

### `/deep-dive <name>` — full research on one name
**Cost: high. Scope: one name.** The core research command.

The complete file on a company: what it does and how revenue breaks down, the
multi-year fundamentals trend (not a single snapshot), valuation against its own
history, the actual language shift read from recent earnings-call transcripts,
disclosure checks (insider activity, promoter-holding trend, corporate actions), recent
material news, the latest credit-rating action, and an even-handed list of what could
go right and what could go wrong. Closes with a short, direct bottom line and points you
to `/bull-bear` and then `/entry`.

**Run it when:** something from `/brief` or `/screen` earns a closer look, or before
sizing any real position.

**Example:**
```
/deep-dive RELIANCE
```

---

### `/panel <name>` — multi-perspective read
**Cost: high. Scope: one name.**

Five distinct investing lenses each render a call — buy, hold, or sell, with a
conviction level and the reasoning — then a synthesis weighs them against each other.
**Where the lenses disagree is itself the signal**, not noise to average away.

The five lenses:
- **Quality-and-moat** — durable competitive advantage, return on capital, whether the
  price is fair for that quality.
- **Value-and-safety** — margin of safety, balance-sheet strength, whether it is
  genuinely cheap.
- **Growth-and-simplicity** — is the growth story easy to understand, does the price
  make sense relative to the growth rate.
- **Macro-and-timing** — is there a sector or macro tailwind, and is the risk/reward
  asymmetric right now.
- **Patient-domestic-compounder** — management quality plus a long-term India growth
  theme, read with a multi-year holding patience.

**Run it when:** a name genuinely splits opinion and you want the bull case and bear
case both argued at full strength, or as a cross-check after a `/deep-dive`.

**Example:**
```
/panel HDFCBANK
```

---

### `/bull-bear <name>` — adversarial stress test
**Cost: moderate (reuses the session's existing research). Scope: one name.**

Builds the single strongest bull case and the single strongest bear case, refusing to
manufacture false balance on either side, then names precisely what has to remain true
for the bull case to hold — and the one fact that would prove it wrong.

**Run it when:** the research is done and you want the thesis pressure-tested before
committing capital, or whenever you notice you're only seeing one side of a position.

**Example:**
```
/bull-bear TCS
```

---

### `/entry <name>` — go / wait / pass
**Cost: moderate. Scope: one name.**

The entry discipline. All of the following must hold, and the output states exactly
which ones do not:

1. **Thesis** — a driver that traces to an actual source, not a hunch.
2. **Quality and valuation** — clears a quality floor, and is not stretched against its
   own trading history or comparable peers.
3. **Disclosure check** — no cluster of insider selling, no rising pledge signal, no
   recent rating warning.
4. **Level** — a defined price zone to buy into, never "at market, right now."
5. **Risk** — a stop-loss and a position size decided *before* any entry, not after.

**Run it when:** the research and stress-test are both done and the only remaining
question is whether, where, and how much to buy.

**Output:** a verdict, an entry zone and stop if it passes, or a clear statement of what
is missing if it does not.

**Example:**
```
/entry MARUTI
```

---

### `/exit <name>` — hold / trim / exit
**Cost: moderate. Scope: one name you hold, or one you have logged a thesis for.**

The exit discipline. Only fires on a genuine trigger:

- **Thesis break** — a rating downgrade, a rising pledge, a cluster of insider selling,
  a guidance cut, or a real deterioration in the order book or margins.
- **Target reached.**
- **Stop reached.**

**A falling price on its own is never treated as a thesis break.** If none of the
triggers has fired, the output says so plainly and holds the position, rather than
reacting to price movement alone.

**Run it when:** a holding moves sharply and you need to know whether it is noise or a
genuine break in the thesis, or as a periodic check on a logged position.

**Example:**
```
/exit SUNPHARMA
```

---

### `/ipo <name>` — IPO application analysis
**Cost: high. Scope: one public issue.**

The primary-market cousin of `/deep-dive`: the same discipline applied to a company that
isn't listed yet, so there's no price history — the analysis rests on the **prospectus**
(the DRHP / RHP offer document), read in full. It covers what the company does, the issue
(how much is fresh money for the company versus existing holders cashing out), what the
money will be used for, the restated financials, the asking valuation against listed peers,
whether promoters or private-equity backers are exiting into the issue, and the risk
factors — ending in an **APPLY / SKIP / NEUTRAL** call with the single biggest risk named.

Grey-market premium (GMP) is deliberately **not** used — it is unofficial, unregulated, and
easily manipulated, the same category of noise the tool excludes everywhere else.

**Run it when:** an IPO is open or upcoming and you're deciding whether to apply.

**Example:**
```
/ipo <company name>
```

---

### `/port-patterns` — adapt external research patterns
**Cost: occasional/setup. Scope: the framework itself, not a stock.**

Brings in and adapts useful research patterns from outside reference material (for
example, the multi-perspective panel format). This is a framework-maintenance command,
used occasionally rather than as part of the daily or per-name workflow.

---

## 6. The typical path from idea to decision

```
/brief                    daily: what changed today
      |
/screen <criteria>        optional: source new candidate names
      |
/deep-dive <name>         the full file on one name
      |
/panel  or  /bull-bear    stress-test the thesis from multiple angles
      |
/entry <name>             go / wait / pass, with a level and a stop
      |            (if a position is taken)
/exit <name>              monitored for a genuine trigger going forward
```

Work one name at a time through the deep research commands. The daily digest exists
precisely so the full research and decision commands are never run against the whole
watchlist at once.

---

## 7. Planned: personal portfolio integration (Zerodha)

**Status: scoped, not yet connected.**

A connection to Zerodha's Kite platform is planned so that the tool can read your own
actual holdings and profit/loss — for your own positions only, never as a data source
for researching other companies. Once connected, the intended scope is:

- Seeing your actual holdings and their entry price alongside `/exit` checks, so the
  exit discipline is checked against what you genuinely hold rather than a logged note.
- Cross-referencing your own position sizes against the sizing that `/entry` recommends.

This integration is **read-only by design** — it is for visibility into your own book,
not for placing trades. No trade-execution capability is planned. This document will be
updated once the connection is active and in daily use.

---

*Decision support, not financial advice. Not a SEBI-registered investment adviser. You
own the decision. Verify every price level against your broker before acting.*

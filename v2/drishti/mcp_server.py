"""`drishti mcp`: a stdio MCP server that lets Claude read the store. Every tool returns the same envelope as the
providers ({data, as_of, sources, error}), so a stale or failed answer is visible to the model and never passes as data.

Tools read what the jobs have already stored; they do not fetch. If something is missing the error says which
`drishti run <job>` fills it. The tool functions are plain functions of a Store so they test without a server.
Profile-neutral: nothing here knows about either brother's portfolio.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable

from . import resolve as resolver
from .core.canary import IST
from .core.envelope import DrishtiError, ErrorKind, Result
from .store import Store

INSIDER_MAX_AGE_DAYS = 5   # newest stored filing older than this -> STALE (the insider job runs twice a day)
VALID_DIRECTIONS = ("BUY", "SELL", "PLEDGE", "REVOKE", "INVOKE")


def _env(res: Result) -> dict:
    return res.to_dict()


def tool_data_health(store: Store) -> dict:
    """Freshness of every dataset, the latest result of every job step and every canary. status is 'degraded' if any failed."""
    h = store.data_health()
    return _env(Result(data=h, as_of=h["generated_at"][:10]))


def tool_resolve_security(store: Store, query: str) -> dict:
    """Find a security by ISIN, NSE symbol, BSE symbol or BSE scrip code. ambiguous=true means several companies matched:
    ask which one, never pick."""
    return _env(resolver.resolve(store, query))


def tool_insider_trades(store: Store, query: str | None = None, days: int = 30, direction: str | None = None,
                        limit: int = 50, *, today: date | None = None) -> dict:
    """SEBI insider-trading disclosures (promoters, directors, designated persons) filed in the last `days` days, newest
    first, with buy/sell totals. `query` narrows to one company (ISIN, symbol or scrip code); `direction` is one of
    BUY SELL PLEDGE REVOKE INVOKE. Percentages are of the company's share capital."""
    today = today or datetime.now(IST).date()
    try:
        if not store.query("SELECT 1 AS x FROM sqlite_master WHERE name='insider_event'"):
            raise DrishtiError(ErrorKind.NOT_FOUND, "no insider data stored yet; run `drishti run insider`")
        if direction is not None and direction.upper() not in VALID_DIRECTIONS:
            raise DrishtiError(ErrorKind.PARSE_FAIL, f"direction must be one of {VALID_DIRECTIONS}, got {direction!r}")
        days, limit = max(1, min(int(days), 365)), max(1, min(int(limit), 500))
        where, args = ["substr(filed_at, 1, 10) >= ?"], [(today - timedelta(days=days)).isoformat()]
        if query:
            r = resolver.resolve(store, query)
            if not r.ok:
                raise r.error
            if r.data["ambiguous"]:
                names = [f"{m['name']} ({m['isin']})" for m in r.data["matches"]]
                raise DrishtiError(ErrorKind.PARSE_FAIL, f"{query!r} matches several companies, pass an ISIN: {names}")
            m = r.data["matches"][0]
            syms = [s for s in (m["nse_symbol"], m["bse_symbol"]) if s]
            where.append("(isin = ? OR (isin IS NULL AND symbol IN (%s)))" % ",".join("?" * len(syms)))
            args += [m["isin"], *syms]
        if direction:
            where.append("direction = ?")
            args.append(direction.upper())
        sql = " AND ".join(where)
        rows = store.query(f"SELECT * FROM insider_event WHERE {sql} ORDER BY filed_at DESC, event_key LIMIT ?", (*args, limit))
        total = store.query(f"SELECT COUNT(*) AS n FROM insider_event WHERE {sql}", args)[0]["n"]
        agg = {r["direction"]: r for r in store.query(
            f"SELECT direction, COUNT(*) AS n, SUM(value_inr) AS value_inr FROM insider_event WHERE {sql} GROUP BY direction", args)}
        newest = store.query("SELECT MAX(filed_at) AS f FROM insider_event")[0]["f"]
        data = {"window_days": days, "matched": total, "returned": len(rows), "events": rows,
                "totals": {d: {"events": a["n"], "value_inr": a["value_inr"]} for d, a in agg.items()},
                "net_buy_value_inr": (agg.get("BUY", {}).get("value_inr") or 0) - (agg.get("SELL", {}).get("value_inr") or 0)}
        age = (today - date.fromisoformat(newest[:10])).days if newest else None
        if age is None or age > INSIDER_MAX_AGE_DAYS:
            return _env(Result(data=data, as_of=newest[:10] if newest else None,
                               error=DrishtiError(ErrorKind.STALE, f"newest stored filing is {age}d old (limit {INSIDER_MAX_AGE_DAYS}d); run `drishti run insider`")))
        return _env(Result(data=data, as_of=newest[:10]))
    except DrishtiError as e:
        return _env(Result(error=e))


def _date(as_of: str | None) -> date | None:
    return date.fromisoformat(as_of) if as_of else None


def tool_signal(store: Store, name: str, as_of: str | None = None, query: str | None = None) -> dict:
    """Compute one signal from stored data up to `as_of` (default today). Single-name signals need `query`."""
    from . import signals
    from .signals.base import SettingsError
    try:
        on = _date(as_of)
        kw = {}
        if name in signals.SINGLE_NAME:
            if not query:
                raise DrishtiError(ErrorKind.PARSE_FAIL, f"{name} needs a company (ISIN, symbol or BSE scrip code)")
            r = resolver.resolve(store, query)
            if not r.ok:
                raise r.error
            if r.data["ambiguous"]:
                raise DrishtiError(ErrorKind.PARSE_FAIL, f"{query!r} matches several companies, pass an ISIN: "
                                                         f"{[m['name'] + ' (' + m['isin'] + ')' for m in r.data['matches']]}")
            m = r.data["matches"][0]
            kw = {"isin": m["isin"]} if name in ("pledge", "price_context") else {"isin": m["isin"], "symbol": m["nse_symbol"]}
        return _env(signals.compute(store, name, on, **kw))
    except SettingsError as e:
        return _env(Result(error=DrishtiError(ErrorKind.PARSE_FAIL, f"signal-settings.toml: {e}")))
    except ValueError as e:
        return _env(Result(error=DrishtiError(ErrorKind.PARSE_FAIL, f"bad as_of date: {e}")))
    except KeyError:
        return _env(Result(error=DrishtiError(ErrorKind.NOT_FOUND, f"unknown signal {name!r}")))
    except DrishtiError as e:
        return _env(Result(error=e))


def tool_disclosure_screen(store: Store, as_of: str | None = None) -> dict:
    """Market-wide: companies with an insider cluster or size buying, and companies with pledge flags."""
    from .signals import disclosure
    try:
        data = disclosure.screen(store, _date(as_of))
        return _env(Result(data=data, as_of=data["as_of"]))
    except ValueError as e:
        return _env(Result(error=DrishtiError(ErrorKind.PARSE_FAIL, f"bad as_of date: {e}")))


def _stock_isin(store: Store, query: str | None) -> str:
    r = resolver.resolve(store, query or "")
    if not r.ok:
        raise r.error
    if r.data["ambiguous"]:
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"{query!r} matches several companies, pass an ISIN")
    return r.data["matches"][0]["isin"]


def tool_gather(store: Store, gate: str, as_of: str, query: str | None = None, blind: bool = False) -> dict:
    """Everything a decision for this gate may use, as of a date, with a hash that proves two authors saw the same evidence."""
    from .decisions import gather
    try:
        isin = _stock_isin(store, query) if query else None
        return _env(gather.gather(store, gate, as_of, isin=isin, blind=blind))
    except DrishtiError as e:
        return _env(Result(error=e))


def tool_record_decision(store: Store, gate: str, as_of: str, action: str, horizon_days: int, thesis: str, agent: str, model: str,
                         signals: list[str], query: str | None = None, confidence: float | None = None, invalidation: str | None = None,
                         bundle_sha: str | None = None, supersedes: str | None = None) -> dict:
    from .decisions import log
    try:
        isin = _stock_isin(store, query) if query else None
        sym = (store.get_security(isin) or {}).get("nse_symbol") if isin else None
        return _env(log.record(store, gate=gate, as_of=as_of, action=action, horizon_days=horizon_days, thesis=thesis, agent=agent, model=model,
                               signals_cited=signals, isin=isin, symbol=sym, confidence=confidence, invalidation=invalidation,
                               bundle_sha=bundle_sha, supersedes=supersedes))
    except DrishtiError as e:
        return _env(Result(error=e))


def tool_get_decisions(store: Store, gate: str | None = None, agent: str | None = None, model: str | None = None, query: str | None = None,
                       since: str | None = None, until: str | None = None, limit: int = 50) -> dict:
    from .decisions import log
    try:
        isin = _stock_isin(store, query) if query else None
        rows = log.current(store, gate=gate, agent=agent, model=model, isin=isin, since=since, until=until, limit=max(1, min(int(limit), 500)))
        return _env(Result(data={"decisions": rows, "n": len(rows)}, as_of=rows[0]["as_of"] if rows else None))
    except DrishtiError as e:
        return _env(Result(error=e))


def tool_score_decisions(store: Store, gate: str | None = None, agent: str | None = None, model: str | None = None, since: str | None = None,
                         until: str | None = None, compare: list[str] | None = None) -> dict:
    """Per-author scorecard (pending and missing-data cases are counted, never dropped) and, with compare=['a/model','b/model'], a paired
    comparison that names a winner only with enough cases and a clear interval."""
    from .decisions import score
    rows = score.score_all(store, gate=gate, agent=agent, model=model, since=since, until=until)
    rows = rows + score.baseline_rows(rows)
    data = {"summary": score.summarise(rows), "method": score.SCORE_VERSION}
    if compare:
        if len(compare) != 2:
            return _env(Result(error=DrishtiError(ErrorKind.PARSE_FAIL, "compare takes two authors, each written agent/model")))
        data["comparison"] = score.compare(rows, compare[0], compare[1], gate=gate)
    return _env(Result(data=data, as_of=max((r["as_of"] for r in rows), default=None)))


SIGNAL_TOOLS = {   # tool name -> (signal name, description). v1 names kept where v1 had the tool.
    "get_macro_regime": ("macro_regime", "Macro tide, -6..+6: DXY, US 10Y, Brent, India VIX, USD/INR move, US-vs-India VIX (v1 thresholds)."),
    "get_participant_oi": ("participant_oi", "Derivatives smart money: (FII + Pro) - Client from NSE participant OI; +/-4 = strong."),
    "get_sector_rotation": ("sector_rotation", "Sector relative strength vs Nifty 50, RRG quadrant (Leading/Weakening/Improving/Lagging), fresh crosses."),
    "get_seasonality": ("seasonality", "India calendar position: quarter theme, budget and monsoon stage, results season, next RBI MPC."),
    "get_cycle_phase": ("cycle_phase", "Partial cycle-phase probabilities (Recovery/Expansion/Late/Contraction) from v1 /macro rules; lists missing inputs."),
    "get_midcap_ratio": ("midcap_ratio", "Nifty Midcap 150 / Nifty 50 trend: midcaps outperforming or lagging."),
    "get_index_valuation": ("index_valuation", "Nifty 50 / Midcap 150 / Smallcap 250 PE, PB, dividend yield, each against its own stored history."),
    "get_fii_dii": ("fii_dii", "Cash-market FII/DII net flows: today, 5 and 20 sessions, streaks. Confirmation, not thesis."),
    "get_liquidity": ("liquidity", "US liquidity: Fed balance sheet 13-week change and M2 year-on-year."),
    "get_credit_spreads": ("credit_spreads", "US high-yield spread level and 20-session change, plus the 10Y-2Y curve."),
}


def build_server(open_store: Callable[[], Store], load_profile: Callable[[], object] | None = None):
    """The MCP server. A store is opened per call and closed after, so a long-lived server never holds a stale connection."""
    from mcp.server.mcpserver import MCPServer   # imported here so the rest of drishti works without the SDK installed

    srv = MCPServer("drishti", instructions=(
        "Drishti: Indian-market data the user's jobs have already collected (NSE, BSE, SEBI filings, flows, macro). "
        "Every reply is {data, as_of, sources, error}. If error is set, say so and do not present data as current. "
        "Call data_health first when freshness matters."))

    @srv.tool()
    def data_health() -> dict:
        """Freshness of every dataset, latest result of every job, canaries. Start here to know what is fresh and what failed."""
        with open_store() as st:
            return tool_data_health(st)

    @srv.tool()
    def resolve_security(query: str) -> dict:
        """Resolve an ISIN, NSE symbol, BSE symbol or BSE scrip code to the security (ISIN, names, both exchange codes)."""
        with open_store() as st:
            return tool_resolve_security(st, query)

    @srv.tool()
    def insider_trades(query: str | None = None, days: int = 30, direction: str | None = None, limit: int = 50) -> dict:
        """Insider-trading disclosures filed in the last `days` days, newest first, with buy/sell totals. Optional `query` (ISIN,
        symbol or scrip code) for one company; optional `direction` BUY, SELL, PLEDGE, REVOKE or INVOKE."""
        with open_store() as st:
            return tool_insider_trades(st, query, days, direction, limit)

    def _register(tool_name: str, signal_name: str, desc: str):
        def fn(as_of: str | None = None) -> dict:
            with open_store() as st:
                return tool_signal(st, signal_name, as_of)
        fn.__name__ = tool_name
        fn.__doc__ = desc + " Optional as_of (YYYY-MM-DD) reads only data up to that date."
        srv.tool(name=tool_name, description=fn.__doc__)(fn)

    for t, (sig, desc) in SIGNAL_TOOLS.items():
        _register(t, sig, desc)

    @srv.tool()
    def get_options_flow(symbol: str = "NIFTY", as_of: str | None = None) -> dict:
        """Index options from the EOD F&O file: PCR (nearest expiry and all), max pain, call/put OI walls. symbol NIFTY or BANKNIFTY."""
        with open_store() as st:
            return tool_signal(st, f"options_flow:{symbol.upper()}", as_of)

    @srv.tool()
    def get_insider_cluster(query: str, as_of: str | None = None) -> dict:
        """One company's open-market insider trades over 90 days: selling clusters (scoring.md strong negative) and size buying (strong positive)."""
        with open_store() as st:
            return tool_signal(st, "insider_cluster", as_of, query)

    @srv.tool()
    def get_pledge(query: str, as_of: str | None = None) -> dict:
        """One company's promoter pledge from its shareholding filings: >25% pledged = strong caution, rising pledge = red flag (scoring.md)."""
        with open_store() as st:
            return tool_signal(st, "pledge", as_of, query)

    @srv.tool()
    def screen_disclosures(as_of: str | None = None) -> dict:
        """Market-wide discovery: companies with insider clusters or size buying, and companies with pledge flags."""
        with open_store() as st:
            return tool_disclosure_screen(st, as_of)

    from . import data_tools as dt

    def _with(fn, *a, **k):
        with open_store() as st:
            return _env(fn(st, *a, **k))

    @srv.tool()
    def get_fundamentals(query: str, refresh: bool = False) -> dict:
        """Screener fundamentals for one company: quarterly results, annual P&L, balance sheet, cash flow, ratios, shareholding,
        concall/annual-report links, plus ROCE/ROE/debt-equity/OCF-vs-profit/growth. Rs crore. Cached for 7 days."""
        return _with(dt.fundamentals, query, refresh=refresh)

    @srv.tool()
    def get_quote(query: str) -> dict:
        """Live or delayed quote (NSE, with BSE as a cross-check; BSE for BSE-only names). `live` says whether the market is open."""
        return _with(dt.quote, query)

    @srv.tool()
    def get_announcements(query: str, days: int = 30) -> dict:
        """One company's filings from NSE and BSE, merged and de-duplicated, newest first, with PDF links."""
        return _with(dt.announcements, query, days=days)

    @srv.tool()
    def get_corporate_actions(query: str) -> dict:
        """Dividends, splits, bonuses, rights, buybacks for one company (stored since the eod_corporate job runs)."""
        return _with(dt.corporate_actions, query)

    @srv.tool()
    def get_delivery(query: str, days: int = 60) -> dict:
        """Delivery % per session for one NSE stock (delivered / traded quantity), newest first, with the 20-session mean."""
        return _with(dt.delivery, query, days=days)

    @srv.tool()
    def get_india_macro(as_of: str | None = None) -> dict:
        """India CPI (headline and food), WPI, IIP and RBI reserves: latest published reading, its month, age, and a stale flag."""
        return _with(dt.india_macro, as_of=as_of)

    @srv.tool()
    def get_news(query: str | None = None, days: int = 3, limit: int = 50) -> dict:
        """Headlines from the investor's own RSS / Atom feeds, newest first; with `query`, those mentioning one company (string-matched)."""
        return _with(dt.news, query, days=days, limit=limit)

    @srv.tool()
    def get_credit_ratings(query: str) -> dict:
        """Credit-rating actions for one company, with the earlier rating and whether it moved up or down the scale."""
        return _with(dt.credit_ratings, query)

    @srv.tool()
    def get_deals(query: str, days: int = 90) -> dict:
        """Bulk, block and short-selling deals in one company's shares over the last `days` days."""
        return _with(dt.deals, query, days=days)

    @srv.tool()
    def get_calendar(query: str | None = None, days: int = 30) -> dict:
        """Upcoming board meetings (results dates, dividends, fund raising) from NSE and BSE. One company with `query`,
        else the whole market, for the next `days` days."""
        return _with(dt.calendar, query, days=days)

    @srv.tool()
    def get_fpi_flows(days: int = 10) -> dict:
        """NSDL-confirmed foreign portfolio investor flows per day (equity exchange / primary, debt, total; Rs crore and
        US$ mn). Lags NSE's provisional FII figure by a day; use both."""
        return _with(dt.fpi_flows, days=days)

    @srv.tool()
    def get_us_quote(ticker: str) -> dict:
        """US stock quote (Yahoo, delayed). Also starts tracking the ticker's daily prices."""
        return _with(dt.us_quote, ticker)

    @srv.tool()
    def get_us_fundamentals(ticker: str, as_of: str | None = None) -> dict:
        """US company fundamentals from SEC XBRL filings: revenue, income, EPS, cash flow, balance sheet, growth, ROE,
        leverage. `as_of` (YYYY-MM-DD) returns only what had been filed by that date."""
        return _with(dt.us_fundamentals, ticker, as_of=as_of)

    @srv.tool()
    def get_us_filings(ticker: str) -> dict:
        """Recent SEC filings (10-K, 10-Q, 8-K, Form 4 insider trades, proxy, 13F) with links."""
        return _with(dt.us_filings, ticker)

    @srv.tool()
    def get_us_13f(filer: str) -> dict:
        """An institution's latest 13F holdings (ticker such as BRK-B, or CIK number)."""
        return _with(dt.us_13f, filer)

    @srv.tool()
    def get_market_today() -> dict:
        """Pre-market page: Gift Nifty vs the last Nifty close, open/upcoming IPOs, next F&O expiries, next holidays."""
        return _with(dt.market_today)

    @srv.tool()
    def get_price_context(query: str, as_of: str | None = None) -> dict:
        """One company's price facts: returns, 52-week range, 20/50/200-day averages, ATR, volume, strength vs the Nifty."""
        with open_store() as st:
            return tool_signal(st, "price_context", as_of, query)

    @srv.tool()
    def get_option_chain(symbol: str = "NIFTY", expiry: str | None = None) -> dict:
        """Intraday option chain for an index and expiry (default nearest). Live in market hours; closing state after."""
        from .providers import nse_quote
        return _env(nse_quote.option_chain(symbol.upper(), expiry))

    @srv.tool()
    def gather_evidence(gate: str, as_of: str, query: str | None = None, blind: bool = False) -> dict:
        """Evidence bundle for one decision (gate: entry, exit, screen, macro; stock gates need `query`), computed only from data up to
        `as_of`, with its hash and the allowed actions. blind=true hides the stock's identity."""
        with open_store() as st:
            return tool_gather(st, gate, as_of, query, blind)

    @srv.tool()
    def record_decision(gate: str, as_of: str, action: str, horizon_days: int, thesis: str, agent: str, model: str, signals: list[str],
                        query: str | None = None, confidence: float | None = None, invalidation: str | None = None,
                        bundle_sha: str | None = None, supersedes: str | None = None) -> dict:
        """Append one decision to the log (never edited; correct with supersedes). confidence = probability the call turns out right over
        horizon_days trading sessions (0.50..0.99; omit for a no-call). A call needs thesis, invalidation and at least one cited signal."""
        with open_store() as st:
            return tool_record_decision(st, gate, as_of, action, horizon_days, thesis, agent, model, signals, query, confidence, invalidation,
                                        bundle_sha, supersedes)

    @srv.tool()
    def get_decisions(gate: str | None = None, agent: str | None = None, model: str | None = None, query: str | None = None,
                      since: str | None = None, until: str | None = None, limit: int = 50) -> dict:
        """Logged decisions (current ones, newest first), filterable by gate, author, company and date."""
        with open_store() as st:
            return tool_get_decisions(st, gate, agent, model, query, since, until, limit)

    @srv.tool()
    def score_decisions(gate: str | None = None, agent: str | None = None, model: str | None = None, since: str | None = None,
                        until: str | None = None, compare: list[str] | None = None) -> dict:
        """How logged decisions turned out against the Nifty 50 (stock gates) or cash (macro gate): hit rate, edge, calibration per author,
        plus a free always-long baseline. compare=['agent/model','agent/model'] adds a paired comparison."""
        with open_store() as st:
            return tool_score_decisions(st, gate, agent, model, since, until, compare)


    def _profile():
        try:
            return load_profile() if load_profile else None
        except Exception:                       # no profile yet: levels and the watchlist degrade, nothing else changes
            return None

    @srv.tool()
    def get_trade_levels(query: str, as_of: str | None = None, risk_budget_pct: float = 1.0) -> dict:
        """Derived (not predicted) trade levels for one stock: entry zone or the level that turns the trend, a volatility stop
        (2.5 x average true range), and a position size from your max_position_pct and a risk budget (default 1% of portfolio).
        No target is derived. Price-history arithmetic only: it ignores fundamentals and results dates."""
        from .decisions import levels
        with open_store() as st:
            try:
                isin = _stock_isin(st, query)
                return _env(levels.derive(st, isin, _date(as_of), profile=_profile(), risk_budget_pct=risk_budget_pct))
            except DrishtiError as e:
                return _env(Result(error=e))

    @srv.tool()
    def save_thesis(query: str, as_of: str, thesis: str, exit_triggers: list[str], entry_low: float | None = None, entry_high: float | None = None,
                    stop: float | None = None, target: float | None = None, size_pct: float | None = None,
                    must_stay_true: list[str] | None = None, decision_id: str | None = None) -> dict:
        """Write the working thesis for a stock you hold or plan to: thesis, entry zone, stop, target, size, what must stay true and
        observable exit triggers. Closes the previous open thesis for that stock. /exit and the watchlist scan read it."""
        from .decisions import thesis as th
        with open_store() as st:
            try:
                isin = _stock_isin(st, query)
                sym = (st.get_security(isin) or {}).get("nse_symbol")
                return _env(th.save(st, isin=isin, symbol=sym, as_of=as_of, thesis=thesis, entry_low=entry_low, entry_high=entry_high, stop=stop,
                                    target=target, size_pct=size_pct, must_stay_true=must_stay_true, exit_triggers=exit_triggers, decision_id=decision_id))
            except DrishtiError as e:
                return _env(Result(error=e))

    @srv.tool()
    def get_thesis(query: str, include_closed: bool = False) -> dict:
        """The open thesis for a stock (or all, with include_closed) with its re-check history."""
        from .decisions import thesis as th
        with open_store() as st:
            try:
                return _env(th.get(st, _stock_isin(st, query), include_closed=include_closed))
            except DrishtiError as e:
                return _env(Result(error=e))

    @srv.tool()
    def recheck_thesis(thesis_id: str, as_of: str, intact: bool, note: str, close: bool = False) -> dict:
        """Log a re-check of a thesis (intact or not, and what changed). close=true ends it (sold, or the thesis broke)."""
        from .decisions import thesis as th
        with open_store() as st:
            return _env(th.recheck(st, thesis_id, as_of=as_of, intact=intact, note=note, close=close))

    @srv.tool()
    def watch_scan(as_of: str | None = None, since: str | None = None, mark: bool = False) -> dict:
        """Cheap watchlist monitor: per watched name, what is new since the last scan (insider trades, bulk/block deals, announcements,
        pledge and insider flags, trend) and ESCALATE lines for names with an open thesis whose stop or target hit or whose red flags rose.
        mark=true remembers today as the last scan so the next one shows only what is new."""
        from .decisions import watch
        with open_store() as st:
            return _env(watch.scan(st, _profile(), _date(as_of) or date.today(), since=since, mark=mark))

    # --- evidence layer (E1): documents, and claims tied to verbatim quotes -------------------------------------------------
    @srv.tool()
    def add_evidence_source(kind: str, title: str, published_at: str, text: str | None = None, path: str | None = None,
                            query: str | None = None, url: str | None = None) -> dict:
        """Store a document as evidence: an earnings-call transcript, annual report, announcement, results, prospectus or rating
        report. Give its full `text` (or a local `path` to a .txt file), the company (`query`: ISIN or symbol) and `published_at`, the
        date it became public: queries as of an earlier day never see it. Returns a source_id for record_evidence."""
        from . import evidence as ev
        with open_store() as st:
            try:
                body = text if text is not None else (Path(path).read_text(encoding="utf-8", errors="replace") if path else None)
                if not body:
                    raise DrishtiError(ErrorKind.REJECTED, "give the document's text or a path to a text file")
                isin = _stock_isin(st, query) if query else None
                return _env(ev.add_source(st, text=body, kind=kind, title=title, published_at=published_at, isin=isin, url=url))
            except (DrishtiError, OSError) as e:
                return _env(Result(error=e if isinstance(e, DrishtiError) else DrishtiError(ErrorKind.NOT_FOUND, str(e))))

    @srv.tool()
    def validate_claim(source_id: str, quote: str) -> dict:
        """Check that `quote` occurs word for word in the source (after normalising spaces, quotes and dashes). Use before citing."""
        from . import evidence as ev
        with open_store() as st:
            return _env(ev.validate_claim(st, source_id, quote))

    @srv.tool()
    def record_evidence(source_id: str, claim: str, quote: str, topic: str, polarity: int, author: str) -> dict:
        """Record one claim read from a source, with the exact quote that supports it. Refused unless the quote is verbatim in the
        source. topic: guidance, demand, margins, capex, debt, orders, pricing, competition, governance, management, risk, accounting,
        regulation or other. polarity: +1 good for the stock, -1 bad, 0 neutral. author: who read it (claude/<model> or a name)."""
        from . import evidence as ev
        with open_store() as st:
            return _env(ev.record_evidence(st, source_id=source_id, claim=claim, quote=quote, topic=topic, polarity=polarity, author=author))

    @srv.tool()
    def query_evidence(query: str | None = None, as_of: str | None = None, topic: str | None = None, source_id: str | None = None,
                       limit: int = 50) -> dict:
        """Recorded claims with their quotes and documents, newest first. as_of keeps it point-in-time (only documents public by then)."""
        from . import evidence as ev
        with open_store() as st:
            try:
                isin = _stock_isin(st, query) if query else None
                return _env(ev.query_evidence(st, isin=isin, on=as_of, topic=topic, source_id=source_id, limit=limit))
            except DrishtiError as e:
                return _env(Result(error=e))

    # --- slash commands: MCP prompts show up as /mcp__drishti__<name> in Claude Code, and in any client that lists prompts ---
    _RULES = ("Rules: use only what the evidence bundle holds; anything under 'unknown' is unknown, not neutral; do not use memory of events after "
              "the as_of date. A call needs confidence (0.50-0.99, the probability it turns out right over the horizon), a thesis in your own words, "
              "an invalidation that is an observable event (a price level, a signal reading, a dated event), and only signals that have a reading. "
              "A no-call (WAIT, SKIP, NEUTRAL) is a valid answer. Record with record_decision using agent 'claude-code' and your own model id, "
              "then give the user the call, the thesis and the invalidation in a few lines.")

    @srv.prompt(name="entry", description="Entry call on one stock: ENTER, WAIT or AVOID, logged to the decision log.")
    def p_entry(stock: str, horizon_days: int = 60) -> str:
        return (f"Judge whether to enter {stock} now. 1) Call data_health only if freshness matters. 2) Call gather_evidence with gate='entry', "
                f"query='{stock}', as_of = the latest stored session (today if a session exists, else the date data_health shows for cm_bhavcopy), "
                f"blind=false. 3) Decide ENTER, WAIT or AVOID over {horizon_days} sessions against the Nifty 50. {_RULES}")

    @srv.prompt(name="exit", description="Exit call on one holding: HOLD, TRIM or EXIT, logged to the decision log.")
    def p_exit(stock: str, horizon_days: int = 60) -> str:
        return (f"Judge whether to keep {stock}. Call gather_evidence with gate='exit', query='{stock}', as_of = the latest stored session. "
                f"Decide HOLD, TRIM or EXIT over {horizon_days} sessions against the Nifty 50. {_RULES}")

    @srv.prompt(name="macro", description="Market-wide call: RISK_ON, NEUTRAL or RISK_OFF, logged to the decision log.")
    def p_macro(horizon_days: int = 60) -> str:
        return (f"Judge the market regime. Call gather_evidence with gate='macro', as_of = the latest stored session. Decide RISK_ON, NEUTRAL or "
                f"RISK_OFF over {horizon_days} sessions (the Nifty 50 against cash). {_RULES}")

    @srv.prompt(name="health", description="What data is fresh and what failed, in plain words.")
    def p_health() -> str:
        return ("Call data_health and summarise it for me: which datasets are fresh, which are stale or failed and whether it matters on a weekend, "
                "in under 10 lines. Do not dump the raw output.")

    @srv.prompt(name="scorecard", description="How the logged calls are doing so far, by author.")
    def p_scorecard(gate: str = "") -> str:
        return ("Call score_decisions" + (f" with gate='{gate}'" if gate else "") + " and explain in plain words: per author, how many calls are scored, "
                "pending or without data, the hit rate and the mean edge after cost, and whether anyone has beaten always-long. "
                "Say clearly when there are too few scored cases to conclude anything.")

    return srv


def serve(db_path, signal_settings=None, profile_path=None) -> None:
    from .signals.base import use_settings
    use_settings(signal_settings)

    def load():
        from .profile import load_profile
        return load_profile(profile_path) if profile_path else None
    build_server(lambda: Store(db_path), load).run("stdio")

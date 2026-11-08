"""`drishti mcp`: a stdio MCP server that lets Claude read the store. Every tool returns the same envelope as the
providers ({data, as_of, sources, error}), so a stale or failed answer is visible to the model and never passes as data.

Tools read what the jobs have already stored; they do not fetch. If something is missing the error says which
`drishti run <job>` fills it. The tool functions are plain functions of a Store so they test without a server.
Profile-neutral: nothing here knows about either brother's portfolio.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
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
            kw = {"isin": m["isin"]} if name == "pledge" else {"isin": m["isin"], "symbol": m["nse_symbol"]}
        return _env(signals.compute(store, name, on, **kw))
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


SIGNAL_TOOLS = {   # tool name -> (signal name, description). v1 names kept where v1 had the tool.
    "get_macro_regime": ("macro_regime", "Macro tide, -6..+6: DXY, US 10Y, Brent, India VIX, USD/INR move, US-vs-India VIX (v1 thresholds)."),
    "get_participant_oi": ("participant_oi", "Derivatives smart money: (FII + Pro) - Client from NSE participant OI; +/-4 = strong."),
    "get_sector_rotation": ("sector_rotation", "Sector relative strength vs Nifty 50, RRG quadrant (Leading/Weakening/Improving/Lagging), fresh crosses."),
    "get_seasonality": ("seasonality", "India calendar position: quarter theme, budget and monsoon stage, results season, next RBI MPC."),
    "get_cycle_phase": ("cycle_phase", "Partial cycle-phase probabilities (Recovery/Expansion/Late/Contraction) from v1 /macro rules; lists missing inputs."),
    "get_midcap_ratio": ("midcap_ratio", "Nifty Midcap 150 / Nifty 50 trend: midcaps outperforming or lagging."),
    "get_fii_dii": ("fii_dii", "Cash-market FII/DII net flows: today, 5 and 20 sessions, streaks. Confirmation, not thesis."),
    "get_liquidity": ("liquidity", "US liquidity: Fed balance sheet 13-week change and M2 year-on-year."),
    "get_credit_spreads": ("credit_spreads", "US high-yield spread level and 20-session change, plus the 10Y-2Y curve."),
}


def build_server(open_store: Callable[[], Store]):
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

    return srv


def serve(db_path) -> None:
    build_server(lambda: Store(db_path)).run("stdio")

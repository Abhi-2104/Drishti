"""Data lookups for one company or the market, shared by the MCP server and the CLI. Store first; a live fetch only
when the stored copy is missing or older than its window (PLAN-P1 section 8), and whatever is fetched is stored.
Every answer is the usual Result envelope. Profile-neutral.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from . import resolve as resolver
from .core.canary import IST, today_ist
from .core.envelope import DrishtiError, ErrorKind, Result, guard
from .providers import bse, nse_corp, nse_quote, screener
from .store import datasets as ds


def company(store, query: str) -> dict:
    """The one security `query` names, or a typed error (not found / ambiguous)."""
    r = resolver.resolve(store, query)
    if not r.ok:
        raise r.error
    if r.data["ambiguous"]:
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"{query!r} matches several companies, pass an ISIN: "
                                                 f"{[m['name'] + ' (' + m['isin'] + ')' for m in r.data['matches']]}")
    return r.data["matches"][0]


def _now() -> datetime:
    return datetime.now(IST)


@guard
def fundamentals(store, query: str, *, refresh: bool = False, max_age_days: int = 7) -> Result:
    """Screener fundamentals + quality metrics for one company. Served from the store when fetched within `max_age_days`."""
    m = company(store, query)
    have = ds.latest_fundamentals(store, m["isin"])
    fresh = have and (_now().date() - date.fromisoformat(have["fetched_at"][:10])).days <= max_age_days
    if have and fresh and not refresh:
        data, src = have["data"], [{"name": "store", "fetched_at": have["fetched_at"]}]
    else:
        slug = m["nse_symbol"] or m["bse_scrip"]
        if not slug:
            raise DrishtiError(ErrorKind.NOT_FOUND, f"{query}: no NSE symbol or BSE code to look up on Screener")
        r = screener.company(slug)
        if not r.ok:
            if have:     # serve the older copy, loudly
                return Result(data={**have["data"], "quality": screener.quality(have["data"])}, as_of=have["latest_quarter"],
                              error=DrishtiError(ErrorKind.STALE, f"live refresh failed ({r.error.kind}: {r.error.detail[:100]}); "
                                                                  f"showing the copy fetched {have['fetched_at'][:10]}"))
            return r
        data = r.data
        if data.get("nse_symbol") and m["nse_symbol"] and data["nse_symbol"] != m["nse_symbol"]:
            raise DrishtiError(ErrorKind.PARSE_FAIL, f"screener page is for {data['nse_symbol']}, expected {m['nse_symbol']}")
        ds.persist_fundamentals(store, m["isin"], slug, data, _now().isoformat(timespec="seconds"))
        return Result(data={**data, "quality": screener.quality(data), "isin": m["isin"]}, as_of=r.as_of, sources=r.sources)
    return Result(data={**data, "quality": screener.quality(data), "isin": m["isin"]}, as_of=data.get("latest_quarter"))


@guard
def quote(store, query: str) -> Result:
    """Live/delayed quote: NSE when the company trades there, BSE otherwise. Both when both, with the gap shown."""
    m = company(store, query)
    if m["nse_symbol"]:
        r = nse_quote.quote(m["nse_symbol"], series=m.get("nse_series") or "EQ")    # SME stocks trade as SM / ST
        if not r.ok and r.error.kind == ErrorKind.NOT_FOUND and m["bse_scrip"]:
            b = nse_quote.bse_quote(m["bse_scrip"])
            if b.ok:
                b.data["note"] = f"NSE quote not found ({m['nse_symbol']} {m.get('nse_series')}); BSE shown instead"
            return b
        if r.ok and today_ist().isoformat() in ds.special_sessions(store):
            r.data["live"] = None
            r.data["live_note"] = "special session today (e.g. Muhurat trading): its hours are not known here"
        if r.ok and m["bse_scrip"]:
            b = nse_quote.bse_quote(m["bse_scrip"])
            if b.ok:
                r.data["bse_cross_check"] = {"last": b.data["last"], "as_on": b.data["as_on"],
                                             "gap_pct": round((b.data["last"] / r.data["last"] - 1) * 100, 3)}
        return r
    if m["bse_scrip"]:
        return nse_quote.bse_quote(m["bse_scrip"])
    raise DrishtiError(ErrorKind.NOT_FOUND, f"{query}: no exchange code")


@guard
def announcements(store, query: str, *, days: int = 30, live: bool = True) -> Result:
    """One company's filings from BOTH exchanges (they differ: a filing can land on one first or only), merged and
    de-duplicated by date + headline. Stored rows plus a live fetch of the window when `live`."""
    m = company(store, query)
    errors = []
    if live:
        if m["nse_symbol"]:
            r = nse_corp.announcements(days=days, symbol=m["nse_symbol"])
            if r.ok:
                ds.persist_announcements(store, r.data)
            else:
                errors.append(f"NSE {r.error.kind}")
        if m["bse_scrip"]:
            r = bse.announcements(m["bse_scrip"], days=days)
            if r.ok:
                ds.persist_announcements(store, r.data)
            else:
                errors.append(f"BSE {r.error.kind}")
    ds.ensure(store, "announcement")
    since = (today_ist() - timedelta(days=days)).isoformat()
    rows = store.query("SELECT * FROM announcement WHERE (isin=? OR (scrip_code IS NOT NULL AND scrip_code=?) OR (isin IS NULL AND symbol=?)) "
                       "AND published >= ? ORDER BY published DESC", (m["isin"], m["bse_scrip"] or "-", m["nse_symbol"] or "-", since))
    seen, out = {}, []
    for r in rows:
        k = (r["published"][:10], (r["headline"] or "")[:60].lower())
        if k in seen:
            seen[k]["also_on"] = r["source"]
            continue
        seen[k] = r
        out.append(r)
    data = {"company": m["name"], "isin": m["isin"], "days": days, "announcements": out,
            "only_on_nse": sum(1 for r in out if r["source"] == "NSE" and "also_on" not in r),
            "only_on_bse": sum(1 for r in out if r["source"] == "BSE" and "also_on" not in r)}
    err = DrishtiError(ErrorKind.NETWORK if all("NETWORK" in e for e in errors) else ErrorKind.BLOCKED, "live fetch failed: " + ", ".join(errors)) \
        if errors and len(errors) == sum(1 for x in (m["nse_symbol"], m["bse_scrip"]) if x) else None
    return Result(data=data, as_of=today_ist().isoformat(), error=err)


def _table_for(store, table: str, where: str, args: tuple, order: str) -> list[dict]:
    ds.ensure(store, table)
    return store.query(f"SELECT * FROM {table} WHERE {where} ORDER BY {order}", args)


@guard
def corporate_actions(store, query: str) -> Result:
    m = company(store, query)
    rows = _table_for(store, "corp_action", "isin=? OR symbol=?", (m["isin"], m["nse_symbol"] or "-"), "ex_date DESC")
    return Result(data={"company": m["name"], "actions": rows}, as_of=today_ist().isoformat())


@guard
def credit_ratings(store, query: str) -> Result:
    m = company(store, query)
    rows = _table_for(store, "credit_rating", "isin=? OR symbol=?", (m["isin"], m["nse_symbol"] or "-"), "rating_date DESC")
    return Result(data={"company": m["name"], "ratings": rows,
                        "note": "Ratings stored since the eod_corporate job first ran; earlier actions are not backfilled."},
                  as_of=today_ist().isoformat())


@guard
def deals(store, query: str, *, days: int = 90) -> Result:
    m = company(store, query)
    since = (today_ist() - timedelta(days=days)).isoformat()
    rows = _table_for(store, "deal", "symbol=? AND trade_date >= ?", (m["nse_symbol"] or "-", since), "trade_date DESC")
    return Result(data={"company": m["name"], "deals": rows}, as_of=today_ist().isoformat())


@guard
def market_today(store) -> Result:
    """Gift Nifty vs the last close, latest IPO lists, next expiries, next holiday: the pre-market page."""
    ds.ensure(store, "gift_nifty", "ipo", "expiry", "market_holiday")
    gift = store.query("SELECT * FROM gift_nifty ORDER BY ts DESC LIMIT 1")
    today = today_ist().isoformat()
    ipos = store.query("SELECT * FROM ipo WHERE close >= ? ORDER BY open", (today,))
    ex = store.query("SELECT symbol, MIN(expiry) AS next_expiry FROM expiry WHERE expiry >= ? GROUP BY symbol", (today,))
    hol = store.query("SELECT date, description FROM market_holiday WHERE segment='CM' AND date >= ? ORDER BY date LIMIT 3", (today,))
    if not gift:
        return Result(error=DrishtiError(ErrorKind.NOT_FOUND, "no Gift Nifty stored; run `drishti run premarket`"))
    return Result(data={"gift_nifty": gift[0], "open_or_upcoming_ipos": ipos, "next_expiries": ex, "next_holidays": hol},
                  as_of=gift[0]["ts"][:10])


@guard
def calendar(store, query: str | None = None, *, days: int = 30, back_days: int = 0) -> Result:
    """Board meetings ahead (results, dividends, fund raising), from NSE and the BSE results calendar, one row per company
    and date. With `query`, one company's meetings; without, the whole market's."""
    today = today_ist()
    isin = company(store, query)["isin"] if query else None
    rows = ds.upcoming_meetings(store, isin=isin, start=(today - timedelta(days=back_days)).isoformat(),
                                end=(today + timedelta(days=days)).isoformat())
    have = store.query("SELECT MAX(announced) AS a FROM board_meeting") if store.query(
        "SELECT 1 FROM sqlite_master WHERE name='board_meeting'") else [{"a": None}]
    if not have[0]["a"]:
        raise DrishtiError(ErrorKind.NOT_FOUND, "no board meetings stored; run `drishti run eod_corporate`")
    return Result(data={"meetings": rows, "count": len(rows), "newest_intimation": have[0]["a"],
                        "note": "BSE rows carry no intimation time; NSE's are shown when both exchanges list a meeting."},
                  as_of=today.isoformat())


@guard
def fpi_flows(store, *, days: int = 10) -> Result:
    """NSDL confirmed FPI flows, newest first: equity net (exchange and primary), debt, total, in Rs crore and US$ mn.
    These are custodian-confirmed and lag NSE's provisional FII figure by a day."""
    ds.ensure(store, "fpi_nsdl")
    dates = [r["report_date"] for r in store.query("SELECT DISTINCT report_date FROM fpi_nsdl ORDER BY report_date DESC LIMIT ?", (days,))]
    if not dates:
        raise DrishtiError(ErrorKind.NOT_FOUND, "no NSDL FPI data stored; run `drishti run premarket`")
    out = []
    for d in dates:
        rows = {(r["category"], r["route"]): r for r in store.query("SELECT * FROM fpi_nsdl WHERE report_date=?", (d,))}
        g = lambda c, rt="Sub-total": (rows.get((c, rt)) or {}).get("net")  # noqa: E731
        debt = [g(c) for c in ("Debt-General Limit", "Debt-VRR", "Debt-FAR") if g(c) is not None]
        out.append({"date": d, "equity_net_cr": g("Equity"), "equity_exchange_net_cr": g("Equity", "Stock Exchange"),
                    "equity_primary_net_cr": g("Equity", "Primary market & others"), "debt_net_cr": round(sum(debt), 2) if debt else None,
                    "total_net_cr": g("Total", "Total"), "total_net_usd_mn": (rows.get(("Total", "Total")) or {}).get("net_usd_mn")})
    eq = [x["equity_net_cr"] for x in out if x["equity_net_cr"] is not None]
    return Result(data={"days": out, "equity_net_sum_cr": round(sum(eq), 2) if eq else None, "source": "NSDL (confirmed)"},
                  as_of=dates[0])

"""Typed history tables that signals read, and the functions that fill them from parsed provider rows.

Raw files stay in raw_blob (the source of truth, re-parseable). These tables hold just the columns signals need,
indexed for fast history queries. Every write is an idempotent upsert, so re-running a day or a backfill is harmless.
"""
from __future__ import annotations

import math
import re
from datetime import date
from typing import Iterable

INDEX_OPTION_SYMBOLS = ("NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50")

DDL = {
    "price_eod": """
CREATE TABLE IF NOT EXISTS price_eod (
    trade_date TEXT NOT NULL, isin TEXT NOT NULL, exchange TEXT NOT NULL, series TEXT NOT NULL, symbol TEXT,
    open REAL, high REAL, low REAL, close REAL NOT NULL, prev_close REAL, volume INTEGER, turnover REAL, trades INTEGER,
    PRIMARY KEY (trade_date, isin, exchange, series));
CREATE INDEX IF NOT EXISTS ix_price_isin ON price_eod (isin, trade_date);
CREATE INDEX IF NOT EXISTS ix_price_sym ON price_eod (exchange, symbol, trade_date)""",
    "deal": """
CREATE TABLE IF NOT EXISTS deal (
    kind TEXT NOT NULL, trade_date TEXT NOT NULL, symbol TEXT NOT NULL, client TEXT NOT NULL, side TEXT NOT NULL, qty INTEGER NOT NULL,
    price REAL, company TEXT, source TEXT NOT NULL, PRIMARY KEY (kind, trade_date, symbol, client, side, qty));
CREATE INDEX IF NOT EXISTS ix_deal_symbol ON deal (symbol, trade_date)""",
    "announcement": """
CREATE TABLE IF NOT EXISTS announcement (
    source TEXT NOT NULL, id TEXT NOT NULL, published TEXT NOT NULL, symbol TEXT, isin TEXT, scrip_code TEXT, company TEXT,
    category TEXT, subcategory TEXT, headline TEXT, text TEXT, url TEXT, critical INTEGER, has_xbrl INTEGER,
    PRIMARY KEY (source, id));
CREATE INDEX IF NOT EXISTS ix_ann_isin ON announcement (isin, published);
CREATE INDEX IF NOT EXISTS ix_ann_scrip ON announcement (scrip_code, published)""",
    "corp_action": """
CREATE TABLE IF NOT EXISTS corp_action (
    symbol TEXT NOT NULL, ex_date TEXT NOT NULL, subject TEXT NOT NULL, isin TEXT, company TEXT, series TEXT, kind TEXT,
    record_date TEXT, bc_start TEXT, bc_end TEXT, face_value REAL, PRIMARY KEY (symbol, ex_date, subject))""",
    "credit_rating": """
CREATE TABLE IF NOT EXISTS credit_rating (
    app_id TEXT PRIMARY KEY, symbol TEXT, isin TEXT, company TEXT, agency TEXT, rating TEXT, outlook TEXT, action TEXT,
    rating_date TEXT, broadcast TEXT, previous_rating TEXT, previous_outlook TEXT, previous_date TEXT, direction_by_scale TEXT,
    listed INTEGER, xbrl_url TEXT);
CREATE INDEX IF NOT EXISTS ix_cr_isin ON credit_rating (isin, rating_date)""",
    "ipo": """
CREATE TABLE IF NOT EXISTS ipo (
    company TEXT NOT NULL, open TEXT NOT NULL, status TEXT, symbol TEXT, security_type TEXT, close TEXT, price_range TEXT,
    issue_price TEXT, issue_size TEXT, listing_date TEXT, seen_at TEXT, PRIMARY KEY (company, open))""",
    "market_holiday": """
CREATE TABLE IF NOT EXISTS market_holiday (date TEXT NOT NULL, segment TEXT NOT NULL, description TEXT, PRIMARY KEY (date, segment))""",
    "expiry": """
CREATE TABLE IF NOT EXISTS expiry (symbol TEXT NOT NULL, expiry TEXT NOT NULL, seen_on TEXT, PRIMARY KEY (symbol, expiry))""",
    "fundamentals": """
CREATE TABLE IF NOT EXISTS fundamentals (
    isin TEXT NOT NULL, latest_quarter TEXT NOT NULL, slug TEXT, basis TEXT, fetched_at TEXT NOT NULL, data TEXT NOT NULL,
    PRIMARY KEY (isin, latest_quarter))""",
    "price_adjustment": """
CREATE TABLE IF NOT EXISTS price_adjustment (
    exchange TEXT NOT NULL, symbol TEXT NOT NULL, ex_date TEXT NOT NULL, kind TEXT NOT NULL, isin TEXT, new_isin TEXT,
    factor REAL, expected_factor REAL, observed_ratio REAL, verified INTEGER NOT NULL, reason TEXT, subject TEXT,
    last_close REAL, last_date TEXT, ex_close REAL, PRIMARY KEY (exchange, symbol, ex_date, kind));
CREATE INDEX IF NOT EXISTS ix_price_adjustment_isin ON price_adjustment (isin, ex_date)""",
    "delivery": """
CREATE TABLE IF NOT EXISTS delivery (
    trade_date TEXT NOT NULL, symbol TEXT NOT NULL, series TEXT NOT NULL, isin TEXT, ttl_qty INTEGER NOT NULL, deliv_qty INTEGER,
    deliv_per REAL, trades INTEGER, PRIMARY KEY (trade_date, symbol, series));
CREATE INDEX IF NOT EXISTS ix_delivery_isin ON delivery (isin, trade_date)""",
    "isin_succession": """
CREATE TABLE IF NOT EXISTS isin_succession (
    exchange TEXT NOT NULL, symbol TEXT NOT NULL, old_isin TEXT NOT NULL, new_isin TEXT NOT NULL, change_date TEXT NOT NULL,
    source TEXT NOT NULL, PRIMARY KEY (old_isin, new_isin));
CREATE INDEX IF NOT EXISTS ix_isin_succession_new ON isin_succession (new_isin)""",
    "us_price": """
CREATE TABLE IF NOT EXISTS us_price (
    symbol TEXT NOT NULL, trade_date TEXT NOT NULL, open REAL, high REAL, low REAL, close REAL NOT NULL, adj_close REAL, volume INTEGER,
    PRIMARY KEY (symbol, trade_date))""",
    "us_security": """
CREATE TABLE IF NOT EXISTS us_security (ticker TEXT PRIMARY KEY, cik INTEGER NOT NULL, name TEXT, updated TEXT)""",
    "us_fundamentals": """
CREATE TABLE IF NOT EXISTS us_fundamentals (cik INTEGER NOT NULL, fetched_at TEXT NOT NULL, latest_period TEXT, data TEXT NOT NULL,
    PRIMARY KEY (cik, fetched_at))""",
    "board_meeting": """
CREATE TABLE IF NOT EXISTS board_meeting (
    exchange TEXT NOT NULL, code TEXT NOT NULL, meeting_date TEXT NOT NULL, purpose TEXT NOT NULL, symbol TEXT, isin TEXT,
    company TEXT, kind TEXT, description TEXT, announced TEXT, url TEXT, PRIMARY KEY (exchange, code, meeting_date, purpose));
CREATE INDEX IF NOT EXISTS ix_board_meeting_isin ON board_meeting (isin, meeting_date);
CREATE INDEX IF NOT EXISTS ix_board_meeting_date ON board_meeting (meeting_date)""",
    "fpi_nsdl": """
CREATE TABLE IF NOT EXISTS fpi_nsdl (
    report_date TEXT NOT NULL, category TEXT NOT NULL, route TEXT NOT NULL, gross_buy REAL NOT NULL, gross_sell REAL NOT NULL,
    net REAL NOT NULL, net_usd_mn REAL, usd_inr REAL, PRIMARY KEY (report_date, category, route))""",
    "fpi_nsdl_deriv": """
CREATE TABLE IF NOT EXISTS fpi_nsdl_deriv (
    report_date TEXT NOT NULL, product TEXT NOT NULL, buy_contracts INTEGER, buy_cr REAL, sell_contracts INTEGER, sell_cr REAL,
    oi_contracts INTEGER, oi_cr REAL, PRIMARY KEY (report_date, product))""",
    "gift_nifty": """
CREATE TABLE IF NOT EXISTS gift_nifty (
    ts TEXT PRIMARY KEY, last REAL NOT NULL, change REAL, pct REAL, expiry TEXT, nifty_close REAL, trade_date TEXT,
    premium_pts REAL, premium_pct REAL)""",
    "index_close": """
CREATE TABLE IF NOT EXISTS index_close (
    trade_date TEXT NOT NULL, index_name TEXT NOT NULL, open REAL, high REAL, low REAL, close REAL NOT NULL,
    change_pct REAL, pe REAL, pb REAL, div_yield REAL, PRIMARY KEY (trade_date, index_name));
CREATE INDEX IF NOT EXISTS ix_index_close_name ON index_close (index_name, trade_date)""",
    "participant_oi": """
CREATE TABLE IF NOT EXISTS participant_oi (
    trade_date TEXT NOT NULL, client_type TEXT NOT NULL,
    fut_idx_long INTEGER, fut_idx_short INTEGER, fut_stk_long INTEGER, fut_stk_short INTEGER,
    opt_idx_call_long INTEGER, opt_idx_put_long INTEGER, opt_idx_call_short INTEGER, opt_idx_put_short INTEGER,
    opt_stk_call_long INTEGER, opt_stk_put_long INTEGER, opt_stk_call_short INTEGER, opt_stk_put_short INTEGER,
    total_long INTEGER, total_short INTEGER, PRIMARY KEY (trade_date, client_type))""",
    "index_option_eod": """
CREATE TABLE IF NOT EXISTS index_option_eod (
    trade_date TEXT NOT NULL, symbol TEXT NOT NULL, expiry TEXT NOT NULL, strike REAL NOT NULL, option_type TEXT NOT NULL,
    oi INTEGER, oi_change INTEGER, volume INTEGER, close REAL, settle REAL, underlying REAL, lot_size INTEGER,
    PRIMARY KEY (trade_date, symbol, expiry, strike, option_type));
CREATE INDEX IF NOT EXISTS ix_iopt_sym ON index_option_eod (symbol, trade_date)""",
    "index_future_eod": """
CREATE TABLE IF NOT EXISTS index_future_eod (
    trade_date TEXT NOT NULL, symbol TEXT NOT NULL, expiry TEXT NOT NULL, close REAL, settle REAL, underlying REAL,
    oi INTEGER, oi_change INTEGER, volume INTEGER, PRIMARY KEY (trade_date, symbol, expiry))""",
    "fii_dii_cash": """
CREATE TABLE IF NOT EXISTS fii_dii_cash (
    trade_date TEXT PRIMARY KEY, fii_buy REAL, fii_sell REAL, fii_net REAL, dii_buy REAL, dii_sell REAL, dii_net REAL, source TEXT)""",
    "macro_series": """
CREATE TABLE IF NOT EXISTS macro_series (
    series TEXT NOT NULL, obs_date TEXT NOT NULL, value REAL NOT NULL, source TEXT NOT NULL, PRIMARY KEY (series, obs_date))""",
    "cftc_tff": """
CREATE TABLE IF NOT EXISTS cftc_tff (
    as_of TEXT NOT NULL, code TEXT NOT NULL, market TEXT, open_interest INTEGER,
    dealer_net INTEGER, asset_mgr_net INTEGER, lev_money_net INTEGER, other_rept_net INTEGER, nonrept_net INTEGER,
    PRIMARY KEY (as_of, code))""",
    "shp_filing": """
CREATE TABLE IF NOT EXISTS shp_filing (
    isin TEXT NOT NULL, as_on TEXT NOT NULL, symbol TEXT, company TEXT, record_id TEXT, broadcast TEXT, revised INTEGER,
    total_shares INTEGER, promoter_shares INTEGER, promoter_pct REAL, public_pct REAL,
    pledged_shares INTEGER, ndu_shares INTEGER, other_encumbered_shares INTEGER, encumbered_shares INTEGER,
    encumbered_pct_of_promoter REAL, pledged_pct_of_promoter REAL, encumbered_pct_of_total REAL,
    flags_consistent INTEGER, no_promoter INTEGER, xbrl_url TEXT, PRIMARY KEY (isin, as_on));
CREATE INDEX IF NOT EXISTS ix_shp_ason ON shp_filing (as_on)""",
}


def ensure(store, *names: str) -> None:
    for n in names or DDL:
        store.ensure_table(DDL[n])


def _b(v):
    return int(v) if isinstance(v, bool) else v


def persist_index_closes(store, rows: Iterable[dict]) -> int:
    ensure(store, "index_close")
    keep = ("trade_date", "index_name", "open", "high", "low", "close", "change_pct", "pe", "pb", "div_yield")
    return store.upsert("index_close", [{k: r.get(k) for k in keep} for r in rows if r.get("close") is not None], ("trade_date", "index_name"))


def persist_participant_oi(store, rows: Iterable[dict]) -> int:
    ensure(store, "participant_oi")
    return store.upsert("participant_oi", [dict(r) for r in rows], ("trade_date", "client_type"))


def persist_fo_index(store, rows: Iterable[dict]) -> int:
    """Keep only index options and futures from the F&O bhavcopy (about 5% of its rows): what PCR, max pain, OI walls and
    the futures basis need. Stock F&O is not stored here."""
    ensure(store, "index_option_eod", "index_future_eod")
    opts, futs = [], []
    for r in rows:
        if r["symbol"] not in INDEX_OPTION_SYMBOLS:
            continue
        if r["instrument_type"] == "IDO" and r["option_type"]:
            opts.append({k: r.get(k) for k in ("trade_date", "symbol", "expiry", "strike", "option_type", "oi", "oi_change", "volume",
                                               "close", "settle", "underlying", "lot_size")})
        elif r["instrument_type"] == "IDF":
            futs.append({k: r.get(k) for k in ("trade_date", "symbol", "expiry", "close", "settle", "underlying", "oi", "oi_change", "volume")})
    n = store.upsert("index_option_eod", opts, ("trade_date", "symbol", "expiry", "strike", "option_type")) if opts else 0
    return n + (store.upsert("index_future_eod", futs, ("trade_date", "symbol", "expiry")) if futs else 0)


def persist_fii_dii(store, data: dict, source: str = "nse_fiidii") -> int:
    ensure(store, "fii_dii_cash")
    f, d = data["fii"], data["dii"]
    return store.upsert("fii_dii_cash", [{"trade_date": data["date"], "fii_buy": f["buy"], "fii_sell": f["sell"], "fii_net": f["net"],
                                          "dii_buy": d["buy"], "dii_sell": d["sell"], "dii_net": d["net"], "source": source}], ("trade_date",))


def persist_macro(store, series: str, rows: Iterable[dict], source: str) -> int:
    ensure(store, "macro_series")
    return store.upsert("macro_series", [{"series": series, "obs_date": r["date"], "value": r["value"], "source": source} for r in rows],
                        ("series", "obs_date"))


def derive_yoy(store, index_series: str, yoy_series: str, source: str = "derived") -> int:
    """Year-on-year % for every month of an index series that also has the same month a year earlier (MoSPI publishes no WPI
    inflation figure). Months without a year-earlier index get nothing, never a guess."""
    ensure(store, "macro_series")
    by = {r["obs_date"][:7]: r for r in store.query("SELECT obs_date, value FROM macro_series WHERE series=?", (index_series,))}
    out = []
    for ym, r in by.items():
        prev = by.get(f"{int(ym[:4]) - 1}-{ym[5:7]}")
        if prev and prev["value"]:
            out.append({"date": r["obs_date"], "value": round((r["value"] / prev["value"] - 1) * 100, 2)})
    return persist_macro(store, yoy_series, out, source) if out else 0


def persist_macro_bundle(store, data: dict, source: str = "mospi") -> int:
    """A provider result carrying `series: {name: [{date, value}]}` (MoSPI CPI / WPI / IIP / reserves): one macro_series write each.
    A WPI index also refreshes the derived WPI year-on-year."""
    n = sum(persist_macro(store, name, pts, source) for name, pts in (data.get("series") or {}).items() if pts)
    if (data.get("series") or {}).get("mospi:wpi_index"):
        derive_yoy(store, "mospi:wpi_index", "mospi:wpi_yoy", "mospi-derived")
    return n


def prune_incomplete(store, series: str, today: str) -> None:
    """Remove bars of a market series dated today or on a weekend (an unfinished or weekend quote, never a session close).
    Weekend special sessions (Muhurat trading) are kept."""
    ensure(store, "macro_series")
    keep = sorted(special_sessions(store)) or ["-"]
    store._run(f"DELETE FROM macro_series WHERE series=? AND (obs_date >= ? OR (strftime('%w', obs_date) IN ('0', '6') "
               f"AND obs_date NOT IN ({','.join('?' * len(keep))})))", (series, today, *keep))


def persist_cftc(store, rows: Iterable[dict]) -> int:
    ensure(store, "cftc_tff")
    return store.upsert("cftc_tff", [{
        "as_of": r["as_of"], "code": r["code"], "market": r["market"], "open_interest": r["open_interest"],
        "dealer_net": r["dealer"]["net"], "asset_mgr_net": r["asset_mgr"]["net"], "lev_money_net": r["lev_money"]["net"],
        "other_rept_net": r["other_rept"]["net"], "nonrept_net": r["nonreportable"]["net"]} for r in rows], ("as_of", "code"))


SHP_COLS = ("symbol", "company", "total_shares", "promoter_shares", "promoter_pct", "public_pct", "pledged_shares", "ndu_shares",
            "other_encumbered_shares", "encumbered_shares", "encumbered_pct_of_promoter", "pledged_pct_of_promoter",
            "encumbered_pct_of_total", "flags_consistent", "no_promoter")


def persist_shp_filing(store, f: dict, master_row: dict) -> int:
    """One parsed filing, keyed by (ISIN, as-on date); a later revision of the same quarter replaces the earlier one."""
    ensure(store, "shp_filing")
    isin = f.get("isin") if f.get("isin_valid") else master_row.get("isin")
    if not isin:
        return 0
    row = {"isin": isin, "as_on": f["as_on"], "record_id": master_row.get("record_id"), "broadcast": master_row.get("broadcast"),
           "revised": _b(master_row.get("revised")), "xbrl_url": master_row.get("xbrl_url"), **{c: _b(f.get(c)) for c in SHP_COLS}}
    return store.upsert("shp_filing", [row], ("isin", "as_on"))


def persist_prices(store, rows, exchange: str) -> int:
    """Daily bars per ISIN. BSE rows are kept only for ISINs NSE did not trade that day (NSE is primary for dual listings,
    BSE covers the BSE-only names), so one stock has one primary price per day."""
    ensure(store, "price_eod")
    rows = [r for r in rows if r.get("isin") and r.get("close") is not None]
    if exchange == "BSE" and rows:
        have = {x["isin"] for x in store.query("SELECT isin FROM price_eod WHERE trade_date=? AND exchange='NSE'", (rows[0]["trade_date"],))}
        rows = [r for r in rows if r["isin"] not in have]
    keep = ("trade_date", "isin", "series", "symbol", "open", "high", "low", "close", "prev_close", "volume", "turnover", "trades")
    out = [{**{k: r.get(k) for k in keep}, "exchange": exchange, "series": r.get("series") or "-"} for r in rows]
    return store.upsert("price_eod", out, ("trade_date", "isin", "exchange", "series")) if out else 0


def persist_delivery(store, rows) -> int:
    """NSE delivery rows, each tagged with its ISIN from the same session's stored prices (symbol + series), falling back to the
    security master. A row that cannot be resolved is stored with ISIN NULL (and counted by `unresolved_delivery`), never dropped."""
    ensure(store, "delivery", "price_eod")
    if not rows:
        return 0
    day = rows[0]["trade_date"]
    by_ss = {(r["symbol"], r["series"]): r["isin"] for r in store.query("SELECT symbol, series, isin FROM price_eod WHERE exchange='NSE' AND trade_date=?", (day,))}
    by_sym = {}
    for r in store.query("SELECT isin, nse_symbol FROM security WHERE nse_symbol IS NOT NULL"):
        by_sym.setdefault(r["nse_symbol"], []).append(r["isin"])
    out = []
    for r in rows:
        isin = by_ss.get((r["symbol"], r["series"]))
        if isin is None and len(by_sym.get(r["symbol"], [])) == 1:
            isin = by_sym[r["symbol"]][0]
        out.append({**{k: r[k] for k in ("trade_date", "symbol", "series", "ttl_qty", "deliv_qty", "deliv_per", "trades")}, "isin": isin})
    return store.upsert("delivery", out, ("trade_date", "symbol", "series"))


def unresolved_delivery(store, day: str) -> int:
    ensure(store, "delivery")
    return store.query("SELECT COUNT(*) AS n FROM delivery WHERE trade_date=? AND isin IS NULL AND deliv_per IS NOT NULL", (day,))[0]["n"]


def persist_deals(store, rows, source: str) -> int:
    ensure(store, "deal")
    out = [{"kind": r["kind"], "trade_date": r["date"], "symbol": r["symbol"], "client": r.get("client") or "-", "side": r.get("side") or "-",
            "qty": r["qty"] or 0, "price": r.get("price"), "company": r.get("company"), "source": source} for r in rows if r.get("symbol")]
    return store.upsert("deal", out, ("kind", "trade_date", "symbol", "client", "side", "qty")) if out else 0


def persist_announcements(store, rows) -> int:
    ensure(store, "announcement")
    cols = ("source", "id", "published", "symbol", "isin", "scrip_code", "company", "category", "subcategory", "headline", "text", "url",
            "critical", "has_xbrl")
    return store.upsert("announcement", [{c: _b(r.get(c)) for c in cols} for r in rows], ("source", "id")) if rows else 0


def persist_corp_actions(store, rows) -> int:
    ensure(store, "corp_action")
    cols = ("symbol", "ex_date", "subject", "isin", "company", "series", "kind", "record_date", "bc_start", "bc_end", "face_value")
    rows = [r for r in rows if r.get("ex_date")]
    n = store.upsert("corp_action", [{c: r.get(c) for c in cols} for r in rows], ("symbol", "ex_date", "subject")) if rows else 0
    for r in rows:              # NSE's wording wins over a BSE copy of the same action stored earlier
        if r.get("isin"):
            store.delete_where("corp_action", "symbol LIKE 'BSE:%' AND isin=? AND ex_date=?", (r["isin"], r["ex_date"]))
    return n


def persist_credit_ratings(store, rows) -> int:
    ensure(store, "credit_rating")
    cols = ("app_id", "symbol", "isin", "company", "agency", "rating", "outlook", "action", "rating_date", "broadcast", "previous_rating",
            "previous_outlook", "previous_date", "direction_by_scale", "listed", "xbrl_url")
    return store.upsert("credit_rating", [{c: _b(r.get(c)) for c in cols} for r in rows], ("app_id",)) if rows else 0


def persist_ipos(store, data: dict, seen_at: str) -> int:
    ensure(store, "ipo")
    rows = [r for part in ("current", "upcoming", "recent") for r in data.get(part, []) if r.get("company") and r.get("open")]
    cols = ("company", "open", "status", "symbol", "security_type", "close", "price_range", "issue_price", "issue_size", "listing_date")
    return store.upsert("ipo", [{**{c: r.get(c) for c in cols}, "seen_at": seen_at} for r in rows], ("company", "open")) if rows else 0


def persist_holidays(store, rows) -> int:
    ensure(store, "market_holiday")
    return store.upsert("market_holiday", [{"date": r["date"], "segment": r["segment"], "description": r["description"]} for r in rows],
                        ("date", "segment")) if rows else 0


def persist_expiries(store, symbol: str, expiries, seen_on: str) -> int:
    ensure(store, "expiry")
    return store.upsert("expiry", [{"symbol": symbol, "expiry": e, "seen_on": seen_on} for e in expiries], ("symbol", "expiry"))


def persist_gift(store, d: dict) -> int:
    ensure(store, "gift_nifty")
    g = d["gift_nifty"]
    return store.upsert("gift_nifty", [{"ts": g["timestamp"] or d["trade_date"], "last": g["last"], "change": g["change"], "pct": g["pct"],
                                        "expiry": g["expiry"], "nifty_close": d.get("nifty_close"), "trade_date": d["trade_date"],
                                        "premium_pts": d.get("gift_premium_pts"), "premium_pct": d.get("gift_premium_pct")}], ("ts",))


def special_sessions(store, segment: str = "CM") -> set[str]:
    """Dates that trade although they look closed: NSE marks a holiday that has a special session (Diwali Muhurat
    trading) with a trailing '*' in its holiday list (8 Nov 2026, a Sunday: 'Diwali Laxmi Pujan*'). A weekend date we
    already hold NSE prices for counts too."""
    out: set[str] = set()
    if store.query("SELECT 1 AS x FROM sqlite_master WHERE name='market_holiday'"):
        out |= {r["date"] for r in store.query("SELECT date FROM market_holiday WHERE segment=? AND description LIKE '%*'", (segment,))}
    if store.query("SELECT 1 AS x FROM sqlite_master WHERE name='price_eod'"):
        out |= {r["d"] for r in store.query("SELECT DISTINCT trade_date AS d FROM price_eod WHERE exchange='NSE' "
                                             "AND strftime('%w', trade_date) IN ('0', '6')")}
    return out


def is_holiday(store, day: str, segment: str = "CM") -> bool | None:
    """True/False from NSE's own holiday list; None if the list for that year is not stored. A special-session day
    (see special_sessions) is not a holiday: an end-of-day file is published for it."""
    if not store.query("SELECT 1 AS x FROM sqlite_master WHERE name='market_holiday'"):
        return None
    if not store.query("SELECT 1 AS x FROM market_holiday WHERE segment=? AND substr(date,1,4)=?", (segment, day[:4])):
        return None
    if day in special_sessions(store, segment):
        return False
    return bool(store.query("SELECT 1 AS x FROM market_holiday WHERE segment=? AND date=?", (segment, day)))


def is_session(store, day: str) -> bool:
    """Should NSE publish end-of-day files for `day`? Weekdays that are not holidays, plus special sessions."""
    if day in special_sessions(store):
        return True
    return date.fromisoformat(day).weekday() < 5 and not is_holiday(store, day)


def persist_fundamentals(store, isin: str, slug: str, data: dict, fetched_at: str) -> int:
    """One row per (ISIN, newest quarter reported): earlier quarters' snapshots are kept, so what was known when stays
    answerable (point-in-time). A re-fetch within the same quarter replaces that quarter's snapshot."""
    import json
    ensure(store, "fundamentals")
    return store.upsert("fundamentals", [{"isin": isin, "latest_quarter": data.get("latest_quarter") or fetched_at[:10], "slug": slug,
                                          "basis": data.get("basis"), "fetched_at": fetched_at, "data": json.dumps(data, default=str)}],
                        ("isin", "latest_quarter"))


def latest_fundamentals(store, isin: str, on: str | None = None) -> dict | None:
    import json
    if not store.query("SELECT 1 AS x FROM sqlite_master WHERE name='fundamentals'"):
        return None
    rows = store.query("SELECT * FROM fundamentals WHERE isin=? AND substr(fetched_at,1,10) <= ? ORDER BY latest_quarter DESC LIMIT 1",
                       (isin, on or "9999-12-31"))
    if not rows:
        return None
    r = rows[0]
    return {**{k: r[k] for k in ("isin", "latest_quarter", "slug", "basis", "fetched_at")}, "data": json.loads(r["data"])}


def _isin_for_scrip(store, code: str) -> str | None:
    m = store.find_securities(bse_scrip=code)
    return m[0]["isin"] if len(m) == 1 else None


def persist_board_meetings(store, rows) -> int:
    """NSE board meetings and the BSE results calendar in one table. BSE rows get their ISIN from the security master,
    so one company's meeting can be found from either exchange's listing."""
    ensure(store, "board_meeting")
    cols = ("exchange", "code", "meeting_date", "purpose", "symbol", "isin", "company", "kind", "description", "announced", "url")
    out = []
    for r in rows:
        r = dict(r)
        if r["exchange"] == "BSE" and not r.get("isin"):
            r["isin"] = _isin_for_scrip(store, r["code"])
        out.append({c: r.get(c) for c in cols})
    return store.upsert("board_meeting", out, ("exchange", "code", "meeting_date", "purpose")) if out else 0


def upcoming_meetings(store, *, isin: str | None = None, start: str, end: str) -> list[dict]:
    """Meetings in [start, end], one row per company and date: the purposes and kinds from every row and both exchanges
    are merged, and NSE's details (intimation time, attachment) are kept when NSE lists it."""
    ensure(store, "board_meeting")
    where, args = "meeting_date BETWEEN ? AND ?", [start, end]
    if isin:
        where += " AND isin = ?"
        args.append(isin)
    rows = store.query(f"SELECT * FROM board_meeting WHERE {where}", tuple(args))
    merged: dict = {}
    for r in sorted(rows, key=lambda r: (r["meeting_date"], r["exchange"] != "NSE", r["announced"] or "")):
        key = (r["isin"] or f"{r['exchange']}:{r['code']}", r["meeting_date"])
        m = merged.get(key)
        if m is None:
            merged[key] = {**r, "purposes": [r["purpose"]], "kinds": [r["kind"]], "exchanges": [r["exchange"]]}
            continue
        for k, v in (("purposes", r["purpose"]), ("kinds", r["kind"]), ("exchanges", r["exchange"])):
            if v not in m[k]:
                m[k].append(v)
        if r["exchange"] == "NSE" and r["description"] and r["kind"] == "results":
            m["description"] = r["description"]
    out = sorted(merged.values(), key=lambda m: (m["meeting_date"], m["symbol"] or ""))
    for m in out:
        m["kind"] = "results" if "results" in m["kinds"] else m["kinds"][0]
        del m["purpose"]
    return out


def persist_bse_corp_actions(store, rows) -> int:
    """BSE corporate actions go into corp_action only for companies NSE did not list an action for on that ex-date
    (dual-listed companies would otherwise appear twice under differently worded subjects)."""
    ensure(store, "corp_action")
    out = []
    for r in rows:
        if not r.get("ex_date"):
            continue
        isin = _isin_for_scrip(store, r["scrip_code"])
        if isin and store.query("SELECT 1 FROM corp_action WHERE isin=? AND ex_date=? AND symbol NOT LIKE 'BSE:%' LIMIT 1", (isin, r["ex_date"])):
            continue
        out.append({"symbol": f"BSE:{r['scrip_code']}", "ex_date": r["ex_date"], "subject": r["subject"], "isin": isin,
                    "company": r.get("company"), "series": "BSE", "kind": r["kind"], "record_date": r.get("record_date"),
                    "bc_start": r.get("bc_start"), "bc_end": r.get("bc_end"), "face_value": None})
    return store.upsert("corp_action", out, ("symbol", "ex_date", "subject")) if out else 0


def persist_fpi(store, data: dict) -> int:
    ensure(store, "fpi_nsdl", "fpi_nsdl_deriv")
    rows = [{"report_date": data["date"], **{k: r[k] for k in ("category", "route", "gross_buy", "gross_sell", "net", "net_usd_mn")},
             "usd_inr": data.get("usd_inr")} for r in data["rows"]]
    rows.append({"report_date": data["date"], "category": "Total", "route": "Total", **data["total"], "usd_inr": data.get("usd_inr")})
    n = store.upsert("fpi_nsdl", rows, ("report_date", "category", "route"))
    d_day = data.get("derivatives_date") or data["date"]
    der = [{"report_date": d_day, **r} for r in data.get("derivatives") or []]
    return n + (store.upsert("fpi_nsdl_deriv", der, ("report_date", "product")) if der else 0)


# --- split / bonus adjustment + ISIN succession ----------------------------------------------------------------
# The exchanges do NOT adjust the bhavcopy's previous close on the ex-date (NSE: CONCORDBIO bonus 1:1 on 2026-11-04 shows
# prev_close 1227.9 and close 655.5), and a face-value split gives the stock a NEW ISIN from the ex-date (KMEW
# INE0CJD01029 -> INE0CJD01037). So the factor comes from the corporate action's own terms and is only applied after the
# stored prices confirm the step. Rights issues are not adjusted (their price effect needs the issue price and is small).
ADJ_TOL = 0.30            # observed close ratio may differ from the expected factor by this much (daily limits + the day's own move)
ADJ_MAX_GAP_DAYS = 5      # the ex-date session must be stored within this many days of the ex-date
SUCC_MAX_GAP_DAYS = 7     # an ISIN change on the same symbol counts as a succession only if the sessions are this close

_FROM_TO = re.compile(r"from\s+(?:rs|re)\.?\s*(\d+(?:\.\d+)?).{0,40}?\bto\s+(?:rs|re)\.?\s*(\d+(?:\.\d+)?)", re.I | re.S)
_BONUS = re.compile(r"bonus\D{0,30}?(\d+)\s*:\s*(\d+)", re.I)


def expected_factor(kind: str, subject: str) -> float | None:
    """The price factor a split / bonus implies, from the action's wording; None when the wording cannot be read."""
    s = " ".join((subject or "").split())
    ft, bo = _FROM_TO.search(s), _BONUS.search(s)
    if kind == "split" and ft and not bo:
        old, new = float(ft.group(1)), float(ft.group(2))
        return round(new / old, 8) if old > 0 and new > 0 else None
    if kind == "bonus" and bo and not ft:
        new, held = int(bo.group(1)), int(bo.group(2))
        return round(held / (new + held), 8) if new > 0 and held > 0 else None
    return None


def _migrate_adjustment_table(store) -> None:
    """The first price_adjustment layout (prev_close mismatch detector) is derived data with the wrong premise: drop it."""
    cols = {r["name"] for r in store.query("PRAGMA table_info(price_adjustment)")}
    if cols and "symbol" not in cols:
        store.drop_table("price_adjustment")
        ensure(store, "price_adjustment")


def _session(store, exchange: str, symbol: str, isin: str | None, *, on_or_after: str | None = None, before: str | None = None):
    """The busiest stored row of the nearest session on/after a date or before it, for one stock. NSE rows are found by symbol
    (the symbol survives the ISIN change); BSE actions carry only an ISIN."""
    who, args = ("symbol = ?", [symbol]) if exchange == "NSE" else ("isin = ?", [isin])
    if on_or_after:
        where, order, args = f"{who} AND trade_date >= ?", "trade_date ASC", args + [on_or_after]
    else:
        where, order, args = f"{who} AND trade_date < ?", "trade_date DESC", args + [before]
    r = store.query(f"SELECT trade_date, isin, series, close FROM price_eod WHERE exchange = ? AND {where} ORDER BY {order}, COALESCE(volume, 0) DESC LIMIT 1",
                    tuple([exchange] + args))
    return r[0] if r else None


def _days(a: str, b: str) -> int:
    return abs((date.fromisoformat(a) - date.fromisoformat(b)).days)


def rebuild_adjustments(store, *, since: str | None = None) -> dict:
    """Split / bonus adjustment events and ISIN successions from the stored corporate actions and prices.

    Each stored split or bonus whose ex-date session is already in the price table becomes one event:
      expected factor from the action's wording (split: new face value / old; bonus a:b -> b / (a + b)),
      observed ratio = close(ex-date session) / close(previous stored session) for the same stock (found by symbol, so an ISIN
      change does not break it). The event is `verified` (and applied to earlier prices) only when the observed ratio is within
      ADJ_TOL of the expected one and closer to it than to 1.0; otherwise it is stored with the reason and NOT applied.
    Returns {events (verified), unverified, unparsed, successions, checked, since}."""
    ensure(store, "price_eod", "price_adjustment", "isin_succession", "corp_action")
    _migrate_adjustment_table(store)
    lo = since or "0000-00-00"
    acts = store.query("SELECT symbol, ex_date, subject, isin, kind FROM corp_action WHERE kind IN ('split', 'bonus') AND ex_date >= ? "
                       "ORDER BY ex_date, symbol", (lo,))
    out, unparsed, successions = [], [], []
    for a in acts:
        bse = a["symbol"].startswith("BSE:")
        exchange = "BSE" if bse else "NSE"
        e = expected_factor(a["kind"], a["subject"])
        base = {"exchange": exchange, "symbol": a["symbol"], "ex_date": a["ex_date"], "kind": a["kind"], "isin": a["isin"], "new_isin": None,
                "factor": None, "expected_factor": e, "observed_ratio": None, "verified": 0, "reason": None, "subject": a["subject"],
                "last_close": None, "last_date": None, "ex_close": None}
        if e is None:
            unparsed.append({"symbol": a["symbol"], "ex_date": a["ex_date"], "subject": a["subject"]})
            out.append({**base, "reason": "terms not readable from the action wording"})
            continue
        if bse and not a["isin"]:
            out.append({**base, "reason": "BSE action without an ISIN mapping"})
            continue
        ex = _session(store, exchange, a["symbol"], a["isin"], on_or_after=a["ex_date"])
        if ex is None:
            continue                                   # an upcoming action: no session yet, nothing to record
        prev = _session(store, exchange, a["symbol"], a["isin"], before=ex["trade_date"])
        if _days(ex["trade_date"], a["ex_date"]) > ADJ_MAX_GAP_DAYS:
            continue                                   # the stored session is far from the ex-date: another event's data
        if prev is None or not prev["close"] or not ex["close"]:
            out.append({**base, "reason": "no stored session before the ex-date (history starts later)"})
            continue
        obs = ex["close"] / prev["close"]
        d_exp, d_one = abs(math.log(obs / e)), abs(math.log(obs))
        verified = d_exp <= math.log(1 + ADJ_TOL) and d_exp < d_one
        why = None if verified else (f"price step {obs:.3f} is not close to the expected {e:g}" if d_exp > math.log(1 + ADJ_TOL)
                                     else f"price step {obs:.3f} is closer to no change than to the expected {e:g}")
        row = {**base, "isin": prev["isin"], "new_isin": ex["isin"] if ex["isin"] != prev["isin"] else None, "observed_ratio": round(obs, 6),
               "verified": int(verified), "reason": why, "factor": e if verified else None, "last_close": prev["close"],
               "last_date": prev["trade_date"], "ex_close": ex["close"]}
        out.append(row)
        if row["new_isin"]:
            successions.append({"exchange": exchange, "symbol": a["symbol"], "old_isin": row["isin"], "new_isin": row["new_isin"],
                                "change_date": ex["trade_date"], "source": "action"})
    _verify_combined(out)
    successions += _price_successions(store, lo)
    if since is None:
        store.delete_where("price_adjustment", "1=1", ())
    else:
        store.delete_where("price_adjustment", "ex_date >= ?", (since,))
    n = store.upsert("price_adjustment", out, ("exchange", "symbol", "ex_date", "kind")) if out else 0
    uniq = {(s["old_isin"], s["new_isin"]): s for s in successions}            # an action-sourced row wins over a price-sourced one
    for s in successions:
        if s["source"] == "action":
            uniq[(s["old_isin"], s["new_isin"])] = s
    if uniq:
        store.upsert("isin_succession", list(uniq.values()), ("old_isin", "new_isin"))
    ver = [x for x in out if x["verified"]]
    return {"events": len(ver), "unverified": [{k: x[k] for k in ("symbol", "ex_date", "kind", "reason")} for x in out if not x["verified"] and x["reason"]],
            "unparsed": unparsed, "successions": len(uniq), "checked": n, "since": since}


def _verify_combined(rows: list[dict]) -> None:
    """A split and a bonus can take effect on the same ex-date (a company announces both); the price then steps by the PRODUCT of the
    two factors and neither action matches alone. When no action of such a pair is confirmed alone and the product matches the step,
    every action of the pair is verified (each keeps its own factor, so applying them all applies the product). Same-kind pairs are
    left alone: they collapse into one row and a duplicate listing would double-count."""
    groups: dict[tuple, list[dict]] = {}
    for r in rows:
        if r["expected_factor"] and r["observed_ratio"]:
            groups.setdefault((r["exchange"], r["symbol"], r["ex_date"]), []).append(r)
    for g in groups.values():
        if len(g) < 2 or len({r["kind"] for r in g}) != len(g) or any(r["verified"] for r in g):
            continue
        obs, prod = g[0]["observed_ratio"], math.prod(r["expected_factor"] for r in g)
        d_exp, d_one = abs(math.log(obs / prod)), abs(math.log(obs))
        if d_exp <= math.log(1 + ADJ_TOL) and d_exp < d_one:
            for r in g:
                r.update(verified=1, factor=r["expected_factor"],
                         reason=f"verified with {len(g) - 1} other action(s) on the same ex-date: combined step {obs:.3f} matches {prod:g}")


def _price_successions(store, lo: str) -> list[dict]:
    """ISIN changes seen in the price table itself: the same symbol, consecutive sessions, the old ISIN never trades again
    and the new one never traded before. Catches ISIN changes with no stored action (older history, other corporate events)."""
    cand = store.query("""
        WITH d AS (SELECT DISTINCT exchange, symbol, isin, trade_date FROM price_eod WHERE symbol IS NOT NULL AND trade_date >= ?),
             s AS (SELECT exchange, symbol, isin, trade_date, LAG(isin) OVER w AS prev_isin, LAG(trade_date) OVER w AS prev_date
                   FROM d WINDOW w AS (PARTITION BY exchange, symbol ORDER BY trade_date, isin))
        SELECT exchange, symbol, prev_isin, isin, trade_date, prev_date FROM s WHERE prev_isin IS NOT NULL AND prev_isin <> isin""", (lo,))
    out = []
    for c in cand:
        if _days(c["trade_date"], c["prev_date"]) > SUCC_MAX_GAP_DAYS:
            continue
        old_last = store.query("SELECT MAX(trade_date) d FROM price_eod WHERE exchange=? AND symbol=? AND isin=?", (c["exchange"], c["symbol"], c["prev_isin"]))[0]["d"]
        new_first = store.query("SELECT MIN(trade_date) d FROM price_eod WHERE exchange=? AND symbol=? AND isin=?", (c["exchange"], c["symbol"], c["isin"]))[0]["d"]
        if old_last is not None and new_first is not None and old_last < new_first:
            out.append({"exchange": c["exchange"], "symbol": c["symbol"], "old_isin": c["prev_isin"], "new_isin": c["isin"],
                        "change_date": new_first, "source": "prices"})
    return out


def isin_chain(store, isin: str) -> list[str]:
    """Every ISIN this security has traded under (oldest first is not guaranteed): follows isin_succession both ways."""
    ensure(store, "isin_succession")
    seen, todo = {isin}, [isin]
    while todo:
        cur = todo.pop()
        for r in store.query("SELECT old_isin, new_isin FROM isin_succession WHERE old_isin = ? OR new_isin = ?", (cur, cur)):
            for x in (r["old_isin"], r["new_isin"]):
                if x not in seen:
                    seen.add(x)
                    todo.append(x)
    return sorted(seen)


def adjustments_for(store, isin: str) -> list[dict]:
    """Verified split / bonus events for a security, including events recorded under its earlier or later ISINs."""
    ensure(store, "price_adjustment")
    chain = isin_chain(store, isin)
    marks = ",".join("?" * len(chain))
    return store.query(f"SELECT * FROM price_adjustment WHERE verified = 1 AND (isin IN ({marks}) OR new_isin IN ({marks})) ORDER BY ex_date",
                       tuple(chain) * 2)


# --- US --------------------------------------------------------------------------------------------------------------
def persist_us_prices(store, symbol: str, bars) -> int:
    ensure(store, "us_price")
    rows = [{"symbol": symbol, "trade_date": b["date"], "open": b.get("open"), "high": b.get("high"), "low": b.get("low"),
             "close": b["close"], "adj_close": b.get("adj_close"), "volume": b.get("volume")} for b in bars]
    return store.upsert("us_price", rows, ("symbol", "trade_date")) if rows else 0


def persist_us_tickers(store, rows, updated: str) -> int:
    ensure(store, "us_security")
    return store.upsert("us_security", [{**r, "updated": updated} for r in rows], ("ticker",)) if rows else 0


def us_cik(store, ticker: str) -> int | None:
    ensure(store, "us_security")
    r = store.query("SELECT cik FROM us_security WHERE ticker=?", (ticker.strip().upper(),))
    return r[0]["cik"] if r else None


def tracked_us_symbols(store) -> list[str]:
    """Every US ticker ever fetched keeps being updated by the us_daily job: no list to maintain."""
    ensure(store, "us_price")
    return [r["symbol"] for r in store.query("SELECT DISTINCT symbol FROM us_price ORDER BY symbol")]

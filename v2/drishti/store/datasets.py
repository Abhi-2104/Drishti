"""Typed history tables that signals read, and the functions that fill them from parsed provider rows.

Raw files stay in raw_blob (the source of truth, re-parseable). These tables hold just the columns signals need,
indexed for fast history queries. Every write is an idempotent upsert, so re-running a day or a backfill is harmless.
"""
from __future__ import annotations

from datetime import date
from typing import Iterable

INDEX_OPTION_SYMBOLS = ("NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50")

DDL = {
    "price_eod": """
CREATE TABLE IF NOT EXISTS price_eod (
    trade_date TEXT NOT NULL, isin TEXT NOT NULL, exchange TEXT NOT NULL, series TEXT NOT NULL, symbol TEXT,
    open REAL, high REAL, low REAL, close REAL NOT NULL, prev_close REAL, volume INTEGER, turnover REAL, trades INTEGER,
    PRIMARY KEY (trade_date, isin, exchange, series));
CREATE INDEX IF NOT EXISTS ix_price_isin ON price_eod (isin, trade_date)""",
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
    isin TEXT NOT NULL, exchange TEXT NOT NULL, series TEXT NOT NULL, ex_date TEXT NOT NULL, factor REAL NOT NULL,
    prev_close REAL, last_close REAL, last_date TEXT, kind TEXT, subject TEXT, PRIMARY KEY (isin, exchange, series, ex_date));
CREATE INDEX IF NOT EXISTS ix_price_adjustment_isin ON price_adjustment (isin, ex_date)""",
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


# --- split / bonus / rights adjustment -------------------------------------------------------------------------
ADJ_MIN_MOVE = 0.005        # prev_close must differ from the stored last close by more than 0.5% to be an adjustment
ADJ_GAP_SHARE = 0.20        # a day where more than this share of stocks 'adjust' is a missing day in the store, not actions


def rebuild_adjustments(store, *, since: str | None = None) -> dict:
    """Find split / bonus / rights adjustments from the exchange's own previous close.

    On an ex-date the bhavcopy's previous close is the adjusted one (a 1:10 split shows 1/10 of the last close), so
    factor = prev_close(ex-date) / close(previous stored session) for the same ISIN, exchange and series. Dividends are
    not adjusted by the exchange, so they never show up here. A day where a large share of all stocks show a factor is
    a session missing from the store (both closes then refer to different days): such days are skipped and returned.
    Each event is labelled from corp_action when an action of that kind is stored for the ISIN and ex-date."""
    ensure(store, "price_eod", "price_adjustment", "corp_action")
    lo = since or "0000-00-00"
    rows = store.query(f"""
        WITH s AS (SELECT isin, exchange, series, trade_date, close, prev_close,
                          LAG(close) OVER w AS last_close, LAG(trade_date) OVER w AS last_date
                   FROM price_eod WINDOW w AS (PARTITION BY isin, exchange, series ORDER BY trade_date))
        SELECT * FROM s WHERE trade_date >= ? AND last_close IS NOT NULL AND last_close > 0 AND prev_close IS NOT NULL
               AND prev_close > 0""", (lo,))
    per_day: dict[tuple[str, str], list] = {}
    for r in rows:
        per_day.setdefault((r["trade_date"], r["exchange"]), []).append(r)
    events, gap_days = [], []
    for (day, exch), rs in sorted(per_day.items()):
        moved = [r for r in rs if abs(r["prev_close"] / r["last_close"] - 1) > ADJ_MIN_MOVE]
        if len(rs) >= 50 and len(moved) > ADJ_GAP_SHARE * len(rs):
            gap_days.append({"date": day, "exchange": exch, "stocks": len(rs), "moved": len(moved)})
            continue
        events += moved
    out = []
    for r in events:
        act = store.query("SELECT kind, subject FROM corp_action WHERE isin=? AND ex_date=? AND kind IN ('split', 'bonus', 'rights') LIMIT 1",
                          (r["isin"], r["trade_date"]))
        out.append({"isin": r["isin"], "exchange": r["exchange"], "series": r["series"], "ex_date": r["trade_date"],
                    "factor": round(r["prev_close"] / r["last_close"], 6), "prev_close": r["prev_close"], "last_close": r["last_close"],
                    "last_date": r["last_date"], "kind": act[0]["kind"] if act else None, "subject": act[0]["subject"] if act else None})
    if since is None:
        store.delete_where("price_adjustment", "1=1", ())
    else:
        store.delete_where("price_adjustment", "ex_date >= ?", (since,))
    n = store.upsert("price_adjustment", out, ("isin", "exchange", "series", "ex_date")) if out else 0
    return {"events": n, "labelled": sum(1 for x in out if x["kind"]), "gap_days": gap_days, "since": since}


def adjustments_for(store, isin: str) -> list[dict]:
    ensure(store, "price_adjustment")
    return store.query("SELECT * FROM price_adjustment WHERE isin=? ORDER BY ex_date", (isin,))


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

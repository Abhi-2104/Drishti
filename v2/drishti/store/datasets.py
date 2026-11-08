"""Typed history tables that signals read, and the functions that fill them from parsed provider rows.

Raw files stay in raw_blob (the source of truth, re-parseable). These tables hold just the columns signals need,
indexed for fast history queries. Every write is an idempotent upsert, so re-running a day or a backfill is harmless.
"""
from __future__ import annotations

from typing import Iterable

INDEX_OPTION_SYMBOLS = ("NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50")

DDL = {
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
    """Remove bars of a market series dated today or on a weekend (an unfinished or weekend quote, never a session close)."""
    ensure(store, "macro_series")
    store._run("DELETE FROM macro_series WHERE series=? AND (obs_date >= ? OR strftime('%w', obs_date) IN ('0', '6'))", (series, today))


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

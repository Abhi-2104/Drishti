"""NSE archive files (nsearchives.nseindia.com): bhavcopies, participant OI, index closes, F&O ban list,
bulk and block deals. Plain files, no session or cookies needed; verified from a home IP on 2026-11-07.

Layering: parse_* are pure (bytes -> rows, raise DrishtiError) and tested on saved fixtures. The public
functions (cm_bhavcopy, ...) fetch through core.fetch, parse, check the file really is for the date asked,
and return a Result. Pass store= to fetch each daily file once; the raw file is stored only after it parses.
"""
from __future__ import annotations

import re
from datetime import date, datetime

from ..core.canary import check_fresh
from ..core.envelope import DrishtiError, ErrorKind, Result, guard
from ..core.fetch import get
from . import _util as u
from . import udiff
from .profiles import NSE_ARCHIVE

SOURCE = "nse_archive"
ARCH = "https://nsearchives.nseindia.com/content"
BAN_URL = "https://nsearchives.nseindia.com/archives/fo/sec_ban/fo_secban_{dmy}.csv"

MIN_CM_ROWS = 1500    # 3,715 on 2026-11-06; far fewer means a truncated file
MIN_FO_ROWS = 5000    # 40,471 on 2026-11-06
MIN_INDEX_ROWS = 50   # 167 on 2026-11-06


# --- parsers (pure) ---------------------------------------------------------------------------------
def parse_cm_bhavcopy(body: bytes, *, min_rows: int = MIN_CM_ROWS) -> list[dict]:
    """UDiFF cash-market bhavcopy zip -> one row per (symbol, series). Series EQ/BE/SM/ST/GS/GB/... all kept."""
    label = "cm_bhavcopy"
    return udiff.parse_cash(u.decode(u.unzip_single(body, label)), label, min_rows)


def parse_fo_bhavcopy(body: bytes, *, min_rows: int = MIN_FO_ROWS) -> list[dict]:
    """UDiFF F&O bhavcopy zip -> one row per contract. instrument_type: IDF/IDO index futures/options, STF/STO stock."""
    label = "fo_bhavcopy"
    need = ["TradDt", "FinInstrmTp", "FinInstrmId", "ISIN", "TckrSymb", "XpryDt", "StrkPric", "OptnTp", "FinInstrmNm",
            "OpnPric", "HghPric", "LwPric", "ClsPric", "LastPric", "PrvsClsgPric", "UndrlygPric", "SttlmPric",
            "OpnIntrst", "ChngInOpnIntrst", "TtlTradgVol", "TtlTrfVal", "TtlNbOfTxsExctd", "NewBrdLotQty"]
    rows = []
    for r in u.csv_rows(u.decode(u.unzip_single(body, label)), need, label):
        opt = u.opt_str(r["OptnTp"])
        if opt not in (None, "CE", "PE"):
            raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: unexpected option type {opt!r}")
        rows.append({
            "trade_date": u.iso_date(r["TradDt"], f"{label}.TradDt"),
            "instrument_type": r["FinInstrmTp"], "instrument_id": r["FinInstrmId"], "symbol": r["TckrSymb"],
            "isin": u.opt_str(r["ISIN"]), "name": r["FinInstrmNm"],
            "expiry": u.iso_date(r["XpryDt"], f"{label}.XpryDt"), "strike": u.num(r["StrkPric"], "strike"), "option_type": opt,
            "open": u.num(r["OpnPric"], "open"), "high": u.num(r["HghPric"], "high"), "low": u.num(r["LwPric"], "low"),
            "close": u.num(r["ClsPric"], "close"), "last": u.num(r["LastPric"], "last"),
            "prev_close": u.num(r["PrvsClsgPric"], "prev_close"), "settle": u.num(r["SttlmPric"], "settle"),
            "underlying": u.num(r["UndrlygPric"], "underlying"),
            "oi": u.integer(r["OpnIntrst"], "oi"), "oi_change": u.integer(r["ChngInOpnIntrst"], "oi_change"),
            "volume": u.integer(r["TtlTradgVol"], "volume"), "turnover": u.num(r["TtlTrfVal"], "turnover"),  # notional value
            "trades": u.integer(r["TtlNbOfTxsExctd"], "trades"), "lot_size": u.integer(r["NewBrdLotQty"], "lot_size"),
        })
    u.require_rows(rows, label, min_rows)
    u.single_date(rows, "trade_date", label)
    u.check_ohlc(rows, label)
    return rows


_OI_TITLE = re.compile(r"as on\s+([A-Za-z]{3,9}\s+\d{1,2},\s*\d{4})")
_OI_COLS = {  # NSE header (stripped) -> our key
    "Future Index Long": "fut_idx_long", "Future Index Short": "fut_idx_short",
    "Future Stock Long": "fut_stk_long", "Future Stock Short": "fut_stk_short",
    "Option Index Call Long": "opt_idx_call_long", "Option Index Put Long": "opt_idx_put_long",
    "Option Index Call Short": "opt_idx_call_short", "Option Index Put Short": "opt_idx_put_short",
    "Option Stock Call Long": "opt_stk_call_long", "Option Stock Put Long": "opt_stk_put_long",
    "Option Stock Call Short": "opt_stk_call_short", "Option Stock Put Short": "opt_stk_put_short",
    "Total Long Contracts": "total_long", "Total Short Contracts": "total_short",
}
_LONGS = [k for k in _OI_COLS.values() if k.endswith("long") and k != "total_long"]
_SHORTS = [k for k in _OI_COLS.values() if k.endswith("short") and k != "total_short"]


def _close(a: int, b: int) -> bool:
    """NSE's own totals are off by a contract or two on the short side (seen 2026-11-06: 5,256,469 vs 5,256,468).
    Tolerate that noise; a shifted or dropped column is off by thousands and still fails."""
    return abs(a - b) <= max(10, 0.0001 * max(abs(a), abs(b)))


def parse_participant_oi(body: bytes) -> list[dict]:
    """Participant-wise open interest -> one row per client type (Client, DII, FII, Pro, TOTAL).

    Integrity checks (a mismatch is PARSE_FAIL, not a quiet number): each row's totals match the sum of its
    components, and the four participants add up to the TOTAL row, column by column (within NSE's +-1 noise).
    """
    label = "participant_oi"
    text = u.decode(body)
    m = _OI_TITLE.search(text.splitlines()[0] if text.strip() else "")
    if not m:
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: no 'as on <date>' in the title line")
    trade_date = _parse_oi_date(m.group(1), label)
    body_lines = text.splitlines()[1:]  # skip the quoted title line, which is not valid CSV
    rows_raw = u.csv_rows("\n".join(body_lines), ["Client Type", *_OI_COLS], label)
    rows = []
    for r in rows_raw:
        row = {"trade_date": trade_date, "client_type": r["Client Type"]}
        for src, key in _OI_COLS.items():
            row[key] = u.integer(r[src], f"{label}.{src}")
            if row[key] is None:
                raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: blank {src} for {r['Client Type']}")
        rows.append(row)
    u.require_rows(rows, label)
    by = {r["client_type"].upper(): r for r in rows}
    for r in rows:
        for total, parts in (("total_long", _LONGS), ("total_short", _SHORTS)):
            if not _close(sum(r[k] for k in parts), r[total]):
                raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: {r['client_type']} {total} {r[total]} != sum of its columns "
                                                         f"{sum(r[k] for k in parts)}")
    if "TOTAL" in by:
        parts = [r for r in rows if r["client_type"].upper() != "TOTAL"]
        for key in _OI_COLS.values():
            if not _close(sum(r[key] for r in parts), by["TOTAL"][key]):
                raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: participants do not add up to TOTAL for {key}")
    return rows


def _parse_oi_date(s: str, label: str) -> str:
    s = re.sub(r"\s+", " ", s.strip())
    for fmt in ("%b %d, %Y", "%B %d, %Y", "%b %d,%Y"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: cannot read date {s!r}")


def parse_index_closes(body: bytes, *, min_rows: int = MIN_INDEX_ROWS) -> list[dict]:
    """ind_close_all: end-of-day values for every NSE index (~167). '-' (e.g. India VIX volume) becomes None."""
    label = "ind_close_all"
    need = ["Index Name", "Index Date", "Open Index Value", "High Index Value", "Low Index Value", "Closing Index Value",
            "Points Change", "Change(%)", "Volume", "Turnover (Rs. Cr.)", "P/E", "P/B", "Div Yield"]
    rows = []
    for r in u.csv_rows(u.decode(body), need, label):
        rows.append({
            "trade_date": u.iso_date(r["Index Date"], f"{label}.Index Date"), "index_name": r["Index Name"],
            "open": u.num(r["Open Index Value"], "open"), "high": u.num(r["High Index Value"], "high"),
            "low": u.num(r["Low Index Value"], "low"), "close": u.num(r["Closing Index Value"], "close"),
            "change_pts": u.num(r["Points Change"], "change_pts"), "change_pct": u.num(r["Change(%)"], "change_pct"),
            "volume": u.integer(r["Volume"], "volume"), "turnover_cr": u.num(r["Turnover (Rs. Cr.)"], "turnover_cr"),
            "pe": u.num(r["P/E"], "pe"), "pb": u.num(r["P/B"], "pb"), "div_yield": u.num(r["Div Yield"], "div_yield"),
        })
        if not rows[-1]["index_name"] or rows[-1]["close"] is None:
            raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: row without a name or close: {r}")
    u.require_rows(rows, label, min_rows)
    u.single_date(rows, "trade_date", label)
    return rows


_BAN_TITLE = re.compile(r"^\s*Securities in Ban For Trade Date\s+(\d{1,2}-[A-Za-z]{3}-\d{4})\s*:?\s*$", re.I)
_BAN_ROW = re.compile(r"^\s*\d+\s*,\s*([A-Za-z0-9&\-]+)\s*$")


def parse_fo_ban(body: bytes) -> tuple[str, list[str]]:
    """F&O ban list -> (trade_date, [symbols]). A title line with no symbols is a real, empty ban list: the title
    proves it is the genuine file, so that is the one place an empty list is a success rather than EMPTY."""
    label = "fo_secban"
    lines = [l for l in u.decode(body).splitlines() if l.strip()]
    if not lines or not (m := _BAN_TITLE.match(lines[0])):
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: unexpected first line {lines[0][:60]!r}" if lines else f"{label}: empty file")
    symbols = []
    for l in lines[1:]:
        mm = _BAN_ROW.match(l)
        if not mm:
            raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: unexpected line {l[:60]!r}")
        symbols.append(mm.group(1).upper())
    return u.iso_date(m.group(1), f"{label}.title"), symbols


def _parse_deals(body: bytes, label: str) -> list[dict]:
    rows = []
    for r in u.csv_rows(u.decode(body), ["Date", "Symbol", "Security Name", "Client Name", "Buy/Sell", "Quantity Traded",
                                         "Trade Price / Wght. Avg. Price"], label):
        side = r["Buy/Sell"].strip().upper()
        if side not in ("BUY", "SELL"):
            raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: unexpected side {side!r}")
        rows.append({
            "trade_date": u.iso_date(r["Date"], f"{label}.Date"), "symbol": r["Symbol"], "name": r["Security Name"],
            "client": r["Client Name"], "side": side, "quantity": u.integer(r["Quantity Traded"], "quantity"),
            "price": u.num(r["Trade Price / Wght. Avg. Price"], "price"), "remarks": u.opt_str(r.get("Remarks")) if r.get("Remarks") != "-" else None,
        })
    u.require_rows(rows, label)
    return rows


def parse_bulk_deals(body: bytes) -> list[dict]:
    return _parse_deals(body, "bulk_deals")


def parse_block_deals(body: bytes) -> list[dict]:
    return _parse_deals(body, "block_deals")


# --- public provider functions (fetch + parse + date check) -----------------------------------------
def _ymd(d: date) -> str:
    return d.strftime("%Y%m%d")


def _dmy(d: date) -> str:
    return d.strftime("%d%m%Y")


def _expect_date(rows_date: str, d: date, label: str) -> None:
    if rows_date != d.isoformat():
        raise DrishtiError(ErrorKind.STALE, f"{label}: file for {d} contains data for {rows_date}")


def _daily(label: str, d: date, url: str, expect: str, parse, store) -> Result:
    rows, got = u.load_parsed(store, label, d, lambda: get(url, NSE_ARCHIVE, expect=expect), parse, SOURCE)
    _expect_date(rows[0]["trade_date"], d, label)
    return Result(data=rows, as_of=d.isoformat(), sources=[got.source(SOURCE)])


@guard
def cm_bhavcopy(d: date, *, store=None) -> Result:
    return _daily("cm_bhavcopy", d, f"{ARCH}/cm/BhavCopy_NSE_CM_0_0_0_{_ymd(d)}_F_0000.csv.zip", "zip", parse_cm_bhavcopy, store)


@guard
def fo_bhavcopy(d: date, *, store=None) -> Result:
    return _daily("fo_bhavcopy", d, f"{ARCH}/fo/BhavCopy_NSE_FO_0_0_0_{_ymd(d)}_F_0000.csv.zip", "zip", parse_fo_bhavcopy, store)


@guard
def participant_oi(d: date, *, store=None) -> Result:
    return _daily("participant_oi", d, f"{ARCH}/nsccl/fao_participant_oi_{_dmy(d)}.csv", "csv", parse_participant_oi, store)


@guard
def index_closes(d: date, *, store=None) -> Result:
    return _daily("ind_close_all", d, f"{ARCH}/indices/ind_close_all_{_dmy(d)}.csv", "csv", parse_index_closes, store)


@guard
def fo_ban_list(d: date, *, store=None) -> Result:
    """Symbols in the F&O ban period on `d`. data = list of symbols (possibly empty: a real, empty ban list)."""
    (file_date, symbols), got = u.load_parsed(store, "fo_secban", d, lambda: get(BAN_URL.format(dmy=_dmy(d)), NSE_ARCHIVE, expect="csv"),
                                              parse_fo_ban, SOURCE)
    _expect_date(file_date, d, "fo_secban")
    return Result(data=symbols, as_of=file_date, sources=[got.source(SOURCE)])


def _snapshot(label: str, url: str, parse, store, max_age_days: int) -> Result:
    """Undated 'latest' file (bulk/block deals): always fetched, freshness judged by the newest Date inside."""
    got = get(url, NSE_ARCHIVE, expect="csv")
    rows = parse(got.body)
    newest = check_fresh(rows, "trade_date", max_age_days, label)
    if store is not None:
        store.put_blob(label, newest, got, SOURCE)
    return Result(data=rows, as_of=newest.isoformat(), sources=[got.source(SOURCE)])


@guard
def bulk_deals(*, store=None, max_age_days: int = 5) -> Result:
    return _snapshot("bulk_deals", f"{ARCH}/equities/bulk.csv", parse_bulk_deals, store, max_age_days)


@guard
def block_deals(*, store=None, max_age_days: int = 5) -> Result:
    return _snapshot("block_deals", f"{ARCH}/equities/block.csv", parse_block_deals, store, max_age_days)

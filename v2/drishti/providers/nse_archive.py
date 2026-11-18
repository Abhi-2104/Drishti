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
MIN_DELIVERY_ROWS = 1500    # 3,559 on 2026-11-06
FIRST_FULL_DELIVERY = date(2019, 8, 23)   # first session sec_bhavdata_full exists (the backfill found nothing earlier)
MIN_MTO_ROWS = 800           # 1,633 on 2016-09-15 (the old delivery file lists fewer stocks)
MAX_BAD_DELIVERY_ROWS = 5   # rows dropped as unusable (one impossible row, a record split by a stray line break); more than this = PARSE_FAIL
DELIVERY_URL = "https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{dmy}.csv"


# --- parsers (pure) ---------------------------------------------------------------------------------
def parse_cm_bhavcopy(body: bytes, *, min_rows: int = MIN_CM_ROWS) -> list[dict]:
    """UDiFF cash-market bhavcopy zip -> one row per (symbol, series). Series EQ/BE/SM/ST/GS/GB/... all kept."""
    label = "cm_bhavcopy"
    return udiff.parse_cash(u.decode(u.unzip_single(body, label)), label, min_rows)


LEGACY_MIN_ROWS = 1000   # 2,101 rows on 15 Sep 2021, 2,681 on 15 Mar 2024


def parse_cm_bhavcopy_legacy(body: bytes, *, min_rows: int = LEGACY_MIN_ROWS) -> list[dict]:
    """Pre-UDiFF NSE cash bhavcopy zip (cmDDMONYYYYbhav.csv, in use until 2024) -> the same row shape as parse_cm_bhavcopy.
    It carries ISIN, so it joins like the new file. Rows without an ISIN are kept with isin None (the store skips them)."""
    label = "cm_bhavcopy_legacy"
    need = ["SYMBOL", "SERIES", "OPEN", "HIGH", "LOW", "CLOSE", "LAST", "PREVCLOSE", "TOTTRDQTY", "TOTTRDVAL", "TIMESTAMP", "TOTALTRADES", "ISIN"]
    rows = []
    for r in u.csv_rows(u.decode(u.unzip_single(body, label)), need, label):
        rows.append({
            "trade_date": u.iso_date(r["TIMESTAMP"], f"{label}.TIMESTAMP"), "symbol": r["SYMBOL"], "series": r["SERIES"],
            "isin": u.opt_str(r["ISIN"]), "name": None, "instrument_id": r["SYMBOL"],
            "open": u.num(r["OPEN"], "open"), "high": u.num(r["HIGH"], "high"), "low": u.num(r["LOW"], "low"),
            "close": u.num(r["CLOSE"], "close"), "last": u.num(r["LAST"], "last"), "prev_close": u.num(r["PREVCLOSE"], "prev_close"),
            "volume": u.integer(r["TOTTRDQTY"], "volume"), "turnover": u.num(r["TOTTRDVAL"], "turnover"),
            "trades": u.integer(r["TOTALTRADES"], "trades"),
        })
    u.require_rows(rows, label, min_rows)
    u.single_date(rows, "trade_date", label)
    u.check_ohlc(rows, label)
    return rows


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


_MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}
_OI_MDY = re.compile(r"\b([A-Za-z]{3,9})\.?\s*(\d{1,2})\s*,?\s*(\d{4})\b")        # "Aug 19, 2024", "Apr 09 2020", 'Aug 19,"2024"""""', "Decemebr 06, 2018"
_OI_DMY = re.compile(r"\b(\d{1,2})[\s-]+([A-Za-z]{3,9})[\s,.-]+(\d{4})\b")            # "19-Aug-2024"
_OI_NUM = re.compile(r"\b(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})\b")                      # "19/08/2024", read day first (Indian order)


def _oi_title_date(line: str, label: str, day: date | None = None) -> str:
    """The session date in the title line. The month is read from its first three letters (NSE's own file once spelled it 'Decemebr');
    a word that is not a month is skipped. Any date format found there is accepted, because the caller then checks it against the day asked for."""
    t = line.replace('"', " ")
    for m in _OI_MDY.finditer(t):
        mon = _MONTHS.get(m.group(1)[:3].lower())
        if mon:
            return _ymd_check(int(m.group(3)), mon, int(m.group(2)), label)
    for m in _OI_DMY.finditer(t):
        mon = _MONTHS.get(m.group(2)[:3].lower())
        if mon:
            return _ymd_check(int(m.group(3)), mon, int(m.group(1)), label)
    for m in _OI_NUM.finditer(t):
        return _ymd_check(int(m.group(3)), int(m.group(2)), int(m.group(1)), label)
    if day is not None:                       # "... as on Jun 08" with no year (seen 8 Jun 2021): the year is the one asked for; the day check still runs
        for m in re.finditer(r"\b([A-Za-z]{3,9})\.?\s*(\d{1,2})\b", t):
            mon = _MONTHS.get(m.group(1)[:3].lower())
            if mon:
                return _ymd_check(day.year, mon, int(m.group(2)), label)
    raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: no 'as on <date>' in the title line {line[:90]!r}")


def _ymd_check(y: int, m: int, d: int, label: str) -> str:
    try:
        return date(y, m, d).isoformat()
    except ValueError:
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: cannot read date {y}-{m}-{d} in the title line") from None

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


def parse_participant_oi(body: bytes, *, day: date | None = None) -> list[dict]:
    """Participant-wise open interest -> one row per client type (Client, DII, FII, Pro, TOTAL).

    Integrity checks (a mismatch is PARSE_FAIL, not a quiet number): each row's totals match the sum of its
    components, and the four participants add up to the TOTAL row, column by column (within NSE's +-1 noise).
    """
    label = "participant_oi"
    text = u.decode(body)
    lines = text.splitlines()
    first = lines[0] if text.strip() else ""
    if first.replace('"', "").lstrip().startswith("Client Type"):     # no title line at all (seen 13 Jun 2018): the date asked for is all there is
        if day is None:
            raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: no title line, so no date, and no day was given")
        trade_date, body_lines = day.isoformat(), lines
    else:
        trade_date = _oi_title_date(first, label, day)      # quotes appear anywhere in old titles
        body_lines = lines[1:]  # skip the quoted title line, which is not valid CSV
    rows_raw = u.csv_rows("\n".join(body_lines), ["Client Type", *_OI_COLS], label)
    rows = []
    for r in rows_raw:
        row = {"trade_date": trade_date, "client_type": r["Client Type"]}
        for src, key in _OI_COLS.items():
            row[key] = _whole(r[src], f"{label}.{src}")
            if row[key] is None:
                raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: blank {src} for {r['Client Type']}")
        rows.append(row)
    u.require_rows(rows, label)
    by = {r["client_type"].upper(): r for r in rows}
    first_bad = next(((r, t, ps) for r in rows for t, ps in (("total_long", _LONGS), ("total_short", _SHORTS))
                      if not _close(sum(r[k] for k in ps), r[t])), None)
    if first_bad:
        r, total, ps = first_bad
        # NSE's own Total columns are wrong in three 2017 files (2017-01-24, 05-29, 09-22: each total leaves out one component column, in the
        # TOTAL row too). The twelve component columns are repaired only if the file proves them on its own: the four participants add up to
        # the TOTAL row in every component, and in the TOTAL row every long matches its short (each contract has a buyer and a seller).
        tot_row = by.get("TOTAL")
        parts = [x for x in rows if x["client_type"].upper() != "TOTAL"]
        comps = _LONGS + _SHORTS
        pairs = [(a, a.replace("long", "short")) for a in _LONGS]
        proven = (tot_row is not None and all(_close(sum(x[k] for x in parts), tot_row[k]) for k in comps)
                  and all(_close(tot_row[a], tot_row[b]) for a, b in pairs))
        if not proven:
            raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: {r['client_type']} {total} {r[total]} != sum of its columns "
                                                     f"{sum(r[k] for k in ps)}")
        for x in rows:
            x["total_long"], x["total_short"] = sum(x[k] for k in _LONGS), sum(x[k] for k in _SHORTS)
    if "TOTAL" in by:
        parts = [r for r in rows if r["client_type"].upper() != "TOTAL"]
        for key in _OI_COLS.values():
            if not _close(sum(r[key] for r in parts), by["TOTAL"][key]):
                raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: participants do not add up to TOTAL for {key}")
    return rows


def _whole(raw: str, label: str):
    """A contract count. Two 2018 files carry averaged counts like '216533.6667' (a source slip); a count is rounded to the nearest
    whole contract, which moves it by less than one contract in 100,000s. Anything else non-numeric is still PARSE_FAIL."""
    try:
        return u.integer(raw, label)
    except DrishtiError:
        n = u.num(raw, label)
        return None if n is None else int(round(n))


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


_BAN_TITLE = re.compile(r"^\s*Securities in Ban For Trade Date\s+(\d{1,2}-[A-Za-z]{3}-\d{4})\s*:?\s*(NIL)?\s*$", re.I)  # "...: NIL" seen 17 Nov 2026
_BAN_ROW = re.compile(r"^\s*\d+\s*,\s*([A-Za-z0-9&\-]+)\s*$")


def parse_fo_ban(body: bytes) -> tuple[str, list[str]]:
    """F&O ban list -> (trade_date, [symbols]). A title line with no symbols is a real, empty ban list: the title
    proves it is the genuine file, so that is the one place an empty list is a success rather than EMPTY."""
    label = "fo_secban"
    lines = [l for l in u.decode(body).splitlines() if l.strip()]
    if not lines or not (m := _BAN_TITLE.match(lines[0])):
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: unexpected first line {lines[0][:60]!r}" if lines else f"{label}: empty file")
    symbols = []
    if m.group(2) and len(lines) > 1:
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: title says NIL but {len(lines) - 1} more lines follow")
    for l in lines[1:]:
        mm = _BAN_ROW.match(l)
        if not mm:
            raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: unexpected line {l[:60]!r}")
        symbols.append(mm.group(1).upper())
    return u.iso_date(m.group(1), f"{label}.title"), symbols


NO_RECORDS = "NO RECORDS"   # NSE's placeholder row on a day with no deals (block.csv, 18 Nov 2026): header + "NO RECORDS,,,,,,"


def _parse_deals(body: bytes, label: str) -> list[dict]:
    """Rows of a bulk/block deal file. A file whose only row is NSE's 'NO RECORDS' placeholder (every other field blank) is a
    real day without deals and returns []; any other blank or odd side is still a PARSE_FAIL."""
    raw = list(u.csv_rows(u.decode(body), ["Date", "Symbol", "Security Name", "Client Name", "Buy/Sell", "Quantity Traded",
                                           "Trade Price / Wght. Avg. Price"], label))
    if len(raw) == 1 and raw[0]["Date"].strip().upper() == NO_RECORDS and not any((v or "").strip() for k, v in raw[0].items() if k != "Date"):
        return []
    rows = []
    for r in raw:
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


def parse_delivery(body: bytes, *, min_rows: int = MIN_DELIVERY_ROWS) -> list[dict]:
    """sec_bhavdata_full: one row per (symbol, series) with traded and delivered quantity. Trade-for-trade series (BE, BZ...) carry
    '-' for delivery: those rows are kept with delivery None (a real "not applicable", not a blank)."""
    label = "delivery"
    need = ["SYMBOL", "SERIES", "DATE1", "TTL_TRD_QNTY", "NO_OF_TRADES", "DELIV_QTY", "DELIV_PER"]
    out, seen, dates, bad = [], set(), set(), []
    for r in u.csv_rows(u.decode(body), need, label):
        r = {k: (v or "").strip() for k, v in r.items()}
        sym, series = r["SYMBOL"].upper(), r["SERIES"].upper()
        if not sym or not series or not r["DATE1"] or u.num(r["TTL_TRD_QNTY"], f"{label}.qty") is None:
            bad.append(f"broken line near {sym or '?'}")          # a record cut in two by a stray line break in the source
            continue
        if (sym, series) in seen:
            raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: {sym}/{series} appears twice")
        seen.add((sym, series))
        day = u.iso_date(r["DATE1"], f"{label}.DATE1")
        dates.add(day)
        ttl, dq, dp = u.num(r["TTL_TRD_QNTY"], f"{label}.qty"), u.num(r["DELIV_QTY"], f"{label}.deliv_qty"), u.num(r["DELIV_PER"], f"{label}.deliv_per")
        if (dq is None) != (dp is None):
            raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: {sym}/{series} has only one of delivery quantity and percentage")
        if dq is not None and (dq > ttl or not 0 <= dp <= 100.0001):
            bad.append(f"{sym}/{series} delivery {dq} ({dp}%) impossible for {ttl} traded")     # seen: WTICAB/ST, 19 Feb 2024
            continue
        out.append({"trade_date": day, "symbol": sym, "series": series, "ttl_qty": int(ttl or 0), "deliv_qty": None if dq is None else int(dq),
                    "deliv_per": dp, "trades": int(u.num(r["NO_OF_TRADES"], f"{label}.trades") or 0)})
    if len(bad) > MAX_BAD_DELIVERY_ROWS:
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: {len(bad)} unusable rows (first: {bad[0]}); the file is not trustworthy")
    u.require_rows(out, label, min_rows)
    if len(dates) != 1:
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: file mixes dates {sorted(dates)[:3]}")
    eq = [x for x in out if x["series"] == "EQ"]
    if eq and sum(1 for x in eq if x["deliv_per"] is not None) < 0.9 * len(eq):
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: delivery missing for most EQ rows; column changed?")
    return out


_MTO_DATE = re.compile(r"Trade Date\s*<\s*(\d{1,2}-[A-Za-z]{3}-\d{4})\s*>", re.I)


def parse_delivery_mto(body: bytes, *, min_rows: int = MIN_MTO_ROWS) -> list[dict]:
    """NSE's older delivery file (MTO_ddmmyyyy.DAT, published 2016 onward and still published): the same measure as sec_bhavdata_full
    (traded quantity, deliverable quantity, %) with no trade count. Rows come out in the delivery table's shape (trades = None).
    The file states its own row count on the '10,MTO,...' line; a file that does not match it is refused as truncated."""
    label = "delivery_mto"
    lines = [l.strip() for l in u.decode(body).splitlines() if l.strip()]
    if not lines or "Delivery Position" not in lines[0]:
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: unexpected first line {lines[0][:60]!r}" if lines else f"{label}: empty file")
    m = _MTO_DATE.search(" ".join(lines[:10]))
    if m:
        day = u.iso_date(m.group(1), f"{label}.trade date")
    else:                                         # six 2017-2019 files have no 'Trade Date <..>' line: the '10,MTO,ddmmyyyy,..' line carries the date
        ten = next((l.split(",") for l in lines[:10] if l.split(",")[0].strip() == "10"), [])
        raw = ten[2].strip() if len(ten) > 2 else ""
        try:
            day = datetime.strptime(raw, "%d%m%Y").date().isoformat()
        except ValueError:
            raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: no trade date in the first lines {' | '.join(l[:50] for l in lines[:3])!r}") from None
    declared = None
    out, seen, bad, records = [], set(), [], 0
    for l in lines[1:]:
        parts = [x.strip() for x in l.split(",")]
        if parts[0] == "10":
            declared = int(parts[4]) if len(parts) > 4 and parts[4].isdigit() else None
            continue
        if l.startswith("Trade Date") or l.startswith("Record Type"):
            continue
        if parts[0] != "20":
            bad.append(f"unexpected line {l[:50]!r}")
            continue
        records += 1
        if len(parts) != 7:
            bad.append(f"line with {len(parts)} fields: {l[:50]!r}")
            continue
        sym, series = parts[2].upper(), parts[3].upper()
        qty, dq, pct = u.num(parts[4], f"{label}.qty"), u.num(parts[5], f"{label}.deliv_qty"), u.num(parts[6], f"{label}.deliv_per")
        if not sym or not series or qty is None or dq is None or pct is None:
            bad.append(f"{sym or '?'}: blank figure")
            continue
        if dq > qty or not 0 <= pct <= 100.0001 or (qty and abs(pct - dq / qty * 100) > 0.1):
            bad.append(f"{sym}/{series} delivery {dq} ({pct}%) impossible for {qty} traded")
            continue
        if (sym, series) in seen:
            raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: {sym}/{series} appears twice")
        seen.add((sym, series))
        out.append({"trade_date": day, "symbol": sym, "series": series, "ttl_qty": int(qty), "deliv_qty": int(dq), "deliv_per": pct, "trades": None})
    if declared is not None and not declared <= records <= declared + 5:      # fewer than declared = cut short; a miscount by one or two (seen 12 Oct 2018) is the source's
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: file says {declared} records, found {records}; truncated or altered")
    if len(bad) > MAX_BAD_DELIVERY_ROWS:
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: {len(bad)} unusable rows (first: {bad[0]}); the file is not trustworthy")
    u.require_rows(out, label, min_rows)
    return out


# --- public provider functions (fetch + parse + date check) -----------------------------------------
def _ymd(d: date) -> str:
    return d.strftime("%Y%m%d")


def _dmy(d: date) -> str:
    return d.strftime("%d%m%Y")


def _expect_date(rows_date: str, d: date, label: str) -> None:
    if rows_date != d.isoformat():
        raise DrishtiError(ErrorKind.STALE, f"{label}: file for {d} contains data for {rows_date}")


def _swapped(rows_date: str, d: date) -> bool:
    """The file's date is the requested date with day and month swapped (seen in ind_close_all for 6, 10 and 11 Apr 2023)."""
    try:
        y, m, dd = (int(x) for x in rows_date.split("-"))
        return date(y, dd, m) == d and m != dd
    except ValueError:
        return False


def _daily(label: str, d: date, url: str, expect: str, parse, store) -> Result:
    rows, got = u.load_parsed(store, label, d, lambda: get(url, NSE_ARCHIVE, expect=expect), parse, SOURCE)
    if rows and _swapped(rows[0]["trade_date"], d) and all(r["trade_date"] == rows[0]["trade_date"] for r in rows):
        rows = [{**r, "trade_date": d.isoformat()} for r in rows]     # the file names the day; its date column had day and month swapped
    _expect_date(rows[0]["trade_date"], d, label)
    return Result(data=rows, as_of=d.isoformat(), sources=[got.source(SOURCE)])


@guard
def cm_bhavcopy(d: date, *, store=None) -> Result:
    return _daily("cm_bhavcopy", d, f"{ARCH}/cm/BhavCopy_NSE_CM_0_0_0_{_ymd(d)}_F_0000.csv.zip", "zip", parse_cm_bhavcopy, store)


@guard
def cm_bhavcopy_legacy(d: date, *, store=None) -> Result:
    """The old-format cash bhavcopy, for sessions before the UDiFF file exists (before 2024). Same rows, same ISIN key."""
    mon = d.strftime("%b").upper()
    url = f"{ARCH}/historical/EQUITIES/{d:%Y}/{mon}/cm{d:%d}{mon}{d:%Y}bhav.csv.zip"
    return _daily("cm_bhavcopy_legacy", d, url, "zip", parse_cm_bhavcopy_legacy, store)


@guard
def fo_bhavcopy(d: date, *, store=None) -> Result:
    return _daily("fo_bhavcopy", d, f"{ARCH}/fo/BhavCopy_NSE_FO_0_0_0_{_ymd(d)}_F_0000.csv.zip", "zip", parse_fo_bhavcopy, store)


@guard
def _delivery_full(d: date, *, store=None) -> Result:
    return _daily("delivery", d, DELIVERY_URL.format(dmy=_dmy(d)), "csv", parse_delivery, store)


@guard
def delivery_mto(d: date, *, store=None) -> Result:
    """The older delivery file, available from 2016 (the new one starts 23 Aug 2019). Same measure, no trade count."""
    return _daily("delivery_mto", d, f"https://nsearchives.nseindia.com/archives/equities/mto/MTO_{_dmy(d)}.DAT", "csv", parse_delivery_mto, store)


def delivery(d: date, *, store=None) -> Result:
    """Delivered quantity and percentage per stock for one session (published in the evening, after the bhavcopy). Uses the current file;
    where that file is missing, holds another day's data or cannot be read (before Aug 2019, the bad file of 30 Sep 2019, the broken one of 8 Aug 2022) it uses
    the older MTO file, which is the same measure. A block page is never worked around.
    If both fail, the current file's error is the one reported."""
    if d < FIRST_FULL_DELIVERY:                   # the current file does not exist yet: no point asking for it 800 times
        return delivery_mto(d, store=store)
    r = _delivery_full(d, store=store)
    if r.ok or r.error.kind not in (ErrorKind.NOT_FOUND, ErrorKind.STALE, ErrorKind.PARSE_FAIL):
        return r
    m = delivery_mto(d, store=store)
    return m if m.ok else r


@guard
def participant_oi(d: date, *, store=None) -> Result:
    return _daily("participant_oi", d, f"{ARCH}/nsccl/fao_participant_oi_{_dmy(d)}.csv", "csv", lambda b: parse_participant_oi(b, day=d), store)


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
    if rows == []:      # NSE says "NO RECORDS": a genuine day without deals. The file carries no date, so no as_of is claimed.
        return Result(data=[], as_of=None, sources=[got.source(SOURCE)])
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

"""Live / delayed market data (L4-3, L3-1, L2-5): NSE equity quote (GetQuoteApi), the all-indices snapshot, the
intraday option chain (v3), and BSE's scrip header as a cross-check / BSE-only quote.

These are snapshots, not end-of-day files: each result states the exchange's own timestamp, and the freshness check is
on that timestamp. After the close they show the closing state, which is correct but not live; `live` says which.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta

from ..core.canary import IST, today_ist
from ..core.envelope import DrishtiError, ErrorKind, Result, guard
from ..core.fetch import get
from ..core.isin import is_valid_isin, normalize_isin
from . import _util as u
from . import nse_live
from .profiles import BSE_API

SOURCE = "nse_api"
_TS = ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M", "%d %b %y | %H:%M")


def _ts(v, label) -> str:
    s = str(v or "").strip().title()
    for f in _TS:
        try:
            return datetime.strptime(s, f).isoformat(timespec="minutes")
        except ValueError:
            continue
    raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: cannot read timestamp {v!r}")


def _json(body: bytes, label: str):
    try:
        return json.loads(body)
    except (ValueError, UnicodeDecodeError) as e:
        kind = ErrorKind.BLOCKED_HTML if body.lstrip()[:1] == b"<" else ErrorKind.PARSE_FAIL
        raise DrishtiError(kind, f"{label}: not JSON ({type(e).__name__})") from e


def market_open(now: datetime | None = None) -> bool:
    """Regular NSE session 09:15-15:30 IST on weekdays (holidays not known here)."""
    n = (now or datetime.now(IST)).astimezone(IST)
    return n.weekday() < 5 and (9 * 60 + 15) <= n.hour * 60 + n.minute <= (15 * 60 + 30)


# --- NSE equity quote ---------------------------------------------------------------------------------------
def parse_quote(body: bytes) -> dict:
    label = "nse_quote"
    doc = _json(body, label)
    try:
        r = doc["equityResponse"][0]
        meta, trade, pinfo, sec = r["metaData"], r["tradeInfo"], r["priceInfo"], r.get("secInfo") or {}
    except (KeyError, IndexError, TypeError) as e:
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: unexpected layout ({type(e).__name__})") from e
    isin = normalize_isin(meta.get("isinCode"))
    last = u.num(trade.get("lastPrice") if trade.get("lastPrice") is not None else r.get("orderBook", {}).get("lastPrice"), "lastPrice")
    if not last or last <= 0:
        raise DrishtiError(ErrorKind.EMPTY, f"{label}: no last price for {meta.get('symbol')}")
    band = str(pinfo.get("priceBand") or "")
    lo_band, hi_band = (u.num(x, "band") for x in band.split("-")) if "-" in band else (None, None)
    return {"symbol": meta.get("symbol"), "isin": isin if is_valid_isin(isin) else None, "series": meta.get("series"),
            "company": meta.get("companyName"), "last": last, "open": u.num(meta.get("open"), "open"), "high": u.num(meta.get("dayHigh"), "high"),
            "low": u.num(meta.get("dayLow"), "low"), "prev_close": u.num(meta.get("previousClose"), "prev"),
            "close": u.num(meta.get("closePrice"), "close") or None, "change_pct": u.num(meta.get("pChange"), "pChange"),
            "vwap": u.num(meta.get("averagePrice"), "vwap"), "volume": u.integer(trade.get("totalTradedVolume"), "vol"),
            "value": u.num(trade.get("totalTradedValue"), "value"), "delivery_pct": u.num(trade.get("deliveryToTradedQuantity"), "delivery"),
            "market_cap": u.num(trade.get("totalMarketCap"), "mcap"), "free_float_mcap": u.num(trade.get("ffmc"), "ffmc"),
            "year_high": u.num(pinfo.get("yearHigh"), "yh"), "year_low": u.num(pinfo.get("yearLow"), "yl"),
            "price_band": [lo_band, hi_band] if lo_band else None, "status": meta.get("symbolStatus"),
            "industry": (sec.get("basicIndustry") or "").strip() or None, "sector": (sec.get("sector") or "").strip() or None,
            "index": (sec.get("index") or "").strip() or None, "symbol_pe": u.num(sec.get("pdSymbolPe"), "pe"),
            "sector_pe": u.num(sec.get("pdSectorPe"), "spe"), "delivery_date": (trade.get("secwisedelposdate") or "")[:11].strip() or None}


@guard
def quote(symbol: str, *, series: str = "EQ", now: datetime | None = None) -> Result:
    """NSE quote for one equity symbol. `live` is True only inside market hours; after the close the figures are the
    closing state. A quote whose ISIN does not validate is still returned, flagged, never joined on."""
    sym = (symbol or "").strip().upper()
    if not sym or len(sym) > 20:
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"nse_quote: bad symbol {symbol!r}")
    series = (series or "EQ").strip().upper()
    if not series.isalnum() or len(series) > 3:
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"nse_quote: bad series {series!r}")
    got = nse_live.api_get("NextApi/apiClient/GetQuoteApi", functionName="getSymbolData", marketType="N", series=series, symbol=sym)
    d = parse_quote(got.body)
    if (d["symbol"] or "").upper() != sym:
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"nse_quote: asked for {sym}, got {d['symbol']}")
    d["live"] = market_open(now)
    return Result(data=d, as_of=(now or datetime.now(IST)).date().isoformat(), sources=[got.source(SOURCE)])


# --- all indices (L3-1 snapshot) ------------------------------------------------------------------------------
def parse_all_indices(body: bytes) -> dict:
    label = "nse_all_indices"
    doc = _json(body, label)
    if not isinstance(doc, dict) or not isinstance(doc.get("data"), list) or "timestamp" not in doc:
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: layout changed")
    rows = [{"index": r["index"], "last": u.num(r.get("last"), "last"), "change_pct": u.num(r.get("percentChange"), "pct"),
             "open": u.num(r.get("open"), "open"), "high": u.num(r.get("high"), "high"), "low": u.num(r.get("low"), "low"),
             "prev_close": u.num(r.get("previousClose"), "prev"), "pe": u.num(r.get("pe"), "pe"), "pb": u.num(r.get("pb"), "pb"),
             "advances": u.integer(r.get("advances"), "adv"), "declines": u.integer(r.get("declines"), "dec"),
             "change_30d_pct": u.num(r.get("perChange30d"), "30d"), "change_365d_pct": u.num(r.get("perChange365d"), "365d")}
            for r in doc["data"] if r.get("index")]
    u.require_rows(rows, label, 50)
    return {"timestamp": _ts(doc["timestamp"], label), "breadth": {"advances": u.integer(doc.get("advances"), "adv"),
            "declines": u.integer(doc.get("declines"), "dec"), "unchanged": u.integer(doc.get("unchanged"), "unch")}, "indices": rows}


@guard
def all_indices(*, now: datetime | None = None, max_age_hours: int = 96) -> Result:
    """Snapshot of every NSE index plus market breadth. Not for EOD history: India VIX 'last' here differs from its
    official close (ledger L1-2), so history comes from the dated ind_close_all file."""
    now = now or datetime.now(IST)
    got = nse_live.api_get("allIndices")
    d = parse_all_indices(got.body)
    if now.replace(tzinfo=None) - datetime.fromisoformat(d["timestamp"]) > timedelta(hours=max_age_hours):
        raise DrishtiError(ErrorKind.STALE, f"nse_all_indices: snapshot from {d['timestamp']}")
    d["live"] = market_open(now)
    return Result(data=d, as_of=d["timestamp"][:10], sources=[got.source(SOURCE)])


# --- option chain v3 (L2-5, on demand) -------------------------------------------------------------------------
def parse_option_chain(body: bytes) -> dict:
    label = "nse_option_chain"
    doc = _json(body, label)
    rec = (doc or {}).get("records") if isinstance(doc, dict) else None
    if not isinstance(rec, dict) or not isinstance(rec.get("data"), list):
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: no records.data")
    if not rec["data"]:
        raise DrishtiError(ErrorKind.EMPTY, f"{label}: chain is empty (wrong or expired expiry?)")
    rows, expiries = [], set()
    for r in rec["data"]:
        for side in ("CE", "PE"):
            x = r.get(side)
            if not x:
                continue
            exp = u.iso_date(x.get("expiryDate") or r.get("expiryDates"), f"{label}.expiry")
            expiries.add(exp)
            rows.append({"expiry": exp, "strike": u.num(r.get("strikePrice"), "strike"), "option_type": side,
                         "oi": u.integer(x.get("openInterest"), "oi"), "oi_change": u.integer(x.get("changeinOpenInterest"), "doi"),
                         "volume": u.integer(x.get("totalTradedVolume"), "vol"), "last": u.num(x.get("lastPrice"), "ltp"),
                         "iv": u.num(x.get("impliedVolatility"), "iv"), "bid": u.num(x.get("buyPrice1"), "bid"), "ask": u.num(x.get("sellPrice1"), "ask")})
    if len(expiries) != 1:
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: expected one expiry, got {sorted(expiries)[:4]}")
    return {"timestamp": _ts(rec.get("timestamp"), label), "underlying": u.num(rec.get("underlyingValue"), "underlying"),
            "expiry": expiries.pop(), "rows": rows}


@guard
def option_chain(symbol: str = "NIFTY", expiry: str | None = None, *, now: datetime | None = None) -> Result:
    """Intraday option chain for one index and expiry (default: nearest). For EOD positioning use the stored F&O
    bhavcopy (options_flow); this is for 'what is the chain doing right now'."""
    from .nse_corp import expiries as _expiries
    now = now or datetime.now(IST)
    if expiry is None:
        ex = _expiries(symbol, today=now.date())
        if not ex.ok:
            return ex
        expiry = ex.data["expiries"][0]
    exp_nse = datetime.fromisoformat(expiry).strftime("%d-%b-%Y")
    got = nse_live.api_get("option-chain-v3", type="Indices", symbol=symbol, expiry=exp_nse)
    d = parse_option_chain(got.body)
    if d["expiry"] != expiry:
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"nse_option_chain: asked for {expiry}, got {d['expiry']}")
    d["live"] = market_open(now)
    return Result(data=d, as_of=d["timestamp"][:10], sources=[got.source(SOURCE)])


# --- BSE scrip header (quote cross-check, BSE-only names) --------------------------------------------------------
BSE_HEADER = "https://api.bseindia.com/BseIndiaAPI/api/getScripHeaderData/w?Debtflag=&scripcode={code}&seriesid="


def parse_bse_header(body: bytes) -> dict:
    label = "bse_scrip_header"
    doc = _json(body, label)
    try:
        h, name, rate = doc["Header"], doc["Cmpname"], doc["CurrRate"]
    except (KeyError, TypeError) as e:
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: layout changed ({type(e).__name__})") from e
    last = u.num(h.get("LTP") or rate.get("LTP"), "LTP")
    if not last:
        raise DrishtiError(ErrorKind.EMPTY, f"{label}: no LTP")
    return {"scrip_code": name.get("EquityScrips"), "company": name.get("FullN"), "last": last, "open": u.num(h.get("Open"), "open"),
            "high": u.num(h.get("High"), "high"), "low": u.num(h.get("Low"), "low"), "prev_close": u.num(h.get("PrevClose"), "prev"),
            "change_pct": u.num(str(rate.get("PcChg", "")).replace("+", ""), "pct"), "as_on": _ts(h.get("Ason"), label),
            "category": h.get("Category")}


@guard
def bse_quote(scrip_code: str, *, now: datetime | None = None) -> Result:
    code = str(scrip_code or "").strip()
    if not code.isdigit():
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"bse_quote: bad scrip code {scrip_code!r}")
    got = get(BSE_HEADER.format(code=code), BSE_API, expect="json")
    d = parse_bse_header(got.body)
    if d["scrip_code"] and d["scrip_code"] != code:
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"bse_quote: asked for {code}, got {d['scrip_code']}")
    d["live"] = market_open(now)
    return Result(data=d, as_of=d["as_on"][:10], sources=[got.source("bse")])

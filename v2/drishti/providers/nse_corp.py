"""NSE corporate and market-calendar data from the live API: announcements, corporate actions, credit-rating actions,
the day's large deals (bulk, block, short selling), IPO lists, trading holidays, F&O expiries, and market status (which
carries Gift Nifty). Every parser checks the shape it depends on and refuses a changed layout with a typed error.
"""
from __future__ import annotations

import re
import time
from datetime import date, datetime, timedelta

from ..core.canary import check_fresh, today_ist
from ..core.envelope import DrishtiError, ErrorKind, Result, guard
from ..core.isin import is_valid_isin, normalize_isin
from . import _util as u
from . import nse_live

SOURCE = "nse_api"


def _dmy(d: date) -> str:
    return d.strftime("%d-%m-%Y")


def _isin(v) -> str | None:
    i = normalize_isin(v)
    return i if is_valid_isin(i) else None


def _need(rows: list[dict], keys: tuple[str, ...], label: str) -> None:
    for r in rows:
        missing = [k for k in keys if k not in r]
        if missing:
            raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: row lacks {missing}; layout changed? {str(r)[:120]}")


def _stamp(v, label: str) -> str:
    """'08-Nov-2026 00:00:16' / '07-NOV-2026 10:14:52' -> ISO datetime."""
    s = str(v or "").strip().title()
    for fmt in ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M", "%d-%b-%Y"):
        try:
            return datetime.strptime(s, fmt).isoformat(timespec="seconds")
        except ValueError:
            continue
    raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: cannot read timestamp {v!r}")


def _opt_date(v, label: str) -> str | None:
    s = str(v or "").strip()
    return None if s in ("", "-", "NA", "None") else u.iso_date(s, label)


# --- announcements (L4-8) ---------------------------------------------------------------------------------
def parse_announcements(body: bytes) -> list[dict]:
    label = "nse_announcements"
    rows = nse_live.rows_of(body, label)
    _need(rows, ("seq_id", "symbol", "an_dt", "desc", "attchmntText"), label)
    return [{"source": "NSE", "id": str(r["seq_id"]), "symbol": u.opt_str(r["symbol"]), "isin": _isin(r.get("sm_isin")),
             "scrip_code": None, "company": u.opt_str(r.get("sm_name")), "category": u.opt_str(r["desc"]), "subcategory": None,
             "headline": u.opt_str(r["desc"]), "text": u.opt_str(r["attchmntText"]), "published": _stamp(r["an_dt"], label),
             "url": u.opt_str(r.get("attchmntFile")), "critical": False, "has_xbrl": bool(r.get("hasXbrl"))} for r in rows]


@guard
def announcements(*, days: int = 1, symbol: str | None = None, today: date | None = None, max_age_days: int = 4) -> Result:
    """Announcements broadcast in the last `days` days (all companies, or one symbol)."""
    today = today or today_ist()
    params = {"index": "equities", "from_date": _dmy(today - timedelta(days=days)), "to_date": _dmy(today)}
    if symbol:
        params["symbol"] = symbol.strip().upper()
    got = nse_live.api_get("corporate-announcements", **params)
    rows = parse_announcements(got.body)
    if not rows and symbol:            # a quiet company can have no filing in the window: that is a real answer
        return Result(data=[], as_of=today.isoformat(), sources=[got.source(SOURCE)])
    newest = check_fresh(rows, "published", max_age_days if not symbol else days + max_age_days, "nse_announcements", today=today)
    return Result(data=rows, as_of=newest.isoformat(), sources=[got.source(SOURCE)])


# --- corporate actions (L4-9) ------------------------------------------------------------------------------
_KINDS = (("dividend", r"dividend"), ("bonus", r"bonus"), ("split", r"split|sub-?division"), ("rights", r"\bright(s)? issue\b|\brights\b"),
          ("buyback", r"buy ?back"), ("demerger", r"demerger"), ("distribution", r"distribution"),
          ("interest", r"interest"), ("redemption", r"redemption"), ("agm", r"general meeting|agm|egm"))


def action_kind(subject: str) -> str:
    s = subject.lower()
    return next((k for k, pat in _KINDS if re.search(pat, s)), "other")


def parse_corporate_actions(body: bytes) -> list[dict]:
    label = "nse_corporate_actions"
    rows = nse_live.rows_of(body, label)
    _need(rows, ("symbol", "subject", "exDate", "isin"), label)
    return [{"symbol": r["symbol"].strip().upper(), "isin": _isin(r["isin"]), "company": u.opt_str(r.get("comp")),
             "series": u.opt_str(r.get("series")), "subject": r["subject"].strip(), "kind": action_kind(r["subject"]),
             "ex_date": _opt_date(r["exDate"], f"{label}.exDate"), "record_date": _opt_date(r.get("recDate"), f"{label}.recDate"),
             "bc_start": _opt_date(r.get("bcStartDate"), label), "bc_end": _opt_date(r.get("bcEndDate"), label),
             "face_value": u.num(r.get("faceVal"), "faceVal")} for r in rows]


@guard
def corporate_actions(*, back_days: int = 7, ahead_days: int = 30, today: date | None = None) -> Result:
    """Corporate actions with ex-dates from `back_days` ago to `ahead_days` ahead (upcoming dividends, splits, bonuses)."""
    today = today or today_ist()
    got = nse_live.api_get("corporates-corporateActions", index="equities", from_date=_dmy(today - timedelta(days=back_days)),
                           to_date=_dmy(today + timedelta(days=ahead_days)))
    rows = parse_corporate_actions(got.body)
    u.require_rows(rows, "nse_corporate_actions")
    return Result(data=rows, as_of=today.isoformat(), sources=[got.source(SOURCE)])


# --- credit ratings (L4-12) ------------------------------------------------------------------------------
_RANK = ["D", "C", "CC", "CCC-", "CCC", "CCC+", "B-", "B", "B+", "BB-", "BB", "BB+", "BBB-", "BBB", "BBB+", "A-", "A", "A+",
         "AA-", "AA", "AA+", "AAA"]


def rating_rank(r: str | None) -> int | None:
    """Long-term scale position (AAA highest). Agency prefixes and suffixes like '(CE)' are stripped. None if unrecognised
    (short-term A1+ scales, 'Withdrawn', etc.)."""
    if not r:
        return None
    s = re.sub(r"\(.*?\)|CRISIL|ICRA|CARE|IND|BWR|ACUITE|INFOMERICS|PR\b|\s", "", r.upper())
    s = s.split("/")[0]
    return _RANK.index(s) if s in _RANK else None


def parse_credit_ratings(body: bytes) -> list[dict]:
    label = "nse_credit_ratings"
    rows = nse_live.rows_of(body, label)
    _need(rows, ("AppID", "CompanyName", "ISIN", "NameOfCRAgency", "CreditRating", "RatingAction", "DateofCR", "BroadcastDateTime"), label)
    out = []
    for r in rows:
        cur, prev = u.opt_str(r["CreditRating"]), u.opt_str(r.get("CreditRatingEarlier"))
        rc, rp = rating_rank(cur), rating_rank(prev)
        action = (u.opt_str(r["RatingAction"]) or "").strip()
        direction = ("upgrade" if rc > rp else "downgrade" if rc < rp else "unchanged") if rc is not None and rp is not None else None
        out.append({"app_id": str(r["AppID"]), "symbol": None if r.get("Symbol") in (None, "NOTLISTED") else r["Symbol"].strip().upper(),
                    "isin": _isin(r["ISIN"]), "company": u.opt_str(r["CompanyName"]), "agency": u.opt_str(r["NameOfCRAgency"]),
                    "rating": cur, "outlook": u.opt_str(r.get("Outlook")), "action": action,
                    "rating_date": u.iso_date(r["DateofCR"], f"{label}.DateofCR"), "broadcast": _stamp(r["BroadcastDateTime"], label),
                    "previous_rating": prev, "previous_outlook": u.opt_str(r.get("OutlookEarlier")),
                    "previous_date": _opt_date(r.get("DateOfCREarlier"), label), "direction_by_scale": direction,
                    "listed": r.get("Symbol") not in (None, "NOTLISTED"), "xbrl_url": u.opt_str(r.get("XbrlFileName"))})
    return out


@guard
def credit_ratings(*, today: date | None = None, max_age_days: int = 5) -> Result:
    """Latest credit-rating actions filed with NSE (listed and unlisted issuers). A downgrade can be read even when the
    filer calls it something else: `direction_by_scale` compares the new and earlier ratings on the long-term scale."""
    today = today or today_ist()
    got = nse_live.api_get("corporate-credit-rating", index="equities")
    rows = parse_credit_ratings(got.body)
    newest = check_fresh(rows, "broadcast", max_age_days, "nse_credit_ratings", today=today)
    return Result(data=rows, as_of=newest.isoformat(), sources=[got.source(SOURCE)])


# --- large deals: bulk, block, short selling (L2-6, L2-8) ---------------------------------------------------
_DEAL_PARTS = (("bulk", "BULK_DEALS_DATA", "BULK_DEALS"), ("block", "BLOCK_DEALS_DATA", "BLOCK_DEALS"), ("short", "SHORT_DEALS_DATA", "SHORT_DEALS"))


def parse_large_deals(body: bytes) -> dict:
    import json
    label = "nse_large_deals"
    try:
        doc = json.loads(body)
    except (ValueError, UnicodeDecodeError) as e:
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: not JSON ({type(e).__name__})") from e
    if not isinstance(doc, dict) or "as_on_date" not in doc:
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: no as_on_date; layout changed?")
    as_on = u.iso_date(doc["as_on_date"], f"{label}.as_on_date")
    out = {"as_on": as_on}
    for kind, data_key, count_key in _DEAL_PARTS:
        rows = doc.get(data_key)
        if not isinstance(rows, list):
            raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: {data_key} missing")
        declared = u.integer(doc.get(count_key), count_key)
        if declared is not None and declared != len(rows):      # NSE states the count: a cut-off list must not pass as complete
            raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: {kind} says {declared} deals but lists {len(rows)}")
        out[kind] = [{"kind": kind, "date": u.iso_date(r["date"], f"{label}.date"), "symbol": (r.get("symbol") or "").strip().upper(),
                      "company": u.opt_str(r.get("name")), "client": u.opt_str(r.get("clientName")),
                      "side": u.opt_str(r.get("buySell")), "qty": u.integer(r.get("qty"), "qty"),
                      "price": u.num(r.get("watp"), "watp")} for r in rows]
    return out


@guard
def large_deals(*, today: date | None = None, max_age_days: int = 5) -> Result:
    today = today or today_ist()
    got = nse_live.api_get("snapshot-capital-market-largedeal")
    data = parse_large_deals(got.body)
    newest = check_fresh([data], "as_on", max_age_days, "nse_large_deals", today=today)
    return Result(data=data, as_of=newest.isoformat(), sources=[got.source(SOURCE)])


# --- IPOs (L4-14) -------------------------------------------------------------------------------------------
def _pick(r: dict, *keys):
    for k in keys:
        v = r.get(k)
        if v is not None and str(v).strip() not in ("", "-"):
            return str(v).strip()
    return None


def _ipo_row(r: dict, status: str, label: str) -> dict:
    start, end = _pick(r, "ipoStartDate", "issueStartDate", "biddingStartDate"), _pick(r, "ipoEndDate", "issueEndDate", "biddingEndDate")
    return {"status": status, "symbol": _pick(r, "symbol"), "company": _pick(r, "companyName", "company", "name"),
            "security_type": _pick(r, "securityType", "series", "issueType"),
            "open": u.iso_date(start, f"{label}.start") if start else None, "close": u.iso_date(end, f"{label}.end") if end else None,
            "price_range": _pick(r, "priceRange", "priceBand", "issuePrice"), "issue_price": _pick(r, "issuePrice"),
            "issue_size": _pick(r, "issueSize"), "listing_date": _pick(r, "listingDate"), "raw": r}


def parse_ipos(body: bytes, status: str) -> list[dict]:
    label = f"nse_ipo_{status}"
    return [_ipo_row(r, status, label) for r in nse_live.rows_of(body, label)]


@guard
def ipos(*, today: date | None = None, max_age_days: int = 45) -> Result:
    """Current, upcoming and recent IPOs (mainboard and SME). Current/upcoming can legitimately be empty; the past list
    is the canary (a feed that stopped updating shows no recently closed issue)."""
    today = today or today_ist()
    past_r = nse_live.api_get("public-past-issues")
    past = parse_ipos(past_r.body, "past")
    newest = check_fresh(past, "close", max_age_days, "nse_ipo_past", today=today)
    cur_r = nse_live.api_get("ipo-current-issue")
    up_r = nse_live.api_get("all-upcoming-issues", category="ipo")
    cur, up = parse_ipos(cur_r.body, "current"), parse_ipos(up_r.body, "upcoming")
    recent = [r for r in past if r["close"] and r["close"] >= (today - timedelta(days=30)).isoformat()]
    return Result(data={"current": cur, "upcoming": up, "recent": recent, "past_count": len(past)}, as_of=newest.isoformat(),
                  sources=[past_r.source(SOURCE), cur_r.source(SOURCE), up_r.source(SOURCE)])


# --- holidays and expiries (L0-1) -----------------------------------------------------------------------------
def parse_holidays(body: bytes, segment: str = "CM") -> list[dict]:
    import json
    label = "nse_holidays"
    doc = json.loads(body)
    if not isinstance(doc, dict) or segment not in doc or not isinstance(doc[segment], list):
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: no {segment} segment; layout changed?")
    rows = [{"date": u.iso_date(r["tradingDate"], f"{label}.date"), "segment": segment, "description": u.opt_str(r.get("description"))}
            for r in doc[segment]]
    u.require_rows(rows, label, 5)
    return rows


@guard
def holidays(*, today: date | None = None) -> Result:
    today = today or today_ist()
    got = nse_live.api_get("holiday-master", type="trading")
    cm, fo = parse_holidays(got.body, "CM"), parse_holidays(got.body, "FO")
    years = {r["date"][:4] for r in cm}
    if str(today.year) not in years:
        raise DrishtiError(ErrorKind.STALE, f"nse_holidays: list covers {sorted(years)}, not {today.year}")
    return Result(data=cm + fo, as_of=today.isoformat(), sources=[got.source(SOURCE)])


def parse_expiries(body: bytes, today: date) -> list[str]:
    import json
    label = "nse_expiries"
    doc = json.loads(body)
    raw = doc.get("expiryDates") if isinstance(doc, dict) else None
    if not isinstance(raw, list) or not raw:
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: no expiryDates")
    ex = [u.iso_date(x, label) for x in raw]
    if ex != sorted(set(ex)):
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: expiries not sorted/unique")
    if ex[0] < (today - timedelta(days=3)).isoformat():
        raise DrishtiError(ErrorKind.STALE, f"{label}: nearest expiry {ex[0]} is in the past")
    return ex


@guard
def expiries(symbol: str = "NIFTY", *, today: date | None = None) -> Result:
    today = today or today_ist()
    got = nse_live.api_get("option-chain-contract-info", symbol=symbol)
    ex = parse_expiries(got.body, today)
    return Result(data={"symbol": symbol, "expiries": ex}, as_of=today.isoformat(), sources=[got.source(SOURCE)])


# --- market status + Gift Nifty (L1-5) --------------------------------------------------------------------------
def parse_market_status(body: bytes) -> dict:
    import json
    label = "nse_market_status"
    doc = json.loads(body)
    if not isinstance(doc, dict) or "marketState" not in doc:
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: no marketState")
    cm = next((m for m in doc["marketState"] if m.get("market") == "Capital Market"), None)
    if cm is None:
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: no Capital Market row")
    g = doc.get("giftnifty") or {}
    gift = None
    if g.get("LASTPRICE") not in (None, "", "-"):
        gift = {"last": u.num(g["LASTPRICE"], "gift.last"), "change": u.num(g.get("DAYCHANGE"), "gift.change"),
                "pct": u.num(g.get("PERCHANGE"), "gift.pct"), "expiry": _opt_date(g.get("EXPIRYDATE"), label),
                "timestamp": _stamp(g["TIMESTMP"], label) if g.get("TIMESTMP") else None}
    ind = doc.get("indicativenifty50") or {}
    nifty_close = u.num(ind.get("closingValue") or cm.get("last"), "nifty close")
    out = {"capital_market": u.opt_str(cm.get("marketStatus")), "trade_date": _stamp(cm.get("tradeDate"), label)[:10],
           "nifty_last": u.num(cm.get("last"), "nifty last"), "nifty_close": nifty_close, "gift_nifty": gift}
    if gift and nifty_close:
        out["gift_premium_pts"] = round(gift["last"] - nifty_close, 2)
        out["gift_premium_pct"] = round((gift["last"] / nifty_close - 1) * 100, 3)
    return out


@guard
def market_status(*, today: date | None = None, max_age_hours: int = 96, retry_wait_s: float = 2.0) -> Result:
    """NSE market state plus Gift Nifty vs the last Nifty close (the pre-market gauge). Gift Nifty trades almost round the
    clock, so a timestamp older than `max_age_hours` means the feed stopped."""
    today = today or today_ist()
    got = nse_live.api_get("marketStatus")
    d = parse_market_status(got.body)
    if d["gift_nifty"] is None:           # NSE leaves the block out of some responses; one more ask usually has it
        time.sleep(retry_wait_s)
        got = nse_live.api_get("marketStatus")
        d = parse_market_status(got.body)
    if d["gift_nifty"] is None:
        raise DrishtiError(ErrorKind.EMPTY, "nse_market_status: no Gift Nifty price in the response")
    ts = d["gift_nifty"]["timestamp"]
    if ts and (datetime.combine(today, datetime.min.time()) - datetime.fromisoformat(ts)) > timedelta(hours=max_age_hours):
        raise DrishtiError(ErrorKind.STALE, f"nse_market_status: Gift Nifty last updated {ts}")
    return Result(data=d, as_of=(ts or d["trade_date"])[:10], sources=[got.source(SOURCE)])


# --- board meetings: the results calendar (L0-2) -----------------------------------------------------------
def meeting_kind(purpose: str, desc: str = "") -> str:
    s = f"{purpose} {desc}".lower()
    if "financial result" in s or "quarterly result" in s or "audited result" in s:
        return "results"
    if "fund raising" in s or "fund-raising" in s or "preferential" in s or "qip" in s:
        return "fund_raising"
    if "buyback" in s or "buy back" in s or "buy-back" in s:
        return "buyback"
    if "dividend" in s:
        return "dividend"
    if "bonus" in s or "split" in s or "merger" in s or "amalgamation" in s or "demerger" in s:
        return "capital_change"
    return "other"


def parse_board_meetings(body: bytes) -> list[dict]:
    label = "nse_board_meetings"
    rows = nse_live.rows_of(body, label)
    _need(rows, ("bm_symbol", "bm_date", "bm_purpose", "bm_timestamp"), label)
    out = []
    for r in rows:
        purpose, desc = (r["bm_purpose"] or "").strip(), u.opt_str(r.get("bm_desc"))
        out.append({"exchange": "NSE", "code": r["bm_symbol"].strip().upper(), "symbol": r["bm_symbol"].strip().upper(),
                    "isin": _isin(r.get("sm_isin")), "company": u.opt_str(r.get("sm_name")),
                    "meeting_date": u.iso_date(r["bm_date"], f"{label}.bm_date"), "purpose": purpose or "-",
                    "kind": meeting_kind(purpose, desc or ""), "description": desc, "announced": _stamp(r["bm_timestamp"], label),
                    "url": u.opt_str(r.get("attachment"))})
    return out


@guard
def board_meetings(*, back_days: int = 7, ahead_days: int = 30, today: date | None = None, max_age_days: int = 5) -> Result:
    """Board meetings dated `back_days` ago to `ahead_days` ahead: when each company will declare results, dividends,
    fund raising. Freshness is judged by the newest intimation, since meeting dates lie in the future by design."""
    today = today or today_ist()
    got = nse_live.api_get("corporate-board-meetings", index="equities", from_date=_dmy(today - timedelta(days=back_days)),
                           to_date=_dmy(today + timedelta(days=ahead_days)))
    rows = parse_board_meetings(got.body)
    lo, hi = (today - timedelta(days=back_days + 1)).isoformat(), (today + timedelta(days=ahead_days + 1)).isoformat()
    stray = [r for r in rows if not lo <= r["meeting_date"] <= hi]
    if len(stray) > len(rows) // 20:       # the range must be honoured, or this is some other list
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"nse_board_meetings: {len(stray)} of {len(rows)} meetings outside the asked range")
    newest = check_fresh(rows, lambda r: r["announced"][:10], max_age_days, "nse_board_meetings", today=today)
    return Result(data=rows, as_of=newest.isoformat(), sources=[got.source(SOURCE)])


# --- historical bulk / block / short deals (L2-7 backfill) ------------------------------------------------
HIST_OPTION = {"bulk": "bulk_deals", "block": "block_deals", "short": "short_selling"}
HIST_CAP = 70           # NSE returns at most this many rows per request (seen 8 Nov: 70 bulk and 70 short, oldest first)


def parse_hist_deals(body: bytes, kind: str) -> list[dict]:
    label = f"nse_hist_{kind}"
    rows = nse_live.rows_of(body, label)
    if kind == "short":
        _need(rows, ("SS_DATE", "SS_SYMBOL", "SS_QTY"), label)
        return [{"kind": "short", "date": u.iso_date(r["SS_DATE"], f"{label}.date"), "symbol": r["SS_SYMBOL"].strip().upper(),
                 "company": u.opt_str(r.get("SS_NAME")), "client": None, "side": None, "qty": u.integer(r["SS_QTY"], "qty"),
                 "price": None} for r in rows]
    _need(rows, ("BD_DT_DATE", "BD_SYMBOL", "BD_CLIENT_NAME", "BD_BUY_SELL", "BD_QTY_TRD"), label)
    out = []
    for r in rows:
        side = (r["BD_BUY_SELL"] or "").strip().upper()
        if side not in ("BUY", "SELL"):
            raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: unexpected side {side!r}")
        out.append({"kind": kind, "date": u.iso_date(r["BD_DT_DATE"], f"{label}.date"), "symbol": r["BD_SYMBOL"].strip().upper(),
                    "company": u.opt_str(r.get("BD_SCRIP_NAME")), "client": u.opt_str(r["BD_CLIENT_NAME"]), "side": side,
                    "qty": u.integer(r["BD_QTY_TRD"], "qty"), "price": u.num(r.get("BD_TP_WATP"), "watp")})
    return out


_CSV_COLS = {"short": ["Date", "Symbol", "Security Name", "Quantity"],
             "bulk": ["Date", "Symbol", "Security Name", "Client Name", "Buy / Sell", "Quantity Traded", "Trade Price / Wght. Avg. Price"]}
_CSV_COLS["block"] = _CSV_COLS["bulk"]


def parse_hist_csv(body: bytes, kind: str) -> list[dict]:
    """The same history as the site's 'Download CSV' button. Unlike the JSON answer it is not cut at 70 rows
    (8 Nov 2026: 96 bulk deals for 6 Nov, as in NSE's own snapshot)."""
    label = f"nse_hist_{kind}_csv"
    rows = u.csv_rows(u.decode(body), _CSV_COLS[kind], label)
    if kind == "short":
        return [{"kind": "short", "date": u.iso_date(r["Date"], f"{label}.date"), "symbol": r["Symbol"].upper(),
                 "company": u.opt_str(r["Security Name"]), "client": None, "side": None, "qty": u.integer(r["Quantity"], "qty"),
                 "price": None} for r in rows]
    out = []
    for r in rows:
        side = r["Buy / Sell"].upper()
        if side not in ("BUY", "SELL"):
            raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: unexpected side {side!r}")
        out.append({"kind": kind, "date": u.iso_date(r["Date"], f"{label}.date"), "symbol": r["Symbol"].upper(),
                    "company": u.opt_str(r["Security Name"]), "client": u.opt_str(r["Client Name"]), "side": side,
                    "qty": u.integer(r["Quantity Traded"], "qty"), "price": u.num(r["Trade Price / Wght. Avg. Price"], "watp")})
    return out


def _hist_json(kind: str, start: date, end: date, sources: list) -> list[dict]:
    got = nse_live.api_get("historicalOR/bulk-block-short-deals", optionType=HIST_OPTION[kind], **{"from": _dmy(start), "to": _dmy(end)})
    sources.append(got.source(SOURCE))
    return parse_hist_deals(got.body, kind)


def _hist_day_csv(kind: str, day: date, sources: list) -> list[dict]:
    """One day from the CSV download. An empty file is accepted only when the JSON answer for that day is empty too."""
    try:
        got = nse_live.api_get_text("historicalOR/bulk-block-short-deals", optionType=HIST_OPTION[kind],
                                    **{"from": _dmy(day), "to": _dmy(day)}, csv="true")
        sources.append(got.source(SOURCE))
        return parse_hist_csv(got.body, kind)
    except DrishtiError as e:
        if e.kind not in (ErrorKind.EMPTY,):
            raise
    if _hist_json(kind, day, day, sources):
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"nse_hist_{kind}: the CSV for {day} is empty but the JSON is not")
    return []


def _hist_window(kind: str, start: date, end: date, label: str, sources: list, capped: list) -> list[dict]:
    rows = _hist_json(kind, start, end, sources)
    if len(rows) < HIST_CAP:
        return rows
    if start >= end:                       # one day that fills the JSON cap: take the full day from the CSV
        full = _hist_day_csv(kind, start, sources)
        if len(full) < len(rows):
            capped.append(start.isoformat())
            return rows
        return full
    mid = start + (end - start) // 2
    return _hist_window(kind, start, mid, label, sources, capped) + _hist_window(kind, mid + timedelta(days=1), end, label, sources, capped)


DAILY_CSV_KINDS = ("bulk", "short")      # ~100 a day: past the JSON cap nearly every day, so ask the CSV day by day


@guard
def historical_deals(kind: str, start: date, end: date, *, extra_days: set[str] = frozenset()) -> Result:
    """Bulk, block or short-selling deals between two dates. Bulk and short selling come day by day from the CSV
    download. Block deals (~20 a day) come from the JSON answer, split in halves while NSE's 70-row cap is hit, with any
    day that fills the cap on its own taken from the CSV. A day that cannot be completed is returned with a PARSE_FAIL
    naming it."""
    if kind not in HIST_OPTION:
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"historical_deals: kind must be one of {sorted(HIST_OPTION)}")
    if start > end:
        raise DrishtiError(ErrorKind.PARSE_FAIL, "historical_deals: start after end")
    sources, capped = [], []
    if kind in DAILY_CSV_KINDS:
        rows, d = [], start
        while d <= end:
            if d.weekday() < 5 or d.isoformat() in extra_days:     # weekend special sessions (Muhurat) too
                rows += _hist_day_csv(kind, d, sources)
            d += timedelta(days=1)
    else:
        rows = _hist_window(kind, start, end, f"nse_hist_{kind}", sources, capped)
    stray = [r for r in rows if not start.isoformat() <= r["date"] <= end.isoformat()]
    if stray:
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"nse_hist_{kind}: {len(stray)} deals outside {start}..{end}")
    err = (DrishtiError(ErrorKind.PARSE_FAIL, f"nse_hist_{kind}: NSE capped the answer at {HIST_CAP} rows on {capped} and the CSV "
                                              "did not give more; those days are incomplete") if capped else None)
    return Result(data={"deals": rows, "capped_days": capped, "requests": len(sources)}, as_of=end.isoformat(), sources=sources[:3], error=err)

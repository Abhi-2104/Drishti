"""BSE end-of-day data. Today: the UDiFF cash bhavcopy (plain CSV from www.bseindia.com/download). BSE's
announcements/PIT API and the bulk/block endpoint join this module in later work packages.

The bhavcopy also feeds the security master: its FinInstrmId is the BSE scrip code, next to the ISIN, which is
what lets BSE-only companies (no NSE listing) be resolved at all.
"""
from __future__ import annotations

from datetime import date

from ..core.envelope import DrishtiError, ErrorKind, Result, guard
from ..core.fetch import get
from . import _util as u
from . import udiff
from .profiles import BSE_API, BSE_WWW

SOURCE = "bse"
ANN_URL = ("https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w?pageno={page}&strCat=-1&strPrevDate={frm}"
           "&strScrip={scrip}&strSearch=P&strToDate={to}&strType=C&subcategory=-1")
ATTACH = "https://www.bseindia.com/xml-data/corpfiling/AttachHis/"
MAX_ANN_PAGES = 20
BHAVCOPY_URL = "https://www.bseindia.com/download/BhavCopy/Equity/BhavCopy_BSE_CM_0_0_0_{ymd}_F_0000.CSV"
MIN_BSE_ROWS = 2000  # 5,059 on 2026-11-06


def parse_bhavcopy(body: bytes, *, min_rows: int = MIN_BSE_ROWS) -> list[dict]:
    """BSE UDiFF bhavcopy CSV -> one row per scrip. `scrip_code` (e.g. '500325') is the BSE join key beside ISIN."""
    label = "bse_bhavcopy"
    rows = udiff.parse_cash(u.decode(body), label, min_rows)
    seen: set[str] = set()
    for r in rows:
        code = r["instrument_id"]
        if not code.isdigit():
            raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: scrip code {code!r} is not numeric; layout changed?")
        if code in seen:
            raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: scrip code {code} appears twice")
        seen.add(code)
        r["scrip_code"] = code
    return rows


@guard
def bhavcopy(d: date, *, store=None) -> Result:
    url = BHAVCOPY_URL.format(ymd=d.strftime("%Y%m%d"))
    rows, got = u.load_parsed(store, "bse_bhavcopy", d, lambda: get(url, BSE_WWW, expect="csv"), parse_bhavcopy, SOURCE)
    if rows[0]["trade_date"] != d.isoformat():
        raise DrishtiError(ErrorKind.STALE, f"bse_bhavcopy: file for {d} contains data for {rows[0]['trade_date']}")
    return Result(data=rows, as_of=d.isoformat(), sources=[got.source(SOURCE)])


# --- announcements (L4-8) ------------------------------------------------------------------------------------
def parse_announcements(body: bytes) -> tuple[list[dict], int | None]:
    """AnnSubCategoryGetData JSON -> (rows, total row count BSE says exists across pages)."""
    import json
    label = "bse_announcements"
    try:
        doc = json.loads(body)
    except (ValueError, UnicodeDecodeError) as e:
        kind = ErrorKind.BLOCKED_HTML if body.lstrip()[:1] == b"<" else ErrorKind.PARSE_FAIL
        raise DrishtiError(kind, f"{label}: not JSON ({type(e).__name__})") from e
    if not isinstance(doc, dict) or not isinstance(doc.get("Table"), list):
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: no Table; layout changed?")
    total = None
    t1 = doc.get("Table1")
    if isinstance(t1, list) and t1 and isinstance(t1[0], dict):
        total = u.integer(t1[0].get("ROWCNT"), "ROWCNT")
    out = []
    for r in doc["Table"]:
        for k in ("NEWSID", "SCRIP_CD", "NEWSSUB", "NEWS_DT"):
            if k not in r:
                raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: row lacks {k}")
        att = u.opt_str(r.get("ATTACHMENTNAME"))
        out.append({"source": "BSE", "id": str(r["NEWSID"]), "symbol": None, "isin": None, "scrip_code": str(r["SCRIP_CD"]),
                    "company": u.opt_str(r.get("SLONGNAME")), "category": u.opt_str(r.get("CATEGORYNAME")),
                    "subcategory": u.opt_str(r.get("SUBCATNAME")), "headline": u.opt_str(r["NEWSSUB"]),
                    "text": u.opt_str(r.get("MORE")) or u.opt_str(r.get("HEADLINE")),
                    "published": str(r["NEWS_DT"])[:19], "url": ATTACH + att if att else None,
                    "critical": str(r.get("CRITICALNEWS", "0")).strip() == "1", "has_xbrl": False})
    return out, total


@guard
def announcements(scrip_code: str, *, days: int = 7, today: date | None = None) -> Result:
    """One company's BSE announcements for the last `days` days, all pages. BSE states the total row count; if the pages
    collected do not add up to it, that is an error, not a shorter list."""
    from datetime import timedelta
    from ..core.canary import today_ist
    today = today or today_ist()
    rows, total, page = [], None, 1
    while page <= MAX_ANN_PAGES:
        url = ANN_URL.format(page=page, frm=(today - timedelta(days=days)).strftime("%Y%m%d"), scrip=scrip_code, to=today.strftime("%Y%m%d"))
        got = get(url, BSE_API, expect="json")
        part, t = parse_announcements(got.body)
        total = t if t is not None else total
        rows += part
        if not part or (total is not None and len(rows) >= total):
            break
        page += 1
    if total is not None and len(rows) < total:
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"bse_announcements: collected {len(rows)} of {total} rows")
    return Result(data=rows, as_of=today.isoformat(), sources=[got.source(SOURCE)])


# --- corporate actions and the results calendar (L4-9, L0-2) ----------------------------------------------------
CORP_ACTIONS_URL = ("https://api.bseindia.com/BseIndiaAPI/api/DefaultData/w?ddlcategorys=E&ddlindustrys=&segment=0&strSearch=D"
                    "&Fdate={frm}&TDate={to}")
RESULT_CAL_URL = "https://api.bseindia.com/BseIndiaAPI/api/Corpforthresults/w?fromdate={frm}&todate={to}"


def _rows(body: bytes, label: str) -> list[dict]:
    import json
    try:
        doc = json.loads(body)
    except (ValueError, UnicodeDecodeError) as e:
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: not JSON ({type(e).__name__})") from e
    if isinstance(doc, dict) and isinstance(doc.get("Table"), list):
        doc = doc["Table"]
    if not isinstance(doc, list) or any(not isinstance(r, dict) for r in doc):
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: expected a list of objects, got {type(doc).__name__}")
    return doc


def _opt(v, where: str) -> str | None:
    s = str(v or "").strip()
    return None if s in ("", "-", "NA") else u.iso_date(s, where)


def parse_corporate_actions(body: bytes) -> list[dict]:
    from .nse_corp import action_kind
    label = "bse_corporate_actions"
    rows = _rows(body, label)
    out = []
    for r in rows:
        for k in ("scrip_code", "Purpose", "Ex_date"):
            if k not in r:
                raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: row lacks {k}; layout changed? {str(r)[:120]}")
        code = str(r["scrip_code"]).strip()
        if not code.isdigit():
            raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: scrip code {code!r} is not numeric")
        subject = " ".join(str(r["Purpose"]).split())
        out.append({"scrip_code": code, "bse_symbol": u.opt_str(r.get("short_name")), "company": u.opt_str(r.get("long_name")),
                    "subject": subject, "kind": action_kind(subject), "ex_date": _opt(r["Ex_date"], f"{label}.Ex_date"),
                    "record_date": _opt(r.get("RD_Date"), f"{label}.RD_Date"), "bc_start": _opt(r.get("BCRD_FROM"), label),
                    "bc_end": _opt(r.get("BCRD_TO"), label), "payment_date": _opt(r.get("payment_date"), label)})
    return out


@guard
def corporate_actions(*, back_days: int = 7, ahead_days: int = 30, today: date | None = None) -> Result:
    """BSE equity corporate actions by ex-date. Covers BSE-only companies the NSE list never has."""
    from datetime import timedelta
    from ..core.canary import today_ist
    today = today or today_ist()
    got = get(CORP_ACTIONS_URL.format(frm=(today - timedelta(days=back_days)).strftime("%Y%m%d"),
                                      to=(today + timedelta(days=ahead_days)).strftime("%Y%m%d")), BSE_API, expect="json")
    rows = parse_corporate_actions(got.body)
    u.require_rows(rows, "bse_corporate_actions")
    return Result(data=rows, as_of=today.isoformat(), sources=[got.source(SOURCE)])


def parse_result_calendar(body: bytes) -> list[dict]:
    label = "bse_result_calendar"
    out = []
    for r in _rows(body, label):
        for k in ("scrip_Code", "meeting_date"):
            if k not in r:
                raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: row lacks {k}; layout changed? {str(r)[:120]}")
        code = str(r["scrip_Code"]).strip()
        if not code.isdigit():
            raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: scrip code {code!r} is not numeric")
        out.append({"exchange": "BSE", "code": code, "symbol": u.opt_str(r.get("short_name")), "isin": None,
                    "company": u.opt_str(r.get("Long_Name")), "meeting_date": u.iso_date(r["meeting_date"], f"{label}.meeting_date"),
                    "purpose": "Financial Results", "kind": "results", "description": None, "announced": None, "url": u.opt_str(r.get("URL"))})
    return out


@guard
def result_calendar(*, ahead_days: int = 30, today: date | None = None) -> Result:
    """Companies whose boards meet on results in the next `ahead_days` days, per BSE. BSE gives no intimation time, so
    freshness here means: the list reaches past today and is not empty."""
    from datetime import timedelta
    from ..core.canary import today_ist
    today = today or today_ist()
    got = get(RESULT_CAL_URL.format(frm=today.strftime("%Y%m%d"), to=(today + timedelta(days=ahead_days)).strftime("%Y%m%d")),
              BSE_API, expect="json")
    rows = parse_result_calendar(got.body)
    u.require_rows(rows, "bse_result_calendar")
    if max(r["meeting_date"] for r in rows) < today.isoformat():
        raise DrishtiError(ErrorKind.STALE, "bse_result_calendar: no meeting dated today or later")
    return Result(data=rows, as_of=today.isoformat(), sources=[got.source(SOURCE)])

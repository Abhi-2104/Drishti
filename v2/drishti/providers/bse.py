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


LEGACY_URL = "https://www.bseindia.com/download/BhavCopy/Equity/EQ_ISINCODE_{ddmmyy}.zip"
OLD_URL = "https://www.bseindia.com/download/BhavCopy/Equity/EQ{ddmmyy}_CSV.ZIP"   # before Dec 2016: no ISIN, no date
LEGACY_MIN_ROWS = 1500   # 3,728 on 15 Sep 2021, 4,269 on 15 Mar 2024


MAX_NAME_COMMAS = 2      # a real name holds one or two commas ("TIAA NC, INDIA"); more surplus fields means spliced records
MAX_BROKEN_LINES = 5     # BSE's own file sometimes splices two records into one line plus a fragment (7 Feb and 15 Jul 2022)


def _fix_commas_in_names(text: str, label: str = "bse_bhavcopy_legacy") -> str:
    """Line up the columns of the old BSE file. A line with one or two surplus fields is a company name holding a comma: the
    surplus is folded back into the name (the second field). A line with more surplus, or too few fields, is a record BSE itself
    spliced or cut (seen: two records joined on one line, the rest of one on the next line); such lines are dropped, because
    their values cannot be told apart. Up to MAX_BROKEN_LINES are dropped; more is PARSE_FAIL (the layout changed)."""
    import csv
    import io
    lines = text.splitlines()
    if not lines:
        return text
    width = len(next(csv.reader([lines[0]])))
    out, broken = [lines[0]], []
    for ln in lines[1:]:
        if not ln.strip():
            continue
        f = next(csv.reader([ln]))
        if len(f) > width + MAX_NAME_COMMAS or len(f) < width:
            broken.append(ln[:60])
            continue
        if len(f) > width:
            extra = len(f) - width
            f = [f[0], " ".join(x.strip() for x in f[1:2 + extra])] + f[2 + extra:]
            buf = io.StringIO()
            csv.writer(buf, lineterminator="").writerow(f)
            ln = buf.getvalue()
        out.append(ln)
    if len(broken) > MAX_BROKEN_LINES:
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: {len(broken)} lines with the wrong number of fields (first: {broken[0]!r}); layout changed?")
    return "\n".join(out)


def parse_bhavcopy_legacy(body: bytes, *, day: date | None = None, min_rows: int = LEGACY_MIN_ROWS) -> list[dict]:
    """Pre-UDiFF BSE bhavcopy -> the same row shape as parse_bhavcopy. Three layouts exist (all 14 base columns, then):
      Jul 2017 onward  EQ_ISINCODE_DDMMYY.zip: ISIN_CODE, TRADING_DATE
      Dec 2016-Jun 2017 EQ_ISINCODE_DDMMYY.zip: ISIN_CODE, FILLER1 (no date: `day`, the requested date, is used)
      before Dec 2016  EQDDMMYY_CSV.ZIP: no ISIN, no date (isin None here; bhavcopy_legacy maps scrip codes to ISINs)
    The file has no ticker symbol (only a name cut at 12 characters), so `symbol` is None; ISIN and scrip code are the keys."""
    from datetime import datetime
    label = "bse_bhavcopy_legacy"
    need = ["SC_CODE", "SC_NAME", "SC_GROUP", "OPEN", "HIGH", "LOW", "CLOSE", "LAST", "PREVCLOSE", "NO_TRADES", "NO_OF_SHRS", "NET_TURNOV"]
    rows, seen = [], set()
    for r in u.csv_rows(_fix_commas_in_names(u.decode(u.unzip_single(body, label))), need, label):
        code = r["SC_CODE"]
        if not code.isdigit():
            raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: scrip code {code!r} is not numeric; layout changed?")
        if code in seen:
            raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: scrip code {code} appears twice")
        seen.add(code)
        if r.get("TRADING_DATE"):
            try:
                d = datetime.strptime(r["TRADING_DATE"].title(), "%d-%b-%y").date().isoformat()
            except ValueError:
                raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: cannot read a date from {r['TRADING_DATE'][:20]!r}") from None
        elif day is not None:
            d = day.isoformat()
        else:
            raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: the file has no date column and no date was given")
        rows.append({
            "trade_date": d, "symbol": None, "series": r["SC_GROUP"].strip() or "-", "isin": u.opt_str(r.get("ISIN_CODE")),
            "name": u.opt_str(r["SC_NAME"]), "instrument_id": code, "scrip_code": code,
            "open": u.num(r["OPEN"], "open"), "high": u.num(r["HIGH"], "high"), "low": u.num(r["LOW"], "low"),
            "close": u.num(r["CLOSE"], "close"), "last": u.num(r["LAST"], "last"), "prev_close": u.num(r["PREVCLOSE"], "prev_close"),
            "volume": u.integer(r["NO_OF_SHRS"], "volume"), "turnover": u.num(r["NET_TURNOV"], "turnover"),
            "trades": u.integer(r["NO_TRADES"], "trades"),
        })
    u.require_rows(rows, label, min_rows)
    u.single_date(rows, "trade_date", label)
    u.check_ohlc(rows, label)
    return rows


MISSING_NOTE = ("BSE answers a file that does not exist (a holiday, or not published yet) with a web page, not a 404; "
                "if this repeats on a day NSE traded, it may be a block")


@guard
def bhavcopy_legacy(d: date, *, store=None, isin_map: dict[str, str] | None = None) -> Result:
    """The old BSE file for `d`: EQ_ISINCODE first, then the older EQ..._CSV name. A web page for both names is NOT_FOUND (see
    MISSING_NOTE). Rows of the oldest layout carry no ISIN: `isin_map` (scrip code -> ISIN, from later files) fills it, and rows it
    cannot map are left with isin None (persist_prices drops them, and the backfill counts them)."""
    ddmmyy = d.strftime("%d%m%y")
    parse = lambda b: parse_bhavcopy_legacy(b, day=d, min_rows=LEGACY_MIN_ROWS)   # noqa: E731
    try:
        rows, got = u.load_parsed(store, "bse_bhavcopy_legacy", d, lambda: get(LEGACY_URL.format(ddmmyy=ddmmyy), BSE_WWW, expect="zip"),
                                  parse, SOURCE)
    except DrishtiError as e:
        if e.kind not in (ErrorKind.BLOCKED_HTML, ErrorKind.NOT_FOUND):
            raise
        try:
            rows, got = u.load_parsed(store, "bse_bhavcopy_old", d, lambda: get(OLD_URL.format(ddmmyy=ddmmyy), BSE_WWW, expect="zip"),
                                      parse, SOURCE)
        except DrishtiError as e2:
            if e2.kind in (ErrorKind.BLOCKED_HTML, ErrorKind.NOT_FOUND):
                raise DrishtiError(ErrorKind.NOT_FOUND, f"bse_bhavcopy_legacy {d}: neither file name exists. {MISSING_NOTE}") from None
            raise
    if rows[0]["trade_date"] != d.isoformat():
        raise DrishtiError(ErrorKind.STALE, f"bse_bhavcopy_legacy: file for {d} contains data for {rows[0]['trade_date']}")
    if isin_map:
        for r in rows:
            if r["isin"] is None:
                r["isin"] = isin_map.get(r["scrip_code"])
    return Result(data=rows, as_of=d.isoformat(), sources=[got.source(SOURCE)])


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
    frm, to = (today - timedelta(days=back_days)).isoformat(), (today + timedelta(days=ahead_days)).isoformat()
    inside = [r for r in rows if r["ex_date"] and frm <= r["ex_date"] <= to]
    if not inside:      # seen 8 Nov 2026: past windows all came back with the same upcoming list, i.e. the date range is ignored
        dates = sorted(r["ex_date"] for r in rows if r["ex_date"])
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"bse_corporate_actions: {len(rows)} rows with ex-dates {dates[0] if dates else '-'}.."
                                                  f"{dates[-1] if dates else '-'}, none inside the requested {frm}..{to}: the endpoint ignores its date range")
    return Result(data=inside, as_of=today.isoformat(), sources=[got.source(SOURCE)])


SCRIP_LIST_URL = ("https://api.bseindia.com/BseIndiaAPI/api/ListofScripData/w?Group=&Scripcode=&industry=&segment=Equity&status=Active")
MIN_SCRIPS = 3000      # 5,146 active equity scrips on 2026-11-08


def parse_scrip_list(body: bytes, *, min_rows: int = MIN_SCRIPS) -> list[dict]:
    """Every active BSE equity scrip with its ISIN: maps the scrip codes the (traded-today-only) bhavcopy never shows."""
    import json
    label = "bse_scrip_list"
    try:
        doc = json.loads(u.decode(body))
    except ValueError:
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: not JSON (first bytes {body[:60]!r})") from None
    if not isinstance(doc, list):
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: expected a list, got {type(doc).__name__}")
    out, seen = [], set()
    for r in doc:
        for k in ("SCRIP_CD", "Scrip_Name", "Status", "GROUP", "ISIN_NUMBER", "scrip_id"):
            if k not in r:
                raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: row lacks {k}; layout changed? {str(r)[:120]}")
        code = str(r["SCRIP_CD"]).strip()
        if not code.isdigit():
            raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: scrip code {code!r} is not numeric")
        if code in seen:
            raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: scrip code {code} appears twice")
        seen.add(code)
        out.append({"scrip_code": code, "name": u.opt_str(r["Scrip_Name"]), "status": u.opt_str(r["Status"]), "group": u.opt_str(r["GROUP"]),
                    "isin": u.opt_str(r["ISIN_NUMBER"]), "symbol": u.opt_str(r["scrip_id"]), "face_value": u.num(r.get("FACE_VALUE"), f"{label}.face_value"),
                    "issuer": u.opt_str(r.get("Issuer_Name")), "mktcap": u.num(r.get("Mktcap"), f"{label}.mktcap")})
    u.require_rows(out, label, min_rows)
    return out


@guard
def scrip_list() -> Result:
    got = get(SCRIP_LIST_URL, BSE_API, expect="json")
    rows = parse_scrip_list(got.body)
    from ..core.canary import today_ist
    return Result(data=rows, as_of=today_ist().isoformat(), sources=[got.source(SOURCE)])


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

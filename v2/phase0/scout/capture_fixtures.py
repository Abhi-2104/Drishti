"""Capture one real sample per data source into tests/fixtures/_raw/ (read-only GETs).

Run on your home network, from v2/:
    uv run python phase0/scout/capture_fixtures.py
    uv run python phase0/scout/capture_fixtures.py --date 20261106 --shp-scan 200 --sec-ua "Your Name you@example.com"

All HTTP goes through drishti.core.fetch (rate limits, per-source profiles, typed errors), so this
is also the first live test of core/. One failure never stops the run: every item lands in
MANIFEST.json with its typed error. Raw files are gitignored; trimmed copies get committed later,
per parser. Groups: archive, nse_live, shp, bse, macro, screener, sec  (--only shp --shp-scan 150 ...).
A partial run (--only) merges into the existing MANIFEST.json instead of replacing it.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import platform
import re
import sys
import time
from collections import Counter
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path

from drishti.core import fetch
from drishti.core.canary import IST, check_fresh, parse_date, today_ist
from drishti.core.envelope import DrishtiError, ErrorKind
from drishti.core.fetch import HONEST_UA, Profile, browser_headers

ALL_GROUPS = ("archive", "nse_live", "shp", "bse", "macro", "screener", "sec")

# --- per-source profiles (UA decided per source; see plan section 2) --------------------------------
NSE = Profile("nse", browser_headers({"Referer": "https://www.nseindia.com/"}), timeout=(5.0, 20.0))
# Same name => same pooled session (and cookies) as NSE, but no retries: the homepage may 403 harmlessly.
NSE_WARM = replace(NSE, retries=0)
NSE_ARCHIVE = Profile("nse_archive", browser_headers(), timeout=(5.0, 30.0))
BSE_WWW = Profile("bse_www", browser_headers({"Referer": "https://www.bseindia.com/"}), timeout=(5.0, 30.0))
BSE_API = Profile("bse_api", browser_headers({
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://www.bseindia.com",
    "Referer": "https://www.bseindia.com/",
    "Sec-Fetch-Site": "same-site",
}), timeout=(5.0, 25.0))
FRED = Profile("fred", {"User-Agent": HONEST_UA, "Accept": "text/csv,*/*"}, timeout=(5.0, 30.0))
CFTC = Profile("cftc", {"User-Agent": HONEST_UA, "Accept": "text/plain,*/*"}, timeout=(5.0, 30.0))
YAHOO = Profile("yahoo", browser_headers({"Accept": "application/json"}), timeout=(5.0, 15.0))
SCREENER = Profile("screener", browser_headers(), timeout=(5.0, 20.0))

ARCH = "https://nsearchives.nseindia.com/content"
API = "https://www.nseindia.com/api/"


def d_ymd(d: date) -> str: return d.strftime("%Y%m%d")
def d_dmy(d: date) -> str: return d.strftime("%d%m%Y")
def d_dash(d: date) -> str: return d.strftime("%d-%m-%Y")


class Run:
    def __init__(self, outdir: Path):
        self.out = outdir
        self.items: list[dict] = []
        self.notes: list[str] = []

    def note(self, msg: str) -> None:
        self.notes.append(msg)
        print(f"     note: {msg}")

    def add(self, entry: dict) -> None:
        self.items.append(entry)
        flag = "OK  " if entry["ok"] else "FAIL"
        extra = entry.get("saved_as") or f'{entry.get("error_kind")}: {entry.get("error_detail", "")[:90]}'
        size = f'{entry.get("bytes", 0):>9,}B' if entry["ok"] else " " * 10
        print(f'{flag} {entry["name"]:<34} {size} {entry["seconds"]:>5.1f}s  {extra}')
        if entry.get("freshness"):
            print(f'     freshness: {entry["freshness"]}')


def _try(run: Run, name: str, subdir: str, filename: str, url: str, profile: Profile, expect: str,
         on_blocked=None) -> tuple[fetch.Fetched | None, dict]:
    t0 = time.time()
    entry: dict = {"name": name, "url": url, "profile": profile.name, "ok": False}
    got = None
    try:
        got = fetch.get(url, profile, expect=expect, on_blocked=on_blocked)
        path = run.out / subdir / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(got.body)
        entry.update(ok=True, bytes=len(got.body), sha256=hashlib.sha256(got.body).hexdigest(),
                     saved_as=path.relative_to(run.out).as_posix())
    except DrishtiError as e:
        entry.update(error_kind=str(e.kind), error_detail=e.detail[:200])
    except Exception as e:  # noqa: BLE001 - a capture run must never die on one item
        entry.update(error_kind="UNEXPECTED", error_detail=f"{type(e).__name__}: {str(e)[:200]}")
    entry["seconds"] = round(time.time() - t0, 1)
    return got, entry


def grab(run: Run, name, subdir, filename, url, profile, expect, *, on_blocked=None, note=""):
    got, entry = _try(run, name, subdir, filename, url, profile, expect, on_blocked)
    if note:
        entry["note"] = note
    run.add(entry)
    return got, entry


def grab_dated(run: Run, name, subdir, url_fn, filename_fn, profile, expect, start: date, *, walk: int = 4):
    """Try `start`, then earlier weekdays while the file is NOT_FOUND. Returns (got, date_used or None)."""
    d, tried, last = start, [], None
    while len(tried) <= walk:
        if d.weekday() >= 5:
            d -= timedelta(days=1)
            continue
        got, entry = _try(run, name, subdir, filename_fn(d), url_fn(d), profile, expect)
        tried.append(d_ymd(d))
        last = entry
        if entry["ok"]:
            entry["note"] = f"trade date {d_ymd(d)}" + (f" (walked back from {tried[0]})" if len(tried) > 1 else "")
            run.add(entry)
            return got, d
        if entry.get("error_kind") != str(ErrorKind.NOT_FOUND):
            break
        d -= timedelta(days=1)
    last["note"] = "dates tried: " + ", ".join(tried)
    run.add(last)
    return None, None


# --- helpers ----------------------------------------------------------------------------------------
def rows_of(j):
    if isinstance(j, list):
        return j
    if isinstance(j, dict):
        for k in ("data", "Data", "Table", "rows"):
            if isinstance(j.get(k), list):
                return j[k]
    return []


def check(entry: dict, got, label: str, rows_fn, key, max_age_days: int) -> None:
    """Run the freshness canary on a captured payload and attach the verdict to its manifest entry."""
    if got is None:
        return
    try:
        newest = check_fresh(rows_fn(got), key, max_age_days, label)
        entry["freshness"] = f"OK newest {newest.isoformat()}"
    except DrishtiError as e:
        entry["freshness"] = f"{e.kind}: {e.detail}"
    except Exception as e:  # noqa: BLE001
        entry["freshness"] = f"PARSE_FAIL: {type(e).__name__}: {str(e)[:120]}"
    print(f'     freshness: {entry["freshness"]}')


def json_rows(got):
    return rows_of(json.loads(got.body))


def fred_rows(got):
    rows = list(csv.DictReader(io.StringIO(got.text())))
    date_col = list(rows[0].keys())[0]
    val_col = list(rows[0].keys())[1]
    return [{"d": r[date_col]} for r in rows if r.get(val_col, ".") not in (".", "")]


# --- NSE live session -------------------------------------------------------------------------------
def warm_nse() -> int:
    """Fresh session + cookie warm-up (homepage may 403 while /api/* still works). Returns cookie count."""
    fetch.reset_session(NSE)
    for url in ("https://www.nseindia.com/", "https://www.nseindia.com/option-chain"):
        try:
            fetch.get(url, NSE_WARM, expect="html")
        except DrishtiError:
            pass
    return len(fetch.session(NSE).cookies)


# --- encumbrance (SHP XBRL) scan --------------------------------------------------------------------
TAG_RE = re.compile(
    r"<(?:[\w-]+:)?(NumberOfSharesEncumbered\w*|EncumberedSharesHeldAsPercentageOfTotalNumberOfShares\w*"
    r"|WhetherAnySharesHeldByPromotersAreEncumbered\w+)\b([^>]*)>\s*([^<]*?)\s*<", re.I)


def shp_summary(text: str) -> list[tuple[str, str, str]]:
    out = []
    for m in TAG_RE.finditer(text):
        ctx = re.search(r'contextRef="([^"]+)"', m.group(2))
        out.append((m.group(1), ctx.group(1) if ctx else "", m.group(3)))
    return out


def shp_flags(summary) -> dict[str, bool]:
    return {n: v.strip().lower() == "true" for n, _, v in summary if n.lower().startswith("whether")}


def capture_shp(run: Run, scan: int, today: date) -> None:
    # 1. Vedanta (the verified example: 100% encumbered, NDU + other, no pledge) via its own master rows
    url = API + "corporate-share-holdings-master?index=equities&symbol=VEDL"
    got, _ = grab(run, "shp_master_vedl", "nse_live", "shp_master_vedl.json", url, NSE, "json", on_blocked=warm_nse)
    if got:
        rows = sorted((r for r in json_rows(got) if r.get("xbrl")), key=lambda r: parse_date(r.get("date")) or date.min)
        if rows:
            grab(run, "shp_xbrl_vedl_latest", "shp", "shp_vedl_latest.xml", rows[-1]["xbrl"], NSE_ARCHIVE, "text",
                 note=f'quarter {rows[-1].get("date")}')
    # 2. Market-wide master, then scan a sample of the latest quarter for partial-pledge examples
    frm, to = today - timedelta(days=130), today
    url = API + f"corporate-share-holdings-master?index=equities&from_date={d_dash(frm)}&to_date={d_dash(to)}"
    got, entry = grab(run, "shp_master_market", "nse_live", "shp_master_market.json", url, NSE, "json", on_blocked=warm_nse)
    if not got:
        return
    allrows = json_rows(got)
    check(entry, got, "shp master", json_rows, "date", 130)
    dated = [(parse_date(r.get("date")), r) for r in allrows if str(r.get("xbrl", "")).startswith("http")]
    dated = [(d, r) for d, r in dated if d]
    if not dated:
        run.note("shp scan skipped: no rows with a date and an xbrl link")
        return
    # The as-on date is mostly a quarter-end, but event filings (new listings...) carry odd dates such as
    # 07-NOV-2026. Use the most common date = the quarter nearly everyone filed for, not the newest.
    counts = Counter(d for d, _ in dated)
    latest = counts.most_common(1)[0][0]
    per_symbol = {}
    for d, r in dated:
        if d == latest:
            per_symbol.setdefault(r.get("symbol") or r.get("name") or r["xbrl"], r)
    symbols = sorted(per_symbol)
    stride = max(1, len(symbols) // max(1, scan))
    sample = symbols[::stride][:scan]
    run.note(f"shp scan: quarter {latest}, {len(symbols)} companies, sampling {len(sample)} (stride {stride})")
    hits, clean, n_scanned, n_pledged = [], None, 0, 0
    for sym in sample:
        link = per_symbol[sym]["xbrl"]
        got, e = _try(run, f"shp_scan_{sym}", "shp", "_scan.tmp", link, NSE_ARCHIVE, "text")
        if not got:
            continue
        n_scanned += 1
        summary = shp_summary(got.text())
        flags = shp_flags(summary)
        pledged = any(k.lower().endswith("underpledged") and v for k, v in flags.items())
        n_pledged += pledged
        if pledged and len(hits) < 3:
            hits.append((sym, summary, got))
        elif flags and not any(flags.values()) and clean is None:
            clean = (sym, summary, got)
    run.note(f"pledged in sample: {n_pledged} of {n_scanned} scanned (quarter {latest}; "
             f"other as-on dates ignored: {len(counts) - 1}, e.g. event filings)")
    tmp = run.out / "shp" / "_scan.tmp"
    if tmp.exists():
        tmp.unlink()
    for tag, found in [("pledged", h) for h in hits] + ([("clean", clean)] if clean else []):
        sym, summary, got = found
        safe = re.sub(r"[^A-Za-z0-9_-]", "_", sym)
        path = run.out / "shp" / f"shp_{tag}_{safe}_{latest:%Y%m%d}.xml"
        path.write_bytes(got.body)
        run.add({"name": f"shp_{tag}_{safe}", "url": got.url, "profile": NSE_ARCHIVE.name, "ok": True,
                 "bytes": len(got.body), "sha256": hashlib.sha256(got.body).hexdigest(),
                 "saved_as": path.relative_to(run.out).as_posix(), "seconds": 0.0,
                 "note": f"{tag} example; encumbrance tags: " + "; ".join(f"{n}[{c}]={v}" for n, c, v in summary[:12])})
    if not hits:
        run.note("no company with Pledged=true in the sample; rerun with a larger --shp-scan")


# --- groups -----------------------------------------------------------------------------------------
def grp_archive(run: Run, start: date) -> date:
    _, td = grab_dated(run, "cm_bhavcopy", "nse_archive", lambda d: f"{ARCH}/cm/BhavCopy_NSE_CM_0_0_0_{d_ymd(d)}_F_0000.csv.zip",
                       lambda d: f"BhavCopy_NSE_CM_{d_ymd(d)}.csv.zip", NSE_ARCHIVE, "zip", start)
    td = td or last_weekday(start)
    run.note(f"anchor trade date: {d_ymd(td)}")
    grab_dated(run, "fo_bhavcopy", "nse_archive", lambda d: f"{ARCH}/fo/BhavCopy_NSE_FO_0_0_0_{d_ymd(d)}_F_0000.csv.zip",
               lambda d: f"BhavCopy_NSE_FO_{d_ymd(d)}.csv.zip", NSE_ARCHIVE, "zip", td)
    grab_dated(run, "participant_oi", "nse_archive", lambda d: f"{ARCH}/nsccl/fao_participant_oi_{d_dmy(d)}.csv",
               lambda d: f"fao_participant_oi_{d_dmy(d)}.csv", NSE_ARCHIVE, "csv", td)
    grab_dated(run, "ind_close_all", "nse_archive", lambda d: f"{ARCH}/indices/ind_close_all_{d_dmy(d)}.csv",
               lambda d: f"ind_close_all_{d_dmy(d)}.csv", NSE_ARCHIVE, "csv", td)
    # F&O ban list: filename/location varies; the first candidate that works wins (the manifest says which).
    for i, tpl in enumerate((f"{ARCH}/fo/fo_secban_{{}}.csv", "https://nsearchives.nseindia.com/archives/fo/sec_ban/fo_secban_{}.csv")):
        got, d = grab_dated(run, f"fo_secban_cand{i + 1}", "nse_archive", lambda d, t=tpl: t.format(d_dmy(d)),
                            lambda d: f"fo_secban_{d_dmy(d)}.csv", NSE_ARCHIVE, "csv", td, walk=1)
        if got:
            break
    grab(run, "bulk_deals", "nse_archive", "bulk.csv", f"{ARCH}/equities/bulk.csv", NSE_ARCHIVE, "csv")
    grab(run, "block_deals", "nse_archive", "block.csv", f"{ARCH}/equities/block.csv", NSE_ARCHIVE, "csv")
    grab(run, "equity_master", "nse_archive", "EQUITY_L.csv", f"{ARCH}/equities/EQUITY_L.csv", NSE_ARCHIVE, "csv",
         note="NSE ISIN master (WP6)")
    return td


def grp_nse_live(run: Run, td: date) -> None:
    n = warm_nse()
    run.note(f"NSE session cookies after warm-up: {n}" + ("  (EMPTY JAR: expect failures)" if n == 0 else ""))
    today = today_ist()
    wk = f"from_date={d_dash(today - timedelta(days=7))}&to_date={d_dash(today)}"

    def g(name, filename, path, **kw):
        return grab(run, name, "nse_live", filename, API + path, NSE, "json", on_blocked=warm_nse, **kw)

    got, e = g("fii_dii", "fiidiiTradeReact.json", "fiidiiTradeReact")
    check(e, got, "fii/dii", json_rows, "date", 5)
    g("market_status", "marketStatus.json", "marketStatus", note="Gift Nifty field lives here")
    g("all_indices", "allIndices.json", "allIndices", note="'last' != EOD close for India VIX")
    got, e = g("announcements", "corporate_announcements.json", "corporate-announcements?index=equities")
    g("short_selling", "short_selling.json", "snapshot-capital-market-largedeal")
    g("holidays", "holiday_master.json", "holiday-master?type=trading")
    got, e = g("ipo_past", "ipo_past.json", "public-past-issues")
    check(e, got, "ipo past", json_rows, "ipoEndDate", 45)
    g("ipo_current", "ipo_current.json", "ipo-current-issue", note="empty is legitimate only if no IPO is open")
    g("ipo_upcoming", "ipo_upcoming.json", "all-upcoming-issues?category=ipo")
    g("credit_ratings", "credit_ratings.json", "corporate-credit-rating?index=equities", note="query params unverified")
    g("corporate_actions", "corporate_actions.json", f"corporates-corporateActions?index=equities&{wk}",
      note="query params unverified")
    got, e = g("pit_gg_7d", "pit_gg_7d.json", f"corporates-pit-gg?index=equities&{wk}")
    check(e, got, "pit-gg", json_rows, "broadcastDateTime", 5)
    if got:
        docs = 0
        for r in json_rows(got):
            ix, xm = r.get("ixbrl"), r.get("xmlFileName")
            if str(ix).startswith("http") and docs < 3:
                stem = re.sub(r"[^A-Za-z0-9_.-]", "_", str(ix).rsplit("/", 1)[-1])
                grab(run, f"pit_filing_{docs + 1}_ixbrl", "pit", stem, ix, NSE_ARCHIVE, "text", note=f'{r.get("symbol")}')
                if str(xm).startswith("http"):
                    grab(run, f"pit_filing_{docs + 1}_xml", "pit", re.sub(r"[^A-Za-z0-9_.-]", "_", str(xm).rsplit("/", 1)[-1]),
                         xm, NSE_ARCHIVE, "text", note="structured twin of the iXBRL")
                docs += 1
    got, _ = g("option_contract_info", "option_contract_info.json", "option-chain-contract-info?symbol=NIFTY")
    if got:
        exp = re.findall(r"\d{2}-[A-Za-z]{3}-\d{4}", json.dumps(json.loads(got.body).get("expiryDates", [])))
        if exp:
            g("option_chain_v3_nifty", "option_chain_v3_nifty.json", f"option-chain-v3?type=Indices&symbol=NIFTY&expiry={exp[0]}",
              note=f"expiry {exp[0]} (nearest); after-close snapshot")
        else:
            run.note("no expiry found in option-chain-contract-info; v3 chain skipped")
    g("quote_reliance", "quote_reliance.json",
      "NextApi/apiClient/GetQuoteApi?functionName=getSymbolData&marketType=N&series=EQ&symbol=RELIANCE",
      note="after-close shape only; live prices need a market-hours run")


def grp_shp(run: Run, scan: int) -> None:
    n = warm_nse()
    run.note(f"NSE session cookies after warm-up: {n}" + ("  (EMPTY JAR: expect failures)" if n == 0 else ""))
    capture_shp(run, scan, today_ist())


def grp_bse(run: Run, td: date) -> None:
    grab_dated(run, "bse_bhavcopy_udiff", "bse", lambda d: f"https://www.bseindia.com/download/BhavCopy/Equity/BhavCopy_BSE_CM_0_0_0_{d_ymd(d)}_F_0000.CSV",
               lambda d: f"BhavCopy_BSE_CM_{d_ymd(d)}.csv", BSE_WWW, "csv", td)
    grab(run, "bse_scrip_header", "bse", "scrip_header_500325.json",
         "https://api.bseindia.com/BseIndiaAPI/api/getScripHeaderData/w?Debtflag=&scripcode=500325&seriesid=", BSE_API, "json")
    today = today_ist()
    url = ("https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w?pageno=1&strCat=-1"
           f"&strPrevDate={d_ymd(today - timedelta(days=7))}&strScrip=500325&strSearch=P&strToDate={d_ymd(today)}&strType=C&subcategory=-1")
    grab(run, "bse_announcements", "bse", "announcements_500325.json", url, BSE_API, "json")


def grp_macro(run: Run) -> None:
    for sid, days in (("DTWEXBGS", 14), ("WALCL", 14)):
        got, e = grab(run, f"fred_{sid}", "fred", f"{sid}.csv", f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}", FRED, "csv")
        check(e, got, f"fred {sid}", fred_rows, "d", days)
    grab(run, "cftc_finfut", "cftc", "FinFutWk.txt", "https://www.cftc.gov/dea/newcot/FinFutWk.txt", CFTC, "text")
    grab(run, "yahoo_nsei_6mo", "yahoo", "nsei_6mo.json",
         "https://query1.finance.yahoo.com/v8/finance/chart/%5ENSEI?range=6mo&interval=1d", YAHOO, "json")
    grab(run, "yahoo_cnxit_6mo", "yahoo", "cnxit_6mo.json",
         "https://query1.finance.yahoo.com/v8/finance/chart/%5ECNXIT?range=6mo&interval=1d", YAHOO, "json")
    grab(run, "yahoo_reliance_1d", "yahoo", "reliance_1d_1m.json",
         "https://query1.finance.yahoo.com/v8/finance/chart/RELIANCE.NS?range=1d&interval=1m", YAHOO, "json",
         note="quote freshness only meaningful 09:15-15:30 IST on a trading day (check meta.regularMarketTime)")


def grp_screener(run: Run) -> None:
    grab(run, "screener_reliance", "screener", "reliance_consolidated.html",
         "https://www.screener.in/company/RELIANCE/consolidated/", SCREENER, "html")


def grp_sec(run: Run, sec_ua: str | None) -> None:
    if not sec_ua:
        run.note("sec group skipped: pass --sec-ua \"Your Name you@example.com\" (SEC requires contact info in the UA)")
        return
    prof = Profile("sec", {"User-Agent": sec_ua, "Accept": "application/json"}, timeout=(5.0, 20.0))
    grab(run, "sec_company_tickers", "sec", "company_tickers.json", "https://www.sec.gov/files/company_tickers.json", prof, "json")
    grab(run, "sec_submissions_aapl", "sec", "submissions_AAPL.json", "https://data.sec.gov/submissions/CIK0000320193.json", prof, "json")


def last_weekday(d: date) -> date:
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="Capture raw fixtures from every Drishti data source")
    ap.add_argument("--date", help="last trading day, YYYYMMDD (default: walk back from today IST)")
    ap.add_argument("--outdir", type=Path, default=Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "_raw")
    ap.add_argument("--only", help="comma list of groups: " + ",".join(ALL_GROUPS))
    ap.add_argument("--shp-scan", type=int, default=80, help="how many companies' SHP XBRL to scan for partial pledges")
    ap.add_argument("--sec-ua", help='SEC User-Agent with contact info, e.g. "Name you@example.com"')
    args = ap.parse_args(argv)

    groups = tuple(g.strip() for g in args.only.split(",")) if args.only else ALL_GROUPS
    unknown = [g for g in groups if g not in ALL_GROUPS]
    if unknown:
        ap.error(f"unknown group(s): {unknown}")
    start = datetime.strptime(args.date, "%Y%m%d").date() if args.date else today_ist()
    run = Run(args.outdir)
    run.out.mkdir(parents=True, exist_ok=True)
    print(f"Drishti fixture capture | start {start} | out {run.out} | groups {','.join(groups)}\n")

    td = last_weekday(start)
    if "archive" in groups:
        td = grp_archive(run, start)
    if "nse_live" in groups:
        grp_nse_live(run, td)
    if "shp" in groups:
        grp_shp(run, args.shp_scan)
    if "bse" in groups:
        grp_bse(run, td)
    if "macro" in groups:
        grp_macro(run)
    if "screener" in groups:
        grp_screener(run)
    if "sec" in groups:
        grp_sec(run, args.sec_ua)

    ok = [i for i in run.items if i["ok"]]
    bad = [i for i in run.items if not i["ok"]]
    mpath = run.out / "MANIFEST.json"
    items, trade_date, notes = run.items, d_ymd(td), run.notes
    if args.only and mpath.exists():  # partial rerun: keep earlier results for the other groups
        try:
            old = json.loads(mpath.read_text(encoding="utf-8"))
            names = {i["name"] for i in run.items}
            items = [i for i in old.get("items", []) if i["name"] not in names] + run.items
            notes = old.get("notes", []) + run.notes
            if "archive" not in groups:
                trade_date = old.get("trade_date", trade_date)
        except (ValueError, KeyError, OSError):
            pass
    manifest = {
        "captured_at": datetime.now(IST).isoformat(timespec="seconds"),
        "trade_date": trade_date, "python": platform.python_version(), "platform": platform.platform(),
        "browser_ua": fetch.browser_ua(), "notes": notes, "items": items,
    }
    mpath.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"\n{len(ok)} ok, {len(bad)} failed, {sum(i.get('bytes', 0) for i in ok) / 1e6:.1f} MB saved")
    for i in bad:
        print(f'  FAIL {i["name"]}: {i.get("error_kind")} {i.get("error_detail", "")[:100]}')
    print(f"Manifest: {run.out / 'MANIFEST.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

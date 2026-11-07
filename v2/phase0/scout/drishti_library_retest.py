#!/usr/bin/env python3
"""Drishti usability re-test for third-party NSE/BSE libraries.

Run on YOUR machine (PowerShell, bash, WSL or macOS). Needs `uv` installed.

    # market-hours run (Mon-Fri 10:00-14:00 IST). Date defaults to the last weekday:
    uv run --no-project --with requests python drishti_library_retest.py

    # after-close run, explicit last trading day:
    uv run --no-project --with requests python drishti_library_retest.py --date 20261106

    # only some libraries, quicker:
    uv run --no-project --with requests python drishti_library_retest.py --only nse,bse --reps 1

What it does: for each library it creates an isolated throw-away environment (uv), runs a fixed
list of calls mapped to ledger IDs (see claude/v2-signal-ledger.md) --reps times, --gap seconds
apart, and compares price/flow values against a control fetched with plain `requests` just
before that library's run. Install size is not a criterion. Read-only public endpoints only,
no logins, no keys. Writes retest_results.json and retest_report.md next to this script.

Result classes: OK = non-empty result | EMPTY = returned None or an empty container
(a SILENT BLANK, the failure we care about most) | ERR = raised an exception (loud failure).
"""
import argparse
import datetime as dt
import json
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent

# library key -> pip packages for its isolated environment
LIBS = {
    "nse": ["nse"],
    "bse": ["bse"],
    "nsepython": ["nsepython"],
    "jugaad": ["jugaad-data"],
    "nselib": ["nselib"],
    "aynse": ["aynse"],
    "nsefin": ["nsefin"],
    "bharat": ["Bharat-SM-Data"],
}

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
CHROME_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36")


def last_weekday():
    d = dt.date.today()
    while d.weekday() >= 5:
        d -= dt.timedelta(days=1)
    return d


def market_open_now():
    n = dt.datetime.now(IST)
    return n.weekday() < 5 and dt.time(9, 15) <= n.time() <= dt.time(15, 30)


# ---------------------------------------------------------------- value extraction helpers
def deep_find(obj, keys, depth=0):
    """Find the first numeric value stored under any of `keys` anywhere inside obj."""
    if depth > 6 or obj is None:
        return None
    if isinstance(obj, dict):
        for k in keys:
            if k in obj:
                try:
                    return float(str(obj[k]).replace(",", ""))
                except (TypeError, ValueError):
                    pass
        for v in obj.values():
            r = deep_find(v, keys, depth + 1)
            if r is not None:
                return r
    elif isinstance(obj, (list, tuple)):
        for v in obj[:50]:
            r = deep_find(v, keys, depth + 1)
            if r is not None:
                return r
    elif hasattr(obj, "to_dict"):
        try:
            return deep_find(obj.to_dict("records"), keys, depth + 1)
        except Exception:
            return None
    return None


def ex_ltp(r):
    if isinstance(r, (int, float)):
        return float(r)
    return deep_find(r, ("lastPrice", "LastTradedPrice", "last_price", "ltp", "LTP", "last", "closePrice", "close"))


def ex_underlying(r):
    return deep_find(r, ("underlyingValue", "underlying_value", "underlying"))


def ex_vix(r):
    if isinstance(r, (int, float)):
        return float(r)
    return deep_find(r, ("last", "lastPrice", "indexValue"))


def ex_flow(category):
    def f(r):
        try:
            rows = r.to_dict("records") if hasattr(r, "to_dict") else r
            for row in rows:
                if category in str(row.get("category", "")).upper():
                    return float(str(row.get("netValue")).replace(",", ""))
        except Exception:
            return None
        return None
    return f


# ---------------------------------------------------------------- call lists per library
# each entry: (ledger_id, label, zero-arg callable, extractor-or-None, control_key-or-None, tolerance)
def calls_for(lib, d, tmp):
    ddmmyyyy = d.strftime("%d-%m-%Y")
    d_from = d - dt.timedelta(days=4)
    dtm = dt.datetime.combine(d, dt.time())
    dtm_from = dt.datetime.combine(d_from, dt.time())
    m_start, m_end = dt.date(d.year, d.month, 1), d + dt.timedelta(days=9)
    C = []

    if lib == "nse":
        from nse import NSE
        n = NSE(download_folder=tmp)
        C = [
            ("L4-3", "quote RELIANCE", lambda: n.quote("RELIANCE"), ex_ltp, "nse_ltp", 0.005),
            ("L2-5", "option_chain nifty", lambda: n.option_chain("nifty"), ex_underlying, "nifty_last", 0.005),
            ("L2-6", "bulk_deals", lambda: n.bulk_deals("bulk_deals", d_from, d), None, None, 0),
            ("L2-6", "block_deals", lambda: n.bulk_deals("block_deals", d_from, d), None, None, 0),
            ("L2-8", "short_selling", lambda: n.bulk_deals("short_selling", d_from, d), None, None, 0),
            ("L2-4", "fno_bhavcopy", lambda: n.fno_bhavcopy(d), None, None, 0),
            ("L4-1", "equity_bhavcopy", lambda: n.equity_bhavcopy(d), None, None, 0),
            ("L4-1", "delivery_bhavcopy", lambda: n.delivery_bhavcopy(d), None, None, 0),
            ("L4-8", "announcements RELIANCE", lambda: n.announcements(symbol="RELIANCE", from_date=d - dt.timedelta(days=30), to_date=d), None, None, 0),
            ("L4-9", "actions", lambda: n.actions(from_date=m_start, to_date=m_start + dt.timedelta(days=29)), None, None, 0),
            ("L4-7", "shareholding RELIANCE", lambda: n.shareholding("RELIANCE"), None, None, 0),
            ("L0-2", "board_meetings", lambda: n.board_meetings(from_date=m_start, to_date=m_end), None, None, 0),
            ("L4-5", "financial_results", lambda: n.financial_results(symbol="RELIANCE"), None, None, 0),
            ("L4-14", "list_upcoming_ipo", lambda: n.list_upcoming_ipo(), None, None, 0),
            ("L4-14", "list_current_ipo", lambda: n.list_current_ipo(), None, None, 0),
            ("L4-14", "list_sme", lambda: n.list_sme(), None, None, 0),
            ("L1-2", "vix history", lambda: n.fetch_historical_vix_data(d_from, d), None, None, 0),
            ("L0-1", "holidays", lambda: n.holidays(), None, None, 0),
        ]
        closer = n
    elif lib == "bse":
        from bse import BSE
        b = BSE(download_folder=tmp)
        C = [
            ("L4-3", "quote 500325", lambda: b.quote("500325"), ex_ltp, "yahoo_bse_ltp", 0.005),
            ("L4-8", "announcements 500325", lambda: b.announcements(scripcode="500325", from_date=dtm - dt.timedelta(days=30), to_date=dtm), None, None, 0),
            ("L4-9", "actions", lambda: b.actions(), None, None, 0),
            ("L0-2", "resultCalendar", lambda: b.resultCalendar(), None, None, 0),
            ("L4-2", "gainers (canary)", lambda: b.gainers(by="all"), None, None, 0),
            ("L4-2", "bhavcopyReport (BSE EOD)", lambda: b.bhavcopyReport(dtm, tmp), None, None, 0),
            ("L4-2", "deliveryReport", lambda: b.deliveryReport(dtm, tmp), None, None, 0),
            ("L3-4", "fetchAllIndicesDataByDate", lambda: b.fetchAllIndicesDataByDate(d), None, None, 0),
            ("L4-5", "resultsSnapshot 500325", lambda: b.resultsSnapshot("500325"), None, None, 0),
            ("L4-4", "equityMetaInfo 500325 (ISIN?)", lambda: b.equityMetaInfo("500325"), None, None, 0),
        ]
        closer = b
    elif lib == "nsepython":
        import nsepython as p
        C = [
            ("L2-1", "nse_fiidii (FII net)", lambda: p.nse_fiidii(), ex_flow("FII"), "fii_net", 0.5),
            ("L2-1", "nse_fiidii (DII net)", lambda: p.nse_fiidii(), ex_flow("DII"), "dii_net", 0.5),
            ("L4-3", "nse_quote_ltp RELIANCE", lambda: p.nse_quote_ltp("RELIANCE"), ex_ltp, "nse_ltp", 0.005),
            ("L1-2", "indiavix", lambda: p.indiavix(), ex_vix, "vix_last", 0.02),
            ("L2-5", "nse_optionchain_scrapper", lambda: p.nse_optionchain_scrapper("NIFTY"), None, None, 0),
            ("L2-5", "pcr", lambda: p.pcr(p.nse_optionchain_scrapper("NIFTY")), None, None, 0),
            ("L2-3", "get_fao_participant_oi", lambda: p.get_fao_participant_oi(d.strftime("%d%m%Y")), None, None, 0),
            ("L2-6", "nse_largedeals", lambda: p.nse_largedeals(), None, None, 0),
            ("L4-7", "share_holding", lambda: p.share_holding("RELIANCE"), None, None, 0),
            ("L0-2", "nse_events", lambda: p.nse_events(), None, None, 0),
            ("L1-5", "nse_marketStatus", lambda: p.nse_marketStatus(), None, None, 0),
            ("L4-1", "get_bhavcopy", lambda: p.get_bhavcopy(ddmmyyyy), None, None, 0),
            ("L4-5", "nse_results", lambda: p.nse_results("equities", "Quarterly"), None, None, 0),
            ("X-NSEMCP", "nse official MCP list_tools", lambda: p.nse_mcp_list_tools("bhavcopy"), None, None, 0),
        ]
        closer = None
    elif lib == "jugaad":
        import jugaad_data.bse as jbse
        from jugaad_data import rbi
        from jugaad_data.nse import NSELive, bhavcopy_fo_raw, bhavcopy_udiff_raw, full_bhavcopy_raw
        n = NSELive()
        C = [
            ("L4-3", "stock_quote RELIANCE", lambda: n.stock_quote("RELIANCE"), ex_ltp, "nse_ltp", 0.005),
            ("L4-3", "trade_info RELIANCE", lambda: n.trade_info("RELIANCE"), None, None, 0),
            ("L3-1", "all_indices", lambda: n.all_indices(), None, None, 0),
            ("L1-5", "market_status", lambda: n.market_status(), None, None, 0),
            ("L2-5", "index_option_chain NIFTY", lambda: n.index_option_chain("NIFTY"), ex_underlying, "nifty_last", 0.005),
            ("L2-5", "equities_option_chain RELIANCE", lambda: n.equities_option_chain("RELIANCE"), None, None, 0),
            ("L4-8", "corporate_announcements", lambda: n.corporate_announcements("equities", from_date=d - dt.timedelta(days=30), to_date=d, symbol="RELIANCE"), None, None, 0),
            ("L4-9", "corporate_actions", lambda: n.corporate_actions(segment="equities"), None, None, 0),
            ("L2-6", "block_deal_session", lambda: n.block_deal_session(), None, None, 0),
            ("L4-1", "bhavcopy_udiff_raw", lambda: bhavcopy_udiff_raw(d), None, None, 0),
            ("L4-1", "full_bhavcopy_raw", lambda: full_bhavcopy_raw(d), None, None, 0),
            ("L2-4", "bhavcopy_fo_raw", lambda: bhavcopy_fo_raw(d), None, None, 0),
            ("L1-8", "RBI current_rates", lambda: rbi.RBI().current_rates(), None, None, 0),
            ("L4-3", "BSELive get_scrip_info 500325", lambda: jbse.BSELive().get_scrip_info("500325"), None, None, 0),
        ]
        closer = None
    elif lib == "nselib":
        from nselib import capital_market as c
        from nselib import derivatives as dv
        d_from_s = d_from.strftime("%d-%m-%Y")
        C = [
            ("L4-1", "bhav_copy_equities", lambda: c.bhav_copy_equities(ddmmyyyy), None, None, 0),
            ("L4-1", "bhav_copy_with_delivery", lambda: c.bhav_copy_with_delivery(ddmmyyyy), None, None, 0),
            ("L2-6", "bulk_deal_data (staging host?)", lambda: c.bulk_deal_data(d_from_s, ddmmyyyy), None, None, 0),
            ("L2-6", "block_deals_data (staging host?)", lambda: c.block_deals_data(d_from_s, ddmmyyyy), None, None, 0),
            ("L2-8", "short_selling_data (staging host?)", lambda: c.short_selling_data(d_from_s, ddmmyyyy), None, None, 0),
            ("L1-2", "india_vix_data (staging host?)", lambda: c.india_vix_data(d_from_s, ddmmyyyy), None, None, 0),
            ("L0-2", "event_calendar_for_equity", lambda: c.event_calendar_for_equity(ddmmyyyy, (d + dt.timedelta(days=8)).strftime("%d-%m-%Y")), None, None, 0),
            ("L4-9", "corporate_actions_for_equity", lambda: c.corporate_actions_for_equity(m_start.strftime("%d-%m-%Y"), (m_start + dt.timedelta(days=29)).strftime("%d-%m-%Y")), None, None, 0),
            ("L3-1", "market_watch_all_indices (staging host?)", lambda: c.market_watch_all_indices(), None, None, 0),
            ("L2-3", "fii_derivatives_statistics", lambda: dv.fii_derivatives_statistics(ddmmyyyy), None, None, 0),
            ("L2-3", "participant_wise_open_interest", lambda: dv.participant_wise_open_interest(ddmmyyyy), None, None, 0),
            ("L2-9", "fno_security_in_ban_period", lambda: dv.fno_security_in_ban_period(ddmmyyyy), None, None, 0),
            ("L2-5", "nse_live_option_chain NIFTY", lambda: dv.nse_live_option_chain("NIFTY"), ex_underlying, "nifty_last", 0.005),
            ("L2-4", "fno_bhav_copy", lambda: dv.fno_bhav_copy(ddmmyyyy), None, None, 0),
        ]
        closer = None
    elif lib == "aynse":
        import aynse
        from aynse import NSELive
        n = NSELive()
        C = [
            ("L4-3", "stock_quote RELIANCE", lambda: n.stock_quote("RELIANCE"), ex_ltp, "nse_ltp", 0.005),
            ("L2-5", "index_option_chain NIFTY", lambda: n.index_option_chain("NIFTY"), ex_underlying, "nifty_last", 0.005),
            ("L2-6", "bulk_deals_raw", lambda: aynse.bulk_deals_raw(d_from, d), None, None, 0),
            ("L4-14", "ipo_upcoming_issues", lambda: aynse.ipo_upcoming_issues(), None, None, 0),
            ("L4-14", "ipo_current_issues", lambda: aynse.ipo_current_issues(), None, None, 0),
            ("L1-8", "policy_rate_archive", lambda: aynse.policy_rate_archive(), None, None, 0),
        ]
        closer = None
    elif lib == "nsefin":
        import nsefin
        n = nsefin.get_nse_instance()
        C = [
            ("L2-1", "get_fii_dii_activity (FII net)", lambda: n.get_fii_dii_activity(), ex_flow("FII"), "fii_net", 0.5),
            ("L4-10", "get_insider_trading", lambda: n.get_insider_trading(), None, None, 0),
            ("L2-5", "get_option_chain NIFTY", lambda: n.get_option_chain("NIFTY"), ex_underlying, "nifty_last", 0.005),
            ("L4-8", "get_corporate_announcements", lambda: n.get_corporate_announcements(), None, None, 0),
            ("L3-1", "get_index_snapshot", lambda: n.get_index_snapshot(), None, None, 0),
            ("L0-2", "get_upcoming_results", lambda: n.get_upcoming_results(), None, None, 0),
            ("L4-3", "get_pre_market_info NIFTY", lambda: n.get_pre_market_info("NIFTY"), None, None, 0),
        ]
        closer = None
    elif lib == "bharat":
        # Bharat-SM-Data installs generic top-level package names. We only check it imports in an
        # isolated env and list what it exposes; wiring calls needs a human read of its README.
        def introspect():
            import importlib
            out = {}
            for mod in ("Derivatives", "Fundamentals", "Technical", "Base"):
                try:
                    m = importlib.import_module(mod)
                    out[mod] = [x for x in dir(m) if not x.startswith("_")][:25]
                except Exception as e:
                    out[mod] = f"IMPORT FAIL {type(e).__name__}: {e}"
            return out
        C = [("X-BHARAT", "import + list exposed names", introspect, None, None, 0)]
        closer = None
    else:
        raise SystemExit(f"unknown library {lib}")
    return C, closer


# ---------------------------------------------------------------- worker (runs inside the lib's env)
def classify(r):
    if r is None:
        return "EMPTY"
    try:
        if hasattr(r, "empty") and bool(r.empty):
            return "EMPTY"
        if hasattr(r, "__len__") and len(r) == 0:
            return "EMPTY"
    except Exception:
        pass
    return "OK"


def rows_of(r):
    try:
        return len(r)
    except Exception:
        return None


def worker(args):
    d = dt.datetime.strptime(args.date, "%Y%m%d").date()
    tmp = tempfile.mkdtemp(prefix="drishti_retest_")
    try:
        calls, closer = calls_for(args.worker, d, tmp)
    except BaseException as e:  # import or construction failure = the whole library is unusable
        Path(args.out).write_text(json.dumps({"lib": args.worker, "fatal": f"{type(e).__name__}: {e}", "records": []}), encoding="utf-8")
        print(f"FATAL {args.worker}: {type(e).__name__}: {e}")
        return
    records = []
    for rep in range(args.reps):
        for lid, label, fn, ext, ckey, tol in calls:
            t0 = time.time()
            rec = {"lib": args.worker, "ledger": lid, "label": label, "rep": rep, "ckey": ckey, "tol": tol}
            try:
                r = fn()
                rec["status"] = classify(r)
                rec["rows"] = rows_of(r)
                if ext is not None and rec["status"] == "OK":
                    try:
                        rec["value"] = ext(r)
                    except Exception:
                        rec["value"] = None
            except BaseException as e:
                rec["status"] = "ERR"
                rec["error"] = f"{type(e).__name__}: {str(e)[:160]}"
            rec["secs"] = round(time.time() - t0, 2)
            records.append(rec)
            print(f"  rep{rep} {rec['status']:5s} {lid:7s} {label[:44]:44s} {rec['secs']:5.1f}s {rec.get('error', '')[:70]}", flush=True)
        if rep < args.reps - 1:
            time.sleep(args.gap)
    try:
        if closer is not None and hasattr(closer, "exit"):
            closer.exit()
    except Exception:
        pass
    Path(args.out).write_text(json.dumps({"lib": args.worker, "records": records}), encoding="utf-8")


# ---------------------------------------------------------------- controls (plain requests, orchestrator)
def fetch_controls():
    import requests
    ctl = {"fetched_at": dt.datetime.now(IST).isoformat(timespec="seconds")}
    s = requests.Session()
    H = {"User-Agent": CHROME_UA, "Accept": "*/*", "Accept-Language": "en-US,en;q=0.9", "Referer": "https://www.nseindia.com/option-chain"}
    try:
        s.get("https://www.nseindia.com/option-chain", headers=H, timeout=20)
        time.sleep(1)
        j = s.get("https://www.nseindia.com/api/NextApi/apiClient/GetQuoteApi?functionName=getSymbolData&marketType=N&series=EQ&symbol=RELIANCE", headers=H, timeout=25).json()
        ctl["nse_ltp"] = deep_find(j, ("lastPrice", "LastTradedPrice", "ltp", "last", "closePrice", "close"))
    except Exception as e:
        ctl["nse_ltp_err"] = f"{type(e).__name__}: {str(e)[:100]}"
    try:
        j = s.get("https://www.nseindia.com/api/fiidiiTradeReact", headers=H, timeout=25).json()
        for row in j:
            cat = str(row.get("category", "")).upper()
            if "FII" in cat:
                ctl["fii_net"] = float(str(row["netValue"]).replace(",", ""))
            elif "DII" in cat:
                ctl["dii_net"] = float(str(row["netValue"]).replace(",", ""))
    except Exception as e:
        ctl["flows_err"] = f"{type(e).__name__}: {str(e)[:100]}"
    try:
        j = s.get("https://www.nseindia.com/api/allIndices", headers=H, timeout=25).json()
        for row in j.get("data", []):
            if row.get("index") == "NIFTY 50":
                ctl["nifty_last"] = float(row["last"])
            if row.get("index") == "INDIA VIX":
                ctl["vix_last"] = float(row["last"])
    except Exception as e:
        ctl["indices_err"] = f"{type(e).__name__}: {str(e)[:100]}"
    # --- NSE endpoint health (so a library's EMPTY can be explained): live vs frozen insider-trade endpoints, IPO, pledge
    def _newest_dt(rows, key):
        best = None
        for x in rows:
            for fmt in ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M", "%d-%b-%Y"):
                try:
                    d = dt.datetime.strptime(str(x.get(key, "")).title(), fmt)
                    break
                except ValueError:
                    d = None
            if d and (best is None or d > best):
                best = d
        return best
    for hkey, path, dkey in (("health_pit_live_newest", "corporates-pit-gg?index=equities", "broadcastDateTime"),
                             ("health_pit_old_TCS_newest", "corporates-pit?index=equities&symbol=TCS", "date"),
                             ("health_ipo_past_newest", "public-past-issues", "ipoEndDate")):
        try:
            j = s.get("https://www.nseindia.com/api/" + path, headers=H, timeout=30).json()
            rows = j.get("data", j) if isinstance(j, dict) else j
            nw = _newest_dt(rows, dkey)
            ctl[hkey] = f"{len(rows)} rows, newest {nw:%Y-%m-%d}" if nw else f"{len(rows)} rows, no date"
        except Exception as e:
            ctl[hkey + "_err"] = f"{type(e).__name__}: {str(e)[:100]}"
    for key, sym in (("yahoo_nse_ltp", "RELIANCE.NS"), ("yahoo_bse_ltp", "RELIANCE.BO")):
        try:
            j = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=1d&interval=1m", headers={"User-Agent": CHROME_UA}, timeout=20).json()
            m = j["chart"]["result"][0]["meta"]
            ctl[key] = float(m["regularMarketPrice"])
            ctl[key + "_age_s"] = int(time.time() - m["regularMarketTime"])
        except Exception as e:
            ctl[key + "_err"] = f"{type(e).__name__}: {str(e)[:100]}"
    return ctl


# ---------------------------------------------------------------- orchestrator
def verdict_for(group, ctl):
    """group = list of records for one (lib, ledger, label) across reps."""
    n = len(group)
    ok = [r for r in group if r["status"] == "OK"]
    empty = [r for r in group if r["status"] == "EMPTY"]
    err = [r for r in group if r["status"] == "ERR"]
    match = "n/a"
    ck = group[0].get("ckey")
    if ck and ok:
        cv = ctl.get(ck)
        vals = [r.get("value") for r in ok if r.get("value") is not None]
        if cv is None or not vals:
            match = "UNCHECKED"
        else:
            tol = group[0]["tol"]
            good = all(abs(v - cv) <= max(tol * abs(cv), 0.01 if tol < 0.1 else tol) for v in vals)
            match = "MATCH" if good else f"MISMATCH(lib={vals[0]:.2f} ctl={cv:.2f})"
    if len(ok) == n and not match.startswith("MISMATCH"):
        v = "PASS"
    elif empty and not err:
        v = "SILENT-EMPTY"
    elif err and not ok:
        v = "FAIL"
    else:
        v = "FLAKY"
    secs = [r["secs"] for r in group]
    return v, len(ok), n, match, statistics.median(secs), (err[0]["error"] if err else "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--worker", help=argparse.SUPPRESS)
    ap.add_argument("--out", help=argparse.SUPPRESS)
    ap.add_argument("--date", default=last_weekday().strftime("%Y%m%d"), help="last trading day, YYYYMMDD")
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--gap", type=int, default=20, help="seconds between repetitions")
    ap.add_argument("--only", default="", help="comma list of: " + ",".join(LIBS))
    args = ap.parse_args()

    if args.worker:
        worker(args)
        return

    uv = shutil.which("uv")
    if not uv:
        sys.exit("uv not found. Install: PowerShell  irm https://astral.sh/uv/install.ps1 | iex   |  bash  curl -LsSf https://astral.sh/uv/install.sh | sh")
    chosen = [x for x in (args.only.split(",") if args.only else LIBS) if x]
    mo = market_open_now()
    print(f"Drishti library re-test | trade date {args.date} | now {dt.datetime.now(IST):%Y-%m-%d %H:%M} IST | "
          f"MARKET {'OPEN' if mo else 'CLOSED (live results will not prove market-hours behaviour)'}")
    all_records, controls, fatals = [], {}, {}
    for lib in chosen:
        if lib not in LIBS:
            print(f"skip unknown library {lib}")
            continue
        print(f"\n=== {lib}  ({', '.join(LIBS[lib])})", flush=True)
        controls[lib] = fetch_controls()
        out = HERE / f"retest_{lib}.json"
        if out.exists():
            out.unlink()
        cmd = [uv, "run", "--no-project"]
        for pkg in LIBS[lib]:
            cmd += ["--with", pkg]
        cmd += ["python", str(Path(__file__).resolve()), "--worker", lib, "--date", args.date,
                "--reps", str(args.reps), "--gap", str(args.gap), "--out", str(out)]
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        try:
            subprocess.run(cmd, timeout=1500, env=env)
        except subprocess.TimeoutExpired:
            print(f"  TIMEOUT: {lib} exceeded 25 minutes")
        if out.exists():
            data = json.loads(out.read_text(encoding="utf-8"))
            if data.get("fatal"):
                fatals[lib] = data["fatal"]
            all_records += data["records"]
        else:
            fatals[lib] = "worker produced no output (install or crash). See console above."

    # report
    lines = [f"# Library re-test report", "",
             f"Trade date {args.date}. Run at {dt.datetime.now(IST):%Y-%m-%d %H:%M} IST. Market {'OPEN' if mo else 'CLOSED'}.",
             f"Reps per call: {args.reps}, gap {args.gap}s.", ""]
    lines += ["## Controls (plain requests, fetched before each library)", "", "```", json.dumps(controls, indent=1), "```", ""]
    if fatals:
        lines += ["## Libraries that could not be used at all", ""] + [f"- **{k}**: {v}" for k, v in fatals.items()] + [""]
    lines += ["## Per-call results", "",
              "| Library | Ledger | Call | Verdict | OK/reps | Value vs control | Median s | First error |",
              "|---|---|---|---|---|---|---|---|"]
    groups = {}
    for r in all_records:
        groups.setdefault((r["lib"], r["ledger"], r["label"]), []).append(r)
    summary = {}
    for (lib, lid, label), g in groups.items():
        v, nok, n, match, med, err = verdict_for(g, controls.get(lib, {}))
        lines.append(f"| {lib} | {lid} | {label} | {v} | {nok}/{n} | {match} | {med} | {err[:80]} |")
        summary.setdefault(lib, {}).setdefault(v, 0)
        summary[lib][v] += 1
    lines += ["", "## Summary per library (count of calls by verdict)", ""]
    for lib, s in summary.items():
        lines.append(f"- **{lib}**: " + ", ".join(f"{k} {v}" for k, v in sorted(s.items())))
    lines += ["", "Verdicts: PASS = non-empty every rep and value matches control. SILENT-EMPTY = returned nothing without raising "
              "(the failure we most want to avoid). FAIL = raised every time. FLAKY = mixed. "
              "A library earns a backend slot per ledger ID only where it PASSES in a market-hours run."]
    rep = HERE / "retest_report.md"
    rep.write_text("\n".join(lines), encoding="utf-8")
    (HERE / "retest_results.json").write_text(json.dumps({"controls": controls, "records": all_records, "fatals": fatals}, indent=1), encoding="utf-8")
    print("\n".join(lines))
    print(f"\nSaved: {rep}\n       {HERE / 'retest_results.json'}\nPaste retest_report.md (or this console output) back to Claude.")


if __name__ == "__main__":
    main()

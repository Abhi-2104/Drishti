#!/usr/bin/env python3
"""
Drishti v2 - Phase 0 data-source probe.

Checks, for every signal in the L0-L4 stack, whether it can be fetched from a
public, keyless source and turned into an actual number.

For each source it:
  1. downloads it (3 attempts, polite spacing),
  2. classifies each attempt: OK / BLOCKED / BLOCKED_HTML / NOT_FOUND / EMPTY /
     TIMEOUT / ERROR / PARSE_FAIL  (BLOCKED_HTML = a block/interstitial page
     came back where data was expected - the "silent blank" failure),
  3. parses it and computes one derived value (e.g. Nifty PCR, max pain),
  4. writes phase0_report.md and phase0_results.json next to this script.

No API keys, no accounts. Only dependency: requests.

Works on Windows, macOS, Linux and WSL (Python 3.10+). Run it on a normal
home/office network -- sandboxed or proxied environments block these sites.

Usage (use `python` or `py` on Windows, `python3` elsewhere):
    python -m pip install requests
    python probe.py            # full run, ~3-5 minutes
    python probe.py --quick    # 1 attempt per source
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import platform
import sys
import time
import zipfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

try:
    import requests
except ImportError:  # pragma: no cover
    sys.exit("Missing dependency: run  python -m pip install requests")

# Windows consoles may default to a legacy code page; never crash on output.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")
    except Exception:  # noqa: BLE001 - very old/odd streams
        pass

IST = timezone(timedelta(hours=5, minutes=30))
TIMEOUT = 20
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)
BROWSER_HEADERS = {
    "User-Agent": BROWSER_UA,
    "Accept": "text/html,application/xhtml+xml,application/json,text/csv,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
}
SEC_UA = "Drishti phase0 personal research (non-commercial)"


# --------------------------------------------------------------------------
# Result model
# --------------------------------------------------------------------------
@dataclass
class Attempt:
    status: str
    http: Optional[int]
    latency_ms: int
    detail: str = ""


@dataclass
class ProbeResult:
    layer: str
    signal: str
    source: str
    kind: str  # public-file | official-api | live-site | unofficial-api
    role: str  # primary | fallback
    url: str = ""
    data_date: str = ""
    derived: str = ""
    attempts: list = field(default_factory=list)
    note: str = ""

    @property
    def ok_count(self) -> int:
        return sum(1 for a in self.attempts if a.status == "OK")


class ParseError(Exception):
    pass


class EmptyData(Exception):
    pass


# --------------------------------------------------------------------------
# HTTP helpers
# --------------------------------------------------------------------------
def looks_like_html(body: bytes) -> bool:
    head = body[:400].lstrip().lower()
    return head.startswith(b"<!doctype html") or head.startswith(b"<html") or b"<head" in head


def fetch_once(session, method, url, expect, headers=None, **kw):
    """One request. Returns (Attempt, body_bytes_or_None)."""
    t0 = time.monotonic()
    try:
        r = session.request(method, url, headers=headers, timeout=TIMEOUT, **kw)
    except requests.Timeout:
        return Attempt("TIMEOUT", None, int((time.monotonic() - t0) * 1000), f">{TIMEOUT}s"), None
    except requests.RequestException as e:
        return Attempt("ERROR", None, int((time.monotonic() - t0) * 1000), type(e).__name__ + ": " + str(e)[:120]), None
    ms = int((time.monotonic() - t0) * 1000)
    code = r.status_code
    body = r.content or b""
    if code in (401, 403, 429):
        return Attempt("BLOCKED", code, ms, f"HTTP {code}"), None
    if code == 404:
        return Attempt("NOT_FOUND", code, ms, "HTTP 404"), None
    if code >= 400:
        return Attempt("ERROR", code, ms, f"HTTP {code}"), None
    if not body.strip():
        return Attempt("EMPTY", code, ms, "empty body"), None
    if expect in ("csv", "zip", "json") and looks_like_html(body):
        return Attempt("BLOCKED_HTML", code, ms, "HTML page returned where data was expected"), None
    return Attempt("OK", code, ms), body


def run_probe(res: ProbeResult, session, url, expect, parse: Callable[[bytes], tuple],
              attempts: int, method="GET", headers=None, spacing=1.0, **kw) -> ProbeResult:
    """Fetch `attempts` times; parse the first good body. parse(body) -> (derived, data_date)."""
    res.url = url
    parsed = False
    for i in range(attempts):
        att, body = fetch_once(session, method, url, expect, headers=headers, **kw)
        if att.status == "OK" and body is not None:
            try:
                derived, data_date = parse(body)
                if not parsed:
                    res.derived, res.data_date = derived, data_date
                    parsed = True
            except EmptyData as e:
                att = Attempt("EMPTY", att.http, att.latency_ms, str(e)[:160])
            except Exception as e:  # noqa: BLE001 - classify any parser failure
                att = Attempt("PARSE_FAIL", att.http, att.latency_ms, f"{type(e).__name__}: {str(e)[:160]}")
        res.attempts.append(att)
        if i < attempts - 1:
            time.sleep(spacing)
    return res


def recent_weekdays(n=8):
    d = datetime.now(IST).date()
    out = []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return out


def dated_probe(res, session, url_for_day, expect, parse, attempts, headers=None):
    """For daily archive files: walk back to the latest trading day that exists,
    then repeat the fetch on that URL."""
    tried = []
    for day in recent_weekdays():
        url = url_for_day(day)
        att, body = fetch_once(session, "GET", url, expect, headers=headers)
        tried.append(f"{day:%d-%b}:{att.status}")
        if att.status == "NOT_FOUND":
            time.sleep(0.4)
            continue
        # Found (or a non-404 failure, which is itself the finding): repeat on this URL.
        res.note = "walk-back " + ", ".join(tried)
        return run_probe(res, session, url, expect, parse, attempts, headers=headers)
    res.url = url_for_day(recent_weekdays()[0])
    res.attempts.append(Attempt("NOT_FOUND", 404, 0, "no file in last 8 weekdays"))
    res.note = "walk-back " + ", ".join(tried)
    return res


# --------------------------------------------------------------------------
# Parsers (each returns (derived_text, data_date))
# --------------------------------------------------------------------------
def unzip_first_csv(body: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(body)) as z:
        name = next(n for n in z.namelist() if n.lower().endswith(".csv"))
        return z.read(name).decode("utf-8", errors="replace")


def parse_fo_bhavcopy(body: bytes):
    rows = list(csv.DictReader(io.StringIO(unzip_first_csv(body))))
    if not rows:
        raise EmptyData("no rows")
    nifty = [r for r in rows if r.get("TckrSymb") == "NIFTY" and r.get("FinInstrmTp") == "IDO"]
    if not nifty:
        raise ParseError("no NIFTY index-option rows (columns: %s)" % ",".join(list(rows[0])[:12]))
    trade_date = nifty[0].get("TradDt", "")
    expiry = min(r["XpryDt"] for r in nifty)
    chain = [r for r in nifty if r["XpryDt"] == expiry]
    ce, pe = {}, {}
    for r in chain:
        k = float(r["StrkPric"])
        oi = float(r.get("OpnIntrst") or 0)
        (ce if r["OptnTp"] == "CE" else pe)[k] = (ce if r["OptnTp"] == "CE" else pe).get(k, 0) + oi
    tot_ce, tot_pe = sum(ce.values()), sum(pe.values())
    if tot_ce <= 0:
        raise EmptyData("call OI is zero")
    strikes = sorted(set(ce) | set(pe))
    pain = {K: sum((K - s) * oi for s, oi in ce.items() if s < K) + sum((s - K) * oi for s, oi in pe.items() if s > K)
            for K in strikes}
    max_pain = min(pain, key=pain.get)
    ce_wall = max(ce, key=ce.get)
    pe_wall = max(pe, key=pe.get) if pe else None
    return (f"NIFTY {expiry}: PCR(OI)={tot_pe / tot_ce:.2f}, max pain={max_pain:.0f}, "
            f"call wall={ce_wall:.0f}, put wall={pe_wall:.0f}, {len(rows)} rows"), trade_date


def parse_cm_bhavcopy(body: bytes):
    rows = list(csv.DictReader(io.StringIO(unzip_first_csv(body))))
    if not rows:
        raise EmptyData("no rows")
    eq = [r for r in rows if r.get("SctySrs") == "EQ"]
    rel = next((r for r in eq if r.get("TckrSymb") == "RELIANCE"), None)
    close = rel.get("ClsPric") if rel else "n/a"
    return f"{len(eq)} EQ stocks; RELIANCE close={close}", rows[0].get("TradDt", "")


def parse_participant_oi(body: bytes):
    lines = body.decode("utf-8", errors="replace").splitlines()
    title = lines[0].strip().strip('"') if lines else ""
    start = next(i for i, l in enumerate(lines) if "Client Type" in l)
    rows = list(csv.DictReader(io.StringIO("\n".join(lines[start:]))))
    rows = [{k.strip(): (v or "").strip() for k, v in r.items() if k} for r in rows]
    fii = next(r for r in rows if r.get("Client Type", "").upper() == "FII")
    net = float(fii["Future Index Long"]) - float(fii["Future Index Short"])
    as_on = title.split("as on", 1)[1].strip() if "as on" in title else ""
    return f"FII index futures net = {net:,.0f} contracts", as_on


def parse_index_close_all(body: bytes):
    rows = list(csv.DictReader(io.StringIO(body.decode("utf-8", errors="replace"))))
    if not rows:
        raise EmptyData("no rows")
    by = {r.get("Index Name", "").strip().upper(): r for r in rows}
    it = by.get("NIFTY IT", {}).get("Closing Index Value", "n/a")
    vix = by.get("INDIA VIX", {}).get("Closing Index Value", "not in file")
    return f"{len(rows)} indices; NIFTY IT={it}; INDIA VIX={vix}", rows[0].get("Index Date", "")


def parse_simple_csv_rows(min_rows=0):
    def _p(body: bytes):
        rows = list(csv.reader(io.StringIO(body.decode("utf-8", errors="replace"))))
        data = [r for r in rows[1:] if any(c.strip() for c in r)]
        if len(data) < min_rows:
            raise EmptyData(f"only {len(data)} rows")
        date = data[0][0] if data and data[0] else ""
        return f"{len(data)} rows", date
    return _p


def parse_fred(series):
    def _p(body: bytes):
        rows = list(csv.reader(io.StringIO(body.decode("utf-8", errors="replace"))))
        vals = [r for r in rows[1:] if len(r) >= 2 and r[1] not in (".", "")]
        if not vals:
            raise EmptyData("no observations")
        return f"{series} latest = {vals[-1][1]} ({len(vals)} obs since {vals[0][0]})", vals[-1][0]
    return _p


def parse_yahoo(body: bytes):
    j = json.loads(body)
    res = (j.get("chart") or {}).get("result")
    if not res:
        raise EmptyData(str((j.get("chart") or {}).get("error"))[:120])
    meta = res[0]["meta"]
    ts = datetime.fromtimestamp(meta.get("regularMarketTime", 0), IST)
    return f"{meta.get('symbol')} = {meta.get('regularMarketPrice')}", f"{ts:%Y-%m-%d %H:%M}"


def parse_niftyindices(body: bytes):
    j = json.loads(body)
    data = json.loads(j["d"]) if isinstance(j.get("d"), str) else j.get("d")
    if not data:
        raise EmptyData("no history rows")
    last = data[0]
    close = last.get("CLOSE") or last.get("Close") or last.get("CLOSING_INDEX_VALUE")
    date = last.get("HistoricalDate") or last.get("Date") or ""
    return f"{len(data)} days returned; latest close={close}", str(date)


def parse_json_key(desc: str, fn: Callable[[object], tuple]):
    def _p(body: bytes):
        return fn(json.loads(body))
    _p.__doc__ = desc
    return _p


def parse_html_contains(*needles):
    def _p(body: bytes):
        text = body.decode("utf-8", errors="replace")
        missing = [n for n in needles if n.lower() not in text.lower()]
        if missing:
            raise ParseError("page loaded but missing: " + ", ".join(missing))
        return f"page OK ({len(body) // 1024} KB), contains {', '.join(needles)}", ""
    return _p


def parse_cot(body: bytes):
    lines = body.decode("utf-8", errors="replace").splitlines()
    if not lines:
        raise EmptyData("empty file")
    spx = next((l for l in lines if "E-MINI S&P 500" in l.upper()), None)
    date = spx.split(",")[2].strip().strip('"') if spx and len(spx.split(",")) > 2 else ""
    return f"{len(lines)} contracts; E-mini S&P row {'found' if spx else 'NOT found'}", date


# --------------------------------------------------------------------------
# NSE live site (session warm-up, same approach v1 uses)
# --------------------------------------------------------------------------
def nse_live_session():
    s = requests.Session()
    s.headers.update(BROWSER_HEADERS)
    att, _ = fetch_once(s, "GET", "https://www.nseindia.com", "html")
    ok = att.status == "OK" and len(s.cookies) > 0
    if att.status == "OK" and not s.cookies:
        att = Attempt("BLOCKED_HTML", att.http, att.latency_ms, "homepage loaded but set no cookies")
    return s, att, ok


NSE_API_HEADERS = {"Referer": "https://www.nseindia.com/", "X-Requested-With": "XMLHttpRequest",
                   "Accept": "application/json, text/plain, */*"}


def _fii_dii(j):
    rows = j if isinstance(j, list) else []
    if not rows:
        raise EmptyData("empty list")
    parts = [f"{r.get('category')}: net {r.get('netValue')}" for r in rows]
    return "; ".join(parts), rows[0].get("date", "")


def _all_indices(j):
    data = j.get("data", [])
    vix = next((d for d in data if d.get("index", "").upper() == "INDIA VIX"), None)
    if not data:
        raise EmptyData("no indices")
    return f"{len(data)} indices; INDIA VIX={vix.get('last') if vix else 'n/a'}", j.get("timestamp", "")


def _market_status(j):
    gift = j.get("giftnifty") or {}
    states = [m.get("marketStatus") for m in j.get("marketState", [])][:2]
    g = f"Gift Nifty={gift.get('LASTPRICE')} ({gift.get('TIMESTMP')})" if gift else "Gift Nifty field absent"
    return f"{g}; market={states}", str(gift.get("TIMESTMP", ""))


def _quote(j):
    p = (j.get("priceInfo") or {}).get("lastPrice")
    if p is None:
        raise EmptyData("no priceInfo.lastPrice")
    return f"RELIANCE last={p}", (j.get("metadata") or {}).get("lastUpdateTime", "")


def _announcements(j):
    rows = j if isinstance(j, list) else j.get("data", [])
    if not rows:
        raise EmptyData("no announcements")
    return f"{len(rows)} announcements", rows[0].get("an_dt", "") or rows[0].get("sort_date", "")


def _short_selling(j):
    rows = j.get("SHORT_DEALS_DATA") if isinstance(j, dict) else None
    rows = rows or []
    if not rows:
        raise EmptyData("no SHORT_DEALS_DATA")
    total_qty = sum(int(str(r.get("qty", 0)).replace(",", "") or 0) for r in rows)
    return f"{len(rows)} symbols short-sold; total qty={total_qty}", rows[0].get("date", "")


def _option_chain(j):
    rec = j.get("records") or {}
    data = rec.get("data") or []
    if not data:
        raise EmptyData("records.data empty (typical silent-blank)")
    return f"{len(data)} strikes; underlying={rec.get('underlyingValue')}", rec.get("timestamp", "")


def _bse_ann(j):
    rows = j.get("Table") or []
    if not rows:
        raise EmptyData("Table empty")
    return f"{len(rows)} announcements on page 1", rows[0].get("NEWS_DT", "")


def _edgar(j):
    return f"{j.get('name')}: {len(j.get('filings', {}).get('recent', {}).get('form', []))} recent filings", ""


# --------------------------------------------------------------------------
# The probe list
# --------------------------------------------------------------------------
def run_all(attempts: int):
    results: list[ProbeResult] = []
    arch = requests.Session()
    arch.headers.update(BROWSER_HEADERS)
    arch.headers["Referer"] = "https://www.nseindia.com/"

    def add(r):
        results.append(r)
        a = r.attempts
        print(f"  [{r.ok_count}/{len(a)}] {r.layer} {r.signal} <- {r.source}: "
              f"{a[-1].status if a else '-'} {('| ' + r.derived) if r.derived else ''}", flush=True)

    print("\n== NSE public archive files ==", flush=True)
    add(dated_probe(ProbeResult("L2", "PCR / max pain / OI walls", "NSE F&O bhavcopy (UDiFF)", "public-file", "primary"),
                    arch, lambda d: f"https://nsearchives.nseindia.com/content/fo/BhavCopy_NSE_FO_0_0_0_{d:%Y%m%d}_F_0000.csv.zip",
                    "zip", parse_fo_bhavcopy, attempts))
    add(dated_probe(ProbeResult("L4", "EOD prices (all stocks)", "NSE cash bhavcopy (UDiFF)", "public-file", "primary"),
                    arch, lambda d: f"https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{d:%Y%m%d}_F_0000.csv.zip",
                    "zip", parse_cm_bhavcopy, attempts))
    add(dated_probe(ProbeResult("L2", "Participant OI (FII/Pro/Client)", "NSE archive (nsearchives host)", "public-file", "primary"),
                    arch, lambda d: f"https://nsearchives.nseindia.com/content/nsccl/fao_participant_oi_{d:%d%m%Y}.csv",
                    "csv", parse_participant_oi, attempts))
    add(dated_probe(ProbeResult("L2", "Participant OI (FII/Pro/Client)", "NSE archive (archives host, v1's URL)", "public-file", "fallback"),
                    arch, lambda d: f"https://archives.nseindia.com/content/nsccl/fao_participant_oi_{d:%d%m%Y}.csv",
                    "csv", parse_participant_oi, attempts))
    add(dated_probe(ProbeResult("L3", "All index closes (RRG inputs, India VIX)", "NSE ind_close_all", "public-file", "primary"),
                    arch, lambda d: f"https://nsearchives.nseindia.com/content/indices/ind_close_all_{d:%d%m%Y}.csv",
                    "csv", parse_index_close_all, attempts))
    add(run_probe(ProbeResult("L2", "Bulk deals", "NSE archive bulk.csv", "public-file", "primary"),
                  arch, "https://nsearchives.nseindia.com/content/equities/bulk.csv", "csv", parse_simple_csv_rows(), attempts))
    add(run_probe(ProbeResult("L2", "Block deals", "NSE archive block.csv", "public-file", "primary"),
                  arch, "https://nsearchives.nseindia.com/content/equities/block.csv", "csv", parse_simple_csv_rows(), attempts))

    print("\n== niftyindices.com (index history for seasonality / RRG) ==", flush=True)
    end = datetime.now(IST).date()
    start = end - timedelta(days=45)
    cinfo = "{'name':'NIFTY IT','startDate':'%s','endDate':'%s','indexName':'NIFTY IT'}" % (
        start.strftime("%d-%b-%Y"), end.strftime("%d-%b-%Y"))
    ni = requests.Session()
    ni.headers.update(BROWSER_HEADERS)
    add(run_probe(ProbeResult("L0/L3", "Index history (NIFTY IT, 45d)", "niftyindices.com", "unofficial-api", "primary"),
                  ni, "https://www.niftyindices.com/Backpage.aspx/getHistoricaldatatabletoString", "json",
                  parse_niftyindices, attempts, method="POST",
                  headers={"Content-Type": "application/json; charset=UTF-8",
                           "Referer": "https://www.niftyindices.com/reports/historical-data",
                           "X-Requested-With": "XMLHttpRequest"},
                  data=json.dumps({"cinfo": cinfo})))

    print("\n== FRED keyless CSV (US Fed public data) ==", flush=True)
    fred = requests.Session()
    # FRED (probably Akamai-fronted) tarpits requests whose TLS handshake
    # doesn't match a claimed browser User-Agent (read-timeout, not a clean
    # block). An honest non-browser UA is not fingerprint-mismatched and
    # returns in <1s. See SEC_UA for the same pattern against EDGAR.
    fred.headers.update({"User-Agent": "Drishti phase0 personal research (non-commercial)"})
    for layer, sig, series in [
        ("L1", "Dollar index (broad)", "DTWEXBGS"), ("L1", "US 10Y yield", "DGS10"),
        ("L1", "Brent crude", "DCOILBRENTEU"), ("L1", "US VIX", "VIXCLS"), ("L1", "USD/INR", "DEXINUS"),
        ("L1", "Fed balance sheet", "WALCL"), ("L1", "US M2", "M2SL"),
        ("L1", "HY credit spread", "BAMLH0A0HYM2"), ("L1", "Yield curve 10y-2y", "T10Y2Y"),
    ]:
        add(run_probe(ProbeResult(layer, sig, f"FRED {series}", "official-api", "primary"), fred,
                      f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}", "csv",
                      parse_fred(series), attempts, spacing=0.5))

    print("\n== Yahoo chart API (unofficial, v1's current macro source) ==", flush=True)
    y = requests.Session()
    y.headers.update({"User-Agent": BROWSER_UA})
    for layer, sig, sym in [("L1", "Dollar index", "DX-Y.NYB"), ("L1", "US 10Y yield", "^TNX"),
                            ("L1", "Brent crude", "BZ=F"), ("L1", "USD/INR", "INR=X"),
                            ("L1", "India VIX", "^INDIAVIX"), ("L3", "Nifty 50", "^NSEI"), ("L3", "Nifty IT", "^CNXIT"),
                            ("L1", "Gold", "GC=F"), ("L1", "Copper", "HG=F")]:
        add(run_probe(ProbeResult(layer, sig, f"Yahoo {sym}", "unofficial-api", "fallback"), y,
                      f"https://query1.finance.yahoo.com/v8/finance/chart/{requests.utils.quote(sym)}?range=5d&interval=1d",
                      "json", parse_yahoo, attempts, spacing=0.5))

    # Follow-up checks (26 Sep): can Yahoo close the 2 real gaps left after the main run?
    def _yahoo_history_depth(body):
        j = json.loads(body)
        result = (j.get("chart") or {}).get("result") or []
        if not result:
            raise EmptyData("no chart.result")
        ts = result[0].get("timestamp") or []
        if not ts:
            raise EmptyData("no timestamps")
        first = datetime.fromtimestamp(ts[0], IST).date()
        last = datetime.fromtimestamp(ts[-1], IST).date()
        return f"{len(ts)} daily points, {first} to {last}", str(last)

    add(run_probe(ProbeResult("L0/L3", "Nifty IT 6mo history (niftyindices substitute)", "Yahoo ^CNXIT range=6mo",
                              "unofficial-api", "fallback"), y,
                  "https://query1.finance.yahoo.com/v8/finance/chart/%5ECNXIT?range=6mo&interval=1d",
                  "json", _yahoo_history_depth, attempts, spacing=0.5))

    def _yahoo_quote(body):
        j = json.loads(body)
        result = (j.get("chart") or {}).get("result") or []
        if not result:
            raise EmptyData("no chart.result")
        meta = result[0].get("meta") or {}
        price = meta.get("regularMarketPrice")
        ts = meta.get("regularMarketTime")
        if price is None:
            raise EmptyData("no regularMarketPrice")
        age_s = int(time.time()) - ts if ts else None
        return f"RELIANCE.NS last={price}, age={age_s}s", str(ts or "")

    add(run_probe(ProbeResult("L4", "RELIANCE live-quote substitute", "Yahoo RELIANCE.NS",
                              "unofficial-api", "fallback"), y,
                  "https://query1.finance.yahoo.com/v8/finance/chart/RELIANCE.NS?range=1d&interval=1m",
                  "json", _yahoo_quote, attempts, spacing=0.5))

    print("\n== NSE live site (session warm-up, like v1) ==", flush=True)
    live, warm, warm_ok = nse_live_session()
    warm_res = ProbeResult("-", "NSE live session warm-up", "nseindia.com homepage", "live-site", "primary",
                           url="https://www.nseindia.com", attempts=[warm],
                           derived=f"{len(live.cookies)} cookies set" if warm_ok else "")
    add(warm_res)
    for layer, sig, path, fn, role in [
        ("L2", "FII/DII cash flows", "/api/fiidiiTradeReact", _fii_dii, "primary"),
        ("L1", "India VIX", "/api/allIndices", _all_indices, "fallback"),
        ("L2", "Gift Nifty", "/api/marketStatus", _market_status, "primary"),
        ("L4", "Live quote", "/api/quote-equity?symbol=RELIANCE", _quote, "primary"),
        ("L4", "Announcements", "/api/corporate-announcements?index=equities", _announcements, "fallback"),
        ("L2", "Option chain (live)", "/api/option-chain-indices?symbol=NIFTY", _option_chain, "fallback"),
        ("L2", "Short selling", "/api/snapshot-capital-market-largedeal", _short_selling, "primary"),
    ]:
        add(run_probe(ProbeResult(layer, sig, f"NSE live {path.split('?')[0]}", "live-site", role), live,
                      "https://www.nseindia.com" + path, "json", parse_json_key(sig, fn), attempts,
                      headers=NSE_API_HEADERS, spacing=1.2))

    print("\n== Other official / public sources ==", flush=True)
    today = datetime.now(IST).date()
    prev = today - timedelta(days=7)
    bse = requests.Session()
    bse.headers.update(BROWSER_HEADERS)
    add(run_probe(ProbeResult("L4", "Announcements / insider / pledges", "BSE API (v1 source)", "official-api", "primary"), bse,
                  "https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w?pageno=1&strCat=-1"
                  f"&strPrevDate={prev:%Y%m%d}&strScrip=500325&strSearch=P&strToDate={today:%Y%m%d}&strType=C&subcategory=-1",
                  "json", parse_json_key("bse", _bse_ann), attempts,
                  headers={"Referer": "https://www.bseindia.com/", "Origin": "https://www.bseindia.com",
                           "Accept": "application/json, text/plain, */*"}))
    other = requests.Session()
    other.headers.update(BROWSER_HEADERS)
    add(run_probe(ProbeResult("L2", "FII/FPI flows (official)", "NSDL FPI daily report", "public-file", "fallback"), other,
                  "https://www.fpi.nsdl.co.in/web/Reports/Latest.aspx", "html",
                  parse_html_contains("Equity"), attempts))
    add(run_probe(ProbeResult("L2", "COT positioning", "CFTC financial futures (weekly)", "public-file", "primary"), other,
                  "https://www.cftc.gov/dea/newcot/FinFutWk.txt", "csv", parse_cot, attempts))
    add(run_probe(ProbeResult("L4", "Fundamentals", "Screener.in page", "live-site", "fallback"), other,
                  "https://www.screener.in/company/RELIANCE/consolidated/", "html",
                  parse_html_contains("Market Cap"), attempts, spacing=2.0))
    sec = requests.Session()
    sec.headers.update({"User-Agent": SEC_UA, "Accept": "application/json"})
    add(run_probe(ProbeResult("L4", "US filings / 13F", "SEC EDGAR", "official-api", "primary"), sec,
                  "https://data.sec.gov/submissions/CIK0000320193.json", "json", parse_json_key("edgar", _edgar), attempts))

    # Not probed automatically: no stable keyless machine endpoint known yet.
    for layer, sig, why in [
        ("L1", "India CPI/WPI/IIP", "MoSPI/Office of Economic Adviser publish PDFs/press releases; needs a scraper decision"),
        ("L1", "RBI forex reserves / stance", "RBI WSS + MPC statements are PDFs/HTML; needs a scraper decision"),
        ("L1", "PMI (India/US/China)", "owned by S&P Global/ISM; no public data file"),
        ("L4", "Credit ratings", "agency sites; BSE disclosures partly cover it"),
        ("L4", "Concall transcripts", "come through BSE announcements (PDF attachments)"),
        ("L6", "Holdings incl. SBI (deferred)", "NSDL/CDSL CAS PDF is your own statement; test when L6 un-parks"),
    ]:
        results.append(ProbeResult(layer, sig, "-", "manual", "-", note="NOT PROBED: " + why))
    return results


# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------
def verdict(group):
    probed = [r for r in group if r.attempts]
    if not probed:
        return "RED" if "PMI" in group[0].signal else "GREY"
    best = max(probed, key=lambda r: (r.ok_count == len(r.attempts), r.ok_count))
    if best.ok_count == 0:
        return "RED"
    full = best.ok_count == len(best.attempts)
    if full and best.kind in ("public-file", "official-api"):
        return "GREEN"
    return "YELLOW"


def write_report(results, attempts, out_dir: Path):
    now = datetime.now(IST)
    icon = {"GREEN": "🟢", "YELLOW": "🟡", "RED": "🔴", "GREY": "⚪"}
    groups: dict[str, list] = {}
    for r in results:
        groups.setdefault(f"{r.layer} | {r.signal}", []).append(r)

    lines = [f"# Drishti v2 — Phase 0 probe report", "",
             f"Run: {now:%Y-%m-%d %H:%M} IST · attempts per source: {attempts} · "
             f"python {platform.python_version()} · {platform.system()} {platform.release()}", "",
             "🟢 works from a public/official source every time · 🟡 works but only via scrape/unofficial source "
             "or not every attempt · 🔴 no working source · ⚪ not probed (needs a decision)", "",
             "## Summary by signal", "", "| | Layer | Signal | Best result |", "|---|---|---|---|"]
    for key, group in groups.items():
        v = verdict(group)
        probed = [r for r in group if r.attempts]
        best = max(probed, key=lambda r: r.ok_count) if probed else group[0]
        if probed:
            why = best.derived or f"{best.attempts[-1].status} ({best.attempts[-1].detail[:90]})"
            detail = f"{best.source}: {best.ok_count}/{len(best.attempts)} OK — {why}"
        else:
            detail = best.note
        layer, sig = key.split(" | ", 1)
        lines.append(f"| {icon[v]} | {layer} | {sig} | {detail.replace('|', '/')} |")

    lines += ["", "## Every source", "",
              "| Layer | Signal | Source | Kind | OK | Statuses | Median ms | Data date | Derived / note |",
              "|---|---|---|---|---|---|---|---|---|"]
    for r in results:
        if not r.attempts:
            continue
        sts = ",".join(a.status for a in r.attempts)
        lat = sorted(a.latency_ms for a in r.attempts)[len(r.attempts) // 2]
        info = r.derived or "; ".join(sorted({a.detail for a in r.attempts if a.detail}))[:160]
        lines.append(f"| {r.layer} | {r.signal} | {r.source} | {r.kind} | {r.ok_count}/{len(r.attempts)} | "
                     f"{sts} | {lat} | {r.data_date} | {info.replace('|', '/')} |")

    (out_dir / "phase0_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    payload = [{**{k: v for k, v in asdict(r).items() if k != "attempts"},
                "attempts": [asdict(a) for a in r.attempts]} for r in results]
    (out_dir / "phase0_results.json").write_text(
        json.dumps({"run_at": now.isoformat(), "attempts": attempts, "results": payload}, indent=2), encoding="utf-8")
    return out_dir / "phase0_report.md"


def main():
    ap = argparse.ArgumentParser(description="Drishti v2 phase 0 data-source probe")
    ap.add_argument("--quick", action="store_true", help="1 attempt per source instead of 3")
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent), help="output folder")
    args = ap.parse_args()
    attempts = 1 if args.quick else 3
    print(f"Drishti phase 0 probe — {datetime.now(IST):%Y-%m-%d %H:%M} IST, {attempts} attempt(s) per source")
    results = run_all(attempts)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    path = write_report(results, attempts, out)
    print(f"\nReport written: {path}")
    print(f"Raw results:    {out / 'phase0_results.json'}")


if __name__ == "__main__":
    main()

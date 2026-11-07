"""Drishti tool-scout: tests that need YOUR machine's IP / allowlist.
Run (PowerShell or bash):   uv run --with requests --with curl_cffi python drishti_local_probe.py [YYYYMMDD]
Optional arg = last trading day (default: most recent weekday). Paste the whole output back.
Read-only GETs/POSTs to public endpoints. No logins, no keys.
"""
import sys, json, datetime as dt, io, zipfile, csv, time
import requests
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36")
HONEST_UA = "drishti-scout/0.1 (personal research; contact: set-your-email)"

def last_weekday():
    d = dt.date.today()
    while d.weekday() >= 5: d -= dt.timedelta(days=1)
    return d
D = dt.datetime.strptime(sys.argv[1], "%Y%m%d").date() if len(sys.argv) > 1 else last_weekday()

def run(name, fn):
    t = time.time()
    try: print(f"OK   {name:34s} {time.time()-t:4.1f}s | {fn()}")
    except Exception as e: print(f"FAIL {name:34s} {time.time()-t:4.1f}s | {type(e).__name__}: {str(e)[:170]}")

def yahoo_live():
    r = requests.get("https://query1.finance.yahoo.com/v8/finance/chart/RELIANCE.NS?range=1d&interval=1m",
                     headers={"User-Agent": UA}, timeout=20); r.raise_for_status()
    m = r.json()["chart"]["result"][0]["meta"]
    age = time.time() - m["regularMarketTime"]
    return f"price={m['regularMarketPrice']} age_s={age:.0f} (live if run 09:15-15:30 IST and age < ~60-900s)"

def fred():
    r = requests.get("https://fred.stlouisfed.org/graph/fredgraph.csv?id=DTWEXBGS", headers={"User-Agent": HONEST_UA}, timeout=30)
    r.raise_for_status(); rows = r.text.strip().splitlines(); return f"{len(rows)} rows, last={rows[-1]}"

def bse_bhav():
    u = f"https://www.bseindia.com/download/BhavCopy/Equity/BhavCopy_BSE_CM_0_0_0_{D:%Y%m%d}_F_0000.CSV"
    r = requests.get(u, headers={"User-Agent": UA, "Referer": "https://www.bseindia.com/"}, timeout=40); r.raise_for_status()
    rows = list(csv.DictReader(io.StringIO(r.content.decode("utf-8-sig"))))
    rel = [x for x in rows if x.get("FinInstrmId") == "500325" or x.get("TckrSymb") == "RELIANCE"]
    return f"rows={len(rows)} cols={list(rows[0])[:12]} RELIANCE={ {k: rel[0].get(k) for k in ('ClsPric','TtlTradgVol','ISIN')} if rel else None}"

def bse_delivery():
    u = f"https://www.bseindia.com/BSEDATA/gross/{D:%Y}/SCBSEALL{D:%d%m}.zip"
    r = requests.get(u, headers={"User-Agent": UA, "Referer": "https://www.bseindia.com/"}, timeout=40); r.raise_for_status()
    z = zipfile.ZipFile(io.BytesIO(r.content)); return f"{z.namelist()} {len(r.content)} bytes"

def bse_api_plain():
    r = requests.get("https://api.bseindia.com/BseIndiaAPI/api/getScripHeaderData/w?Debtflag=&scripcode=500325&seriesid=",
                     headers={"User-Agent": UA, "Accept": "application/json, text/plain, */*", "Accept-Language": "en-US,en;q=0.5",
                              "Origin": "https://www.bseindia.com/", "Referer": "https://www.bseindia.com/", "Sec-Fetch-Site": "same-site"},
                     timeout=25, allow_redirects=False)
    return f"status={r.status_code} head={r.text[:80]!r} (403/302 => needs curl_cffi or fresher UA)"

def bse_api_curlcffi():
    from curl_cffi import requests as cr
    r = cr.Session(impersonate="chrome").get("https://api.bseindia.com/BseIndiaAPI/api/getScripHeaderData/w?Debtflag=&scripcode=500325&seriesid=",
        headers={"Origin": "https://www.bseindia.com", "Referer": "https://www.bseindia.com/"}, timeout=25)
    return f"status={r.status_code} head={r.text[:80]!r}"

def nse_mcp(path):
    def f():
        h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream", "User-Agent": HONEST_UA}
        u = "https://mcp.nseindia.in/" + path
        r = requests.post(u, headers=h, timeout=40, json={"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"scout","version":"0"}}})
        sid = r.headers.get("mcp-session-id"); h2 = dict(h); 
        if sid: h2["Mcp-Session-Id"] = sid
        requests.post(u, headers=h2, timeout=20, json={"jsonrpc":"2.0","method":"notifications/initialized"})
        r2 = requests.post(u, headers=h2, timeout=40, json={"jsonrpc":"2.0","id":2,"method":"tools/list"})
        t = r2.text; 
        if "data:" in t: t = "".join(l[5:].strip() for l in t.splitlines() if l.startswith("data:"))
        try: tools = [x["name"] for x in json.loads(t)["result"]["tools"]]
        except Exception: return f"init={r.status_code} NO tools list; tools/list status={r2.status_code} raw={t[:300]!r}"
        return f"init={r.status_code} tools={len(tools)} {tools[:8]}"
    return f

def _legacy_tls_session():
    """MoSPI's server uses an old TLS renegotiation that OpenSSL 3 rejects by default
    ('UNSAFE_LEGACY_RENEGOTIATION_DISABLED'). Setting OP_LEGACY_SERVER_CONNECT (0x4) on the
    client context is the standard, narrow fix. Certificate verification stays ON."""
    from requests.adapters import HTTPAdapter
    from urllib3.util.ssl_ import create_urllib3_context
    def ctx():
        c = create_urllib3_context(); c.options |= 0x4; return c
    class Legacy(HTTPAdapter):
        def init_poolmanager(self, *a, **k):
            k["ssl_context"] = ctx(); return super().init_poolmanager(*a, **k)
        def proxy_manager_for(self, proxy, **k):
            k["ssl_context"] = ctx(); return super().proxy_manager_for(proxy, **k)
    s = requests.Session(); s.mount("https://", Legacy()); return s

def mospi_plain():
    r = requests.get("https://api.mospi.gov.in/api/rbi/getRbiRecords", params={"sub_indicator_code": 1}, headers={"User-Agent": HONEST_UA}, timeout=40)
    return f"status={r.status_code} len={len(r.text)} head={r.text[:140]!r}"

def mospi():
    r = _legacy_tls_session().get("https://api.mospi.gov.in/api/rbi/getRbiRecords", params={"sub_indicator_code": 1}, headers={"User-Agent": HONEST_UA}, timeout=40)
    return f"status={r.status_code} len={len(r.text)} head={r.text[:140]!r}"

def reach(url):
    def f():
        r = requests.get(url, headers={"User-Agent": UA}, timeout=25); return f"status={r.status_code} len={len(r.content)}"
    return f

_NSE = {}
def _nse():
    """One warmed NSE session shared by the checks below."""
    if "s" not in _NSE:
        s = requests.Session(); H = {"User-Agent": UA, "Accept": "application/json, */*", "Referer": "https://www.nseindia.com/option-chain"}
        s.get("https://www.nseindia.com/option-chain", headers=H, timeout=20)
        _NSE["s"], _NSE["H"] = s, H
    return _NSE["s"], _NSE["H"]

def _nse_get(path):
    s, H = _nse(); r = s.get("https://www.nseindia.com/api/" + path, headers=H, timeout=30)
    j = r.json(); return j.get("data", j) if isinstance(j, dict) else j

def _newest(rows, key):
    best = None
    for x in rows:
        for fmt in ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M", "%d-%b-%Y"):
            try: d = dt.datetime.strptime(str(x.get(key, "")).title(), fmt); break
            except ValueError: d = None
        if d and (best is None or d > best): best = d
    return best

def _fresh_or_fail(label, rows, key, max_age_days):
    """Freshness canary: row count alone is NOT proof of life (the old PIT endpoint returns 20 stale rows)."""
    if not rows: raise AssertionError(f"{label}: EMPTY (silent blank)")
    nw = _newest(rows, key)
    if nw is None: raise AssertionError(f"{label}: {len(rows)} rows but no parseable '{key}'")
    age = (dt.datetime.now() - nw).days
    if age > max_age_days: raise AssertionError(f"{label}: STALE {len(rows)} rows, newest {nw:%Y-%m-%d} ({age}d old > {max_age_days}d)")
    return f"{len(rows)} rows, newest {nw:%Y-%m-%d %H:%M}"

def pit_live_feed():
    fd = (D - dt.timedelta(days=7)).strftime("%d-%m-%Y"); td = dt.date.today().strftime("%d-%m-%Y")
    rows = _nse_get(f"corporates-pit-gg?index=equities&from_date={fd}&to_date={td}")
    out = _fresh_or_fail("pit-gg 7d", rows, "broadcastDateTime", 5)
    # can we read the actual trade rows from one filing's iXBRL on nsearchives?
    f = next((x for x in rows if x.get("ixbrl")), None)
    if f:
        t = requests.get(f["ixbrl"], headers={"User-Agent": UA}, timeout=30)
        txt = t.text
        out += f" | iXBRL {f['symbol']} status={t.status_code} len={len(txt)} has_trade_table={'Securities acquired' in txt}"
    return out

def pit_frozen_endpoint():
    # EXPECT FAIL (STALE): the old endpoint returns historic rows only. If this ever passes, NSE revived it.
    rows = _nse_get("corporates-pit?index=equities&symbol=TCS")
    return _fresh_or_fail("corporates-pit TCS", rows, "date", 120)

def pledge_depository_page():
    # EXPECT FAIL (EMPTY): NSE's own Pledged Data page shows 0 companies for 1Y as of 7 Nov 2026.
    fd = (D - dt.timedelta(days=365)).strftime("%d-%m-%Y"); td = D.strftime("%d-%m-%Y")
    rows = _nse_get(f"corporate-pledgedata?index=equities&from_date={fd}&to_date={td}")
    return _fresh_or_fail("corporate-pledgedata 1Y", rows, "date", 400)

def pledge_via_shp_xbrl():
    rows = _nse_get("corporate-share-holdings-master?index=equities&symbol=VEDL")
    out = _fresh_or_fail("SHP VEDL", rows, "date", 130)
    latest = max(rows, key=lambda x: _newest([x], "date") or dt.datetime.min)
    x = requests.get(latest["xbrl"], headers={"User-Agent": UA}, timeout=40).text
    import re
    def fact(tag, ctx="ShareholdingOfPromoterAndPromoterGroup_ContextI"):
        m = re.search(r'<in-bse-shp:%s contextRef="%s"[^>]*>([^<]*)<' % (tag, ctx), x)
        return m.group(1) if m else None
    def flag(tag):
        m = re.search(r'<in-bse-shp:%s contextRef="MainI"[^>]*>([^<]*)<' % tag, x)
        return m.group(1) if m else None
    E = "WhetherAnySharesHeldByPromotersAreEncumberedUnder"
    enc = {"pledge_flag": flag(E + "Pledged"), "ndu_flag": flag(E + "NonDisposalUndertaking"),
           "other_flag": flag("WhetherAnySharesHeldByPromotersAreEncumberedOtherThanByWayOfPledgeOrNDU"),
           "shares_encumbered_total": fact("NumberOfSharesEncumbered"), "ndu_shares": fact("NumberOfSharesEncumberedUnderNonDisposalUndertaking"),
           "other_shares": fact("NumberOfSharesEncumberedUnderOtherEncumbrances"),
           "encumbered_pct_of_promoter": fact("EncumberedSharesHeldAsPercentageOfTotalNumberOfShares")}
    if all(v is None for v in enc.values()): raise AssertionError("SHP XBRL parsed but no encumbrance facts found (schema change?)")
    return f"{out} | latest quarter {latest['date']} promoter%={latest.get('pr_and_prgrp')} encumbrance={enc}"

def ipo_lists():
    past = _nse_get("public-past-issues"); cur = _nse_get("ipo-current-issue"); up = _nse_get("all-upcoming-issues?category=ipo")
    out = _fresh_or_fail("public-past-issues", past, "ipoEndDate", 45)
    return f"{out} | current={len(cur)} upcoming={len(up)} (0 can be genuine; compare with NSE IPO page by eye)"

print("Drishti local probe | trade date", D, "| now", dt.datetime.now().isoformat(timespec="seconds"))
run("yahoo RELIANCE.NS live (mkt hours!)", yahoo_live)
run("FRED DTWEXBGS csv", fred)
run("BSE EOD bhavcopy UDiFF (www host)", bse_bhav)
run("BSE delivery SCBSEALL (www host)", bse_delivery)
run("BSE api plain requests", bse_api_plain)
run("BSE api curl_cffi chrome", bse_api_curlcffi)
run("NSE official MCP: cmmkt", nse_mcp("cmmkt/mcp"))
run("NSE official MCP: bhavcopy", nse_mcp("bhavcopy/cm/mcp"))
run("MoSPI plain requests (expect TLS fail)", mospi_plain)
run("MoSPI with legacy-TLS flag", mospi)
run("RBI WSS page (reserves)", reach("https://rbi.org.in/Scripts/WSSView.aspx?Id=23329"))
run("niftyindices.com", reach("https://www.niftyindices.com/"))
run("NSDL FPI reports", reach("https://www.fpi.nsdl.co.in/web/Reports/Latest.aspx"))
run("S&P Global India PMI press rel.", reach("https://www.pmi.spglobal.com/Public/Home/PressRelease/b5951f0005ca45f0861f810f7e49501a"))
run("PIB press releases", reach("https://pib.gov.in/"))
run("NPCI UPI stats page", reach("https://www.npci.org.in/what-we-do/upi/product-statistics"))
run("IMD", reach("https://mausam.imd.gov.in/"))
run("NSE PIT live feed + iXBRL", pit_live_feed)
run("NSE PIT old endpoint (expect STALE)", pit_frozen_endpoint)
run("NSE pledge depository (expect EMPTY)", pledge_depository_page)
run("NSE pledge via SHP XBRL (VEDL)", pledge_via_shp_xbrl)
run("NSE IPO past/current/upcoming", ipo_lists)

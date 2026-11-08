"""Per-source HTTP profiles (UA, headers, timeouts). The UA policy is decided per source; see PLAN-P1 section 2."""
from __future__ import annotations

from dataclasses import replace

from ..core.fetch import HONEST_UA, Profile, browser_headers

NSE_ARCHIVE = Profile("nse_archive", browser_headers(), timeout=(5.0, 30.0))

BSE_WWW = Profile("bse_www", browser_headers({"Referer": "https://www.bseindia.com/"}), timeout=(5.0, 30.0))

# NSE live API (www.nseindia.com/api/*): needs a warmed cookie session. NSE_WARM shares NSE's session by name but
# never retries, because the homepage may 403 harmlessly while /api/* still works.
NSE = Profile("nse", browser_headers({"Referer": "https://www.nseindia.com/"}), timeout=(5.0, 20.0))
NSE_WARM = replace(NSE, retries=0)

# Public data services that publish for programmatic use: identify ourselves honestly instead of posing as a browser.
FRED = Profile("fred", {"User-Agent": HONEST_UA, "Accept": "text/csv,*/*"}, timeout=(5.0, 30.0))
CFTC = Profile("cftc", {"User-Agent": HONEST_UA, "Accept": "text/plain,*/*"}, timeout=(5.0, 30.0))

# Yahoo chart API: browser-like headers, as in Phase 0 (capture_fixtures.YAHOO).
YAHOO = Profile("yahoo", browser_headers({"Accept": "application/json"}), timeout=(5.0, 15.0))

# BSE JSON API (api.bseindia.com): needs the www Origin/Referer, as in Phase 0 (capture_fixtures.BSE_API).
BSE_API = Profile("bse_api", browser_headers({"Accept": "application/json, text/plain, */*", "Origin": "https://www.bseindia.com",
                                              "Referer": "https://www.bseindia.com/", "Sec-Fetch-Site": "same-site"}), timeout=(5.0, 25.0))

# Screener.in company pages (HTML), browser-like as in Phase 0 (capture_fixtures.SCREENER).
SCREENER = Profile("screener", browser_headers({"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}),
                   timeout=(5.0, 20.0))

# NSDL FPI monitor (www.fpi.nsdl.co.in): a public ASP.NET report page, slow to answer; browser-like as in the 8 Nov probe.
NSDL = Profile("nsdl", browser_headers({"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}),
               timeout=(5.0, 30.0))

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

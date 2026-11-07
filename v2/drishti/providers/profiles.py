"""Per-source HTTP profiles (UA, headers, timeouts). The UA policy is decided per source; see PLAN-P1 section 2."""
from __future__ import annotations

from ..core.fetch import Profile, browser_headers

NSE_ARCHIVE = Profile("nse_archive", browser_headers(), timeout=(5.0, 30.0))

BSE_WWW = Profile("bse_www", browser_headers({"Referer": "https://www.bseindia.com/"}), timeout=(5.0, 30.0))

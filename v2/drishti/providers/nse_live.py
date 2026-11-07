"""NSE live API access (www.nseindia.com/api/*). Shared by every NSE live dataset.

The API needs browser cookies, so a session is warmed first (homepage + /option-chain; the homepage may 403
without harm). A blocked call re-warms the session and retries through core.fetch's backoff. Everything is
profile-neutral: callers pass the path and params, nothing here knows about a portfolio.
"""
from __future__ import annotations

import json
import threading
from typing import Any
from urllib.parse import urlencode

from ..core import fetch as _fetch
from ..core.envelope import DrishtiError, ErrorKind
from ..core.fetch import Fetched
from .profiles import NSE, NSE_WARM

API = "https://www.nseindia.com/api/"
_WARM_URLS = ("https://www.nseindia.com/", "https://www.nseindia.com/option-chain")
_lock = threading.Lock()


def warm() -> int:
    """Start a fresh NSE session and collect cookies. Returns the cookie count (0 = warm-up gave us nothing)."""
    with _lock:
        _fetch.reset_session(NSE)
        for url in _WARM_URLS:
            try:
                _fetch.get(url, NSE_WARM, expect="html")
            except DrishtiError:
                pass  # the homepage is allowed to 403; the API call below is the real test
        return len(_fetch.session(NSE).cookies)


def api_get(path: str, **params: Any) -> Fetched:
    """GET https://www.nseindia.com/api/<path>?<params> as JSON; warms the session first if it has no cookies."""
    if len(_fetch.session(NSE).cookies) == 0:
        warm()
    url = API + path + (("?" + urlencode(params)) if params else "")
    return _fetch.get(url, NSE, expect="json", on_blocked=warm)


def rows_of(body: bytes, label: str) -> list[dict]:
    """The row list inside an NSE JSON payload: a bare list, or {"data": [...]}. Anything else is PARSE_FAIL."""
    try:
        doc = json.loads(body)
    except (ValueError, UnicodeDecodeError) as e:
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: not JSON ({type(e).__name__})") from e
    rows = doc.get("data") if isinstance(doc, dict) else doc
    if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows):
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: expected a list of objects, got {type(rows).__name__}")
    return rows

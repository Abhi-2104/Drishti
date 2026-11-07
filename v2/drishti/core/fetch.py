"""The only HTTP entry point. Providers never import requests or call sleep().

Every call: shared per-host rate limit -> request with the source's own headers
and split (connect, read) timeout -> classify -> bytes, or raise DrishtiError.
Classification mirrors probe.py's fetch_once (proven in Phase 0).
"""
from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Literal
from urllib.parse import urlsplit

import requests

from .cache import TTLCache
from .envelope import DrishtiError, ErrorKind, Source, now_utc
from .ratelimit import RateLimiter, Rule

log = logging.getLogger("drishti.fetch")

Expect = Literal["json", "csv", "zip", "xml", "html", "text"]

# Chrome 153 is what the scout verified against the BSE API, which rejects stale Chrome
# strings (120/131/140 got 403). Override without a code change via DRISHTI_BROWSER_UA.
DEFAULT_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36"
)
# For sources that tarpit spoofed browsers (FRED). No contact details baked in; EDGAR builds its own.
HONEST_UA = "drishti-copilot/0.1 (personal research tool)"

# Both mean "the server refused us": back off, re-warm, retry. (NSE often blocks with a 200 HTML page.)
RETRYABLE = (ErrorKind.BLOCKED, ErrorKind.BLOCKED_HTML)
TRANSIENT = (ErrorKind.NETWORK, ErrorKind.TIMEOUT)  # connection reset / timeout: retried the same way, then raised typed


def browser_ua() -> str:
    return os.environ.get("DRISHTI_BROWSER_UA", "").strip() or DEFAULT_BROWSER_UA


def browser_headers(extra: dict[str, str] | None = None) -> dict[str, str]:
    """A fresh browser-like header set with the current UA, for building a Profile. `extra` adds/overrides."""
    h = {
        "User-Agent": browser_ua(),
        "Accept": "text/html,application/xhtml+xml,application/json,text/csv,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
    }
    h.update(extra or {})
    return h


def default_limiter() -> RateLimiter:
    """Plan section 4 starting values. A factory so tests can check the real rules."""
    return RateLimiter(
        rules={
            "nseindia.com": Rule(0.5, jitter=0.25),  # www / nsearchives / archives share one slot
            "bseindia.com": Rule(0.5),               # api. and www. (UDiFF bhavcopy) share one slot
            "query1.finance.yahoo.com": Rule(0.3),
            "fred.stlouisfed.org": Rule(0.5),
            "sec.gov": Rule(0.1),  # SEC's published limit: 10 req/s, www + data share it
        },
        default=Rule(1.0),
    )


limiter = default_limiter()
cache = TTLCache()
BACKOFF_BASE = 1.0  # seconds; 1, 2, 4... on BLOCKED
_sleep = time.sleep  # the only sleep outside the limiter; patched in tests


@dataclass(frozen=True)
class Profile:
    """How one source is fetched. The User-Agent is decided per source — no global default."""
    name: str
    headers: dict[str, str]
    timeout: tuple[float, float] = (5.0, 15.0)  # (connect, read)
    retries: int = 2  # extra attempts after BLOCKED / BLOCKED_HTML, with exponential backoff

    def __post_init__(self):
        if not self.headers.get("User-Agent"):
            raise ValueError(f"profile {self.name!r} must set its own User-Agent")


@dataclass(frozen=True)
class Fetched:
    url: str
    body: bytes
    status: int
    fetched_at: datetime = field(default_factory=now_utc)

    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")

    def source(self, name: str) -> Source:
        return Source(name, self.url, self.fetched_at)


_sessions: dict[str, requests.Session] = {}
_sessions_lock = threading.Lock()


def session(profile: Profile) -> requests.Session:
    """One pooled session per profile (keeps cookies, e.g. NSE live)."""
    with _sessions_lock:
        s = _sessions.get(profile.name)
        if s is None:
            s = _sessions[profile.name] = requests.Session()
            s.headers.update(profile.headers)
        return s


def reset_session(profile: Profile) -> None:
    """Drop the session (and its cookies); the next call starts fresh."""
    with _sessions_lock:
        s = _sessions.pop(profile.name, None)
    if s is not None:
        s.close()


def looks_like_html(body: bytes) -> bool:
    head = body[:400].lstrip().lower()
    return head.startswith(b"<!doctype html") or head.startswith(b"<html") or b"<head" in head


def _once(profile: Profile, url: str, expect: Expect, headers: dict | None) -> Fetched:
    connect, read = profile.timeout
    try:
        r = session(profile).get(url, headers=headers, timeout=profile.timeout)
    except requests.ConnectTimeout:
        raise DrishtiError(ErrorKind.TIMEOUT, f"connect >{connect}s {url}")
    except requests.Timeout:
        raise DrishtiError(ErrorKind.TIMEOUT, f"read >{read}s {url}")
    except requests.RequestException as e:
        raise DrishtiError(ErrorKind.NETWORK, f"{type(e).__name__}: {str(e)[:120]} {url}")
    code, body = r.status_code, r.content or b""
    if code in (401, 403, 429):
        raise DrishtiError(ErrorKind.BLOCKED, f"HTTP {code} {url}")
    if code == 404:
        raise DrishtiError(ErrorKind.NOT_FOUND, f"HTTP 404 {url}")
    if code >= 400:
        raise DrishtiError(ErrorKind.NETWORK, f"HTTP {code} {url}")
    if not body.strip():
        raise DrishtiError(ErrorKind.EMPTY, f"empty body {url}")
    if expect in ("csv", "zip", "json", "xml") and looks_like_html(body):
        raise DrishtiError(ErrorKind.BLOCKED_HTML, f"HTML where {expect} expected {url}")
    return Fetched(url, body, code)


def get(
    url: str,
    profile: Profile,
    *,
    expect: Expect = "text",
    headers: dict[str, str] | None = None,
    ttl: float = 0,
    on_blocked: Callable[[], None] | None = None,
) -> Fetched:
    """GET `url` as `profile`. Raises DrishtiError; never returns a blank.

    ttl > 0: serve/keep successful responses in the in-memory cache for ttl seconds.
    on_blocked: called before each retry after 401/403/429 or an HTML block page (e.g. NSE session re-warm).
    """
    if ttl:
        hit = cache.get(url)
        if hit is not None:
            return hit
    host = urlsplit(url).hostname or ""
    for attempt in range(profile.retries + 1):
        limiter.wait(host)
        try:
            got = _once(profile, url, expect, headers)
            break
        except DrishtiError as e:
            if e.kind not in RETRYABLE + TRANSIENT or attempt == profile.retries:
                log.debug("%s: %s", profile.name, e)
                raise
            wait = BACKOFF_BASE * 2 ** attempt
            log.info("%s blocked (%s), retry %d in %.0fs", profile.name, e.detail, attempt + 1, wait)
            _sleep(wait)
            if on_blocked and e.kind in RETRYABLE:
                on_blocked()
    if ttl:
        cache.set(url, got, ttl)
    return got

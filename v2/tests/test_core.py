import json
import threading
import time

import pytest
import requests

from drishti.core import fetch
from drishti.core.cache import TTLCache
from drishti.core.envelope import DrishtiError, ErrorKind, Result, guard
from drishti.core.ratelimit import RateLimiter, Rule

PROFILE = fetch.Profile("test", {"User-Agent": "drishti-test"})


def resp(code=200, body=b"x"):
    r = requests.Response()
    r.status_code, r._content = code, body
    return r


class FakeSession:
    """Returns (or raises) the queued items in order; records calls."""
    def __init__(self, *items):
        self.items, self.calls = list(items), []

    def get(self, url, **kw):
        self.calls.append(url)
        item = self.items.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(fetch, "limiter", RateLimiter({}, Rule(0)))
    monkeypatch.setattr(fetch, "_sleep", lambda s: None)
    fetch.cache.clear()


def use(monkeypatch, *items):
    s = FakeSession(*items)
    monkeypatch.setattr(fetch, "session", lambda profile: s)
    return s


# --- classification -------------------------------------------------------
@pytest.mark.parametrize("item, expect, kind", [
    (resp(403), "json", ErrorKind.BLOCKED),
    (resp(401), "json", ErrorKind.BLOCKED),
    (resp(429), "json", ErrorKind.BLOCKED),
    (resp(404), "csv", ErrorKind.NOT_FOUND),
    (resp(500), "csv", ErrorKind.NETWORK),
    (resp(200, b"  \n"), "csv", ErrorKind.EMPTY),
    (resp(200, b"<!DOCTYPE html><html>Access Denied</html>"), "json", ErrorKind.BLOCKED_HTML),
    (resp(200, b"\n<html><head></head></html>"), "zip", ErrorKind.BLOCKED_HTML),
    (resp(200, b"<html><body>Access Denied</body></html>"), "xml", ErrorKind.BLOCKED_HTML),
    (requests.ReadTimeout(), "csv", ErrorKind.TIMEOUT),
    (requests.ConnectTimeout(), "csv", ErrorKind.TIMEOUT),
    (requests.ConnectionError("refused"), "csv", ErrorKind.NETWORK),
])
def test_classification(monkeypatch, item, expect, kind):
    use(monkeypatch, item, item, item)
    with pytest.raises(DrishtiError) as e:
        fetch.get("https://x.test/a", PROFILE, expect=expect)
    assert e.value.kind is kind
    assert "https://x.test/a" in e.value.detail


def test_html_is_fine_when_html_expected(monkeypatch):
    use(monkeypatch, resp(200, b"<html>ok</html>"))
    assert fetch.get("https://x.test/p", PROFILE, expect="html").body == b"<html>ok</html>"


def test_blocked_retries_with_backoff_and_rewarm(monkeypatch):
    s = use(monkeypatch, resp(403), resp(429), resp(200, b"{}"))
    sleeps, rewarms = [], []
    monkeypatch.setattr(fetch, "_sleep", sleeps.append)
    got = fetch.get("https://x.test/j", PROFILE, expect="json", on_blocked=lambda: rewarms.append(1))
    assert got.body == b"{}" and len(s.calls) == 3
    assert sleeps == [1.0, 2.0] and len(rewarms) == 2


def test_not_found_is_not_retried(monkeypatch):
    s = use(monkeypatch, resp(404))
    with pytest.raises(DrishtiError):
        fetch.get("https://x.test/f", PROFILE)
    assert len(s.calls) == 1


def test_ttl_cache_serves_repeat_calls(monkeypatch):
    s = use(monkeypatch, resp(200, b"a"))
    assert fetch.get("https://x.test/c", PROFILE, ttl=60).body == b"a"
    assert fetch.get("https://x.test/c", PROFILE, ttl=60).body == b"a"
    assert len(s.calls) == 1


def test_profile_requires_user_agent():
    with pytest.raises(ValueError):
        fetch.Profile("nope", {"Accept": "*/*"})


# --- guard / envelope -----------------------------------------------------
def test_guard_bad_json_is_parse_fail(monkeypatch):
    use(monkeypatch, resp(200, b"{not json"))

    @guard
    def provider():
        got = fetch.get("https://x.test/j", PROFILE, expect="json")
        return Result(data=json.loads(got.body), sources=[got.source("x")])

    r = provider()
    assert not r.ok and r.error.kind is ErrorKind.PARSE_FAIL and r.data is None
    assert r.to_dict()["error"]["kind"] == "PARSE_FAIL"


def test_guard_keeps_typed_kind(monkeypatch):
    use(monkeypatch, resp(403), resp(403), resp(403))
    r = guard(lambda: Result(data=fetch.get("https://x.test/j", PROFILE)))()
    assert r.error.kind is ErrorKind.BLOCKED


# --- rate limiter ---------------------------------------------------------
def test_limiter_spacing_across_threads():
    lim = RateLimiter({"nseindia.com": Rule(0.05)}, Rule(0))
    stamps, lock = [], threading.Lock()

    def call(host):
        lim.wait(host)
        with lock:
            stamps.append(time.monotonic())

    hosts = ["www.nseindia.com", "nsearchives.nseindia.com"] * 4  # share one slot
    ts = [threading.Thread(target=call, args=(h,)) for h in hosts]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    stamps.sort()
    gaps = [b - a for a, b in zip(stamps, stamps[1:])]
    assert min(gaps) >= 0.045, gaps


def test_limiter_matching():
    lim = RateLimiter({"sec.gov": Rule(0.1), "data.sec.gov": Rule(0.2)}, Rule(1))
    assert lim._match("www.sec.gov") == ("sec.gov", Rule(0.1))
    assert lim._match("data.sec.gov") == ("data.sec.gov", Rule(0.2))
    assert lim._match("notsec.gov") == ("notsec.gov", Rule(1))


# --- cache ----------------------------------------------------------------
def test_cache_ttl_expiry():
    now = [100.0]
    c = TTLCache(clock=lambda: now[0])
    c.set("k", "v", ttl=60)
    assert c.get("k") == "v"
    now[0] += 59.9
    assert c.get("k") == "v"
    now[0] += 0.2
    assert c.get("k") is None


# --- WP0 fixes: HTML blocks are retried, real limiter rules, per-source UA --
def test_blocked_html_is_retried_and_rewarmed(monkeypatch):
    s = use(monkeypatch, resp(200, b"<!DOCTYPE html><html>Access Denied</html>"), resp(200, b"{}"))
    sleeps, rewarms = [], []
    monkeypatch.setattr(fetch, "_sleep", sleeps.append)
    got = fetch.get("https://x.test/j", PROFILE, expect="json", on_blocked=lambda: rewarms.append(1))
    assert got.body == b"{}" and len(s.calls) == 2
    assert sleeps == [1.0] and len(rewarms) == 1


def test_default_limiter_rules():
    lim = fetch.default_limiter()
    nse = {lim._match(h)[0] for h in ("www.nseindia.com", "nsearchives.nseindia.com", "archives.nseindia.com")}
    bse = {lim._match(h)[0] for h in ("api.bseindia.com", "www.bseindia.com")}
    sec = {lim._match(h)[0] for h in ("www.sec.gov", "data.sec.gov")}
    assert nse == {"nseindia.com"} and bse == {"bseindia.com"} and sec == {"sec.gov"}
    assert lim._match("api.bseindia.com")[1].interval == 0.5
    assert lim._match("query1.finance.yahoo.com")[1].interval == 0.3
    assert lim._match("rbi.org.in") == ("rbi.org.in", Rule(1.0))  # unknown host: 1 s default


def test_browser_headers_use_current_ua_and_allow_override(monkeypatch):
    monkeypatch.delenv("DRISHTI_BROWSER_UA", raising=False)
    h = fetch.browser_headers({"Origin": "https://www.bseindia.com"})
    assert "Chrome/153" in h["User-Agent"] and h["Origin"] == "https://www.bseindia.com"
    fetch.Profile("bse", h)  # a browser header set is a valid profile
    monkeypatch.setenv("DRISHTI_BROWSER_UA", "Mozilla/5.0 Chrome/999")
    assert fetch.browser_headers()["User-Agent"] == "Mozilla/5.0 Chrome/999"
    assert fetch.browser_headers() is not fetch.browser_headers()  # fresh dict each call


def test_honest_ua_is_not_a_browser_string():
    assert "Mozilla" not in fetch.HONEST_UA and "@" not in fetch.HONEST_UA

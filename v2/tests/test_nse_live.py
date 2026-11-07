import json
from types import SimpleNamespace

import pytest

from drishti.core.envelope import DrishtiError, ErrorKind
from drishti.core.fetch import Fetched
from drishti.providers import nse_live


class FakeFetch:
    """Stands in for core.fetch: tracks cookies and calls."""

    def __init__(self, cookies=0, home_fails=False):
        self.n_cookies, self.home_fails, self.calls, self.resets = cookies, home_fails, [], 0

    def session(self, profile):
        return SimpleNamespace(cookies=[object()] * self.n_cookies)

    def reset_session(self, profile):
        self.resets += 1
        self.n_cookies = 0

    def get(self, url, profile, *, expect="text", on_blocked=None, **kw):
        self.calls.append((url, profile.name, expect, profile.retries, on_blocked))
        if url == "https://www.nseindia.com/":
            if self.home_fails:
                raise DrishtiError(ErrorKind.BLOCKED, "403")
            self.n_cookies += 3
        elif url.endswith("/option-chain"):
            self.n_cookies += 5
        return Fetched(url, b"{}", 200)


@pytest.fixture
def fake(monkeypatch):
    def install(**kw):
        f = FakeFetch(**kw)
        monkeypatch.setattr(nse_live, "_fetch", f)
        return f
    return install


def test_api_get_warms_a_cold_session_then_calls_the_api(fake):
    f = fake()
    nse_live.api_get("corporates-pit-gg", index="equities", from_date="01-11-2026")
    urls = [c[0] for c in f.calls]
    assert urls[:2] == ["https://www.nseindia.com/", "https://www.nseindia.com/option-chain"]
    assert urls[2] == "https://www.nseindia.com/api/corporates-pit-gg?index=equities&from_date=01-11-2026"
    url, name, expect, retries, on_blocked = f.calls[2]
    assert (name, expect, on_blocked) == ("nse", "json", nse_live.warm)
    assert all(c[3] == 0 for c in f.calls[:2])  # warm-up never retries


def test_api_get_skips_warmup_when_cookies_exist(fake):
    f = fake(cookies=8)
    nse_live.api_get("marketStatus")
    assert [c[0] for c in f.calls] == ["https://www.nseindia.com/api/marketStatus"]


def test_warm_tolerates_a_blocked_homepage(fake):
    f = fake(home_fails=True)
    assert nse_live.warm() == 5 and f.resets == 1  # option-chain still gave cookies


def test_warm_reports_zero_cookies_honestly(fake, monkeypatch):
    f = fake()
    f.get = lambda *a, **k: (_ for _ in ()).throw(DrishtiError(ErrorKind.BLOCKED, "403"))
    assert nse_live.warm() == 0


def test_rows_of_shapes():
    assert nse_live.rows_of(json.dumps([{"a": 1}]).encode(), "x") == [{"a": 1}]
    assert nse_live.rows_of(json.dumps({"data": [{"a": 1}]}).encode(), "x") == [{"a": 1}]
    for bad in (b"not json", b'{"data": "nope"}', b'{"other": 1}', b"[1, 2]", b"\xff\xfe"):
        with pytest.raises(DrishtiError) as ei:
            nse_live.rows_of(bad, "x")
        assert ei.value.kind == ErrorKind.PARSE_FAIL

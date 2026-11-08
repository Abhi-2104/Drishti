"""News infrastructure on synthetic feeds (no real publisher content): RSS and Atom parsing, safety, storage, tagging, config, job, tool."""
from __future__ import annotations

from datetime import datetime

import pytest

from drishti import data_tools as dt
from drishti import jobs
from drishti.config import ConfigError, load_settings
from drishti.core.envelope import DrishtiError, ErrorKind
from drishti.core.fetch import Fetched
from drishti.providers import feeds
from drishti.store import Store
from drishti.store import datasets as ds

RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>
<item><title>Reliance Industries Limited announces results</title><link>https://x.test/a</link><guid>g1</guid>
 <pubDate>Fri, 06 Nov 2026 10:30:00 +0530</pubDate><description>&lt;p&gt;Profit up. HDFCBANK also rose.&lt;/p&gt;</description></item>
<item><title>Markets close flat</title><link>https://x.test/b</link><guid>g2</guid><pubDate>Fri, 06 Nov 2026 11:00:00 GMT</pubDate></item>
<item><title>No time on this one</title><link>https://x.test/c</link><guid>g3</guid></item>
</channel></rss>"""
ATOM = b"""<?xml version="1.0" encoding="utf-8"?><feed xmlns="http://www.w3.org/2005/Atom"><title>t</title>
<entry><id>urn:1</id><title>Infosys Limited wins deal</title><link rel="alternate" href="https://y.test/1"/><published>2026-11-07T04:00:00Z</published>
 <summary>Deal worth a lot</summary></entry>
<entry><id>urn:2</id><title>Other</title><link href="https://y.test/2"/><updated>2026-11-07T05:00:00+05:30</updated></entry></feed>"""


@pytest.fixture
def st():
    s = Store()
    yield s
    s.close()


def kind(fn, *a, **k) -> ErrorKind:
    with pytest.raises(DrishtiError) as ei:
        fn(*a, **k)
    return ei.value.kind


# --- parsing ---------------------------------------------------------------------------------------------------
def test_rss_items_times_and_cleaning():
    r = feeds.parse_feed(RSS)
    assert r["format"] == "rss" and r["dropped"] == 1 and [i["item_id"] for i in r["items"]] == ["g1", "g2"]
    a = r["items"][0]
    assert a["published"] == "2026-11-06T05:00:00Z" and a["summary"] == "Profit up. HDFCBANK also rose." and a["link"] == "https://x.test/a"


def test_atom_items_and_zones():
    r = feeds.parse_feed(ATOM)
    assert r["format"] == "atom" and [i["published"] for i in r["items"]] == ["2026-11-07T04:00:00Z", "2026-11-06T23:30:00Z"]
    assert r["items"][1]["link"] == "https://y.test/2"                      # no rel -> the only link


@pytest.mark.parametrize("body,expect", [
    (b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><rss><channel><item/></channel></rss>', ErrorKind.PARSE_FAIL),
    (b"<html><body>hi</body></html>", ErrorKind.PARSE_FAIL),
    (b"not xml at all", ErrorKind.PARSE_FAIL),
    (b'<rss version="2.0"><channel><title>t</title></channel></rss>', ErrorKind.EMPTY),
    (b'<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"/>', ErrorKind.PARSE_FAIL),
])
def test_bad_documents_are_typed(body, expect):
    assert kind(feeds.parse_feed, body) == expect


def test_most_items_without_a_time_is_a_layout_error():
    body = b'<rss version="2.0"><channel>' + b"".join(b"<item><title>t%d</title><guid>%d</guid></item>" % (i, i) for i in range(5)) + \
           b"<item><title>ok</title><guid>x</guid><pubDate>Fri, 06 Nov 2026 10:30:00 GMT</pubDate></item></channel></rss>"
    assert kind(feeds.parse_feed, body) == ErrorKind.PARSE_FAIL


def test_fetch_requires_https_and_checks_blocks(monkeypatch):
    assert feeds.fetch_feed("a", "http://x.test/f").error.kind == ErrorKind.PARSE_FAIL
    monkeypatch.setattr(feeds, "get", lambda url, profile, expect="text", **k: Fetched(url, RSS, 200))
    r = feeds.fetch_feed("a", "https://x.test/f")
    assert r.ok and r.as_of == "2026-11-06" and r.data["source"] == "a"


# --- storage and tagging ---------------------------------------------------------------------------------------------
def master(st):
    st.upsert_security("INE002A01018", name="Reliance Industries Limited", nse_symbol="RELIANCE", source="t", instrument_class="equity")
    st.upsert_security("INE040A01034", name="HDFC Bank Limited", nse_symbol="HDFCBANK", source="t", instrument_class="equity")
    st.upsert_security("INE009A01021", name="Infosys Limited", nse_symbol="INFY", source="t", instrument_class="equity")


def test_tags_by_name_and_by_symbol_and_reports_what_matched(st):
    master(st)
    items = feeds.parse_feed(RSS)["items"]
    out = ds.persist_news(st, "x", items, "2026-11-08T10:00:00+05:30")
    assert out == {"new": 2, "known": 0, "tagged": 1}
    tags = {(t["isin"], t["match_kind"], t["matched"]) for t in st.query("SELECT * FROM news_entity")}
    assert tags == {("INE002A01018", "name", "reliance industries"), ("INE040A01034", "symbol", "HDFCBANK")}


def test_a_name_shared_by_two_isins_is_never_tagged(st):
    st.upsert_security("INE000A01010", name="Alpha Textiles Limited", nse_symbol="ALPHATEXT", source="t", instrument_class="equity")
    st.upsert_security("INE000B01018", name="Alpha Textiles Ltd", nse_symbol="ALPHATX2", source="t", instrument_class="equity")
    ds.persist_news(st, "x", [{"item_id": "1", "title": "Alpha Textiles posts a loss", "link": "", "published": "2026-11-06T00:00:00Z", "summary": ""}], "t")
    assert st.query("SELECT * FROM news_entity") == []


def test_short_names_and_lowercase_symbols_do_not_tag(st):
    st.upsert_security("INE111A01011", name="Tata Co Limited", nse_symbol="TATA", source="t", instrument_class="equity")
    ds.persist_news(st, "x", [{"item_id": "1", "title": "tata steel and the tata group, tata shares", "link": "", "published": "2026-11-06T00:00:00Z", "summary": ""}], "t")
    assert st.query("SELECT * FROM news_entity") == []                        # "tata" is below the length floor; lowercase never matches a symbol


def test_persist_is_idempotent_and_keeps_first_seen(st):
    master(st)
    items = feeds.parse_feed(RSS)["items"]
    ds.persist_news(st, "x", items, "2026-11-08T10:00:00+05:30")
    again = ds.persist_news(st, "x", items, "2026-11-09T10:00:00+05:30")
    assert again == {"new": 0, "known": 2, "tagged": 0}
    assert {r["first_seen"] for r in st.query("SELECT first_seen FROM news_item")} == {"2026-11-08T10:00:00+05:30"}


# --- config ----------------------------------------------------------------------------------------------------------
def settings_with(tmp_path, text):
    (tmp_path / "settings.toml").write_text(text, encoding="utf-8")
    return load_settings({"DRISHTI_HOME": str(tmp_path)})


def test_config_default_is_no_sources_and_parses_a_list(tmp_path):
    assert settings_with(tmp_path, "").news_sources == ()
    s = settings_with(tmp_path, '[[news_source]]\nname = "a-1"\nurl = "https://x.test/f"\n[[news_source]]\nname = "b"\nurl = "https://y.test/g"\n')
    assert [(x.name, x.url) for x in s.news_sources] == [("a-1", "https://x.test/f"), ("b", "https://y.test/g")]


@pytest.mark.parametrize("text", [
    '[[news_source]]\nname = "a"\nurl = "http://x.test/f"\n',                    # not https
    '[[news_source]]\nname = "A B"\nurl = "https://x.test/f"\n',                 # bad name
    '[[news_source]]\nname = "a"\nurl = "https://x.test/f"\n[[news_source]]\nname = "a"\nurl = "https://y.test/f"\n',   # duplicate
    '[[news_source]]\nname = "a"\nurl = "https://x.test/f"\ntypo = 1\n',         # unknown key
    'news_source = "https://x.test/f"\n',                                         # wrong shape
])
def test_config_rejects_bad_sources(tmp_path, text):
    with pytest.raises(ConfigError):
        settings_with(tmp_path, text)


# --- job and tool --------------------------------------------------------------------------------------------------------
NOW = datetime(2026, 11, 8, 7, 5, tzinfo=jobs.IST)


def test_job_with_no_sources_is_ok_and_says_so(tmp_path, monkeypatch, st):
    monkeypatch.setenv("DRISHTI_HOME", str(tmp_path))
    rep = jobs.run_job(st, "news", now=NOW)
    assert rep.ok and rep.steps[0].rows == 0
    assert dt.news(st).error.kind == ErrorKind.EMPTY and "no sources are configured" in dt.news(st).error.detail


def test_job_fetches_stores_and_survives_one_bad_feed(tmp_path, monkeypatch, st):
    master(st)
    monkeypatch.setenv("DRISHTI_HOME", str(tmp_path))
    (tmp_path / "settings.toml").write_text('[[news_source]]\nname = "good"\nurl = "https://x.test/f"\n[[news_source]]\nname = "bad"\nurl = "https://y.test/f"\n', encoding="utf-8")
    monkeypatch.setattr(feeds, "get", lambda url, profile, expect="text", **k: Fetched(url, RSS if "x.test" in url else b"<html>nope</html>", 200))
    rep = jobs.run_job(st, "news", now=NOW)
    assert rep.ok and rep.steps[0].rows == 2
    assert st.query("SELECT COUNT(*) AS n FROM news_item")[0]["n"] == 2
    monkeypatch.setattr(feeds, "get", lambda url, profile, expect="text", **k: Fetched(url, b"<html>nope</html>", 200))
    assert not jobs.run_job(st, "news", now=NOW).ok                              # every source failing is a failure


def test_news_tool_by_company_and_overall(st):
    master(st)
    ds.persist_news(st, "x", feeds.parse_feed(RSS)["items"], "t")
    ds.persist_news(st, "y", feeds.parse_feed(ATOM)["items"], "t")
    r = dt.news(st, days=3650)
    assert r.ok and [i["title"] for i in r.data["items"]][0] == "Infosys Limited wins deal" and len(r.data["items"]) == 4       # newest first
    rel = dt.news(st, "RELIANCE", days=3650)
    assert rel.ok and len(rel.data["items"]) == 1 and rel.data["items"][0]["match_kind"] == "name" and rel.data["company"] == "Reliance Industries Limited"
    assert dt.news(st, "INFY", days=3650).error.kind == ErrorKind.EMPTY      # "infosys" is 7 characters, under the 8-character floor: not tagged by name
    st.upsert_security("INE999A01010", name="Quiet Company Limited", nse_symbol="QUIETCO", source="t", instrument_class="equity")
    e = dt.news(st, "QUIETCO", days=3650).error
    assert e.kind == ErrorKind.EMPTY and "4 items stored" in e.detail


def test_mcp_exposes_get_news():
    pytest.importorskip("mcp")
    import asyncio

    from drishti import mcp_server as m

    class _K:
        def __init__(self, s):
            self.s = s

        def __enter__(self):
            return self.s

        def __exit__(self, *a):
            return False
    s = Store()
    try:
        assert "get_news" in [t.name for t in asyncio.run(m.build_server(lambda: _K(s)).list_tools())]
    finally:
        s.close()

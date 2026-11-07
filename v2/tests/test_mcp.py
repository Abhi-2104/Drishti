"""MCP tools: the plain functions (no SDK needed), the server wiring, and a real stdio round trip."""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import date

import pytest

from drishti import mcp_server as m
from drishti.store import Store

TODAY = date(2026, 11, 8)


def ev(key, sym, isin, direction, value, filed, person="A Person"):
    return dict(event_key=key, app_id=key, filed_at=filed, symbol=sym, isin=isin, scrip_code=None, company=sym, category="Promoter",
                person=person, direction=direction, txn_type=direction.title(), mode="Market", quantity=10, value_inr=value,
                held_pre_shares=0, held_post_shares=10, held_pre_pct=0.0, held_post_pct=1.0, holding_reconciles=1, from_date=filed[:10],
                to_date=filed[:10], intimation_date=filed[:10], filing_date=filed[:10], exchange="NSE", revised=0, form_version="2.0",
                stored_at="2026-11-08T00:00:00")


@pytest.fixture
def st():
    from drishti.jobs import INSIDER_DDL
    s = Store()
    s.ensure_table(INSIDER_DDL)
    s.upsert_security("INE002A01018", name="Reliance Industries Limited", nse_symbol="RELIANCE", bse_scrip="500325", bse_symbol="RELIANCE", source="t")
    s.upsert_security("INE111A01011", name="Other Co", nse_symbol="OTHER", source="t")
    s.upsert("insider_event", [
        ev("1", "RELIANCE", "INE002A01018", "BUY", 1000.0, "2026-11-07T10:00:00"),
        ev("2", "RELIANCE", "INE002A01018", "SELL", 400.0, "2026-11-06T10:00:00"),
        ev("3", "OTHER", "INE111A01011", "PLEDGE", 0.0, "2026-11-05T10:00:00"),
        ev("4", "RELIANCE", "INE002A01018", "BUY", 50.0, "2026-09-01T10:00:00"),     # outside a 30-day window
        ev("5", "NOISIN", None, "BUY", 5.0, "2026-11-07T11:00:00"),
    ], ("event_key",))
    yield s
    s.close()


def test_data_health_envelope(st):
    r = m.tool_data_health(st)
    assert r["error"] is None and r["data"]["status"] == "ok" and set(r["data"]) >= {"jobs", "datasets", "canaries"}
    json.dumps(r)   # must be JSON-serialisable end to end


def test_resolve_tool(st):
    r = m.tool_resolve_security(st, "reliance")
    assert r["error"] is None and r["data"]["matches"][0]["isin"] == "INE002A01018"
    assert m.tool_resolve_security(st, "zzz")["error"]["kind"] == "NOT_FOUND"


def test_insider_window_and_totals(st):
    r = m.tool_insider_trades(st, days=30, today=TODAY)
    assert r["error"] is None and r["as_of"] == "2026-11-07"
    assert r["data"]["matched"] == 4 and [e["event_key"] for e in r["data"]["events"]] == ["5", "1", "2", "3"]   # newest first (5 was filed an hour after 1); the September row is out
    assert r["data"]["totals"]["BUY"] == {"events": 2, "value_inr": 1005.0} and r["data"]["net_buy_value_inr"] == 605.0
    assert m.tool_insider_trades(st, days=365, today=TODAY)["data"]["matched"] == 5


def test_insider_company_and_direction_filters(st):
    r = m.tool_insider_trades(st, "500325", days=30, today=TODAY)           # by BSE scrip code
    assert r["data"]["matched"] == 2 and {e["symbol"] for e in r["data"]["events"]} == {"RELIANCE"}
    r = m.tool_insider_trades(st, "RELIANCE", days=30, direction="sell", today=TODAY)
    assert [e["event_key"] for e in r["data"]["events"]] == ["2"]
    assert m.tool_insider_trades(st, "OTHER", today=TODAY)["data"]["totals"] == {"PLEDGE": {"events": 1, "value_inr": 0.0}}
    assert m.tool_insider_trades(st, limit=1, today=TODAY)["data"]["returned"] == 1


def test_insider_failures_are_typed_not_blank(st):
    assert m.tool_insider_trades(st, "nosuchco", today=TODAY)["error"]["kind"] == "NOT_FOUND"
    assert m.tool_insider_trades(st, direction="sideways", today=TODAY)["error"]["kind"] == "PARSE_FAIL"
    st.upsert_security("INE222A01010", name="Clash Two", bse_symbol="OTHER", bse_scrip="511111", source="t")
    r = m.tool_insider_trades(st, "other", today=TODAY)
    assert r["error"]["kind"] == "PARSE_FAIL" and "several companies" in r["error"]["detail"] and r["data"] is None
    with Store() as empty:
        assert m.tool_insider_trades(empty, today=TODAY)["error"]["kind"] == "NOT_FOUND"      # job never ran


def test_stale_insider_data_is_returned_with_a_stale_error(st):
    r = m.tool_insider_trades(st, days=60, today=date(2026, 11, 20))
    assert r["error"]["kind"] == "STALE" and r["data"]["matched"] >= 1 and r["as_of"] == "2026-11-07"   # data shown, but loudly not current


def test_sql_injection_in_query_is_inert(st):
    r = m.tool_insider_trades(st, "x'; DROP TABLE insider_event; --", today=TODAY)
    assert r["error"]["kind"] == "NOT_FOUND"
    assert st.query("SELECT COUNT(*) AS n FROM insider_event")[0]["n"] == 5


# --- server wiring (needs the SDK) ------------------------------------------------------------------------------
def test_server_lists_and_calls_tools(st):
    pytest.importorskip("mcp")
    srv = m.build_server(lambda: _Keep(st))

    async def go():
        tools = await srv.list_tools()
        out = await srv.call_tool("resolve_security", {"query": "RELIANCE"})
        return [t.name for t in tools], out
    names, out = asyncio.run(go())
    assert sorted(names) == ["data_health", "insider_trades", "resolve_security"]
    assert "INE002A01018" in json.dumps(out.model_dump(mode="json"), default=str)


class _Keep:
    """Hands the shared test store to the server without letting `with` close it."""
    def __init__(self, s):
        self.s = s

    def __enter__(self):
        return self.s

    def __exit__(self, *a):
        return False


def test_real_stdio_round_trip(tmp_path, monkeypatch):
    """Spawn `drishti mcp` as Claude Desktop would and call a tool over stdio. Catches any stray print on stdout."""
    pytest.importorskip("mcp")
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from drishti.config import load_settings
    monkeypatch.setenv("DRISHTI_HOME", str(tmp_path))
    with Store(load_settings().db_path) as s:
        s.upsert_security("INE002A01018", name="Reliance Industries Limited", nse_symbol="RELIANCE", source="t")
    params = StdioServerParameters(command=sys.executable, args=["-m", "drishti.cli", "mcp"], env={"DRISHTI_HOME": str(tmp_path)})

    async def go():
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as session:
                await session.initialize()
                tools = await session.list_tools()
                res = await session.call_tool("resolve_security", {"query": "RELIANCE"})
                return [t.name for t in tools.tools], res
    names, res = asyncio.run(asyncio.wait_for(go(), 60))
    assert "data_health" in names and "INE002A01018" in json.dumps(res.model_dump(mode="json"), default=str)

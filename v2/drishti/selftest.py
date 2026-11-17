"""`drishti selftest`: one command that exercises the whole chain against YOUR database and prints PASS/FAIL per step.
Offline and read-only (no fetches, no writes): settings -> store -> data health -> MCP server and its tools -> a signal ->
blind evidence -> a rules decision (computed, not logged) -> the decision log and scorer."""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import date


def run(cfg) -> int:
    from . import mcp_server as m
    from .decisions import cases as cs, gather, history, log, rules, score
    from .store import Store
    results: list[tuple[str, bool, str]] = []

    def step(name, fn):
        try:
            ok, note = fn()
        except Exception as e:                                            # a failing step is a result, not a crash
            ok, note = False, f"{type(e).__name__}: {str(e)[:140]}"
        results.append((name, ok, note))
        print(f"[{'PASS' if ok else 'FAIL'}] {name:<34s} {note}", flush=True)
        return ok

    state: dict = {}
    with Store(cfg.db_path) as st:
        def s_store():
            days = cs.sessions(st)
            state["days"] = days
            return bool(days), f"{len(days)} price sessions, last {days[-1] if days else '-'}"

        def s_health():
            r = m.tool_data_health(st)
            return r.get("error") is None, f"status {r['data']['status']}" if r.get("data") else str(r.get("error"))

        def s_server():
            names = sorted(t.name for t in asyncio.run(m.build_server(lambda: Store(cfg.db_path)).list_tools()))
            need = {"data_health", "gather_evidence", "record_decision", "get_decisions", "score_decisions"}
            return need <= set(names), f"{len(names)} tools registered" + ("" if need <= set(names) else f"; missing {sorted(need - set(names))}")

        def s_pick():
            on = state["days"][-1]
            row = st.query("SELECT isin, MAX(symbol) AS symbol FROM price_eod WHERE exchange='NSE' AND series='EQ' AND trade_date=? "
                           "GROUP BY isin ORDER BY MAX(turnover) DESC LIMIT 1", (on,))[0]
            state.update(on=on, isin=row["isin"], symbol=row["symbol"])
            return True, f"{row['symbol']} on {on}"

        def s_signal():
            r = m.tool_signal(st, "price_context", state["on"], state["isin"])
            return r.get("error") is None, (r["data"]["label"] if r.get("data") else str(r.get("error")))[:80]

        def s_gather():
            r = gather.gather(st, "entry", state["on"], isin=state["isin"], blind=True)
            leak = r.ok and state["symbol"] in json.dumps(r.data, default=str)
            return r.ok and not leak, "blind bundle " + (r.data["bundle_sha"][:10] if r.ok else str(r.error.detail)[:80]) + (" LEAKS the symbol" if leak else "")

        def s_rules():
            d = rules.decide_stock(st, "entry", state["isin"], state["symbol"], date.fromisoformat(state["on"]))
            return d is not None, f"{d['action']} ({d['points']:+d} points), not logged" if d else "too little data"

        def s_log():
            log.ensure(st)
            n = len(log.current(st))
            return True, f"{n} decisions in the log"

        def s_score():
            rows = score.score_all(st)
            sc = sum(r["status"] == "scored" for r in rows)
            return True, f"{len(rows)} decisions: {sc} scored, {sum(r['status'] == 'pending' for r in rows)} pending"

        def s_cov():
            c = history.coverage(st)
            return c["sessions"] > 0, f"{c['sessions']} sessions {c['first']}..{c['last']}, {c['stocks_ever_traded']} stocks, {c['stocks_missing_from_master']} not in master"

        step("store has prices", s_store)
        if results[-1][1]:
            step("data_health tool", s_health)
            step("MCP server builds, tools listed", s_server)
            if step("pick the most liquid stock", s_pick):
                step("price_context signal", s_signal)
                step("blind evidence bundle", s_gather)
                step("rules-v1 decision (dry run)", s_rules)
            step("decision log readable", s_log)
            step("scorer runs", s_score)
            step("history coverage", s_cov)
    bad = [n for n, ok, _ in results if not ok]
    print(f"\n{'ALL ' + str(len(results)) + ' STEPS PASSED' if not bad else str(len(bad)) + ' STEP(S) FAILED: ' + ', '.join(bad)}")
    return 0 if not bad else 1

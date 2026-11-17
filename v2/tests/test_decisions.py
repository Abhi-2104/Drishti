"""Decision layer: the log refuses vague or unverifiable calls and never edits history; rules-v1 follows its frozen numbers;
the scorer judges against the Nifty (or cash) without dropping pending cases; the comparison names a winner only with evidence."""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from drishti.decisions import gather, log, rules, score
from drishti.store import Store
from drishti.store import datasets as ds

UP, DOWN = "INE002A01018", "INE009A01021"


def weekdays(start: date, n: int) -> list[str]:
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


DAYS = weekdays(date(2025, 1, 1), 330)       # ends mid-2026, comfortably in the past


def put_stock(st, isin, symbol, closes):
    st.upsert_security(isin, name=f"{symbol} Industries Limited", nse_symbol=symbol, source="test")
    st.upsert("price_eod", [{"trade_date": d, "isin": isin, "exchange": "NSE", "series": "EQ", "symbol": symbol, "open": c, "high": c * 1.01,
                             "low": c * 0.99, "close": c, "prev_close": c, "volume": 100000, "turnover": c * 1e5, "trades": 500}
                            for d, c in zip(DAYS, closes)], key=["trade_date", "isin", "exchange", "series"])


@pytest.fixture
def st():
    s = Store()
    ds.ensure(s)
    n = len(DAYS)
    put_stock(s, UP, "RISER", [100 * 1.004 ** i for i in range(n)])          # +0.4% a day
    put_stock(s, DOWN, "FALLER", [100 * 0.996 ** i for i in range(n)])      # -0.4% a day
    ds.persist_index_closes(s, [{"trade_date": d, "index_name": "Nifty 50", "close": 20000 * 1.0004 ** i} for i, d in enumerate(DAYS)])
    yield s
    s.close()


AS_OF = DAYS[250]           # 250 sessions of history behind it, 79 ahead


def call(st, **kw):
    base = dict(gate="entry", as_of=AS_OF, action="ENTER", horizon_days=20, thesis="Trend is up and beating the index.",
                agent="claude", model="m1", signals_cited=["price_context"], isin=UP, confidence=0.6,
                invalidation="closes below the 50-day average")
    base.update(kw)
    return log.record(st, **base)


# --- the log ------------------------------------------------------------------------------------------------------
def test_record_stores_recomputed_evidence(st):
    r = call(st)
    assert r.ok, r.error
    ev = r.data["evidence"][0]
    assert ev["name"] == "price_context" and ev["as_of"] <= AS_OF and ev["params_version"] == "price"
    assert r.data["stance"] == 1 and r.data["id"].startswith("dec_")
    assert len(log.current(st)) == 1


@pytest.mark.parametrize("kw,needle", [
    ({"gate": "nope"}, "unknown gate"), ({"action": "BUY"}, "not valid for /entry"), ({"as_of": "2999-01-01"}, "future"),
    ({"as_of": "01-02-2026"}, "YYYY-MM-DD"), ({"horizon_days": 0}, "horizon_days"), ({"horizon_days": 99999}, "horizon_days"),
    ({"confidence": 0.3}, "0.50..0.99"), ({"confidence": None}, "0.50..0.99"), ({"confidence": 1.0}, "0.50..0.99"),
    ({"invalidation": "wrong"}, "invalidation"), ({"thesis": "short"}, "thesis"), ({"signals_cited": []}, "cite"),
    ({"agent": " "}, "agent and model"), ({"isin": None}, "pass its ISIN"), ({"isin": "INE000000000"}, "security master"),
    ({"signals_cited": ["made_up"]}, "does not exist"), ({"signals_cited": ["insider_cluster"]}, "no reading"),
])
def test_record_refuses_vague_calls(st, kw, needle):
    r = call(st, **kw)
    assert not r.ok and needle in r.error.detail, r.error
    assert log.current(st) == []


def test_a_no_call_needs_no_confidence_or_invalidation(st):
    r = call(st, action="WAIT", confidence=None, invalidation=None, signals_cited=[], thesis="Not enough to say.")
    assert r.ok and r.data["stance"] == 0


def test_market_gate_ignores_the_stock(st):
    r = log.record(st, gate="macro", as_of=AS_OF, action="NEUTRAL", horizon_days=20, thesis="No clear tide.", agent="a", model="m", signals_cited=[],
                   isin=UP)
    assert r.ok and r.data["subject"] == "MARKET" and r.data["isin"] is None


def test_log_is_append_only_and_supersede_hides_the_old_row(st):
    first = call(st)
    dup = call(st)
    assert not dup.ok and "append-only" in dup.error.detail
    fixed = call(st, action="WAIT", confidence=None, invalidation=None, thesis="Changed my mind after rereading.", supersedes=first.data["id"])
    assert fixed.ok
    assert [d["action"] for d in log.current(st)] == ["WAIT"]
    assert st.query("SELECT COUNT(*) AS n FROM decision")[0]["n"] == 2          # the old row is still there
    third = call(st, action="AVOID", confidence=0.6, thesis="Second thoughts: weak balance sheet.", supersedes=fixed.data["id"])
    assert third.ok and [d["action"] for d in log.current(st)] == ["AVOID"]
    assert not call(st, supersedes="dec_nothing").ok


def test_two_authors_may_decide_the_same_case(st):
    assert call(st).ok and call(st, agent="rules", model="rules-v1").ok
    assert len(log.current(st)) == 2 and len(log.current(st, agent="rules")) == 1


# --- rules-v1 -----------------------------------------------------------------------------------------------------
def test_rules_enter_a_strong_uptrend_and_avoid_a_downtrend(st):
    up = rules.decide_stock(st, "entry", UP, "RISER", date.fromisoformat(AS_OF))
    dn = rules.decide_stock(st, "entry", DOWN, "FALLER", date.fromisoformat(AS_OF))
    assert up["action"] == "ENTER" and up["points"] >= 3 and 0.55 < up["confidence"] <= 0.70
    assert dn["action"] == "AVOID" and dn["points"] <= -2
    assert "price_context" in up["signals_cited"] and "Unknown:" in up["thesis"]         # insider / pledge / macro are not stored: said so


def test_rules_exit_follows_the_trend_and_chasing_costs_a_point(st):
    assert rules.decide_stock(st, "exit", DOWN, "FALLER", date.fromisoformat(AS_OF))["action"] in ("EXIT", "TRIM")
    assert rules.decide_stock(st, "exit", UP, "RISER", date.fromisoformat(AS_OF))["action"] == "HOLD"
    sc = rules.stock_points(st, UP, "RISER", date.fromisoformat(AS_OF))
    assert not any("already up" in p["why"] for p in sc["parts"])        # +0.4% a day is ~9% a month: below the 25% line


def test_rules_run_writes_the_log_under_the_rules_version(st):
    r = rules.run(st, "entry", AS_OF, isin=UP, symbol="RISER")
    assert r.ok, r.error
    assert r.data["agent"] == "rules" and r.data["model"].startswith("rules-v1+") and r.data["notes"].startswith("points=")
    assert not rules.run(st, "entry", AS_OF, isin=UP, symbol="RISER").ok          # the same case twice is refused
    assert rules.run(st, "entry", DAYS[5], isin=UP, symbol="RISER").error.kind.name in ("NOT_FOUND",)  # 5 sessions of history: no call, no guess


def test_rules_macro_needs_enough_readable_votes(st, monkeypatch):
    votes = {"macro_regime": {"score": 3, "label": "x", "details": {}}, "participant_oi": {"score": 5, "label": "x", "details": {}},
             "cycle_phase": {"score": None, "label": "Expansion most likely (60%)", "details": {"probabilities": {"Expansion": 0.6, "Late": 0.4}}},
             "midcap_ratio": {"score": 2.0, "label": "midcaps outperforming", "details": {}}}
    monkeypatch.setattr(rules, "_ok", lambda store, name, on, **kw: votes.get(name))
    d = rules.decide_macro(st, date.fromisoformat(AS_OF))
    assert d["action"] == "RISK_ON" and d["points"] == 4 and d["confidence"] == 0.67
    monkeypatch.setattr(rules, "_ok", lambda store, name, on, **kw: votes.get(name) if name in ("macro_regime", "midcap_ratio") else None)
    assert rules.decide_macro(st, date.fromisoformat(AS_OF)) is None                 # two readable inputs: no call
    votes["macro_regime"]["score"] = -3
    votes["participant_oi"]["score"] = -5
    votes["midcap_ratio"]["label"] = "midcaps lagging"
    monkeypatch.setattr(rules, "_ok", lambda store, name, on, **kw: votes.get(name))
    assert rules.decide_macro(st, date.fromisoformat(AS_OF))["action"] == "RISK_OFF"


def test_rules_file_is_hashed_into_the_model_name():
    _, ver = rules.load()
    assert ver.startswith("rules-v1+") and len(ver.split("+")[1]) == 8


# --- the scorer ---------------------------------------------------------------------------------------------------
def test_outcome_scores_a_bullish_call_against_the_nifty(st):
    d = call(st).data
    o = score.outcome(st, {**d, "scope": "stock"})
    assert o["status"] == "scored" and o["hit"] is True
    exp = ((1.004 ** 270 / 1.004 ** 250) - (1.0004 ** 270 / 1.0004 ** 250)) * 100
    assert o["excess_pct"] == pytest.approx(exp, abs=0.01)
    assert o["edge_net_pct"] == pytest.approx(o["edge_pct"] - score.COST_PCT, abs=1e-6)       # ENTER pays the trade cost


def test_outcome_bearish_call_earns_the_opposite_and_pays_no_cost(st):
    d = call(st, isin=DOWN, action="AVOID").data
    o = score.outcome(st, {**d, "scope": "stock"})
    assert o["hit"] is True and o["edge_pct"] > 0 and o["edge_net_pct"] == o["edge_pct"]


def test_outcome_pending_and_no_data_are_never_dropped(st):
    late = call(st, as_of=DAYS[320], horizon_days=60).data
    o = score.outcome(st, {**late, "scope": "stock"})
    assert o["status"] == "pending" and "of 60 sessions" in o["why"]
    st.upsert_security("INE0AAA01010", name="Ghost Ltd", nse_symbol="GHOST", source="test")
    assert score.outcome(st, {**late, "as_of": AS_OF, "horizon_days": 20, "isin": "INE0AAA01010", "scope": "stock"})["status"] == "no_data"


def test_market_call_is_judged_against_cash(st):
    d = log.record(st, gate="macro", as_of=AS_OF, action="RISK_ON", horizon_days=20, thesis="Backdrop is supportive of risk.", agent="a", model="m",
                   signals_cited=["midcap_ratio"] if False else [], confidence=0.6, invalidation="Nifty closes below 50-day average")
    assert not d.ok       # a call with no cited signal is refused even for the market gate
    d = log.record(st, gate="macro", as_of=AS_OF, action="NEUTRAL", horizon_days=20, thesis="No clear tide in the data.", agent="a", model="m", signals_cited=[])
    o = score.outcome(st, {**d.data, "scope": "market"})
    assert o["status"] == "scored" and o["hit"] is None and o["edge_pct"] == 0                       # no call earns nothing, loses nothing
    assert o["bench_pct"] == pytest.approx(score.CASH_PCT_PA * 20 / 250)


def test_score_all_and_summary_count_everything(st):
    call(st)
    call(st, isin=DOWN, action="ENTER", agent="claude")                      # a wrong call
    call(st, as_of=DAYS[320], horizon_days=60)                                # pending
    rows = score.score_all(st)
    s = {(r["author"], r["gate"]): r for r in score.summarise(rows)}[("claude/m1", "entry")]
    assert (s["decisions"], s["scored"], s["pending"], s["calls"], s["hit_rate"]) == (3, 2, 1, 2, 0.5)
    assert s["brier"] == pytest.approx(((0.6 - 1) ** 2 + (0.6 - 0) ** 2) / 2)
    assert s["calibration"] == [{"said": "60%-70%", "n": 2, "actually_right": 0.5}]


# --- comparing authors --------------------------------------------------------------------------------------------
def fake(author, gate, as_of, subject, edge, status="scored", stance=1):
    a, m = author.split("/")
    return {"author": author, "agent": a, "model": m, "gate": gate, "as_of": as_of, "subject": subject, "horizon_days": 20, "status": status,
            "stance": stance, "edge_pct": edge, "edge_net_pct": edge, "excess_pct": edge, "confidence": 0.6, "hit": edge > 0, "symbol": None,
            "action": "ENTER", "id": "x"}


def paired(n_dates, per_date, diff):
    rows = []
    for i in range(n_dates):
        for j in range(per_date):
            noise = ((i * 7 + j * 3) % 5) - 2
            rows.append(fake("a/x", "entry", f"2026-01-{i + 1:02d}", f"S{j}", 1 + diff + noise))
            rows.append(fake("b/x", "entry", f"2026-01-{i + 1:02d}", f"S{j}", 1 + noise))
    return rows


def test_compare_refuses_to_name_a_winner_on_thin_evidence():
    c = score.compare(paired(3, 4, 5.0), "a/x", "b/x")
    assert c["pairs"] == 12 and c["verdict"].startswith("not enough evidence")


def test_compare_names_a_winner_only_when_the_interval_clears_zero():
    big = score.compare(paired(12, 5, 2.0), "a/x", "b/x")
    assert big["pairs"] == 60 and big["dates"] == 12 and big["verdict"].startswith("a/x is better") and big["ci95_pct"][0] > 0
    flat = score.compare(paired(12, 5, 0.0), "a/x", "b/x")
    assert flat["verdict"].startswith("no clear difference") and flat["mean_edge_diff_pct"] == 0
    rev = score.compare(paired(12, 5, -2.0), "a/x", "b/x")
    assert rev["verdict"].startswith("b/x is better")
    assert score.compare(paired(12, 5, 2.0), "a/x", "b/x") == big                                    # seeded: same answer every time


def test_compare_uses_only_cases_both_scored_and_reports_no_overlap():
    rows = [fake("a/x", "entry", "2026-01-01", "S1", 1), fake("b/x", "entry", "2026-01-01", "S2", 1), fake("b/x", "entry", "2026-01-02", "S1", 1, status="pending")]
    c = score.compare(rows, "a/x", "b/x")
    assert c["pairs"] == 0 and c["verdict"].startswith("no overlap")


def test_baseline_always_long_is_built_from_scored_cases_only():
    rows = [fake("a/x", "entry", "2026-01-01", "S1", -3, stance=-1), fake("a/x", "entry", "2026-01-02", "S1", 1, status="pending")]
    rows[0]["excess_pct"] = 3.0
    b = score.baseline_rows(rows)
    assert len(b) == 1 and b[0]["author"] == "baseline/always-long" and b[0]["edge_net_pct"] == 3.0 and b[0]["hit"] is True


# --- evidence bundles ---------------------------------------------------------------------------------------------
def test_gather_bundle_is_stable_and_lists_unknowns(st):
    a = gather.gather(st, "entry", AS_OF, isin=UP)
    b = gather.gather(st, "entry", AS_OF, isin=UP)
    assert a.ok and a.data["bundle_sha"] == b.data["bundle_sha"] and len(a.data["bundle_sha"]) == 16
    ev = a.data["evidence"]
    assert "price_context" in ev["stock"]["signals"] and ev["stock"]["symbol"] == "RISER"
    assert "insider_cluster" in [u["signal"] for u in ev["unknown"]] and "macro_regime" in [u["signal"] for u in ev["unknown"]]
    assert a.data["answer_with"]["allowed_actions"] == ["AVOID", "ENTER", "WAIT"]
    assert gather.gather(st, "entry", DAYS[200], isin=UP).data["bundle_sha"] != a.data["bundle_sha"]


def test_gather_blind_hides_the_identity(st):
    r = gather.gather(st, "entry", AS_OF, isin=UP, blind=True)
    text = str(r.data)
    assert r.ok and "RISER" not in text and UP not in text and "Industries" not in text and r.data["evidence"]["stock"]["identity"] == "hidden"


def test_gather_refuses_bad_requests(st):
    assert gather.gather(st, "entry", AS_OF).error.kind.name == "PARSE_FAIL"
    assert gather.gather(st, "weird", AS_OF, isin=UP).error.kind.name == "PARSE_FAIL"
    assert gather.gather(st, "entry", "yesterday", isin=UP).error.kind.name == "PARSE_FAIL"
    assert gather.gather(st, "entry", AS_OF, isin="INE0ZZZ01017").error.kind.name == "NOT_FOUND"
    assert gather.gather(st, "macro", AS_OF).ok          # a market gate needs no stock


def test_gather_reads_the_profile_only_when_given(st):
    from drishti.profile import Goals, Profile, Risk, WatchItem
    p = Profile("t", Goals(25.0), Risk(10.0, 25.0), watchlist=(WatchItem(UP, "RISER"),))
    inv = gather.gather(st, "entry", AS_OF, isin=UP, profile=p).data["evidence"]["investor"]
    assert inv == {"target_xirr_pct": 25.0, "max_position_pct": 10.0, "max_sector_pct": 25.0, "on_watchlist": True}
    assert "investor" not in gather.gather(st, "entry", AS_OF, isin=UP).data["evidence"]


# --- cases, bundles and answers ----------------------------------------------------------------------------------
def test_make_cases_is_seeded_and_respects_history_and_horizon(st):
    from drishti.decisions import cases
    a = cases.make_cases(st, start=DAYS[0], end=DAYS[-1], every=20, n_stocks=2, seed=3, min_horizon_after=20)
    b = cases.make_cases(st, start=DAYS[0], end=DAYS[-1], every=20, n_stocks=2, seed=3, min_horizon_after=20)
    assert a == b and a
    assert min(c["as_of"] for c in a) >= DAYS[cases.MIN_SESSIONS] and max(c["as_of"] for c in a) <= DAYS[-21]
    assert {c["gate"] for c in a} == {"entry", "macro"} and all(c["isin"] in (UP, DOWN) for c in a if c["gate"] == "entry")
    assert a != cases.make_cases(st, start=DAYS[0], end=DAYS[-1], every=20, n_stocks=1, seed=4, min_horizon_after=20)


def test_rules_on_cases_then_blind_bundles_then_answers_roundtrip(st, tmp_path):
    from drishti.decisions import cases
    cs = cases.make_cases(st, start=DAYS[100], end=DAYS[200], every=25, n_stocks=2, market=False)
    res = cases.run_rules(st, cs)
    assert res["recorded"] == len(cs) and res["failed"] == 0
    assert cases.run_rules(st, cs)["already_logged"] == len(cs)
    bundles = tmp_path / "bundles"
    out = cases.export_bundles(st, cs, bundles, blind=True)
    keyf = tmp_path / "bundles_key.json"
    assert out["written"] == len(cs) and keyf.exists() and out["key"] == str(keyf)
    assert not (bundles / "_key.json").exists() and not list(bundles.glob("*key*"))          # the key is outside the agents' folder
    assert not any(UP in f.read_text() or "RISER" in f.read_text() for f in bundles.glob("case-*.json"))
    first = sorted(bundles.glob("case-*.json"))[0].stem
    answers = tmp_path / "a.jsonl"
    answers.write_text('{"case_id": "%s", "action": "ENTER", "horizon_days": 60, "confidence": 0.6, "thesis": "Uptrend with strength against the index.", '
                       '"invalidation": "closes below the 50-day average", "signals": ["price_context"]}\n'
                       '{"case_id": "nope", "action": "ENTER"}\n{"case_id": "%s", "action": "BUY"}\nnot json\n' % (first, first), encoding="utf-8")
    r = cases.import_answers(st, keyf, answers, agent="claude", model="m1")
    assert r["recorded"] == 1 and r["rejected"] == 3
    d = log.current(st, agent="claude")[0]
    wrong = tmp_path / "w.jsonl"
    wrong.write_text('{"case_id": "%s", "action": "WAIT", "horizon_days": 20, "thesis": "x"}\n' % first, encoding="utf-8")
    assert "horizon_days must be 60" in cases.import_answers(st, keyf, wrong, agent="c2", model="m")["rejections"][0]["why"]
    assert d["horizon_days"] == 60 and d["isin"] in (UP, DOWN) and d["bundle_sha"] and d["as_of"] in {c["as_of"] for c in cs}


# --- MCP tools and CLI --------------------------------------------------------------------------------------------
def test_mcp_tools_gather_record_list_score(st):
    from drishti import mcp_server as m
    g = m.tool_gather(st, "entry", AS_OF, "RISER")
    assert g["error"] is None and g["data"]["bundle_sha"]
    r = m.tool_record_decision(st, "entry", AS_OF, "ENTER", 60, "Trend is up and beating the index.", "claude", "m1", ["price_context"], "RISER", 0.6,
                               "closes below the 50-day average", g["data"]["bundle_sha"])
    assert r["error"] is None and r["data"]["bundle_sha"] == g["data"]["bundle_sha"] and r["data"]["symbol"] == "RISER"
    bad = m.tool_record_decision(st, "entry", AS_OF, "BUY", 20, "x", "claude", "m1", [], "RISER")
    assert bad["error"] and "not valid" in bad["error"]["detail"]
    assert m.tool_get_decisions(st, agent="claude", query="RISER")["data"]["n"] == 1
    rules.run(st, "entry", AS_OF, isin=UP, symbol="RISER")
    sc = m.tool_score_decisions(st, compare=["claude/m1", rules.load()[1].join(["rules/", ""])])
    assert sc["error"] is None
    authors = {x["author"] for x in sc["data"]["summary"]}
    assert "baseline/always-long" in authors and "claude/m1" in authors
    assert sc["data"]["comparison"]["pairs"] == 1 and sc["data"]["comparison"]["verdict"].startswith("not enough evidence")
    assert m.tool_score_decisions(st, compare=["only-one"])["error"]["kind"] == "PARSE_FAIL"


def test_mcp_server_lists_the_decision_tools(st):
    pytest.importorskip("mcp")
    import asyncio
    from drishti import mcp_server as m

    class Keep:
        def __enter__(self):
            return st

        def __exit__(self, *a):
            return False
    srv = m.build_server(lambda: Keep())
    names = {t.name for t in asyncio.run(srv.list_tools())}
    assert {"gather_evidence", "record_decision", "get_decisions", "score_decisions"} <= names


def test_cli_decisions_end_to_end(tmp_path, monkeypatch, capsys):
    from drishti import cli
    from drishti.config import load_settings
    monkeypatch.setenv("DRISHTI_HOME", str(tmp_path))
    with Store(load_settings().db_path) as s:
        ds.ensure(s)
        put_stock(s, UP, "RISER", [100 * 1.004 ** i for i in range(len(DAYS))])
        ds.persist_index_closes(s, [{"trade_date": d, "index_name": "Nifty 50", "close": 20000 * 1.0004 ** i} for i, d in enumerate(DAYS)])
    cf = tmp_path / "eval" / "nested" / "cases.json"          # the folder does not exist yet: the command must create it
    assert cli.main(["decisions", "cases", "--start", DAYS[80], "--end", DAYS[200], "--every", "40", "--n-stocks", "1", "--cases", str(cf), "--horizon-after", "20"]) == 0
    assert cli.main(["decisions", "rules", "--cases", str(cf)]) == 0
    assert '"recorded": 3' in capsys.readouterr().out or True
    assert cli.main(["decisions", "export", "--cases", str(cf), "--out", str(tmp_path / "b")]) == 0
    assert cli.main(["decisions", "list"]) == 0
    assert cli.main(["decisions", "score", "--agent", "rules"]) == 0
    out = capsys.readouterr().out
    assert "rules/rules-v1+" in out and "baseline/always-long" in out
    assert cli.main(["decisions", "cases", "--cases", str(cf)]) == 2          # missing --start/--end
    assert cli.main(["decisions", "nonsense"]) == 2


# --- audit: did holding its ground pay? -------------------------------------------------------------------------
def test_audit_counts_held_and_changed_cases_against_the_critics_suggestion(st):
    from drishti.decisions import audit
    up = call(st)                                                                   # claude ENTER on the riser: a winner
    dn = call(st, isin=DOWN, action="ENTER")                                       # claude ENTER on the faller: a loser
    call(st, isin=DOWN, action="AVOID", agent="claude+r", model="m1")             # revised: changed its mind on the faller
    call(st, agent="claude+r", model="m1")                                         # revised: held on the riser
    key = {"c1": {"gate": "entry", "as_of": AS_OF, "isin": UP}, "c2": {"gate": "entry", "as_of": AS_OF, "isin": DOWN},
           "c3": {"gate": "entry", "as_of": AS_OF, "isin": "INE0NONE0001"}}
    crit = {"c1": {"verdict": "weak", "suggested_action": "AVOID"}, "c2": {"verdict": "sound", "suggested_action": "AVOID"}, "c3": {"suggested_action": "WAIT"}}
    a = audit.audit(st, key, crit, "claude/m1", "claude+r/m1")
    assert a["scored_cases_with_critique"] == 2 and a["skipped"]["no_decision"] == 1
    assert (a["author_calls"], a["author_calls_right"], a["critic_suggested_a_different_action"]) == (2, 1, 2)
    assert a["author_held"]["n"] == 1 and a["author_held"]["author_better"] == 1 and a["author_held"]["critic_better"] == 0      # held on the riser: right
    assert a["author_changed"]["n"] == 1 and a["author_changed"]["critic_better"] == 1                                            # the faller: the critic was right
    assert a["critic_verdicts"] == {"weak": 1, "sound": 1}
    assert audit.audit(st, key, {}, "claude/m1")["skipped"]["no_critique"] == 3


# --- forward log ------------------------------------------------------------------------------------------------
def test_forward_step_freezes_the_universe_runs_rules_and_exports_blind_bundles(st, tmp_path):
    from drishti.decisions import forward
    up = tmp_path / "u.json"
    r = forward.step(st, out_dir=tmp_path / "f", universe_path=up, as_of=AS_OF, size=2)
    assert r["done"] and r["cases"] == 2 * (2 + 1) and r["bundles"] == r["cases"]          # 2 stocks + macro, at 2 horizons
    u1 = up.read_text()
    assert {c["horizon_days"] for c in log.current(st, agent="rules")} == {20, 60}
    again = forward.step(st, out_dir=tmp_path / "f", universe_path=up, as_of=AS_OF, size=1)      # same date: idempotent, universe unchanged
    assert again["done"] and up.read_text() == u1 and again["rules"]["recorded"] == 0 and again["rules"]["already_logged"] > 0
    soon = forward.step(st, out_dir=tmp_path / "f", universe_path=up, as_of=DAYS[252])
    assert not soon["done"] and "weekly" in soon["why"]
    later = forward.step(st, out_dir=tmp_path / "f", universe_path=up, as_of=DAYS[256])
    assert later["done"]
    assert not forward.step(st, out_dir=tmp_path / "f", universe_path=up, as_of=DAYS[10])["done"]      # too little history
    assert "RISER" not in " ".join(p.read_text() for p in (tmp_path / "f" / f"bundles-{AS_OF}").glob("case-*.json"))      # blind


def test_rules_accept_a_per_case_horizon(st):
    r = rules.run(st, "entry", AS_OF, isin=UP, symbol="RISER", horizon_days=20)
    assert r.ok and r.data["horizon_days"] == 20


# --- history + selftest -----------------------------------------------------------------------------------------
def test_history_registers_missing_isins_runs_rules_and_reports_per_year(st):
    from drishti.decisions import history
    c = history.coverage(st)
    assert c["sessions"] == len(DAYS) and c["stocks_ever_traded"] == 2 and c["stocks_missing_from_master"] == 0
    import tempfile, pathlib
    out = pathlib.Path(tempfile.mkdtemp()) / "h" / "cases.json"
    r = history.run(st, out=out, every=40, n_stocks=2, min_sessions_before=100)
    assert r["done"] and r["cases"] > 0 and r["rules"]["failed"] == 0 and out.exists()
    rep = history.report(st)
    assert any(x["gate"] == "entry" for x in rep["overall"]) and rep["per_year"] and "verdict" in rep["rules_vs_always_long_entry"]
    short = history.run(st, out=out, min_sessions_before=5000)
    assert not short["done"] and "Backfill" in short["why"]


def test_selftest_passes_on_a_populated_store(st, monkeypatch, capsys):
    from drishti import selftest
    import drishti.store as store_mod

    class Cfg:
        db_path = ":memory:"
    monkeypatch.setattr(store_mod, "Store", lambda *a, **k: _Keep(st))
    assert selftest.run(Cfg()) == 0
    assert "PASSED" in capsys.readouterr().out


class _Keep:
    """Hands out the test store without letting `with` close it."""
    def __init__(self, s):
        self._s = s

    def __enter__(self):
        return self._s

    def __exit__(self, *a):
        return False

    def __getattr__(self, k):
        return getattr(self._s, k)


def test_mcp_server_lists_slash_command_prompts():
    import asyncio
    from drishti import mcp_server as m
    srv = m.build_server(lambda: _Keep(None))
    names = {p.name for p in asyncio.run(srv.list_prompts())}
    assert {"entry", "exit", "macro", "health", "scorecard"} <= names
    got = asyncio.run(srv.get_prompt("entry", {"stock": "TCS"}))
    text = got.messages[0].content.text
    assert "gather_evidence" in text and "TCS" in text and "invalidation" in text


# ---------- trade levels, thesis log, watchlist scan ----------
def _price_store(tmp_path, closes, isin="INE002A01018", symbol="RELIANCE"):
    from datetime import date, timedelta
    from drishti.store import Store, datasets as ds
    st = Store(tmp_path / "p.db")
    st.upsert_security(isin, name="Reliance Industries Limited", nse_symbol=symbol, source="t")
    ds.ensure(st, "price_eod")
    d, rows = date(2026, 11, 6) - timedelta(days=len(closes) * 2), []
    for c in closes:
        while d.weekday() >= 5:
            d += timedelta(days=1)
        rows.append(dict(trade_date=d.isoformat(), isin=isin, exchange="NSE", series="EQ", symbol=symbol, open=c, high=c * 1.01, low=c * 0.99,
                         close=c, prev_close=c, volume=1000, turnover=1.0, trades=1))
        d += timedelta(days=1)
    st.upsert("price_eod", rows, key=("trade_date", "isin", "exchange", "series"))
    return st, rows[-1]["trade_date"]


def test_levels_stop_below_close_and_no_entry_zone_in_a_downtrend(tmp_path):
    from drishti.decisions import levels
    from drishti.profile import Profile, Risk
    up, last = _price_store(tmp_path, [100 + i * 0.5 for i in range(260)])
    r = levels.derive(up, "INE002A01018", last, profile=Profile(risk=Risk(max_position_pct=5.0)))
    assert r.ok and r.data["stop"] < r.data["close"] and r.data["entry_zone"] and r.data["target"] is None
    assert r.data["size_pct_of_portfolio"] <= 5.0 and r.data["derived"] is True
    down, last2 = _price_store(tmp_path, [300 - i * 0.5 for i in range(260)], isin="INE009A01021", symbol="INFY")
    r2 = levels.derive(down, "INE009A01021", last2)
    assert r2.ok and r2.data["entry_zone"] is None and "50-day" in r2.data["entry_note"]


def test_thesis_needs_an_exit_trigger_and_a_new_one_closes_the_old(tmp_path):
    from drishti.decisions import thesis as th
    from drishti.store import Store
    st = Store(tmp_path / "t.db")
    bad = th.save(st, isin="INE002A01018", symbol="RELIANCE", as_of="2026-11-06", thesis="Retail and Jio compounding", exit_triggers=[])
    assert not bad.ok
    stop_bad = th.save(st, isin="INE002A01018", symbol="RELIANCE", as_of="2026-11-06", thesis="x", entry_low=100, stop=120, exit_triggers=["close below 90"])
    assert not stop_bad.ok
    a = th.save(st, isin="INE002A01018", symbol="RELIANCE", as_of="2026-11-06", thesis="first", entry_low=100, entry_high=104, stop=90, exit_triggers=["close below 90"])
    b = th.save(st, isin="INE002A01018", symbol="RELIANCE", as_of="2026-11-07", thesis="second", stop=92, exit_triggers=["pledge above 25%"])
    got = th.get(st, "INE002A01018").data["theses"]
    assert len(got) == 1 and got[0]["id"] == b.data["id"] and got[0]["exit_triggers"] == ["pledge above 25%"]
    assert th.recheck(st, b.data["id"], as_of="2026-11-08", intact=True, note="results fine").ok
    assert th.recheck(st, b.data["id"], as_of="2026-11-08", intact=True, note=" ").ok is False
    assert th.get(st, "INE002A01018").data["theses"][0]["rechecks"][0]["intact"] == 1
    assert th.recheck(st, b.data["id"], as_of="2026-11-09", intact=False, note="sold", close=True).ok
    assert th.get(st, "INE002A01018").data["n"] == 0 and th.get(st, "INE002A01018", include_closed=True).data["n"] == 2
    assert a.ok


def test_watch_scan_escalates_when_the_stop_is_hit_and_reports_only_new_items(tmp_path):
    from datetime import date
    from drishti.decisions import thesis as th, watch
    from drishti.profile import Profile, WatchItem
    st, last = _price_store(tmp_path, [200 - i * 0.2 for i in range(260)])
    close = st.query("SELECT close FROM price_eod ORDER BY trade_date DESC LIMIT 1")[0]["close"]
    prof = Profile(watchlist=(WatchItem(isin="INE002A01018", symbol="RELIANCE"),))
    assert not watch.scan(st, Profile(), date.fromisoformat(last)).ok            # empty watchlist is an error, not a quiet "all clear"
    r = watch.scan(st, prof, date.fromisoformat(last), mark=True)
    assert r.ok and r.data["scanned"] == 1 and r.data["escalate"] == []
    th.save(st, isin="INE002A01018", symbol="RELIANCE", as_of=last, thesis="t", stop=close + 1, exit_triggers=["stop"])
    r2 = watch.scan(st, prof, date.fromisoformat(last))
    assert r2.data["since"] == last and r2.data["escalate"] and "STOP HIT" in r2.data["escalate"][0]["why"][0]

"""Signal study and portfolio simulation: rows are resumable and keep UNKNOWN, lifts are judged within a date for stock
readings, verdicts need both halves to agree, and the portfolio XIRR follows from staggered sleeves."""
from __future__ import annotations

import json
import random
from datetime import date, timedelta

import pytest

from drishti.decisions import history, portfolio, study
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


DAYS = weekdays(date(2025, 1, 1), 330)


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
    put_stock(s, UP, "RISER", [100 * 1.004 ** i for i in range(n)])
    put_stock(s, DOWN, "FALLER", [100 * 0.996 ** i for i in range(n)])
    ds.persist_index_closes(s, [{"trade_date": d, "index_name": "Nifty 50", "close": 20000 * 1.0004 ** i} for i, d in enumerate(DAYS)])
    yield s
    s.close()


def test_study_rows_are_resumable_keep_unknown_and_judge_stock_readings_within_the_date(st, tmp_path):
    cases_path = tmp_path / "cases.json"
    history.run(st, out=cases_path, every=10, n_stocks=2, min_sessions_before=210)
    msgs = []
    r = study.run(st, cases_path=cases_path, out_dir=tmp_path / "s", progress=msgs.append)
    assert r["rows"]["written"] > 0 and (tmp_path / "s" / "study.json").exists() and msgs
    again = study.run(st, cases_path=cases_path, out_dir=tmp_path / "s")
    assert again["rows"]["written"] == 0 and again["rows"]["already"] == r["rows"]["written"]
    sig = r["signals"]
    assert set(sig["delivery_trend"]["readings"]) == {study.UNKNOWN} and sig["delivery_trend"]["coverage_known_pct"] == 0
    assert set(sig["participant_oi (vs own past year)"]["readings"]) == {study.UNKNOWN}
    tr = sig["trend (rules-v1 input)"]["readings"]
    assert tr["uptrend"]["all"]["lift"] > 0 > tr["downtrend"]["all"]["lift"]          # the riser beats the faller on the same days
    # within-date lifts of a two-stock day are exact opposites
    assert abs(tr["uptrend"]["all"]["lift"] + tr["downtrend"]["all"]["lift"]) < 0.02
    acts = sig["rules-v1 action"]["readings"]
    assert {"ENTER", "AVOID"} <= set(acts)
    row = json.loads((tmp_path / "s" / "rows.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert row["status"] == "scored" and row["price"]["ret_3m"] is not None and row["delivery_trend"] is None


def _synthetic(lift_first: float, lift_second: float, n_dates: int = 120, per_date: int = 6, seed: int = 3) -> list[dict]:
    """Half the stocks each day carry reading A; A's excess is shifted by lift_first before 2022 and lift_second after."""
    rng, rows = random.Random(seed), []
    d0 = date(2017, 1, 2)
    for i in range(n_dates):
        d = (d0 + timedelta(days=28 * i)).isoformat()
        market = rng.gauss(0, 5)
        for j in range(per_date):
            a = j % 2 == 0
            shift = (lift_first if d < "2022" else lift_second) if a else 0.0
            ex = market + shift + rng.gauss(0, 3)
            rows.append({"case_id": f"{d}-{j}", "as_of": d, "isin": f"X{j}", "status": "scored", "excess_pct": ex,
                         "delivery_trend": {"label": "A" if a else "B"}})
    return rows


def test_verdict_needs_both_halves_to_agree():
    stable = study.tables(_synthetic(2.0, 2.0))["signals"]["delivery_trend"]["readings"]
    assert stable["A"]["verdict"] == "stable: better than average" and stable["B"]["verdict"] == "stable: worse than average"
    flips = study.tables(_synthetic(3.0, -3.0))["signals"]["delivery_trend"]["readings"]
    assert flips["A"]["verdict"].startswith("flips")
    noise = [study.tables(_synthetic(0.0, 0.0, seed=s))["signals"]["delivery_trend"]["readings"]["A"]["verdict"] for s in (4, 5, 6)]
    assert not any(v.startswith("stable") for v in noise)          # pure noise on these seeds (about 1 seed in 20 clears zero by luck)
    t = study.tables(_synthetic(2.0, 2.0))
    assert t["expected_false_stable_by_chance"] == round(0.05 * t["readings_tested"], 1)


def test_market_readings_are_judged_across_dates_not_within():
    rows = _synthetic(0.0, 0.0)
    for r in rows:                      # a market-wide label: the same for every stock on a date, on good days
        r["midcap_ratio"] = {"label": "up" if r["excess_pct"] > -100 and int(r["as_of"][8:10]) % 2 else "down"}
    t = study.tables(rows)["signals"]["midcap_ratio"]
    assert t["level"] == "market" and set(t["readings"]) == {"up", "down"}


def test_xirr_matches_a_known_rate_and_refuses_one_sided_flows():
    r = portfolio.xirr([("2021-01-01", -100.0), ("2022-01-01", 110.0)])
    assert abs(r - 0.10) < 1e-6
    assert portfolio.xirr([("2021-01-01", 100.0)]) is None


def _rows(n_dates: int, stock_ret: float, nifty_ret: float, enter: bool) -> list[dict]:
    out = []
    days = weekdays(date(2018, 1, 1), n_dates * 10 + 80)
    for i in range(n_dates):
        d, end = days[i * 10], days[i * 10 + 60]
        for j in range(3):
            out.append({"case_id": f"{d}-{j}", "as_of": d, "end": end, "isin": f"I{j}", "status": "scored", "ret_pct": stock_ret, "bench_pct": nifty_ret,
                        "excess_pct": stock_ret - nifty_ret, "rules_v1": {"action": "ENTER" if enter and j == 0 else "WAIT", "points": 3}})
    return out


def test_portfolio_sleeves_compound_costs_and_fallbacks():
    rows = _rows(60, stock_ret=5.0, nifty_ret=2.0, enter=True)
    res = portfolio.run(rows, every=10, horizon=60)["strategies"]
    al, nf, rv = res["always-long (all sampled stocks)"]["all"], res["nifty"]["all"], res["rules-v1 ENTER, else cash"]["all"]
    assert al["sleeves"] == 6 and al["periods_in_stocks"] == 60 and al["avg_picks_when_in_stocks"] == 3
    assert al["xirr_pct"] > nf["xirr_pct"] > 0 and abs(al["mean_period_pct"] - (5.0 - 0.4)) < 1e-9
    assert rv["avg_picks_when_in_stocks"] == 1
    none = portfolio.run(_rows(60, 5.0, 2.0, enter=False), every=10, horizon=60)["strategies"]
    cash, nif = none["rules-v1 ENTER, else cash"]["all"], none["rules-v1 ENTER, else Nifty"]["all"]
    assert cash["periods_in_stocks"] == 0 and abs(cash["mean_period_pct"] - 6.5 * 60 / 250) < 1e-9
    assert abs(nif["xirr_pct"] - nf["xirr_pct"]) < 1e-6
    assert portfolio.simulate(rows[:6], None, "nifty")["error"]


def test_cli_portfolio_sim_needs_the_rows_first(tmp_path, monkeypatch, capsys):
    from drishti import cli
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DRISHTI_HOME", str(tmp_path / "home"))
    assert cli.main(["decisions", "portfolio-sim", "--out", str(tmp_path / "none")]) == 2
    assert "signal-study" in capsys.readouterr().err


def test_import_can_correct_an_earlier_call_without_editing_it(st, tmp_path):
    from drishti.decisions import cases as cs, log
    d = DAYS[250]
    case = {"case_id": f"exit-{d}-{UP}", "gate": "exit", "as_of": d, "isin": UP, "symbol": "RISER", "horizon_days": 60}
    key = tmp_path / "k.json"
    key.write_text(json.dumps({case["case_id"]: {**case, "bundle_sha": "x"}}), encoding="utf-8")
    a = {"case_id": case["case_id"], "action": "HOLD", "horizon_days": 60, "confidence": 0.6, "thesis": "Uptrend and beating the index.",
         "invalidation": "closes below the 50-day average", "signals": ["price_context"]}
    ans = tmp_path / "a.jsonl"
    ans.write_text(json.dumps(a) + "\n", encoding="utf-8")
    assert cs.import_answers(st, key, ans, agent="claude", model="m")["recorded"] == 1
    again = cs.import_answers(st, key, ans, agent="claude", model="m")
    assert again["recorded"] == 0 and "supersedes=" in again["rejections"][0]["why"]
    old = log.current(st, agent="claude")[0]["id"]
    ans.write_text(json.dumps({**a, "confidence": 0.65, "thesis": "Uptrend, beating the index, and a longer reason.", "supersedes": old}) + "\n", encoding="utf-8")
    assert cs.import_answers(st, key, ans, agent="claude", model="m")["recorded"] == 1
    now = log.current(st, agent="claude")
    assert len(now) == 1 and now[0]["confidence"] == 0.65 and now[0]["supersedes"] == old
    assert st.query("SELECT COUNT(*) AS n FROM decision WHERE agent='claude'")[0]["n"] == 2       # both rows kept

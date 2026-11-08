"""Signals: v1-fidelity for the ported ones (participant OI, sector rotation, macro regime), hand-checked values for the
new ones, and the rule that a missing input is UNKNOWN, never neutral."""
from __future__ import annotations

import math
from datetime import date, timedelta
from pathlib import Path

import pytest

from drishti import signals
from drishti.providers import nse_archive, yahoo
from drishti.signals import calendar, cycle, disclosure, flows, market
from drishti.signals.base import params
from drishti.store import Store
from drishti.store import datasets as ds

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture
def st():
    s = Store()
    ds.ensure(s)
    yield s
    s.close()


def weekdays(end: date, n: int) -> list[str]:
    out, d = [], end
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d -= timedelta(days=1)
    return list(reversed(out))


def put_index(st, name, dates, values):
    ds.persist_index_closes(st, [{"trade_date": d, "index_name": name, "close": v} for d, v in zip(dates, values)])


# --- participant OI: v1 fidelity ------------------------------------------------------------------------------
def oi_row(d, who, fl, fs, cl, cs, pl, ps):
    return {"trade_date": d, "client_type": who, "fut_idx_long": fl, "fut_idx_short": fs, "fut_stk_long": 0, "fut_stk_short": 0,
            "opt_idx_call_long": cl, "opt_idx_put_long": pl, "opt_idx_call_short": cs, "opt_idx_put_short": ps,
            "opt_stk_call_long": 0, "opt_stk_put_long": 0, "opt_stk_call_short": 0, "opt_stk_put_short": 0, "total_long": 0, "total_short": 0}


def test_participant_oi_labels_follow_v1(st):
    # FII: futures net +100 rising (strong bull +2), calls net +50 falling (mild bull +1), puts net -30 falling further (strong bull +2) = 5
    # Pro: all flat vs yesterday, futures net -10 (strong bear -2), calls net -5 (strong bear -2), puts net +5 (strong bear -2) = -6
    # Client: futures net -100 rising toward 0 (mild bear -1), calls net 0 unchanged (strong bear -2), puts net +40 falling (mild bear -1) = -4
    # DII: anything (ignored in the smart score)
    y = [oi_row("2026-11-05", "FII", 100, 10, 60, 0, 0, 20), oi_row("2026-11-05", "Pro", 0, 10, 0, 5, 5, 0),
         oi_row("2026-11-05", "Client", 0, 150, 10, 10, 50, 0), oi_row("2026-11-05", "DII", 0, 0, 0, 0, 0, 0)]
    t = [oi_row("2026-11-06", "FII", 110, 10, 50, 0, 0, 30), oi_row("2026-11-06", "Pro", 0, 10, 0, 5, 5, 0),
         oi_row("2026-11-06", "Client", 0, 100, 10, 10, 40, 0), oi_row("2026-11-06", "DII", 5, 0, 0, 0, 0, 0)]
    ds.persist_participant_oi(st, y + t)
    r = market.participant_oi(st, date(2026, 11, 6))
    s = r.data
    assert s["details"]["participant_scores"] == {"FII": 5, "DII": 2 + (-2) + (-2), "Pro": -6, "Client": -4}
    assert s["score"] == (5 + -6) - (-4) == 3 and s["label"] == "no strong divergence"
    fii = {x["segment"]: x["label"] for x in s["details"]["stance"]["FII"]}
    assert fii == {"Index Futures": "Strong Bullish", "Call Options": "Mild Bullish", "Put Options": "Strong Bullish"}
    assert s["as_of"] == "2026-11-06" and s["missing"] == []


def test_participant_oi_strong_threshold_and_first_day(st):
    ds.persist_participant_oi(st, [oi_row("2026-11-06", "FII", 100, 0, 100, 0, 0, 100), oi_row("2026-11-06", "Pro", 100, 0, 100, 0, 0, 100),
                                   oi_row("2026-11-06", "Client", 0, 100, 0, 100, 100, 0), oi_row("2026-11-06", "DII", 0, 0, 0, 0, 0, 0)])
    s = market.participant_oi(st, date(2026, 11, 6)).data
    # no prior day: change = 0 as in v1 -> bullish nets are "Mild Bullish" (+1 each): FII 3, Pro 3; Client bearish nets -> Strong Bearish (-2 x3) = -6
    assert s["details"]["participant_scores"]["FII"] == 3 and s["details"]["participant_scores"]["Client"] == -6
    assert s["score"] == 12 and s["label"] == "strong bullish" and s["missing"]


def test_participant_oi_real_file_and_unknown(st):
    rows = nse_archive.parse_participant_oi((FIX / "nse_archive" / "fao_participant_oi_06112026.csv").read_bytes())
    ds.persist_participant_oi(st, rows)
    r = market.participant_oi(st, date(2026, 11, 8))
    assert r.ok and r.as_of == "2026-11-06" and -18 <= r.data["score"] <= 18
    with Store() as empty:
        u = market.participant_oi(empty)
        assert not u.ok and "UNKNOWN (not neutral)" in u.error.detail


# --- sector rotation: v1 fidelity ----------------------------------------------------------------------------
def test_sector_rotation_quadrants_match_v1_math(st):
    dates = weekdays(date(2026, 11, 6), 45)
    put_index(st, "Nifty 50", dates, [100.0] * 45)
    put_index(st, "Nifty IT", dates, [100 + 0.02 * i * i for i in range(45)])          # accelerating up: Leading
    put_index(st, "Nifty Bank", dates, [100 + 0.5 * i for i in range(45)])               # linear up: momentum >0, slowly fading
    put_index(st, "Nifty Pharma", dates, [120 - 0.02 * i * i for i in range(45)])        # accelerating down: Lagging
    put_index(st, "Nifty FMCG", dates, [100 - (i - 30) * 0.3 if i > 30 else 100 - 0.0 * i for i in range(45)][:-3] +
              [95.5, 95.8, 96.4])                                                        # below average but turning up: Improving
    r = market.sector_rotation(st, date(2026, 11, 6))
    rows = {x["name"]: x for x in r.data["details"]["sectors"]}
    assert rows["IT"]["quadrant"] == "Leading" and rows["Pharma"]["quadrant"] == "Lagging"
    assert rows["Bank"]["quadrant"] == "Weakening"     # v1: momentum >= 0 and acceleration <= 0
    assert rows["FMCG"]["quadrant"] == "Improving"
    # momentum is exactly v1's ((rs - ma20) / ma20) * 100 on the last point
    rs = [(100 + 0.02 * i * i) / 100 for i in range(45)]
    ma = sum(rs[-20:]) / 20
    assert rows["IT"]["momentum"] == round((rs[-1] - ma) / ma * 100, 2)
    assert set(r.data["details"]["unavailable"]) >= {"Metal", "Auto", "Realty", "Energy", "Midcap 150"}  # no data -> named, not dropped


def test_rotation_alert_on_fresh_cross(st):
    dates = weekdays(date(2026, 11, 6), 40)
    put_index(st, "Nifty 50", dates, [100.0] * 40)
    put_index(st, "Nifty Metal", dates, [100 - 0.2 * i for i in range(38)] + [99.0, 99.5])
    rows = {x["name"]: x for x in market.sector_rotation(st, date(2026, 11, 6)).data["details"]["sectors"]}
    assert rows["Metal"]["rotation_alert"] is True


def test_sector_rotation_needs_history(st):
    put_index(st, "Nifty 50", weekdays(date(2026, 11, 6), 10), [100] * 10)
    assert not market.sector_rotation(st, date(2026, 11, 6)).ok


# --- macro regime: v1 thresholds ------------------------------------------------------------------------------
def put_macro(st, series, pts):
    ds.persist_macro(st, series, [{"date": d, "value": v} for d, v in pts], "test")


def test_macro_regime_scores_like_v1(st):
    put_macro(st, "yahoo:DX-Y.NYB", [("2026-11-06", 105.2)])          # > 104 -> -1
    put_macro(st, "yahoo:^TNX", [("2026-11-06", 4.1)])                # between -> 0
    put_macro(st, "yahoo:BZ=F", [("2026-11-06", 72.0)])               # < 75 -> +1
    put_macro(st, "yahoo:INR=X", [("2026-11-05", 88.0), ("2026-11-06", 88.2)])   # +0.23% -> -1
    put_macro(st, "yahoo:^VIX", [("2026-11-06", 21.0)])
    put_index(st, "India VIX", ["2026-11-05", "2026-11-06"], [17.0, 18.0])      # between; US VIX 21 > 18 + 2 -> -1
    s = market.macro_regime(st, date(2026, 11, 7)).data
    contrib = {c["input"]: c["contribution"] for c in s["components"]}
    assert contrib == {"DXY": -1, "US_10Y": 0, "Brent_Crude": 1, "India_VIX": 0, "USD_INR": -1, "US_VIX": -1}
    assert s["score"] == -2 and s["label"] == "bearish backdrop" and s["missing"] == []
    assert "Bearish: DXY (dollar index) elevated at 105.20 (>104.0)" in s["details"]["signals"]
    assert any("US VIX premium" in x for x in s["details"]["signals"])


def test_macro_regime_stale_or_missing_inputs_are_unknown(st):
    put_macro(st, "yahoo:DX-Y.NYB", [("2026-10-01", 99.0)])           # 5+ days old -> missing, not +1
    put_macro(st, "yahoo:BZ=F", [("2026-11-06", 80.0)])
    s = market.macro_regime(st, date(2026, 11, 7)).data
    assert "DXY" in s["missing"] and "USD_INR" in s["missing"] and s["score"] == 0
    assert s["details"]["signals"] == ["Neutral: no macro extreme triggered"]
    with Store() as empty:
        assert not market.macro_regime(empty, date(2026, 11, 7)).ok


def test_yahoo_chart_fixture():
    rows = yahoo.parse_chart((FIX / "yahoo" / "nsei_6mo.json").read_bytes(), "^NSEI")
    assert rows[-1]["date"] == "2026-11-06" and math.isclose(rows[-1]["close"], 25030.35, abs_tol=0.01)
    assert len(rows) == 128 and all(a["date"] < b["date"] for a, b in zip(rows, rows[1:]))
    from drishti.core.envelope import DrishtiError
    with pytest.raises(DrishtiError):
        yahoo.parse_chart((FIX / "yahoo" / "nsei_6mo.json").read_bytes(), "DX-Y.NYB")   # response for another symbol


# --- midcap ratio + seasonality ------------------------------------------------------------------------------
def test_midcap_ratio(st):
    dates = weekdays(date(2026, 11, 6), 90)
    put_index(st, "Nifty 50", dates, [100.0] * 90)
    put_index(st, "Nifty Midcap 150", dates, [50 + 0.1 * i for i in range(90)])
    s = market.midcap_ratio(st, date(2026, 11, 6)).data
    assert s["label"] == "midcaps outperforming" and s["score"] == round(((50 + 8.9) / (50 + 2.9) - 1) * 100, 2)


def test_seasonality_today():
    s = calendar.seasonality(on=date(2026, 11, 8)).data
    assert s["details"]["quarter"] == 4 and "FII tax-selling" in s["label"]
    assert s["details"]["results_season"]["quarter"] == "Q2 FY27" and s["details"]["results_season"]["deadline"] == "2026-11-14"
    assert s["details"]["next_mpc"] == {"decision_date": "2026-12-04", "days_away": 26}
    comps = {c["input"]: c["value"] for c in s["components"]}
    assert comps["budget cycle"].startswith("pre-budget") and comps["monsoon"] == "post-monsoon"


@pytest.mark.parametrize("d, q", [(date(2026, 5, 20), "Q4 FY26"), (date(2026, 8, 1), "Q1 FY27"), (date(2027, 1, 20), "Q3 FY27")])
def test_results_season_names(d, q):
    assert calendar.results_season(d)["quarter"] == q


def test_outside_results_season_and_after_mpc_list():
    assert calendar.results_season(date(2026, 12, 20)) is None
    s = calendar.seasonality(on=date(2027, 3, 1)).data
    assert s["missing"] and "MPC" in s["missing"][0]


# --- flows ---------------------------------------------------------------------------------------------------
def test_fii_dii_streaks_and_windows(st):
    for i, d in enumerate(weekdays(date(2026, 11, 6), 22)):
        ds.persist_fii_dii(st, {"date": d, "fii": {"buy": 1, "sell": 1, "net": -100.0 if i >= 19 else 50.0},
                                "dii": {"buy": 1, "sell": 1, "net": 80.0}})
    s = flows.fii_dii(st, date(2026, 11, 6)).data
    c = {x["input"]: x["value"] for x in s["components"]}
    assert c["FII streak"] == -3 and c["DII streak"] == 20 and c["FII 5d"] == -300 + 100 and c["FII 20d"] == -300 + 17 * 50
    assert s["label"] == "FII buying, DII buying" and s["provisional"]


def test_fii_dii_short_history_says_so(st):
    ds.persist_fii_dii(st, {"date": "2026-11-06", "fii": {"buy": 1, "sell": 1, "net": -10.0}, "dii": {"buy": 1, "sell": 1, "net": 5.0}})
    s = flows.fii_dii(st, date(2026, 11, 6)).data
    assert s["missing"] and s["label"] == "FII selling, DII buying"


def chain(d="2026-11-06", exp="2026-11-11"):
    rows = []
    for k, ce, pe in ((24800, 100, 900), (24900, 300, 700), (25000, 800, 600), (25100, 1200, 200), (25200, 600, 50)):
        for t, oi in (("CE", ce), ("PE", pe)):
            rows.append({"trade_date": d, "instrument_type": "IDO", "symbol": "NIFTY", "expiry": exp, "strike": float(k), "option_type": t,
                         "oi": oi, "oi_change": 1, "volume": 1, "close": 1.0, "settle": 1.0, "underlying": 25030.35, "lot_size": 75})
    return rows


def test_options_flow_pcr_max_pain_walls(st):
    ds.persist_fo_index(st, chain() + chain(exp="2026-11-25"))
    s = flows.options_flow(st, date(2026, 11, 6), "NIFTY").data
    assert s["score"] == round(2450 / 3000, 3) and s["label"] == "balanced" and s["provisional"]
    # max pain by hand: payout at 25000 = calls 100*200 + 300*100 + puts 700*100 + 900*200 = 300000 (the minimum here)
    assert flows.max_pain(chain()) == 25000.0
    d = s["details"]
    assert [w["strike"] for w in d["call_walls"]] == [25100.0, 25000.0, 25200.0] and d["put_walls"][0]["strike"] == 24800.0
    assert d["expiry"] == "2026-11-11"


def test_liquidity_and_credit(st):
    put_macro(st, "fred:WALCL", [(f"2026-{8 + i // 5:02d}-{1 + (i % 5) * 6:02d}", 6800 - i * 5) for i in range(14)])
    put_macro(st, "fred:M2SL", [(f"20{25 + (i + 10) // 12}-{(i + 10) % 12 + 1:02d}-01", 21000 + i * 20) for i in range(13)])
    s = flows.liquidity(st, date(2026, 11, 8)).data
    assert "mixed" in s["label"]    # balance sheet shrinking, M2 growing
    put_macro(st, "fred:BAMLH0A0HYM2", [(d, 3.0 + 0.03 * i) for i, d in enumerate(weekdays(date(2026, 11, 6), 21))])
    c = flows.credit_spreads(st, date(2026, 11, 6)).data
    assert c["score"] == 60.0 and c["label"] == "credit stress building" and "US 10Y-2Y curve" in c["missing"]


# --- cycle phase --------------------------------------------------------------------------------------------
def test_cycle_phase_late_when_brent_high(st):
    put_macro(st, "yahoo:BZ=F", [("2026-11-06", 95.0)])
    s = cycle.cycle_phase(st, date(2026, 11, 6)).data
    assert s["details"]["probabilities"]["Late"] == 1.0 and s["label"].startswith("Late")
    assert "India/US/China PMI" in s["missing"] and any("FII selling" in m for m in s["missing"])


def test_cycle_phase_with_nothing_is_no_clear_phase(st):
    s = cycle.cycle_phase(st, date(2026, 11, 6)).data
    assert s["label"] == "no clear phase" and all(v == 0.25 for v in s["details"]["probabilities"].values())


# --- disclosure (pulled forward) -------------------------------------------------------------------------------
def insider(st, rows):
    from drishti.jobs import INSIDER_DDL
    st.ensure_table(INSIDER_DDL)
    base = dict(app_id="1", filed_at="2026-11-07T10:00:00", symbol="ABC", isin="INE002A01018", scrip_code=None, company="ABC",
                quantity=1, held_pre_shares=0, held_post_shares=1, held_pre_pct=0.0, held_post_pct=0.0, holding_reconciles=1,
                intimation_date="2026-11-07", filing_date="2026-11-07", exchange="NSE", revised=0, form_version="2.0",
                stored_at="2026-11-08T00:00:00", txn_type="x")
    st.upsert("insider_event", [{**base, **r, "from_date": r["to_date"]} for r in rows], ("event_key",))


def test_insider_selling_cluster_and_size_buying(st):
    insider(st, [
        dict(event_key="a", person="P1", category="Promoter", direction="SELL", mode="Market Sale", value_inr=1e6, to_date="2026-10-20"),
        dict(event_key="b", person="P2", category="Director", direction="SELL", mode="Market Sale", value_inr=1e6, to_date="2026-10-28"),
        dict(event_key="c", person="P3", category="KMP", direction="SELL", mode="Market Sale", value_inr=1e6, to_date="2026-11-05"),
        dict(event_key="d", person="P4", category="Promoter", direction="SELL", mode="Gift", value_inr=9e9, to_date="2026-11-05"),   # not market
        dict(event_key="e", person="P5", category="Promoter Group", direction="BUY", mode="Market Purchase", value_inr=2e7, to_date="2026-11-01"),
    ])
    s = disclosure.insider_cluster(st, "INE002A01018", date(2026, 11, 8)).data
    assert s["label"] == "conflicting insider activity" and len(s["details"]["flags"]) == 2
    assert s["details"]["sell_value_inr"] == 3e6 and s["details"]["sell_clusters"][0]["persons"] == ["P1", "P2", "P3"]
    later = disclosure.insider_cluster(st, "INE002A01018", date(2026, 11, 8) + timedelta(days=120)).data
    assert later["label"] == "no open-market insider trades" and later["missing"]       # window passed; data not current either


def shp(st, isin, as_on, pledged_pct, enc=None, promoter_shares=1000):
    ds.persist_shp_filing(st, {"isin": isin, "isin_valid": True, "as_on": as_on, "symbol": "ABC", "company": "ABC", "total_shares": 2000,
                               "promoter_shares": promoter_shares, "promoter_pct": 50.0, "public_pct": 50.0,
                               "pledged_shares": int(pledged_pct * 10), "ndu_shares": 0, "other_encumbered_shares": 0,
                               "encumbered_shares": int((enc if enc is not None else pledged_pct) * 10), "encumbered_pct_of_promoter": enc if enc is not None else pledged_pct,
                               "pledged_pct_of_promoter": pledged_pct, "encumbered_pct_of_total": 0, "flags_consistent": True,
                               "no_promoter": promoter_shares == 0}, {"record_id": "r", "broadcast": "x", "revised": False, "xbrl_url": "u"})


def test_pledge_flags(st):
    shp(st, "INE002A01018", "2026-06-30", 24.0)
    shp(st, "INE002A01018", "2026-09-30", 31.5)
    s = disclosure.pledge(st, "INE002A01018", date(2026, 11, 8)).data
    assert len(s["details"]["flags"]) == 2 and "strong caution" in s["label"] and "+7.5pp" in s["label"]
    shp(st, "INE101A01026", "2026-09-30", 0.0)
    assert disclosure.pledge(st, "INE101A01026", date(2026, 11, 8)).data["label"] == "no pledge"
    shp(st, "INE111A01011", "2026-09-30", 10.0)
    s = disclosure.pledge(st, "INE111A01011", date(2026, 11, 8)).data
    assert s["label"] == "pledge present, no flag" and "previous quarter" in s["missing"][0]
    scr = disclosure.screen(st, date(2026, 11, 8))
    assert [x["isin"] for x in scr["pledge"]] == ["INE002A01018"]


# --- registry ------------------------------------------------------------------------------------------------
def test_every_market_signal_returns_a_typed_result_on_an_empty_store(st):
    for name in signals.MARKET:
        r = signals.compute(st, name, date(2026, 11, 6))
        assert r.ok or r.error.kind.value == "NOT_FOUND", name     # UNKNOWN is explicit, never an exception or a blank


def test_params_have_sources_and_version():
    p, v = params("macro_regime")
    assert p["dxy_bearish_above"] == 104.0 and v.startswith("2026-11-08.") and "+" in v


def test_replay_over_history(st):
    dates = weekdays(date(2026, 11, 6), 80)
    put_index(st, "Nifty 50", dates, [100.0] * 80)
    put_index(st, "Nifty IT", dates, [100 + i for i in range(80)])
    rows = signals.replay(st, "sector_rotation", date(2026, 10, 1), date(2026, 11, 6))
    assert rows and all(r["ok"] for r in rows if r["date"] >= "2026-10-01")


def test_yahoo_provider_stores_closes_for_the_regime(st, monkeypatch):
    """Live run 8 Nov: rows were stored with the wrong key. The provider must write closes that macro_regime can read."""
    from drishti.core.fetch import Fetched
    body = (FIX / "yahoo" / "nsei_6mo.json").read_bytes()
    monkeypatch.setattr(yahoo, "get", lambda url, profile, expect="json", **k: Fetched(url, body, 200))
    put_macro(st, "yahoo:^NSEI", [("2026-11-08", 1.0), ("2026-11-07", 1.0)])     # bad bars stored by an earlier run
    r = yahoo.daily("^NSEI", rng="6mo", store=st, today=date(2026, 11, 8))
    assert r.ok and r.as_of == "2026-11-06"
    pts = st.query("SELECT obs_date, value FROM macro_series WHERE series='yahoo:^NSEI' ORDER BY obs_date DESC LIMIT 1")
    assert pts[0]["obs_date"] == "2026-11-06" and math.isclose(pts[0]["value"], 25030.35, abs_tol=0.01)


def test_yahoo_alias_accepted():
    body = (FIX / "yahoo" / "nsei_6mo.json").read_bytes().replace(b'"symbol":"^NSEI"', b'"symbol":"USDINR=X"')
    assert yahoo.parse_chart(body, "INR=X")[-1]["date"] == "2026-11-06"


def test_macro_cross_check_flags_disagreement(st):
    put_macro(st, "yahoo:^TNX", [("2026-11-06", 4.86)])
    put_macro(st, "fred:DGS10", [("2026-11-05", 4.83)])                 # agrees within 0.10
    put_macro(st, "yahoo:BZ=F", [("2026-11-06", 98.3)])
    put_macro(st, "fred:DCOILBRENTEU", [("2026-11-03", 80.0)])
    s = market.macro_regime(st, date(2026, 11, 7)).data
    ch = {c["input"]: c for c in s["details"]["cross_checks"]}
    assert ch["US_10Y"]["agrees"] is True if "US_10Y" in ch else True
    assert "Brent_Crude" not in ch           # FRED's Brent is dated 3 Nov and Yahoo has no 3 Nov bar here: no like-for-like compare
    put_macro(st, "yahoo:BZ=F", [("2026-11-03", 98.0)])               # now both sources have 3 Nov: $18 apart -> flagged, not averaged
    s = market.macro_regime(st, date(2026, 11, 7)).data
    ch = {c["input"]: c for c in s["details"]["cross_checks"]}
    assert not ch["Brent_Crude"]["agrees"] and ch["Brent_Crude"]["date"] == "2026-11-03"
    assert any("source disagreement on Brent_Crude on 2026-11-03" in m for m in s["missing"])
    put_macro(st, "yahoo:^TNX", [("2026-11-05", 4.84)])
    s = market.macro_regime(st, date(2026, 11, 7)).data
    assert {c["input"]: c for c in s["details"]["cross_checks"]}["US_10Y"]["agrees"]


def test_replay_skips_holidays_and_summarises(st):
    for d in ("2026-10-01", "2026-10-05"):     # 2 Oct is a holiday: no row
        ds.persist_participant_oi(st, [oi_row(d, w, 1, 0, 1, 0, 0, 1) for w in ("FII", "Pro", "Client", "DII")])
    rows = signals.replay(st, "participant_oi", date(2026, 10, 1), date(2026, 10, 5))
    assert [r["as_of"] for r in rows] == ["2026-10-01", "2026-10-05"]
    s = signals.summarise(rows)
    assert s["ok_days"] == 2 and sum(s["labels"].values()) == 2


def test_client_contribution_is_negative_and_percentile_needs_history(st):
    rows = []
    for i, d in enumerate(weekdays(date(2026, 11, 6), 30)):
        k = 100 + i * 10        # FII grows steadily more long: today is the most bullish reading of the window
        rows += [oi_row(d, "FII", k, 0, 0, 0, 0, 0), oi_row(d, "Pro", 0, 0, 0, 0, 0, 0), oi_row(d, "Client", 0, k, 0, 0, 0, 0),
                 oi_row(d, "DII", 0, 0, 0, 0, 0, 0)]
    ds.persist_participant_oi(st, rows)
    s = market.participant_oi(st, date(2026, 11, 6)).data
    c = {x["input"]: x for x in s["components"]}
    assert c["Client"]["contribution"] == -c["Client"]["value"]
    assert s["details"]["history_sessions"] == 29 and s["details"]["percentile"] == 100.0


def test_yahoo_drops_live_and_weekend_bars():
    rows = [{"date": "2026-11-06", "close": 1.0}, {"date": "2026-11-07", "close": 1.0}, {"date": "2026-11-08", "close": 1.0}]
    assert [r["date"] for r in yahoo.complete_bars(rows, date(2026, 11, 8))] == ["2026-11-06"]
    assert [r["date"] for r in yahoo.complete_bars(rows[:1], date(2026, 11, 6))] == []      # today's bar may still be live

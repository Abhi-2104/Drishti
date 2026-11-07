"""Job runner: logging, canaries, partial failure, idempotence, the insider job's storage, and the CLI around it."""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pytest

from drishti import jobs
from drishti.cli import main
from drishti.core.canary import IST
from drishti.core.envelope import DrishtiError, ErrorKind, Result
from drishti.providers import nse_pit
from drishti.store import Store

FIX = Path(__file__).parent / "fixtures" / "nse_pit"
WED_EVE = datetime(2026, 11, 11, 19, 0, tzinfo=IST)


@pytest.fixture
def st():
    s = Store()
    yield s
    s.close()


def only(monkeypatch, *steps, name="t"):
    monkeypatch.setattr(jobs, "JOBS", {name: jobs.Job(name, "00:00", "test", tuple(steps))})


# --- expected trading day -----------------------------------------------------------------------------
@pytest.mark.parametrize("when, want", [
    ((2026, 11, 7, 10, 0), date(2026, 11, 6)),    # Saturday -> Friday
    ((2026, 11, 8, 23, 0), date(2026, 11, 6)),    # Sunday night -> Friday
    ((2026, 11, 9, 10, 0), date(2026, 11, 6)),    # Monday morning: Friday's files are the latest
    ((2026, 11, 9, 19, 0), date(2026, 11, 9)),    # Monday evening: today's
    ((2026, 11, 10, 18, 29), date(2026, 11, 9)),  # just before the publish window
    ((2026, 11, 6, 18, 30), date(2026, 11, 6)),
])
def test_expected_trading_day(when, want):
    assert jobs.expected_trading_day(datetime(*when, tzinfo=IST)) == want


# --- runner ---------------------------------------------------------------------------------------------
def test_every_step_is_logged_and_one_failure_does_not_stop_the_rest(st, monkeypatch):
    only(monkeypatch,
         jobs.Step("a", lambda s, t: Result(data=[1, 2, 3], as_of="2026-11-11"), daily=True),
         jobs.Step("b", lambda s, t: Result(error=DrishtiError(ErrorKind.BLOCKED, "403"))),
         jobs.Step("c", lambda s, t: 1 / 0),
         jobs.Step("d", lambda s, t: Result(data={"x": 1}, as_of="2026-11-11"), count=lambda r: 7))
    rep = jobs.run_job(st, "t", now=WED_EVE)
    assert [(s.dataset, s.ok, s.error_kind, s.rows) for s in rep.steps] == [
        ("a", True, None, 3), ("b", False, "BLOCKED", None), ("c", False, "PARSE_FAIL", None), ("d", True, None, 7)]
    assert "ZeroDivisionError" in rep.steps[2].detail and not rep.ok
    log = {r["dataset"]: r for r in st.query("SELECT * FROM job_log WHERE job='t'")}
    assert log["a"]["status"] == "ok" and log["a"]["rows"] == 3 and log["a"]["trade_date"] == "2026-11-11"
    assert log["b"]["status"] == "failed" and log["b"]["error_kind"] == "BLOCKED"
    can = {r["name"]: r for r in st.query("SELECT * FROM canary")}
    assert can["t.a"]["ok"] == 1 and can["t.b"]["ok"] == 0 and can["t.b"]["error_kind"] == "BLOCKED"
    assert st.data_health()["status"] == "degraded"


def test_behind_file_is_a_note_not_a_failure(st, monkeypatch):
    only(monkeypatch, jobs.Step("a", lambda s, t: Result(data=[1], as_of="2026-11-10"), daily=True),
         jobs.Step("snap", lambda s, t: Result(data=[1], as_of="2026-11-01")))      # undated snapshots are not compared
    rep = jobs.run_job(st, "t", now=WED_EVE)
    assert rep.ok and "expected 2026-11-11" in rep.steps[0].note and rep.steps[1].note == ""


def test_persist_failure_is_a_typed_error(st, monkeypatch):
    def boom(s, r):
        raise RuntimeError("disk full")
    only(monkeypatch, jobs.Step("a", lambda s, t: Result(data=[1], as_of="2026-11-11"), persist=boom))
    rep = jobs.run_job(st, "t", now=WED_EVE)
    assert not rep.ok and rep.steps[0].error_kind == "PARSE_FAIL" and "store: RuntimeError: disk full" in rep.steps[0].detail


def test_unknown_job(st):
    with pytest.raises(KeyError):
        jobs.run_job(st, "nope")


def test_registry_is_consistent():
    assert {"eod_bhavcopy", "eod_flows", "insider", "macro_weekly", "shp_master", "security_master"} <= set(jobs.JOBS)
    for j in jobs.JOBS.values():
        assert j.steps and j.when and j.doc
        for s in j.steps:
            assert jobs.STEPS[s.dataset] is s


# --- the insider job stores events once ----------------------------------------------------------------------
def events():
    out = []
    for f, app, at in (("IT_22833_WebXMLFile_20261107_230727135.xml", "4250", "2026-11-07T23:07:27"),
                       ("IT_22833_WebXMLFile_20261107_230425023.xml", "4240", "2026-11-07T23:04:25"),   # same trade, older filing
                       ("IT_16166_WebXMLFile_20261107_215948860.xml", "4100", "2026-11-07T21:59:48")):
        for e in nse_pit.parse_pit_filing((FIX / f).read_bytes()):
            out.append({**e, "app_id": app, "filed_at": at, "submission_type": "Original"})
    return out


def test_insider_job_stores_deduped_events_and_skips_known_filings(st, monkeypatch):
    seen = []

    def fake(days, *, today, skip_app_ids=(), **k):
        seen.append(set(skip_app_ids))
        evs = [e for e in nse_pit.dedupe_events(events()) if e["app_id"] not in skip_app_ids]
        return Result(data={"events": evs, "filings": len(evs), "failed": [], "duplicates_dropped": 1}, as_of="2026-11-07")
    monkeypatch.setattr(jobs.nse_pit, "insider_trades", fake)
    only(monkeypatch, jobs.STEPS["insider_trades"])
    rep = jobs.run_job(st, "t", now=WED_EVE)
    assert rep.ok and rep.steps[0].rows == 2
    rows = st.query("SELECT symbol, direction, quantity, app_id, holding_reconciles FROM insider_event ORDER BY symbol")
    assert [(r["symbol"], r["direction"], r["app_id"]) for r in rows] == [("INDOSTAR", "BUY", "4100"), ("KRONOX", "PLEDGE", "4250")]
    assert rows[0]["holding_reconciles"] == 1
    rep2 = jobs.run_job(st, "t", now=WED_EVE)                                  # second run: known filings are not requested again
    assert seen[1] == {"4100", "4250"} and rep2.ok and rep2.steps[0].rows == 0
    assert st.query("SELECT COUNT(*) AS n FROM insider_event")[0]["n"] == 2   # idempotent


def test_insider_partial_failure_still_stores_what_parsed(st, monkeypatch):
    def fake(days, *, today, skip_app_ids=(), **k):
        return Result(data={"events": nse_pit.dedupe_events(events()), "filings": 10, "failed": [{}] * 6, "duplicates_dropped": 1},
                      as_of="2026-11-07", error=DrishtiError(ErrorKind.NETWORK, "pit: 6/10 filings failed"))
    monkeypatch.setattr(jobs.nse_pit, "insider_trades", fake)
    only(monkeypatch, jobs.STEPS["insider_trades"])
    rep = jobs.run_job(st, "t", now=WED_EVE)
    assert not rep.ok and rep.steps[0].error_kind == "NETWORK"
    assert st.query("SELECT COUNT(*) AS n FROM insider_event")[0]["n"] == 2        # the good events are kept, the failure is loud


def test_event_key_is_stable_across_duplicate_filings():
    a, b = events()[0], events()[1]
    assert a["app_id"] != b["app_id"] and nse_pit.event_key(a) == nse_pit.event_key(b)


# --- CLI ----------------------------------------------------------------------------------------------------
def test_cli_jobs_run_health(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DRISHTI_HOME", str(tmp_path))
    assert main(["jobs"]) == 0
    out = capsys.readouterr().out
    assert "eod_bhavcopy" in out and "cm_bhavcopy, fo_bhavcopy, bse_bhavcopy" in out
    assert main(["run", "nosuchjob"]) == 2 and "unknown job" in capsys.readouterr().err
    only(monkeypatch, jobs.Step("good", lambda s, t: Result(data=[1, 2], as_of="2026-11-06")),
         jobs.Step("bad", lambda s, t: Result(error=DrishtiError(ErrorKind.STALE, "newest record is 20d old"))))
    assert main(["run", "t"]) == 1                                              # any failed step -> exit 1
    out = capsys.readouterr().out
    assert "OK   good" in out and "FAIL bad" in out and "STALE" in out and "1/2 steps ok" in out
    assert main(["health"]) == 1                                                # degraded -> non-zero, for monitors
    out = capsys.readouterr().out
    assert "status: degraded" in out and "t.bad" in out and "canary FAIL" in out
    only(monkeypatch, jobs.Step("good", lambda s, t: Result(data=[1], as_of="2026-11-06")))
    assert main(["run", "t"]) == 0

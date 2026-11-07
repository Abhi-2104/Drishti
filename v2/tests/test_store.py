"""Store tests: schema, blobs/fetch-once, upsert, signals, job log, canaries, security master, health, threads."""
from __future__ import annotations

import sqlite3
import threading
from datetime import date, datetime, timezone

import pytest

from drishti.core.envelope import DrishtiError, ErrorKind, Result
from drishti.core.fetch import Fetched
from drishti.store import SCHEMA_VERSION, Store

D = date(2026, 11, 6)


def fx(body: bytes = b"a,b\n1,2\n", url: str = "https://x.test/f.csv") -> Fetched:
    return Fetched(url, body, 200, datetime(2026, 11, 8, 1, 0, tzinfo=timezone.utc))


@pytest.fixture
def st():
    with Store() as s:
        yield s


# --- schema ---------------------------------------------------------------------------------------
def test_schema_created_and_idempotent(tmp_path):
    p = tmp_path / "d.db"
    Store(p).close()
    s = Store(p)  # reopen: no error, no duplicate objects
    names = {r["name"] for r in s.query("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"raw_blob", "signal", "job_log", "canary", "security"} <= names
    assert s.query("PRAGMA user_version")[0]["user_version"] == SCHEMA_VERSION
    s.close()


def test_refuses_newer_schema(tmp_path):
    p = tmp_path / "d.db"
    Store(p).close()
    con = sqlite3.connect(p)
    con.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    con.commit()
    con.close()
    with pytest.raises(RuntimeError, match="newer"):
        Store(p)


def test_file_store_uses_wal_and_creates_parent(tmp_path):
    s = Store(tmp_path / "sub" / "dir" / "d.db")
    assert s.query("PRAGMA journal_mode")[0]["journal_mode"] == "wal"
    s.close()


# --- blobs / fetch_once ---------------------------------------------------------------------------
def test_blob_round_trip(st):
    sha = st.put_blob("cm_bhavcopy", D, fx(), "nse_archive")
    assert len(sha) == 64
    got = st.get_blob("cm_bhavcopy", D)
    assert got.body == b"a,b\n1,2\n" and got.url == "https://x.test/f.csv"
    assert got.fetched_at == datetime(2026, 11, 8, 1, 0, tzinfo=timezone.utc)
    assert st.has_blob("cm_bhavcopy", D) and not st.has_blob("cm_bhavcopy", date(2026, 11, 5))
    assert st.get_blob("cm_bhavcopy", date(2026, 11, 5)) is None


def test_put_blob_overwrites_same_key(st):
    st.put_blob("ds", D, fx(b"one"), "s")
    st.put_blob("ds", D, fx(b"two"), "s")
    assert st.get_blob("ds", D).body == b"two"
    assert st.query("SELECT COUNT(*) AS n FROM raw_blob")[0]["n"] == 1


def test_metadata_only_blob_is_marker_but_not_servable(st):
    st.put_blob("ds", D, fx(), "s", keep_body=False)
    assert st.has_blob("ds", D)
    assert st.get_blob("ds", D) is None


def test_latest_trade_date_ignores_undated(st):
    st.put_blob("ds", date(2026, 11, 4), fx(), "s")
    st.put_blob("ds", date(2026, 11, 6), fx(), "s")
    st.put_blob("ds", "latest", fx(), "s")
    assert st.latest_trade_date("ds") == "2026-11-06"
    assert st.latest_trade_date("nothing") is None


def test_fetch_once_downloads_once(st):
    calls = []

    def fetcher():
        calls.append(1)
        return fx()

    a, from_store_a = st.fetch_once("ds", D, fetcher, source="s")
    b, from_store_b = st.fetch_once("ds", D, fetcher, source="s")
    assert (from_store_a, from_store_b) == (False, True)
    assert len(calls) == 1 and a.body == b.body


def test_fetch_once_force_refetches(st):
    n = []
    st.fetch_once("ds", D, lambda: (n.append(1), fx())[1], source="s")
    st.fetch_once("ds", D, lambda: (n.append(1), fx())[1], source="s", force=True)
    assert len(n) == 2


def test_fetch_once_failure_leaves_no_state_and_retries(st):
    def boom():
        raise DrishtiError(ErrorKind.BLOCKED, "403")

    with pytest.raises(DrishtiError) as ei:
        st.fetch_once("ds", D, boom, source="s")
    assert ei.value.kind == ErrorKind.BLOCKED
    assert not st.has_blob("ds", D)
    got, from_store = st.fetch_once("ds", D, fx, source="s")  # next attempt works
    assert not from_store and st.has_blob("ds", D)


# --- typed tables / upsert ------------------------------------------------------------------------
BARS = ("CREATE TABLE IF NOT EXISTS bars (trade_date TEXT NOT NULL, isin TEXT NOT NULL, close REAL, meta TEXT, "
        "PRIMARY KEY (trade_date, isin))")


def test_ensure_table_accepts_only_idempotent_create(st):
    st.ensure_table(BARS)
    st.ensure_table(BARS)
    st.ensure_table("CREATE INDEX IF NOT EXISTS ix_bars ON bars (isin)")
    for bad in ("DROP TABLE security", "CREATE TABLE bars2 (a int)", "DELETE FROM security",
                BARS + "; DROP TABLE security"):
        with pytest.raises(ValueError):
            st.ensure_table(bad)
    assert st.query("SELECT COUNT(*) AS n FROM security")[0]["n"] == 0  # still there


def test_upsert_is_idempotent_and_updates(st):
    st.ensure_table(BARS)
    rows = [{"trade_date": D, "isin": "INE002A01018", "close": 100.0, "meta": None}]
    assert st.upsert("bars", rows, ("trade_date", "isin")) == 1
    st.upsert("bars", rows, ("trade_date", "isin"))
    assert st.query("SELECT COUNT(*) AS n FROM bars")[0]["n"] == 1
    st.upsert("bars", [{"trade_date": D, "isin": "INE002A01018", "close": 101.5, "meta": {"k": [1, 2]}}], ("trade_date", "isin"))
    r = st.query("SELECT * FROM bars")[0]
    assert r["close"] == 101.5 and r["trade_date"] == "2026-11-06" and r["meta"] == '{"k": [1, 2]}'


def test_upsert_all_key_columns_does_nothing_on_conflict(st):
    st.ensure_table("CREATE TABLE IF NOT EXISTS seen (a TEXT, b TEXT, PRIMARY KEY (a, b))")
    row = [{"a": "x", "b": "y"}]
    st.upsert("seen", row, ("a", "b"))
    st.upsert("seen", row, ("a", "b"))
    assert st.query("SELECT COUNT(*) AS n FROM seen")[0]["n"] == 1


def test_upsert_empty_and_bad_input(st):
    st.ensure_table(BARS)
    assert st.upsert("bars", [], ("trade_date", "isin")) == 0
    with pytest.raises(ValueError):
        st.upsert("bars", [{"trade_date": "x", "isin": "y"}], ("nope",))
    with pytest.raises(ValueError):
        st.upsert("bars", [{"trade_date": "x", "isin": "y"}], ())
    with pytest.raises(ValueError):  # ragged rows
        st.upsert("bars", [{"trade_date": "x", "isin": "y"}, {"trade_date": "x"}], ("trade_date", "isin"))


@pytest.mark.parametrize("table,col", [("bars; DROP TABLE security", "a"), ("bars", "close) VALUES (1); DROP TABLE security; --"),
                                       ("1bars", "a"), ("bars", "a b")])
def test_upsert_rejects_injection(st, table, col):
    st.ensure_table(BARS)
    with pytest.raises(ValueError, match="identifier"):
        st.upsert(table, [{col: 1}], (col,))
    assert st.query("SELECT COUNT(*) AS n FROM security")[0]["n"] == 0


def test_values_are_parameterised_not_interpolated(st):
    st.ensure_table(BARS)
    nasty = "x'); DROP TABLE security; --"
    st.upsert("bars", [{"trade_date": D, "isin": nasty, "close": 1.0, "meta": None}], ("trade_date", "isin"))
    assert st.query("SELECT isin FROM bars")[0]["isin"] == nasty
    assert st.query("SELECT COUNT(*) AS n FROM security")[0]["n"] == 0


# --- signals --------------------------------------------------------------------------------------
def test_signal_set_get_latest_and_overwrite(st):
    st.set_signal("fii_net", date(2026, 11, 5), {"v": -100})
    st.set_signal("fii_net", date(2026, 11, 6), {"v": 250.5})
    assert st.get_signal("fii_net")["as_of"] == "2026-11-06"
    assert st.get_signal("fii_net", date(2026, 11, 5))["value"] == {"v": -100}
    st.set_signal("fii_net", date(2026, 11, 6), {"v": 1})
    assert st.get_signal("fii_net", "2026-11-06")["value"] == {"v": 1}
    assert st.get_signal("nope") is None and st.get_signal("fii_net", date(2020, 1, 1)) is None


# --- job log --------------------------------------------------------------------------------------
def last_job(st):
    return st.query("SELECT * FROM job_log ORDER BY id DESC LIMIT 1")[0]


def test_job_success_records_rows_and_date(st):
    with st.job("eod", "cm_bhavcopy", D) as j:
        j.rows = 2375
    r = last_job(st)
    assert (r["status"], r["rows"], r["trade_date"], r["error_kind"]) == ("ok", 2375, "2026-11-06", None)
    assert r["finished_at"]


def test_job_trade_date_can_be_set_inside_block(st):
    with st.job("eod", "ds") as j:
        j.trade_date = "2026-11-06"
    assert last_job(st)["trade_date"] == "2026-11-06"


def test_job_drishti_error_logged_with_kind_and_reraised(st):
    with pytest.raises(DrishtiError):
        with st.job("eod", "fo_bhavcopy", D):
            raise DrishtiError(ErrorKind.STALE, "newest 2026-10-01")
    r = last_job(st)
    assert (r["status"], r["error_kind"], r["detail"]) == ("failed", "STALE", "newest 2026-10-01")


def test_job_unexpected_error_is_parse_fail_not_swallowed(st):
    with pytest.raises(KeyError):
        with st.job("eod", "ds"):
            {}["missing"]
    r = last_job(st)
    assert r["status"] == "failed" and r["error_kind"] == "PARSE_FAIL" and "KeyError" in r["detail"]


def test_job_keyboard_interrupt_is_not_left_running(st):
    with pytest.raises(KeyboardInterrupt):
        with st.job("eod", "ds"):
            raise KeyboardInterrupt
    r = last_job(st)
    assert r["status"] == "failed" and r["error_kind"] == "INTERRUPTED"


def test_log_result_ok_and_error(st):
    st.log_result("eod", Result(data=[1, 2, 3], as_of="2026-11-06"), dataset="ds")
    r = last_job(st)
    assert (r["status"], r["rows"], r["trade_date"]) == ("ok", 3, "2026-11-06")
    st.log_result("eod", Result(error=DrishtiError(ErrorKind.EMPTY, "no rows")), dataset="ds", trade_date=D)
    r = last_job(st)
    assert (r["status"], r["error_kind"], r["detail"], r["trade_date"]) == ("failed", "EMPTY", "no rows", "2026-11-06")


def test_log_result_scalar_data_has_no_row_count(st):
    st.log_result("j", Result(data=42))
    assert last_job(st)["rows"] is None


# --- canaries -------------------------------------------------------------------------------------
def test_canary_ok_then_failure_overwrites(st):
    st.record_canary("pit", ok=True, newest=date(2026, 11, 7))
    c = st.query("SELECT * FROM canary WHERE name='pit'")[0]
    assert c["ok"] == 1 and c["newest"] == "2026-11-07" and c["error_kind"] is None
    st.record_canary("pit", ok=False, error=DrishtiError(ErrorKind.STALE, "newest 2026-08-01"))
    rows = st.query("SELECT * FROM canary")
    assert len(rows) == 1 and rows[0]["ok"] == 0 and rows[0]["error_kind"] == "STALE" and "2026-08-01" in rows[0]["detail"]


# --- security master ------------------------------------------------------------------------------
def test_security_merge_never_blanks_and_normalises_isin(st):
    st.upsert_security("ine002a01018", name="Reliance Industries", nse_symbol="RELIANCE", source="nse_equity_l")
    st.upsert_security("INE002A01018", bse_scrip=500325, bse_symbol="RELIANCE", source="bse_bhavcopy")
    r = st.get_security("ine002a01018")
    assert r["isin"] == "INE002A01018"
    assert (r["name"], r["nse_symbol"], r["bse_scrip"], r["bse_symbol"]) == ("Reliance Industries", "RELIANCE", "500325", "RELIANCE")
    assert r["source"] == "bse_bhavcopy"
    assert st.query("SELECT COUNT(*) AS n FROM security")[0]["n"] == 1


def test_security_bse_only_and_lookups(st):
    st.upsert_security("INE000B01011", name="BSE Only Ltd", bse_scrip="512345", source="bse")
    assert st.get_security("INE000B01011")["nse_symbol"] is None
    assert [r["isin"] for r in st.find_securities(bse_scrip="512345")] == ["INE000B01011"]
    assert [r["isin"] for r in st.find_securities(bse_scrip=512345)] == ["INE000B01011"]
    assert st.find_securities(nse_symbol="NOPE") == []
    st.upsert_security("INE111A01011", nse_symbol="OLD")
    st.upsert_security("INE222A01011", nse_symbol="OLD")  # symbol reuse: callers must cope with a list
    assert len(st.find_securities(nse_symbol="old")) == 2
    with pytest.raises(ValueError):
        st.find_securities()


def test_security_requires_isin(st):
    for bad in ("", "  ", None):
        with pytest.raises(ValueError):
            st.upsert_security(bad, name="x")
    assert st.get_security("INE999Z99999") is None


# --- data_health ----------------------------------------------------------------------------------
def test_data_health_empty_store_is_ok(st):
    h = st.data_health()
    assert h["status"] == "ok" and h["jobs"] == [] and h["datasets"] == [] and h["canaries"] == []


def test_data_health_reports_latest_per_step_and_degrades_on_failure(st):
    st.put_blob("cm_bhavcopy", date(2026, 11, 5), fx(), "s")
    st.put_blob("cm_bhavcopy", D, fx(), "s")
    with st.job("eod", "cm_bhavcopy", D) as j:
        j.rows = 10
    with pytest.raises(DrishtiError):
        with st.job("eod", "fo_bhavcopy", D):
            raise DrishtiError(ErrorKind.NOT_FOUND, "404")
    h = st.data_health()
    by = {j["dataset"]: j for j in h["jobs"]}
    assert by["cm_bhavcopy"]["status"] == "ok" and by["fo_bhavcopy"]["error_kind"] == "NOT_FOUND"
    assert h["status"] == "degraded"
    ds = {d["dataset"]: d for d in h["datasets"]}
    assert ds["cm_bhavcopy"]["latest_trade_date"] == "2026-11-06" and ds["cm_bhavcopy"]["days_stored"] == 2
    assert h["failures_24h"] == [{"error_kind": "NOT_FOUND", "n": 1}]


def test_data_health_recovers_when_latest_run_succeeds(st):
    with pytest.raises(DrishtiError):
        with st.job("eod", "ds"):
            raise DrishtiError(ErrorKind.TIMEOUT, "slow")
    assert st.data_health()["status"] == "degraded"
    with st.job("eod", "ds"):
        pass
    assert st.data_health()["status"] == "ok"  # only the latest run per step counts


def test_data_health_failed_canary_degrades(st):
    st.record_canary("pit", ok=False, error=DrishtiError(ErrorKind.STALE, "old"))
    h = st.data_health()
    assert h["status"] == "degraded" and h["canaries"][0]["error_kind"] == "STALE"


def test_data_health_is_json_serialisable(st):
    import json
    st.put_blob("ds", D, fx(), "s")
    st.record_canary("c", ok=True, newest=D)
    st.set_signal("s", D, [1])
    with st.job("j", "ds") as j:
        j.rows = 1
    json.dumps(st.data_health())


def test_data_health_failure_window_is_24h(st):
    with pytest.raises(DrishtiError):
        with st.job("eod", "ds"):
            raise DrishtiError(ErrorKind.EMPTY)
    later = datetime.now(timezone.utc).replace(year=datetime.now(timezone.utc).year + 1)
    assert st.data_health(now=later)["failures_24h"] == []


# --- concurrency ----------------------------------------------------------------------------------
def test_concurrent_upserts_on_file_db(tmp_path):
    s = Store(tmp_path / "c.db")
    s.ensure_table(BARS)
    errors = []

    def work(i):
        try:
            for k in range(25):
                s.upsert("bars", [{"trade_date": D, "isin": f"ISIN{i}_{k}", "close": float(k), "meta": None}], ("trade_date", "isin"))
                s.set_signal(f"sig{i}", D, k)
                with s.job(f"job{i}", "ds"):
                    pass
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    ts = [threading.Thread(target=work, args=(i,)) for i in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errors
    assert s.query("SELECT COUNT(*) AS n FROM bars")[0]["n"] == 200
    assert s.query("SELECT COUNT(*) AS n FROM job_log WHERE status='ok'")[0]["n"] == 200
    s.close()


def test_data_persists_across_reopen(tmp_path):
    p = tmp_path / "p.db"
    with Store(p) as s:
        s.put_blob("ds", D, fx(), "s")
        s.upsert_security("INE002A01018", name="R")
    with Store(p) as s:
        assert s.has_blob("ds", D) and s.get_security("INE002A01018")["name"] == "R"

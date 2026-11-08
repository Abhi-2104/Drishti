"""The per-install SQLite store: raw blobs, signals, job log, canaries, security master.

One connection per Store, guarded by a lock (jobs and MCP calls may be threaded). Nothing here talks
to the network: callers hand in a fetcher (which goes through core.fetch) for "fetch a daily file once".
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import re
import sqlite3
import threading
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Mapping, Sequence

from ..core.envelope import DrishtiError, ErrorKind, Result, now_utc
from ..core.fetch import Fetched
from .schema import ADDED_COLUMNS, DDL, SCHEMA_VERSION

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_IDEMPOTENT = re.compile(r"^\s*CREATE\s+(TABLE|INDEX|UNIQUE\s+INDEX)\s+IF\s+NOT\s+EXISTS\s", re.I)


def _ts() -> str:
    return now_utc().isoformat(timespec="seconds")


def _day(d: date | datetime | str) -> str:
    if isinstance(d, datetime):
        return d.date().isoformat()
    return d.isoformat() if isinstance(d, date) else str(d)


def _ident(name: str) -> str:
    if not _IDENT.match(name):
        raise ValueError(f"invalid SQL identifier: {name!r}")
    return name


def _adapt(v: Any) -> Any:
    """Make a Python value storable: dates to ISO text, containers to JSON."""
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, (dict, list, tuple)):
        return json.dumps(v, default=str)
    return v


class JobRun:
    """Handle yielded by Store.job(): set .rows / .trade_date before the block ends."""

    def __init__(self, run_id: int):
        self.id = run_id
        self.rows: int | None = None
        self.trade_date: str | None = None


class Store:
    def __init__(self, path: str | Path = ":memory:"):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(self.path, check_same_thread=False, timeout=10)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys = ON")
        self._db.execute("PRAGMA busy_timeout = 10000")
        if self.path != ":memory:":
            self._db.execute("PRAGMA journal_mode = WAL")
            self._db.execute("PRAGMA synchronous = NORMAL")
        self._migrate()

    # --- lifecycle ----------------------------------------------------------------------------------
    def _migrate(self) -> None:
        with self._lock:
            have = self._db.execute("PRAGMA user_version").fetchone()[0]
            if have > SCHEMA_VERSION:
                raise RuntimeError(f"store schema v{have} is newer than this drishti (v{SCHEMA_VERSION}); upgrade drishti")
            self._db.executescript(DDL)
            for table, col, typ in ADDED_COLUMNS:  # a v1 store was created without these; fresh stores already have them
                if col not in {r[1] for r in self._db.execute(f"PRAGMA table_info({table})")}:
                    self._db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
            self._db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # --- generic tables -----------------------------------------------------------------------------
    def _run(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._db.execute(sql, params)
            self._db.commit()
            return cur

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._db.execute(sql, params).fetchall()]

    def ensure_table(self, ddl: str) -> None:
        """Create a provider's typed table. Only idempotent CREATE ... IF NOT EXISTS statements are accepted."""
        stmts = [s.strip() for s in ddl.split(";") if s.strip()]
        for s in stmts:
            if not _IDEMPOTENT.match(s):
                raise ValueError(f"ensure_table accepts only CREATE ... IF NOT EXISTS, got: {s[:60]!r}")
        with self._lock:
            for s in stmts:
                self._db.execute(s)
            self._db.commit()

    def upsert(self, table: str, rows: Iterable[Mapping[str, Any]], key: Sequence[str]) -> int:
        """Idempotent insert-or-update keyed on `key` (e.g. ('trade_date', 'symbol')). Returns rows written."""
        rows = list(rows)
        if not rows:
            return 0
        cols = list(rows[0].keys())
        _ident(table)
        for c in cols:
            _ident(c)
        missing = [k for k in key if k not in cols]
        if not key or missing:
            raise ValueError(f"key columns {list(key)} must be among the row columns (missing: {missing})")
        if any(set(r.keys()) != set(cols) for r in rows):
            raise ValueError("all rows must have the same columns")
        non_key = [c for c in cols if c not in key]
        action = "DO UPDATE SET " + ", ".join(f"{c} = excluded.{c}" for c in non_key) if non_key else "DO NOTHING"
        sql = (f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))}) "
               f"ON CONFLICT ({', '.join(key)}) {action}")
        with self._lock:
            self._db.executemany(sql, [[_adapt(r[c]) for c in cols] for r in rows])
            self._db.commit()
        return len(rows)

    def drop_table(self, table: str) -> None:
        """Drop a DERIVED table whose layout changed (it is rebuilt from stored source data). Never used for raw or source tables."""
        _ident(table)
        self._run(f"DROP TABLE IF EXISTS {table}")

    def delete_where(self, table: str, where: str, params: Sequence[Any]) -> int:
        """DELETE FROM table WHERE <where> with bound parameters; `where` is written by code, never by data."""
        _ident(table)
        if ";" in where:
            raise ValueError("one condition only")
        return self._run(f"DELETE FROM {table} WHERE {where}", params).rowcount

    # --- raw blobs / fetch-once ---------------------------------------------------------------------
    def put_blob(self, dataset: str, trade_date: date | str, fetched: Fetched, source: str, *, keep_body: bool = True) -> str:
        sha = hashlib.sha256(fetched.body).hexdigest()
        self._run(
            "INSERT INTO raw_blob (dataset, trade_date, source, url, fetched_at, bytes, sha256, body) VALUES (?,?,?,?,?,?,?,?) "
            "ON CONFLICT (dataset, trade_date) DO UPDATE SET source=excluded.source, url=excluded.url, "
            "fetched_at=excluded.fetched_at, bytes=excluded.bytes, sha256=excluded.sha256, body=excluded.body",
            (dataset, _day(trade_date), source, fetched.url, fetched.fetched_at.isoformat(timespec="seconds"),
             len(fetched.body), sha, fetched.body if keep_body else None),
        )
        return sha

    def get_blob(self, dataset: str, trade_date: date | str) -> Fetched | None:
        rows = self.query("SELECT url, body, fetched_at FROM raw_blob WHERE dataset=? AND trade_date=?", (dataset, _day(trade_date)))
        if not rows or rows[0]["body"] is None:
            return None
        r = rows[0]
        return Fetched(r["url"], bytes(r["body"]), 200, datetime.fromisoformat(r["fetched_at"]))

    def has_blob(self, dataset: str, trade_date: date | str) -> bool:
        return bool(self.query("SELECT 1 FROM raw_blob WHERE dataset=? AND trade_date=?", (dataset, _day(trade_date))))

    def latest_trade_date(self, dataset: str) -> str | None:
        rows = self.query("SELECT MAX(trade_date) AS d FROM raw_blob WHERE dataset=? AND trade_date != 'latest'", (dataset,))
        return rows[0]["d"] if rows else None

    def fetch_once(self, dataset: str, trade_date: date | str, fetcher: Callable[[], Fetched], *, source: str,
                   force: bool = False) -> tuple[Fetched, bool]:
        """Return (data, from_store). A daily file is downloaded once; later calls are served from the store.

        `fetcher` does the real GET (through core.fetch) and may raise DrishtiError: nothing is recorded on
        failure, so the next attempt tries again.
        """
        if not force:
            hit = self.get_blob(dataset, trade_date)
            if hit is not None:
                return hit, True
        got = fetcher()
        self.put_blob(dataset, trade_date, got, source)
        return got, False

    # --- signals ------------------------------------------------------------------------------------
    def set_signal(self, name: str, as_of: date | str, value: Any) -> None:
        self._run(
            "INSERT INTO signal (name, as_of, value, computed_at) VALUES (?,?,?,?) "
            "ON CONFLICT (name, as_of) DO UPDATE SET value=excluded.value, computed_at=excluded.computed_at",
            (name, _day(as_of), json.dumps(value, default=str), _ts()),
        )

    def get_signal(self, name: str, as_of: date | str | None = None) -> dict | None:
        """The signal for `as_of`, or the latest one."""
        if as_of is None:
            rows = self.query("SELECT * FROM signal WHERE name=? ORDER BY as_of DESC LIMIT 1", (name,))
        else:
            rows = self.query("SELECT * FROM signal WHERE name=? AND as_of=?", (name, _day(as_of)))
        if not rows:
            return None
        r = rows[0]
        return {"name": r["name"], "as_of": r["as_of"], "value": json.loads(r["value"]), "computed_at": r["computed_at"]}

    # --- job log ------------------------------------------------------------------------------------
    def _finish(self, run_id: int, status: str, kind: str | None, detail: str, rows: int | None, trade_date: str | None) -> None:
        self._run("UPDATE job_log SET finished_at=?, status=?, error_kind=?, detail=?, rows=COALESCE(?, rows), "
                  "trade_date=COALESCE(?, trade_date) WHERE id=?",
                  (_ts(), status, kind, detail or None, rows, trade_date, run_id))

    @contextlib.contextmanager
    def job(self, job: str, dataset: str | None = None, trade_date: date | str | None = None) -> Iterator[JobRun]:
        """Log one job step. Failures are recorded with their typed kind and then re-raised: never a silent blank."""
        run_id = self._run("INSERT INTO job_log (job, dataset, trade_date, started_at, status) VALUES (?,?,?,?, 'running')",
                           (job, dataset, _day(trade_date) if trade_date else None, _ts())).lastrowid
        h = JobRun(run_id)
        try:
            yield h
        except DrishtiError as e:
            self._finish(run_id, "failed", str(e.kind), e.detail, h.rows, h.trade_date)
            raise
        except BaseException as e:  # noqa: BLE001 - includes KeyboardInterrupt: never leave a job 'running' forever
            kind = ErrorKind.PARSE_FAIL if isinstance(e, Exception) else "INTERRUPTED"
            self._finish(run_id, "failed", str(kind), f"{type(e).__name__}: {str(e)[:200]}", h.rows, h.trade_date)
            raise
        else:
            self._finish(run_id, "ok", None, "", h.rows, h.trade_date)

    def log_result(self, job: str, result: Result, *, dataset: str | None = None, trade_date: date | str | None = None,
                   rows: int | None = None) -> None:
        """Record a provider Result (ok or typed error) as one finished job step."""
        if rows is None:
            rows = len(result.data) if hasattr(result.data, "__len__") and not isinstance(result.data, (str, bytes, dict)) else None
        run_id = self._run("INSERT INTO job_log (job, dataset, trade_date, started_at, status) VALUES (?,?,?,?, 'running')",
                           (job, dataset, _day(trade_date or result.as_of) if (trade_date or result.as_of) else None, _ts())).lastrowid
        if result.ok:
            self._finish(run_id, "ok", None, "", rows, None)
        else:
            self._finish(run_id, "failed", str(result.error.kind), result.error.detail, rows, None)

    # --- canaries -----------------------------------------------------------------------------------
    def record_canary(self, name: str, *, ok: bool, newest: date | str | None = None,
                      error: DrishtiError | None = None, detail: str = "") -> None:
        self._run(
            "INSERT INTO canary (name, checked_at, ok, newest, error_kind, detail) VALUES (?,?,?,?,?,?) "
            "ON CONFLICT (name) DO UPDATE SET checked_at=excluded.checked_at, ok=excluded.ok, newest=excluded.newest, "
            "error_kind=excluded.error_kind, detail=excluded.detail",
            (name, _ts(), int(ok), _day(newest) if newest else None, str(error.kind) if error else None,
             (error.detail if error else detail) or None),
        )

    # --- security master (ISIN is the key) ----------------------------------------------------------
    def upsert_security(self, isin: str, *, name: str | None = None, nse_symbol: str | None = None,
                        bse_scrip: str | None = None, bse_symbol: str | None = None, source: str = "",
                        instrument_class: str | None = None, nse_series: str | None = None, bse_series: str | None = None,
                        nse_listed_on: str | None = None) -> None:
        """Merge facts about one security. A later source adds fields but never blanks an existing one,
        so NSE EQUITY_L and the BSE bhavcopy can each contribute their half of a cross-listing."""
        if not isin or not str(isin).strip():
            raise ValueError("isin is required")
        self._run(
            "INSERT INTO security (isin, name, nse_symbol, bse_scrip, bse_symbol, source, updated_at, "
            "instrument_class, nse_series, bse_series, nse_listed_on) VALUES (?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT (isin) DO UPDATE SET "
            "name=COALESCE(excluded.name, security.name), nse_symbol=COALESCE(excluded.nse_symbol, security.nse_symbol), "
            "bse_scrip=COALESCE(excluded.bse_scrip, security.bse_scrip), bse_symbol=COALESCE(excluded.bse_symbol, security.bse_symbol), "
            "instrument_class=COALESCE(excluded.instrument_class, security.instrument_class), "
            "nse_series=COALESCE(excluded.nse_series, security.nse_series), bse_series=COALESCE(excluded.bse_series, security.bse_series), "
            "nse_listed_on=COALESCE(excluded.nse_listed_on, security.nse_listed_on), "
            "source=excluded.source, updated_at=excluded.updated_at",
            (str(isin).strip().upper(), name, nse_symbol, bse_scrip and str(bse_scrip), bse_symbol, source, _ts(),
             instrument_class, nse_series, bse_series, nse_listed_on),
        )

    def get_security(self, isin: str) -> dict | None:
        rows = self.query("SELECT * FROM security WHERE isin=?", (str(isin).strip().upper(),))
        return rows[0] if rows else None

    def find_securities(self, *, nse_symbol: str | None = None, bse_scrip: str | None = None, bse_symbol: str | None = None) -> list[dict]:
        """Lookup by NSE symbol, BSE scrip code or BSE symbol. A list: symbols are reused over time, so callers must handle >1."""
        if nse_symbol is not None:
            return self.query("SELECT * FROM security WHERE nse_symbol=?", (nse_symbol.strip().upper(),))
        if bse_scrip is not None:
            return self.query("SELECT * FROM security WHERE bse_scrip=?", (str(bse_scrip).strip(),))
        if bse_symbol is not None:
            return self.query("SELECT * FROM security WHERE bse_symbol=?", (bse_symbol.strip().upper(),))
        raise ValueError("pass nse_symbol, bse_scrip or bse_symbol")

    def search_securities(self, text: str, *, limit: int = 8) -> list[dict]:
        """Substring match on the company name (for 'did you mean' suggestions). LIKE wildcards in the input are escaped."""
        t = text.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        return self.query("SELECT * FROM security WHERE name LIKE ? ESCAPE '\\' ORDER BY name LIMIT ?", (f"%{t}%", int(limit)))

    # --- health -------------------------------------------------------------------------------------
    def data_health(self, *, now: datetime | None = None) -> dict:
        """One JSON-able snapshot for the data_health tool: latest run per job step, dataset freshness,
        canaries, and recent failures by typed kind. 'degraded' if any latest step or canary failed."""
        now = now or now_utc()
        jobs = self.query(
            "SELECT j.job, j.dataset, j.status, j.started_at, j.finished_at, j.error_kind, j.detail, j.rows, j.trade_date "
            "FROM job_log j JOIN (SELECT MAX(id) AS mid FROM job_log GROUP BY job, COALESCE(dataset, '')) m ON j.id = m.mid "
            "ORDER BY j.job, j.dataset")
        datasets = self.query(
            "SELECT dataset, MAX(CASE WHEN trade_date != 'latest' THEN trade_date END) AS latest_trade_date, "
            "MAX(fetched_at) AS last_fetched_at, COUNT(*) AS days_stored FROM raw_blob GROUP BY dataset ORDER BY dataset")
        canaries = self.query("SELECT name, checked_at, ok, newest, error_kind, detail FROM canary ORDER BY name")
        since = (now - timedelta(hours=24)).isoformat(timespec="seconds")
        failures = self.query("SELECT error_kind, COUNT(*) AS n FROM job_log WHERE status='failed' AND started_at >= ? "
                              "GROUP BY error_kind ORDER BY n DESC", (since,))
        bad = [j for j in jobs if j["status"] == "failed"] + [c for c in canaries if not c["ok"]]
        return {"status": "degraded" if bad else "ok", "generated_at": now.isoformat(timespec="seconds"),
                "jobs": jobs, "datasets": datasets, "canaries": canaries, "failures_24h": failures}

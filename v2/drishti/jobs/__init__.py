"""Job registry and runner. A job is an ordered list of steps; each step calls one provider with the store and is
logged to job_log (ok or typed error) plus a freshness canary. One failing step never stops the others, and no job
ever reports success on a step that returned an error: the exit code is non-zero if any step failed.

Every job is safe to run twice: raw files are fetched once per day (fetch-once store) and parsed rows are upserted.
That is what lets Task Scheduler simply repeat a job until the day's file appears.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Callable

from ..core.canary import IST
from ..core.envelope import DrishtiError, ErrorKind, Result
from ..providers import _util as u
from .. import signals
from ..providers import cftc, fred, nse_archive, nse_flows, nse_pit, nse_shp, security_master, yahoo
from ..signals import disclosure
from ..store import datasets as ds
from ..providers import bse as bse_provider
from ..store import Store

log = logging.getLogger("drishti.jobs")

INSIDER_DDL = """
CREATE TABLE IF NOT EXISTS insider_event (
    event_key TEXT PRIMARY KEY, app_id TEXT NOT NULL, filed_at TEXT, symbol TEXT, isin TEXT, scrip_code TEXT, company TEXT,
    category TEXT, person TEXT, direction TEXT, txn_type TEXT, mode TEXT, quantity INTEGER, value_inr REAL,
    held_pre_shares INTEGER, held_post_shares INTEGER, held_pre_pct REAL, held_post_pct REAL, holding_reconciles INTEGER,
    from_date TEXT, to_date TEXT, intimation_date TEXT, filing_date TEXT, exchange TEXT, revised INTEGER, form_version TEXT,
    stored_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_insider_isin ON insider_event (isin, to_date);
CREATE INDEX IF NOT EXISTS ix_insider_filed ON insider_event (filed_at)
"""
_INSIDER_COLS = ("app_id filed_at symbol isin scrip_code company category person direction txn_type mode quantity value_inr "
                 "held_pre_shares held_post_shares held_pre_pct held_post_pct holding_reconciles from_date to_date "
                 "intimation_date filing_date exchange revised form_version").split()


@dataclass(frozen=True)
class Step:
    dataset: str
    run: Callable[[Store, date], Result]
    persist: Callable[[Store, Result], int] | None = None   # parsed rows -> typed table; returns rows written
    count: Callable[[Result], int | None] | None = None     # row count for the log when len(data) is not it
    daily: bool = False                                      # a dated file: compare as_of with the expected trading day


@dataclass(frozen=True)
class Job:
    name: str
    when: str     # suggested IST time, shown by `drishti jobs` and used in the scheduling recipe
    doc: str
    steps: tuple[Step, ...]


@dataclass
class StepReport:
    dataset: str
    ok: bool
    as_of: str | None
    rows: int | None
    secs: float
    error_kind: str | None = None
    detail: str = ""
    note: str = ""


@dataclass
class JobReport:
    job: str
    steps: list[StepReport] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.steps) and all(s.ok for s in self.steps)


# --- steps --------------------------------------------------------------------------------------------
def _latest(fn) -> Callable[[Store, date], Result]:
    return lambda store, today: u.latest(lambda d: fn(d, store=store), today)


def _insider_run(store: Store, today: date) -> Result:
    store.ensure_table(INSIDER_DDL)
    since = (today - timedelta(days=14)).isoformat()
    known = [r["app_id"] for r in store.query("SELECT DISTINCT app_id FROM insider_event WHERE filed_at >= ?", (since,))]
    return nse_pit.insider_trades(7, today=today, skip_app_ids=known)


def _insider_persist(store: Store, res: Result) -> int:
    events = (res.data or {}).get("events") or []
    stamp = datetime.now(IST).isoformat(timespec="seconds")
    rows = [{"event_key": nse_pit.event_key(e), **{c: (int(e[c]) if isinstance(e.get(c), bool) else e.get(c)) for c in _INSIDER_COLS},
             "stored_at": stamp} for e in events]
    return store.upsert("insider_event", rows, ("event_key",))


def _p(fn):
    return lambda store, res: fn(store, res.data)


def _fred(sid: str) -> Step:
    return Step(f"fred_{sid}", lambda s, t: fred.fred_series(sid, store=s, today=t), count=lambda r: r.data["n_total"])


def _yahoo(sym: str) -> Step:
    return Step(f"yahoo_{sym}", lambda s, t: yahoo.daily(sym, rng="1mo", store=s, today=t))


def _signal_step(name: str) -> Step:
    def persist(store, res):
        store.set_signal(name, res.as_of, res.data)
        return 1
    return Step(f"signal_{name}", lambda s, t: signals.compute(s, name), persist=persist, count=lambda r: 1)


def _screen_step() -> Step:
    def run(store, today):
        data = disclosure.screen(store, today)
        return Result(data=data, as_of=data["as_of"])

    def persist(store, res):
        store.set_signal("disclosure_screen", res.as_of, res.data)
        return 1
    return Step("signal_disclosure_screen", run, persist=persist, count=lambda r: len(r.data["insider"]) + len(r.data["pledge"]))


DAILY_FRED = ("DTWEXBGS", "DGS10", "DCOILBRENTEU", "DEXINUS", "VIXCLS", "BAMLH0A0HYM2", "T10Y2Y")
WEEKLY_FRED = ("WALCL", "M2SL")

STEPS = {
    "cm_bhavcopy": Step("cm_bhavcopy", _latest(nse_archive.cm_bhavcopy), daily=True),
    "fo_bhavcopy": Step("fo_bhavcopy", _latest(nse_archive.fo_bhavcopy), persist=_p(ds.persist_fo_index), daily=True),
    "bse_bhavcopy": Step("bse_bhavcopy", _latest(bse_provider.bhavcopy), daily=True),
    "participant_oi": Step("participant_oi", _latest(nse_archive.participant_oi), persist=_p(ds.persist_participant_oi), daily=True),
    "index_closes": Step("index_closes", _latest(nse_archive.index_closes), persist=_p(ds.persist_index_closes), daily=True),
    "fo_ban_list": Step("fo_ban_list", _latest(nse_archive.fo_ban_list), daily=True),
    "bulk_deals": Step("bulk_deals", lambda s, t: nse_archive.bulk_deals(store=s)),
    "block_deals": Step("block_deals", lambda s, t: nse_archive.block_deals(store=s)),
    "fii_dii_cash": Step("fii_dii_cash", lambda s, t: nse_flows.fii_dii_cash(store=s, today=t), persist=_p(ds.persist_fii_dii),
                         count=lambda r: 2),
    **{f"fred_{x}": _fred(x) for x in DAILY_FRED + WEEKLY_FRED},
    **{f"yahoo_{x}": _yahoo(x) for x in yahoo.SYMBOLS},
    "cftc_tff": Step("cftc_tff", lambda s, t: cftc.cftc_tff(store=s, today=t), persist=_p(ds.persist_cftc)),
    "shp_master": Step("shp_master", lambda s, t: nse_shp.shp_master(store=s, today=t)),
    "security_master": Step("security_master", lambda s, t: security_master.build_security_master(s, today=t),
                            count=lambda r: r.data["securities"]),
    "insider_trades": Step("insider_trades", _insider_run, persist=_insider_persist, count=lambda r: len(r.data["events"])),
    **{f"signal_{n}": _signal_step(n) for n in signals.MARKET},
    "signal_disclosure_screen": _screen_step(),
}

JOBS: dict[str, Job] = {j.name: j for j in (
    Job("eod_bhavcopy", "18:45", "NSE cash + F&O and BSE cash bhavcopies for the latest trading day",
        tuple(STEPS[k] for k in ("cm_bhavcopy", "fo_bhavcopy", "bse_bhavcopy"))),
    Job("eod_flows", "19:15", "Participant OI, F&O ban list, index closes, bulk and block deals, cash FII/DII",
        tuple(STEPS[k] for k in ("participant_oi", "fo_ban_list", "index_closes", "bulk_deals", "block_deals", "fii_dii_cash"))),
    Job("insider", "07:30 and 20:30", "SEBI insider-trading disclosures, last 7 days (downloads only filings not yet stored)",
        (STEPS["insider_trades"],)),
    Job("macro_daily", "07:15", "Regime inputs: Yahoo DXY/US10Y/Brent/USD-INR/VIX and FRED daily series",
        tuple(STEPS[f"yahoo_{x}"] for x in yahoo.SYMBOLS) + tuple(STEPS[f"fred_{x}"] for x in DAILY_FRED)),
    Job("macro_weekly", "Sat 10:00", "Fed balance sheet, US M2, CFTC positioning",
        tuple(STEPS[f"fred_{x}"] for x in WEEKLY_FRED) + (STEPS["cftc_tff"],)),
    Job("shp_master", "07:45", "Shareholding-pattern master: promoter holding per company with QoQ change", (STEPS["shp_master"],)),
    Job("security_master", "Sun 10:00", "ISIN master joining NSE and BSE listings", (STEPS["security_master"],)),
    Job("signals_daily", "20:00", "Compute every market signal and the disclosure screen from stored data (run after the data jobs)",
        tuple(STEPS[f"signal_{n}"] for n in signals.MARKET) + (STEPS["signal_disclosure_screen"],)),
)}


# --- runner -------------------------------------------------------------------------------------------
def expected_trading_day(now: datetime) -> date:
    """The latest weekday whose end-of-day files should exist by now (published around 18:30 IST). Exchange holidays are not
    known here, so a holiday makes a file look one day 'behind': that is a note, never a failure."""
    now = now.astimezone(IST)
    d = now.date() if now.hour * 60 + now.minute >= 18 * 60 + 30 else now.date() - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def run_job(store: Store, name: str, *, now: datetime | None = None, sleep: Callable[[float], None] = time.sleep) -> JobReport:
    if name not in JOBS:
        raise KeyError(name)
    now = now or datetime.now(IST)
    today = now.astimezone(IST).date()
    report = JobReport(name)
    for step in JOBS[name].steps:
        t0 = time.time()
        try:
            res = step.run(store, today)
        except DrishtiError as e:     # providers return errors in Results, but a helper may still raise one
            res = Result(error=e)
        except Exception as e:  # noqa: BLE001 - a bug in one step must not stop the others, and must still be logged typed
            log.exception("job %s step %s crashed", name, step.dataset)
            res = Result(error=DrishtiError(ErrorKind.PARSE_FAIL, f"{type(e).__name__}: {str(e)[:200]}"))
        rows = None
        note = ""
        if res.data is not None:
            try:
                if step.persist:
                    step.persist(store, res)
                rows = step.count(res) if step.count else (len(res.data) if isinstance(res.data, list) else None)
            except Exception as e:  # noqa: BLE001
                log.exception("job %s step %s could not store its rows", name, step.dataset)
                res = Result(data=res.data, as_of=res.as_of, sources=res.sources,
                             error=DrishtiError(ErrorKind.PARSE_FAIL, f"store: {type(e).__name__}: {str(e)[:200]}"))
        if res.ok and step.daily and res.as_of and date.fromisoformat(res.as_of) < expected_trading_day(now):
            note = f"newest available is {res.as_of}; expected {expected_trading_day(now)} (not published yet, or an exchange holiday)"
        store.log_result(name, res, dataset=step.dataset, trade_date=res.as_of, rows=rows)
        store.record_canary(f"{name}.{step.dataset}", ok=res.ok, newest=res.as_of, error=res.error)
        report.steps.append(StepReport(step.dataset, res.ok, res.as_of, rows, time.time() - t0,
                                       str(res.error.kind) if res.error else None, res.error.detail if res.error else "", note))
    return report

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
from ..providers import cftc, feeds, fred, mospi, nsdl, nse_archive, nse_corp, nse_flows, nse_pit, nse_shp, security_master, yahoo
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
    return lambda store, today: u.latest(lambda d: fn(d, store=store), today, sessions=ds.special_sessions(store))


def _bse_bhavcopy(store: Store, today: date) -> Result:
    """BSE answers a missing bhavcopy with an HTML page, not a 404, so walking back day by day cannot tell 'not
    published yet' from a block. Both exchanges trade the same sessions, so ask BSE for the newest day NSE's file
    exists for (the cm_bhavcopy step runs first); an HTML answer for that day is a real problem and is reported."""
    ds.ensure(store, "price_eod")
    row = store.query("SELECT MAX(trade_date) AS d FROM price_eod WHERE exchange='NSE' AND trade_date <= ?", (today.isoformat(),))
    if not row or not row[0]["d"]:
        return u.latest(lambda d: bse_provider.bhavcopy(d, store=store), today, sessions=ds.special_sessions(store))
    return bse_provider.bhavcopy(date.fromisoformat(row[0]["d"]), store=store)


def _us_daily(store: Store, today: date) -> Result:
    """Refresh every tracked US ticker (any ticker ever looked up). Nothing tracked yet is a note, not a failure."""
    syms = ds.tracked_us_symbols(store)
    bars, failed, kinds = 0, [], []
    for sym in syms:
        r = yahoo.us_daily(sym, rng="1mo", store=store, today=today)
        if r.ok:
            bars += len(r.data["bars"])
        else:
            failed.append(f"{sym}: {r.error.kind}")
            kinds.append(r.error.kind)
    if failed and len(failed) == len(syms):
        return Result(error=DrishtiError(kinds[0], f"us_daily: every ticker failed: {failed[:5]}"))
    return Result(data={"symbols": len(syms), "bars": bars, "failed": failed}, as_of=today.isoformat())


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


def _news_run(store: Store, today: date) -> Result:
    """Fetch every configured feed, store the items not seen before and tag them. No sources configured is a valid, reported state."""
    from ..config import load_settings
    srcs = load_settings().news_sources
    if not srcs:
        return Result(data={"sources": 0, "new_items": 0, "failed": [], "note": "no news sources configured (settings.toml [[news_source]])"},
                      as_of=today.isoformat())
    seen_at = datetime.now(IST).isoformat(timespec="seconds")
    new, tagged, failed, kinds, newest = 0, 0, [], [], None
    for s in srcs:
        r = feeds.fetch_feed(s.name, s.url)
        if not r.ok:
            failed.append(f"{s.name}: {r.error.kind}: {r.error.detail[:100]}")
            kinds.append(r.error.kind)
            continue
        out = ds.persist_news(store, s.name, r.data["items"], seen_at)
        new, tagged = new + out["new"], tagged + out["tagged"]
        newest = max(newest or r.as_of, r.as_of)
    if len(failed) == len(srcs):
        return Result(error=DrishtiError(kinds[0], f"news: every source failed: {failed[:3]}"))
    return Result(data={"sources": len(srcs), "new_items": new, "tagged": tagged, "failed": failed}, as_of=newest or today.isoformat())


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


def _archive_deals(kind: str):
    def persist(store, res):
        return ds.persist_deals(store, [{"kind": kind, "date": r["trade_date"], "symbol": r["symbol"], "client": r["client"], "side": r["side"],
                                         "qty": r["quantity"], "price": r["price"], "company": r["name"]} for r in res.data], "nse_archive")
    return persist


def _large_deals_persist(store, res):
    d = res.data
    return ds.persist_deals(store, d["bulk"] + d["block"] + d["short"], "nse_api")


def _expiry_step(sym: str) -> Step:
    return Step(f"expiries_{sym}", lambda s, t: nse_corp.expiries(sym, today=t),
                persist=lambda st, r: ds.persist_expiries(st, sym, r.data["expiries"], r.as_of), count=lambda r: len(r.data["expiries"]))


DAILY_FRED = ("DTWEXBGS", "DGS10", "DCOILBRENTEU", "DEXINUS", "VIXCLS", "BAMLH0A0HYM2", "T10Y2Y")
WEEKLY_FRED = ("WALCL", "M2SL")
MONTHLY_FRED = ("TRESEGINM052N",)       # India total reserves (the MoSPI reserves table stopped in June 2025)

STEPS = {
    "cm_bhavcopy": Step("cm_bhavcopy", _latest(nse_archive.cm_bhavcopy), persist=lambda st, r: ds.persist_prices(st, r.data, "NSE"), daily=True),
    "delivery": Step("delivery", _latest(nse_archive.delivery), persist=_p(ds.persist_delivery), daily=True),
    "fo_bhavcopy": Step("fo_bhavcopy", _latest(nse_archive.fo_bhavcopy), persist=_p(ds.persist_fo_index), daily=True),
    "bse_bhavcopy": Step("bse_bhavcopy", lambda s, t: _bse_bhavcopy(s, t), persist=lambda st, r: ds.persist_prices(st, r.data, "BSE"), daily=True),
    "participant_oi": Step("participant_oi", _latest(nse_archive.participant_oi), persist=_p(ds.persist_participant_oi), daily=True),
    "index_closes": Step("index_closes", _latest(nse_archive.index_closes), persist=_p(ds.persist_index_closes), daily=True),
    "fo_ban_list": Step("fo_ban_list", _latest(nse_archive.fo_ban_list), daily=True),
    "bulk_deals": Step("bulk_deals", lambda s, t: nse_archive.bulk_deals(store=s), persist=_archive_deals("bulk")),
    "block_deals": Step("block_deals", lambda s, t: nse_archive.block_deals(store=s), persist=_archive_deals("block")),
    "large_deals": Step("large_deals", lambda s, t: nse_corp.large_deals(today=t), persist=_large_deals_persist,
                        count=lambda r: sum(len(r.data[k]) for k in ("bulk", "block", "short"))),
    "announcements": Step("announcements", lambda s, t: nse_corp.announcements(days=2, today=t), persist=_p(ds.persist_announcements)),
    "corp_actions": Step("corp_actions", lambda s, t: nse_corp.corporate_actions(today=t), persist=_p(ds.persist_corp_actions)),
    "price_adjustments": Step("price_adjustments", lambda s, t: Result(data=ds.rebuild_adjustments(s, since=(t - timedelta(days=30)).isoformat()),
                                                                      as_of=t.isoformat()), count=lambda r: r.data["events"]),
    "us_daily": Step("us_daily", lambda s, t: _us_daily(s, t), count=lambda r: r.data["bars"]),
    "board_meetings": Step("board_meetings", lambda s, t: nse_corp.board_meetings(today=t), persist=_p(ds.persist_board_meetings)),
    "bse_corp_actions": Step("bse_corp_actions", lambda s, t: bse_provider.corporate_actions(today=t), persist=_p(ds.persist_bse_corp_actions)),
    "bse_result_calendar": Step("bse_result_calendar", lambda s, t: bse_provider.result_calendar(today=t),
                                persist=_p(ds.persist_board_meetings)),
    "nsdl_fpi": Step("nsdl_fpi", lambda s, t: nsdl.fpi_daily(today=t), persist=_p(ds.persist_fpi), count=lambda r: len(r.data["rows"])),
    "credit_ratings": Step("credit_ratings", lambda s, t: nse_corp.credit_ratings(today=t), persist=_p(ds.persist_credit_ratings)),
    "ipos": Step("ipos", lambda s, t: nse_corp.ipos(today=t), persist=lambda st, r: ds.persist_ipos(st, r.data, r.as_of),
                 count=lambda r: len(r.data["current"]) + len(r.data["upcoming"]) + len(r.data["recent"])),
    "holidays": Step("holidays", lambda s, t: nse_corp.holidays(today=t), persist=_p(ds.persist_holidays)),
    "expiries_NIFTY": _expiry_step("NIFTY"),
    "expiries_BANKNIFTY": _expiry_step("BANKNIFTY"),
    "market_status": Step("market_status", lambda s, t: nse_corp.market_status(today=t), persist=_p(ds.persist_gift), count=lambda r: 1),
    "fii_dii_cash": Step("fii_dii_cash", lambda s, t: nse_flows.fii_dii_cash(store=s, today=t), persist=_p(ds.persist_fii_dii),
                         count=lambda r: 2),
    **{f"fred_{x}": _fred(x) for x in DAILY_FRED + WEEKLY_FRED + MONTHLY_FRED},
    **{f"yahoo_{x}": _yahoo(x) for x in yahoo.SYMBOLS},
    **{f"mospi_{k}": Step(f"mospi_{k}", lambda s, t, f=getattr(mospi, k): f(today=t), persist=lambda st, r: ds.persist_macro_bundle(st, r.data),
                          count=lambda r: sum(len(v) for v in r.data["series"].values())) for k in ("cpi", "wpi", "iip", "rbi_reserves")},
    "news": Step("news", _news_run, count=lambda r: r.data["new_items"]),
    "cftc_tff": Step("cftc_tff", lambda s, t: cftc.cftc_tff(store=s, today=t), persist=_p(ds.persist_cftc)),
    "shp_master": Step("shp_master", lambda s, t: nse_shp.shp_master(store=s, today=t)),
    "security_master": Step("security_master", lambda s, t: security_master.build_security_master(s, today=t),
                            count=lambda r: r.data["securities"]),
    "insider_trades": Step("insider_trades", _insider_run, persist=_insider_persist, count=lambda r: len(r.data["events"])),
    **{f"signal_{n}": _signal_step(n) for n in signals.MARKET},
    "signal_disclosure_screen": _screen_step(),
}

JOBS: dict[str, Job] = {j.name: j for j in (
    Job("eod_bhavcopy", "18:45", "NSE cash + F&O and BSE cash bhavcopies for the latest trading day, then split/bonus adjustments",
        tuple(STEPS[k] for k in ("cm_bhavcopy", "fo_bhavcopy", "bse_bhavcopy", "price_adjustments"))),
    Job("eod_flows", "19:15", "Participant OI, F&O ban list, index closes, bulk and block deals, cash FII/DII, delivery %",
        tuple(STEPS[k] for k in ("participant_oi", "fo_ban_list", "index_closes", "bulk_deals", "block_deals", "fii_dii_cash", "delivery"))),
    Job("eod_corporate", "19:45", "Announcements, corporate actions (NSE, then BSE-only), board meetings and the BSE results "
        "calendar, credit ratings, large deals incl. short selling, IPO lists",
        tuple(STEPS[k] for k in ("announcements", "corp_actions", "bse_corp_actions", "board_meetings", "bse_result_calendar",
                                 "credit_ratings", "large_deals", "ipos"))),
    Job("premarket", "08:45", "Market status and Gift Nifty vs the last Nifty close; NSDL confirmed FPI flows for the last day",
        (STEPS["market_status"], STEPS["nsdl_fpi"])),
    Job("calendars", "Mon 07:00", "NSE trading holidays and NIFTY/BANKNIFTY expiry dates",
        tuple(STEPS[k] for k in ("holidays", "expiries_NIFTY", "expiries_BANKNIFTY"))),
    Job("insider", "07:30 and 20:30", "SEBI insider-trading disclosures, last 7 days (downloads only filings not yet stored)",
        (STEPS["insider_trades"],)),
    Job("macro_daily", "07:15", "Regime inputs: Yahoo DXY/US10Y/Brent/USD-INR/VIX and FRED daily series",
        tuple(STEPS[f"yahoo_{x}"] for x in yahoo.SYMBOLS) + tuple(STEPS[f"fred_{x}"] for x in DAILY_FRED)),
    Job("us_daily", "07:20", "Daily bars for every tracked US ticker (any ticker looked up once)", (STEPS["us_daily"],)),
    Job("macro_india", "18:10", "India CPI, WPI and IIP from MoSPI, RBI reserves from FRED (MoSPI's reserves table ended June 2025; "
        "a stale series is reported, not reused)",
        tuple(STEPS[f"mospi_{k}"] for k in ("cpi", "wpi", "iip")) + tuple(STEPS[f"fred_{x}"] for x in MONTHLY_FRED)),
    Job("news", "07:05, every 2 h until 21:00", "Headlines from the RSS / Atom feeds listed in settings.toml (none by default); new items are stored and "
        "tagged to companies by name or symbol", (STEPS["news"],)),
    Job("macro_weekly", "Sat 10:00", "Fed balance sheet, US M2, CFTC positioning",
        tuple(STEPS[f"fred_{x}"] for x in WEEKLY_FRED) + (STEPS["cftc_tff"],)),
    Job("shp_master", "07:45", "Shareholding-pattern master: promoter holding per company with QoQ change", (STEPS["shp_master"],)),
    Job("security_master", "Sun 10:00", "ISIN master joining NSE and BSE listings", (STEPS["security_master"],)),
    Job("signals_daily", "20:00", "Compute every market signal and the disclosure screen from stored data (run after the data jobs)",
        tuple(STEPS[f"signal_{n}"] for n in signals.MARKET) + (STEPS["signal_disclosure_screen"],)),
)}


# --- runner -------------------------------------------------------------------------------------------
def expected_trading_day(now: datetime, store: Store | None = None) -> date:
    """The latest session whose end-of-day files should exist by now (published around 18:30 IST). With a store, NSE's
    holiday list is used: holidays are skipped and weekend special sessions count. Without one, weekdays only, so a
    holiday makes a file look one day 'behind': that is a note, never a failure."""
    now = now.astimezone(IST)
    d = now.date() if now.hour * 60 + now.minute >= 18 * 60 + 30 else now.date() - timedelta(days=1)
    if store is None:
        while d.weekday() >= 5:
            d -= timedelta(days=1)
        return d
    while not ds.is_session(store, d.isoformat()):     # holidays skipped, special sessions (Muhurat) counted
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
        exp = expected_trading_day(now, store)
        if res.ok and step.daily and res.as_of and date.fromisoformat(res.as_of) < exp:
            known = ds.is_holiday(store, exp.isoformat()) is not None
            note = f"newest available is {res.as_of}; expected {exp} " + ("(not published yet)" if known else "(not published yet, or a holiday: run `drishti run calendars`)")
        store.log_result(name, res, dataset=step.dataset, trade_date=res.as_of, rows=rows)
        store.record_canary(f"{name}.{step.dataset}", ok=res.ok, newest=res.as_of, error=res.error)
        report.steps.append(StepReport(step.dataset, res.ok, res.as_of, rows, time.time() - t0,
                                       str(res.error.kind) if res.error else None, res.error.detail if res.error else "", note))
    return report

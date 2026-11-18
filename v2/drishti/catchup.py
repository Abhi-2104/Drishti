"""`drishti catchup`: the one command Windows Task Scheduler runs (at logon, 08:30, 20:30, Saturday 10:00).

Three slots (IST). For each slot, the latest scheduled time that is already past counts as "due"; it runs when the job log shows
no successful completion of that slot since then. So a laptop that was off simply catches up at the next trigger, the slots run
oldest first, and nothing runs twice. A failed slot is retried at the next trigger (not sooner than RETRY_AFTER_MIN), until the
slot's next scheduled time replaces it.

  morning 08:30  macro_daily, us_daily, premarket, insider, shp_master, news (+ calendars Mon, security_master Sun)
  evening 20:30  eod_bhavcopy, eod_flows, eod_corporate, macro_india, insider, news, then the gap fill, then signals_daily
  weekly  Sat 10:00  macro_weekly, forward log, holdings import, weekly pack, import of Claude's answers

Gap fill: for dated datasets (prices, delivery, participant OI, index closes, deals) it finds sessions missing from the last
MAX_GAP_DAYS and fetches only those, through the same resumable backfill code. Known loss: NSE serves FII/DII cash for the latest
day only, so a day the laptop stays off is gone (NSDL FPI covers FPI history).

One catch-up at a time (lock file), a time budget (slots that do not fit wait for the next trigger), typed errors everywhere, and
exit code 1 if anything failed. Re-running is always safe.
"""
from __future__ import annotations

import contextlib
import os
import time
from dataclasses import dataclass, field
from datetime import date, datetime, time as dtime, timedelta, timezone
from pathlib import Path
from typing import Callable

from .core.canary import IST
from .core.envelope import DrishtiError, ErrorKind, Result
from .jobs import expected_trading_day, run_job
from .store import Store
from .store import datasets as ds

RETRY_AFTER_MIN = 30
MAX_GAP_DAYS = 400
GAP_WINDOW_DAYS = 30           # how far back to look for holes inside the stored range
RETRY_GAP_AFTER_DAYS = 7       # a missing day the exchange confirmed has no file (holiday) is not asked for again for this long
STALE_LOCK_S = 3 * 3600
BUDGET_MIN = 100               # the Task Scheduler limit is 2 h
RESERVE_S = 600                # kept free for what follows the gap fill
SAT = 5


@dataclass(frozen=True)
class Slot:
    name: str
    at: dtime
    weekday: int | None                       # None = every day
    jobs: tuple[str, ...]
    extra: dict = field(default_factory=dict)     # weekday of the due date -> extra jobs (Mon = 0)
    gap_fill: bool = False
    weekly_tail: bool = False

    def jobs_for(self, due: datetime) -> tuple[str, ...]:
        return self.jobs + tuple(self.extra.get(due.weekday(), ()))


# signals_daily is last in the evening: it reads what the gap fill filled
SLOTS = (
    Slot("morning", dtime(8, 30), None, ("macro_daily", "us_daily", "premarket", "insider", "shp_master", "news"),
         extra={0: ("calendars",), 6: ("security_master",)}),
    Slot("evening", dtime(20, 30), None, ("eod_bhavcopy", "eod_flows", "eod_corporate", "macro_india", "insider", "news"), gap_fill=True),
    Slot("weekly", dtime(10, 0), SAT, ("macro_weekly",), weekly_tail=True),
)
EVENING_LAST = ("signals_daily",)
SLOT_JOBS = {j for s in SLOTS for j in s.jobs} | {j for s in SLOTS for js in s.extra.values() for j in js} | set(EVENING_LAST)

GAP_DDL = "CREATE TABLE IF NOT EXISTS catchup_gap_tried (target TEXT NOT NULL, day TEXT NOT NULL, tried_at TEXT NOT NULL, PRIMARY KEY (target, day))"


def latest_due(slot: Slot, now: datetime) -> datetime:
    now = now.astimezone(IST)
    for back in range(0, 9):
        day = now.date() - timedelta(days=back)
        if slot.weekday is not None and day.weekday() != slot.weekday:
            continue
        dt = datetime.combine(day, slot.at, tzinfo=IST)
        if dt <= now:
            return dt
    raise AssertionError("unreachable: every slot occurs within 8 days")


def _utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def completed_since(store: Store, slot: str, due: datetime) -> bool:
    return bool(store.query("SELECT 1 AS x FROM job_log WHERE job='catchup' AND dataset=? AND status='ok' AND finished_at >= ? LIMIT 1", (slot, _utc(due))))


def failed_recently(store: Store, slot: str, now: datetime, minutes: int = RETRY_AFTER_MIN) -> bool:
    return bool(store.query("SELECT 1 AS x FROM job_log WHERE job='catchup' AND dataset=? AND status='failed' AND finished_at >= ? LIMIT 1",
                            (slot, _utc(now - timedelta(minutes=minutes)))))


def due_slots(store: Store, now: datetime) -> list[tuple[Slot, datetime, str]]:
    """(slot, due time, state) oldest first; state is 'run' | 'done' | 'retry_later'."""
    out = []
    for s in SLOTS:
        due = latest_due(s, now)
        state = "done" if completed_since(store, s.name, due) else "retry_later" if failed_recently(store, s.name, now) else "run"
        out.append((s, due, state))
    return sorted(out, key=lambda x: x[1])


# --- lock -------------------------------------------------------------------------------------------------------------
class Locked(Exception):
    pass


@contextlib.contextmanager
def lock(path: Path, stale_s: float = STALE_LOCK_S):
    """One catch-up at a time. A lock older than `stale_s` is a crashed run and is taken over."""
    path = Path(path)
    for attempt in (1, 2):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            try:
                age = time.time() - path.stat().st_mtime
            except FileNotFoundError:
                continue
            if age <= stale_s or attempt == 2:
                raise Locked(f"{path} is held by another catchup (age {age / 60:.0f} min)") from None
            path.unlink(missing_ok=True)
    try:
        os.write(fd, f"{os.getpid()} {datetime.now(IST).isoformat(timespec='seconds')}\n".encode())
        os.close(fd)
        yield
    finally:
        path.unlink(missing_ok=True)


# --- reports ---------------------------------------------------------------------------------------------------------
@dataclass
class Step:
    label: str
    ok: bool
    detail: str = ""


@dataclass
class SlotReport:
    slot: str
    due: datetime
    status: str                    # ok | failed | incomplete | done | retry_later
    steps: list[Step] = field(default_factory=list)

    @property
    def failed(self) -> bool:
        return self.status == "failed"

    def line(self) -> str:
        n_ok = sum(s.ok for s in self.steps)
        head = f"{self.slot:<8s} due {self.due:%a %d %b %H:%M}: {self.status.upper():<11s}"
        if self.status == "run":
            return head + "would run: " + ", ".join(s.label for s in self.steps)
        if self.status in ("done", "retry_later"):
            return head + ("already completed since then" if self.status == "done" else f"failed within the last {RETRY_AFTER_MIN} min; retrying later")
        bad = [f"{s.label} ({s.detail[:90]})" for s in self.steps if not s.ok]
        return head + f"{n_ok}/{len(self.steps)} steps ok" + (f"; FAILED: {'; '.join(bad)}" if bad else "")


@dataclass
class CatchupReport:
    slots: list[SlotReport] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(s.failed for s in self.slots)


# --- gap fill ---------------------------------------------------------------------------------------------------------
def _have(store: Store, table: str, lo: str) -> set[str]:
    return {r["d"] for r in store.query(f"SELECT DISTINCT trade_date AS d FROM {table} WHERE trade_date >= ?", (lo,))}


def missing_sessions(store: Store, table: str, expected: date, *, window: int = GAP_WINDOW_DAYS, max_days: int = MAX_GAP_DAYS) -> list[str]:
    """Sessions up to `expected` that `table` lacks: holes in the last `window` days, plus everything since the newest stored day.
    An empty table is not a gap (the first fill is `drishti backfill`, a deliberate multi-hour job)."""
    ds.ensure(store, table)
    newest = store.query(f"SELECT MAX(trade_date) AS d FROM {table}")[0]["d"]
    if not newest:
        return []
    start = max(expected - timedelta(days=max_days), min(date.fromisoformat(newest), expected - timedelta(days=window)))
    have = _have(store, table, start.isoformat())
    specials = ds.special_sessions(store)
    out, d = [], start
    while d <= expected:
        iso = d.isoformat()
        if iso not in have and ((d.weekday() < 5 and ds.is_holiday(store, iso) is not True) or iso in specials):
            out.append(iso)
        d += timedelta(days=1)
    return out


def _untried(store: Store, target: str, days: list[str], now: datetime) -> list[str]:
    store.ensure_table(GAP_DDL)
    cut = (now - timedelta(days=RETRY_GAP_AFTER_DAYS)).date().isoformat()
    tried = {r["day"] for r in store.query("SELECT day FROM catchup_gap_tried WHERE target=? AND tried_at >= ?", (target, cut))}
    return [d for d in days if d not in tried]


def _mark_tried(store: Store, target: str, days: list[str], now: datetime) -> None:
    store.upsert("catchup_gap_tried", [{"target": target, "day": d, "tried_at": now.date().isoformat()} for d in days], ("target", "day"))


def gap_fill(store: Store, now: datetime, *, budget_s: float, clock=time.monotonic, progress: Callable[[str], None] = lambda m: None) -> list[Step]:
    """Fetch the dated data a missed evening left behind. One Step per dataset; a dataset with no gap is not a step."""
    from .jobs import backfill as bf
    expected = expected_trading_day(now, store)
    t0 = clock()
    steps: list[Step] = []
    targets = (("prices", "price_eod", bf.backfill_prices), ("delivery", "delivery", bf.backfill_delivery),
               ("participant_oi", "participant_oi", bf.backfill_participant_oi), ("index_closes", "index_close", bf.backfill_index_closes))
    for name, table, fn in targets:
        gone = _untried(store, name, missing_sessions(store, table, expected), now)
        if not gone:
            continue
        left = budget_s - (clock() - t0)
        if left <= 0:
            steps.append(Step(f"gap {name}", False, f"time budget used up before it ran; {len(gone)} session(s) still missing"))
            continue
        days = (expected - date.fromisoformat(gone[0])).days + 2
        try:
            rep = fn(store, days=days, budget_s=left, start=expected, progress=progress)
        except Exception as e:  # noqa: BLE001 - a bug here must not stop the other datasets, and must be reported typed
            steps.append(Step(f"gap {name}", False, f"{ErrorKind.PARSE_FAIL}: {type(e).__name__}: {str(e)[:120]}"))
            continue
        still = [d for d in gone if d not in _have(store, table, gone[0])]
        failed_days = {f["date"] for f in rep.failed}
        _mark_tried(store, name, [d for d in still if d not in failed_days and not rep.stopped], now)    # confirmed holidays only
        ok = not rep.failed and not rep.stopped.startswith("aborted")      # a day that failed to download is not a holiday: the slot is retried
        steps.append(Step(f"gap {name}", ok, f"{rep.done} of {len(gone)} missing session(s) filled" + (f", {len(rep.failed)} failed ({rep.failed[0]['kind']})" if rep.failed else "")
                          + (f"; {rep.stopped}" if rep.stopped else "")))
    newest = store.query("SELECT MAX(trade_date) AS d FROM deal")[0]["d"] if store.query("SELECT 1 AS x FROM sqlite_master WHERE name='deal'") else None
    if newest and (expected - date.fromisoformat(newest)).days > 4 and _untried(store, "deals", [expected.isoformat()], now):
        left = budget_s - (clock() - t0)
        if left > 0:
            gap = (expected - date.fromisoformat(newest)).days + 2
            try:
                rep = bf.backfill_deals(store, days=min(gap, MAX_GAP_DAYS), window=min(30, gap), budget_s=left, today=now.astimezone(IST).date(), progress=progress)
                _mark_tried(store, "deals", [expected.isoformat()], now)
                steps.append(Step("gap deals", not rep.failed and not rep.stopped.startswith("aborted"), f"{rep.done} window(s) fetched, {len(rep.failed)} failed" + (f"; {rep.stopped}" if rep.stopped else "")))
            except Exception as e:  # noqa: BLE001
                steps.append(Step("gap deals", False, f"{ErrorKind.PARSE_FAIL}: {type(e).__name__}: {str(e)[:120]}"))
    return steps


# --- the weekly tail --------------------------------------------------------------------------------------------------
def weekly_tail(store: Store, *, private: Path, eval_dir: Path, profile=None) -> list[Step]:
    from . import weekly
    from .decisions import forward as fw
    from .portfolio import holdings
    steps: list[Step] = []
    try:
        r = fw.step(store, out_dir=Path(eval_dir) / "forward", universe_path=Path(eval_dir) / "forward_universe.json", profile=profile)
        too_soon = not r["done"] and r.get("why", "").startswith("last forward date")
        steps.append(Step("forward log", r["done"] or too_soon, (r.get("why") or f"{r['cases']} cases on {r['as_of']}")[:140]))
    except Exception as e:  # noqa: BLE001
        steps.append(Step("forward log", False, f"{ErrorKind.PARSE_FAIL}: {type(e).__name__}: {str(e)[:120]}"))
    csv_path = Path(private) / "holdings.csv"
    if csv_path.is_file():
        r = holdings.import_snapshot_csv(store, csv_path)
        steps.append(Step("holdings import", r.ok and not r.data["rejected"],
                          (f"{r.data['imported']} rows" + (f", {len(r.data['rejected'])} rejected" if r.data["rejected"] else "")) if r.ok else f"{r.error.kind}: {r.error.detail[:120]}"))
    else:
        steps.append(Step("holdings import", True, f"no {csv_path.name} in {Path(private)}: candidates only"))
    r = weekly.build_pack(store, Path(private), profile=profile)
    steps.append(Step("weekly pack", r.ok, (f"built {r.data['dir']}" if r.data.get("built") else r.data.get("why", "")) if r.ok else f"{r.error.kind}: {r.error.detail[:140]}"))
    try:
        a = weekly.import_found_answers(store, Path(private))
        n = sum(x["recorded"] for x in a["imported"])
        steps.append(Step("import Claude answers", not a["problems"], f"{n} call(s) imported from {len(a['imported'])} file(s)" + (f"; {a['problems'][0]['why']}" if a["problems"] else "")))
    except Exception as e:  # noqa: BLE001
        steps.append(Step("import Claude answers", False, f"{ErrorKind.PARSE_FAIL}: {type(e).__name__}: {str(e)[:120]}"))
    return steps


# --- the run ----------------------------------------------------------------------------------------------------------
def _record(store: Store, slot: Slot, due: datetime, steps: list[Step], status: str) -> None:
    if status == "incomplete":
        return                                    # nothing recorded: the slot stays due
    bad = [s for s in steps if not s.ok]
    if bad:
        res = Result(error=DrishtiError(ErrorKind.PARSE_FAIL, f"{len(bad)} step(s) failed: {', '.join(s.label for s in bad)}"))
    else:
        res = Result(data={"steps": len(steps)}, as_of=due.date().isoformat())
    store.log_result("catchup", res, dataset=slot.name, trade_date=due.date().isoformat(), rows=len(steps))


def run(store: Store, *, now: datetime | None = None, budget_min: float = BUDGET_MIN, private: Path | None = None, eval_dir: Path = Path("eval"),
        profile=None, only: str | None = None, plan: bool = False, run_job_fn=run_job, clock=time.monotonic,
        progress: Callable[[str], None] = lambda m: None) -> CatchupReport:
    now = (now or datetime.now(IST)).astimezone(IST)
    rep = CatchupReport()
    from . import weekly
    private = weekly.private_dir(private)
    t0, budget = clock(), budget_min * 60
    todo = due_slots(store, now)
    if only:
        if only not in {s.name for s in SLOTS}:
            raise KeyError(only)
        todo = [(s, due, "run") for s, due, _ in todo if s.name == only]
    for slot, due, state in todo:
        if state != "run":
            rep.slots.append(SlotReport(slot.name, due, state))
            continue
        if plan:
            jobs = slot.jobs_for(due) + (EVENING_LAST if slot.gap_fill else ())
            rep.slots.append(SlotReport(slot.name, due, "run", [Step(j, True, "would run") for j in jobs] + ([Step("gap fill", True, "would run")] if slot.gap_fill else [])
                                        + ([Step("weekly pack", True, "would run")] if slot.weekly_tail else [])))
            continue
        steps: list[Step] = []
        status = "ok"

        def over() -> bool:
            return clock() - t0 > budget

        def run_jobs(names):
            nonlocal status
            for name in names:
                if over():
                    status = "incomplete"
                    steps.append(Step(name, False, "not started: time budget used up; the slot stays due"))
                    continue
                try:
                    jr = run_job_fn(store, name, now=now)
                    bad = [f"{s.dataset}: {s.error_kind}" for s in jr.steps if not s.ok]
                    steps.append(Step(name, jr.ok, "; ".join(bad)[:160]))
                except Exception as e:  # noqa: BLE001
                    steps.append(Step(name, False, f"{ErrorKind.PARSE_FAIL}: {type(e).__name__}: {str(e)[:120]}"))
                progress(f"   {slot.name}: {steps[-1].label} {'ok' if steps[-1].ok else 'FAILED'}")
        run_jobs(slot.jobs_for(due))
        if slot.gap_fill and status != "incomplete":
            steps += gap_fill(store, now, budget_s=max(0.0, budget - (clock() - t0) - RESERVE_S), clock=clock, progress=progress)
        if slot.gap_fill:
            run_jobs(EVENING_LAST)
        if slot.weekly_tail and status != "incomplete":
            steps += weekly_tail(store, private=private, eval_dir=eval_dir, profile=profile)
        if status != "incomplete":
            status = "failed" if any(not s.ok for s in steps) else "ok"
        _record(store, slot, due, steps, status)
        rep.slots.append(SlotReport(slot.name, due, status, steps))
    return rep

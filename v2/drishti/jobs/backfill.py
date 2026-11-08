"""History backfill: walk back over dated archive files (or windows of an API) and fill the typed history tables.

Resumable and polite: a date already in its table is skipped, every request goes through core.fetch's per-host rate
limit, a time budget stops the run cleanly, and five hard failures in a row (blocks, network) stop it rather than hammer
a host. NOT_FOUND on a weekday is an exchange holiday and is counted, not failed. Every failed date is reported by kind.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Callable

from ..core.canary import today_ist
from ..core.envelope import DrishtiError, ErrorKind, Result
from ..providers import bse, fred, nse_archive, nse_corp, nse_pit, nse_shp, yahoo
from ..store import Store
from ..store import datasets as ds

MAX_CONSECUTIVE_FAILURES = 5


@dataclass
class BackfillReport:
    target: str
    done: int = 0
    skipped: int = 0
    holidays: int = 0
    rows: int = 0
    failed: list[dict] = field(default_factory=list)
    stopped: str = ""
    oldest: str | None = None
    newest: str | None = None

    @property
    def ok(self) -> bool:
        return not self.stopped.startswith("aborted") and len(self.failed) <= max(2, self.done // 20)


def _weekdays_back(start: date, days: int, sessions: set[str] = frozenset()):
    """Weekdays from `start` going back `days`, plus weekend special sessions (Muhurat trading) in `sessions`."""
    d = start
    end = start - timedelta(days=days)
    while d > end:
        if d.weekday() < 5 or d.isoformat() in sessions:
            yield d
        d -= timedelta(days=1)


def _have_dates(store: Store, table: str, col: str = "trade_date") -> set[str]:
    ds.ensure(store, table)
    return {r["d"] for r in store.query(f"SELECT DISTINCT {col} AS d FROM {table}")}


def _noop(msg: str) -> None:
    pass


def _daily(store: Store, target: str, table: str, fetch: Callable[[date], Result], persist: Callable[[Store, Result], int], *,
           days: int, budget_s: float, start: date, clock=time.monotonic, progress=_noop) -> BackfillReport:
    rep = BackfillReport(target)
    have = _have_dates(store, table)
    t0, streak = clock(), 0
    for d in _weekdays_back(start, days, ds.special_sessions(store)):
        if clock() - t0 > budget_s:
            rep.stopped = f"time budget reached at {d}; run again to continue"
            break
        if d.isoformat() in have:
            rep.skipped += 1
            continue
        res = fetch(d)
        if res.ok:
            rep.rows += persist(store, res)
            rep.done += 1
            if rep.done % 25 == 0:
                progress(f"   ...{target}: {rep.done} days stored, now at {d} ({clock() - t0:.0f}s)")
            streak = 0
            rep.oldest = d.isoformat()
            rep.newest = rep.newest or d.isoformat()
        elif res.error.kind == ErrorKind.NOT_FOUND:
            rep.holidays += 1
        else:
            rep.failed.append({"date": d.isoformat(), "kind": str(res.error.kind), "detail": res.error.detail[:160]})
            streak += 1
            if streak >= MAX_CONSECUTIVE_FAILURES:
                rep.stopped = f"aborted after {streak} failures in a row (last: {res.error.kind}); host may be blocking, try later"
                break
    return rep


def backfill_index_closes(store, *, days=400, budget_s=1800, start=None, progress=_noop):
    return _daily(store, "index_closes", "index_close", lambda d: nse_archive.index_closes(d, store=store),
                  lambda s, r: ds.persist_index_closes(s, r.data), days=days, budget_s=budget_s, start=start or today_ist(), progress=progress)


def backfill_participant_oi(store, *, days=365, budget_s=1800, start=None, progress=_noop):
    return _daily(store, "participant_oi", "participant_oi", lambda d: nse_archive.participant_oi(d, store=store),
                  lambda s, r: ds.persist_participant_oi(s, r.data), days=days, budget_s=budget_s, start=start or today_ist(), progress=progress)


def backfill_fo_index(store, *, days=180, budget_s=3600, start=None, progress=_noop):
    """F&O bhavcopy history, keeping only index options/futures rows (the whole file is ~40k rows a day). The raw zip is
    not kept for backfilled days, which keeps the database small; the latest days still are, via the daily job."""
    return _daily(store, "fo_index", "index_option_eod", lambda d: nse_archive.fo_bhavcopy(d, store=None),
                  lambda s, r: ds.persist_fo_index(s, r.data), days=days, budget_s=budget_s, start=start or today_ist(), progress=progress)


def backfill_prices(store, *, days=380, budget_s=3600, start=None, progress=_noop):
    """One year of daily bars: NSE cash bhavcopy (all traded stocks) plus BSE for names NSE did not trade. Raw files are not
    kept for backfilled days. A BSE failure on a day is reported but does not throw away that day's NSE prices."""
    def fetch(d):
        n = nse_archive.cm_bhavcopy(d, store=None)
        if not n.ok:
            return n
        b = bse.bhavcopy(d, store=None)
        return Result(data={"nse": n.data, "bse": b.data if b.ok else [], "bse_error": None if b.ok else f"{b.error.kind}: {b.error.detail[:80]}"},
                      as_of=n.as_of)

    def persist(s, r):
        k = ds.persist_prices(s, r.data["nse"], "NSE") + ds.persist_prices(s, r.data["bse"], "BSE")
        if r.data["bse_error"]:
            progress(f"   ...prices {r.as_of}: BSE file failed ({r.data['bse_error']}); NSE prices kept")
        return k
    ds.ensure(store, "price_eod")
    return _daily(store, "prices", "price_eod", fetch, persist, days=days, budget_s=budget_s, start=start or today_ist(), progress=progress)


def backfill_macro(store, *, rng="5y", today=None, progress=_noop):
    """Full FRED histories and Yahoo daily bars for the regime inputs. One request per series."""
    rep = BackfillReport("macro")
    for sid in fred.SERIES:
        r = fred.fred_series(sid, store=store, today=today)
        _tally(rep, sid, r, (r.data or {}).get("n_total", 0))
    for sym in yahoo.SYMBOLS:
        r = yahoo.daily(sym, rng=rng, store=store, today=today)
        _tally(rep, sym, r, len(r.data or []))
    return rep


def _tally(rep: BackfillReport, name: str, r: Result, n: int) -> None:
    if r.ok:
        rep.done += 1
        rep.rows += n
    else:
        rep.failed.append({"date": name, "kind": str(r.error.kind), "detail": r.error.detail[:160]})


def backfill_insider(store, *, days=180, window=7, budget_s=3600, today=None, clock=time.monotonic, progress=_noop):
    """Insider filings in `window`-day slices going back `days`. Filings already stored are skipped, so this resumes."""
    from . import INSIDER_DDL, _insider_persist
    store.ensure_table(INSIDER_DDL)
    rep = BackfillReport("insider")
    today = today or today_ist()
    t0 = clock()
    for k in range(0, days, window):
        if clock() - t0 > budget_s:
            rep.stopped = f"time budget reached at window {k}d; run again to continue"
            break
        end = today - timedelta(days=k)
        known = [x["app_id"] for x in store.query("SELECT DISTINCT app_id FROM insider_event")]
        r = nse_pit.insider_trades(window, today=end, skip_app_ids=known, max_filings=5000, max_age_days=window + 3, progress=progress)
        if r.data:
            rep.rows += _insider_persist(store, r)
        n_ev = len((r.data or {}).get("events") or [])
        progress(f"   ...insider: window ending {end} -> {(r.data or {}).get('filings', 0)} new filings, {n_ev} events"
                 f"{'' if r.ok else ' (' + str(r.error.kind) + ')'} ({clock() - t0:.0f}s)")
        if r.ok:
            rep.done += 1
            rep.oldest = (end - timedelta(days=window)).isoformat()
            rep.newest = rep.newest or end.isoformat()
        else:
            rep.failed.append({"date": end.isoformat(), "kind": str(r.error.kind), "detail": r.error.detail[:160]})
            if r.error.kind in (ErrorKind.BLOCKED, ErrorKind.BLOCKED_HTML, ErrorKind.TIMEOUT, ErrorKind.NETWORK):
                rep.stopped = f"aborted: NSE is throttling ({r.error.kind}); what was fetched is kept, try again in a few hours"
                break
    return rep


def backfill_shp(store, *, budget_s=3600, today=None, clock=time.monotonic, progress=_noop):
    """Shareholding filings: the latest covered quarter for every company, plus the previous quarter only for companies
    that have encumbered shares now (that is what 'rising pledge' needs). Known gap: a company that released its whole
    pledge last quarter shows zero now and is not compared. Already-stored filings are skipped, so this resumes."""
    ds.ensure(store, "shp_filing")
    rep = BackfillReport("shp")
    today = today or today_ist()
    m = nse_shp.shp_master_rows(days=230, today=today)
    if not m.ok:
        rep.stopped = f"aborted: master list failed ({m.error.kind}: {m.error.detail[:120]})"
        return rep
    q_ends = sorted({r["as_on"] for r in m.data if nse_shp.is_quarter_end(date.fromisoformat(r["as_on"]))}, reverse=True)
    counts = {q: len({r["isin"] for r in m.data if r["as_on"] == q}) for q in q_ends}
    current = next((q for q in q_ends if counts[q] >= 0.6 * max(counts.values())), None)   # newest quarter most companies have filed
    if current is None:
        rep.stopped = "aborted: no well-covered quarter in the master list"
        return rep
    previous = next((q for q in q_ends if q < current), None)
    latest: dict[tuple, dict] = {}
    for r in m.data:
        if r["as_on"] in (current, previous) and r["isin"]:
            k = (r["isin"], r["as_on"])
            if k not in latest or r["broadcast"] > latest[k]["broadcast"]:
                latest[k] = r
    t0 = clock()
    for phase, quarter in (("current", current), ("previous", previous)):
        if quarter is None:
            continue
        have = {x["isin"] for x in store.query("SELECT isin FROM shp_filing WHERE as_on=?", (quarter,))}
        if phase == "previous":   # only companies encumbered in the current quarter
            wanted = {x["isin"] for x in store.query("SELECT isin FROM shp_filing WHERE as_on=? AND encumbered_shares > 0", (current,))}
        else:
            wanted = {k[0] for k in latest if k[1] == quarter}
        todo = sorted(i for i in wanted if i not in have and (i, quarter) in latest)
        rep.skipped += len(wanted) - len(todo)
        progress(f"   ...shp: {phase} quarter {quarter}: {len(todo)} filings to fetch, {len(wanted) - len(todo)} already stored")
        streak = 0
        for isin in todo:
            if clock() - t0 > budget_s:
                rep.stopped = f"time budget reached in the {phase} quarter; run again to continue"
                break
            row = latest[(isin, quarter)]
            r = nse_shp.shp_filing(row["xbrl_url"])
            if r.ok:
                rep.rows += ds.persist_shp_filing(store, r.data, row)
                rep.done += 1
                streak = 0
                if rep.done % 100 == 0:
                    progress(f"   ...shp: {rep.done} filings stored ({clock() - t0:.0f}s)")
            else:
                rep.failed.append({"date": f"{row['symbol']} {quarter}", "kind": str(r.error.kind), "detail": r.error.detail[:160]})
                streak = streak + 1 if r.error.kind in (ErrorKind.BLOCKED, ErrorKind.BLOCKED_HTML, ErrorKind.NETWORK, ErrorKind.TIMEOUT) else 0
                if streak >= MAX_CONSECUTIVE_FAILURES:
                    rep.stopped = f"aborted after {streak} network/block failures in a row; try later"
                    break
        if rep.stopped:
            break
    rep.newest, rep.oldest = current, previous or current
    return rep


WINDOW_DDL = """
CREATE TABLE IF NOT EXISTS backfill_window (
    target TEXT NOT NULL, start TEXT NOT NULL, end TEXT NOT NULL, rows INTEGER, capped TEXT, done_at TEXT,
    PRIMARY KEY (target, start, end))"""


# Verified 8 Nov 2026: NSE's historical JSON caps every answer at 70 rows, even for one day; its CSV download does not
# (96 bulk deals on 6 Nov, as in the snapshot). nse_corp.historical_deals uses the CSV wherever the cap bites.
COMPLETE_HISTORY_KINDS = ("block", "bulk", "short")


def backfill_deals(store, *, days=365, window=30, kinds=COMPLETE_HISTORY_KINDS, budget_s=3600, today=None, clock=time.monotonic,
                   progress=_noop):
    """Deals for the past `days` from NSE's historical endpoint in `window`-day pieces (split further when NSE's 70-row
    cap is hit). Block deals only by default: see COMPLETE_HISTORY_KINDS. Finished windows are remembered, so this
    resumes. A day that still fills the cap on its own is kept but reported as failed: its list is known to be short."""
    from datetime import datetime
    ds.ensure(store, "deal")
    store.ensure_table(WINDOW_DDL)
    rep = BackfillReport("deals")
    today = today or today_ist()
    t0 = clock()
    have = {(r["target"], r["start"], r["end"]) for r in store.query("SELECT target, start, end FROM backfill_window WHERE capped IS NULL")}
    fails = 0
    for kind in kinds:
        for k in range(0, days, window):
            end = today - timedelta(days=k + 1)
            start = end - timedelta(days=window - 1)
            tag = f"deals_{kind}"
            if (tag, start.isoformat(), end.isoformat()) in have:
                rep.skipped += 1
                continue
            if clock() - t0 > budget_s:
                rep.stopped = f"time budget reached at {kind} {end}; run again to continue"
                return rep
            r = nse_corp.historical_deals(kind, start, end, extra_days=ds.special_sessions(store))
            if r.data:
                n = ds.persist_deals(store, r.data["deals"], "nse_hist")
                rep.rows += n
                store.upsert("backfill_window", [{"target": tag, "start": start.isoformat(), "end": end.isoformat(), "rows": n,
                                                  "capped": ",".join(r.data["capped_days"]) or None,
                                                  "done_at": datetime.now().isoformat(timespec="seconds")}], ("target", "start", "end"))
                rep.done += 1
                rep.oldest = min(rep.oldest or start.isoformat(), start.isoformat())
                rep.newest = max(rep.newest or end.isoformat(), end.isoformat())
                for d in r.data["capped_days"]:
                    rep.failed.append({"date": d, "kind": "PARSE_FAIL", "detail": f"{kind}: NSE capped the day at {nse_corp.HIST_CAP} rows"})
                fails = 0
                progress(f"   ...deals {kind}: {start}..{end} -> {n} deals in {r.data['requests']} requests"
                         f"{', capped days ' + str(r.data['capped_days']) if r.data['capped_days'] else ''} ({clock() - t0:.0f}s)")
            else:
                fails += 1
                rep.failed.append({"date": end.isoformat(), "kind": str(r.error.kind), "detail": f"{kind}: {r.error.detail[:150]}"})
                progress(f"   ...deals {kind}: {start}..{end} failed {r.error.kind}")
                if fails >= MAX_CONSECUTIVE_FAILURES:
                    rep.stopped = f"aborted after {fails} failures in a row ({r.error.kind}); what was fetched is kept"
                    return rep
    return rep


TARGETS = {
    "index_closes": backfill_index_closes, "participant_oi": backfill_participant_oi, "fo_index": backfill_fo_index,
    "prices": backfill_prices, "macro": backfill_macro, "insider": backfill_insider, "shp": backfill_shp, "deals": backfill_deals,
}
DEFAULT_DAYS = {"prices": 380, "index_closes": 400, "participant_oi": 365, "fo_index": 180, "insider": 180, "deals": 365}

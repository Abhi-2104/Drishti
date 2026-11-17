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
from ..providers import bse, fred, mospi, nse_archive, nse_corp, nse_pit, nse_shp, yahoo
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


UDIFF_FROM = date(2024, 1, 1)   # the UDiFF price files exist from here (checked 9 Nov 2026); earlier days use the older file format


def _noop(msg: str) -> None:
    pass


def _bar(done: int, total: int, width: int = 20) -> str:
    frac = 0 if total <= 0 else min(1.0, done / total)
    n = round(frac * width)
    return f"[{'█' * n}{'░' * (width - n)}] {frac * 100:3.0f}%"


def _eta(elapsed: float, done: int, total: int) -> str:
    if done <= 0 or elapsed <= 0:
        return "?"
    left = max(0.0, elapsed / done * (total - done))
    return f"{left / 60:.0f} min left" if left >= 90 else f"{left:.0f}s left"


def _daily(store: Store, target: str, table: str, fetch: Callable[[date], Result], persist: Callable[[Store, Result], int], *,
           days: int, budget_s: float, start: date, clock=time.monotonic, progress=_noop) -> BackfillReport:
    rep = BackfillReport(target)
    have = _have_dates(store, table)
    t0, streak = clock(), 0
    plan = list(_weekdays_back(start, days, ds.special_sessions(store)))
    total, todo = len(plan), sum(1 for x in plan if x.isoformat() not in have)
    if todo < total:
        progress(f"   {target}: {total - todo} of {total} weekdays already stored, {todo} to fetch")
    for i, d in enumerate(plan, 1):
        if clock() - t0 > budget_s:
            rep.stopped = f"time budget reached at {d}; run again to continue"
            break
        if d.isoformat() in have:
            rep.skipped += 1
            continue
        res = fetch(d)
        tried = rep.done + rep.holidays + len(rep.failed) + 1
        if res.ok:
            rep.rows += persist(store, res)
            rep.done += 1
            if rep.done % 25 == 0:
                progress(f"   {_bar(i, total)}  {target}: {rep.done} stored, now at {d}, {_eta(clock() - t0, tried, todo)} ({clock() - t0:.0f}s)")
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


def _bse_for_day(d: date, isin_map: dict[str, str]) -> Result:
    """BSE prices for one day in whichever file layout that day has (UDiFF from 2024, the old files before, or when UDiFF is missing)."""
    if d >= UDIFF_FROM:
        b = bse.bhavcopy(d, store=None)
        if b.ok or b.error.kind not in (ErrorKind.NOT_FOUND, ErrorKind.BLOCKED_HTML):
            return b
    return bse.bhavcopy_legacy(d, store=None, isin_map=isin_map)


def _bse_persist(s, rows: list[dict], isin_map: dict[str, str]) -> tuple[int, int]:
    """(rows stored, rows dropped for want of an ISIN). Every scrip -> ISIN pair seen is remembered, and the in-memory map is
    moved to the older ISIN as the backfill walks back in time."""
    ds.record_bse_scrips(s, rows)
    for r in rows:
        if r.get("isin") and r.get("scrip_code"):
            isin_map[r["scrip_code"]] = r["isin"]
    return ds.persist_prices(s, rows, "BSE"), sum(1 for r in rows if not r.get("isin"))


def backfill_prices(store, *, days=380, budget_s=3600, start=None, progress=_noop):
    """Daily bars: NSE cash bhavcopy (all traded stocks) plus BSE for names NSE did not trade. Raw files are not kept for
    backfilled days. A BSE failure on a day is reported but does not throw away that day's NSE prices; `backfill bse_prices`
    later fills just the BSE side of such days."""
    isin_map = ds.bse_isin_map(store)

    def fetch(d):
        legacy = d < UDIFF_FROM                                  # known not to exist: skip the guaranteed 404 and save ~1s a day
        n = nse_archive.cm_bhavcopy_legacy(d, store=None) if legacy else nse_archive.cm_bhavcopy(d, store=None)
        if not n.ok and not legacy and n.error.kind == ErrorKind.NOT_FOUND:     # before the UDiFF files existed (or a holiday: the legacy file is also missing then)
            n = nse_archive.cm_bhavcopy_legacy(d, store=None)
        if not n.ok:
            return n
        b = _bse_for_day(d, isin_map)
        return Result(data={"nse": n.data, "bse": b.data if b.ok else [], "bse_error": None if b.ok else f"{b.error.kind}: {b.error.detail[:80]}"},
                      as_of=n.as_of)

    def persist(s, r):
        k = ds.persist_prices(s, r.data["nse"], "NSE")
        kb, unmapped = _bse_persist(s, r.data["bse"], isin_map)
        if r.data["bse_error"]:
            progress(f"   ...prices {r.as_of}: BSE file failed ({r.data['bse_error']}); NSE prices kept")
        elif unmapped:
            progress(f"   ...prices {r.as_of}: {unmapped} BSE rows without a known ISIN skipped (oldest file layout)")
        return k + kb
    ds.ensure(store, "price_eod")
    rep = _daily(store, "prices", "price_eod", fetch, persist, days=days, budget_s=budget_s, start=start or today_ist(), progress=progress)
    progress("   ...prices: rebuilding split/bonus adjustments over the whole history (a few minutes, no output until done)")
    adj = ds.rebuild_adjustments(store)
    progress(_adj_line(adj, "prices"))
    return rep


def backfill_bse_prices(store, *, days=3800, budget_s=3600, start=None, clock=time.monotonic, progress=_noop):
    """Only the BSE side, for stored sessions that have NSE prices but no BSE rows (a BSE file failed or was unreadable when the day
    was first fetched). Walks back from the newest such day; resumes by itself, since a filled day no longer qualifies."""
    ds.ensure(store, "price_eod")
    rep = BackfillReport("bse_prices")
    lo = ((start or today_ist()) - timedelta(days=days)).isoformat()
    todo = [r["d"] for r in store.query(
        "SELECT trade_date d FROM price_eod WHERE trade_date >= ? GROUP BY trade_date HAVING SUM(exchange='BSE') = 0 ORDER BY trade_date DESC",
        (lo,))]
    progress(f"   bse_prices: {len(todo)} stored sessions have NSE prices but no BSE prices")
    isin_map = ds.bse_isin_map(store)
    t0, streak, unmapped = clock(), 0, 0
    for i, day in enumerate(todo, 1):
        if clock() - t0 > budget_s:
            rep.stopped = f"time budget reached at {day}; run again to continue"
            break
        r = _bse_for_day(date.fromisoformat(day), isin_map)
        if r.ok:
            k, miss = _bse_persist(store, r.data, isin_map)
            rep.rows += k
            unmapped += miss
            rep.done += 1
            streak = 0
            rep.oldest, rep.newest = day, rep.newest or day
            if rep.done % 25 == 0:
                progress(f"   {_bar(i, len(todo))}  bse_prices: {rep.done} filled, now at {day}, {_eta(clock() - t0, i, len(todo))} ({clock() - t0:.0f}s)")
        elif r.error.kind == ErrorKind.NOT_FOUND:
            rep.holidays += 1          # BSE published no file for a day NSE traded: reported in the summary as missing
            rep.failed.append({"date": day, "kind": "NOT_FOUND", "detail": "no BSE file for a day NSE traded"})
        else:
            rep.failed.append({"date": day, "kind": str(r.error.kind), "detail": r.error.detail[:160]})
            streak += 1
            if streak >= MAX_CONSECUTIVE_FAILURES:
                rep.stopped = f"aborted after {streak} failures in a row (last: {r.error.kind}); host may be blocking, try later"
                break
    if unmapped:
        progress(f"   ...bse_prices: {unmapped} rows from the oldest layout had no known ISIN and were skipped")
    return rep


def backfill_delivery(store, *, days=380, budget_s=3600, start=None, progress=_noop):
    """Delivery quantity and % per stock, one NSE file per session, so the delivery signal has a year of history."""
    ds.ensure(store, "delivery")
    return _daily(store, "delivery", "delivery", lambda d: nse_archive.delivery(d, store=None), lambda s, r: ds.persist_delivery(s, r.data),
                  days=days, budget_s=budget_s, start=start or today_ist(), progress=progress)


def _adj_line(adj: dict, tag: str) -> str:
    return (f"   ...{tag}: {adj['events']} split/bonus events verified against the stored prices, {len(adj['unverified'])} not confirmed "
            f"(not applied), {len(adj['unparsed'])} with unreadable terms, {adj['successions']} ISIN successions")


def backfill_adjustments(store, *, progress=_noop, **_):
    """Recompute every split / bonus / rights adjustment from the stored prices (after a corporate-action backfill, so
    the events get labelled). Fast: one pass over the price table."""
    rep = BackfillReport("adjustments")
    adj = ds.rebuild_adjustments(store)
    rep.done, rep.rows = 1, adj["events"]
    for x in adj["unparsed"]:
        rep.failed.append({"date": x["ex_date"], "kind": "UNPARSED", "detail": f"{x['symbol']}: {x['subject'][:100]}"})
    progress(_adj_line(adj, "adjustments"))
    return rep


def backfill_corp_actions(store, *, days=730, window=90, budget_s=1800, today=None, clock=time.monotonic, progress=_noop):
    """NSE and BSE corporate actions by ex-date for the past `days`, in `window`-day pieces (remembered, so this resumes),
    then the price adjustments are relabelled. Lets every split / bonus in the price history say what it was."""
    from datetime import datetime
    ds.ensure(store, "corp_action")
    store.ensure_table(WINDOW_DDL)
    rep = BackfillReport("corp_actions")
    today = today or today_ist()
    t0 = clock()
    have = {(r["target"], r["start"], r["end"]) for r in store.query("SELECT target, start, end FROM backfill_window")}
    for k in range(0, days, window):
        end = today - timedelta(days=k)
        start = end - timedelta(days=window - 1)
        if clock() - t0 > budget_s:
            rep.stopped = f"time budget reached at {end}; run again to continue"
            break
        for tag, fn, persist in (("corp_nse", lambda: nse_corp.corporate_actions(back_days=window - 1, ahead_days=0, today=end), ds.persist_corp_actions),
                                 ("corp_bse", lambda: bse.corporate_actions(back_days=window - 1, ahead_days=0, today=end), ds.persist_bse_corp_actions)):
            if (tag, start.isoformat(), end.isoformat()) in have:
                rep.skipped += 1
                continue
            if tag == "corp_bse" and k > 0:
                if k == window:
                    progress("   ...corp actions BSE: the endpoint has no history (it ignores its date range); past BSE-only actions cannot be backfilled")
                continue
            r = fn()
            if r.ok or (r.error and r.error.kind == ErrorKind.EMPTY):
                n = persist(store, r.data) if r.ok else 0
                rep.rows += n
                rep.done += 1
                store.upsert("backfill_window", [{"target": tag, "start": start.isoformat(), "end": end.isoformat(), "rows": n, "capped": None,
                                                  "done_at": datetime.now().isoformat(timespec="seconds")}], ("target", "start", "end"))
                progress(f"   ...corp actions {tag[5:].upper()} {start}..{end}: {n} actions ({clock() - t0:.0f}s)")
            else:
                rep.failed.append({"date": end.isoformat(), "kind": str(r.error.kind), "detail": f"{tag}: {r.error.detail[:150]}"})
                progress(f"   ...corp actions {tag[5:].upper()} {start}..{end}: {r.error.kind}")
    adj = ds.rebuild_adjustments(store)
    progress(_adj_line(adj, "adjustments rebuilt"))
    return rep


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


# first month each MoSPI series can be asked for: CPI base 2024 starts Jan 2025, WPI base 2022-23 has 2023 onward, IIP 2022-23 from 2024
INDIA_START = {"cpi": (2025, 1), "wpi": (2023, 1), "iip": (2024, 1)}


def backfill_india_macro(store, *, budget_s=1800, today=None, clock=time.monotonic, progress=_noop, **_):
    """Monthly CPI, WPI and IIP history from MoSPI, newest month first, one request per month. A month already stored is skipped;
    a month not published yet (No Data Found) is counted as a hole, not a failure. WPI year-on-year is derived at the end."""
    ds.ensure(store, "macro_series")
    rep = BackfillReport("india_macro")
    today = today or today_ist()
    t0, fails = clock(), 0
    for kind, (y0, m0) in INDIA_START.items():
        have = {r["d"][:7] for r in store.query("SELECT obs_date AS d FROM macro_series WHERE series=?", (mospi.SERIES[f"{kind}_index"],))}
        y, m = today.year, today.month
        while (y, m) >= (y0, m0):
            ym = f"{y}-{m:02d}"
            if ym in have:
                rep.skipped += 1
            elif clock() - t0 > budget_s:
                rep.stopped = f"time budget reached at {kind} {ym}; run again to continue"
                return rep
            else:
                r = mospi.MONTH_FETCH[kind](y, m)
                if r.ok:
                    rep.done += 1
                    rep.rows += ds.persist_macro_bundle(store, r.data)
                    rep.oldest, rep.newest = min(rep.oldest or ym, ym), max(rep.newest or ym, ym)
                    fails = 0
                elif r.error.kind == ErrorKind.EMPTY:
                    rep.holidays += 1          # not published (yet) or outside the series: a hole, not a failure
                else:
                    fails += 1
                    rep.failed.append({"date": ym, "kind": str(r.error.kind), "detail": f"{kind}: {r.error.detail[:150]}"})
                    if fails >= MAX_CONSECUTIVE_FAILURES:
                        rep.stopped = f"aborted after {fails} failures in a row ({r.error.kind}); what was fetched is kept"
                        return rep
            m -= 1
            if m == 0:
                y, m = y - 1, 12
        progress(f"   ...india_macro {kind}: done to {y0}-{m0:02d} ({clock() - t0:.0f}s)")
    ds.derive_yoy(store, "mospi:wpi_index", "mospi:wpi_yoy", "mospi-derived")
    return rep


TARGETS = {
    "index_closes": backfill_index_closes, "participant_oi": backfill_participant_oi, "fo_index": backfill_fo_index,
    "prices": backfill_prices, "macro": backfill_macro, "insider": backfill_insider, "shp": backfill_shp, "deals": backfill_deals,
    "corp_actions": backfill_corp_actions, "adjustments": backfill_adjustments, "delivery": backfill_delivery,
    "india_macro": backfill_india_macro, "bse_prices": backfill_bse_prices,
}
DEFAULT_DAYS = {"prices": 380, "delivery": 380, "bse_prices": 3800, "index_closes": 400, "participant_oi": 365, "fo_index": 180, "insider": 180, "deals": 365, "corp_actions": 730}

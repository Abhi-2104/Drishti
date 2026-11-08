"""Run the real providers against live data once and report how they cope. Read-only; home IP, any day.

    uv run python phase0\\scout\\validate_live.py                 # everything
    uv run python phase0\\scout\\validate_live.py --only shp --shp-sample 300
Groups: archive (NSE bhavcopies, OI, indices, ban, deals), bse, pit (insider trades), shp (shareholding patterns).
Uses an in-memory store, so the fetch-once path is exercised too. Writes tests/fixtures/_raw/validate_live.json
(gitignored) with every failure's URL so a failing real-world case can become a fixture.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

from drishti.core.canary import today_ist
from drishti.core.envelope import DrishtiError, Result
from drishti.core.fetch import get
from drishti.providers import _util as u
from drishti import resolve
from drishti.providers import nse_quote, screener
from drishti.providers import bse, cftc, fred, nse_archive, nse_corp, nse_flows, nse_pit, nse_shp, security_master
from drishti.providers.profiles import NSE_ARCHIVE
from drishti.store import Store

OUT = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "_raw" / "validate_live.json"
GROUPS = ("archive", "bse", "pit", "shp", "macro", "master", "corp", "quotes", "samples", "batch3", "adjust", "batch5", "market", "us")


def line(ok: bool, name: str, secs: float, detail: str) -> None:
    print(f"{'OK  ' if ok else 'FAIL'} {name:<28s} {secs:5.1f}s  {detail}")


def timed(fn):
    t = time.time()
    r = fn()
    return r, time.time() - t


def show(report: list, name: str, fn, describe) -> Result:
    r, secs = timed(fn)
    detail = describe(r) if r.ok else f"{r.error.kind}: {r.error.detail[:150]}"
    line(r.ok, name, secs, detail)
    report.append({"name": name, "ok": r.ok, "as_of": r.as_of, "secs": round(secs, 2),
                   "error": None if r.ok else {"kind": str(r.error.kind), "detail": r.error.detail}})
    return r


def save_failure(kind: str, name: str, url: str) -> str | None:
    """Download a file that failed to parse so it can become a fixture (the failing body is the evidence)."""
    try:
        dst = OUT.parent / "validate_failures" / f"{kind}_{name}.xml"
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(get(url, NSE_ARCHIVE, expect="xml").body)
        return str(dst)
    except (DrishtiError, OSError) as e:
        return f"(could not save: {e})"


def summarise_shp(filings: list[dict]) -> dict:
    """Facts about a batch of parsed SHP filings, for eyeballing real-world variety."""
    enc = [f for f in filings if f["encumbered_shares"] > 0]
    return {
        "parsed": len(filings), "with_encumbrance": len(enc),
        "pledge_only": sum(1 for f in enc if f["pledged_shares"] == f["encumbered_shares"]),
        "ndu_or_other": sum(1 for f in enc if f["ndu_shares"] or f["other_encumbered_shares"]),
        "flags_inconsistent": [f["symbol"] for f in filings if not f["flags_consistent"]],
        "holders_do_not_reconcile": [f["symbol"] for f in filings if not f["holders_reconcile"]],
        "no_promoter": [f["symbol"] for f in filings if f["no_promoter"]],
        "invalid_isin": [f["symbol"] for f in filings if not f["isin_valid"]],
        "versions": dict(Counter(f["form_version"] for f in filings)),
        "worst_encumbered_pct_of_promoter": sorted(((f["symbol"], f["encumbered_pct_of_promoter"]) for f in enc),
                                                   key=lambda x: -(x[1] or 0))[:5],
    }


def group_archive(report: list, store: Store) -> None:
    for name, fn in (("cm_bhavcopy", nse_archive.cm_bhavcopy), ("fo_bhavcopy", nse_archive.fo_bhavcopy),
                     ("participant_oi", nse_archive.participant_oi), ("index_closes", nse_archive.index_closes),
                     ("fo_ban_list", nse_archive.fo_ban_list)):
        show(report, name, lambda fn=fn: u.latest(lambda d: fn(d, store=store)), lambda r: f"{len(r.data)} rows as of {r.as_of}")
    show(report, "bulk_deals", lambda: nse_archive.bulk_deals(store=store), lambda r: f"{len(r.data)} rows as of {r.as_of}")
    show(report, "block_deals", lambda: nse_archive.block_deals(store=store), lambda r: f"{len(r.data)} rows as of {r.as_of}")
    r1 = u.latest(lambda d: nse_archive.cm_bhavcopy(d, store=store))   # second call: must come from the store
    print(f"     fetch-once: second cm_bhavcopy call served from store = {r1.ok and len(store.query('SELECT 1 FROM raw_blob')) > 0}")


def group_bse(report: list, store: Store) -> None:
    show(report, "bse_bhavcopy", lambda: u.latest(lambda d: bse.bhavcopy(d, store=store)), lambda r: f"{len(r.data)} rows as of {r.as_of}")


def group_pit(report: list, store: Store) -> None:
    r = show(report, "insider_trades_7d", lambda: nse_pit.insider_trades(7),
             lambda r: f"{len(r.data['events'])} events from {r.data['filings']} filings, {r.data['duplicates_dropped']} duplicates dropped, as of {r.as_of}")
    d = r.data
    if not d:
        return
    ev = d["events"]
    print(f"     directions: {dict(Counter(e['direction'] for e in ev))}; categories: {dict(Counter(e['category'] for e in ev).most_common(6))}")
    other = Counter(e["txn_type"] for e in ev if e["direction"] == "OTHER")
    if other:
        print(f"     direction OTHER, by transaction type: {dict(other.most_common(8))}")
    bad_isin = [(e["symbol"], e.get("isin_raw")) for e in ev if not e["isin_valid"]]
    if bad_isin:
        print(f"     invalid ISIN in filings: {sorted(set(bad_isin))[:5]}")
    print(f"     holding_reconciles False: {sum(1 for e in ev if e['holding_reconciles'] is False)}; invalid ISIN: {sum(1 for e in ev if not e['isin_valid'])}; "
          f"form versions: {dict(Counter(e['form_version'] for e in ev))}")
    for f in d["failed"][:8]:
        print(f"     FAILED filing {f['app_id']} {f['symbol']}: {f['kind']} {f['detail'][:100]}")
    report[-1]["failed_filings"] = d["failed"]


def group_macro(report: list, store: Store) -> None:
    r = show(report, "fii_dii_cash", lambda: nse_flows.fii_dii_cash(store=store),
             lambda r: f"{r.data['date']}: FII net {r.data['fii']['net']:+,.0f} cr, DII net {r.data['dii']['net']:+,.0f} cr (provisional)")
    for sid in fred.SERIES:
        show(report, f"fred_{sid}", lambda sid=sid: fred.fred_series(sid, store=store),
             lambda r: f"{r.data['n_total']} observations, latest {r.data['latest']['value']} on {r.as_of}")
    r = show(report, "cftc_tff", lambda: cftc.cftc_tff(store=store), lambda r: f"{len(r.data)} watched contracts, positions as of {r.as_of}")
    for x in (r.data or []):
        print(f"     {x['label']:<16s} OI {x['open_interest']:>9,}  lev funds net {x['lev_money']['net']:>+10,} ({x['lev_money']['net_change']:+,})"
              f"  asset mgrs net {x['asset_mgr']['net']:>+10,}")


def group_master(report: list, store: Store) -> None:
    r = show(report, "security_master", lambda: security_master.build_security_master(store),
             lambda r: f"{r.data['securities']} securities; BSE file {r.data['stats']['bse_file_date']}")
    if not r.ok:
        return
    st = r.data["stats"]
    print("     " + json.dumps({k: st[k] for k in ("by_listing", "by_class", "nse_equities", "nse_equities_seen_on_bse", "overlap",
                                                    "skipped_from_bse", "rejected", "conflicts", "symbol_differs", "warnings")}, default=str))
    for c in r.data["conflicts"][:5]:
        print(f"     CONFLICT {c}")
    for x in r.data["rejected"][:5]:
        print(f"     REJECTED {x}")
    bse_only = store.query("SELECT bse_scrip, bse_symbol FROM security WHERE nse_symbol IS NULL AND bse_series IN ('A','B') AND instrument_class='equity' LIMIT 2")
    for q in ["RELIANCE", "500325", "M&M", "INFRABEES", "GOLDBEES"] + [x["bse_scrip"] for x in bse_only]:
        rr = resolve.resolve(store, q)
        if rr.ok:
            for m in rr.data["matches"]:
                print(f"     {q:<10s} -> {m['isin']} {m['name'][:28]:<28s} NSE {m['nse_symbol']} ({m['nse_series']})  BSE {m['bse_symbol']} {m['bse_scrip']} ({m['bse_series']})  {m['instrument_class']}")
        else:
            print(f"     {q:<10s} -> {rr.error.kind}: {rr.error.detail[:90]}")


def group_corp(report: list, store: Store) -> None:
    r1 = show(report, "announcements_1d", lambda: nse_corp.announcements(days=1), lambda r: f"{len(r.data)} announcements, newest {r.as_of}")
    r7 = show(report, "announcements_7d", lambda: nse_corp.announcements(days=7), lambda r: f"{len(r.data)} announcements over 7 days")
    if r1.ok and r7.ok:
        oldest = min(x["published"] for x in r7.data)
        honoured = len(r7.data) > len(r1.data) and oldest[:10] < r1.data[-1]["published"][:10]
        print(f"     date range honoured: {honoured} (7d oldest {oldest}; a capped feed would return the same ~20 rows)")
        print(f"     categories: {dict(Counter(x['category'] for x in r7.data).most_common(6))}")
    show(report, "announcements_RELIANCE", lambda: nse_corp.announcements(days=30, symbol="RELIANCE"), lambda r: f"{len(r.data)} for RELIANCE in 30 days")
    show(report, "bse_announcements_500325", lambda: bse.announcements("500325", days=30), lambda r: f"{len(r.data)} BSE filings for 500325 in 30 days")
    r = show(report, "corporate_actions", lambda: nse_corp.corporate_actions(), lambda r: f"{len(r.data)} actions (ex-dates -7d..+30d)")
    if r.ok:
        print(f"     kinds: {dict(Counter(x['kind'] for x in r.data))}; upcoming: {[(x['symbol'], x['ex_date'], x['kind']) for x in r.data if x['ex_date'] and x['ex_date'] >= str(today_ist())][:5]}")
    r = show(report, "credit_ratings", lambda: nse_corp.credit_ratings(), lambda r: f"{len(r.data)} rating actions, newest {r.as_of}")
    if r.ok:
        print(f"     actions: {dict(Counter(x['action'] for x in r.data))}; by scale: {dict(Counter(x['direction_by_scale'] for x in r.data))}")
        dn = [(x["company"], x["previous_rating"], x["rating"]) for x in r.data if x["direction_by_scale"] == "downgrade"][:3]
        print(f"     downgrades by scale: {dn}")
    r = show(report, "large_deals", lambda: nse_corp.large_deals(), lambda r: f"bulk {len(r.data['bulk'])}, block {len(r.data['block'])}, short {len(r.data['short'])} on {r.as_of}")
    r = show(report, "ipos", lambda: nse_corp.ipos(), lambda r: f"current {len(r.data['current'])}, upcoming {len(r.data['upcoming'])}, closed in 30d {len(r.data['recent'])}")
    if r.ok and (r.data["current"] or r.data["upcoming"]):
        x = (r.data["current"] or r.data["upcoming"])[0]
        print(f"     first open/upcoming: {x['company']} {x['open']}..{x['close']} {x['price_range']}  raw keys: {sorted(x['raw'])[:12]}")
    show(report, "holidays", lambda: nse_corp.holidays(), lambda r: f"{len(r.data)} holiday rows (CM+FO)")
    for sym in ("NIFTY", "BANKNIFTY"):
        show(report, f"expiries_{sym}", lambda sym=sym: nse_corp.expiries(sym), lambda r: f"next: {r.data['expiries'][:4]}")
    show(report, "market_status", lambda: nse_corp.market_status(),
         lambda r: f"CM {r.data['capital_market']}, Gift Nifty {r.data['gift_nifty']['last']} at {r.data['gift_nifty']['timestamp']}, premium {r.data.get('gift_premium_pts')} pts")


BASKET = ["RELIANCE", "HDFCBANK", "BAJFINANCE", "INFY", "SBIN", "M&M", "BAJAJ-AUTO", "ITC", "VEDL", "KAYNES", "542034"]


def _pick_dynamic(store: Store) -> list[str]:
    """One NSE SME stock and one recent IPO, chosen from live data so the thinnest-data cases get exercised too."""
    out = []
    sme = store.query("SELECT nse_symbol FROM security WHERE nse_series IN ('SM', 'ST') AND nse_symbol IS NOT NULL ORDER BY nse_listed_on DESC LIMIT 1")
    if sme:
        out.append(sme[0]["nse_symbol"])
    r = nse_corp.ipos()
    if r.ok:
        rec = [x["symbol"] for x in r.data["recent"] if x["symbol"] and x["security_type"] != "SME"]
        if rec:
            out.append(rec[0])
    return out


def group_quotes(report: list, store: Store) -> None:
    """Batch 2 end to end over a basket chosen for edge cases (banks, ADR/GDR issuers, '&' and '-' symbols, no promoter,
    high pledge, midcap, BSE-only, an SME stock and a recent IPO)."""
    from drishti import data_tools as dt
    from drishti.config import load_settings
    from drishti.signals import price
    m = security_master.build_security_master(store)
    if not m.ok:
        print(f"     security master failed ({m.error.kind}); cannot resolve the basket")
        return
    names = BASKET + _pick_dynamic(store)
    print(f"     basket: {names}")
    with Store(load_settings().db_path) as real:          # read-only use: price history lives in your real store
        for q in names:
            f = show(report, f"fundamentals_{q}", lambda q=q: dt.fundamentals(store, q),
                     lambda r: f"{r.data['company']} ({r.data['basis']}), quarter {r.as_of}, quality {r.data['quality']}")
            show(report, f"quote_{q}", lambda q=q: dt.quote(store, q),
                 lambda r: f"last {r.data['last']}" + (f", BSE gap {r.data['bse_cross_check']['gap_pct']}%" if r.data.get("bse_cross_check") else "")
                 + f", live={r.data.get('live')}")
            sec = dt.company(store, q)
            pc = price.price_context(real, sec["isin"])
            print(f"     price: {pc.data['label'] + ', close ' + str(pc.data['details']['close']) if pc.ok else pc.error.kind}")
        show(report, "announcements_HDFCBANK", lambda: dt.announcements(store, "HDFCBANK", days=30),
             lambda r: f"{len(r.data['announcements'])} merged, only on NSE {r.data['only_on_nse']}, only on BSE {r.data['only_on_bse']}")
    show(report, "all_indices", lambda: nse_quote.all_indices(),
         lambda r: f"{len(r.data['indices'])} indices at {r.data['timestamp']}, breadth {r.data['breadth']}, live={r.data['live']}")
    for sym in ("NIFTY", "BANKNIFTY"):
        show(report, f"option_chain_{sym}", lambda sym=sym: nse_quote.option_chain(sym),
             lambda r: f"expiry {r.data['expiry']}, {len(r.data['rows'])} rows, underlying {r.data['underlying']} at {r.data['timestamp']}, live={r.data['live']}")


def group_batch3(report: list, store: Store) -> None:
    """Board meetings (NSE + BSE), BSE corporate actions, NSDL FPI, and the historical-deals cap: one day asked of the
    historical endpoint must match the full count NSE states in today's large-deals snapshot."""
    from datetime import date, timedelta
    from drishti.providers import nsdl
    from drishti.store import datasets as ds
    security_master.build_security_master(store)
    r = show(report, "board_meetings", lambda: nse_corp.board_meetings(),
             lambda r: f"{len(r.data)} meetings {min(x['meeting_date'] for x in r.data)}..{max(x['meeting_date'] for x in r.data)}, newest intimation {r.as_of}")
    if r.ok:
        print(f"     kinds: {dict(Counter(x['kind'] for x in r.data))}; with ISIN {sum(1 for x in r.data if x['isin'])}/{len(r.data)}")
        ds.persist_board_meetings(store, r.data)
    b = show(report, "bse_result_calendar", lambda: bse.result_calendar(),
             lambda r: f"{len(r.data)} BSE results meetings {min(x['meeting_date'] for x in r.data)}..{max(x['meeting_date'] for x in r.data)}")
    if b.ok:
        ds.persist_board_meetings(store, b.data)
        mapped = store.query("SELECT COUNT(*) AS n, SUM(isin IS NOT NULL) AS m FROM board_meeting WHERE exchange='BSE'")[0]
        both = len(ds.upcoming_meetings(store, start=str(today_ist()), end=str(today_ist() + timedelta(days=30))))
        print(f"     BSE rows mapped to an ISIN: {mapped['m']}/{mapped['n']}; one row per company and date over 30 days: {both}")
    a = show(report, "bse_corp_actions", lambda: bse.corporate_actions(), lambda r: f"{len(r.data)} BSE actions, kinds {dict(Counter(x['kind'] for x in r.data))}")
    if a.ok:
        nse_ca = nse_corp.corporate_actions()
        if nse_ca.ok:
            ds.persist_corp_actions(store, nse_ca.data)
        n = ds.persist_bse_corp_actions(store, a.data)
        print(f"     BSE actions stored after skipping ones NSE already lists: {n} of {len(a.data)}")
    show(report, "nsdl_fpi", lambda: nsdl.fpi_daily(),
         lambda r: f"{r.as_of}: equity net Rs {r.data['equity_net_cr']} cr (exchange {r.data['equity_exchange_net_cr']}), total {r.data['total']['net']} cr, {len(r.data['derivatives'])} derivative rows")
    snap = nse_corp.large_deals()
    if not snap.ok:
        print(f"     large-deals snapshot failed ({snap.error.kind}); cannot check the historical cap")
        return
    day = date.fromisoformat(snap.data["as_on"])
    for kind_ in ("bulk", "block", "short"):
        want = len(snap.data[kind_])
        if not want:
            print(f"     {kind_}: snapshot is empty, nothing to compare")
            continue
        day = date.fromisoformat(Counter(x["date"] for x in snap.data[kind_]).most_common(1)[0][0])   # short deals run a day behind
        h = nse_corp.historical_deals(kind_, day, day)
        got = len(h.data["deals"]) if h.data else None
        same_day = [x for x in snap.data[kind_] if x["date"] == str(day)]
        verdict = "OK  " if h.ok and got == len(same_day) else "FAIL"
        report.append({"name": f"hist_{kind_}_vs_snapshot", "ok": verdict.strip() == "OK", "detail": f"{got} vs {len(same_day)}"})
        print(f"{verdict} hist_{kind_}_one_day           historical {got} vs snapshot {len(same_day)} on {day} (snapshot total {want})"
              f"{'' if h.ok else '  ' + str(h.error.kind) + ': ' + h.error.detail[:90]}")
        if h.data and got != len(same_day):
            key = lambda x: (x["symbol"], x["qty"], x.get("client") or "-", x.get("side") or "-")  # noqa: E731
            a, b = Counter(map(key, h.data["deals"])), Counter(map(key, same_day))
            print(f"     only in history: {list((a - b).elements())[:5]}; only in snapshot: {list((b - a).elements())[:5]}")
    day = date.fromisoformat(snap.data["as_on"])
    show(report, "hist_block_30d", lambda: nse_corp.historical_deals("block", day - timedelta(days=29), day),
         lambda r: f"{len(r.data['deals'])} block deals in 30 days using {r.data['requests']} requests, capped days {r.data['capped_days']}")


def group_market(report: list, store: Store) -> None:
    """Run in market hours (Mon-Fri 10:00-14:00 IST). Proves the live paths are live: each quote is taken twice 45 s
    apart and must move (volume up), the option chain and index snapshot must be minutes old, and NSE vs BSE must agree."""
    from datetime import datetime, timedelta
    from drishti import data_tools as dt
    from drishti.core.canary import IST

    def check(name, ok_, detail):
        line(ok_, name, 0.0, detail)
        report.append({"name": name, "ok": ok_, "detail": detail})

    now = datetime.now(IST)
    if not nse_quote.market_open(now):
        print(f"     market is closed now ({now:%a %H:%M} IST): run this Mon-Fri between 10:00 and 14:00")
        return
    m = show(report, "market_status", lambda: nse_corp.market_status(), lambda r: f"CM {r.data['capital_market']}, Gift Nifty "
             f"{r.data['gift_nifty']['last']} at {r.data['gift_nifty']['timestamp']}")
    if m.ok:
        check("market_status_open", m.data["capital_market"] == "Open", f"capital market says {m.data['capital_market']!r}")
        gts = m.data["gift_nifty"]["timestamp"]
        check("gift_nifty_fresh", bool(gts) and now.replace(tzinfo=None) - datetime.fromisoformat(gts) < timedelta(hours=2), f"last at {gts}")
    security_master.build_security_master(store)
    names = ["RELIANCE", "HDFCBANK", "M&M", "542034"] + _pick_dynamic(store)[:1]
    first = {q: dt.quote(store, q) for q in names}
    time.sleep(45)
    for q in names:
        a, b = first[q], dt.quote(store, q)
        if not (a.ok and b.ok):
            check(f"live_quote_{q}", False, f"{(a.error or b.error).kind}: {(a.error or b.error).detail[:100]}")
            continue
        moved = (b.data.get("volume") or 0) > (a.data.get("volume") or 0) or b.data["last"] != a.data["last"]
        gap = (b.data.get("bse_cross_check") or {}).get("gap_pct")
        check(f"live_quote_{q}", moved and b.data.get("live") is not False,
              f"last {a.data['last']} -> {b.data['last']}, volume {a.data.get('volume')} -> {b.data.get('volume')}, live={b.data.get('live')}"
              + (f", BSE gap {gap}%" if gap is not None else ""))
        if gap is not None:
            check(f"nse_bse_agree_{q}", abs(gap) < 1.0, f"gap {gap}%")
    for sym in ("NIFTY", "BANKNIFTY"):
        r = nse_quote.option_chain(sym)
        if not r.ok:
            check(f"option_chain_live_{sym}", False, f"{r.error.kind}: {r.error.detail[:100]}")
            continue
        age = now.replace(tzinfo=None) - datetime.fromisoformat(r.data["timestamp"])
        check(f"option_chain_live_{sym}", age < timedelta(minutes=10) and r.data["live"], f"expiry {r.data['expiry']}, {len(r.data['rows'])} rows, "
              f"timestamp {r.data['timestamp']} ({age.total_seconds() / 60:.0f} min old)")
    r = nse_quote.all_indices()
    if r.ok:
        age = now.replace(tzinfo=None) - datetime.fromisoformat(r.data["timestamp"])
        check("all_indices_live", age < timedelta(minutes=10), f"{len(r.data['indices'])} indices at {r.data['timestamp']}, breadth {r.data['breadth']}")
    i = show(report, "ipos_weekday", lambda: nse_corp.ipos(), lambda r: f"current {len(r.data['current'])}, upcoming {len(r.data['upcoming'])}, "
             f"closed in 30d {len(r.data['recent'])}")
    if i.ok:
        for x in (i.data["current"] + i.data["upcoming"])[:3]:
            print(f"     {x['status']}: {x['company']} {x['open']}..{x['close']} {x['price_range']} {x['security_type']}")


def group_us(report: list, store: Store) -> None:
    """US layer end to end: Yahoo quote and bars, SEC fundamentals (now and as of a past date), filings, a 13F."""
    from drishti import data_tools as dt
    for t in ("AAPL", "MSFT", "BRK-B"):
        show(report, f"us_quote_{t}", lambda t=t: dt.us_quote(store, t),
             lambda r: f"{r.data['name']} {r.data['last']} {r.data['currency']} at {r.data['market_time']} ({r.data['exchange']})")
    for t in ("AAPL", "MSFT", "JPM"):
        show(report, f"us_fundamentals_{t}", lambda t=t: dt.us_fundamentals(store, t), lambda r: f"{r.data['name']}: {r.data['quality']}")
    show(report, "us_fundamentals_AAPL_asof_2024", lambda: dt.us_fundamentals(store, "AAPL", as_of="2024-01-01"),
         lambda r: f"latest annual period known on 1 Jan 2024: {r.data['quality']['latest_annual_period']}")
    show(report, "us_filings_AAPL", lambda: dt.us_filings(store, "AAPL"),
         lambda r: f"{len(r.data['filings'])} filings, newest {[(f['form'], f['filed']) for f in r.data['filings'][:4]]}")
    show(report, "us_13f_BRK", lambda: dt.us_13f(store, "1067983"),
         lambda r: f"{r.data['filer']} {r.data['period']}: {len(r.data['holdings'])} holdings, ${r.data['total_usd'] / 1e9:.1f} bn; top "
                   f"{sorted(r.data['holdings'], key=lambda h: -h['value_usd'])[0]['issuer']}")


def group_batch5(report: list, store: Store) -> None:
    """India macro (MoSPI), delivery %, BSE scrip list. IIP and RBI reserves are expected to report STALE/EMPTY today: that is the
    feed's truth and counts as a pass when the typed error names the newest month."""
    from drishti.providers import bse, mospi, nse_archive
    from drishti.providers import _util as pu
    for k in ("cpi", "wpi"):
        show(report, f"mospi_{k}", lambda k=k: getattr(mospi, k)(),
             lambda r: f"{r.data['latest']} as of {r.as_of}")
    for k in ("iip", "rbi_reserves"):
        r, secs = timed(lambda k=k: getattr(mospi, k)())
        stale = (not r.ok) and getattr(r.error.kind, "value", r.error.kind) == "STALE" and "20" in r.error.detail
        line(r.ok or stale, f"mospi_{k}", secs, (f"{r.data['latest']} as of {r.as_of}" if r.ok else f"{r.error.kind}: {r.error.detail[:150]} (expected while the feed is behind)"))
        report.append({"name": f"mospi_{k}", "ok": r.ok or stale, "as_of": r.as_of, "secs": round(secs, 2),
                       "error": None if r.ok else {"kind": str(r.error.kind), "detail": r.error.detail}})
    show(report, "delivery_latest", lambda: pu.latest(lambda d: nse_archive.delivery(d), today_ist(), sessions=None),
         lambda r: f"{len(r.data)} rows for {r.as_of}; RELIANCE {[x['deliv_per'] for x in r.data if x['symbol'] == 'RELIANCE']}")
    show(report, "bse_scrip_list", lambda: bse.scrip_list(), lambda r: f"{len(r.data)} active scrips")


def group_adjust(report: list, store: Store) -> None:
    """Split / bonus events rebuilt from YOUR stored prices and corporate actions (read-only except that it rewrites the derived
    price_adjustment / isin_succession tables). Passes when most stored actions are confirmed by the price step."""
    from drishti.config import load_settings
    from drishti.store import datasets as ds
    with Store(load_settings().db_path) as real:
        t0 = time.time()
        r = ds.rebuild_adjustments(real)
        total = r["events"] + len(r["unverified"]) + len(r["unparsed"])
        share = r["events"] / total if total else 0.0
        ok = r["events"] > 0 and share >= 0.6
        line(ok, "price_adjustments", time.time() - t0,
             f"{r['events']} verified of {total} split/bonus actions with a stored ex-date session ({share:.0%}); {len(r['unverified'])} not confirmed, "
             f"{len(r['unparsed'])} unreadable; {r['successions']} ISIN successions")
        report.append({"name": "price_adjustments", "ok": ok, "events": r["events"], "unverified": len(r["unverified"]), "unparsed": len(r["unparsed"]),
                       "successions": r["successions"]})
        ev = real.query("SELECT * FROM price_adjustment WHERE verified = 1 ORDER BY ex_date DESC LIMIT 12")
        for e in ev:
            print(f"     ok   {e['ex_date']} {e['symbol']:<12s} {e['kind']:<6s} x{e['factor']:<8g} observed {e['observed_ratio']:<8g} "
                  f"{e['isin']}{' -> ' + e['new_isin'] if e['new_isin'] else ''}")
        for x in sorted(r["unverified"], key=lambda x: x["ex_date"], reverse=True)[:12]:
            print(f"     NOT CONFIRMED {x['ex_date']} {x['symbol']:<12s} {x['kind']:<6s} {x['reason']}")
        for x in r["unparsed"][:8]:
            print(f"     UNREADABLE {x['ex_date']} {x['symbol']:<12s} {x['subject'][:80]}")
        for sym in ("KMEW", "GNRL", "JSWDULUX", "AZADIND", "CONCORDBIO"):      # the cases seen by hand on 8 Nov 2026
            row = real.query("SELECT verified, factor, observed_ratio FROM price_adjustment WHERE symbol = ? ORDER BY ex_date DESC LIMIT 1", (sym,))
            print(f"     known case {sym:<11s} " + (f"verified={row[0]['verified']} factor={row[0]['factor']} observed={row[0]['observed_ratio']}" if row else "no event row"))


def group_samples(report: list, store: Store) -> None:
    """Save raw pages needed to pin parsers with real fixtures: Screener pages for a bank, an NBFC, an '&' symbol and an
    SME (half-yearly) filer, plus today's marketStatus."""
    from drishti.providers import nse_live
    dst = OUT.parent / "validate_failures"
    dst.mkdir(parents=True, exist_ok=True)
    got = nse_live.api_get("marketStatus")
    (dst / "marketStatus_today.json").write_bytes(got.body)
    print(f"     saved marketStatus ({len(got.body)} bytes); giftnifty present: {bool(json.loads(got.body).get('giftnifty'))}")
    security_master.build_security_master(store)
    slugs = ["HDFCBANK", "BAJFINANCE", "M&M"] + [x for x in _pick_dynamic(store)[:1]]
    for slug in slugs:
        for cons in (True, False):
            try:
                body = screener._fetch(slug, cons).body
                f = dst / f"screener_{slug.replace('&', '_')}_{'cons' if cons else 'sa'}.html"
                f.write_bytes(body)
                print(f"     saved {f.name} ({len(body):,} bytes)")
            except DrishtiError as e:
                print(f"     {slug} {'consolidated' if cons else 'standalone'}: {e.kind} {e.detail[:80]}")


def group_shp(report: list, store: Store, sample: int) -> None:
    r = show(report, "shp_master", lambda: nse_shp.shp_master(store=store),
             lambda r: f"{len(r.data)} companies, newest covered quarter {r.as_of}")
    if not r.ok:
        return
    quarter = r.as_of
    current = [x for x in r.data if x["as_on"] >= quarter]
    step = max(1, len(current) // sample)
    pick = current[::step][:sample]
    moved = [x for x in r.data if x["promoter_change_pp"] is not None]
    clean = sorted((x for x in moved if not x["change_flags"]), key=lambda x: x["promoter_change_pp"])
    flagged = sorted((x for x in moved if x["change_flags"]), key=lambda x: x["promoter_change_pp"])
    print(f"     promoter QoQ: {len(clean)} unflagged changes, {len(flagged)} flagged as unreliable")
    for x in clean[:3] + clean[-2:]:
        print(f"       {x['promoter_change_pp']:+.2f}pp {x['symbol']}")
    for x in flagged[:4]:
        print(f"       FLAGGED {x['promoter_change_pp']:+.2f}pp {x['symbol']} {x['change_flags']}: {(x.get('remarks') or '-')[:70]}")
    print(f"     sampling {len(pick)} of {len(current)} filings for the covered quarter (stride {step})")
    parsed, failed, t0 = [], [], time.time()
    for i, row in enumerate(pick, 1):
        fr = nse_shp.shp_filing(row["xbrl_url"])
        if fr.ok:
            parsed.append(fr.data)
        else:
            failed.append({"symbol": row["symbol"], "url": row["xbrl_url"], "kind": str(fr.error.kind), "detail": fr.error.detail[:200],
                           "saved_as": save_failure("SHP", row["symbol"], row["xbrl_url"])})
        if i % 25 == 0:
            print(f"     ...{i}/{len(pick)} ({time.time() - t0:.0f}s)")
    line(not failed, "shp_filings", time.time() - t0, f"{len(parsed)} parsed, {len(failed)} failed")
    for k, n in Counter((f["kind"], f["detail"][:90]) for f in failed).most_common(6):
        print(f"     FAILED x{n}: {k[0]} {k[1]}")
    for f in failed[:5]:
        print(f"     saved failing file: {f['symbol']} -> {f['saved_as']}")
    summ = summarise_shp(parsed)
    print("     " + json.dumps({k: v for k, v in summ.items() if k != "worst_encumbered_pct_of_promoter"}, default=str))
    print(f"     most encumbered promoters: {summ['worst_encumbered_pct_of_promoter']}")
    report.append({"name": "shp_filings", "ok": not failed, "summary": summ, "failed": failed})


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--only", action="append", choices=GROUPS)
    ap.add_argument("--shp-sample", type=int, default=120)
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(f"Drishti live validation | {today_ist()} | groups {','.join(args.only or GROUPS)}\n")
    report: list = []
    with Store() as store:
        for g in args.only or GROUPS:
            print(f"== {g}")
            {"archive": lambda: group_archive(report, store), "bse": lambda: group_bse(report, store),
             "pit": lambda: group_pit(report, store), "macro": lambda: group_macro(report, store), "master": lambda: group_master(report, store), "corp": lambda: group_corp(report, store), "quotes": lambda: group_quotes(report, store), "samples": lambda: group_samples(report, store), "batch3": lambda: group_batch3(report, store), "adjust": lambda: group_adjust(report, store), "batch5": lambda: group_batch5(report, store), "market": lambda: group_market(report, store), "us": lambda: group_us(report, store), "shp": lambda: group_shp(report, store, args.shp_sample)}[g]()
            print()
        bad = [r["name"] for r in report if not r["ok"]]
        print(f"{len(report) - len(bad)} ok, {len(bad)} failed" + (f": {', '.join(bad)}" if bad else ""))
        print("data_health:", json.dumps(store.data_health()["datasets"], default=str)[:600])
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"date": str(today_ist()), "report": report}, indent=1, default=str), encoding="utf-8")
    print(f"Report: {OUT}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())

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
from drishti.providers import bse, nse_archive, nse_pit, nse_shp
from drishti.providers.profiles import NSE_ARCHIVE
from drishti.store import Store

OUT = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "_raw" / "validate_live.json"
GROUPS = ("archive", "bse", "pit", "shp")


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
    bad_isin = [(e["symbol"], e["isin"]) for e in ev if not e["isin_valid"]]
    if bad_isin:
        print(f"     invalid ISIN in filings: {sorted(set(bad_isin))[:5]}")
    print(f"     holding_reconciles False: {sum(1 for e in ev if e['holding_reconciles'] is False)}; invalid ISIN: {sum(1 for e in ev if not e['isin_valid'])}; "
          f"form versions: {dict(Counter(e['form_version'] for e in ev))}")
    for f in d["failed"][:8]:
        print(f"     FAILED filing {f['app_id']} {f['symbol']}: {f['kind']} {f['detail'][:100]}")
    report[-1]["failed_filings"] = d["failed"]


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
             "pit": lambda: group_pit(report, store), "shp": lambda: group_shp(report, store, args.shp_sample)}[g]()
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

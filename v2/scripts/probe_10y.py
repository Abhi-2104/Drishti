"""Read-only probe before a 10-year price backfill (run: uv run python scripts/probe_10y.py).

Part A (database, no network): how far back split/bonus history goes, and whether the price days where the exchange's
own previous close disagrees with our stored previous close (a split/bonus day) are covered by a verified adjustment.
An uncovered one looks like a crash or a jump in the history test.
Part B (network, about 25 requests): can 2016-2021 files and corporate actions be read at all?
Writes eval/probe_10y.txt as well as printing."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

from drishti.config import load_settings
from drishti.providers import bse, nse_archive, nse_corp
from drishti.store import Store

OUT: list[str] = []


def say(s: str = "") -> None:
    print(s, flush=True)
    OUT.append(s)


def part_a(st: Store) -> None:
    say("== A. database")
    r = st.query("SELECT kind, COUNT(*) n, MIN(ex_date) lo, MAX(ex_date) hi FROM corp_action WHERE kind IN ('split','bonus') GROUP BY kind")
    for x in r:
        say(f"   corp_action {x['kind']:6s} {x['n']:5d} rows  {x['lo']} .. {x['hi']}")
    r = st.query("SELECT verified, COUNT(*) n, MIN(ex_date) lo FROM price_adjustment GROUP BY verified")
    for x in r:
        say(f"   price_adjustment verified={x['verified']}  {x['n']:5d}  earliest {x['lo']}")
    say("   scanning for split/bonus-like days (exchange prev_close vs our stored previous close, off by >20%) ...")
    rows = st.query("""
        WITH p AS (
          SELECT trade_date, symbol, isin, close, prev_close,
                 LAG(close) OVER (PARTITION BY isin ORDER BY trade_date) AS our_prev
          FROM price_eod WHERE exchange='NSE' AND series='EQ')
        SELECT substr(trade_date,1,4) yr, trade_date, symbol, prev_close, our_prev
        FROM p WHERE our_prev > 0 AND prev_close > 0 AND (prev_close / our_prev < 0.8 OR prev_close / our_prev > 1.25)""")
    adj = {(x["symbol"], x["ex_date"]) for x in st.query("SELECT symbol, ex_date FROM price_adjustment WHERE verified=1 AND exchange='NSE'")}
    by_year: dict[str, list[int]] = {}
    missing = []
    for x in rows:
        hit = (x["symbol"], x["trade_date"]) in adj
        c = by_year.setdefault(x["yr"], [0, 0])
        c[0] += 1
        c[1] += hit
        if not hit:
            missing.append(x)
    say("   year  event-days  covered-by-verified-adjustment")
    for y in sorted(by_year):
        say(f"   {y}   {by_year[y][0]:6d}      {by_year[y][1]:6d}")
    say(f"   uncovered: {len(missing)}; first 15:")
    for x in missing[:15]:
        say(f"     {x['trade_date']} {x['symbol']:12s} exch prev {x['prev_close']}  our prev {x['our_prev']}")


def part_b() -> None:
    say("== B. network samples (old formats)")
    for d in (date(2016, 3, 15), date(2017, 6, 15), date(2018, 10, 15), date(2019, 12, 16), date(2020, 3, 24), date(2021, 2, 15)):
        for name, fn in (("nse_cm_legacy", nse_archive.cm_bhavcopy_legacy), ("bse_legacy", bse.bhavcopy_legacy),
                         ("index_closes", nse_archive.index_closes)):
            try:
                r = fn(d, store=None)
                if r.ok:
                    rows = r.data if isinstance(r.data, list) else []
                    isin = sum(1 for x in rows if isinstance(x, dict) and x.get("isin"))
                    say(f"   {d} {name:14s} OK   {len(rows):5d} rows, {isin} with ISIN")
                else:
                    say(f"   {d} {name:14s} FAIL {r.error.kind}: {r.error.detail[:90]}")
            except Exception as e:  # noqa: BLE001 - a probe reports everything
                say(f"   {d} {name:14s} FAIL {type(e).__name__}: {str(e)[:90]}")
    say("== C. NSE corporate actions, old 90-day windows")
    for end in (date(2016, 9, 30), date(2018, 6, 30), date(2020, 6, 30)):
        try:
            r = nse_corp.corporate_actions(back_days=89, ahead_days=0, today=end)
            if r.ok:
                sb = [x for x in r.data if any(w in str(x.get("subject", "")).lower() for w in ("split", "bonus"))]
                say(f"   window ending {end}: OK {len(r.data)} actions, {len(sb)} split/bonus, ex-dates "
                    f"{min((x.get('ex_date') or '') for x in r.data)} .. {max((x.get('ex_date') or '') for x in r.data)}")
            else:
                say(f"   window ending {end}: FAIL {r.error.kind}: {r.error.detail[:90]}")
        except Exception as e:  # noqa: BLE001
            say(f"   window ending {end}: FAIL {type(e).__name__}: {str(e)[:90]}")


def main() -> int:
    cfg = load_settings()
    with Store(cfg.db_path) as st:
        part_a(st)
    part_b()
    Path("eval").mkdir(exist_ok=True)
    Path("eval/probe_10y.txt").write_text("\n".join(OUT) + "\n", encoding="utf-8")
    say("wrote eval/probe_10y.txt")
    return 0


if __name__ == "__main__":
    sys.exit(main())

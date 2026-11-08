"""drishti CLI: run <job> | jobs | health | resolve | init | paths  (worker is a stub)."""
from __future__ import annotations

import argparse
import sys

from .config import ConfigError, load_settings, write_templates


def _todo(what: str) -> int:
    print(f"drishti {what}: not implemented yet", file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="drishti", description="Drishti investor copilot")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("mcp", help="run the stdio MCP server")
    sub.add_parser("worker", help="run the scheduler loop")
    run = sub.add_parser("run", help="run one job by hand")
    run.add_argument("job")
    sub.add_parser("jobs", help="list jobs and their suggested schedule")
    sub.add_parser("health", help="data freshness and the latest result of every job step")
    rv = sub.add_parser("resolve", help="look up a security by ISIN, NSE/BSE symbol or BSE scrip code")
    rv.add_argument("query")
    bf = sub.add_parser("backfill", help="fill history: index_closes | participant_oi | fo_index | macro | insider | shp | all")
    bf.add_argument("target")
    bf.add_argument("--days", type=int, default=None, help="calendar days back (daily targets)")
    bf.add_argument("--budget-min", type=float, default=60.0, help="stop cleanly after this many minutes; rerun to resume")
    sg = sub.add_parser("signal", help="compute one signal now (or on --date), or --replay N weekdays of history")
    sg.add_argument("name", help="e.g. macro_regime, participant_oi, sector_rotation, insider_cluster, pledge; 'list' to list")
    sg.add_argument("--date", default=None, help="as-of date YYYY-MM-DD (default: today)")
    sg.add_argument("--query", default=None, help="company for single-name signals: ISIN, symbol or BSE scrip code")
    sg.add_argument("--replay", type=int, default=None, help="recompute for each of the last N weekdays and print a table")
    sg.add_argument("--summary", action="store_true", help="with --replay: print only the distribution (label counts, score percentiles)")
    sub.add_parser("init", help="create the settings/profile templates (never overwrites)")
    sub.add_parser("paths", help="show where settings, profile and the database live")
    args = ap.parse_args(argv)

    if args.cmd in ("init", "paths"):
        try:
            cfg = load_settings()
            if args.cmd == "init":
                made = write_templates(cfg)
                for path in made:
                    print(f"created {path}")
                if not made:
                    print("nothing to do: settings and profile already exist")
            print(f"settings : {cfg.settings_path}")
            print(f"profile  : {cfg.profile_path}")
            print(f"database : {cfg.db_path}")
        except ConfigError as e:
            print(f"drishti {args.cmd}: {e}", file=sys.stderr)
            return 2
        return 0
    if args.cmd == "resolve":
        from .resolve import resolve
        from .store import Store
        try:
            cfg = load_settings()
        except ConfigError as e:
            print(f"drishti resolve: {e}", file=sys.stderr)
            return 2
        with Store(cfg.db_path) as st:
            r = resolve(st, args.query)
        if not r.ok:
            print(f"{r.error.kind}: {r.error.detail}", file=sys.stderr)
            return 1
        for m in r.data["matches"]:
            print(f"{m['isin']}  {m['name']}  NSE:{m['nse_symbol'] or '-'}  BSE:{m['bse_symbol'] or '-'} {m['bse_scrip'] or ''}  [{m['instrument_class'] or '?'}]")
        return 3 if r.data["ambiguous"] else 0   # 3 = several companies matched: the caller must choose
    if args.cmd == "mcp":
        try:
            cfg = load_settings()
            from .mcp_server import serve
        except ConfigError as e:
            print(f"drishti mcp: {e}", file=sys.stderr)
            return 2
        except ModuleNotFoundError as e:
            print(f"drishti mcp: the MCP SDK is not installed ({e}); run `uv sync`", file=sys.stderr)
            return 2
        serve(cfg.db_path)    # stdout belongs to the protocol: everything else logs to stderr
        return 0
    if args.cmd in ("backfill", "signal"):
        from .store import Store
        try:
            cfg = load_settings()
        except ConfigError as e:
            print(f"drishti {args.cmd}: {e}", file=sys.stderr)
            return 2
        with Store(cfg.db_path) as st:
            return _backfill(st, args) if args.cmd == "backfill" else _signal(st, args)
    if args.cmd == "jobs":
        from .jobs import JOBS
        for j in JOBS.values():
            print(f"{j.name:<16s} {j.when:<16s} {j.doc}")
            print(f"{'':<16s} steps: {', '.join(s.dataset for s in j.steps)}")
        return 0
    if args.cmd in ("run", "health"):
        from .jobs import JOBS, run_job
        from .store import Store
        if args.cmd == "run" and args.job not in JOBS:
            print(f"drishti run: unknown job {args.job!r}; known: {', '.join(JOBS)}", file=sys.stderr)
            return 2
        try:
            cfg = load_settings()
        except ConfigError as e:
            print(f"drishti {args.cmd}: {e}", file=sys.stderr)
            return 2
        with Store(cfg.db_path) as st:
            if args.cmd == "health":
                return _print_health(st.data_health())
            rep = run_job(st, args.job)
        for s in rep.steps:
            if s.ok:
                print(f"OK   {s.dataset:<16s} {s.rows if s.rows is not None else '-':>6} rows  as of {s.as_of}  ({s.secs:.1f}s)" + (f"\n     note: {s.note}" if s.note else ""))
            else:
                print(f"FAIL {s.dataset:<16s} {s.error_kind}: {s.detail[:160]}  ({s.secs:.1f}s)")
        print(f"{rep.job}: {sum(s.ok for s in rep.steps)}/{len(rep.steps)} steps ok")
        return 0 if rep.ok else 1
    return _todo(args.cmd)


def _backfill(st, args) -> int:
    import logging
    from .jobs import backfill as bf
    logging.getLogger("drishti").setLevel(logging.ERROR)   # holiday 404s are expected here; real failures are in the report below
    targets = list(bf.TARGETS) if args.target == "all" else [args.target]
    bad = [t for t in targets if t not in bf.TARGETS]
    if bad:
        print(f"drishti backfill: unknown target {bad[0]!r}; known: {', '.join(bf.TARGETS)}, all", file=sys.stderr)
        return 2
    worst = 0
    for t in targets:
        kw = {}
        if t in bf.DEFAULT_DAYS:
            kw["days"] = args.days or bf.DEFAULT_DAYS[t]
        if t != "macro":
            kw["budget_s"] = args.budget_min * 60
        print(f"== backfill {t} {kw}", flush=True)
        rep = bf.TARGETS[t](st, progress=lambda m: print(m, flush=True), **kw)
        print(f"   {'OK  ' if rep.ok else 'FAIL'} done {rep.done}, skipped (already stored) {rep.skipped}, holidays {rep.holidays}, "
              f"failed {len(rep.failed)}, rows {rep.rows}, range {rep.oldest} .. {rep.newest}")
        if rep.stopped:
            print(f"   stopped: {rep.stopped}")
        for f in rep.failed[:8]:
            print(f"   failed {f['date']}: {f['kind']} {f['detail'][:110]}")
        worst = max(worst, 0 if rep.ok else 1)
    return worst


def _signal(st, args) -> int:
    import json
    from datetime import date, timedelta
    from . import signals
    from .resolve import resolve
    if args.name == "list":
        print("market:      " + ", ".join(signals.MARKET))
        print("single-name: " + ", ".join(signals.SINGLE_NAME) + "  (need --query)")
        return 0
    on = date.fromisoformat(args.date) if args.date else date.today()
    if args.replay:
        if args.name not in signals.MARKET:
            print("drishti signal: --replay works for market signals only", file=sys.stderr)
            return 2
        rows = signals.replay(st, args.name, on - timedelta(days=int(args.replay * 1.5) + 10), on)[-args.replay:]
        if args.summary:
            s = signals.summarise(rows)
            print(f"{args.name}: {s['ok_days']} of {s['days']} trading days computed")
            for lab, n in s["labels"].items():
                print(f"  {n:>4}  ({n / max(1, s['ok_days']):.0%})  {lab[:90]}")
            if s["score_p50"] is not None:
                print(f"  score min {s['score_min']}  p10 {s['score_p10']}  median {s['score_p50']}  p90 {s['score_p90']}  max {s['score_max']}")
            return 0
        for r in rows:
            score = "" if r["score"] is None else f"{r['score']:>8}"
            print(f"{r['as_of'] or r['date']}  {'ok  ' if r['ok'] else '--  '} {score:>8}  {r['label'][:100]}")
        return 0
    kw = {}
    if args.name in signals.SINGLE_NAME:
        if not args.query:
            print("drishti signal: single-name signals need --query", file=sys.stderr)
            return 2
        rr = resolve(st, args.query)
        if not rr.ok or rr.data["ambiguous"]:
            print(f"drishti signal: cannot resolve {args.query!r}: {rr.error.detail if not rr.ok else 'several companies match'}", file=sys.stderr)
            return 2
        m = rr.data["matches"][0]
        kw = {"isin": m["isin"]} if args.name == "pledge" else {"isin": m["isin"], "symbol": m["nse_symbol"]}
    try:
        r = signals.compute(st, args.name, on, **kw)
    except KeyError:
        print(f"drishti signal: unknown signal {args.name!r}; try `drishti signal list`", file=sys.stderr)
        return 2
    if not r.ok:
        print(f"{r.error.kind}: {r.error.detail}", file=sys.stderr)
        return 1
    s = r.data
    print(f"{s['name']}  as of {s['as_of']}  score {s['score']}  ->  {s['label']}" + ("   [provisional thresholds]" if s["provisional"] else ""))
    for c in s["components"]:
        val = json.dumps(c["value"], default=str) if isinstance(c["value"], (list, dict)) else c["value"]
        contrib = "" if c["contribution"] is None else f" [{c['contribution']:+}]"
        print(f"  - {c['input']}: {val}{contrib}  {c['text'] or c['rule']}")
    for m in s["missing"]:
        print(f"  ! missing: {m}")
    print(f"  note: {s['note']}")
    print(f"  params: {s['params_version']}")
    return 0


def _print_health(h: dict) -> int:
    print(f"status: {h['status']}  (generated {h['generated_at']})")
    for d in h["datasets"]:
        print(f"  data   {d['dataset']:<16s} latest {d['latest_trade_date'] or '-'}  fetched {d['last_fetched_at']}  days stored {d['days_stored']}")
    for j in h["jobs"]:
        flag = "OK  " if j["status"] == "ok" else j["status"].upper()
        print(f"  job    {flag:<6s} {j['job']}.{j['dataset']}  as of {j['trade_date'] or '-'}  {j['error_kind'] or ''} {(j['detail'] or '')[:80]}")
    for k in h["canaries"]:
        print(f"  canary {'ok' if k['ok'] else 'FAIL':<5s} {k['name']}  newest {k['newest'] or '-'}  {k['error_kind'] or ''}")
    if h["failures_24h"]:
        print("  failures in the last 24h: " + ", ".join(f"{f['error_kind']} x{f['n']}" for f in h["failures_24h"]))
    return 0 if h["status"] == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())

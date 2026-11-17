"""drishti CLI: run <job> | jobs | health | resolve | init | paths  (worker is a stub)."""
from __future__ import annotations

import argparse
import sys

from .config import ConfigError, write_templates
from .config import load_settings as _load_settings


def load_settings():
    """Settings, plus this install's signal-settings.toml applied to every signal computed in this process."""
    cfg = _load_settings()
    from .signals.base import use_settings
    use_settings(cfg.signal_settings_path)
    return cfg


def _todo(what: str) -> int:
    print(f"drishti {what}: not implemented yet", file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="drishti", description="Drishti investor copilot")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("mcp", help="run the stdio MCP server")
    sub.add_parser("worker", help="run the scheduler loop")
    sub.add_parser("selftest", help="exercise the whole chain against your database and print PASS/FAIL per step (read-only)")
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
    dp = sub.add_parser("data", help="look up stored/live data: fundamentals | quote | announcements | actions | ratings | deals | today | calendar | fpi")
    dp.add_argument("what")
    dp.add_argument("--query", default=None, help="company: ISIN, NSE symbol, BSE symbol or scrip code")
    dp.add_argument("--refresh", action="store_true", help="fundamentals: fetch again even if a recent copy is stored")
    dc = sub.add_parser("decisions", help="decision log and the rules-vs-agents test: cases | rules | export | import | list | score | compare | audit | forward | history | history-report | history-coverage")
    dc.add_argument("what")
    dc.add_argument("--gate", default=None)
    dc.add_argument("--start", default=None)
    dc.add_argument("--end", default=None)
    dc.add_argument("--every", type=int, default=10, help="cases: take every Nth stored session")
    dc.add_argument("--n-stocks", type=int, default=10, help="cases: stocks drawn per date")
    dc.add_argument("--seed", type=int, default=11)
    dc.add_argument("--horizon-after", type=int, default=60, help="cases: sessions that must exist after the date so the call can be scored")
    dc.add_argument("--cases", default=None, help="case file (JSON) written by `cases`")
    dc.add_argument("--out", default=None, help="export: folder for evidence bundles")
    dc.add_argument("--no-blind", action="store_true", help="export: keep the stock's name and symbol in the bundles")
    dc.add_argument("--key", default=None, help="import: _key.json written by export")
    dc.add_argument("--answers", default=None, help="import: JSON-lines answers file")
    dc.add_argument("--agent", default=None)
    dc.add_argument("--model", default=None)
    dc.add_argument("--universe", default=None, help="forward: the frozen stock list (default eval/forward_universe.json)")
    dc.add_argument("--universe-size", type=int, default=20, help="forward: stocks drawn when the universe file is first made")
    dc.add_argument("--horizons", default="20,60", help="forward: horizons in sessions, comma-separated")
    dc.add_argument("--as-of", default=None, help="forward: decision date (default: the latest stored session)")
    dc.add_argument("--min-gap", type=int, default=5, help="forward: least sessions between forward dates")
    dc.add_argument("--critiques", default=None, help="audit: critiques JSON-lines file")
    dc.add_argument("--a", default=None, help="compare/audit: first author, agent/model")
    dc.add_argument("--b", default=None, help="compare: second author; audit: the revised author (optional)")
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
            print(f"signals  : {cfg.signal_settings_path}")
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
        serve(cfg.db_path, cfg.signal_settings_path, cfg.profile_path)    # stdout belongs to the protocol: everything else logs to stderr
        return 0
    if args.cmd == "data":
        import json as _json
        from . import data_tools as dt
        from .store import Store
        fns = {"fundamentals": lambda st: dt.fundamentals(st, args.query, refresh=args.refresh), "quote": lambda st: dt.quote(st, args.query),
               "announcements": lambda st: dt.announcements(st, args.query), "actions": lambda st: dt.corporate_actions(st, args.query),
               "ratings": lambda st: dt.credit_ratings(st, args.query), "deals": lambda st: dt.deals(st, args.query),
               "today": lambda st: dt.market_today(st), "calendar": lambda st: dt.calendar(st, args.query),
               "fpi": lambda st: dt.fpi_flows(st)}
        if args.what not in fns or (args.what not in ("today", "calendar", "fpi") and not args.query):
            print(f"drishti data: use one of {', '.join(fns)} (all but 'today', 'calendar', 'fpi' need --query)", file=sys.stderr)
            return 2
        cfg = load_settings()
        with Store(cfg.db_path) as st:
            r = fns[args.what](st)
        out = r.to_dict()
        if args.what == "fundamentals" and r.data:
            out["data"] = {k: v for k, v in r.data.items() if k != "tables"}
            out["data"]["tables"] = {k: {"periods": v["periods"][-4:], "rows": list(v["rows"])} for k, v in r.data.get("tables", {}).items()}
            out["data"]["documents"] = {k: v[:4] for k, v in r.data.get("documents", {}).items()}
        print(_json.dumps(out, indent=1, default=str)[:6000])
        return 0 if r.ok else 1
    if args.cmd == "selftest":
        from . import selftest
        try:
            return selftest.run(load_settings())
        except ConfigError as e:
            print(f"drishti selftest: {e}", file=sys.stderr)
            return 2
    if args.cmd == "decisions":
        from .store import Store
        try:
            cfg = load_settings()
        except ConfigError as e:
            print(f"drishti decisions: {e}", file=sys.stderr)
            return 2
        with Store(cfg.db_path) as st:
            return _decisions(st, args, cfg)
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


def _decisions(st, args, cfg) -> int:
    """`drishti decisions ...`: build cases, run the rule baseline, export blind evidence for agents, take their answers back, score, compare."""
    import json as _json
    from pathlib import Path
    from .decisions import cases as cs, log, score
    w = args.what

    def need(**kw):
        miss = [f"--{k.replace('_', '-')}" for k, v in kw.items() if not v]
        if miss:
            print(f"drishti decisions {w}: needs {', '.join(miss)}", file=sys.stderr)
        return not miss
    if w == "cases":
        if not need(start=args.start, end=args.end, cases=args.cases):
            return 2
        gates = tuple(args.gate.split(",")) if args.gate else ("entry",)
        cases = cs.make_cases(st, start=args.start, end=args.end, every=args.every, n_stocks=args.n_stocks, seed=args.seed, stock_gates=tuple(g for g in gates if g != "macro"),
                              market="macro" in gates or not args.gate, min_horizon_after=args.horizon_after)
        cs.save(cases, Path(args.cases), {"start": args.start, "end": args.end, "every": args.every, "n_stocks": args.n_stocks, "seed": args.seed,
                                          "gates": gates, "horizon_after": args.horizon_after})
        dates = sorted({c["as_of"] for c in cases})
        print(f"{len(cases)} cases on {len(dates)} dates ({dates[0] if dates else '-'} .. {dates[-1] if dates else '-'}) -> {args.cases}")
        return 0 if cases else 1
    if w in ("rules", "export"):
        if not need(cases=args.cases):
            return 2
        cases, _ = cs.load(Path(args.cases))
        if w == "rules":
            print(_json.dumps(cs.run_rules(st, cases), indent=1))
            return 0
        if not need(out=args.out):
            return 2
        prof = None
        try:
            from .profile import load_profile
            prof = load_profile(cfg)
        except Exception:
            prof = None
        r = cs.export_bundles(st, cases, Path(args.out), blind=not args.no_blind, profile=prof)
        print(_json.dumps(r, indent=1))
        return 0 if r["written"] else 1
    if w == "import":
        if not need(key=args.key, answers=args.answers, agent=args.agent, model=args.model):
            return 2
        r = cs.import_answers(st, Path(args.key), Path(args.answers), agent=args.agent, model=args.model)
        print(_json.dumps(r, indent=1))
        return 0 if r["recorded"] and not r["rejected"] else 1
    if w == "list":
        for d in log.current(st, gate=args.gate, agent=args.agent, model=args.model, since=args.start, until=args.end, limit=60):
            print(f"{d['as_of']} {d['gate']:<6s} {d['symbol'] or 'MARKET':<12s} {d['action']:<9s} {d['horizon_days']:>3d}d conf {d['confidence'] if d['confidence'] is not None else '-':<5} {d['agent']}/{d['model']}")
        return 0
    if w in ("score", "compare"):
        rows = score.score_all(st, gate=args.gate, agent=args.agent, model=args.model, since=args.start, until=args.end)
        rows = rows + score.baseline_rows(rows)
        if w == "score":
            for s_ in score.summarise(rows):
                print(_json.dumps(s_))
            return 0
        if not need(a=args.a, b=args.b):
            return 2
        print(_json.dumps(score.compare(rows, args.a, args.b, gate=args.gate), indent=1))
        return 0
    if w == "audit":
        if not need(key=args.key, critiques=args.critiques, a=args.a):
            return 2
        from .decisions import audit as au
        key = _json.loads(Path(args.key).read_text(encoding="utf-8"))
        crit = {r["case_id"]: r for r in (_json.loads(x) for x in Path(args.critiques).read_text(encoding="utf-8").splitlines() if x.strip())}
        print(_json.dumps(au.audit(st, key, crit, args.a, args.b), indent=1))
        return 0
    if w == "forward":
        from .decisions import forward as fw
        hz = tuple(int(x) for x in args.horizons.split(","))
        prof = None
        try:
            from .profile import load_profile
            prof = load_profile(cfg)
        except Exception:
            prof = None
        r = fw.step(st, out_dir=Path(args.out or "eval/forward"), universe_path=Path(args.universe or "eval/forward_universe.json"), as_of=args.as_of,
                    horizons=hz, size=args.universe_size, seed=args.seed, min_gap=args.min_gap, profile=prof)
        print(_json.dumps(r, indent=1))
        return 0 if r["done"] else 1
    if w == "history-report":
        from .decisions import history as hs
        print(_json.dumps(hs.report(st, since=args.start, until=args.end), indent=1))
        return 0
    if w == "history-coverage":
        from .decisions import history as hs
        print(_json.dumps(hs.coverage(st), indent=1))
        return 0
    if w == "history":
        from .decisions import history as hs
        r = hs.run(st, out=Path(args.cases or "eval/history/cases.json"), start=args.start, end=args.end, every=args.every, n_stocks=args.n_stocks, seed=args.seed)
        print(_json.dumps(r, indent=1))
        return 0 if r["done"] else 1
    print(f"drishti decisions: unknown action {w!r}; one of cases rules export import list score compare audit forward history history-report history-coverage", file=sys.stderr)
    return 2


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
        kw = {"isin": m["isin"]} if args.name in ("pledge", "price_context") else {"isin": m["isin"], "symbol": m["nse_symbol"]}
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

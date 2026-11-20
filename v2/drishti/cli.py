"""drishti CLI: catchup | run <job> | jobs | health | holdings | decisions | resolve | init | paths  (worker is a stub: use catchup)."""
from __future__ import annotations

import argparse
import os
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
    for name in ("stdout", "stderr"):        # started by pythonw (no console): there is no stream, and print() would crash
        if getattr(sys, name) is None:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))
    ap = argparse.ArgumentParser(prog="drishti", description="Drishti investor copilot")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("mcp", help="run the stdio MCP server")
    sub.add_parser("worker", help="run the scheduler loop")
    sub.add_parser("selftest", help="exercise the whole chain against your database and print PASS/FAIL per step (read-only)")
    run = sub.add_parser("run", help="run one job by hand")
    run.add_argument("job")
    sub.add_parser("jobs", help="list jobs and their suggested schedule")
    cu = sub.add_parser("catchup", help="what Task Scheduler runs: every slot that is due and not yet done (morning, evening, weekly), then the gap fill")
    cu.add_argument("--plan", action="store_true", help="show what is due and what would run; change nothing")
    cu.add_argument("--slot", default=None, help="run one slot now whether or not it is due: morning | evening | weekly")
    cu.add_argument("--budget-min", type=float, default=100.0, help="start no new job after this many minutes; unfinished slots stay due")
    cu.add_argument("--private-dir", default=None, help="folder with holdings.csv and weekly packs (default: $DRISHTI_PRIVATE, ../private, ./private)")
    cu.add_argument("--eval-dir", default="eval", help="folder for the forward log (default: eval)")
    cu.add_argument("--log", action="store_true", help="write the output to the log file (logs/catchup-<date>.log next to the database) instead of the terminal; "
                    "what the scheduled task does, since it runs without a console")
    hd = sub.add_parser("holdings", help="holdings in the database: import <holdings.csv> | txns <transactions.csv> | summary")
    hd.add_argument("what")
    hd.add_argument("file", nargs="?", default=None)
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
    dc = sub.add_parser("decisions", help="decision log and the rules-vs-agents test: cases | rules | export | import | list | score | compare | audit | forward | history | history-report | history-breakdown | history-coverage | signal-study | portfolio-sim | holdout | candidates | pack")
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
    dc.add_argument("--masked", action="store_true", help="export: also hide the date (offsets, prices rescaled, market levels and month names dropped)")
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
    dc.add_argument("--max-positions", type=int, default=5, help="portfolio-sim: most stocks held per sleeve")
    dc.add_argument("--top", type=int, default=15, help="candidates / pack: how many new-stock candidates")
    dc.add_argument("--private-dir", default=None, help="pack: folder with weekly packs (default: $DRISHTI_PRIVATE, ../private, ./private)")
    dc.add_argument("--force", action="store_true", help="pack: rebuild even if this week's pack exists")
    evp = sub.add_parser("evidence", help="evidence layer checks: audit (re-prove every stored quote) | sample (claims to check by eye) | summary (review score)")
    evp.add_argument("what")
    evp.add_argument("--n", type=int, default=20)
    evp.add_argument("--seed", type=int, default=7)
    sub.add_parser("storage", help="how big the database is and which tables take the space")
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
            print(f"logs     : {cfg.db_path.parent / 'logs'}")
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
    if args.cmd in ("evidence", "storage"):
        import json as _json
        from .store import Store
        try:
            cfg = load_settings()
        except ConfigError as e:
            print(f"drishti {args.cmd}: {e}", file=sys.stderr)
            return 2
        with Store(cfg.db_path) as st:
            if args.cmd == "storage":
                from . import storage
                print(_json.dumps(storage.report(st), indent=1))
                return 0
            from . import evidence as ev
            fns = {"audit": lambda: ev.audit(st), "sample": lambda: ev.sample_claims(st, n=args.n, seed=args.seed), "summary": lambda: ev.review_summary(st)}
            if args.what not in fns:
                print(f"drishti evidence: use one of {', '.join(fns)}", file=sys.stderr)
                return 2
            r = fns[args.what]()
            print(_json.dumps(r.to_dict(), indent=1, default=str))
            return 0 if r.ok and not (args.what == "audit" and r.data["broken"]) else 1
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
    if args.cmd in ("catchup", "holdings"):
        from .store import Store
        try:
            cfg = load_settings()
        except ConfigError as e:
            print(f"drishti {args.cmd}: {e}", file=sys.stderr)
            return 2
        with Store(cfg.db_path) as st:
            return _catchup(st, args, cfg) if args.cmd == "catchup" else _holdings(st, args)
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
        r = cs.export_bundles(st, cases, Path(args.out), blind=not args.no_blind, masked=args.masked, profile=prof)
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
    if w == "history-breakdown":
        from .decisions import breakdown as bd
        print(_json.dumps(bd.run(st, since=args.start, until=args.end), indent=1))
        return 0
    if w == "history-coverage":
        from .decisions import history as hs
        print(_json.dumps(hs.coverage(st), indent=1))
        return 0
    if w == "signal-study":
        from .decisions import study
        out = Path(args.out or "eval/study")
        r = study.run(st, cases_path=Path(args.cases or "eval/history/cases.json"), out_dir=out, since=args.start, until=args.end,
                      progress=study._stderr)
        summary = {k: r[k] for k in ("rows", "cases", "dates", "first", "last", "not_scored", "readings_tested", "expected_false_stable_by_chance", "stable", "written_to")
                   if k in r}
        print(_json.dumps(summary if "error" not in r else r, indent=1))
        return 0 if "error" not in r else 1
    if w == "portfolio-sim":
        from .decisions import portfolio, study
        out = Path(args.out or "eval/study")
        rows_path = out / "rows.jsonl"
        if not rows_path.exists():
            print(f"drishti decisions portfolio-sim: {rows_path} not found; run `drishti decisions signal-study` first", file=sys.stderr)
            return 2
        rows = [r for r in study.load_rows(rows_path) if (not args.start or r["as_of"] >= args.start) and (not args.end or r["as_of"] <= args.end)]
        r = portfolio.run(rows, every=args.every, max_positions=args.max_positions)
        (out / "portfolio.json").write_text(_json.dumps(r, indent=1), encoding="utf-8")
        print(_json.dumps(r, indent=1))
        return 0
    if w == "holdout":
        from .decisions import holdout
        out = Path(args.out or "eval/study")
        rows_path = out / "rows.jsonl"
        if not rows_path.exists():
            print(f"drishti decisions holdout: {rows_path} not found; run `drishti decisions signal-study` first", file=sys.stderr)
            return 2
        try:
            r, target = holdout.execute(rows_path, out, start=args.start, end=args.end, max_positions=args.max_positions, every=args.every)
        except FileExistsError as e:
            print(f"drishti decisions holdout: {e}", file=sys.stderr)
            return 2
        print(_json.dumps(r, indent=1))
        print(f"written to {target}", file=sys.stderr)
        return 0
    if w == "candidates":
        from .decisions import candidates as cd
        r = cd.screen(st, args.as_of, top_n=args.top)
        if not r.ok:
            print(f"{r.error.kind}: {r.error.detail}", file=sys.stderr)
            return 1
        d = r.data
        print(f"{d['status']} candidates as of {d['as_of']} ({d['params_version']}): {d['universe']} liquid, {d['equities']} equities, "
              f"{d['passed_readings']} pass both readings, {d['after_red_flags']} after red flags; top {len(d['candidates'])}")
        for c in d["candidates"]:
            print(f"{c['rank']:>3}  {c['symbol'] or '-':<12s} {(c['name'] or '')[:34]:<34s} vs Nifty 3m {c['rs_3m']:+6.1f}%  {c['gap_to_52w_high_pct']:+5.1f}% from 52w high"
                  + (f"   unknown: {', '.join(c['unknown'])}" if c["unknown"] else ""))
        print("dropped: " + "; ".join(f"{k} {v}" for k, v in d["dropped"].items()))
        print(d["note"])
        return 0
    if w == "pack":
        from . import weekly
        prof = None
        try:
            from .profile import load_profile
            prof = load_profile(cfg)
        except Exception:
            prof = None
        r = weekly.build_pack(st, weekly.private_dir(args.private_dir), as_of=args.as_of, force=args.force, profile=prof, top_n=args.top)
        if not r.ok:
            print(f"{r.error.kind}: {r.error.detail}", file=sys.stderr)
            return 1
        print(_json.dumps(r.data, indent=1, default=str))
        return 0
    if w == "history":
        from .decisions import history as hs
        r = hs.run(st, out=Path(args.cases or "eval/history/cases.json"), start=args.start, end=args.end, every=args.every, n_stocks=args.n_stocks, seed=args.seed)
        print(_json.dumps(r, indent=1))
        return 0 if r["done"] else 1
    print(f"drishti decisions: unknown action {w!r}; one of cases rules export import list score compare audit forward history history-report history-breakdown history-coverage signal-study portfolio-sim holdout candidates pack", file=sys.stderr)
    return 2


LOG_KEEP = 30


def _log_to_file(cfg):
    """Send stdout and stderr to logs/catchup-<date>.log next to the database; returns what to restore. Keeps the newest LOG_KEEP logs."""
    from datetime import datetime
    d = cfg.db_path.parent / "logs"
    d.mkdir(parents=True, exist_ok=True)
    f = open(d / f"catchup-{datetime.now().date().isoformat()}.log", "a", encoding="utf-8", buffering=1)
    f.write(f"\n=== {datetime.now().isoformat(timespec='seconds')} drishti catchup ===\n")
    for old in sorted(d.glob("catchup-*.log"))[:-LOG_KEEP]:
        old.unlink(missing_ok=True)
    saved = (sys.stdout, sys.stderr)
    sys.stdout = sys.stderr = f
    return saved, f


def _catchup(st, args, cfg) -> int:
    saved = None
    if args.log or sys.__stdout__ is None:          # no console (pythonw, i.e. the scheduled task), or asked for
        saved, logf = _log_to_file(cfg)
    try:
        return _catchup_run(st, args, cfg)
    finally:
        if saved:
            sys.stdout, sys.stderr = saved
            logf.close()


def _catchup_run(st, args, cfg) -> int:
    import logging
    from pathlib import Path
    from . import catchup as cu
    logging.getLogger("drishti").setLevel(logging.ERROR)     # holiday 404s are expected; real failures are in the report below
    prof = None
    try:
        from .profile import load_profile
        prof = load_profile(cfg)
    except Exception:
        prof = None
    try:
        with cu.lock(cfg.db_path.parent / "catchup.lock"):
            rep = cu.run(st, budget_min=args.budget_min, private=args.private_dir, eval_dir=Path(args.eval_dir), profile=prof, only=args.slot, plan=args.plan,
                         progress=lambda m: print(m, flush=True))
    except cu.Locked as e:
        print(f"drishti catchup: another catch-up is already running ({e}); nothing to do", file=sys.stderr)
        return 0
    except KeyError:
        print(f"drishti catchup: unknown slot {args.slot!r}; one of {', '.join(s.name for s in cu.SLOTS)}", file=sys.stderr)
        return 2
    for s_ in rep.slots:
        print(s_.line())
    return 0 if rep.ok else 1


def _holdings(st, args) -> int:
    import json as _json
    from pathlib import Path
    from .portfolio import holdings as hd
    if args.what in ("import", "txns"):
        if not args.file:
            print(f"drishti holdings {args.what}: give the CSV file", file=sys.stderr)
            return 2
        r = (hd.import_snapshot_csv if args.what == "import" else hd.import_txn_csv)(st, Path(args.file))
        if not r.ok:
            print(f"{r.error.kind}: {r.error.detail}", file=sys.stderr)
            return 1
        print(f"imported {r.data['imported']} row(s)" + (f" as of {', '.join(r.data['as_of'])}" if r.data.get("as_of") else ""))
        for x in r.data["rejected"][:20]:
            print(f"  rejected line {x['line']}: {x['why']}")
        return 0 if not r.data["rejected"] else 1
    if args.what == "summary":
        s = hd.summary(st)
        print(f"holdings as of {s['as_of'] or '-'}")
        for h in s["holdings"]:
            pnl = f"{h['pnl']:>12,.0f} ({h['pnl_pct']:+.1f}%)" if h.get("pnl") is not None else f"{'at cost':>12s}"
            print(f"  {(h['symbol'] or h['isin']):<12s} {h['kind']:<5s} {h['quantity']:>9g} @ {h['avg_cost']:>9,.2f}  cost {h['cost']:>11,.0f}  value {h['value'] if h['value'] is not None else '-':>11}  {pnl}  txns {h['txns']}")
        t = s["totals"]
        if t:
            print(f"priced: cost {t['cost_priced']:,.0f}  value {t['value_priced']:,.0f}  profit {t['pnl_priced']:,.0f} ({t['pnl_priced_pct']}%)  | all cost {t['cost_all']:,.0f}  | unpriced: {', '.join(t['unpriced']) or '-'}")
        print(f"XIRR: {s['xirr'] if s['xirr'] is not None else 'not available'}  ({s['xirr_note']})")
        return 0
    print("drishti holdings: use one of import, txns, summary", file=sys.stderr)
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

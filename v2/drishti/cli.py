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

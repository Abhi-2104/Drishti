"""drishti CLI: mcp | worker | run <job> | jobs."""
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
    sub.add_parser("jobs", help="list jobs")
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
    if args.cmd == "run":
        return _todo(f"run {args.job}")
    return _todo(args.cmd)


if __name__ == "__main__":
    sys.exit(main())

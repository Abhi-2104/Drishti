"""drishti CLI: mcp | worker | run <job> | jobs."""
from __future__ import annotations

import argparse
import sys


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
    args = ap.parse_args(argv)

    if args.cmd == "run":
        return _todo(f"run {args.job}")
    return _todo(args.cmd)


if __name__ == "__main__":
    sys.exit(main())

"""Read-only probe, about 14 requests, writes eval/probe_flows_old.txt. Run: uv run python scripts/probe_flows_old.py
A. Does NSE's old delivery file (MTO_ddmmyyyy.DAT) exist for 2016-2019, and what does it look like?
B. What do the participant-OI files that failed to parse actually start with?"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

from drishti.core.envelope import DrishtiError
from drishti.core.fetch import get
from drishti.providers.profiles import NSE_ARCHIVE

OUT: list[str] = []


def say(s: str) -> None:
    print(s, flush=True)
    OUT.append(s)


def head(url: str, n: int, expect: str = "text") -> None:
    try:
        got = get(url, NSE_ARCHIVE, expect=expect)
    except DrishtiError as e:
        say(f"  {e.kind}: {e.detail[:110]}")
        return
    text = got.body.decode("utf-8", "replace")
    lines = text.splitlines()
    say(f"  OK {len(got.body)} bytes, {len(lines)} lines")
    for l in lines[:n]:
        say("    | " + l[:160])
    if len(lines) > n:
        say("    | ... last: " + lines[-1][:160])


def main() -> int:
    say("A. old delivery files (MTO)")
    for d in (date(2016, 9, 15), date(2018, 6, 14), date(2019, 8, 22), date(2019, 9, 30)):
        url = f"https://nsearchives.nseindia.com/archives/equities/mto/MTO_{d:%d%m%Y}.DAT"
        say(f"{d} {url}")
        head(url, 8)
    say("B. participant-OI files that failed")
    for d in (date(2021, 6, 8), date(2018, 6, 13), date(2018, 12, 6), date(2018, 5, 28)):
        url = f"https://nsearchives.nseindia.com/content/nsccl/fao_participant_oi_{d:%d%m%Y}.csv"
        say(f"{d}")
        head(url, 3, "csv")
    Path("eval").mkdir(exist_ok=True)
    Path("eval/probe_flows_old.txt").write_text("\n".join(OUT) + "\n", encoding="utf-8")
    print("wrote eval/probe_flows_old.txt")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Read-only probe, about 10 requests, writes eval/probe_flows_old2.txt. Run: uv run python scripts/probe_flows_old2.py
What is wrong with the delivery file of 8 Aug 2022, six old MTO files, and three 2017 participant-OI files?"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

from drishti.core.envelope import DrishtiError
from drishti.core.fetch import get
from drishti.providers import nse_archive as n
from drishti.providers.profiles import NSE_ARCHIVE

OUT: list[str] = []


def say(s: str) -> None:
    print(s, flush=True)
    OUT.append(s)


def raw(url: str):
    try:
        return get(url, NSE_ARCHIVE, expect="csv").body
    except DrishtiError as e:
        say(f"  {e.kind}: {e.detail[:110]}")
        return None


def main() -> int:
    d = date(2022, 8, 8)
    say(f"A. delivery file {d}")
    b = raw(n.DELIVERY_URL.format(dmy=d.strftime("%d%m%Y")))
    if b:
        say(f"  {len(b)} bytes; starts {b[:260]!r}")
        cr, crlf, lf = b"\r", b"\r\n", b"\n"
        say(f"  lone CR count {b.count(cr) - b.count(crlf)}, CRLF count {b.count(crlf)}, LF count {b.count(lf)}")
    say("B. old delivery (MTO) files with no trade-date line")
    for d in (date(2019, 3, 28), date(2019, 2, 25), date(2018, 9, 21), date(2018, 5, 31), date(2017, 11, 30), date(2017, 10, 25)):
        b = raw(f"https://nsearchives.nseindia.com/archives/equities/mto/MTO_{d:%d%m%Y}.DAT")
        if b:
            say(f"  {d}: " + " | ".join(repr(l[:70]) for l in b.decode('utf-8', 'replace').splitlines()[:4]))
    say("C. participant OI 2017 files (every row)")
    for d in (date(2017, 9, 22), date(2017, 5, 29), date(2017, 1, 24)):
        b = raw(f"{n.ARCH}/nsccl/fao_participant_oi_{d:%d%m%Y}.csv")
        if b:
            say(f"  {d}")
            for l in b.decode("utf-8", "replace").splitlines():
                say("    | " + l[:200])
    Path("eval").mkdir(exist_ok=True)
    Path("eval/probe_flows_old2.txt").write_text("\n".join(OUT) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())

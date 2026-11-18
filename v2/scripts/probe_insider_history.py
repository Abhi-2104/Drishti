"""Read-only probe: how far back NSE's insider-trading (SEBI PIT) list goes, how many filings a week it holds, and whether
old filings still have a readable XBRL file. About 20 requests. Run: uv run python scripts/probe_insider_history.py
Writes eval/probe_insider_history.txt."""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

from drishti.core.envelope import DrishtiError
from drishti.core.fetch import get
from drishti.providers import nse_live, nse_pit
from drishti.providers.profiles import NSE_ARCHIVE

OUT: list[str] = []


def say(s: str) -> None:
    print(s, flush=True)
    OUT.append(s)


def main() -> int:
    for end in (date(2016, 9, 30), date(2018, 6, 29), date(2020, 6, 30), date(2022, 6, 30), date(2024, 6, 28)):
        start = end - timedelta(days=6)
        try:
            got = nse_live.api_get("corporates-pit-gg", index="equities", from_date=start.strftime("%d-%m-%Y"), to_date=end.strftime("%d-%m-%Y"))
            rows = nse_pit.parse_pit_list(got.body)
        except DrishtiError as e:
            say(f"week to {end}: LIST FAIL {e.kind}: {e.detail[:100]}")
            continue
        days = sorted({r['broadcast'][:10] for r in rows})
        say(f"week to {end}: {len(rows)} filings, {len({r['symbol'] for r in rows})} companies, broadcast {days[0] if days else '-'}..{days[-1] if days else '-'}")
        for r in rows[:3]:
            try:
                x = get(r["xml_url"], NSE_ARCHIVE, expect="xml")
                ev = nse_pit.parse_pit_filing(x.body)
                say(f"    {r['symbol']:12s} xml OK, {len(ev)} events, e.g. {ev[0].get('category') if ev else '-'} {ev[0].get('mode') if ev else ''}")
            except DrishtiError as e:
                say(f"    {r['symbol']:12s} xml FAIL {e.kind}: {e.detail[:90]}")
    Path("eval").mkdir(exist_ok=True)
    Path("eval/probe_insider_history.txt").write_text("\n".join(OUT) + "\n", encoding="utf-8")
    print("wrote eval/probe_insider_history.txt")
    return 0


if __name__ == "__main__":
    sys.exit(main())

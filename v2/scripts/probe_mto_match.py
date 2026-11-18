"""Read-only check, 4 requests, writes eval/probe_mto_match.txt. Run: uv run python scripts/probe_mto_match.py
Do the old delivery file (MTO) and the current one (sec_bhavdata_full) agree on days where both exist? If yes, one delivery history can use both."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

from drishti.providers import nse_archive as n

OUT: list[str] = []


def say(s: str) -> None:
    print(s, flush=True)
    OUT.append(s)


def main() -> int:
    ok_all = True
    for d in (date(2019, 8, 23), date(2022, 3, 15)):
        a, b = n._delivery_full(d), n.delivery_mto(d)
        if not (a.ok and b.ok):
            say(f"{d}: full {'ok' if a.ok else a.error.kind} / mto {'ok' if b.ok else b.error.kind}")
            ok_all = False
            continue
        full = {(r["symbol"], r["series"]): r for r in a.data if r["deliv_per"] is not None}
        mto = {(r["symbol"], r["series"]): r for r in b.data}
        both = [k for k in full if k in mto]
        same_q = sum(1 for k in both if full[k]["ttl_qty"] == mto[k]["ttl_qty"] and full[k]["deliv_qty"] == mto[k]["deliv_qty"])
        close = sum(1 for k in both if abs(full[k]["deliv_per"] - mto[k]["deliv_per"]) <= 0.05)
        say(f"{d}: full {len(full)} rows, mto {len(mto)} rows, in both {len(both)}; identical quantities {same_q} ({same_q / max(1, len(both)):.1%}); "
            f"% within 0.05 {close} ({close / max(1, len(both)):.1%}); only in mto {len(set(mto) - set(full))}, only in full {len(set(full) - set(mto))}")
    Path("eval").mkdir(exist_ok=True)
    Path("eval/probe_mto_match.txt").write_text("\n".join(OUT) + "\n", encoding="utf-8")
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())

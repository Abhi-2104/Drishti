"""Read-only probe of old BSE bhavcopy files, 2016-2018 (run: uv run python scripts/probe_bse_old.py).
For one weekday per month it tries the two known file names and records what comes back: error kind, or the header line and
two data lines. Also tries the current file for 2-17 Nov 2026 and counts BSE rows stored per day. About 80 requests, 2-4 minutes. Writes eval/bse_old_samples.txt."""
from __future__ import annotations

import io
import sys
import zipfile
from datetime import date, timedelta
from pathlib import Path

from drishti.core.envelope import DrishtiError
from drishti.core.fetch import get
from drishti.providers import _util as u
from drishti.providers.profiles import BSE_WWW

VARIANTS = {
    "isincode": "https://www.bseindia.com/download/BhavCopy/Equity/EQ_ISINCODE_{ddmmyy}.zip",
    "eq_csv": "https://www.bseindia.com/download/BhavCopy/Equity/EQ{ddmmyy}_CSV.ZIP",
}
UDIFF = "https://www.bseindia.com/download/BhavCopy/Equity/BhavCopy_BSE_CM_0_0_0_{ymd}_F_0000.CSV"
OUT: list[str] = []


def say(s: str) -> None:
    print(s, flush=True)
    OUT.append(s)


def days():
    for y in (2016, 2017, 2018):
        for m in range(1, 13):
            d = date(y, m, 15)
            while d.weekday() >= 5:
                d += timedelta(days=1)
            yield d


def main() -> int:
    for d in days():
        for name, tpl in VARIANTS.items():
            url = tpl.format(ddmmyy=d.strftime("%d%m%y"))
            try:
                body = get(url, BSE_WWW, expect="zip").body
            except DrishtiError as e:
                say(f"{d} {name:8s} {e.kind}: {e.detail[:70]}")
                continue
            try:
                zf = zipfile.ZipFile(io.BytesIO(body))
                inner = zf.namelist()
                text = u.decode(zf.read(inner[0])).splitlines()
                say(f"{d} {name:8s} OK  file {inner[0]}  {len(text) - 1} rows")
                for ln in text[:3]:
                    say(f"      | {ln[:220]}")
            except Exception as e:  # noqa: BLE001
                say(f"{d} {name:8s} UNREADABLE {type(e).__name__}: {str(e)[:70]} ({len(body)} bytes)")
    say("== recent days, current (UDiFF) file; 17 Nov is not published until evening, so it shows what 'missing' looks like")
    d = date(2026, 11, 2)
    while d <= date(2026, 11, 17):
        if d.weekday() < 5:
            try:
                b = get(UDIFF.format(ymd=d.strftime("%Y%m%d")), BSE_WWW, expect="csv").body
                say(f"{d} udiff    OK  {len(b)} bytes  | {u.decode(b).splitlines()[0][:120]}")
            except DrishtiError as e:
                say(f"{d} udiff    {e.kind}: {e.detail[:70]}")
        d += timedelta(days=1)
    say("== BSE rows stored per day, last 40 days")
    from drishti.config import load_settings
    from drishti.store import Store
    with Store(load_settings().db_path) as st:
        for r in st.query("SELECT trade_date, SUM(exchange='BSE') bse, SUM(exchange='NSE') nse FROM price_eod "
                          "WHERE trade_date >= date('now','-40 days') GROUP BY trade_date ORDER BY trade_date"):
            say(f"{r['trade_date']}  BSE {r['bse']:5d}  NSE {r['nse']:5d}")
    Path("eval").mkdir(exist_ok=True)
    Path("eval/bse_old_samples.txt").write_text("\n".join(OUT) + "\n", encoding="utf-8")
    print("wrote eval/bse_old_samples.txt")
    return 0


if __name__ == "__main__":
    sys.exit(main())

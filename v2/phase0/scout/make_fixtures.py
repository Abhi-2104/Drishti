"""Trim the raw captures in tests/fixtures/_raw (gitignored) into small committed fixtures in tests/fixtures/<group>/.

Reproducible and offline: rerun after a new capture_fixtures run if you want fresher samples. Stdlib only.
    python phase0/scout/make_fixtures.py [--only nse_archive]
Trimmed bhavcopies keep the real header and real rows, only fewer of them; parsers take min_rows= so tests can
read them, while production keeps the real minimum-row guard.
"""
from __future__ import annotations

import argparse
import csv
import io
import shutil
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "tests" / "fixtures"
RAW = ROOT / "_raw"


def _read_zip(p: Path) -> tuple[str, str]:
    with zipfile.ZipFile(p) as z:
        name = z.namelist()[0]
        return name, z.read(name).decode("utf-8-sig")


def _write_zip(dst: Path, member: str, text: str) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(member, text)


def _csv_text(header: list[str], rows: list[list[str]]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(header)
    w.writerows(rows)
    return buf.getvalue()


def trim_cm(src: Path, dst: Path) -> int:
    member, text = _read_zip(src)
    rows = list(csv.reader(io.StringIO(text)))
    header, body = rows[0], rows[1:]
    ix = {h: i for i, h in enumerate(header)}
    keep_syms = {"RELIANCE", "TCS", "INFY", "HDFCBANK", "KAYNES", "ITC", "SBIN", "VEDL"}
    seen: dict[str, int] = {}
    out = []
    for r in body:
        series = r[ix["SctySrs"]]
        take = r[ix["TckrSymb"]] in keep_syms or seen.get(series, 0) < 6  # a few rows of every series (EQ, BE, SM, GB, ...)
        if take:
            seen[series] = seen.get(series, 0) + 1
            out.append(r)
    _write_zip(dst, member, _csv_text(header, out))
    return len(out)


def trim_fo(src: Path, dst: Path) -> int:
    member, text = _read_zip(src)
    rows = list(csv.reader(io.StringIO(text)))
    header, body = rows[0], rows[1:]
    ix = {h: i for i, h in enumerate(header)}
    focus = {"NIFTY", "BANKNIFTY", "RELIANCE", "KAYNES", "ABCAPITAL"}
    per: dict[tuple[str, str, str], int] = {}
    out = []
    for r in body:
        tp, sym, opt = r[ix["FinInstrmTp"]], r[ix["TckrSymb"]], r[ix["OptnTp"]]
        if tp == "IDF" or (tp == "STF" and sym in focus):
            out.append(r)  # all index futures, and futures of the focus names
            continue
        if sym in focus:
            k = (sym, tp, opt)
            traded = r[ix["TtlTradgVol"]] not in ("", "0")
            if per.get(k, 0) < 25 or (traded and per.get((*k, "t"), 0) < 15):
                per[k] = per.get(k, 0) + 1
                if traded:
                    per[(*k, "t")] = per.get((*k, "t"), 0) + 1
                out.append(r)
    _write_zip(dst, member, _csv_text(header, out))
    return len(out)


def group_nse_archive() -> None:
    src, dst = RAW / "nse_archive", ROOT / "nse_archive"
    dst.mkdir(parents=True, exist_ok=True)
    n = trim_cm(next(src.glob("BhavCopy_NSE_CM_*.csv.zip")), dst / "BhavCopy_NSE_CM_20261106.csv.zip")
    print(f"cm_bhavcopy: {n} rows")
    n = trim_fo(next(src.glob("BhavCopy_NSE_FO_*.csv.zip")), dst / "BhavCopy_NSE_FO_20261106.csv.zip")
    print(f"fo_bhavcopy: {n} rows")
    for name in ("fao_participant_oi_06112026.csv", "ind_close_all_06112026.csv", "fo_secban_06112026.csv", "bulk.csv", "block.csv"):
        shutil.copyfile(src / name, dst / name)  # small: copied whole
        print(f"{name}: copied ({(dst / name).stat().st_size:,} bytes)")


def group_bse() -> None:
    src, dst = RAW / "bse", ROOT / "bse"
    dst.mkdir(parents=True, exist_ok=True)
    text = next(src.glob("BhavCopy_BSE_CM_*.csv")).read_text(encoding="utf-8-sig")
    rows = list(csv.reader(io.StringIO(text)))
    header, body = rows[0], rows[1:]
    ix = {h: i for i, h in enumerate(header)}
    keep_syms = {"RELIANCE", "TCS", "INFY", "ABB", "VEDL", "KAYNES"}
    seen: dict[str, int] = {}
    out = []
    for r in body:
        series = r[ix["SctySrs"]]
        if r[ix["TckrSymb"]] in keep_syms or seen.get(series, 0) < 6:  # a few rows of every BSE group (A, B, X, XT, M, T, ...)
            seen[series] = seen.get(series, 0) + 1
            out.append(r)
    (dst / "BhavCopy_BSE_CM_20261106.csv").write_text(_csv_text(header, out), encoding="utf-8", newline="")
    print(f"bse_bhavcopy: {len(out)} rows")


GROUPS = {"nse_archive": group_nse_archive, "bse": group_bse}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--only", choices=sorted(GROUPS), action="append")
    args = ap.parse_args()
    for name in args.only or sorted(GROUPS):
        print(f"== {name}")
        GROUPS[name]()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

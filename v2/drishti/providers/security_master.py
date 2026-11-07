"""Security master: one row per ISIN, with the NSE symbol and the BSE scrip code that belong to it.

Sources: NSE EQUITY_L.csv (every NSE equity listing, traded or not: authoritative for "is it on NSE"), the BSE
bhavcopy (scrip code + ISIN + symbol), and the NSE cash bhavcopy only to pick up ETFs, which EQUITY_L omits.

Read this before relying on the result: the BSE bhavcopy lists only scrips that *traded that day*. So a company that
EQUITY_L lists but the BSE file lacks may still be listed on BSE; its bse_scrip is simply not observed yet (the store
merges and never blanks, so repeated runs fill it in). The reverse is solid: an ISIN in the BSE file and absent from
EQUITY_L is not on NSE's equity segment (`listing == "bse"`).
"""
from __future__ import annotations

from collections import Counter
from datetime import date

from ..core.canary import check_fresh, today_ist
from ..core.envelope import DrishtiError, ErrorKind, Result, guard
from ..core.fetch import get
from ..core.isin import is_valid_isin, normalize_isin
from . import _util as u
from . import bse, nse_archive
from .profiles import NSE_ARCHIVE

SOURCE = "security_master"
EQUITY_L_URL = f"{nse_archive.ARCH}/equities/EQUITY_L.csv"
MIN_EQUITY_L = 1500       # 2,630 on 2026-11-06
MIN_OVERLAP = 0.6         # share of NSE equities that must also appear in the BSE file, or the ISIN join is broken (actual: 93%)
KEEP_FROM_BSE = ("equity", "fund")   # NCDs, government securities etc. also sit in the BSE bhavcopy; they are not part of this master


def instrument_class(isin: str) -> str:
    """Rough class from the ISIN's shape (INE + 2-digit security type; INF = fund unit; IN0-IN3 = government paper)."""
    if isin.startswith("INF"):
        return "fund"
    if isin[:3] in ("IN0", "IN1", "IN2", "IN3"):
        return "sovereign"
    if isin.startswith("INE"):
        t = isin[7:9]
        return "equity" if t == "01" else "debt" if t in ("07", "08", "09") else "other"
    return "other"


def parse_equity_l(body: bytes, *, min_rows: int = MIN_EQUITY_L) -> list[dict]:
    label = "equity_l"
    out, syms, isins, bad = [], set(), set(), []
    for r in u.csv_rows(u.decode(body), ["SYMBOL", "NAME OF COMPANY", "SERIES", "DATE OF LISTING", "ISIN NUMBER"], label):
        isin = normalize_isin(r["ISIN NUMBER"])
        sym = r["SYMBOL"].strip().upper()
        if not is_valid_isin(isin):
            bad.append((sym, r["ISIN NUMBER"]))
            continue
        if sym in syms or isin in isins:
            raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: {sym}/{isin} appears twice")
        syms.add(sym), isins.add(isin)
        out.append({"nse_symbol": sym, "name": u.opt_str(r["NAME OF COMPANY"]), "nse_series": r["SERIES"].strip(), "isin": isin,
                    "listed_on": u.iso_date(r["DATE OF LISTING"], f"{label}.listing date"),
                    "face_value": u.num(r.get("FACE VALUE"), "face_value")})
    if bad and len(bad) > 0.01 * (len(out) + len(bad)):
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: {len(bad)} rows with an invalid ISIN, e.g. {bad[:3]}")
    u.require_rows(out, label, min_rows)
    return out


def merge_master(equity_l: list[dict], bse_rows: list[dict], nse_cm: list[dict] = ()) -> dict:
    """Join the three listings on ISIN. Nothing is dropped silently: skipped / rejected / conflicting rows are reported."""
    sec: dict[str, dict] = {}
    rejected, conflicts, skipped = [], [], Counter()
    for r in equity_l:
        sec[r["isin"]] = {"isin": r["isin"], "name": r["name"], "instrument_class": "equity", "nse_symbol": r["nse_symbol"],
                          "nse_series": r["nse_series"], "nse_listed_on": r["listed_on"], "bse_scrip": None, "bse_symbol": None, "bse_series": None}
    for r in nse_cm:   # ETFs and other fund units traded on NSE
        isin = normalize_isin(r.get("isin"))
        if is_valid_isin(isin) and instrument_class(isin) == "fund" and isin not in sec:
            sec[isin] = {"isin": isin, "name": r["name"], "instrument_class": "fund", "nse_symbol": r["symbol"].strip().upper(),
                         "nse_series": r["series"], "nse_listed_on": None, "bse_scrip": None, "bse_symbol": None, "bse_series": None}
    for r in sorted(bse_rows, key=lambda x: x["scrip_code"]):
        isin = normalize_isin(r.get("isin"))
        if not is_valid_isin(isin):
            rejected.append({"source": "bse", "symbol": r["symbol"], "scrip": r["scrip_code"], "isin": r.get("isin")})
            continue
        cls = instrument_class(isin)
        if isin not in sec and cls not in KEEP_FROM_BSE:
            skipped[cls] += 1
            continue
        s = sec.setdefault(isin, {"isin": isin, "name": r["name"], "instrument_class": cls, "nse_symbol": None, "nse_series": None,
                                  "nse_listed_on": None, "bse_scrip": None, "bse_symbol": None, "bse_series": None})
        if s["bse_scrip"] and s["bse_scrip"] != r["scrip_code"]:
            conflicts.append({"isin": isin, "kept": s["bse_scrip"], "ignored": r["scrip_code"]})
            continue
        s.update(bse_scrip=r["scrip_code"], bse_symbol=r["symbol"].strip().upper(), bse_series=r["series"])
    for s in sec.values():
        s["listing"] = "both" if s["nse_symbol"] and s["bse_scrip"] else "nse" if s["nse_symbol"] else "bse"
    rows = sorted(sec.values(), key=lambda s: s["isin"])
    nse_eq = [s for s in rows if s["nse_symbol"] and s["instrument_class"] == "equity"]
    both_eq = [s for s in nse_eq if s["bse_scrip"]]
    overlap = len(both_eq) / len(nse_eq) if nse_eq else 0.0
    stats = {"securities": len(rows), "by_listing": dict(Counter(s["listing"] for s in rows)),
             "by_class": dict(Counter(s["instrument_class"] for s in rows)), "nse_equities": len(nse_eq),
             "nse_equities_seen_on_bse": len(both_eq), "overlap": round(overlap, 3), "skipped_from_bse": dict(skipped),
             "rejected": len(rejected), "conflicts": len(conflicts),
             "symbol_differs": sum(1 for s in both_eq if s["nse_symbol"] != s["bse_symbol"])}
    return {"securities": rows, "stats": stats, "rejected": rejected, "conflicts": conflicts}


@guard
def equity_list(*, today: date | None = None, max_listing_age_days: int = 90, min_rows: int = MIN_EQUITY_L) -> Result:
    """NSE EQUITY_L. The file carries no date of its own, so freshness is judged by the newest listing date: a feed that
    stopped updating shows no new listing for months, while a live one adds a few a month."""
    got = get(EQUITY_L_URL, NSE_ARCHIVE, expect="csv")
    rows = parse_equity_l(got.body, min_rows=min_rows)
    newest = check_fresh(rows, "listed_on", max_listing_age_days, "equity_l", today=today)
    return Result(data=rows, as_of=newest.isoformat(), sources=[got.source("nse_equity_l")])


@guard
def build_security_master(store, *, today: date | None = None, min_equity_rows: int = MIN_EQUITY_L,
                          max_listing_age_days: int = 90) -> Result:
    """Fetch the three listings, merge on ISIN, upsert into the store. Returns the merge stats."""
    today = today or today_ist()
    eq = equity_list(today=today, min_rows=min_equity_rows, max_listing_age_days=max_listing_age_days)
    if not eq.ok:
        return eq
    bs = u.latest(lambda d: bse.bhavcopy(d, store=store), today)
    if not bs.ok:
        return bs
    warnings = []
    nse = u.latest(lambda d: nse_archive.cm_bhavcopy(d, store=store), today)
    if not nse.ok:
        warnings.append(f"NSE cash bhavcopy unavailable ({nse.error.kind}): ETFs traded on NSE are not added this run")
    merged = merge_master(eq.data, bs.data, nse.data if nse.ok else [])
    st = merged["stats"]
    if st["overlap"] < MIN_OVERLAP:
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"security_master: only {st['overlap']:.0%} of NSE equities appear in the BSE file (need {MIN_OVERLAP:.0%}); ISIN join broken?")
    for s in merged["securities"]:
        store.upsert_security(s["isin"], name=s["name"], nse_symbol=s["nse_symbol"], bse_scrip=s["bse_scrip"], bse_symbol=s["bse_symbol"],
                              source=SOURCE, instrument_class=s["instrument_class"], nse_series=s["nse_series"], bse_series=s["bse_series"],
                              nse_listed_on=s["nse_listed_on"])
    st["bse_file_date"] = bs.as_of
    st["warnings"] = warnings
    return Result(data={**merged, "securities": len(merged["securities"])}, as_of=bs.as_of,
                  sources=eq.sources + bs.sources + (nse.sources if nse.ok else []))

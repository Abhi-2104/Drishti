"""Shareholding patterns (SHP): promoter holding, promoter pledges and other encumbrances.

Two layers, because the full filings are big (100-300 KB of XBRL):
  * the market-wide master list (one JSON, ~5 MB): promoter % and public % for every company and quarter, plus
    a link to each filing. Enough on its own to screen QoQ promoter holding changes for the whole market.
  * the filing XBRL: the only place that says how many promoter shares are pledged / under NDU / otherwise
    encumbered. Fetched per company (watchlist and screen hits), not market-wide.

Semantics verified by arithmetic on real filings (Alps, Arshiya, Kiri, Vedanta, 2026-11-07):
  EncumberedSharesHeldAsPercentageOfTotalNumberOfShares in the promoter-group context is a FRACTION of the shares held
  in that same context (promoter shares), 1.0 meaning all promoter shares; in the grand-total context it is a fraction
  of all shares. Total encumbered = pledged + non-disposal undertaking (NDU) + other encumbrances (Vedanta is 100%
  encumbered with zero pledge, all NDU and "other"), so a pledge-only signal would have missed it. We recompute every
  percentage from share counts and refuse the filing if NSE's own figure disagrees: that would mean the definition
  moved under us.
"""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, datetime, timedelta

from ..core.canary import today_ist
from ..core.envelope import DrishtiError, ErrorKind, Result, guard
from ..core.fetch import get
from ..core.isin import is_valid_isin, normalize_isin
from . import _util as u
from . import nse_live
from .profiles import NSE_ARCHIVE

SOURCE = "nse_shp"
CTX_PROMOTER = "ShareholdingOfPromoterAndPromoterGroup_ContextI"
CTX_PUBLIC = "PublicShareholding_ContextI"
CTX_NPNP = "SharesHeldByNonPromoterNonPublicShareholders_ContextI"
CTX_TOTAL = "ShareholdingPattern_ContextI"
CTX_DR = "CustodianOrDRHolder_ContextI"   # C1: shares behind ADRs/GDRs, outside SEBI's percentage base
_HOLDER = re.compile(r"^(?P<cat>.+)_Context(?P<n>\d+)$")
_FLAGS = {"pledged": "WhetherAnySharesHeldByPromotersAreEncumberedUnderPledged",
          "ndu": "WhetherAnySharesHeldByPromotersAreEncumberedUnderNonDisposalUndertaking",
          "other": "WhetherAnySharesHeldByPromotersAreEncumberedOtherThanByWayOfPledgeOrNDU"}
PCT_TOL = 0.0006        # NSE reports fractions to 4 decimals; allow for rounding
SUM_TOL = 0.0001        # relative tolerance for share-count reconciliation


# --- one company's filing (XBRL) --------------------------------------------------------------------
def _i(x: dict, name: str) -> int | None:
    return u.integer(x.get(name), name) if name in x else None


def _frac(x: dict, name: str) -> float | None:
    return u.num(x.get(name), name) if name in x else None


def _close(a: float, b: float, rel: float = SUM_TOL) -> bool:
    return abs(a - b) <= max(1.0, rel * max(abs(a), abs(b)))


def parse_shp_filing(xml: bytes) -> dict:
    """One filing's XBRL -> promoter holding and encumbrance summary (+ per-holder rows). All percentages are in
    percent (0-100) and computed from share counts; NSE's own figures are only used as a cross-check."""
    label = "shp_filing"
    ctx, version = u.xbrl_facts(xml, "shp", label)
    main = {**ctx.get("MainD", {}), **ctx.get("MainI", {})}
    for need in ("DateOfReport",):
        if need not in main:
            raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: no {need}")
    for need in (CTX_PUBLIC, CTX_TOTAL):
        if "NumberOfShares" not in ctx.get(need, {}):
            raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: no share count in {need}")
    total = _i(ctx[CTX_TOTAL], "NumberOfShares")
    pub = _i(ctx[CTX_PUBLIC], "NumberOfShares")
    npnp = _i(ctx.get(CTX_NPNP, {}), "NumberOfShares") or 0
    pr = ctx.get(CTX_PROMOTER, {})
    # Professionally managed companies (Coforge, ...) have no promoter section to speak of. That is accepted only
    # when the rest of the register adds up to the whole, so a truncated filing cannot pass as "no promoter".
    prom = _i(pr, "NumberOfShares")
    if prom is None:
        prom = 0
    if not total or total <= 0 or pub is None or pub < 0 or prom < 0:
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: nonsensical share counts total={total} promoter={prom} public={pub}")
    if not _close(prom + pub + npnp, total):
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: promoter {prom} + public {pub} + non-promoter-non-public {npnp} != total {total}")
    # SEBI's percentages exclude shares held by the depository-receipt custodian (category C1: Infosys, M&M, SBI, ...),
    # so the base is total - C1. With no DRs the two are the same.
    dr = _i(ctx.get(CTX_DR, {}), "NumberOfShares") or 0
    if dr < 0 or dr > npnp:
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: depository-receipt shares {dr} outside non-promoter-non-public {npnp}")
    base = total - dr
    pc = _frac(pr, "ShareholdingAsAPercentageOfTotalNumberOfShares")
    if pc is not None and abs(pc - prom / base) > PCT_TOL and abs(pc - prom / total) > PCT_TOL:
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: reported promoter holding {pc} disagrees with {prom}/{base} (and /{total})")

    pledged, ndu, other = (_i(pr, "NumberOfSharesEncumberedUnderPledged"), _i(pr, "NumberOfSharesEncumberedUnderNonDisposalUndertaking"),
                           _i(pr, "NumberOfSharesEncumberedUnderOtherEncumbrances"))
    parts = [v for v in (pledged, ndu, other) if v is not None]
    enc_total = _i(pr, "NumberOfSharesEncumbered")
    if enc_total is None:
        enc_total = sum(parts)
    elif parts and not _close(sum(parts), enc_total):
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: pledged+NDU+other {sum(parts)} != total encumbered {enc_total}")
    if enc_total > prom:
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: encumbered {enc_total} exceeds promoter shares {prom}")
    rep_p = _frac(pr, "EncumberedSharesHeldAsPercentageOfTotalNumberOfShares")
    if prom and rep_p is not None and abs(rep_p - enc_total / prom) > PCT_TOL:
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: reported encumbered share {rep_p} disagrees with {enc_total}/{prom}; definition changed?")
    rep_t = _frac(ctx[CTX_TOTAL], "EncumberedSharesHeldAsPercentageOfTotalNumberOfShares")
    if rep_t is not None and abs(rep_t - enc_total / base) > PCT_TOL and abs(rep_t - enc_total / total) > PCT_TOL:
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: reported encumbered share of total {rep_t} disagrees with {enc_total}/{base}")

    declared = {k: main.get(v, "").strip().lower() == "true" for k, v in _FLAGS.items()}
    holders = _holders(ctx)
    isin = normalize_isin(main.get("ISIN"))
    pct = lambda a, b: round(a / b * 100, 4) if b else None  # noqa: E731
    return {
        "isin": isin or None, "isin_valid": is_valid_isin(isin), "symbol": u.opt_str(main.get("Symbol")),
        "scrip_code": u.opt_str(main.get("ScripCode")), "company": u.opt_str(main.get("NameOfTheCompany")),
        "as_on": u.iso_date(main["DateOfReport"], f"{label}.DateOfReport"), "form_version": version,
        "total_shares": total, "promoter_shares": prom, "dr_shares": dr,
        "promoter_pct": pct(prom, base), "public_pct": pct(pub, base),
        "pledged_shares": pledged or 0, "ndu_shares": ndu or 0, "other_encumbered_shares": other or 0, "encumbered_shares": enc_total,
        "encumbered_pct_of_promoter": pct(enc_total, prom), "pledged_pct_of_promoter": pct(pledged or 0, prom),
        "encumbered_pct_of_total": pct(enc_total, base),
        "declared_pledged": declared["pledged"], "declared_ndu": declared["ndu"], "declared_other": declared["other"],
        "flags_consistent": any(declared.values()) == (enc_total > 0),  # box ticked iff shares are encumbered
        "no_promoter": prom == 0, "holders": holders, "holders_reconcile": _close(sum(h["shares"] for h in holders), prom),
    }


def _holders(ctx: dict[str, dict[str, str]]) -> list[dict]:
    """Per-promoter rows: a detail context 'D_<cat>_ContextN' carries the name, 'cat_ContextN' the numbers."""
    out = []
    for cid, facts in ctx.items():
        m = _HOLDER.match(cid)
        d = ctx.get("D_" + cid)
        if cid.startswith("D_") or not m or m.group("n") == "I" or d is None or "TypeOfPromoterShareholding" not in d:
            continue
        shares = _i(facts, "NumberOfShares")
        if shares is None:
            continue
        enc = _i(facts, "NumberOfSharesEncumbered") or 0
        out.append({"name": u.opt_str(d.get("NameOfTheShareholder")), "category": m.group("cat"), "shares": shares,
                    "encumbered_shares": enc, "pledged_shares": _i(facts, "NumberOfSharesEncumberedUnderPledged") or 0,
                    "encumbered_pct_of_holding": round(enc / shares * 100, 4) if shares else None})
    return sorted(out, key=lambda h: (-h["encumbered_shares"], h["name"] or ""))


# --- market-wide master list ------------------------------------------------------------------------
def parse_shp_master(body: bytes) -> list[dict]:
    """corporate-share-holdings-master JSON -> one row per filing (a company has several: quarters, revisions)."""
    label = "shp_master"
    out = []
    for r in nse_live.rows_of(body, label):
        for k in ("symbol", "date", "recordId", "xbrl", "pr_and_prgrp"):
            if not r.get(k):
                raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: row without {k}: {str(r)[:120]}")
        isin = normalize_isin(r.get("isin"))
        out.append({
            "record_id": str(r["recordId"]), "symbol": r["symbol"].strip().upper(), "company": u.opt_str(r.get("name")),
            "isin": isin or None, "isin_valid": is_valid_isin(isin) if isin else None,
            "as_on": u.iso_date(r["date"], f"{label}.date"), "broadcast": _stamp(r.get("broadcastDate") or r["submissionDate"]),
            "revised": str(r.get("revisedData") or "").strip().lower() == "revised",
            "promoter_pct": u.num(r["pr_and_prgrp"], "pr_and_prgrp"), "public_pct": u.num(r.get("public_val"), "public_val"),
            "xbrl_url": r["xbrl"], "remarks": _remarks(r.get("remarksWeb")),
        })
    u.require_rows(out, label)
    return out


def _remarks(v) -> str | None:
    """The filer's own note ('N' means none). It explains many odd numbers: a capital reduction with the ISIN
    suspended, or a clarification note correcting an earlier quarter, so it travels with the row."""
    t = str(v or "").strip()
    return None if t in ("", "N", "-", "None") else t[:400]


def _stamp(v: str) -> str:
    v = str(v).strip().title()
    for fmt in ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y"):
        try:
            return datetime.strptime(v, fmt).isoformat(timespec="seconds")
        except ValueError:
            continue
    raise DrishtiError(ErrorKind.PARSE_FAIL, f"shp_master: cannot read timestamp {v!r}")


def quarter_end_on_or_before(d: date) -> date:
    for m, day in ((12, 31), (9, 30), (6, 30), (3, 31)):
        q = date(d.year, m, day)
        if q <= d:
            return q
    return date(d.year - 1, 12, 31)


def is_quarter_end(d: date) -> bool:
    return (d.month, d.day) in ((3, 31), (6, 30), (9, 30), (12, 31))


def check_coverage(rows: list[dict], today: date, *, min_companies: int = 1500, grace_days: int = 30) -> date:
    """Quarterly canary. The newest row's date proves little (event filings carry odd dates like 07-NOV-2026), so
    test quarter-end coverage instead: once `grace_days` have passed since a quarter-end (SEBI's deadline is 21 days),
    at least `min_companies` must have filed for it. Before that, the previous quarter must be fully there.
    Returns the quarter-end that is covered. Raises STALE otherwise."""
    count: dict[date, int] = defaultdict(int)
    for r in rows:
        d = date.fromisoformat(r["as_on"])
        if is_quarter_end(d):
            count[d] += 1
    expected = quarter_end_on_or_before(today)
    if (today - expected).days > grace_days:
        need = expected
    else:
        need = quarter_end_on_or_before(expected - timedelta(days=1))
    if count.get(need, 0) < min_companies:
        raise DrishtiError(ErrorKind.STALE, f"shp_master: only {count.get(need, 0)} companies for quarter {need} "
                                            f"(need >= {min_companies}); quarters seen: { {str(k): v for k, v in sorted(count.items())[-3:]} }")
    return need


SWING_PP = 10.0      # a promoter stake rarely moves this much in one quarter; when it does, check the filing before believing it
_NOTE_WORDS = re.compile(r"clarif|restat|correct|revis|suspen|reduction|amalgam|merger|demerger|scheme|delist", re.I)


def change_flags(cur: dict, prev: dict | None, fl: list[dict]) -> list[str]:
    """Why a QoQ promoter change should not be taken at face value. Empty list = nothing suspicious found."""
    if prev is None or cur["promoter_pct"] is None or prev["promoter_pct"] is None:
        return []
    flags = []
    for r in (cur, prev):  # same as-on date but different promoter %: the master carries a restated/contradictory row
        vals = {x["promoter_pct"] for x in fl if x["as_on"] == r["as_on"] and x["promoter_pct"] is not None}
        if len(vals) > 1 and max(vals) - min(vals) > 0.5:
            flags.append(f"conflicting_rows_{r['as_on']}")
    if any(r.get("remarks") and _NOTE_WORDS.search(r["remarks"]) for r in (cur, prev)):
        flags.append("filer_note")
    if abs(cur["promoter_pct"] - prev["promoter_pct"]) >= SWING_PP:
        flags.append("implausible_swing")
    return flags


def latest_filings(rows: list[dict]) -> list[dict]:
    """One row per company: its newest filing (by as-on date, then broadcast time; revisions broadcast later and win),
    with the previous quarter-end filing alongside so the QoQ promoter change is one subtraction. `change_flags`
    lists reasons to distrust that subtraction (conflicting rows, filer notes about corrections or corporate actions,
    swings of 10pp or more); screens should show flagged rows separately rather than rank them as signals."""
    by: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by[r["isin"] or r["symbol"]].append(r)
    out = []
    for fl in by.values():
        fl.sort(key=lambda r: (r["as_on"], r["broadcast"]))
        cur = fl[-1]
        # previous quarter-end filing; for an event-dated filing (e.g. 07-NOV) that is the last quarter-end before it
        prior = [r for r in fl if r["as_on"] < cur["as_on"] and is_quarter_end(date.fromisoformat(r["as_on"]))]
        prev = max(prior, key=lambda r: (r["as_on"], r["broadcast"])) if prior else None
        flags = change_flags(cur, prev, fl)
        out.append({**cur, "prev_as_on": prev["as_on"] if prev else None, "prev_promoter_pct": prev["promoter_pct"] if prev else None,
                    "promoter_change_pp": round(cur["promoter_pct"] - prev["promoter_pct"], 4)
                    if prev and cur["promoter_pct"] is not None and prev["promoter_pct"] is not None else None,
                    "change_flags": flags})
    return sorted(out, key=lambda r: r["symbol"])


# --- providers --------------------------------------------------------------------------------------
@guard
def shp_master(*, days: int = 130, today: date | None = None, store=None, min_companies: int = 1500) -> Result:
    """Latest shareholding filing per company with promoter % and QoQ change. Refuses (STALE) if the newest
    quarter is not properly covered, rather than reporting last quarter's data as current."""
    today = today or today_ist()
    got = nse_live.api_get("corporate-share-holdings-master", index="equities",
                           from_date=(today - timedelta(days=days)).strftime("%d-%m-%Y"), to_date=today.strftime("%d-%m-%Y"))
    rows = parse_shp_master(got.body)
    covered = check_coverage(rows, today, min_companies=min_companies)
    if store is not None:
        store.put_blob("shp_master", today, got, SOURCE)
    return Result(data=latest_filings(rows), as_of=covered.isoformat(), sources=[got.source(SOURCE)])


@guard
def shp_master_rows(*, days: int, today: date | None = None) -> Result:
    """Every filing row in the window (all quarters, revisions included), for history backfills. No coverage check:
    the caller picks the quarters it wants."""
    today = today or today_ist()
    got = nse_live.api_get("corporate-share-holdings-master", index="equities",
                           from_date=(today - timedelta(days=days)).strftime("%d-%m-%Y"), to_date=today.strftime("%d-%m-%Y"))
    rows = parse_shp_master(got.body)
    return Result(data=rows, as_of=max(r["as_on"] for r in rows), sources=[got.source(SOURCE)])


@guard
def shp_filing(xbrl_url: str) -> Result:
    """Promoter holding and encumbrance (pledge / NDU / other) from one company's filing."""
    if not xbrl_url or xbrl_url.rstrip("/").endswith(("/-", "xbrl")) or xbrl_url.strip() == "-":
        # NSE lists some filings with no XBRL file at all (TIINDIA, June 2026 quarter: link '.../xbrl/-')
        raise DrishtiError(ErrorKind.NOT_FOUND, f"NSE published no XBRL file for this filing (link {xbrl_url!r})")
    got = get(xbrl_url, NSE_ARCHIVE, expect="xml")
    data = parse_shp_filing(got.body)
    return Result(data=data, as_of=data["as_on"], sources=[got.source(SOURCE)])

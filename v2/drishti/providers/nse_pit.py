"""Insider trading disclosures (SEBI PIT Reg 7(2)) from NSE: promoter / director / KMP buys, sells and pledges.

Two steps: the filing index (corporates-pit-gg JSON: one row per filing with links) and each filing's structured
XBRL twin (~5 KB XML; the iXBRL HTML is 20 KB of the same facts and is not needed).

Notes verified on real filings (2026-11-07): percentages in the XML are FRACTIONS (0.6426 is shown as 64.26% in the
HTML), so we store percent = value * 100. The same event is sometimes filed twice under different appIds, hence
dedupe_events. A pledge's "quantity" is the number of shares pledged, not a change in holding.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Iterable

from ..core.canary import check_fresh, today_ist
from ..core.envelope import DrishtiError, ErrorKind, Result, guard
from ..core.fetch import get
from ..core.isin import is_valid_isin, normalize_isin
from . import _util as u
from . import nse_live
from .profiles import NSE_ARCHIVE

SOURCE = "nse_pit"
_DISCLOSURE = re.compile(r"^Disclosure(\d+)$")

_EVENT_FIELDS = {  # XBRL local name -> our key ("Acquistion" is NSE's own spelling)
    "TypeOfInstrument": "instrument", "CategoryOfPerson": "category", "NameOfThePerson": "person",
    "SecuritiesHeldPriorToAcquisitionOrDisposalNumberOfSecurity": "held_pre_shares",
    "SecuritiesHeldPriorToAcquisitionOrDisposalPercentageOfShareholding": "held_pre_pct",
    "SecuritiesAcquiredOrDisposedNumberOfSecurity": "quantity",
    "SecuritiesAcquiredOrDisposedValueOfSecurity": "value_inr",
    "SecuritiesAcquiredOrDisposedTransactionType": "txn_type",
    "SecuritiesHeldPostAcquistionOrDisposalNumberOfSecurity": "held_post_shares",
    "SecuritiesHeldPostAcquistionOrDisposalPercentageOfShareholding": "held_post_pct",
    "DateOfAllotmentAdviceOrAcquisitionOrSaleOfSharesSpecifyFromDate": "from_date",
    "DateOfAllotmentAdviceOrAcquisitionOfSharesOrSaleOfSharesSpecifyFromDate": "from_date",
    "DateOfAllotmentAdviceOrAcquisitionOfSharesOrSaleOfSharesSpecifyToDate": "to_date",
    "ModeOfAcquisitionOrDisposal": "mode", "DateOfIntimationToCompany": "intimation_date",
    "ExchangeOnWhichTheTradeWasExecuted": "exchange",
}
def _direction(txn_type: str) -> str:
    """NSE's transaction types: Buy, Sell, Pledge, 'Pledge Revoke', 'Pledge Invoke' (older forms: Revocation/Invocation)."""
    t = txn_type.strip().lower()
    if t in ("buy", "sell"):
        return t.upper()
    if "revo" in t:
        return "REVOKE"
    if "invo" in t:
        return "INVOKE"
    return "PLEDGE" if t == "pledge" else "OTHER"


def parse_pit_list(body: bytes) -> list[dict]:
    """corporates-pit-gg JSON -> one row per filing (newest-first order is not assumed)."""
    label = "pit_list"
    out = []
    for r in nse_live.rows_of(body, label):
        for k in ("appId", "symbol", "broadcastDateTime", "xmlFileName"):
            if not r.get(k):
                raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: filing without {k}: {str(r)[:120]}")
        out.append({
            "app_id": str(r["appId"]), "prev_app_id": u.opt_str(r.get("prevAppId")), "symbol": r["symbol"].strip().upper(),
            "company": u.opt_str(r.get("companyName")), "regulation": u.opt_str(r.get("regulation")),
            "submission_type": u.opt_str(r.get("typeOfSubmission")), "revision_remark": u.opt_str(r.get("revisionRemark")),
            "broadcast": _broadcast(r["broadcastDateTime"]), "xml_url": r["xmlFileName"], "ixbrl_url": u.opt_str(r.get("ixbrl")),
        })
    u.require_rows(out, label)
    return out


def _broadcast(v: str) -> str:
    for fmt in ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M"):
        try:
            return datetime.strptime(v.strip().title(), fmt).isoformat(timespec="seconds")
        except ValueError:
            continue
    raise DrishtiError(ErrorKind.PARSE_FAIL, f"pit_list: cannot read broadcast time {v!r}")


def parse_pit_filing(xml: bytes) -> list[dict]:
    """One filing's XBRL XML -> one event per disclosure (a filing can cover several persons)."""
    label = "pit_filing"
    ctx, version = u.xbrl_facts(xml, "co", label)
    main = ctx.get("MainI")
    if not main:
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: no filing-level (MainI) facts")
    isin = normalize_isin(main.get("ISINCode"))
    if not main.get("Symbol") and not main.get("ScripCode"):
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: filing names neither a symbol nor a scrip code")
    filing = {
        "isin": isin if is_valid_isin(isin) else None, "isin_raw": isin or None, "isin_valid": is_valid_isin(isin), "symbol": u.opt_str(main.get("Symbol")),
        "scrip_code": u.opt_str(main.get("ScripCode")), "company": u.opt_str(main.get("NameOfTheCompany")),
        "filing_date": u.iso_date(main["DateOfFiling"], f"{label}.DateOfFiling") if main.get("DateOfFiling") else None,
        "regulation": u.opt_str(main.get("DisclosureUnderRegulation")),
        "revised": (main.get("RevisedFilling", "").lower() == "true"), "form_version": version,
    }
    disclosures = sorted((int(m.group(1)), c) for c in ctx if (m := _DISCLOSURE.match(c)))
    if not disclosures:
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: filing has no disclosures")
    events = []
    for _, cid in disclosures:
        raw = {key: ctx[cid][name] for name, key in _EVENT_FIELDS.items() if name in ctx[cid]}
        for k in ("category", "person", "txn_type"):
            if not raw.get(k):
                raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: {cid} has no {k}")
        ev = dict(filing)
        ev.update({
            "instrument": raw.get("instrument"), "category": raw["category"], "person": raw["person"],
            "txn_type": raw["txn_type"], "direction": _direction(raw["txn_type"]),
            "quantity": u.integer(raw.get("quantity"), "quantity"), "value_inr": u.num(raw.get("value_inr"), "value_inr"),
            "held_pre_shares": u.integer(raw.get("held_pre_shares"), "held_pre_shares"),
            "held_pre_pct": _pct(raw.get("held_pre_pct")), "held_post_shares": u.integer(raw.get("held_post_shares"), "held_post_shares"),
            "held_post_pct": _pct(raw.get("held_post_pct")),
            "from_date": _opt_date(raw.get("from_date")), "to_date": _opt_date(raw.get("to_date")),
            "mode": u.opt_str(raw.get("mode")), "intimation_date": _opt_date(raw.get("intimation_date")),
            "exchange": u.opt_str(raw.get("exchange")),
        })
        ev["holding_reconciles"] = _reconciles(ev)
        events.append(ev)
    return events


def _pct(v: str | None) -> float | None:
    f = u.num(v, "percentage")
    return None if f is None else round(f * 100, 4)  # XML holds a fraction


def _opt_date(v: str | None) -> str | None:
    return u.iso_date(v, "date") if v and v.strip() else None


def _reconciles(ev: dict) -> bool | None:
    """Does held_pre +/- quantity equal held_post? None when it cannot apply (pledges, missing numbers).
    Filers get this wrong now and then; it is a flag for the signal layer to weigh, not a reason to drop the row."""
    pre, q, post = ev["held_pre_shares"], ev["quantity"], ev["held_post_shares"]
    if None in (pre, q, post) or ev["direction"] not in ("BUY", "SELL"):
        return None
    return (pre + q if ev["direction"] == "BUY" else pre - q) == post


def dedupe_events(events: Iterable[dict]) -> list[dict]:
    """The same trade is sometimes filed twice (two appIds). Keep one per (isin/symbol, person, direction, quantity,
    value, dates, mode); the one from the latest filing wins. Order of first appearance is preserved."""
    best: dict[tuple, dict] = {}
    for e in events:
        key = (e.get("isin") or e.get("symbol"), e["person"].upper(), e["direction"], e["quantity"], e["value_inr"],
               e["from_date"], e["to_date"], e["mode"])
        cur = best.get(key)
        if cur is None or (e.get("filed_at") or "") > (cur.get("filed_at") or ""):
            best[key] = e
    return list(best.values())


def _dmy(d: date) -> str:
    return d.strftime("%d-%m-%Y")


@guard
def insider_trades(days: int = 7, *, today: date | None = None, max_filings: int = 300, skip_app_ids=(),
                   max_failed_frac: float = 0.2, max_age_days: int = 5) -> Result:
    """Insider trades filed in the last `days` days. data = {"events": [...], "filings": n, "failed": [...],
    "duplicates_dropped": n}. Partial failure is never hidden: failed filings are listed with their typed kind, and
    the Result itself is an error if more than `max_failed_frac` of the filings failed (a layout change looks
    like that). `skip_app_ids`: filings already stored, so a daily job only downloads new ones."""
    today = today or today_ist()
    got = nse_live.api_get("corporates-pit-gg", index="equities", from_date=_dmy(today - timedelta(days=days)), to_date=_dmy(today))
    listing = parse_pit_list(got.body)
    newest = check_fresh(listing, "broadcast", max_age_days, "pit_list", today=today)  # frozen endpoint = STALE, not "no trades"
    superseded = {r["prev_app_id"] for r in listing if r["prev_app_id"]}
    skip = set(skip_app_ids)
    todo = [r for r in sorted(listing, key=lambda r: r["broadcast"], reverse=True)
            if r["app_id"] not in superseded and r["app_id"] not in skip][:max_filings]
    events, failed = [], []
    for r in todo:
        try:
            x = get(r["xml_url"], NSE_ARCHIVE, expect="xml")
            for ev in parse_pit_filing(x.body):
                ev.update(app_id=r["app_id"], filed_at=r["broadcast"], submission_type=r["submission_type"])
                events.append(ev)
        except DrishtiError as e:
            failed.append({"app_id": r["app_id"], "symbol": r["symbol"], "kind": str(e.kind), "detail": e.detail[:160]})
    deduped = dedupe_events(events)
    data = {"events": deduped, "filings": len(todo), "failed": failed, "duplicates_dropped": len(events) - len(deduped)}
    srcs = [got.source(SOURCE)]
    if todo and len(failed) / len(todo) > max_failed_frac:
        kinds = sorted({f["kind"] for f in failed})
        return Result(data=data, as_of=newest.isoformat(), sources=srcs,
                      error=DrishtiError(ErrorKind(kinds[0]) if len(kinds) == 1 else ErrorKind.PARSE_FAIL,
                                         f"pit: {len(failed)}/{len(todo)} filings failed ({', '.join(kinds)})"))
    return Result(data=data, as_of=newest.isoformat(), sources=srcs)

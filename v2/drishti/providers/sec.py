"""SEC EDGAR (L4-17, L2-12): US company filings, XBRL fundamentals and 13F holdings. Official, free, keyless.

SEC asks every client to identify itself with contact details in the User-Agent and to stay under 10 requests a
second (the rate limiter's sec.gov slot). The UA comes from settings.toml `sec_user_agent`, never from code; without it
every call is a typed NOT_FOUND that says how to set it.

Fundamentals are point-in-time: every XBRL fact carries the date it was filed, so `as_of` returns only what had been
filed by then, and a figure restated later does not leak into the past.
"""
from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from datetime import date

from ..core.envelope import DrishtiError, ErrorKind, Result, guard
from ..core.fetch import Profile, get

SOURCE = "sec_edgar"
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
INDEX_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/index.json"
DOC_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{name}"

# metric -> us-gaap tags in order of preference (companies use different ones for the same line)
TAGS = {
    "revenue": ("RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet",
                "RevenueFromContractWithCustomerIncludingAssessedTax"),
    "operating_income": ("OperatingIncomeLoss",),
    "net_income": ("NetIncomeLoss", "ProfitLoss"),
    "eps_diluted": ("EarningsPerShareDiluted",),
    "ocf": ("NetCashProvidedByUsedInOperatingActivities",),
    "capex": ("PaymentsToAcquirePropertyPlantAndEquipment",),
    "assets": ("Assets",),
    "liabilities": ("Liabilities",),
    "equity": ("StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"),
    "cash": ("CashAndCashEquivalentsAtCarryingValue",),
    "long_term_debt": ("LongTermDebt", "LongTermDebtNoncurrent"),
}
FLOW = {"revenue", "operating_income", "net_income", "eps_diluted", "ocf", "capex"}   # period figures; the rest are balances


def profile(user_agent: str | None) -> Profile:
    ua = (user_agent or "").strip()
    if not ua or "@" not in ua:
        raise DrishtiError(ErrorKind.NOT_FOUND, "SEC needs contact details in the User-Agent: set sec_user_agent = \"Your Name "
                                                "you@example.com\" in settings.toml (see `drishti paths`)")
    return Profile("sec", {"User-Agent": ua, "Accept": "application/json, text/xml, */*", "Accept-Encoding": "gzip, deflate"},
                   timeout=(5.0, 60.0))


def _json(body: bytes, label: str):
    try:
        return json.loads(body)
    except (ValueError, UnicodeDecodeError) as e:
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: not JSON ({type(e).__name__})") from e


# --- tickers -> CIK ----------------------------------------------------------------------------------------------
def parse_tickers(body: bytes) -> list[dict]:
    doc = _json(body, "sec_tickers")
    rows = list(doc.values()) if isinstance(doc, dict) else None
    if not rows or not all(isinstance(r, dict) and {"cik_str", "ticker", "title"} <= set(r) for r in rows):
        raise DrishtiError(ErrorKind.PARSE_FAIL, "sec_tickers: layout changed")
    if len(rows) < 5000:
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"sec_tickers: only {len(rows)} companies (expected ~10,000)")
    return [{"ticker": str(r["ticker"]).upper(), "cik": int(r["cik_str"]), "name": r["title"]} for r in rows]


@guard
def tickers(*, user_agent: str | None) -> Result:
    got = get(TICKERS_URL, profile(user_agent), expect="json")
    return Result(data=parse_tickers(got.body), as_of=got.fetched_at.date().isoformat(), sources=[got.source(SOURCE)])


# --- submissions: company facts and recent filings ------------------------------------------------------------------
def parse_submissions(body: bytes) -> dict:
    label = "sec_submissions"
    d = _json(body, label)
    try:
        r = d["filings"]["recent"]
        n = len(r["accessionNumber"])
        cik = int(d["cik"])
    except (KeyError, TypeError, ValueError) as e:
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: layout changed ({type(e).__name__})") from e
    cols = ("accessionNumber", "filingDate", "reportDate", "form", "primaryDocument", "primaryDocDescription")
    if any(len(r.get(c, [])) != n for c in cols):
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: filing columns of unequal length")
    filings = []
    for i in range(n):
        acc = r["accessionNumber"][i]
        filings.append({"accession": acc, "form": r["form"][i], "filed": r["filingDate"][i], "report_date": r["reportDate"][i] or None,
                        "description": r["primaryDocDescription"][i] or None,
                        "url": DOC_URL.format(cik=cik, acc=acc.replace("-", ""), name=r["primaryDocument"][i]) if r["primaryDocument"][i] else None})
    return {"cik": cik, "name": d.get("name"), "tickers": [t.upper() for t in d.get("tickers") or []], "exchanges": d.get("exchanges") or [],
            "sic": d.get("sic"), "industry": d.get("sicDescription"), "fiscal_year_end": d.get("fiscalYearEnd"),
            "category": d.get("category"), "filings": filings}


@guard
def submissions(cik: int, *, user_agent: str | None) -> Result:
    got = get(SUBMISSIONS_URL.format(cik=int(cik)), profile(user_agent), expect="json")
    d = parse_submissions(got.body)
    return Result(data=d, as_of=d["filings"][0]["filed"] if d["filings"] else None, sources=[got.source(SOURCE)])


# --- XBRL company facts -> point-in-time fundamentals --------------------------------------------------------------
def _days(a: str, b: str) -> int:
    return (date.fromisoformat(b) - date.fromisoformat(a)).days


def _series(facts: dict, metric: str, as_of: str | None) -> dict:
    """{'annual': [...], 'quarterly': [...]} for one metric, each point the FIRST filing of that period's value that
    was filed by `as_of` (originals, not later restatements, so history stays as it was known)."""
    gaap = facts.get("us-gaap", {})
    for tag in TAGS[metric]:
        if tag not in gaap:
            continue
        units = gaap[tag]["units"]
        unit = "USD/shares" if metric == "eps_diluted" else "USD"
        pts = [p for p in units.get(unit, []) if p.get("form", "").startswith(("10-K", "10-Q", "20-F", "40-F"))
               and (as_of is None or p["filed"] <= as_of)]
        if not pts:
            continue
        ann, qtr = {}, {}
        for p in sorted(pts, key=lambda p: p["filed"]):
            if metric in FLOW:
                span = _days(p["start"], p["end"]) if p.get("start") else None
                bucket = ann if span and 350 <= span <= 380 else qtr if span and 80 <= span <= 100 else None
            else:
                bucket = ann if p.get("fp") == "FY" else qtr
            if bucket is not None and p["end"] not in bucket:
                bucket[p["end"]] = {"end": p["end"], "value": p["val"], "filed": p["filed"], "form": p["form"], "tag": tag}
        if ann or qtr:
            return {"annual": [ann[k] for k in sorted(ann)], "quarterly": [qtr[k] for k in sorted(qtr)]}
    return {"annual": [], "quarterly": []}


def parse_companyfacts(body: bytes, *, as_of: str | None = None) -> dict:
    label = "sec_companyfacts"
    d = _json(body, label)
    if not isinstance(d, dict) or "facts" not in d or "cik" not in d:
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: layout changed")
    series = {m: _series(d["facts"], m, as_of) for m in TAGS}
    if not series["revenue"]["annual"] and not series["net_income"]["annual"]:
        raise DrishtiError(ErrorKind.EMPTY, f"{label}: no annual revenue or net income filed{' by ' + as_of if as_of else ''}")
    return {"cik": int(d["cik"]), "name": d.get("entityName"), "as_of": as_of, "series": series, "quality": quality(series)}


def quality(series: dict) -> dict:
    """Same floor inputs as the Indian quality(): growth, margins, returns, leverage, cash conversion. None, never 0,
    when a line is missing."""
    def last(m, k=1, kind="annual"):
        xs = series[m][kind]
        return xs[-k]["value"] if len(xs) >= k else None

    def cagr(m, years):
        xs = series[m]["annual"]
        if len(xs) <= years:
            return None
        a, b = xs[-1 - years]["value"], xs[-1]["value"]
        return round(((b / a) ** (1 / years) - 1) * 100, 2) if a and a > 0 and b > 0 else None

    rev, ni, eq, ocf, capex = last("revenue"), last("net_income"), last("equity"), last("ocf"), last("capex")
    debt = last("long_term_debt")
    ocf3 = [x["value"] for x in series["ocf"]["annual"][-3:]]
    ni3 = [x["value"] for x in series["net_income"]["annual"][-3:]]
    return {"revenue_cagr_3y_pct": cagr("revenue", 3), "net_income_cagr_3y_pct": cagr("net_income", 3),
            "net_margin_pct": round(ni / rev * 100, 2) if ni is not None and rev else None,
            "roe_pct": round(ni / eq * 100, 2) if ni is not None and eq and eq > 0 else None,
            "debt_to_equity": round(debt / eq, 2) if debt is not None and eq and eq > 0 else None,
            "ocf_to_net_income_3y": round(sum(ocf3) / sum(ni3), 2) if len(ocf3) == 3 and len(ni3) == 3 and sum(ni3) > 0 else None,
            "free_cash_flow": ocf - capex if ocf is not None and capex is not None else None,
            "latest_annual_period": series["revenue"]["annual"][-1]["end"] if series["revenue"]["annual"] else None,
            "latest_quarter": max((x["end"] for m in ("revenue", "net_income") for x in series[m]["quarterly"]), default=None)}


@guard
def companyfacts(cik: int, *, user_agent: str | None, as_of: str | None = None) -> Result:
    got = get(FACTS_URL.format(cik=int(cik)), profile(user_agent), expect="json")
    d = parse_companyfacts(got.body, as_of=as_of)
    return Result(data=d, as_of=d["quality"]["latest_quarter"] or d["quality"]["latest_annual_period"], sources=[got.source(SOURCE)])


# --- 13F holdings ------------------------------------------------------------------------------------------------
_NS = re.compile(r"\{[^}]*\}")


def parse_13f_infotable(xml: bytes) -> list[dict]:
    """A 13F information table. Values are in US dollars for filings from 2023 on (SEC's change from thousands)."""
    label = "sec_13f"
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as e:
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: not XML ({e})") from e
    out = []
    for it in root.iter():
        if _NS.sub("", it.tag) != "infoTable":
            continue
        f = {}
        for el in it.iter():
            f.setdefault(_NS.sub("", el.tag), (el.text or "").strip())
        try:
            out.append({"issuer": f["nameOfIssuer"], "class": f.get("titleOfClass"), "cusip": f["cusip"].upper(), "value_usd": int(f["value"]),
                        "shares": int(f["sshPrnamt"]), "share_type": f.get("sshPrnamtType"), "put_call": f.get("putCall") or None,
                        "discretion": f.get("investmentDiscretion")})
        except (KeyError, ValueError) as e:
            raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: holding without {e}") from e
    if not out:
        raise DrishtiError(ErrorKind.EMPTY, f"{label}: no holdings in the information table")
    return out


def pick_infotable(index_body: bytes) -> str:
    items = (_json(index_body, "sec_13f_index").get("directory") or {}).get("item") or []
    names = [i.get("name", "") for i in items]
    xml = [n for n in names if n.lower().endswith(".xml") and "primary_doc" not in n.lower()]
    if not xml:
        raise DrishtiError(ErrorKind.NOT_FOUND, f"sec_13f: no information-table XML in the filing ({names[:6]})")
    return next((n for n in xml if "info" in n.lower()), xml[0])


@guard
def holdings_13f(cik: int, accession: str, *, user_agent: str | None) -> Result:
    prof = profile(user_agent)
    acc = accession.replace("-", "")
    idx = get(INDEX_URL.format(cik=int(cik), acc=acc), prof, expect="json")
    name = pick_infotable(idx.body)
    got = get(DOC_URL.format(cik=int(cik), acc=acc, name=name), prof, expect="xml")
    rows = parse_13f_infotable(got.body)
    return Result(data={"cik": int(cik), "accession": accession, "holdings": rows, "total_usd": sum(r["value_usd"] for r in rows)},
                  sources=[idx.source(SOURCE), got.source(SOURCE)])

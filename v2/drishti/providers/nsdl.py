"""NSDL FPI daily trends (L2-2): confirmed FPI investment by asset class and route, from custodian reports.

NSE's FII/DII cash figure (nse_flows) is provisional and same-day; this is the confirmed figure, reported the next day
for the previous trading day(s), and it splits equity from debt and the primary market from the exchange. Every
sub-total and the grand total are checked against their parts and net = purchases - sales, so a mangled page cannot
pass as numbers.
"""
from __future__ import annotations

import re
from datetime import date
from html.parser import HTMLParser

from ..core.canary import check_fresh, today_ist
from ..core.envelope import DrishtiError, ErrorKind, Result, guard
from ..core.fetch import get
from . import _util as u
from .profiles import NSDL

SOURCE = "nsdl_fpi"
LATEST_URL = "https://www.fpi.nsdl.co.in/web/Reports/Latest.aspx"
TOL = 0.06            # crore: the page rounds each figure to 2 decimals


class _Tables(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[str]]] = []
        self._depth = 0
        self._row: list[str] | None = None
        self._cell: str | None = None

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._depth += 1
            self.tables.append([])
        elif tag == "tr" and self._depth:
            self._flush()              # NSDL opens rows without closing the previous one
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = ""
        elif tag == "br" and self._cell is not None:
            self._cell += " "

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append(re.sub(r"\s+", " ", self._cell).strip())
            self._cell = None
        elif tag == "tr":
            self._flush()
        elif tag == "table" and self._depth:
            self._flush()
            self._depth -= 1

    def _flush(self):
        if self._cell is not None and self._row is not None:
            self._row.append(re.sub(r"\s+", " ", self._cell).strip())
            self._cell = None
        if self._row and self.tables:
            self.tables[-1].append(self._row)
        self._row = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell += data


def _amt(s: str, where: str) -> float:
    """'16168.66' / '(490.00)' (negative) / '1,234.5' -> float."""
    t = s.replace(",", "").strip()
    neg = t.startswith("(") and t.endswith(")")
    v = u.num(t.strip("()"), where)
    if v is None:
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"{where}: not a number {s!r}")
    return -v if neg else v


_DATE = re.compile(r"^\d{2}-[A-Za-z]{3}-\d{4}$")


def _investment(rows: list[list[str]], label: str) -> dict:
    """The investment table: rowspans mean the date and the asset class appear only on their first row."""
    out, category, day, usd_inr = [], None, None, None
    total = None
    for r in rows:
        cells = [c for c in r if c != ""]
        if not cells:
            continue
        if cells and cells[0] == "Total" and len(cells) >= 5:
            total = dict(zip(("gross_buy", "gross_sell", "net", "net_usd_mn"), (_amt(c, label) for c in cells[1:5])))
            continue
        if not any(re.fullmatch(r"\(?-?[\d,]+\.\d+\)?", c) for c in cells):
            continue                       # headings and notes
        if _DATE.match(cells[0]):
            day = u.iso_date(cells[0], f"{label}.date")
            cells = cells[1:]
        if cells and cells[-1].startswith("Rs."):
            usd_inr = u.num(cells[-1][3:], "usd_inr")
            cells = cells[:-1]
        if len(cells) == 6:
            category, cells = cells[0], cells[1:]
        if len(cells) != 5 or category is None:
            raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: unexpected row {r}")
        route = cells[0]
        gb, gs, net, usd = (_amt(c, f"{label}.{category}.{route}") for c in cells[1:])
        out.append({"category": category, "route": route, "gross_buy": gb, "gross_sell": gs, "net": net, "net_usd_mn": usd})
    if day is None or total is None or not out:
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: no reporting date / total row; layout changed?")
    # integrity: net = buy - sell on every row; each Sub-total = its routes; Total = the sub-totals
    for x in out:
        if abs(x["gross_buy"] - x["gross_sell"] - x["net"]) > TOL:
            raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: {x['category']} / {x['route']} net {x['net']} != buy - sell")
    subs = [x for x in out if x["route"] == "Sub-total"]
    for s in subs:
        parts = [x for x in out if x["category"] == s["category"] and x["route"] != "Sub-total"]
        for k in ("gross_buy", "gross_sell", "net"):
            if abs(sum(p[k] for p in parts) - s[k]) > TOL * max(1, len(parts)):
                raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: {s['category']} sub-total {k} does not add up")
    for k in ("gross_buy", "gross_sell", "net"):
        if abs(sum(s[k] for s in subs) - total[k]) > TOL * max(1, len(subs)):
            raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: total {k} {total[k]} != sum of sub-totals")
    return {"date": day, "usd_inr": usd_inr, "rows": out, "total": total,
            "equity_net_cr": next((s["net"] for s in subs if s["category"] == "Equity"), None),
            "equity_exchange_net_cr": next((x["net"] for x in out if x["category"] == "Equity" and x["route"] == "Stock Exchange"), None)}


def _derivatives(rows: list[list[str]], label: str) -> tuple[str | None, list[dict]]:
    day, out = None, []
    for r in rows:
        cells = [c for c in r if c != ""]
        if cells and _DATE.match(cells[0]):
            day = u.iso_date(cells[0], f"{label}.date")
            cells = cells[1:]
        if len(cells) == 7 and not re.fullmatch(r"[\d.,()-]+", cells[0]) and all(re.fullmatch(r"[\d.,]+", c) for c in cells[1:]):
            b_n, b_a, s_n, s_a, oi_n, oi_a = (_amt(c, f"{label}.{cells[0]}") for c in cells[1:])
            out.append({"product": cells[0], "buy_contracts": int(b_n), "buy_cr": b_a, "sell_contracts": int(s_n), "sell_cr": s_a,
                        "oi_contracts": int(oi_n), "oi_cr": oi_a})
    return day, out


def parse_latest(html: bytes | str) -> dict:
    label = "nsdl_fpi"
    text = html.decode("utf-8", errors="replace") if isinstance(html, bytes) else html
    if "Daily Trends in FPI Investments" not in text:
        raise DrishtiError(ErrorKind.BLOCKED_HTML if "<html" in text.lower()[:2000] else ErrorKind.PARSE_FAIL,
                           f"{label}: no 'Daily Trends in FPI Investments' table; blocked or layout changed?")
    p = _Tables()
    p.feed(text)
    inv = next((t for t in p.tables if any("Gross Purchases" in " ".join(r) for r in t)), None)
    der = next((t for t in p.tables if any("Derivative Products" in " ".join(r) for r in t)), None)
    if inv is None:
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: investment table not found")
    out = _investment(inv, label)
    d_day, d_rows = _derivatives(der or [], label + "_derivatives")
    if der is not None and not d_rows:
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: derivatives table has no rows")
    out["derivatives"] = d_rows
    out["derivatives_date"] = d_day
    return out


@guard
def fpi_daily(*, today: date | None = None, max_age_days: int = 5) -> Result:
    """The latest NSDL daily FPI report (previous trading day). STALE if its date is more than `max_age_days` old."""
    today = today or today_ist()
    got = get(LATEST_URL, NSDL, expect="html")
    data = parse_latest(got.body)
    newest = check_fresh([data], "date", max_age_days, "nsdl_fpi", today=today)
    return Result(data=data, as_of=newest.isoformat(), sources=[got.source(SOURCE)])

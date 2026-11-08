"""Screener.in company pages: quarterly results, annual P&L, balance sheet, cash flow, ratios, shareholding, headline
ratios, and links to concall transcripts / presentations / annual reports (L4-5, L4-6).

Parsed into typed tables (period -> value per line item) with stdlib html.parser. Screener is a website, not an API: the
parser checks every section it depends on and refuses a page whose layout moved (typed PARSE_FAIL), so a redesign shows
up as an error instead of empty fundamentals. Figures are Rs crore unless the row says %, as Screener presents them.
"""
from __future__ import annotations

import json
import re
from datetime import date, timedelta
from html import unescape
from html.parser import HTMLParser
from urllib.parse import unquote, urlsplit

from ..core.canary import today_ist
from ..core.envelope import DrishtiError, ErrorKind, Result, guard
from ..core.fetch import get
from .profiles import SCREENER

SOURCE = "screener"
BASE = "https://www.screener.in"
SECTIONS = ("quarters", "profit-loss", "balance-sheet", "cash-flow", "ratios")
SHP_TABS = ("quarterly-shp", "yearly-shp")
_NUM = re.compile(r"^-?[\d,]*\.?\d+$")
_SLUG = re.compile(r"^[A-Za-z0-9&._-]{1,30}$")


def _num(s: str):
    t = s.replace("₹", "").replace("%", "").replace("Cr.", "").replace(",", "").strip()
    if t in ("", "-", "--"):
        return None
    if _NUM.match(t):
        return float(t)
    return s.strip()


class _Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.section = None          # current <section id>
        self.tab = None              # current shareholding tab div id
        self.tables: dict[str, dict] = {}
        self._t = None               # table being built
        self._row = None
        self._cell = None
        self._cell_attrs = {}
        self._in_head = False
        self.ratios: list[dict] = []
        self._li = None
        self._in_top = False
        self._span_class = None
        self.links: list[dict] = []
        self._a = None
        self.h1 = ""
        self._in_h1 = False
        self.sub_texts: list[str] = []
        self._in_sub = False
        self._concall_month = None
        self._in_month = False
        self._h3 = ""
        self._in_h3 = False

    # --- tags
    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "section" and a.get("id"):
            self.section, self.tab = a["id"], None
        if tag == "div" and a.get("id") in SHP_TABS:
            self.tab = a["id"]
        if tag == "ul" and a.get("id") == "top-ratios":
            self._in_top = True
        if self._in_top and tag == "li":
            self._li = {"name": "", "value": "", "numbers": []}
        if self._li is not None and tag == "span":
            self._span_class = a.get("class", "")
        if tag == "h1" and not self.h1.strip():      # the page repeats the name in a second heading; keep the first
            self._in_h1 = True
        if tag == "h3":
            self._in_h3, self._h3 = True, ""
        if tag == "p" and "sub" in a.get("class", "").split():
            self._in_sub = True
            self.sub_texts.append("")
        if tag == "div" and "nowrap" in a.get("class", "") and self.section == "documents":
            self._in_month = True
            self._concall_month = ""
        if tag == "a" and a.get("href"):
            self._a = {"href": a["href"], "text": "", "section": self.section, "h3": self._h3.strip(), "month": self._concall_month,
                       "title": a.get("title")}
        key = self.tab or self.section
        if tag == "table" and key in SECTIONS + SHP_TABS and key not in self.tables and self._t is None:
            self._t = {"key": key, "head": [], "rows": []}
        if self._t is not None:
            if tag == "thead":
                self._in_head = True
            elif tag == "tr":
                self._row = []
            elif tag in ("th", "td"):
                self._cell, self._cell_attrs = "", a

    def handle_endtag(self, tag):
        if self._t is not None:
            if tag in ("th", "td") and self._cell is not None:
                txt = re.sub(r"\s+", " ", unescape(self._cell)).strip()
                if self._in_head:
                    self._t["head"].append(self._cell_attrs.get("data-date-key") or txt)
                else:
                    self._row.append(txt)
                self._cell = None
            elif tag == "tr" and self._row is not None:
                if not self._in_head and self._row:
                    self._t["rows"].append(self._row)
                self._row = None
            elif tag == "thead":
                self._in_head = False
            elif tag == "table":
                self.tables[self._t["key"]] = self._t
                self._t = None
        if tag == "ul" and self._in_top:
            self._in_top = False
        if tag == "li" and self._li is not None:
            self.ratios.append(self._li)
            self._li = None
        if tag == "span":
            self._span_class = None
        if tag == "h1":
            self._in_h1 = False
        if tag == "h3":
            self._in_h3 = False
        if tag == "p":
            self._in_sub = False
        if tag == "div" and self._in_month:
            self._in_month = False
        if tag == "a" and self._a is not None:
            self._a["text"] = re.sub(r"\s+", " ", self._a["text"]).strip()
            self.links.append(self._a)
            self._a = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell += data
        if self._li is not None:
            cls = self._span_class or ""
            if "name" in cls.split():
                self._li["name"] += data
            elif "number" in cls.split():
                self._li["numbers"].append(data.strip())
                self._li["value"] += data
            else:
                self._li["value"] += data
        if self._in_h1:
            self.h1 += data
        if self._in_h3:
            self._h3 += data
        if self._in_sub and self.sub_texts:
            self.sub_texts[-1] += data
        if self._in_month:
            self._concall_month += data
        if self._a is not None:
            self._a["text"] += data


def _table(t: dict, label: str, optional: bool = False) -> dict:
    head = t["head"][1:]                    # first header cell is the row-label column
    if not head and optional:
        return {"periods": [], "rows": {}}
    if not head:
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: {t['key']} table has no period columns")
    rows = {}
    for r in t["rows"]:
        name = re.sub(r"\s*\+$", "", r[0]).strip()
        if not name:
            continue
        vals = r[1:]
        if len(vals) != len(head):
            raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: {t['key']} row {name!r} has {len(vals)} cells for {len(head)} periods")
        rows[name] = [_num(v) for v in vals]
    if not rows:
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: {t['key']} table has no rows")
    return {"periods": head, "rows": rows}


def _nse_symbol(href: str) -> str | None:
    """The symbol from an NSE quote link. Parsed as a query string so 'M%26M' comes back as 'M&M', not 'M'."""
    m = re.search(r"(?:^|&)symbol=(.+?)(?=&\w+=|$)", urlsplit(href).query)   # 'M&M' may come literal or as 'M%26M'
    sym = unquote(m.group(1)).strip().upper() if m else ""
    return sym if re.fullmatch(r"[A-Z0-9&\-]{1,20}", sym) else None


def _direct_pdf(url: str) -> str:
    m = re.search(r"AnnPdfOpen\.aspx\?Pname=([^&]+)", url, re.I)
    return f"https://www.bseindia.com/xml-data/corpfiling/AttachHis/{m.group(1)}" if m else url


def parse_company(html: bytes | str) -> dict:
    label = "screener"
    text = html.decode("utf-8", errors="replace") if isinstance(html, bytes) else html
    if "<section" not in text or 'id="top-ratios"' not in text:
        raise DrishtiError(ErrorKind.BLOCKED_HTML if "captcha" in text.lower() or "<title>" in text.lower()[:500] and "screener" not in text.lower()
                           else ErrorKind.PARSE_FAIL, f"{label}: not a company page (no sections / top ratios); layout changed or blocked?")
    p = _Page()
    p.feed(text)
    missing = [s for s in SECTIONS + SHP_TABS[:1] if s not in p.tables]
    if missing:
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: sections missing {missing}; Screener layout changed?")
    # SME companies report half-yearly, so Screener's quarterly table can be empty; every other table must be there.
    tables = {k: _table(v, label, optional=(k == "quarters")) for k, v in p.tables.items()}
    ratios = {}
    for li in p.ratios:
        name = re.sub(r"\s+", " ", li["name"]).strip()
        nums = [_num(x) for x in li["numbers"] if x]
        if name:
            ratios[name] = nums[0] if len(nums) == 1 else nums or re.sub(r"\s+", " ", li["value"]).strip()
    if "Market Cap" not in ratios or "Current Price" not in ratios:
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: headline ratios missing Market Cap / Current Price")
    basis = "consolidated" if any("Consolidated" in s for s in p.sub_texts) else "standalone"
    docs = {"concalls": [], "annual_reports": []}
    for a in p.links:
        if a["section"] != "documents":
            continue
        if a["h3"].startswith("Concalls") or a["text"] in ("Transcript", "PPT", "REC", "Notes"):
            docs["concalls"].append({"month": re.sub(r"\s+", " ", a["month"] or "").strip() or None, "kind": a["text"],
                                     "url": _direct_pdf(a["href"])})
        elif "Annual Report" in a["text"]:
            docs["annual_reports"].append({"label": a["text"], "url": _direct_pdf(a["href"])})
    nse = next((sym for a in p.links if "nseindia.com/get-quotes" in a["href"] for sym in [_nse_symbol(a["href"])] if sym), None)
    bse = next((re.search(r"/(\d{6})/?$", a["href"].split("?")[0]).group(1) for a in p.links
                if "bseindia.com/stock-share-price" in a["href"] and re.search(r"/(\d{6})/?$", a["href"].split("?")[0])), None)
    dated = lambda xs: [x for x in xs if re.match(r"\d{4}-\d{2}-\d{2}$", x)]  # noqa: E731
    latest_q = max(dated(tables["quarters"]["periods"]), default=None)
    out = {"company": re.sub(r"\s+", " ", p.h1).strip() or None, "basis": basis, "nse_symbol": nse, "bse_scrip": bse,
           "ratios": ratios, "tables": tables, "documents": docs, "latest_quarter": latest_q,
           "latest_annual": max(dated(tables["profit-loss"]["periods"]), default=None)}
    if latest_q is None:
        out["notes"] = ["no quarterly results on Screener (SME companies report half-yearly); use the annual tables"]
    return out


def _fetch(slug: str, consolidated: bool):
    return get(f"{BASE}/company/{slug}/{'consolidated/' if consolidated else ''}", SCREENER, expect="html")


@guard
def company(slug: str, *, consolidated: bool = True, today: date | None = None, max_quarter_age_days: int = 200) -> Result:
    """One company by Screener slug: NSE symbol (RELIANCE) or BSE code (500325). Consolidated figures when Screener has
    them, else standalone (the result says which). Refuses data whose newest quarter is older than `max_quarter_age_days`."""
    if not _SLUG.match(slug or ""):
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"screener: bad slug {slug!r}")
    today = today or today_ist()
    try:
        got = _fetch(slug, consolidated)
        data = parse_company(got.body)
    except DrishtiError as e:
        if not consolidated or e.kind not in (ErrorKind.NOT_FOUND, ErrorKind.PARSE_FAIL):
            raise
        got = _fetch(slug, False)             # some companies have no consolidated accounts
        data = parse_company(got.body)
    if consolidated and data["basis"] != "consolidated":
        data["note"] = "Screener shows standalone figures for this company"
    lq, la = data["latest_quarter"], data.get("latest_annual")
    if lq and (today - date.fromisoformat(lq)) > timedelta(days=max_quarter_age_days):
        raise DrishtiError(ErrorKind.STALE, f"screener {slug}: newest quarter {lq} is more than {max_quarter_age_days} days old")
    if not lq and not la:
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"screener {slug}: no dated quarterly or annual period")
    if not lq and (today - date.fromisoformat(la)) > timedelta(days=550):   # a year's accounts plus the filing lag
        raise DrishtiError(ErrorKind.STALE, f"screener {slug}: no quarterly results and newest annual period {la} is over 18 months old")
    return Result(data=data, as_of=lq or la, sources=[got.source(SOURCE)])


# --- a few deterministic quality metrics the rulebook asks for (scoring.md quality floor) ------------------------
def _row(t: dict, *names):
    for n in names:
        if n in t["rows"]:
            return t["rows"][n]
    return None


def quality(data: dict) -> dict:
    """ROCE, ROE, debt/equity, OCF vs net profit, sales and profit growth: the scoring.md floor inputs, from the parsed
    tables. A metric whose rows are missing is None, never 0."""
    pl, bs, cf, ra = (data["tables"][k] for k in ("profit-loss", "balance-sheet", "cash-flow", "ratios"))
    ann = [i for i, p in enumerate(pl["periods"]) if re.match(r"\d{4}-03-31$", p) or re.match(r"\d{4}-\d{2}-\d{2}$", p)]
    sales, np_ = _row(pl, "Sales", "Revenue"), _row(pl, "Net Profit")
    eq, res, borr = _row(bs, "Equity Capital"), _row(bs, "Reserves"), _row(bs, "Borrowings")
    ocf = _row(cf, "Cash from Operating Activity")
    roce = _row(ra, "ROCE %")

    def last(xs, k=1):
        vals = [x for x in (xs or []) if isinstance(x, (int, float))]
        return vals[-k] if len(vals) >= k else None

    def cagr(xs, years):
        idx = [i for i in ann if i < len(xs or []) and isinstance(xs[i], (int, float))]
        if len(idx) <= years:
            return None
        a, b = xs[idx[-1 - years]], xs[idx[-1]]
        return round(((b / a) ** (1 / years) - 1) * 100, 2) if a and a > 0 and b > 0 else None
    d_e = None
    if borr and eq and res and isinstance(last(borr), (int, float)) and last(eq) is not None and last(res) is not None:
        net_worth = last(eq) + last(res)
        d_e = round(last(borr) / net_worth, 2) if net_worth > 0 else None
    ocf_np = None
    if ocf and np_:          # line the years up by date: the P&L has a TTM column the cash-flow table does not
        npy = dict(zip(pl["periods"], np_))
        pairs = [(o, npy.get(p)) for p, o in zip(cf["periods"], ocf) if isinstance(o, (int, float)) and isinstance(npy.get(p), (int, float))][-3:]
        ocf_np = round(sum(o for o, _ in pairs) / sum(n for _, n in pairs), 2) if len(pairs) == 3 and sum(n for _, n in pairs) > 0 else None
    roe = data["ratios"].get("ROE")
    if is_lender(data):     # banks / NBFCs: borrowing is raw material and lending is operating cash outflow
        return {"lender": True, "roce_pct": None, "roe_pct": roe if isinstance(roe, (int, float)) else None, "debt_to_equity": None,
                "ocf_to_net_profit_3y": None, "sales_cagr_3y_pct": cagr(sales, 3), "profit_cagr_3y_pct": cagr(np_, 3),
                "pe": data["ratios"].get("Stock P/E") if isinstance(data["ratios"].get("Stock P/E"), (int, float)) else None,
                "pb": _pb(data),
                "note": "Bank/NBFC: ROCE, debt/equity and operating cash flow do not apply (borrowing is raw material, "
                        "lending shows as operating outflow). Judge by ROE, growth, price/book and asset quality (GNPA/NNPA)."}
    return {"lender": False, "roce_pct": last(roce), "roe_pct": roe if isinstance(roe, (int, float)) else None, "debt_to_equity": d_e,
            "ocf_to_net_profit_3y": ocf_np, "sales_cagr_3y_pct": cagr(sales, 3), "profit_cagr_3y_pct": cagr(np_, 3),
            "pe": data["ratios"].get("Stock P/E") if isinstance(data["ratios"].get("Stock P/E"), (int, float)) else None}


def is_lender(data: dict) -> bool:
    """Screener lays out banks and NBFCs differently: 'Revenue' / 'Financing Profit' instead of Sales / Operating Profit,
    and a balance sheet with Deposits or Borrowing instead of Borrowings."""
    t = data["tables"]
    rows = set(t["quarters"]["rows"]) | set(t["profit-loss"]["rows"]) | set(t["balance-sheet"]["rows"])
    return bool({"Financing Profit", "Financing Margin %", "Deposits"} & rows) or ("Sales" not in rows and "Revenue" in rows
                                                                                   and "Interest" in rows and "Borrowings" not in rows)


def _pb(data: dict):
    price, bv = data["ratios"].get("Current Price"), data["ratios"].get("Book Value")
    return round(price / bv, 2) if isinstance(price, (int, float)) and isinstance(bv, (int, float)) and bv > 0 else None


def as_json(data: dict) -> str:
    return json.dumps(data, default=str)

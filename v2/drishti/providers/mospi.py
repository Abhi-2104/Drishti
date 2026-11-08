"""India macro from MoSPI eSankhyiki (api.mospi.gov.in): CPI (base 2024), WPI (base 2022-23), IIP (base 2011-12), RBI reserves.

Public JSON, no key; the host needs legacy TLS renegotiation (profile MOSPI), certificates stay verified. Every response is
`{data: [...], meta_data: {...}, msg, statusCode}`. Rules kept here:
  * "No Data Found" is EMPTY, never an empty list that looks like a quiet month.
  * A reading is dated by the END of its reference month. When it was *published* is a separate fact (see the release-lag
    parameters in signals/params.toml): a September CPI is not known on 30 September.
  * The newest month must be recent enough for the series' release cadence, else STALE with the month named.
    IIP currently ends in March 2026 and the RBI monthly reserves in June 2025: both come back STALE, not as old numbers.
  * The first page of a year is newest-month-first with the All India headline rows leading; if a headline row is not there,
    that is PARSE_FAIL (the ordering we rely on changed), not a guess.
"""
from __future__ import annotations

import calendar
import json
from datetime import date

from ..core.canary import check_fresh, today_ist
from ..core.envelope import DrishtiError, ErrorKind, Result, guard
from ..core.fetch import get
from . import _util as u
from .profiles import MOSPI

SOURCE = "mospi"
BASE = "https://api.mospi.gov.in/api"
CPI_URL = BASE + "/cpi/getCPIData?base_year=2024&series=Current&year={year}&limit=100&Format=JSON"
WPI_URL = BASE + "/wpi/getWpiRecords?base_year=2022-23&year={year}&limit=100&Format=JSON"
# IIP was rebased: base 2011-12 stops at March 2026, base 2022-23 runs to the latest month (confirmed 8 Nov 2026: Sep 2026 index 124.3, +6.7%).
IIP_URL = BASE + "/iip/getIipData?base_year=2022-23&frequency=Monthly&year={year}&limit=100&Format=JSON"
# One reference month, for history. CPI: All India + Combined only (the codes are MoSPI's own: state_code 1 = All India, sector_code 3 = Combined).
CPI_MONTH_URL = BASE + "/cpi/getCPIData?base_year=2024&series=Current&year={year}&month_code={month}&state_code=1&sector_code=3&limit=100&Format=JSON"
WPI_MONTH_URL = BASE + "/wpi/getWpiRecords?base_year=2022-23&year={year}&month_code={month}&limit=100&Format=JSON"
IIP_MONTH_URL = BASE + "/iip/getIipData?base_year=2022-23&frequency=Monthly&year={year}&month_code={month}&limit=100&Format=JSON"
RBI_URL = BASE + "/rbi/getRbiRecords?sub_indicator_code=47&year={year}&limit=100&Format=JSON"

# max age (calendar days) of the newest reference-month END. CPI comes out about the 12th of the next month, WPI the 14th, IIP the 28th.
MAX_AGE = {"cpi": 75, "wpi": 75, "iip": 80, "rbi_reserves": 110}
MONTHS = {m: i for i, m in enumerate(calendar.month_name) if m}

SERIES = {
    "cpi_index": "mospi:cpi_index", "cpi_yoy": "mospi:cpi_yoy", "cpi_food_yoy": "mospi:cpi_food_yoy",
    "wpi_index": "mospi:wpi_index", "wpi_yoy": "mospi:wpi_yoy", "iip_index": "mospi:iip_index", "iip_yoy": "mospi:iip_yoy",
    "fx_reserves_usd_mn": "mospi:fx_reserves_usd_mn",
}


# --- shared ------------------------------------------------------------------------------------------
def _data(body: bytes, label: str) -> list[dict]:
    try:
        doc = json.loads(u.decode(body))
    except ValueError:
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: not JSON (first bytes {body[:60]!r})") from None
    if not isinstance(doc, dict) or "data" not in doc:
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: unexpected shape {str(doc)[:120]}")
    if doc.get("statusCode") is False:
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: the API reported failure: {str(doc.get('msg'))[:120]}")
    data = doc["data"]
    if not isinstance(data, list):
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: data is {type(data).__name__}, expected a list")
    if not data:
        raise DrishtiError(ErrorKind.EMPTY, f"{label}: {doc.get('msg') or 'no rows'}")
    return data


def _need(r: dict, keys: tuple[str, ...], label: str) -> None:
    for k in keys:
        if k not in r:
            raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: row lacks {k}; layout changed? {str(r)[:140]}")


def _month_end(year, month, label: str) -> tuple[str, str]:
    """('2026-09', '2026-09-30') from MoSPI's year (str or int) and month name."""
    try:
        y, m = int(year), MONTHS[str(month).strip().capitalize()]
    except (ValueError, KeyError):
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: unreadable reference month {year!r}/{month!r}") from None
    if not 1990 <= y <= 2100:
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: implausible year {y}")
    return f"{y}-{m:02d}", f"{y}-{m:02d}-{calendar.monthrange(y, m)[1]:02d}"


def _value(v, label: str, lo: float, hi: float) -> float | None:
    x = u.num(v, label)
    if x is not None and not lo <= x <= hi:
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: {x} is outside {lo}..{hi}")
    return x


# --- parsers -----------------------------------------------------------------------------------------
def parse_cpi(body: bytes) -> list[dict]:
    label = "mospi_cpi"
    out = []
    for r in _data(body, label):
        _need(r, ("year", "month", "state", "sector", "division", "index", "inflation"), label)
        period, end = _month_end(r["year"], r["month"], label)
        out.append({"period": period, "obs_date": end, "state": r["state"], "sector": r["sector"], "division": r["division"],
                    "code": u.opt_str(r.get("code")), "index": _value(r["index"], f"{label}.index", 1, 10000),
                    "inflation": _value(r["inflation"], f"{label}.inflation", -50, 100)})
    return out


def parse_wpi(body: bytes) -> list[dict]:
    label = "mospi_wpi"
    out = []
    for r in _data(body, label):
        _need(r, ("year", "month", "major_group", "group", "index_value"), label)
        period, end = _month_end(r["year"], r["month"], label)
        out.append({"period": period, "obs_date": end, "major_group": r["major_group"], "group": u.opt_str(r["group"]),
                    "sub_group": u.opt_str(r.get("sub_group")), "item": u.opt_str(r.get("item")),
                    "index": _value(r["index_value"], f"{label}.index", 1, 10000)})
    return out


def parse_iip(body: bytes) -> list[dict]:
    label = "mospi_iip"
    out = []
    for r in _data(body, label):
        _need(r, ("year", "month", "type", "category", "index", "growth_rate"), label)
        period, end = _month_end(r["year"], r["month"], label)
        out.append({"period": period, "obs_date": end, "type": r["type"], "category": r["category"],
                    "sub_category": u.opt_str(r.get("sub_category")), "index": _value(r["index"], f"{label}.index", 1, 10000),
                    "growth": _value(r["growth_rate"], f"{label}.growth", -100, 200)})
    return out


def parse_rbi_reserves(body: bytes) -> list[dict]:
    label = "mospi_rbi_reserves"
    out = []
    for r in _data(body, label):
        _need(r, ("year", "month", "foreign_exchange_reserve_type", "foreign_exchange_reserve_currency", "value"), label)
        period, end = _month_end(r["year"], r["month"], label)
        out.append({"period": period, "obs_date": end, "type": r["foreign_exchange_reserve_type"].strip(),
                    "currency": r["foreign_exchange_reserve_currency"].strip(), "value": _value(r["value"], f"{label}.value", 0, 1e9)})
    return out


# --- headline picks (a missing headline row is an error, never a substitute) ------------------------------
def _pick(rows: list[dict], label: str, **want) -> dict:
    hit = [r for r in rows if all(r.get(k) == v for k, v in want.items())]
    if not hit:
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: no row matching {want}; page ordering or labels changed?")
    return hit[0]


def cpi_headline(rows: list[dict]) -> dict:
    """The newest month's All India CPI (General): Combined, with Rural and Urban beside it, and Food and beverages."""
    newest = max(r["period"] for r in rows)
    cur = [r for r in rows if r["period"] == newest and r["state"] == "All India"]
    g = {s: _pick(cur, "mospi_cpi", sector=s, division="CPI (General)") for s in ("Combined", "Rural", "Urban")}
    food = _pick(cur, "mospi_cpi", sector="Combined", division="Food and beverages")
    return {"period": newest, "obs_date": g["Combined"]["obs_date"], "combined": g["Combined"], "rural": g["Rural"], "urban": g["Urban"], "food": food}


def wpi_headline(rows: list[dict]) -> dict:
    newest = max(r["period"] for r in rows)
    return _pick([r for r in rows if r["period"] == newest], "mospi_wpi", major_group="Wholesale Price Index", group=None)


def iip_headline(rows: list[dict]) -> dict:
    gen = [r for r in rows if r["type"] == "General" and r["category"] == "General" and not r["sub_category"]]
    if not gen:
        raise DrishtiError(ErrorKind.PARSE_FAIL, "mospi_iip: no General/General row; labels changed?")
    return max(gen, key=lambda r: r["period"])


def reserves_headline(rows: list[dict]) -> dict:
    newest = max(r["period"] for r in rows)
    return _pick([r for r in rows if r["period"] == newest], "mospi_rbi_reserves", type="Total", currency="US $ Millions")


# --- providers ---------------------------------------------------------------------------------------
def _fetch_year(url: str, kind: str, parse, today: date):
    """Newest year first; a year with no rows yet (early January) falls back to the year before."""
    last = None
    for y in (today.year, today.year - 1):
        got = get(url.format(year=y), MOSPI, expect="json")
        try:
            return got, parse(got.body)
        except DrishtiError as e:
            if e.kind != ErrorKind.EMPTY:
                raise
            last = e
    raise last


def _result(kind: str, got, head: dict, series: dict[str, list[dict]], today: date, as_of: str) -> Result:
    check_fresh([{"d": as_of}], "d", MAX_AGE[kind], f"mospi_{kind}", today=today)
    return Result(data={"period": head.get("period"), "latest": head, "series": series}, as_of=as_of, sources=[got.source(SOURCE)])


@guard
def cpi(*, today: date | None = None) -> Result:
    today = today or today_ist()
    got, rows = _fetch_year(CPI_URL, "cpi", parse_cpi, today)
    h = cpi_headline(rows)
    d = h["obs_date"]
    series = {SERIES["cpi_index"]: [{"date": d, "value": h["combined"]["index"]}] if h["combined"]["index"] is not None else [],
              SERIES["cpi_yoy"]: [{"date": d, "value": h["combined"]["inflation"]}] if h["combined"]["inflation"] is not None else [],
              SERIES["cpi_food_yoy"]: [{"date": d, "value": h["food"]["inflation"]}] if h["food"]["inflation"] is not None else []}
    if not series[SERIES["cpi_yoy"]]:
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"mospi_cpi: the {h['period']} headline carries no inflation figure")
    return _result("cpi", got, {"period": h["period"], "cpi_index": h["combined"]["index"], "cpi_yoy": h["combined"]["inflation"],
                                "rural_yoy": h["rural"]["inflation"], "urban_yoy": h["urban"]["inflation"],
                                "food_yoy": h["food"]["inflation"]}, series, today, d)


@guard
def wpi(*, today: date | None = None) -> Result:
    today = today or today_ist()
    got, rows = _fetch_year(WPI_URL, "wpi", parse_wpi, today)
    h = wpi_headline(rows)
    if h["index"] is None:
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"mospi_wpi: the {h['period']} headline carries no index")
    return _result("wpi", got, {"period": h["period"], "wpi_index": h["index"]},
                   {SERIES["wpi_index"]: [{"date": h["obs_date"], "value": h["index"]}]}, today, h["obs_date"])


@guard
def iip(*, today: date | None = None) -> Result:
    today = today or today_ist()
    got, rows = _fetch_year(IIP_URL, "iip", parse_iip, today)
    gen = sorted((r for r in rows if r["type"] == "General" and r["category"] == "General" and not r["sub_category"]), key=lambda r: r["period"])
    h = iip_headline(rows)
    if h["growth"] is None:
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"mospi_iip: the {h['period']} headline carries no growth figure")
    series = {SERIES["iip_index"]: [{"date": r["obs_date"], "value": r["index"]} for r in gen if r["index"] is not None],
              SERIES["iip_yoy"]: [{"date": r["obs_date"], "value": r["growth"]} for r in gen if r["growth"] is not None]}
    return _result("iip", got, {"period": h["period"], "iip_index": h["index"], "iip_yoy": h["growth"]}, series, today, h["obs_date"])


@guard
def rbi_reserves(*, today: date | None = None) -> Result:
    today = today or today_ist()
    got, rows = _fetch_year(RBI_URL, "rbi_reserves", parse_rbi_reserves, today)
    tot = sorted((r for r in rows if r["type"] == "Total" and r["currency"] == "US $ Millions" and r["value"] is not None), key=lambda r: r["period"])
    h = reserves_headline(rows)
    return _result("rbi_reserves", got, {"period": h["period"], "fx_reserves_usd_mn": h["value"]},
                   {SERIES["fx_reserves_usd_mn"]: [{"date": r["obs_date"], "value": r["value"]} for r in tot]}, today, h["obs_date"])


# --- one month (history backfill): no freshness check, but the month returned must be the month asked for ----
def _month_rows(url: str, parse, year: int, mon: int, label: str):
    got = get(url.format(year=year, month=mon), MOSPI, expect="json")
    rows = parse(got.body)
    want = f"{year}-{mon:02d}"
    other = {r["period"] for r in rows} - {want}
    if other:
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: asked for {want}, got rows for {sorted(other)[:3]}")
    return got, rows


def _pts(series: str, date_: str, value) -> list[dict]:
    return [{"date": date_, "value": value}] if value is not None else []


@guard
def cpi_month(year: int, mon: int) -> Result:
    got, rows = _month_rows(CPI_MONTH_URL, parse_cpi, year, mon, "mospi_cpi_month")
    g = _pick([r for r in rows if r["state"] == "All India"], "mospi_cpi_month", sector="Combined", division="CPI (General)")
    food = _pick([r for r in rows if r["state"] == "All India"], "mospi_cpi_month", sector="Combined", division="Food and beverages")
    d = g["obs_date"]
    series = {SERIES["cpi_index"]: _pts("", d, g["index"]), SERIES["cpi_yoy"]: _pts("", d, g["inflation"]),
              SERIES["cpi_food_yoy"]: _pts("", d, food["inflation"])}
    if g["index"] is None:
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"mospi_cpi_month: {g['period']} headline carries no index")
    return Result(data={"period": g["period"], "series": series}, as_of=d, sources=[got.source(SOURCE)])


@guard
def wpi_month(year: int, mon: int) -> Result:
    got, rows = _month_rows(WPI_MONTH_URL, parse_wpi, year, mon, "mospi_wpi_month")
    h = wpi_headline(rows)
    if h["index"] is None:
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"mospi_wpi_month: {h['period']} headline carries no index")
    return Result(data={"period": h["period"], "series": {SERIES["wpi_index"]: _pts("", h["obs_date"], h["index"])}}, as_of=h["obs_date"],
                  sources=[got.source(SOURCE)])


@guard
def iip_month(year: int, mon: int) -> Result:
    got, rows = _month_rows(IIP_MONTH_URL, parse_iip, year, mon, "mospi_iip_month")
    h = iip_headline(rows)
    if h["index"] is None:
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"mospi_iip_month: {h['period']} headline carries no index")
    return Result(data={"period": h["period"], "series": {SERIES["iip_index"]: _pts("", h["obs_date"], h["index"]),
                                                          SERIES["iip_yoy"]: _pts("", h["obs_date"], h["growth"])}},
                  as_of=h["obs_date"], sources=[got.source(SOURCE)])


MONTH_FETCH = {"cpi": cpi_month, "wpi": wpi_month, "iip": iip_month}

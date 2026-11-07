"""Parsing helpers shared by providers. Every failure here is a typed DrishtiError, never a blank."""
from __future__ import annotations

import csv
import functools
import io
import re
import xml.etree.ElementTree as ET
import zipfile
from datetime import date, timedelta
from typing import Any, Callable, Iterable

from ..core.canary import parse_date, today_ist
from ..core.envelope import DrishtiError, ErrorKind, Result
from ..core.fetch import Fetched, looks_like_html

_MISSING = {"", "-", "--", "NA", "N/A", "NULL", "NAN"}


def num(v: Any, where: str = "") -> float | None:
    """'' / '-' / 'NA' -> None. Anything else must be a number (commas tolerated) or it is PARSE_FAIL."""
    if v is None:
        return None
    s = str(v).strip()
    if s.upper() in _MISSING:
        return None
    try:
        return float(s.replace(",", ""))
    except ValueError:
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"{where or 'value'}: {s[:40]!r} is not a number") from None


def integer(v: Any, where: str = "") -> int | None:
    f = num(v, where)
    if f is None:
        return None
    if f != int(f):
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"{where or 'value'}: {v!r} is not a whole number")
    return int(f)


@functools.lru_cache(maxsize=4096)
def _iso_cached(s: str) -> str | None:
    if len(s) == 10 and s[4] == "-" and s[7] == "-":  # fast path: already ISO (bhavcopies have ~40k rows, 1 date)
        try:
            return date.fromisoformat(s).isoformat()
        except ValueError:
            return None
    d = parse_date(s)
    return d.isoformat() if d else None


def iso_date(v: Any, where: str = "") -> str:
    out = _iso_cached(str(v).strip()) if v is not None else None
    if out is None:
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"{where or 'date'}: cannot read a date from {str(v)[:40]!r}")
    return out


def opt_str(v: Any) -> str | None:
    s = "" if v is None else str(v).strip()
    return s or None


def decode(body: bytes) -> str:
    """UTF-8 (BOM tolerated); legacy cp1252 as a fallback for old NSE/BSE files."""
    try:
        return body.decode("utf-8-sig")
    except UnicodeDecodeError:
        return body.decode("cp1252", errors="replace")


def csv_rows(text: str, required: Iterable[str], label: str) -> list[dict[str, str]]:
    """DictReader with stripped header names; missing required columns is PARSE_FAIL (layout changed)."""
    reader = csv.reader(io.StringIO(text))
    try:
        header = [h.strip() for h in next(reader)]
    except StopIteration:
        raise DrishtiError(ErrorKind.EMPTY, f"{label}: no header line") from None
    missing = sorted(set(required) - set(header))
    if missing:
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: columns missing {missing}; got {header[:12]}")
    rows = []
    for line in reader:
        if not line or all(not c.strip() for c in line):
            continue
        rows.append({h: (line[i].strip() if i < len(line) else "") for i, h in enumerate(header)})
    return rows


def unzip_single(body: bytes, label: str) -> bytes:
    """Contents of the one file inside a zip. An HTML block page where a zip was expected is BLOCKED_HTML."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(body))
    except zipfile.BadZipFile:
        kind = ErrorKind.BLOCKED_HTML if looks_like_html(body) else ErrorKind.PARSE_FAIL
        raise DrishtiError(kind, f"{label}: not a zip file ({len(body)} bytes)") from None
    names = [n for n in zf.namelist() if not n.endswith("/")]
    if len(names) != 1:
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: expected 1 file in zip, found {names[:5]}")
    try:
        return zf.read(names[0])
    except (zipfile.BadZipFile, OSError, EOFError) as e:
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: corrupt zip member ({type(e).__name__})") from e


def require_rows(rows: list, label: str, minimum: int = 1) -> None:
    if not rows:
        raise DrishtiError(ErrorKind.EMPTY, f"{label}: file parsed but has no data rows")
    if len(rows) < minimum:
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: only {len(rows)} rows (expected >= {minimum}); truncated file?")


def single_date(rows: list[dict], key: str, label: str) -> str:
    dates = {r[key] for r in rows}
    if len(dates) != 1:
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: expected one trade date, found {sorted(dates)[:5]}")
    return next(iter(dates))


def check_ohlc(rows: list[dict], label: str, *, max_bad_frac: float = 0.01) -> None:
    """Column-drift canary: where something traded, low <= open, close <= high. A shifted column breaks this
    for most rows at once, so >1% violations means the layout changed and we must not trust the numbers."""
    checked = bad = 0
    for r in rows:
        o, h, lo, c, v = r.get("open"), r.get("high"), r.get("low"), r.get("close"), r.get("volume")
        if None in (o, h, lo, c) or not v:
            continue
        checked += 1
        eps = 1e-6 * max(abs(h), 1.0)
        if not (lo - eps <= o <= h + eps and lo - eps <= c <= h + eps):
            bad += 1
    if checked and bad / checked > max_bad_frac:
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: {bad}/{checked} traded rows violate low<=open,close<=high; layout shifted?")


def load_parsed(store, dataset: str, d: date | str, fetcher: Callable[[], Fetched], parse: Callable[[bytes], Any], source: str):
    """(parsed, fetched). Serve a stored blob if there is one, else fetch. The raw file is stored only AFTER it
    parses, so a block page or a changed layout can never be cached as if it were good data."""
    if store is not None:
        hit = store.get_blob(dataset, d)
        if hit is not None:
            return parse(hit.body), hit
    got = fetcher()
    parsed = parse(got.body)
    if store is not None:
        store.put_blob(dataset, d, got, source)
    return parsed, got


def latest(fn: Callable[[date], Result], start: date | None = None, max_back: int = 7) -> Result:
    """Newest available daily file: try `start` (default today IST), then earlier weekdays while the file is
    NOT_FOUND (not yet published, or a market holiday). Any other error is returned as is, not skipped."""
    d = start or today_ist()
    tried = 0
    while tried < max_back:
        if d.weekday() < 5:
            tried += 1
            r = fn(d)
            if not (r.error and r.error.kind == ErrorKind.NOT_FOUND):
                return r
        d -= timedelta(days=1)
    return Result(error=DrishtiError(ErrorKind.NOT_FOUND, f"no file in the last {max_back} weekdays before {start or today_ist()}"))


_XBRL_VERSION = re.compile(rb"<!--\s*(?:PIT|SHP)\s+V([\d.]+)")


def xbrl_facts(xml: bytes, family: str, label: str) -> tuple[dict[str, dict[str, str]], str | None]:
    """BSE/NSE XBRL instance -> ({contextRef: {FactName: text}}, form version).

    `family` is 'co' (insider trading) or 'shp' (shareholding). The namespace URI carries the taxonomy date
    (.../xbrl/shp/2025-10-31/in-bse-shp), which changes between form versions, so only its shape is matched.
    DTDs and entities are refused: the stdlib parser would expand them.
    """
    head = xml[:4000].upper()
    if b"<!DOCTYPE" in head or b"<!ENTITY" in head:
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: DTD/entity declarations are not accepted")
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as e:
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: not valid XML ({e})") from e
    pre, post = f"http://www.bseindia.com/xbrl/{family}/", f"/in-bse-{family}"
    ctx: dict[str, dict[str, str]] = {}
    for el in root:
        cid = el.get("contextRef")
        if not cid or not el.tag.startswith("{"):
            continue
        ns, _, local = el.tag[1:].partition("}")
        if ns.startswith(pre) and ns.endswith(post):
            ctx.setdefault(cid, {})[local] = (el.text or "").strip()
    m = _XBRL_VERSION.search(xml[:600])
    return ctx, (m.group(1).decode() if m else None)

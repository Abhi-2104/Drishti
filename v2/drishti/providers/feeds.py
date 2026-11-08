"""News from RSS 2.0 and Atom feeds the investor chooses. Headline, link, publish time and a short plain-text summary only:
no article scraping, no full text (copyright, and the feed is the publisher's own invitation to read the headline).

Rules kept here:
  * Sources come from settings (an empty list is a valid state); this module takes (name, url) and never reads settings itself.
  * Plain feed fetches with an honest User-Agent. A block is reported as a block (BLOCKED), never routed around.
  * XML with a DOCTYPE or entity declaration is refused (entity-expansion attacks); stdlib ElementTree is used without extra packages.
  * An item without a readable publish time is dropped and counted; if most items lack one the feed is PARSE_FAIL, not "quiet".
  * Times are stored in UTC ISO-8601.
"""
from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from ..core.envelope import DrishtiError, ErrorKind, Result, guard
from ..core.fetch import HONEST_UA, Profile, get
from . import _util as u

SOURCE = "news_feed"
FEED = Profile("news_feed", {"User-Agent": HONEST_UA, "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*"},
               timeout=(5.0, 20.0))
ATOM = "{http://www.w3.org/2005/Atom}"
MAX_SUMMARY = 400
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def clean(text: str | None, limit: int | None = None) -> str:
    """Plain text from a feed field: tags removed, entities decoded, whitespace collapsed."""
    t = _WS.sub(" ", html.unescape(_TAG.sub(" ", text or ""))).strip()
    return t[:limit].rstrip() if limit and len(t) > limit else t


def parse_time(v: str | None) -> str | None:
    """RFC 822 (RSS) or ISO 8601 (Atom) to UTC ISO-8601 seconds; None when unreadable. Zone-less times are taken as UTC."""
    v = (v or "").strip()
    if not v:
        return None
    dt = None
    try:
        dt = parsedate_to_datetime(v)
    except (TypeError, ValueError, IndexError):
        try:
            dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _text(el, tag: str) -> str:
    c = el.find(tag)
    return (c.text or "") if c is not None and c.text else ""


def parse_feed(body: bytes, label: str = "news_feed", *, min_items: int = 1) -> dict:
    """{'items': [{item_id, title, link, published, summary}], 'dropped': n_without_time, 'format': 'rss'|'atom'}."""
    head = body[:4096].lower()
    if b"<!doctype" in head or b"<!entity" in body[:65536].lower():
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: XML with a DOCTYPE / entity declaration is refused")
    try:
        root = ET.fromstring(body)
    except ET.ParseError as e:
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: not XML ({e}); first bytes {body[:60]!r}") from None
    raw: list[dict] = []
    if root.tag == "rss" and root.find("channel") is not None:
        fmt = "rss"
        for it in root.find("channel").findall("item"):
            link = _text(it, "link").strip()
            guid = _text(it, "guid").strip()
            raw.append({"id": guid or link, "title": _text(it, "title"), "link": link, "time": _text(it, "pubDate") or _text(it, "{http://purl.org/dc/elements/1.1/}date"),
                        "summary": _text(it, "description")})
    elif root.tag == f"{ATOM}feed":
        fmt = "atom"
        for it in root.findall(f"{ATOM}entry"):
            lk = [l for l in it.findall(f"{ATOM}link") if l.get("rel", "alternate") == "alternate"] or it.findall(f"{ATOM}link")
            link = (lk[0].get("href") or "").strip() if lk else ""
            raw.append({"id": _text(it, f"{ATOM}id").strip() or link, "title": _text(it, f"{ATOM}title"), "link": link,
                        "time": _text(it, f"{ATOM}published") or _text(it, f"{ATOM}updated"),
                        "summary": _text(it, f"{ATOM}summary") or _text(it, f"{ATOM}content")})
    else:
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: root <{root.tag[:40]}> is neither RSS 2.0 nor Atom")
    if not raw:
        raise DrishtiError(ErrorKind.EMPTY, f"{label}: the feed has no items")
    items, dropped, seen = [], 0, set()
    for r in raw:
        when, title = parse_time(r["time"]), clean(r["title"], 300)
        if not r["id"] or not title or when is None:
            dropped += 1
            continue
        if r["id"] in seen:
            continue
        seen.add(r["id"])
        items.append({"item_id": r["id"][:500], "title": title, "link": r["link"][:1000], "published": when, "summary": clean(r["summary"], MAX_SUMMARY)})
    if dropped > len(raw) / 2:
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: {dropped} of {len(raw)} items lack an id, title or readable time; layout changed?")
    u.require_rows(items, label, min_items)
    return {"items": items, "dropped": dropped, "format": fmt}


@guard
def fetch_feed(name: str, url: str) -> Result:
    if not url.lower().startswith("https://"):
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"news source {name!r}: only https:// feeds are fetched")
    got = get(url, FEED, expect="xml")
    parsed = parse_feed(got.body, f"news_feed[{name}]")
    newest = max(i["published"] for i in parsed["items"])
    return Result(data={"source": name, **parsed}, as_of=newest[:10], sources=[got.source(SOURCE)])

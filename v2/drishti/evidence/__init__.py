"""Evidence layer, E1 (cross-cutting, ledger X-4): documents and the claims read out of them, each tied to an exact quote.

    source   one document (earnings-call transcript, annual report, announcement, prospectus ...), its full text, the company, and
             WHEN IT BECAME PUBLIC (published_at). That date is what keeps evidence point-in-time: a query "as of" a day never sees
             a document published later.
    claim    one statement read from a source ("management cut its FY revenue guidance"), with the quote that supports it.

The rule that makes this trustworthy: a claim is stored only if its quote occurs in the source text, character for character after
normalising whitespace, quotes and dashes (`validate_claim`). Whoever extracts claims (Claude in the session, a person, a later tool)
can be wrong about what a quote means, but can never cite words the document does not contain. Paraphrase is never accepted as a quote.

Extraction itself is not done here (E1 is the store and the check). Ingesting PDFs is E2; /drishti-concall and /ipo are E3.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import date, datetime, timezone

from ..core.envelope import DrishtiError, ErrorKind, Result

KINDS = ("concall", "annual_report", "announcement", "results", "prospectus", "rating_report", "other")
TOPICS = ("guidance", "demand", "margins", "capex", "debt", "orders", "pricing", "competition", "governance", "management", "risk",
          "accounting", "regulation", "other")
MIN_QUOTE, MAX_QUOTE = 20, 800

DDL = """
CREATE TABLE IF NOT EXISTS evidence_source (
    id TEXT PRIMARY KEY, kind TEXT NOT NULL, isin TEXT, title TEXT NOT NULL, url TEXT, published_at TEXT NOT NULL, added_at TEXT NOT NULL,
    chars INTEGER NOT NULL, text TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_evsrc_isin ON evidence_source (isin, published_at);
CREATE TABLE IF NOT EXISTS evidence_claim (
    id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES evidence_source(id), isin TEXT, claim TEXT NOT NULL, quote TEXT NOT NULL,
    char_start INTEGER NOT NULL, char_end INTEGER NOT NULL, topic TEXT NOT NULL, polarity INTEGER NOT NULL, author TEXT NOT NULL,
    recorded_at TEXT NOT NULL, UNIQUE (source_id, quote, claim));
CREATE INDEX IF NOT EXISTS ix_evclaim_isin ON evidence_claim (isin, topic)
"""

_QUOTES = {"‘": "'", "’": "'", "‚": "'", "‛": "'", "′": "'", "“": '"', "”": '"', "„": '"',
           "″": '"', "–": "-", "—": "-", "−": "-", " ": " ", "…": "..."}


def normalise(text: str) -> str:
    """The one canonical form used for both stored text and quotes: Unicode NFKC, curly quotes and dashes made plain, whitespace runs
    collapsed to one space. Case and punctuation are kept: a quote must match the document's own words."""
    t = unicodedata.normalize("NFKC", text or "")
    for a, b in _QUOTES.items():
        t = t.replace(a, b)
    return re.sub(r"\s+", " ", t).strip()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ensure(store) -> None:
    store.ensure_table(DDL)


def _day(v, what: str) -> str:
    try:
        return (v if isinstance(v, date) else date.fromisoformat(str(v)[:10])).isoformat()
    except ValueError:
        raise DrishtiError(ErrorKind.REJECTED, f"{what} must be a date (YYYY-MM-DD), got {v!r}") from None


def add_source(store, *, text: str, kind: str, title: str, published_at, isin: str | None = None, url: str | None = None) -> Result:
    """Store a document. The id is a hash of its normalised text, so adding the same document twice returns the same id."""
    ensure(store)
    if kind not in KINDS:
        return Result(error=DrishtiError(ErrorKind.REJECTED, f"kind must be one of {', '.join(KINDS)}"))
    body = normalise(text)
    if len(body) < 200:
        return Result(error=DrishtiError(ErrorKind.REJECTED, f"source text is {len(body)} characters; a real document is longer"))
    try:
        pub = _day(published_at, "published_at")
    except DrishtiError as e:
        return Result(error=e)
    sid = "src-" + hashlib.sha1(body.encode()).hexdigest()[:16]
    have = store.query("SELECT id, published_at FROM evidence_source WHERE id = ?", (sid,))
    if not have:
        store.upsert("evidence_source", [{"id": sid, "kind": kind, "isin": isin, "title": title.strip(), "url": url, "published_at": pub,
                                          "added_at": _now(), "chars": len(body), "text": body}], key=["id"])
    return Result(data={"source_id": sid, "chars": len(body), "published_at": have[0]["published_at"] if have else pub, "new": not have},
                  as_of=pub)


def _source(store, source_id: str) -> dict:
    ensure(store)
    rows = store.query("SELECT * FROM evidence_source WHERE id = ?", (source_id,))
    if not rows:
        raise DrishtiError(ErrorKind.NOT_FOUND, f"no evidence source {source_id!r}")
    return rows[0]


def validate_claim(store, source_id: str, quote: str) -> Result:
    """Is `quote` word for word in the source? data = {found, char_start, char_end, occurrences, context}. Never fuzzy: one changed
    word fails. Case differences fail too, except for a quote whose only difference is case, which is reported as not found with a hint."""
    try:
        src = _source(store, source_id)
    except DrishtiError as e:
        return Result(error=e)
    q = normalise(quote)
    if not (MIN_QUOTE <= len(q) <= MAX_QUOTE):
        return Result(error=DrishtiError(ErrorKind.REJECTED, f"quote is {len(q)} characters; it must be {MIN_QUOTE}-{MAX_QUOTE} (a full phrase, not a word or a page)"))
    text = src["text"]
    hits = [m.start() for m in re.finditer(re.escape(q), text)]
    if not hits:
        hint = "the words occur with different capitalisation; quote them exactly" if q.lower() in text.lower() else \
            "these words are not in the document; quote it exactly or drop the claim"
        return Result(data={"found": False, "why": hint}, as_of=src["published_at"])
    s = hits[0]
    return Result(data={"found": True, "char_start": s, "char_end": s + len(q), "occurrences": len(hits),
                        "context": text[max(0, s - 120): s + len(q) + 120]}, as_of=src["published_at"])


def record_evidence(store, *, source_id: str, claim: str, quote: str, topic: str, polarity: int, author: str) -> Result:
    """Store one claim with its quote, only if the quote is verbatim in the source. polarity: +1 good for the stock, -1 bad, 0 neutral."""
    if topic not in TOPICS:
        return Result(error=DrishtiError(ErrorKind.REJECTED, f"topic must be one of {', '.join(TOPICS)}"))
    if polarity not in (-1, 0, 1):
        return Result(error=DrishtiError(ErrorKind.REJECTED, "polarity must be -1, 0 or +1"))
    if len((claim or "").strip()) < 15:
        return Result(error=DrishtiError(ErrorKind.REJECTED, "claim must say what the quote shows, in a sentence"))
    if not (author or "").strip():
        return Result(error=DrishtiError(ErrorKind.REJECTED, "author is required (who read the claim: e.g. claude/<model> or a person)"))
    v = validate_claim(store, source_id, quote)
    if not v.ok:
        return v
    if not v.data["found"]:
        return Result(error=DrishtiError(ErrorKind.REJECTED, f"quote not in source {source_id}: {v.data['why']}"))
    src = _source(store, source_id)
    q = normalise(quote)
    cid = "clm-" + hashlib.sha1(f"{source_id}|{q}|{claim.strip()}".encode()).hexdigest()[:16]
    store.upsert("evidence_claim", [{"id": cid, "source_id": source_id, "isin": src["isin"], "claim": claim.strip(), "quote": q,
                                     "char_start": v.data["char_start"], "char_end": v.data["char_end"], "topic": topic, "polarity": polarity,
                                     "author": author.strip(), "recorded_at": _now()}], key=["id"])
    return Result(data={"claim_id": cid, "source_id": source_id, "char_start": v.data["char_start"], "published_at": src["published_at"]},
                  as_of=src["published_at"])


def query_evidence(store, *, isin: str | None = None, on=None, topic: str | None = None, source_id: str | None = None, limit: int = 50) -> Result:
    """Claims with their quotes and sources, newest document first. `on` (a date) keeps it point-in-time: only documents published on or
    before that day. Without isin or source_id, all companies."""
    ensure(store)
    where, args = ["1=1"], []
    if isin:
        where.append("c.isin = ?")
        args.append(isin)
    if source_id:
        where.append("c.source_id = ?")
        args.append(source_id)
    if topic:
        where.append("c.topic = ?")
        args.append(topic)
    if on:
        try:
            where.append("s.published_at <= ?")
            args.append(_day(on, "on"))
        except DrishtiError as e:
            return Result(error=e)
    rows = store.query(f"""SELECT c.id, c.claim, c.quote, c.topic, c.polarity, c.author, c.char_start, s.id AS source_id, s.kind, s.title,
                                  s.url, s.published_at FROM evidence_claim c JOIN evidence_source s ON s.id = c.source_id
                           WHERE {' AND '.join(where)} ORDER BY s.published_at DESC, c.char_start LIMIT ?""", (*args, int(limit)))
    srcs = store.query(f"SELECT COUNT(*) AS n FROM evidence_source s WHERE {'s.isin = ?' if isin else '1=1'}"
                       f"{' AND s.published_at <= ?' if on else ''}", tuple(x for x in (isin, _day(on, 'on') if on else None) if x))[0]["n"]
    return Result(data={"claims": [dict(r) for r in rows], "count": len(rows), "sources_on_file": srcs,
                        "note": "every quote here was checked verbatim against its document when recorded"},
                  as_of=_day(on, "on") if on else None)

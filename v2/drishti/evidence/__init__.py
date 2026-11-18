"""Evidence layer, E1 (cross-cutting, ledger X-4): documents and the claims read out of them, each tied to an exact quote.

    source   one document (earnings-call transcript, annual report, announcement, prospectus ...), its full text, the company, and
             WHEN IT BECAME PUBLIC (published_at). That date is what keeps evidence point-in-time: a query "as of" a day never sees
             a document published later.
    claim    one statement read from a source ("management cut its FY revenue guidance"), with the quote that supports it.

The rule that makes this trustworthy: a claim is stored only if its quote occurs in the source text, character for character after
normalising whitespace, quotes and dashes (`validate_claim`). Whoever extracts claims (Claude in the session, a person, a later tool)
can be wrong about what a quote means, but can never cite words the document does not contain. Paraphrase is never accepted as a quote.

Extraction itself is not done here: Claude (or a person) reads a source and records claims. The pieces around that:
    E2  `pdf_text`, `fetch_document`, `ingest`: turn a PDF (local file, or a URL on an exchange host) into a stored source.
    E3  `find_filings`, `read_source`: list a company's concall transcripts / results / reports from the announcements already stored,
        and read a stored source in windows, so a long document can be read piece by piece. The slash commands that drive this are
        /drishti-concall and /drishti-ipo.
    E4  `sample_claims`, `review_claim`, `review_summary`, `audit`: a person spot-checks a sample of claims against their quotes and the
        precision (how many are really supported) is kept; `audit` re-proves every stored quote against its source.
"""
from __future__ import annotations

import hashlib
import io
import random
import re
import unicodedata
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

from ..core.envelope import DrishtiError, ErrorKind, Result

KINDS = ("concall", "annual_report", "announcement", "results", "prospectus", "rating_report", "other")
TOPICS = ("guidance", "demand", "margins", "capex", "debt", "orders", "pricing", "competition", "governance", "management", "risk",
          "accounting", "regulation", "other")
MIN_QUOTE, MAX_QUOTE = 20, 800
VERDICTS = ("supported", "overstated", "wrong", "unclear")     # a reviewer's reading of a claim against its own quote
MAX_PDF_BYTES = 30 * 1024 * 1024
DOC_HOSTS = {   # where a document may be fetched from: exchange hosts only, never an arbitrary address
    "nsearchives.nseindia.com": "nse_archive", "www.nseindia.com": "nse_archive", "archives.nseindia.com": "nse_archive",
    "www.bseindia.com": "bse_www", "bseindia.com": "bse_www"}

DDL = """
CREATE TABLE IF NOT EXISTS evidence_source (
    id TEXT PRIMARY KEY, kind TEXT NOT NULL, isin TEXT, title TEXT NOT NULL, url TEXT, published_at TEXT NOT NULL, added_at TEXT NOT NULL,
    chars INTEGER NOT NULL, text TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_evsrc_isin ON evidence_source (isin, published_at);
CREATE TABLE IF NOT EXISTS evidence_claim (
    id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES evidence_source(id), isin TEXT, claim TEXT NOT NULL, quote TEXT NOT NULL,
    char_start INTEGER NOT NULL, char_end INTEGER NOT NULL, topic TEXT NOT NULL, polarity INTEGER NOT NULL, author TEXT NOT NULL,
    recorded_at TEXT NOT NULL, UNIQUE (source_id, quote, claim));
CREATE INDEX IF NOT EXISTS ix_evclaim_isin ON evidence_claim (isin, topic);
CREATE TABLE IF NOT EXISTS evidence_review (
    claim_id TEXT NOT NULL REFERENCES evidence_claim(id), verdict TEXT NOT NULL, reviewer TEXT NOT NULL, note TEXT, reviewed_at TEXT NOT NULL,
    PRIMARY KEY (claim_id, reviewer))
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


# --- E2: documents in ----------------------------------------------------------------------------------------------------
def pdf_text(data: bytes) -> str:
    """The text layer of a PDF, page by page (a form feed is not kept; pages are joined with a blank line). A PDF with no text layer
    (a scan) is refused: reading it needs OCR, which is not done here, and guessing would put words in the document that it does not contain."""
    try:
        from pypdf import PdfReader
    except ImportError:
        raise DrishtiError(ErrorKind.REJECTED, "reading PDFs needs the pypdf package: run `uv add pypdf` in the v2 folder") from None
    if len(data) > MAX_PDF_BYTES:
        raise DrishtiError(ErrorKind.REJECTED, f"PDF is {len(data) / 1e6:.0f} MB; the limit is {MAX_PDF_BYTES // (1024 * 1024)} MB")
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted and not reader.decrypt(""):
            raise DrishtiError(ErrorKind.REJECTED, "the PDF is password-protected")
        pages = [(p.extract_text() or "").strip() for p in reader.pages]
    except DrishtiError:
        raise
    except Exception as e:     # pypdf raises many kinds for damaged files
        raise DrishtiError(ErrorKind.PARSE_FAIL, f"cannot read the PDF: {type(e).__name__}: {str(e)[:100]}") from None
    text = "\n\n".join(p for p in pages if p)
    if len(normalise(text)) < max(200, 40 * len(pages) // 4):
        raise DrishtiError(ErrorKind.EMPTY, f"the PDF has {len(pages)} pages but almost no text: a scan? (OCR is not done here)")
    return text


def fetch_document(url: str) -> bytes:
    """A PDF from an exchange host (see DOC_HOSTS). Goes through the one fetch layer, so a block page is a typed error, never data."""
    from ..core.fetch import get
    from ..providers import profiles
    parsed = urlparse(url)
    prof = DOC_HOSTS.get((parsed.hostname or "").lower())
    if parsed.scheme != "https" or not prof:
        raise DrishtiError(ErrorKind.REJECTED, f"documents are fetched only over https from {', '.join(sorted(DOC_HOSTS))}; got {parsed.hostname!r}")
    return get(url, getattr(profiles, prof.upper()), expect="pdf").body


def ingest(store, *, kind: str, title: str, published_at, isin: str | None = None, url: str | None = None, path: str | None = None,
           text: str | None = None) -> Result:
    """Store a document from whichever form it comes in: `text`, a local .txt/.md/.pdf `path`, or a PDF `url` on an exchange host."""
    try:
        if text is None:
            if path:
                f = Path(path).expanduser()
                data = f.read_bytes()
                text = pdf_text(data) if f.suffix.lower() == ".pdf" or data[:5] == b"%PDF-" else data.decode("utf-8", errors="replace")
            elif url:
                text = pdf_text(fetch_document(url))
            else:
                raise DrishtiError(ErrorKind.REJECTED, "give the document's text, a local path, or a PDF url")
    except DrishtiError as e:
        return Result(error=e)
    except OSError as e:
        return Result(error=DrishtiError(ErrorKind.NOT_FOUND, f"cannot read {path}: {e}"))
    return add_source(store, text=text, kind=kind, title=title, published_at=published_at, isin=isin, url=url)


# --- E3: finding and reading ---------------------------------------------------------------------------------------------
_FILING_KINDS = {   # headline / category patterns on the announcements already stored
    "concall": re.compile(r"transcript|earnings call|con(?:ference)?\.? ?call|analyst|investor meet|investor call", re.I),
    "results": re.compile(r"financial results|outcome of board|unaudited|audited results|quarterly results|results for", re.I),
    "annual_report": re.compile(r"annual report|integrated report|business responsibility", re.I),
    "presentation": re.compile(r"presentation|investor deck", re.I),
}
_NOT_A_DOCUMENT = re.compile(r"audio|recording|\.mp3|schedule of|intimation of|prior intimation", re.I)   # an audio link or a date notice carries no words to read


def find_filings(store, *, isin: str, kind: str = "concall", days: int = 400, on=None, limit: int = 12) -> Result:
    """A company's filings of one kind from the announcements stored in the database (newest first): published date, headline and the
    PDF link. This lists candidates; `ingest` then stores one. `on` keeps it point-in-time."""
    if kind not in _FILING_KINDS:
        return Result(error=DrishtiError(ErrorKind.REJECTED, f"kind must be one of {', '.join(_FILING_KINDS)}"))
    try:
        end = date.fromisoformat(_day(on, "on")) if on else date.today()
    except DrishtiError as e:
        return Result(error=e)
    start = (end - timedelta(days=days)).isoformat()
    rows = store.query("SELECT published, source, id, headline, category, subcategory, url FROM announcement WHERE isin = ? AND published >= ? "
                       "AND substr(published, 1, 10) <= ? AND url IS NOT NULL AND url != '' ORDER BY published DESC", (isin, start, end.isoformat()))
    pat, out = _FILING_KINDS[kind], []
    for r in rows:
        label = f"{r['headline'] or ''} {r['category'] or ''} {r['subcategory'] or ''}"
        if pat.search(label) and not _NOT_A_DOCUMENT.search(r["headline"] or "") and (r["url"] or "").lower().split("?")[0].endswith(".pdf"):
            out.append({"published": r["published"][:10], "headline": r["headline"], "category": r["category"], "url": r["url"], "source": r["source"]})
        if len(out) >= limit:
            break
    note = None if out else (f"no {kind} documents among the stored announcements for this stock in the last {days} days "
                             "(the announcements feed only lists what was fetched; ask for a wider window or give a URL directly)")
    return Result(data={"filings": out, "count": len(out), "note": note}, as_of=end.isoformat())


def read_source(store, source_id: str, *, offset: int = 0, length: int = 12000) -> Result:
    """A window of a stored source's text, so a long document is read in pieces. data = {text, offset, next_offset (None at the end), chars}.
    Quotes are checked against the same text, so what is read here is exactly what a claim can quote."""
    try:
        src = _source(store, source_id)
    except DrishtiError as e:
        return Result(error=e)
    offset, length = max(0, int(offset)), max(500, min(int(length), 30000))
    text = src["text"]
    end = min(len(text), offset + length)
    if end < len(text):                          # end the window on a word boundary
        cut = text.rfind(" ", offset, end)
        end = cut if cut > offset + length // 2 else end
    return Result(data={"source_id": source_id, "title": src["title"], "kind": src["kind"], "published_at": src["published_at"],
                        "chars": len(text), "offset": offset, "text": text[offset:end], "next_offset": end if end < len(text) else None},
                  as_of=src["published_at"])


# --- E4: does the evidence layer tell the truth? -----------------------------------------------------------------------------
def audit(store) -> Result:
    """Re-prove every stored claim: its quote must still be in its source. data = {claims, ok, moved, broken: [...]}. A quote that is still in the
    text but no longer at its recorded place is `moved` (still true, still counted ok); one that is gone is `broken`."""
    ensure(store)
    broken, moved, n = [], 0, 0
    for c in store.query("SELECT c.id, c.quote, c.char_start, c.char_end, s.text FROM evidence_claim c LEFT JOIN evidence_source s ON s.id = c.source_id"):
        n += 1
        if c["text"] is None or c["quote"] not in c["text"]:
            broken.append(c["id"])
        elif c["text"][c["char_start"]:c["char_end"]] != c["quote"]:
            moved += 1
    return Result(data={"claims": n, "ok": n - len(broken), "moved": moved, "broken": broken[:50]})


def sample_claims(store, *, n: int = 20, seed: int = 7, only_unreviewed: bool = True) -> Result:
    """A random sample of claims to check by eye: each with its quote in context. A claim can have a verbatim quote and still say more than the
    quote does; only a reader can judge that (see `review_claim`)."""
    ensure(store)
    rows = store.query("SELECT c.id, c.claim, c.quote, c.topic, c.polarity, c.author, c.char_start, c.char_end, s.title, s.kind, s.published_at, s.text "
                       "FROM evidence_claim c JOIN evidence_source s ON s.id = c.source_id ORDER BY c.id")
    if only_unreviewed:
        done = {r["claim_id"] for r in store.query("SELECT DISTINCT claim_id FROM evidence_review")}
        rows = [r for r in rows if r["id"] not in done]
    random.Random(seed).shuffle(rows)
    out = []
    for r in rows[:max(1, int(n))]:
        t = r["text"]
        out.append({"claim_id": r["id"], "claim": r["claim"], "topic": r["topic"], "polarity": r["polarity"], "author": r["author"],
                    "source": f"{r['kind']}: {r['title']} ({r['published_at']})", "quote": r["quote"],
                    "context": t[max(0, r["char_start"] - 250): r["char_end"] + 250]})
    return Result(data={"sample": out, "count": len(out), "unreviewed_left": max(0, len(rows) - len(out)),
                        "how_to_review": "for each: is the CLAIM fully supported by the QUOTE (and its context)? verdict supported / overstated "
                                         "(quote says less) / wrong (quote says something else or the opposite) / unclear"})


def review_claim(store, *, claim_id: str, verdict: str, reviewer: str, note: str | None = None) -> Result:
    ensure(store)
    if verdict not in VERDICTS:
        return Result(error=DrishtiError(ErrorKind.REJECTED, f"verdict must be one of {', '.join(VERDICTS)}"))
    if not (reviewer or "").strip():
        return Result(error=DrishtiError(ErrorKind.REJECTED, "reviewer is required (a person's name: a claim's author must not review its own claim)"))
    c = store.query("SELECT author FROM evidence_claim WHERE id = ?", (claim_id,))
    if not c:
        return Result(error=DrishtiError(ErrorKind.NOT_FOUND, f"no claim {claim_id!r}"))
    if c[0]["author"].strip().lower() == reviewer.strip().lower():
        return Result(error=DrishtiError(ErrorKind.REJECTED, "a claim cannot be reviewed by its own author"))
    store.upsert("evidence_review", [{"claim_id": claim_id, "verdict": verdict, "reviewer": reviewer.strip(), "note": note, "reviewed_at": _now()}],
                 key=["claim_id", "reviewer"])
    return Result(data={"claim_id": claim_id, "verdict": verdict})


def review_summary(store) -> Result:
    """How good is the extraction? Of the reviewed claims, the share judged supported, and by author. Too few reviews say so."""
    ensure(store)
    rows = store.query("SELECT r.verdict, c.author FROM evidence_review r JOIN evidence_claim c ON c.id = r.claim_id")
    total = store.query("SELECT COUNT(*) AS n FROM evidence_claim")[0]["n"]
    by: dict[str, dict] = {}
    for r in rows:
        d = by.setdefault(r["author"], {v: 0 for v in VERDICTS})
        d[r["verdict"]] += 1
    n = len(rows)
    sup = sum(1 for r in rows if r["verdict"] == "supported")
    return Result(data={"claims_on_file": total, "reviewed": n, "supported": sup, "supported_share": round(sup / n, 3) if n else None, "by_author": by,
                        "note": ("fewer than 20 reviews: too few to judge the extraction" if n < 20 else "a share below about 90% means the extraction needs a stricter prompt")})

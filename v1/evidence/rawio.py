"""Raw-payload helpers: the stored bytes are the ground truth an excerpt is bound to."""
from __future__ import annotations

import hashlib
from pathlib import Path

# INFY identity map -> canonical ISIN (invariant: ticker is never identity).
_ISIN = "INE009A01021"
ISIN_MAP = {
    "INFY": _ISIN, "INFY.NS": _ISIN, "INFY.BO": _ISIN,
    "500209": _ISIN, "NSE:INFY": _ISIN, _ISIN: _ISIN,
}


def normalize_isin(x: str) -> str | None:
    return ISIN_MAP.get((x or "").strip().upper())


def sha256_hex(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def canonical_text(raw_ref: str) -> str:
    """Canonical text of a stored raw payload = its exact UTF-8 decoding.

    No transformation: locator offsets index straight into this, so
    canonical_text(raw)[start:end] is byte-faithful to what was captured.
    """
    return Path(raw_ref).read_text(encoding="utf-8")


def raw_bytes(raw_ref: str) -> bytes:
    return Path(raw_ref).read_bytes()

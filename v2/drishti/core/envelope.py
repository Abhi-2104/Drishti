"""Result envelope and typed errors. Every provider call returns a Result — never a silent blank."""
from __future__ import annotations

import functools
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Callable

log = logging.getLogger("drishti")


class ErrorKind(StrEnum):
    TIMEOUT = "TIMEOUT"
    BLOCKED = "BLOCKED"            # 401 / 403 / 429
    BLOCKED_HTML = "BLOCKED_HTML"  # HTML page where data was expected
    NOT_FOUND = "NOT_FOUND"        # 404
    EMPTY = "EMPTY"                # fetched fine, nothing in it
    STALE = "STALE"                # fetched fine, newest record is too old (freshness canary)
    PARSE_FAIL = "PARSE_FAIL"      # fetched fine, layout not what we expect
    NETWORK = "NETWORK"            # connection errors and any other non-2xx
    NOT_IMPLEMENTED = "NOT_IMPLEMENTED"  # P1 stubs
    REJECTED = "REJECTED"          # input refused by a check (e.g. a quote that is not in its document)


class DrishtiError(Exception):
    def __init__(self, kind: ErrorKind, detail: str = ""):
        super().__init__(f"{kind}: {detail}" if detail else str(kind))
        self.kind = kind
        self.detail = detail

    def to_dict(self) -> dict:
        return {"kind": str(self.kind), "detail": self.detail}


@dataclass
class Source:
    name: str
    url: str
    fetched_at: datetime


@dataclass
class Result:
    data: Any = None
    as_of: str | None = None  # the date the data describes (trade date, obs date), not fetch time
    sources: list[Source] = field(default_factory=list)
    error: DrishtiError | None = None

    @property
    def ok(self) -> bool:
        return self.error is None

    def to_dict(self) -> dict:
        return {
            "data": self.data,
            "as_of": self.as_of,
            "sources": [{"name": s.name, "url": s.url, "fetched_at": s.fetched_at.isoformat()} for s in self.sources],
            "error": self.error.to_dict() if self.error else None,
        }


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def guard(fn: Callable[..., Result]) -> Callable[..., Result]:
    """Wrap a provider function: any exception becomes a typed error Result.

    DrishtiError keeps its kind; anything else raised while parsing (KeyError,
    bad JSON, StopIteration...) is PARSE_FAIL — same rule as probe.py's run_probe.
    """
    @functools.wraps(fn)
    def wrapper(*args, **kwargs) -> Result:
        try:
            return fn(*args, **kwargs)
        except DrishtiError as e:
            err = e
        except Exception as e:  # noqa: BLE001 - classify any parser failure
            err = DrishtiError(ErrorKind.PARSE_FAIL, f"{type(e).__name__}: {str(e)[:200]}")
        log.warning("%s failed: %s", fn.__qualname__, err)
        return Result(error=err)
    return wrapper

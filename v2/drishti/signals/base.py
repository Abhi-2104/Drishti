"""Shared pieces for signals: parameters, the Signal record, and the conversion to the usual Result envelope.

A signal is a pure function of the store and a date: it reads history up to that date and never fetches, so the same
store and date always give the same reading, and old dates can be recomputed for testing.
"""
from __future__ import annotations

import hashlib
import tomllib
from dataclasses import asdict, dataclass, field
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

from ..core.envelope import DrishtiError, ErrorKind, Result

PARAMS_PATH = Path(__file__).with_name("params.toml")


@lru_cache(maxsize=4)
def _load(path: str, mtime: float) -> tuple[dict, str]:
    raw = Path(path).read_bytes()
    p = tomllib.loads(raw.decode("utf-8"))
    return p, f"{p.get('version', '?')}+{hashlib.sha1(raw).hexdigest()[:8]}"


def params(section: str) -> tuple[dict, str]:
    """(section params, version). The version includes a hash of the file, so an edit without bumping it still shows."""
    p, v = _load(str(PARAMS_PATH), PARAMS_PATH.stat().st_mtime)
    if section not in p:
        raise KeyError(f"params.toml has no [{section}]")
    return p[section], v


@dataclass
class Component:
    input: str
    value: Any
    as_of: str | None = None
    rule: str = ""
    contribution: float | None = None
    text: str = ""


@dataclass
class Signal:
    name: str
    as_of: str
    score: float | None
    label: str
    components: list[Component] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    note: str = ""
    params_version: str = ""
    provisional: bool = False
    details: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def ok(sig: Signal) -> Result:
    return Result(data=sig.to_dict(), as_of=sig.as_of)


def unknown(name: str, why: str) -> Result:
    """No reading possible: an explicit UNKNOWN as a typed NOT_FOUND, never a silent neutral."""
    return Result(error=DrishtiError(ErrorKind.NOT_FOUND, f"{name}: UNKNOWN (not neutral): {why}"))


def iso(d: date | str | None) -> str | None:
    return d.isoformat() if isinstance(d, date) else d

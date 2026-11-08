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
SETTINGS_FILE = "signal-settings.toml"
_override_path: Path | None = None


class SettingsError(ValueError):
    """signal-settings.toml has a key or value the code does not know: refuse rather than silently ignore a setting."""


def use_settings(path: Path | None) -> None:
    """Called by entry points (CLI, MCP server, jobs) with this install's signal-settings.toml. Tests never call it,
    so they always run on the shipped defaults."""
    global _override_path
    _override_path = Path(path) if path else None


def _load(path: str) -> tuple[dict, str]:
    return _parse_params(Path(path).read_bytes())


@lru_cache(maxsize=4)
def _parse_params(raw: bytes) -> tuple[dict, str]:
    p = tomllib.loads(raw.decode("utf-8"))
    return p, f"{p.get('version', '?')}+{hashlib.sha1(raw).hexdigest()[:8]}"


def _load_override(path: str) -> tuple[dict, str]:
    # Keyed on the file's bytes, not its mtime: Windows file times are too coarse to see two saves in one second.
    raw = Path(path).read_bytes()
    return _parse_override(path, raw)


@lru_cache(maxsize=8)
def _parse_override(path: str, raw: bytes) -> tuple[dict, str]:
    try:
        return tomllib.loads(raw.decode("utf-8")), hashlib.sha1(raw).hexdigest()[:8]
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as e:
        raise SettingsError(f"{path}: not valid TOML ({e})") from e


def params(section: str) -> tuple[dict, str]:
    """(section params, version). Shipped defaults from params.toml, overlaid with this install's signal-settings.toml.
    The version names both, so a reading can always be traced to the exact numbers it used."""
    p, v = _load(str(PARAMS_PATH))
    if section not in p:
        raise KeyError(f"params.toml has no [{section}]")
    out = dict(p[section])
    if _override_path is not None and _override_path.exists():
        o, h = _load_override(str(_override_path))
        for sec, vals in o.items():
            if sec not in p or not isinstance(vals, dict):
                raise SettingsError(f"{_override_path}: unknown section [{sec}]")
            bad = sorted(set(vals) - set(p[sec]))
            if bad:
                raise SettingsError(f"{_override_path}: [{sec}] has unknown setting(s) {bad}")
            for k, val in vals.items():
                if type(val) is not type(p[sec][k]) and not (isinstance(val, (int, float)) and isinstance(p[sec][k], (int, float))):
                    raise SettingsError(f"{_override_path}: [{sec}] {k} should be like {p[sec][k]!r}, got {val!r}")
        out.update(o.get(section, {}))
        if o:
            v = f"{v}+local:{h}"
    return out, v


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

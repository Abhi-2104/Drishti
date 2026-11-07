"""The investor profile: goals, risk limits, sizing, watchlist. One per install (each brother has his own).

Read by decisions and tools ONLY. Providers and signals stay profile-neutral and must never import this
module (tests/test_profile.py enforces that on every module that exists).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import ConfigError, Settings, read_toml, reject_unknown
from .core.isin import is_valid_isin, normalize_isin

SIZING_METHODS = ("equal", "conviction")
_SYMBOL = re.compile(r"^[A-Z0-9&\-]{1,20}$")  # NSE symbols include M&M, BAJAJ-AUTO
_SCRIP = re.compile(r"^\d{5,6}$")


@dataclass(frozen=True)
class Goals:
    target_xirr_pct: float = 25.0
    horizon_years: int | None = None


@dataclass(frozen=True)
class Risk:
    max_position_pct: float | None = None
    max_sector_pct: float | None = None


@dataclass(frozen=True)
class Sizing:
    method: str | None = None


@dataclass(frozen=True)
class WatchItem:
    isin: str
    symbol: str | None = None
    bse_scrip: str | None = None
    name: str | None = None


@dataclass(frozen=True)
class Profile:
    name: str = ""
    goals: Goals = field(default_factory=Goals)
    risk: Risk = field(default_factory=Risk)
    sizing: Sizing = field(default_factory=Sizing)
    watchlist: tuple[WatchItem, ...] = ()

    def watch_isins(self) -> list[str]:
        return [w.isin for w in self.watchlist]

    def find(self, *, isin: str | None = None, symbol: str | None = None, bse_scrip: str | None = None) -> WatchItem | None:
        for w in self.watchlist:
            if (isin and w.isin == normalize_isin(isin)) or (symbol and w.symbol == symbol.strip().upper()) \
                    or (bse_scrip and w.bse_scrip == str(bse_scrip).strip()):
                return w
        return None


def _num(v: Any, where: str, path: Path | None, *, lo: float, hi: float) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ConfigError(f"{where} must be a number, got {v!r}", path)
    if not (lo < v <= hi):
        raise ConfigError(f"{where} must be > {lo} and <= {hi}, got {v}", path)
    return float(v)


def _table(raw: dict, key: str, path: Path | None) -> dict:
    t = raw.get(key, {})
    if not isinstance(t, dict):
        raise ConfigError(f"[{key}] must be a table", path)
    return t


def _str_or_none(v: Any, where: str, path: Path | None) -> str | None:
    if v is None:
        return None
    if not isinstance(v, str):
        raise ConfigError(f"{where} must be a string, got {v!r}", path)
    return v.strip() or None


def _watch_item(i: int, t: Any, path: Path | None) -> WatchItem:
    where = f"watchlist[{i}]"
    if not isinstance(t, dict):
        raise ConfigError(f"{where} must be a table", path)
    reject_unknown(t, {"isin", "symbol", "bse_scrip", "name"}, where, path)
    isin = normalize_isin(t.get("isin"))
    if not isin:
        raise ConfigError(f"{where}: isin is required (it is the join key)", path)
    if not is_valid_isin(isin):
        raise ConfigError(f"{where}: {isin!r} is not a valid Indian ISIN (format or check digit wrong)", path)
    symbol = _str_or_none(t.get("symbol"), f"{where}.symbol", path)
    symbol = symbol.upper() if symbol else None
    if symbol and not _SYMBOL.match(symbol):
        raise ConfigError(f"{where}: symbol {symbol!r} looks wrong (letters, digits, & and - only)", path)
    scrip = t.get("bse_scrip")
    scrip = None if scrip is None else str(scrip).strip() or None  # accept 500325 or "500325"
    if scrip and not _SCRIP.match(scrip):
        raise ConfigError(f"{where}: bse_scrip {scrip!r} must be a 5-6 digit code", path)
    if not symbol and not scrip:
        raise ConfigError(f"{where} ({isin}): give an NSE symbol and/or a BSE scrip code", path)
    return WatchItem(isin, symbol, scrip, _str_or_none(t.get("name"), f"{where}.name", path))


def parse_profile(raw: dict[str, Any], path: Path | None = None) -> Profile:
    reject_unknown(raw, {"name", "goals", "risk", "sizing", "watchlist"}, "profile", path)
    name = raw.get("name", "")
    if not isinstance(name, str):
        raise ConfigError("name must be a string", path)

    g = _table(raw, "goals", path)
    reject_unknown(g, {"target_xirr_pct", "horizon_years"}, "[goals]", path)
    goals = Goals(
        target_xirr_pct=_num(g["target_xirr_pct"], "goals.target_xirr_pct", path, lo=0, hi=200) if "target_xirr_pct" in g else 25.0,
        horizon_years=int(_num(g["horizon_years"], "goals.horizon_years", path, lo=0, hi=60)) if "horizon_years" in g else None,
    )
    if "horizon_years" in g and float(g["horizon_years"]) != int(g["horizon_years"]):
        raise ConfigError("goals.horizon_years must be a whole number", path)

    r = _table(raw, "risk", path)
    reject_unknown(r, {"max_position_pct", "max_sector_pct"}, "[risk]", path)
    risk = Risk(**{k: _num(r[k], f"risk.{k}", path, lo=0, hi=100) for k in r})

    s = _table(raw, "sizing", path)
    reject_unknown(s, {"method"}, "[sizing]", path)
    method = _str_or_none(s.get("method"), "sizing.method", path)
    if method is not None and method not in SIZING_METHODS:
        raise ConfigError(f"sizing.method must be one of {SIZING_METHODS}, got {method!r}", path)

    wl_raw = raw.get("watchlist", [])
    if not isinstance(wl_raw, list):
        raise ConfigError("watchlist must be an array of [[watchlist]] tables", path)
    items = [_watch_item(i, t, path) for i, t in enumerate(wl_raw)]
    seen: set[str] = set()
    for w in items:
        if w.isin in seen:
            raise ConfigError(f"watchlist: duplicate ISIN {w.isin}", path)
        seen.add(w.isin)
    return Profile(name.strip(), goals, risk, Sizing(method), tuple(items))


def load_profile(path_or_settings: Path | Settings) -> Profile:
    path = path_or_settings.profile_path if isinstance(path_or_settings, Settings) else Path(path_or_settings)
    raw = read_toml(path)
    if raw is None:
        raise ConfigError("profile not found; run `drishti init` to create a template", path)
    return parse_profile(raw, path)


def seed_watchlist(store, profile: Profile) -> int:
    """Put the watchlist into the store's security master (source='profile'), so BSE-only names resolve
    before the exchange files are loaded. Merges, never blanks. Returns the number of securities written."""
    for w in profile.watchlist:
        store.upsert_security(w.isin, name=w.name, nse_symbol=w.symbol, bse_scrip=w.bse_scrip, source="profile")
    return len(profile.watchlist)

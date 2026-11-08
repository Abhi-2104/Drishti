"""Read history out of the typed tables, always 'as of' a date (nothing after it), so past readings can be recomputed."""
from __future__ import annotations

from datetime import date, timedelta

from ..store import datasets as ds


def _d(on) -> str:
    return (on or date.today()).isoformat() if not isinstance(on, str) else on


def _exists(store, table: str) -> bool:
    return bool(store.query("SELECT 1 AS x FROM sqlite_master WHERE type='table' AND name=?", (table,)))


def index_series(store, name: str, on=None, *, days: int = 400) -> list[tuple[str, float]]:
    """[(date, close)] ascending for one NSE index, from `days` calendar days before `on` up to `on`."""
    if not _exists(store, "index_close"):
        return []
    end = _d(on)
    start = (date.fromisoformat(end) - timedelta(days=days)).isoformat()
    rows = store.query("SELECT trade_date, close FROM index_close WHERE index_name=? AND trade_date BETWEEN ? AND ? ORDER BY trade_date",
                       (name, start, end))
    return [(r["trade_date"], r["close"]) for r in rows]


def latest_index_date(store, on=None) -> str | None:
    if not _exists(store, "index_close"):
        return None
    r = store.query("SELECT MAX(trade_date) AS d FROM index_close WHERE trade_date <= ?", (_d(on),))
    return r[0]["d"]


def participant_days(store, on=None, n: int = 2) -> list[tuple[str, dict]]:
    """Newest-first [(date, {client_type: row})] for the last n trading days with data, up to `on`."""
    if not _exists(store, "participant_oi"):
        return []
    dates = [r["trade_date"] for r in store.query(
        "SELECT DISTINCT trade_date FROM participant_oi WHERE trade_date <= ? ORDER BY trade_date DESC LIMIT ?", (_d(on), n))]
    out = []
    for d in dates:
        out.append((d, {r["client_type"]: r for r in store.query("SELECT * FROM participant_oi WHERE trade_date=?", (d,))}))
    return out


def macro_points(store, series: str, on=None, n: int = 2) -> list[tuple[str, float]]:
    """Newest-first last n observations of a macro series up to `on`."""
    if not _exists(store, "macro_series"):
        return []
    rows = store.query("SELECT obs_date, value FROM macro_series WHERE series=? AND obs_date <= ? ORDER BY obs_date DESC LIMIT ?",
                       (series, _d(on), n))
    return [(r["obs_date"], r["value"]) for r in rows]


def macro_on_or_before(store, series: str, day: str) -> tuple[str, float] | None:
    p = macro_points(store, series, day, 1)
    return p[0] if p else None


def fii_dii_days(store, on=None, n: int = 20) -> list[dict]:
    if not _exists(store, "fii_dii_cash"):
        return []
    return store.query("SELECT * FROM fii_dii_cash WHERE trade_date <= ? ORDER BY trade_date DESC LIMIT ?", (_d(on), n))


def option_chain(store, symbol: str, on=None) -> tuple[str | None, list[dict]]:
    """(trade_date, rows) for the newest trading day <= on that has this index's options stored."""
    if not _exists(store, "index_option_eod"):
        return None, []
    r = store.query("SELECT MAX(trade_date) AS d FROM index_option_eod WHERE symbol=? AND trade_date <= ?", (symbol, _d(on)))
    d = r[0]["d"]
    if not d:
        return None, []
    return d, store.query("SELECT * FROM index_option_eod WHERE symbol=? AND trade_date=?", (symbol, d))


def ensure_all(store) -> None:
    ds.ensure(store)

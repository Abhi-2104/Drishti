"""Holdings in the database: what you own now (snapshots) and the buys and sells behind it (transactions).

`holding_snapshot` is one row per (as_of, ISIN): quantity and average cost from a broker statement. `holding_txn` is one row per
trade with a date; portfolio XIRR needs those dates, so until every held stock has its full buy history the summary reports
cost-only profit and says XIRR is not available. Nothing is guessed: a row that fails a check is rejected with its reason.

Both importers are idempotent (re-importing the same file changes nothing). Data never leaves the database and the private
folder: this module only reads the CSV you point it at.
"""
from __future__ import annotations

import csv
import hashlib
from datetime import date, datetime
from pathlib import Path

from ..core.canary import IST
from ..core.envelope import DrishtiError, ErrorKind, Result
from ..core.isin import is_valid_isin, normalize_isin
from ..store import Store

DDL = """
CREATE TABLE IF NOT EXISTS holding_snapshot (
    as_of TEXT NOT NULL, isin TEXT NOT NULL, symbol TEXT, name TEXT, kind TEXT NOT NULL, quantity REAL NOT NULL, avg_cost REAL NOT NULL,
    source TEXT, imported_at TEXT NOT NULL, PRIMARY KEY (as_of, isin));
CREATE INDEX IF NOT EXISTS ix_holding_snapshot_isin ON holding_snapshot (isin, as_of);
CREATE TABLE IF NOT EXISTS holding_txn (
    txn_key TEXT PRIMARY KEY, trade_date TEXT NOT NULL, isin TEXT NOT NULL, quantity REAL NOT NULL, price REAL NOT NULL,
    fees REAL NOT NULL DEFAULT 0, source TEXT, imported_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_holding_txn_isin ON holding_txn (isin, trade_date)"""

KINDS = ("stock", "fund")
SNAPSHOT_COLS = ("isin", "symbol", "name", "kind", "quantity", "avg_cost", "source", "as_of")
TXN_COLS = ("date", "isin", "side", "quantity", "price")        # fees and source are optional columns


def ensure(store: Store) -> None:
    store.ensure_table(DDL)


def _now() -> str:
    return datetime.now(IST).isoformat(timespec="seconds")


def _rows(path: Path, required: tuple[str, ...], label: str) -> list[dict]:
    p = Path(path)
    if not p.is_file():
        raise DrishtiError(ErrorKind.NOT_FOUND, f"{label}: {p} does not exist")
    with p.open(encoding="utf-8-sig", newline="") as f:
        rd = csv.DictReader(f)
        miss = [c for c in required if c not in (rd.fieldnames or [])]
        if miss:
            raise DrishtiError(ErrorKind.PARSE_FAIL, f"{label}: missing column(s) {', '.join(miss)}; expected {', '.join(required)}")
        rows = list(rd)
    if not rows:
        raise DrishtiError(ErrorKind.EMPTY, f"{label}: no rows in {p}")
    return rows


def _num(v, what: str, *, positive: bool = True) -> float:
    try:
        x = float(str(v).replace(",", "").strip())
    except ValueError:
        raise ValueError(f"{what} {v!r} is not a number") from None
    if x != x or (positive and x <= 0) or (not positive and x < 0):
        raise ValueError(f"{what} {v!r} must be {'above' if positive else 'at least'} zero")
    return x


def _day(v) -> str:
    try:
        return date.fromisoformat(str(v).strip()).isoformat()
    except ValueError:
        raise ValueError(f"date {v!r} is not YYYY-MM-DD") from None


def import_snapshot_csv(store: Store, path: Path) -> Result:
    """`isin,symbol,name,kind,quantity,avg_cost,source,as_of` (kind = stock | fund). Returns counts and every rejected line."""
    try:
        rows = _rows(path, SNAPSHOT_COLS, "holdings")
    except DrishtiError as e:
        return Result(error=e)
    ensure(store)
    good, rejected = [], []
    for n, r in enumerate(rows, 2):
        try:
            isin = normalize_isin(r["isin"])
            if not is_valid_isin(isin):
                raise ValueError(f"isin {r['isin']!r} is not a valid ISIN")
            kind = (r["kind"] or "").strip().lower()
            if kind not in KINDS:
                raise ValueError(f"kind {r['kind']!r} must be one of {', '.join(KINDS)}")
            good.append({"as_of": _day(r["as_of"]), "isin": isin, "symbol": (r["symbol"] or "").strip().upper() or None,
                         "name": (r["name"] or "").strip() or None, "kind": kind, "quantity": _num(r["quantity"], "quantity"),
                         "avg_cost": _num(r["avg_cost"], "avg_cost", positive=False), "source": (r["source"] or "").strip() or None,
                         "imported_at": _now()})
        except ValueError as e:
            rejected.append({"line": n, "why": str(e)})
    seen, dup = set(), []
    for g in good:
        k = (g["as_of"], g["isin"])
        if k in seen:
            dup.append(k)
        seen.add(k)
    if dup:       # two lines for one stock on one date would silently overwrite each other
        return Result(error=DrishtiError(ErrorKind.REJECTED, f"holdings: {len(dup)} ISIN(s) appear twice on the same as_of, e.g. {dup[0][1]}; merge them first"))
    n = store.upsert("holding_snapshot", good, ("as_of", "isin"))
    return Result(data={"imported": n, "rejected": rejected, "as_of": sorted({g["as_of"] for g in good})},
                  as_of=max((g["as_of"] for g in good), default=None))


def _txn_key(t: dict) -> str:
    raw = "|".join(str(t[k]) for k in ("trade_date", "isin", "quantity", "price", "fees", "source"))
    return hashlib.sha1(raw.encode()).hexdigest()[:20]


def import_txn_csv(store: Store, path: Path) -> Result:
    """`date,isin,side,quantity,price[,fees][,source]` with side = buy | sell. A sell is stored as a negative quantity."""
    try:
        rows = _rows(path, TXN_COLS, "transactions")
    except DrishtiError as e:
        return Result(error=e)
    ensure(store)
    good, rejected = [], []
    for n, r in enumerate(rows, 2):
        try:
            isin = normalize_isin(r["isin"])
            if not is_valid_isin(isin):
                raise ValueError(f"isin {r['isin']!r} is not a valid ISIN")
            side = (r["side"] or "").strip().lower()
            if side not in ("buy", "sell"):
                raise ValueError(f"side {r['side']!r} must be buy or sell")
            qty = _num(r["quantity"], "quantity")
            t = {"trade_date": _day(r["date"]), "isin": isin, "quantity": qty if side == "buy" else -qty, "price": _num(r["price"], "price", positive=False),
                 "fees": _num(r.get("fees") or 0, "fees", positive=False), "source": (r.get("source") or "").strip() or None, "imported_at": _now()}
            t["txn_key"] = _txn_key(t)
            good.append(t)
        except ValueError as e:
            rejected.append({"line": n, "why": str(e)})
    n = store.upsert("holding_txn", good, ("txn_key",))
    return Result(data={"imported": n, "rejected": rejected}, as_of=max((g["trade_date"] for g in good), default=None))


def latest_snapshot(store: Store) -> list[dict]:
    """The newest snapshot: every holding on the latest as_of date."""
    ensure(store)
    row = store.query("SELECT MAX(as_of) AS d FROM holding_snapshot")
    if not row or not row[0]["d"]:
        return []
    return store.query("SELECT * FROM holding_snapshot WHERE as_of=? ORDER BY kind DESC, name, isin", (row[0]["d"],))


def _last_close(store: Store, isin: str) -> tuple[float, str] | None:
    r = store.query("SELECT close, trade_date FROM price_eod WHERE isin=? ORDER BY trade_date DESC, exchange LIMIT 1", (isin,)) \
        if store.query("SELECT 1 AS x FROM sqlite_master WHERE name='price_eod'") else []
    return (r[0]["close"], r[0]["trade_date"]) if r else None


def summary(store: Store) -> dict:
    """Per holding: cost, latest value, profit, and whether the stored transactions fully explain the quantity. Funds have no NAV
    data here, so they are shown at cost and left out of value, profit and XIRR."""
    snap = latest_snapshot(store)
    out = {"as_of": snap[0]["as_of"] if snap else None, "holdings": [], "totals": {}, "xirr": None, "xirr_note": ""}
    if not snap:
        out["xirr_note"] = "no holdings imported: run `drishti holdings import <file>`"
        return out
    txns: dict[str, list[dict]] = {}
    for t in store.query("SELECT * FROM holding_txn ORDER BY trade_date, txn_key"):
        txns.setdefault(t["isin"], []).append(t)
    cost_all = cost_priced = value = 0.0
    incomplete = []
    for h in snap:
        cost = h["quantity"] * h["avg_cost"]
        cost_all += cost
        lc = _last_close(store, h["isin"]) if h["kind"] == "stock" else None
        t = txns.get(h["isin"], [])
        complete = bool(t) and abs(sum(x["quantity"] for x in t) - h["quantity"]) < 1e-6
        if h["kind"] == "stock" and not complete:
            incomplete.append(h["symbol"] or h["isin"])
        row = {"isin": h["isin"], "symbol": h["symbol"], "name": h["name"], "kind": h["kind"], "quantity": h["quantity"], "avg_cost": h["avg_cost"],
               "cost": round(cost, 2), "last_close": lc[0] if lc else None, "price_date": lc[1] if lc else None,
               "value": round(h["quantity"] * lc[0], 2) if lc else None, "txns": len(t), "history_complete": complete}
        if lc:
            cost_priced += cost
            value += row["value"]
            row["pnl"] = round(row["value"] - cost, 2)
            row["pnl_pct"] = round(row["pnl"] / cost * 100, 2) if cost else None
        out["holdings"].append(row)
    out["totals"] = {"cost_all": round(cost_all, 2), "cost_priced": round(cost_priced, 2), "value_priced": round(value, 2),
                     "pnl_priced": round(value - cost_priced, 2), "pnl_priced_pct": round((value - cost_priced) / cost_priced * 100, 2) if cost_priced else None,
                     "unpriced": [r["symbol"] or r["isin"] for r in out["holdings"] if r["value"] is None]}
    stocks = [r for r in out["holdings"] if r["kind"] == "stock" and r["value"] is not None]
    if not stocks:
        out["xirr_note"] = "no priced stocks"
    elif incomplete:
        out["xirr_note"] = f"XIRR needs every stock's dated buys: missing or partial for {', '.join(incomplete)}"
    else:
        # a buy (quantity > 0) is cash out, a sell (quantity < 0) is cash in; fees always reduce what you hold
        flows = [(t["trade_date"], -(t["quantity"] * t["price"]) - t["fees"]) for r in stocks for t in txns[r["isin"]]]
        end = max(r["price_date"] for r in stocks)
        flows.append((end, sum(r["value"] for r in stocks)))
        from ..decisions.portfolio import xirr
        x = xirr(flows)
        out["xirr"] = None if x is None else round(x * 100, 2)
        out["xirr_note"] = "stocks only, price return, before tax, funds excluded" if x is not None else "cash flows do not change sign"
    return out

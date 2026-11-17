"""Cheap watchlist monitor (the /brief loop): one compact row per watched name, only what is new since the last scan, and an
ESCALATE flag where an open thesis needs a look. No analysis here: scan, dedupe, escalate. Reads the profile (the watchlist)."""
from __future__ import annotations

from datetime import date, timedelta

from .. import signals
from ..core.envelope import DrishtiError, ErrorKind, Result
from . import log, thesis

STATE_DDL = "CREATE TABLE IF NOT EXISTS scan_state (key TEXT PRIMARY KEY, value TEXT NOT NULL)"


def _last_scan(store) -> str | None:
    store.ensure_table(STATE_DDL)
    r = store.query("SELECT value FROM scan_state WHERE key='watch_last'")
    return r[0]["value"] if r else None


def scan(store, profile, on: date, *, since: str | None = None, mark: bool = False) -> Result:
    if profile is None or not profile.watchlist:
        return Result(error=DrishtiError(ErrorKind.EMPTY, "the watchlist is empty: add names under [[watchlist]] in your profile (drishti paths shows where)"))
    last = since or _last_scan(store) or (on - timedelta(days=7)).isoformat()
    open_th = {t["isin"]: t for t in thesis.open_all(store)}
    rows, escalate = [], []
    has_ins = bool(store.query("SELECT 1 AS x FROM sqlite_master WHERE name='insider_event'"))
    for w in profile.watchlist:
        name = w.symbol or w.name or w.isin
        row = {"name": name, "isin": w.isin, "new": [], "flags": []}
        p = signals.compute(store, "price_context", on, isin=w.isin)
        if p.ok:
            d, comps = p.data["details"], {c["input"]: c["value"] for c in p.data["components"]}
            row.update(close=d["close"], as_of=p.data["as_of"], trend=p.data["label"], ret_1m_3m_6m_1y=comps.get("return 1m / 3m / 6m / 1y %"),
                       vs_50dma_pct=round((d["close"] / d["dma"]["50"] - 1) * 100, 1) if d["dma"]["50"] else None)
        else:
            row["flags"].append(f"no price reading: {p.error.detail[:90]}")
        if has_ins:
            ev = store.query("SELECT direction, COUNT(*) AS n, SUM(value_inr) AS v FROM insider_event WHERE (isin=? OR symbol=?) AND to_date > ? AND direction IN ('BUY','SELL') GROUP BY direction",
                             (w.isin, w.symbol or "", last))
            for e in ev:
                row["new"].append(f"insider {e['direction'].lower()}s since {last}: {e['n']} trades, Rs {(e['v'] or 0) / 1e7:.2f} crore")
        deals = store.query("SELECT kind, side, COUNT(*) AS n, SUM(qty) AS q FROM deal WHERE symbol=? AND trade_date > ? GROUP BY kind, side", (w.symbol or "", last)) \
            if store.query("SELECT 1 AS x FROM sqlite_master WHERE name='deal'") else []
        for e in deals:
            row["new"].append(f"{e['kind']} deal {e['side'].lower()}: {e['n']} client(s), {e['q']:,} shares")
        if store.query("SELECT 1 AS x FROM sqlite_master WHERE name='announcement'"):
            a = store.query("SELECT COUNT(*) AS n, SUM(critical) AS c FROM announcement WHERE isin=? AND published > ?", (w.isin, last))[0]
            if a["n"]:
                row["new"].append(f"{a['n']} new announcements" + (f" ({a['c']} marked critical)" if a["c"] else ""))
        for sig in ("pledge", "insider_cluster"):
            r = signals.compute(store, sig, on, isin=w.isin) if sig == "pledge" else signals.compute(store, sig, on, isin=w.isin, symbol=w.symbol)
            if r.ok:
                row["flags"] += r.data["details"].get("flags", [])
        t = open_th.get(w.isin)
        if t and p.ok:
            c = row["close"]
            hit = [f"STOP HIT: close {c} at or below stop {t['stop']}"] if t["stop"] is not None and c <= t["stop"] else []
            hit += [f"TARGET HIT: close {c} at or above target {t['target']}"] if t["target"] is not None and c >= t["target"] else []
            red = [f for f in row["flags"] if "selling cluster" in f or "pledge" in f]
            row["thesis"] = {"id": t["id"], "stop": t["stop"], "target": t["target"], "since": t["as_of"]}
            if hit or red:
                row["escalate"] = "exit"
                escalate.append({"name": name, "command": f"/drishti-exit {name}", "why": hit + red})
        rows.append(row)
    if mark:
        store.ensure_table(STATE_DDL)
        store.upsert("scan_state", [{"key": "watch_last", "value": on.isoformat()}], key=("key",))
    return Result(data={"since": last, "scanned": len(rows), "names": rows, "escalate": escalate, "marked": mark}, as_of=on.isoformat())

"""Thesis log: for a name you hold or plan to, the thesis, entry, stop, target, size, what must stay true and the exit triggers,
plus a re-check history. Append-only in spirit: a new thesis closes the previous open one for that stock. Separate from the decision
log, which is the scored record; this is the working note /exit reads."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from ..core.envelope import DrishtiError, ErrorKind, Result

DDL = """
CREATE TABLE IF NOT EXISTS thesis (
    id TEXT PRIMARY KEY, created_at TEXT NOT NULL, as_of TEXT NOT NULL, isin TEXT NOT NULL, symbol TEXT, thesis TEXT NOT NULL,
    entry_low REAL, entry_high REAL, stop REAL, target REAL, size_pct REAL, must_stay_true TEXT, exit_triggers TEXT,
    decision_id TEXT, status TEXT NOT NULL DEFAULT 'open', closed_at TEXT, closed_note TEXT);
CREATE INDEX IF NOT EXISTS ix_thesis_isin ON thesis (isin, status);
CREATE TABLE IF NOT EXISTS thesis_recheck (
    id TEXT PRIMARY KEY, thesis_id TEXT NOT NULL, checked_at TEXT NOT NULL, as_of TEXT NOT NULL, intact INTEGER NOT NULL, note TEXT NOT NULL)
"""


def ensure(store) -> None:
    store.ensure_table(DDL)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def save(store, *, isin: str, symbol: str | None, as_of: str, thesis: str, entry_low=None, entry_high=None, stop=None, target=None,
         size_pct=None, must_stay_true=None, exit_triggers=None, decision_id=None) -> Result:
    if not thesis or not thesis.strip():
        return Result(error=DrishtiError(ErrorKind.PARSE_FAIL, "a thesis needs the thesis text"))
    if not exit_triggers:
        return Result(error=DrishtiError(ErrorKind.PARSE_FAIL, "a thesis needs at least one exit trigger you can observe (a level, a reading, a dated event)"))
    if stop is not None and entry_low is not None and stop >= entry_low:
        return Result(error=DrishtiError(ErrorKind.PARSE_FAIL, "the stop must be below the entry zone"))
    ensure(store)
    now = _now()
    store._run("UPDATE thesis SET status='closed', closed_at=?, closed_note='replaced by a newer thesis' WHERE isin=? AND status='open'", (now, isin))
    tid = f"th_{uuid.uuid4().hex[:10]}"
    store.upsert("thesis", [dict(id=tid, created_at=now, as_of=as_of, isin=isin, symbol=symbol, thesis=thesis.strip(), entry_low=entry_low,
                                 entry_high=entry_high, stop=stop, target=target, size_pct=size_pct,
                                 must_stay_true=json.dumps(must_stay_true or []), exit_triggers=json.dumps(exit_triggers),
                                 decision_id=decision_id, status="open", closed_at=None, closed_note=None)], key=("id",))
    return Result(data={"id": tid}, as_of=as_of)


def _row(r: dict, rechecks: list[dict]) -> dict:
    return {**r, "must_stay_true": json.loads(r["must_stay_true"] or "[]"), "exit_triggers": json.loads(r["exit_triggers"] or "[]"),
            "rechecks": rechecks}


def get(store, isin: str, *, include_closed: bool = False) -> Result:
    ensure(store)
    rows = store.query("SELECT * FROM thesis WHERE isin=?" + ("" if include_closed else " AND status='open'") + " ORDER BY created_at DESC", (isin,))
    out = [_row(r, store.query("SELECT checked_at, as_of, intact, note FROM thesis_recheck WHERE thesis_id=? ORDER BY checked_at", (r["id"],))) for r in rows]
    return Result(data={"theses": out, "n": len(out)}, as_of=out[0]["as_of"] if out else None)


def recheck(store, thesis_id: str, *, as_of: str, intact: bool, note: str, close: bool = False) -> Result:
    ensure(store)
    if not store.query("SELECT 1 AS x FROM thesis WHERE id=?", (thesis_id,)):
        return Result(error=DrishtiError(ErrorKind.NOT_FOUND, f"no thesis {thesis_id}"))
    if not note.strip():
        return Result(error=DrishtiError(ErrorKind.PARSE_FAIL, "a re-check needs a note saying what was checked and what changed"))
    now = _now()
    store.upsert("thesis_recheck", [dict(id=f"rc_{uuid.uuid4().hex[:10]}", thesis_id=thesis_id, checked_at=now, as_of=as_of, intact=int(intact), note=note.strip())], key=("id",))
    if close:
        store._run("UPDATE thesis SET status='closed', closed_at=?, closed_note=? WHERE id=?", (now, note.strip(), thesis_id))
    return Result(data={"thesis_id": thesis_id, "intact": intact, "closed": close}, as_of=as_of)


def open_all(store) -> list[dict]:
    ensure(store)
    return store.query("SELECT * FROM thesis WHERE status='open'")

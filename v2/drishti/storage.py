"""`drishti storage`: how big is the database and which tables take the space. Read-only."""
from __future__ import annotations


def report(store) -> dict:
    tables = [r["name"] for r in store.query("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    size: dict[str, int] = {}
    try:      # dbstat gives bytes per table (tables and their indexes together); not every SQLite build has it
        idx = {r["name"]: r["tbl_name"] for r in store.query("SELECT name, tbl_name FROM sqlite_master WHERE type='index'")}
        for r in store.query("SELECT name, SUM(pgsize) AS b FROM dbstat GROUP BY name"):
            t = idx.get(r["name"], r["name"])
            size[t] = size.get(t, 0) + (r["b"] or 0)
    except Exception:
        size = {}
    out = []
    for t in tables:
        n = store.query(f'SELECT COUNT(*) AS n FROM "{t}"')[0]["n"]
        out.append({"table": t, "rows": n, "mb": round(size.get(t, 0) / 1e6, 1) if size else None})
    out.sort(key=lambda r: -(r["mb"] or 0) if size else -r["rows"])
    return {"tables": out, "total_mb": round(sum(size.values()) / 1e6, 1) if size else None,
            "note": None if size else "this SQLite build has no dbstat, so only row counts are shown"}

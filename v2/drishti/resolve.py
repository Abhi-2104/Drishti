"""Resolve whatever the user typed (ISIN, NSE symbol, BSE symbol, BSE scrip code) to the security-master row(s).

The answer is always a list: symbols get reused over time, and an NSE symbol can equal a different company's BSE symbol.
`ambiguous` is True when more than one ISIN matched; callers must then ask, never pick.
"""
from __future__ import annotations

from .core.envelope import DrishtiError, ErrorKind, Result, guard
from .core.isin import is_valid_isin, normalize_isin


@guard
def resolve(store, query: str) -> Result:
    q = (query or "").strip().upper()
    if not q:
        raise DrishtiError(ErrorKind.NOT_FOUND, "resolve: empty query")
    matches: dict[str, dict] = {}
    if q.startswith("IN") and len(q) == 12:
        isin = normalize_isin(q)
        if not is_valid_isin(isin):
            raise DrishtiError(ErrorKind.PARSE_FAIL, f"resolve: {q} has the shape of an ISIN but fails its check digit; typo?")
        row = store.get_security(isin)
        kind = "isin"
        if row:
            matches[row["isin"]] = row
    elif q.isdigit():
        kind = "bse_scrip"
        for row in store.find_securities(bse_scrip=q):
            matches[row["isin"]] = row
    else:
        kind = "symbol"
        for row in store.find_securities(nse_symbol=q) + store.find_securities(bse_symbol=q):
            matches[row["isin"]] = row
    if not matches:
        hint = [f"{r['name']} ({r['nse_symbol'] or r['bse_symbol']})" for r in store.search_securities(query, limit=5)]
        raise DrishtiError(ErrorKind.NOT_FOUND, f"resolve: nothing matches {query!r}" + (f"; did you mean {hint}?" if hint else ""))
    rows = sorted(matches.values(), key=lambda r: r["isin"])
    return Result(data={"query": query, "kind": kind, "ambiguous": len(rows) > 1, "matches": rows},
                  as_of=max(r["updated_at"] for r in rows))

"""The decision log: every call an agent or the rule baseline makes is written here, once, and never edited.

Without this there is nothing to grade. A decision is a stance on a stock or on the market, with a horizon, a reason, what
would prove it wrong, how sure the author is, and which stored signals it leaned on. The log recomputes every cited signal
itself (an author cannot cite a reading that was UNKNOWN or does not exist), so the record shows what the data said.

Rules kept here:
  * Append only. A correction is a new row whose `supersedes` names the old one. There is no update or delete path.
  * `confidence` means one thing: the probability that the stance turns out right over the horizon (bullish: the stock beats
    the Nifty 50; bearish: it lags it; market gates: see GATES). So it is 0.50..0.99 for any non-neutral call.
  * A non-neutral call needs a thesis, an invalidation, at least one cited signal, and an agent and model name.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone

from ..core.envelope import DrishtiError, ErrorKind, Result

# gate -> (scope, {action: stance}). stance +1 = expects to beat the benchmark, -1 = expects to lag it, 0 = no call.
GATES: dict[str, tuple[str, dict[str, int]]] = {
    "entry": ("stock", {"ENTER": 1, "WAIT": 0, "AVOID": -1}),
    "exit": ("stock", {"HOLD": 1, "TRIM": -1, "EXIT": -1}),
    "screen": ("stock", {"SHORTLIST": 1, "SKIP": 0}),
    "macro": ("market", {"RISK_ON": 1, "NEUTRAL": 0, "RISK_OFF": -1}),
}
MARKET_KEY = "MARKET"
DDL = """
CREATE TABLE IF NOT EXISTS decision (
    id TEXT PRIMARY KEY, created_at TEXT NOT NULL, as_of TEXT NOT NULL, gate TEXT NOT NULL, scope TEXT NOT NULL,
    subject TEXT NOT NULL, isin TEXT, symbol TEXT, action TEXT NOT NULL, stance INTEGER NOT NULL, horizon_days INTEGER NOT NULL,
    confidence REAL, thesis TEXT NOT NULL, invalidation TEXT, evidence TEXT NOT NULL, agent TEXT NOT NULL, model TEXT NOT NULL,
    bundle_sha TEXT, supersedes TEXT, notes TEXT);
CREATE INDEX IF NOT EXISTS ix_decision_asof ON decision (as_of, gate);
CREATE INDEX IF NOT EXISTS ix_decision_agent ON decision (agent, model, gate);
CREATE INDEX IF NOT EXISTS ix_decision_case ON decision (as_of, gate, subject, horizon_days, agent, model)
"""
MAX_HORIZON = 500


def ensure(store) -> None:
    store.ensure_table(DDL)


def _fail(msg: str, kind: ErrorKind = ErrorKind.PARSE_FAIL) -> Result:
    return Result(error=DrishtiError(kind, msg))


def stance_of(gate: str, action: str) -> int:
    return GATES[gate][1][action]


def _cite(store, name: str, isin: str | None, symbol: str | None, on: date) -> dict:
    """Recompute one cited signal as of `on`; the record keeps what the data said, not what the author claimed."""
    from .. import signals
    if name in signals.MARKET or name.split(":")[0] in signals.MARKET:
        r = signals.compute(store, name, on)
    elif name in signals.SINGLE_NAME:
        if not isin:
            raise DrishtiError(ErrorKind.PARSE_FAIL, f"cited signal {name!r} is a single-name signal; the decision needs a stock")
        kw = {"isin": isin} if name in ("pledge", "price_context") else {"isin": isin, "symbol": symbol}
        r = signals.compute(store, name, on, **kw)
    else:
        raise DrishtiError(ErrorKind.NOT_FOUND, f"cited signal {name!r} does not exist")
    if not r.ok:
        raise DrishtiError(ErrorKind.NOT_FOUND, f"cited signal {name!r} has no reading as of {on}: {r.error.detail[:140]}")
    d = r.data
    return {"name": name, "as_of": d["as_of"], "label": d["label"], "score": d["score"], "params_version": d["params_version"],
            "missing": d["missing"][:5]}


def record(store, *, gate: str, as_of: str, action: str, horizon_days: int, thesis: str, agent: str, model: str,
           signals_cited: list[str], isin: str | None = None, symbol: str | None = None, confidence: float | None = None,
           invalidation: str | None = None, bundle_sha: str | None = None, supersedes: str | None = None,
           notes: str | None = None) -> Result:
    """Validate and append one decision. Returns the stored row (with its id) or a typed error."""
    ensure(store)
    gate, action = (gate or "").strip().lower(), (action or "").strip().upper()
    if gate not in GATES:
        return _fail(f"unknown gate {gate!r}; one of {sorted(GATES)}")
    scope, actions = GATES[gate]
    if action not in actions:
        return _fail(f"action {action!r} is not valid for /{gate}; one of {sorted(actions)}")
    try:
        on = date.fromisoformat(as_of)
    except (TypeError, ValueError):
        return _fail(f"as_of must be YYYY-MM-DD, got {as_of!r}")
    if on > datetime.now(timezone.utc).date():
        return _fail(f"as_of {as_of} is in the future")
    if not isinstance(horizon_days, int) or isinstance(horizon_days, bool) or not 1 <= horizon_days <= MAX_HORIZON:
        return _fail(f"horizon_days must be a whole number of trading sessions, 1..{MAX_HORIZON}")
    if not (agent or "").strip() or not (model or "").strip():
        return _fail("agent and model are both required (the log exists to compare authors)")
    thesis = (thesis or "").strip()
    stance = actions[action]
    if scope == "stock":
        if not isin:
            return _fail(f"/{gate} is about one stock: pass its ISIN")
        if not store.get_security(isin):
            return _fail(f"{isin} is not in the security master; run `drishti run security_master`", ErrorKind.NOT_FOUND)
        subject = isin
    else:
        isin, symbol, subject = None, None, MARKET_KEY
    if stance != 0:
        if confidence is None or isinstance(confidence, bool) or not 0.5 <= float(confidence) <= 0.99:
            return _fail("confidence must be 0.50..0.99 for a call: the probability that it turns out right over the horizon")
        if len((invalidation or "").strip()) < 10:
            return _fail("a call needs an invalidation: what, specifically, would prove it wrong (10+ characters)")
        if len(thesis) < 20:
            return _fail("a call needs a thesis of at least 20 characters")
        if not signals_cited:
            return _fail("a call must cite at least one stored signal")
    elif confidence is not None and not 0.0 <= float(confidence) <= 1.0:
        return _fail("confidence must be between 0 and 1")
    try:
        evidence = [_cite(store, n, isin, symbol, on) for n in dict.fromkeys(signals_cited or [])]
    except DrishtiError as e:
        return Result(error=e)
    dup = store.query("SELECT id FROM decision WHERE as_of=? AND gate=? AND subject=? AND horizon_days=? AND agent=? AND model=? "
                      "AND id NOT IN (SELECT supersedes FROM decision WHERE supersedes IS NOT NULL)",
                      (as_of, gate, subject, horizon_days, agent.strip(), model.strip()))
    if dup and supersedes != dup[0]["id"]:
        return _fail(f"{agent}/{model} already decided /{gate} on {subject} for {as_of} ({dup[0]['id']}); the log is append-only: "
                     f"pass supersedes={dup[0]['id']} to replace it with a corrected call")
    if supersedes and not store.query("SELECT 1 AS x FROM decision WHERE id=?", (supersedes,)):
        return _fail(f"supersedes {supersedes!r}: no such decision", ErrorKind.NOT_FOUND)
    created = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    did = "dec_" + hashlib.sha1(f"{as_of}|{gate}|{subject}|{horizon_days}|{agent}|{model}|{created}|{thesis}".encode()).hexdigest()[:10]
    row = {"id": did, "created_at": created, "as_of": as_of, "gate": gate, "scope": scope, "subject": subject, "isin": isin, "symbol": symbol,
           "action": action, "stance": stance, "horizon_days": horizon_days, "confidence": None if confidence is None else round(float(confidence), 4),
           "thesis": thesis, "invalidation": (invalidation or "").strip() or None, "evidence": json.dumps(evidence), "agent": agent.strip(),
           "model": model.strip(), "bundle_sha": bundle_sha, "supersedes": supersedes, "notes": notes}
    store.upsert("decision", [row], key=["id"])
    return Result(data={**row, "evidence": evidence}, as_of=as_of)


def _unpack(r: dict) -> dict:
    return {**r, "evidence": json.loads(r["evidence"])}


def current(store, *, gate: str | None = None, agent: str | None = None, model: str | None = None, isin: str | None = None,
            since: str | None = None, until: str | None = None, limit: int | None = None) -> list[dict]:
    """Decisions that have not been superseded, newest as_of first."""
    ensure(store)
    where, args = ["id NOT IN (SELECT supersedes FROM decision WHERE supersedes IS NOT NULL)"], []
    for col, v, op in (("gate", gate, "="), ("agent", agent, "="), ("model", model, "="), ("isin", isin, "="), ("as_of", since, ">="), ("as_of", until, "<=")):
        if v:
            where.append(f"{col} {op} ?")
            args.append(v)
    sql = f"SELECT * FROM decision WHERE {' AND '.join(where)} ORDER BY as_of DESC, created_at DESC" + (f" LIMIT {int(limit)}" if limit else "")
    return [_unpack(r) for r in store.query(sql, args)]

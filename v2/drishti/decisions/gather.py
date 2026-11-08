"""Evidence bundles: everything a decision-maker (an agent or the rule baseline) is allowed to see for one case, as one JSON
document, computed from stored data up to `as_of`. Code gathers; the author decides.

The bundle carries its own hash. A decision records that hash, so a grader can prove two authors were shown identical
evidence. Missing inputs are listed under `unknown` with the reason, never filled in. `blind=True` strips the stock's name,
symbol, ISIN and insider names, so a model cannot lean on what it remembers about the company (it does not hide the date).
"""
from __future__ import annotations

import hashlib
import json
from datetime import date

from .. import resolve as resolver
from .. import signals
from ..core.envelope import DrishtiError, ErrorKind, Result
from . import log

MARKET_SIGNALS = ("macro_regime", "participant_oi", "cycle_phase", "midcap_ratio", "fii_dii", "index_valuation", "sector_rotation", "seasonality")
STOCK_SIGNALS = ("price_context", "insider_cluster", "pledge")
_PRIVATE = {"isin", "symbol", "company", "name", "person", "trades", "nse_symbol"}

HOW_TO_ANSWER = {
    "entry": "ENTER = buy now; WAIT = no call; AVOID = expect the stock to lag the Nifty 50.",
    "exit": "HOLD = keep; TRIM = sell part; EXIT = sell all (both expect the stock to lag the Nifty 50).",
    "screen": "SHORTLIST = worth a deeper look (expects to beat the Nifty 50); SKIP = no call.",
    "macro": "RISK_ON / RISK_OFF are judged on the Nifty 50 against cash over the horizon; NEUTRAL = no call.",
    "all": ("Answer with one decision: action, horizon_days (trading sessions; use the one given in answer_with if there is one), confidence 0.50..0.99 (the probability that the call turns out "
            "right over the horizon; omit for a no-call), a thesis (20+ chars), an invalidation (what would prove it wrong), and the signal "
            "names you leaned on. Use only this bundle. Anything marked unknown is unknown, not neutral."),
}


def _compact(sig: dict, full: bool) -> dict:
    out = {"as_of": sig["as_of"], "label": sig["label"], "score": sig["score"], "missing": sig["missing"], "provisional": sig["provisional"],
           "params_version": sig["params_version"], "inputs": [{"input": c["input"], "value": c["value"], "text": c["text"]} for c in sig["components"]]}
    if full:
        out["details"] = sig["details"]
    return out


def _scrub(v):
    if isinstance(v, dict):
        return {k: _scrub(x) for k, x in v.items() if k not in _PRIVATE}
    if isinstance(v, list):
        return [_scrub(x) for x in v]
    return v


def gather(store, gate: str, as_of: str, *, isin: str | None = None, blind: bool = False, profile=None, horizon_days: int | None = None) -> Result:
    gate = (gate or "").lower()
    if gate not in log.GATES:
        return Result(error=DrishtiError(ErrorKind.PARSE_FAIL, f"unknown gate {gate!r}; one of {sorted(log.GATES)}"))
    try:
        on = date.fromisoformat(as_of)
    except (TypeError, ValueError):
        return Result(error=DrishtiError(ErrorKind.PARSE_FAIL, f"as_of must be YYYY-MM-DD, got {as_of!r}"))
    scope = log.GATES[gate][0]
    ev: dict = {"gate": gate, "as_of": as_of, "market": {}, "unknown": []}
    symbol = None
    if scope == "stock":
        if not isin:
            return Result(error=DrishtiError(ErrorKind.PARSE_FAIL, f"/{gate} is about one stock: pass its ISIN"))
        sec = store.get_security(isin)
        if not sec:
            return Result(error=DrishtiError(ErrorKind.NOT_FOUND, f"{isin} is not in the security master"))
        symbol = sec.get("nse_symbol")
        ev["stock"] = {"isin": isin, "symbol": symbol, "name": sec.get("name"), "signals": {}}
        for n in STOCK_SIGNALS:
            kw = {"isin": isin} if n in ("pledge", "price_context") else {"isin": isin, "symbol": symbol}
            r = signals.compute(store, n, on, **kw)
            if r.ok:
                ev["stock"]["signals"][n] = _compact(r.data, full=True)
            else:
                ev["unknown"].append({"signal": n, "why": r.error.detail[:200]})
    for n in MARKET_SIGNALS:
        r = signals.compute(store, n, on)
        if r.ok:
            ev["market"][n] = _compact(r.data, full=n in ("cycle_phase", "macro_regime"))
        else:
            ev["unknown"].append({"signal": n, "why": r.error.detail[:200]})
    if profile is not None:        # the only place a profile is read: limits shape sizing, never the signals themselves
        ev["investor"] = {"target_xirr_pct": profile.goals.target_xirr_pct, "max_position_pct": profile.risk.max_position_pct,
                          "max_sector_pct": profile.risk.max_sector_pct, "on_watchlist": bool(isin and profile.find(isin=isin))}
    if blind and "stock" in ev:
        names = [x for x in (isin, symbol, ev["stock"].get("name")) if x]
        for u in ev["unknown"]:
            for n in names:
                u["why"] = u["why"].replace(n, "the stock")
        ev["stock"] = {"signals": _scrub(ev["stock"]["signals"]), "identity": "hidden"}
        ev["market"] = _scrub(ev["market"])
    sha = hashlib.sha1(json.dumps(ev, sort_keys=True, default=str).encode()).hexdigest()[:16]
    bundle = {"evidence": ev, "bundle_sha": sha, "blind": blind, "how_to_answer": {"gate": HOW_TO_ANSWER[gate], "all": HOW_TO_ANSWER["all"]},
              "answer_with": {"gate": gate, "as_of": as_of, **({"horizon_days": horizon_days} if horizon_days else {}), **({} if blind else {"isin": isin}), "allowed_actions": sorted(log.GATES[gate][1])}}
    return Result(data=bundle, as_of=as_of)

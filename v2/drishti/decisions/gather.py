"""Evidence bundles: everything a decision-maker (an agent or the rule baseline) is allowed to see for one case, as one JSON
document, computed from stored data up to `as_of`. Code gathers; the author decides.

The bundle carries its own hash. A decision records that hash, so a grader can prove two authors were shown identical
evidence. Missing inputs are listed under `unknown` with the reason, never filled in. `blind=True` strips the stock's name,
symbol, ISIN and insider names, so a model cannot lean on what it remembers about the company (it does not hide the date).
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date

from .. import resolve as resolver
from .. import signals
from ..core.envelope import DrishtiError, ErrorKind, Result
from . import log

MARKET_SIGNALS = ("macro_regime", "participant_oi", "cycle_phase", "midcap_ratio", "fii_dii", "index_valuation", "sector_rotation", "seasonality")
STOCK_SIGNALS = ("price_context", "insider_cluster", "pledge", "deal_flow", "delivery_trend")
_PRIVATE = {"isin", "symbol", "company", "name", "person", "trades", "nse_symbol", "client"}

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


# --- masked mode: hide WHEN the case is, not just WHICH stock ---------------------------------------------------------
# A model trained on market history can recognise a date (or a Nifty / VIX / rupee level that pins one) and recall what
# happened next. Masking keeps only what is relative to the case date: the stock's own prices rescaled to close = 100, returns,
# distances, market signal labels and scores. Market inputs (absolute levels) and the seasonality signal (it names the month)
# and history-length notes are dropped, unknown reasons are made generic; every date becomes an offset in calendar days ("D-35"); month names and years in text are blanked.
_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})(?:[T ][0-9:.+]+)?\b")
_DMY = re.compile(r"\b\d{1,2}[-/ ](?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*[-/ ]\d{2,4}\b", re.I)
_MONTH = re.compile(r"\b(?:january|february|march|april|may|june|july|august|september|october|november|december|"
                    r"jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec)\b\.?", re.I)
_YEAR = re.compile(r"\b(?:19[89]\d|20[0-4]\d)\b")
_FY = re.compile(r"\b(?:FY|Q[1-4]\s?FY)\s?'?\d{2,4}\b", re.I)
MASK_DROP_MARKET = ("seasonality", "sector_rotation")   # the month; which sectors led (recognisable, and useless with the stock's sector hidden)
_PRICE_KEYS = {"close", "atr14", "high_52w", "low_52w"}


def _offset(m: re.Match, on: date) -> str:
    try:
        d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return "D?"
    k = (d - on).days
    return "D0" if k == 0 else f"D{k:+d}"


def _mask_text(s: str, on: date) -> str:
    s = _ISO.sub(lambda m: _offset(m, on), s)
    s = _DMY.sub("[date]", s)
    s = _FY.sub("[period]", s)
    s = _YEAR.sub("[year]", s)
    return _MONTH.sub("[month]", s)


def _mask_any(v, on: date):
    if isinstance(v, str):
        return _mask_text(v, on)
    if isinstance(v, dict):
        return {_mask_text(k, on) if isinstance(k, str) else k: _mask_any(x, on) for k, x in v.items()}
    if isinstance(v, list):
        return [_mask_any(x, on) for x in v]
    return v


def _rescale_price(sig: dict) -> dict:
    """price_context with prices expressed against the latest close = 100 (returns, % distances and volume are kept)."""
    d = dict(sig.get("details") or {})
    c = d.get("close")
    if not c:
        return sig
    f = 100.0 / c
    sc = lambda x: None if x is None else round(x * f, 3)   # noqa: E731
    for k in _PRICE_KEYS & d.keys():
        d[k] = sc(d[k])
    if isinstance(d.get("dma"), dict):
        d["dma"] = {k: sc(x) for k, x in d["dma"].items()}
    d["adjustments"] = [{"factor": a.get("factor"), "kind": a.get("kind")} for a in d.get("adjustments") or []]
    out = []
    for i in sig.get("inputs") or []:
        name, val = i["input"], i["value"]
        if name == "close":
            val = 100.0
        elif name in ("52-week high / low", "20 / 50 / 200-day average") and isinstance(val, list):
            val = [sc(x) for x in val]
        elif name == "ATR(14)":
            val = sc(val)
        elif name == "adjusted for":
            val = None
        out.append({**i, "value": val})
    return {**sig, "details": d, "inputs": out, "note": "prices rescaled: latest close = 100"}


def mask(bundle: dict) -> dict:
    """A masked copy of a BLIND stock bundle (see the section comment). Re-hashed, so it is a different bundle from the dated one."""
    if not bundle.get("blind"):
        raise ValueError("mask() needs a blind bundle: the stock identity must already be hidden")
    ev = json.loads(json.dumps(bundle["evidence"], default=str))
    on = date.fromisoformat(ev["as_of"])
    stock = ev.get("stock") or {}
    sigs = stock.get("signals") or {}
    if "price_context" in sigs:
        sigs["price_context"] = _rescale_price(sigs["price_context"])
    for sig in sigs.values():
        sig["params_version"] = "masked"      # a settings version can embed the date the settings were made (seen: "D+3415.3+..."): an era clue
    market = {}
    for n, sig in (ev.get("market") or {}).items():
        if n in MASK_DROP_MARKET:
            continue
        market[n] = {k: sig.get(k) for k in ("label", "score", "provisional")}   # "missing" notes say how much history exists: an era clue
    ev["market"] = market
    ev["unknown"] = [{"signal": u.get("signal"), "why": "not available for this case"} for u in ev.get("unknown") or []
                     if u.get("signal") not in MASK_DROP_MARKET]
    ev.pop("investor", None)
    ev = _mask_any(ev, on)
    ev["as_of"] = "D0"
    ev["masked"] = "dates are offsets in calendar days from the decision day D0; absolute market levels are hidden"
    sha = hashlib.sha1(json.dumps(ev, sort_keys=True, default=str).encode()).hexdigest()[:16]
    aw = {**bundle["answer_with"], "as_of": "D0"}
    return {**bundle, "evidence": ev, "bundle_sha": sha, "masked": True, "answer_with": aw}

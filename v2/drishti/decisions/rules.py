"""rules-v1: a plain, fixed-threshold baseline for each gate, so an agent has something honest to beat.

It reads the same stored signals an agent sees (never anything newer than `as_of`), adds up points with the numbers in
rules.toml, and writes its decision to the same log under agent "rules". It has no profile and no memory. Numbers are frozen
before scoring: edit them only by raising `version`.
"""
from __future__ import annotations

import hashlib
import tomllib
from functools import lru_cache
from pathlib import Path

from .. import signals
from . import log

RULES_PATH = Path(__file__).with_name("rules.toml")
AGENT = "rules"


@lru_cache(maxsize=1)
def load() -> tuple[dict, str]:
    raw = RULES_PATH.read_bytes()
    p = tomllib.loads(raw.decode("utf-8"))
    return p, f"rules-v{p['version']}+{hashlib.sha1(raw).hexdigest()[:8]}"


def _ok(store, name, on, **kw):
    r = signals.compute(store, name, on, **kw)
    return r.data if r.ok else None


def _comp(sig: dict, label: str):
    return next((c["value"] for c in sig["components"] if c["input"] == label), None)


def stock_points(store, isin: str, symbol: str | None, on) -> dict:
    """{'points': int, 'parts': [...], 'unknown': [...], 'cited': [...], 'pledge_veto': bool, 'close': float}, or None without prices."""
    P, _ = load()
    sp = P["stock_points"]
    pc = _ok(store, "price_context", on, isin=isin)
    if pc is None or pc["details"]["sessions"] < P["data"]["min_sessions"]:
        return None
    parts, unknown, cited = [], [], ["price_context"]
    pts = 0

    def add(n, why):
        nonlocal pts
        pts += n
        parts.append({"points": n, "why": why})

    det = pc["details"]
    close, d50, d200 = det["close"], det["dma"].get("50"), det["dma"].get("200")
    rets = _comp(pc, "return 1m / 3m / 6m / 1y %") or [None] * 4
    if d50 and d200:
        if close > d50 > d200:
            add(sp["trend_up"], "close above the 50-day and 200-day averages, 50 above 200")
        elif close < d50 < d200:
            add(sp["trend_down"], "close below the 50-day and 200-day averages, 50 below 200")
        else:
            parts.append({"points": 0, "why": "mixed trend"})
    elif d50 and rets[1] is not None:
        if close > d50 and rets[1] > 0:
            add(sp["trend_weak_up"], "fewer than 200 sessions stored: above the 50-day average and up over 3 months")
        elif close < d50 and rets[1] < 0:
            add(sp["trend_weak_down"], "fewer than 200 sessions stored: below the 50-day average and down over 3 months")
    else:
        unknown.append("trend (not enough stored sessions)")
    rs = _comp(pc, "strength vs Nifty, ~3 months")
    if rs is None:
        unknown.append("strength vs Nifty")
    else:
        add(sp["rel_strength_pos"] if rs > 0 else sp["rel_strength_neg"], f"{rs:+.1f}% against the Nifty over ~3 months")
    if rets[0] is not None and rets[0] > sp["chasing_above_pct"]:
        add(sp["chasing"], f"already up {rets[0]:.0f}% in a month")
    ins = _ok(store, "insider_cluster", on, isin=isin, symbol=symbol)
    if ins is None:
        unknown.append("insider_cluster")
    else:
        cited.append("insider_cluster")
        fl = " ".join(ins["details"]["flags"])
        sell, buy = "selling cluster" in fl, "size buying" in fl
        if buy and not sell:
            add(sp["insider_buy"], "insider buying")
        elif sell and not buy:
            add(sp["insider_sell"], "insider selling cluster")
    pl = _ok(store, "pledge", on, isin=isin)
    veto = False
    if pl is None:
        unknown.append("pledge")
    else:
        cited.append("pledge")
        veto = any("strong caution" in f for f in pl["details"]["flags"])
        if veto:
            parts.append({"points": 0, "why": "promoter pledge above the strong-caution level (veto on buying)"})
    mr = _ok(store, "macro_regime", on)
    if mr is None or mr["score"] is None:
        unknown.append("macro_regime")
    else:
        cited.append("macro_regime")
        if mr["score"] >= sp["macro_bull_at"]:
            add(sp["macro_bull"], f"macro tide {mr['score']:+g}")
        elif mr["score"] <= sp["macro_bear_at"]:
            add(sp["macro_bear"], f"macro tide {mr['score']:+g}")
    return {"points": pts, "parts": parts, "unknown": unknown, "cited": cited, "pledge_veto": veto, "close": close}


def _conf(points: int) -> float:
    c = load()[0]["confidence"]
    return round(min(c["cap"], c["base"] + c["per_point"] * abs(points)), 4)


def _why(sc: dict) -> str:
    return "; ".join(f"{x['points']:+d} {x['why']}" if x["points"] else x["why"] for x in sc["parts"]) or "no reading moved the score"


def decide_stock(store, gate: str, isin: str, symbol: str | None, on) -> dict | None:
    P, _ = load()
    sc = stock_points(store, isin, symbol, on)
    if sc is None:
        return None
    pts = sc["points"]
    if gate == "entry":
        e = P["entry"]
        action = "AVOID" if sc["pledge_veto"] or pts <= e["avoid_at"] else "ENTER" if pts >= e["enter_at"] else "WAIT"
        hz, conf = e["horizon_days"], _conf(pts)
        inval = {"ENTER": "closes below the 50-day average, or an insider selling cluster / pledge flag appears",
                 "AVOID": "closes above the 50-day average with beating-the-Nifty strength and no pledge flag",
                 "WAIT": None}[action]
    elif gate == "exit":
        e = P["exit"]
        action = "EXIT" if pts <= e["exit_at"] else "TRIM" if pts <= e["trim_at"] else "HOLD"
        hz, conf = e["horizon_days"], _conf(pts)
        inval = {"EXIT": "closes back above the 50-day average and beats the Nifty over 3 months",
                 "TRIM": "closes back above the 50-day average and beats the Nifty over 3 months",
                 "HOLD": "closes below the 50-day average or an insider selling cluster / pledge flag appears"}[action]
    else:
        raise KeyError(gate)
    stance = log.stance_of(gate, action)
    return {"gate": gate, "action": action, "horizon_days": hz, "confidence": conf if stance else None,
            "thesis": f"Rule score {pts:+d}: {_why(sc)}." + (f" Unknown: {', '.join(sc['unknown'])}." if sc["unknown"] else ""),
            "invalidation": inval, "signals_cited": sc["cited"], "points": pts}


def decide_macro(store, on) -> dict | None:
    P, _ = load()
    m = P["macro"]
    votes, why, cited, unknown = 0, [], [], []

    def vote(n, text, name):
        nonlocal votes
        votes += n
        cited.append(name)
        why.append(f"{n:+d} {text}")

    readable = 0
    mr = _ok(store, "macro_regime", on)
    if mr and mr["score"] is not None:
        readable += 1
        if mr["score"] >= 2:
            vote(1, f"macro tide {mr['score']:+g}", "macro_regime")
        elif mr["score"] <= -2:
            vote(-1, f"macro tide {mr['score']:+g}", "macro_regime")
        else:
            cited.append("macro_regime")
    else:
        unknown.append("macro_regime")
    po = _ok(store, "participant_oi", on)
    if po and po["score"] is not None:
        readable += 1
        if po["score"] >= m["participant_strong"]:
            vote(1, f"derivatives smart money {po['score']:+g}", "participant_oi")
        elif po["score"] <= -m["participant_strong"]:
            vote(-1, f"derivatives smart money {po['score']:+g}", "participant_oi")
        else:
            cited.append("participant_oi")
    else:
        unknown.append("participant_oi")
    cy = _ok(store, "cycle_phase", on)
    probs = cy["details"]["probabilities"] if cy else None
    if probs and cy["label"] != "no clear phase":
        readable += 1
        best = max(probs, key=probs.get)
        if best in ("Expansion", "Recovery"):
            vote(1, f"cycle phase leans {best}", "cycle_phase")
        elif best in ("Late", "Contraction"):
            vote(-1, f"cycle phase leans {best}", "cycle_phase")
    else:
        unknown.append("cycle_phase")
    mc = _ok(store, "midcap_ratio", on)
    if mc:
        readable += 1
        if mc["label"] == "midcaps outperforming":
            vote(1, "midcaps outperforming", "midcap_ratio")
        elif mc["label"] == "midcaps lagging":
            vote(-1, "midcaps lagging", "midcap_ratio")
        else:
            cited.append("midcap_ratio")
    else:
        unknown.append("midcap_ratio")
    if readable < m["min_inputs"]:
        return None
    action = "RISK_ON" if votes >= m["on_at"] else "RISK_OFF" if votes <= m["off_at"] else "NEUTRAL"
    conf = _conf(votes) if action != "NEUTRAL" else None
    return {"gate": "macro", "action": action, "horizon_days": m["horizon_days"], "confidence": conf,
            "thesis": f"Net market votes {votes:+d}: " + ("; ".join(why) or "no reading was decisive") + "."
                      + (f" Unknown: {', '.join(unknown)}." if unknown else ""),
            "invalidation": {"RISK_ON": "the Nifty closes below its 50-day average, or the macro tide turns to -2 or worse",
                             "RISK_OFF": "the Nifty closes above its 50-day average with the macro tide at +2 or better", "NEUTRAL": None}[action],
            "signals_cited": cited, "points": votes}


def run(store, gate: str, as_of: str, *, isin: str | None = None, symbol: str | None = None):
    """Decide one case and append it to the log. Returns the log's Result; a case with too little data is a typed NOT_FOUND
    ('skipped'), never a made-up neutral."""
    from datetime import date
    from ..core.envelope import DrishtiError, ErrorKind, Result
    _, ver = load()
    on = date.fromisoformat(as_of)
    d = decide_macro(store, on) if gate == "macro" else decide_stock(store, gate, isin, symbol, on)
    if d is None:
        return Result(error=DrishtiError(ErrorKind.NOT_FOUND, f"{ver}: too little stored data to decide /{gate} {isin or 'MARKET'} on {as_of}"))
    return log.record(store, gate=gate, as_of=as_of, action=d["action"], horizon_days=d["horizon_days"], thesis=d["thesis"], agent=AGENT,
                      model=ver, signals_cited=d["signals_cited"], isin=isin, symbol=symbol, confidence=d["confidence"],
                      invalidation=d["invalidation"], notes=f"points={d['points']}")

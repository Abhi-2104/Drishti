"""rules-v2 (entry gate): v1's plain points idea, rebuilt on what the 2017-21 signal study supported.

One pure function, `score`, reads the stored readings of a stock on a date (the same dicts the signal study keeps per case) and
returns points and an action. Because it takes readings and not the store, it can judge the old history offline and, later,
the weekly candidate screen with the same numbers. Numbers live in rules_v2.toml and are frozen: change them only by raising
`version`. It has no profile and no memory.
"""
from __future__ import annotations

import hashlib
import tomllib
from functools import lru_cache
from pathlib import Path

RULES_PATH = Path(__file__).with_name("rules_v2.toml")
HOLDOUT_FROM = "2022-01-01"      # the 2022-26 half was not used to build v2; it is opened once (see holdout.py)


@lru_cache(maxsize=1)
def load() -> tuple[dict, str]:
    raw = RULES_PATH.read_bytes()
    p = tomllib.loads(raw.decode("utf-8"))
    return p, f"rules-v{p['version']}+{hashlib.sha1(raw).hexdigest()[:8]}"


def score(price: dict | None, deal: dict | None = None, *, insider: str | None = None, pledge_veto: bool = False) -> dict | None:
    """Points and an entry action for one stock on one date, or None when there is too little stored data to decide.

    price: the study's price reading (close, dma{'50','200'}, ret_3m, rs_3m, high_52w, sessions).
    deal: the deal_flow reading (its `label`). A missing reading adds nothing and is listed under `unknown`.
    insider: 'buy' / 'sell' / None, and pledge_veto: live use only; history has neither, so the hold-out leaves them out.
    """
    P, _ = load()
    sp, en = P["stock_points"], P["entry"]
    if not price or (price.get("sessions") or 0) < P["data"]["min_sessions"]:
        return None
    pts, parts, unknown = 0, [], []

    def add(n, why):
        nonlocal pts
        pts += n
        parts.append({"points": n, "why": why})

    close = price.get("close")
    dma = price.get("dma") or {}
    d50, d200, r3 = dma.get("50"), dma.get("200"), price.get("ret_3m")
    if close is None:
        return None
    if d50 and d200:
        if close > d50 > d200:
            add(sp["trend_up"], "close above the 50-day and 200-day averages, 50 above 200")
        elif close < d50 < d200:
            add(sp["trend_down"], "close below the 50-day and 200-day averages, 50 below 200")
        else:
            parts.append({"points": 0, "why": "mixed trend"})
    elif d50 and r3 is not None:
        if close > d50 and r3 > 0:
            add(sp["trend_weak_up"], "fewer than 200 sessions stored: above the 50-day average and up over 3 months")
        elif close < d50 and r3 < 0:
            add(sp["trend_weak_down"], "fewer than 200 sessions stored: below the 50-day average and down over 3 months")
    else:
        unknown.append("trend")
    rs = price.get("rs_3m")
    if rs is None:
        unknown.append("strength vs Nifty")
    else:
        add(sp["rel_strength_pos"] if rs > 0 else sp["rel_strength_neg"], f"{rs:+.1f}% against the Nifty over ~3 months")
    hi = price.get("high_52w")
    if not hi:
        unknown.append("52-week high")
    else:
        gap = (close / hi - 1) * 100
        if gap >= -sp["near_high_pct"]:
            add(sp["near_high"], f"within {sp['near_high_pct']:g}% of the 52-week high ({gap:+.1f}%)")
    if deal is None:
        unknown.append("deal_flow")
    elif deal.get("label") == "net deal selling":
        add(sp["deal_selling"], "net bulk/block deal selling")
    if insider == "buy":
        add(sp["insider_buy"], "insider buying")
    elif insider == "sell":
        add(sp["insider_sell"], "insider selling cluster")
    action = "AVOID" if pledge_veto or pts <= en["avoid_at"] else "ENTER" if pts >= en["enter_at"] else "WAIT"
    return {"action": action, "points": pts, "parts": parts, "unknown": unknown, "pledge_veto": pledge_veto}

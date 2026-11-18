"""The tradeable universe: names liquid enough to buy and sell without moving the price, by recent NSE turnover.

About 3,400 stocks have prices; most never trade enough to act on. A discovery list (screen, brief) that includes them is noise at
best and a trap at worst (a pledge flag on a stock with Rs 5 lakh a day of turnover is not an opportunity). `liquid` ranks by average
daily turnover over `window` sessions and keeps the top `top`. Watchlist and holdings are never filtered: you own what you own.
"""
from __future__ import annotations

from datetime import date, timedelta

TOP = 800         # roughly Nifty 500 plus the most active smaller names
WINDOW_DAYS = 45  # calendar days of sessions looked at


def liquid(store, on=None, *, top: int = TOP, window_days: int = WINDOW_DAYS) -> set[str] | None:
    """ISINs of the `top` most liquid NSE equity names as of `on`. None when there are no prices to rank (then nothing is filtered)."""
    d = on if isinstance(on, date) else date.fromisoformat(on) if on else date.today()
    if not store.query("SELECT 1 AS x FROM sqlite_master WHERE type='table' AND name='price_eod'"):
        return None
    rows = store.query("SELECT isin FROM price_eod WHERE exchange='NSE' AND series='EQ' AND isin IS NOT NULL AND trade_date BETWEEN ? AND ? "
                       "GROUP BY isin ORDER BY AVG(turnover) DESC, isin LIMIT ?",
                       ((d - timedelta(days=window_days)).isoformat(), d.isoformat(), int(top)))
    return {r["isin"] for r in rows} or None

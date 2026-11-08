"""L0 calendar signals: India seasonality and the RBI MPC calendar. Pure functions of the date: no data needed."""
from __future__ import annotations

from datetime import date, timedelta

from .base import Component, Signal, ok

# v1:macro.md "SEASONALITY (India calendar)": calendar quarter -> theme
QUARTER_THEMES = {
    1: "pre-budget capex + earnings run (Jan-Mar)",
    2: "pre-monsoon optimism + rural consumption (Apr-Jun)",
    3: "post-budget rebalancing + festive pre-run (Jul-Sep)",
    4: "festive consumer surge + FII tax-selling risk (Oct-Dec)",
}

# RBI MPC schedule, policy-decision day = the last day of each meeting. Hand-checked against RBI press release
# https://www.rbi.org.in/Scripts/BS_PressReleaseDisplay.aspx?prid=62422 (FY2026-27). Add the next year when RBI publishes it.
MPC_DECISIONS = ["2026-04-08", "2026-06-05", "2026-08-05", "2026-10-07", "2026-12-04", "2027-02-05"]
MPC_SOURCE = "https://www.rbi.org.in/Scripts/BS_PressReleaseDisplay.aspx?prid=62422"


def _fy_quarter(q_end: date) -> str:
    """Indian fiscal-quarter name for a quarter ending on q_end: Jun -> Q1, Sep -> Q2, Dec -> Q3, Mar -> Q4."""
    fy = q_end.year + 1 if q_end.month >= 4 else q_end.year
    return f"Q{ {6: 1, 9: 2, 12: 3, 3: 4}[q_end.month] } FY{fy % 100:02d}"


def results_season(d: date) -> dict | None:
    """Inside a results season? SEBI LODR: results within 45 days of quarter end (60 for the March quarter, i.e. annual).
    The season is taken to start on the 10th of the month after quarter end, when filings start arriving in volume."""
    candidates = []
    for y in (d.year - 1, d.year):
        for m, last in ((3, 31), (6, 30), (9, 30), (12, 31)):
            q_end = date(y, m, last)
            start = (q_end + timedelta(days=10)).replace(day=10)
            deadline = q_end + timedelta(days=60 if m == 3 else 45)
            candidates.append((q_end, start, deadline))
    for q_end, start, deadline in candidates:
        if start <= d <= deadline:
            return {"quarter": _fy_quarter(q_end), "quarter_end": q_end.isoformat(), "deadline": deadline.isoformat(),
                    "days_to_deadline": (deadline - d).days}
    return None


def _budget_stage(d: date) -> str:
    """Union Budget is presented on 1 February."""
    if d.month in (10, 11, 12) or d.month == 1:
        return "pre-budget build-up (Oct-Jan)"
    if d.month == 2 and d.day <= 7:
        return "budget week"
    if d.month in (2, 3):
        return "post-budget (Feb-Mar)"
    return "outside the budget window"


def _monsoon_stage(d: date) -> str:
    if d.month in (4, 5):
        return "pre-monsoon (Apr-May)"
    if d.month in (6, 7, 8, 9):
        return "monsoon (Jun-Sep): rainfall progress drives rural and agri names"
    return "post-monsoon"


def next_mpc(d: date) -> dict | None:
    for x in MPC_DECISIONS:
        if date.fromisoformat(x) >= d:
            return {"decision_date": x, "days_away": (date.fromisoformat(x) - d).days}
    return None


def seasonality(store=None, on=None):
    d = on if isinstance(on, date) else date.fromisoformat(on) if on else date.today()
    q = (d.month - 1) // 3 + 1
    rs = results_season(d)
    mpc = next_mpc(d)
    comps = [Component("calendar quarter", f"Q{q}", d.isoformat(), "v1 /macro seasonality", None, QUARTER_THEMES[q]),
             Component("budget cycle", _budget_stage(d), d.isoformat()),
             Component("monsoon", _monsoon_stage(d), d.isoformat()),
             Component("results season", rs["quarter"] if rs else "no", d.isoformat(), "SEBI LODR 45/60-day deadline", None,
                       f"{rs['quarter']} results due by {rs['deadline']}" if rs else "between results seasons")]
    missing = []
    if mpc:
        comps.append(Component("next RBI MPC decision", mpc["decision_date"], d.isoformat(), MPC_SOURCE, None, f"in {mpc['days_away']} days"))
    else:
        missing.append("RBI MPC schedule beyond the stored years; add the next year's dates to calendar.MPC_DECISIONS")
    label = f"Q{q}: {QUARTER_THEMES[q]}" + (f"; {rs['quarter']} results season" if rs else "")
    return ok(Signal("seasonality", d.isoformat(), None, label, comps, missing,
                     "Calendar position only (India). Context for timing, never a signal on its own.", "calendar",
                     details={"quarter": q, "results_season": rs, "next_mpc": mpc}))

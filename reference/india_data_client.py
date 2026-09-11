"""DrishtiDataClient — India (NSE/BSE) implementation of ai-hedge-fund's DataClient protocol.

DRAFT / SKETCH produced by /port-patterns. Not wired into anything yet.

Upstream contract (reference/ai-hedge-fund/hedge_fund/data/protocol.py, MIT):
  - Structural typing: any object with these methods IS a DataClient. No import
    of the upstream Protocol needed here.
  - Empty list / None  => the data genuinely does not exist.
  - Infra failure (auth, rate limit, network, 5xx) => RAISE. Never return empty
    on failure — a backtest cannot tell "no signal" from "fetch broke".
  - get_financial_metrics MUST be point-in-time: return only rows publicly filed
    by `end_date` (filter on filing_date, not fiscal-period-end).

Source split (see MASTER.md section 4):
  prices, company_facts, market_cap  -> Yahoo Finance (.NS / .BO)   [WORKS]
  insider_trades, news/announcements -> NSE public API (same endpoints NSE-MCP
                                        uses: reference/NSE-MCP/src/tools/)      [WORKS, thin]
  financial_metrics                  -> Yahoo (shallow) + Screener.in (deep)    [PARTIAL - see caveats]
  earnings / earnings_history        -> NOT PORTABLE cheaply (no free Indian
                                        consensus EPS estimates => no BEAT/MISS) [RAISES NotImplementedError]

ENSEMBLE, NOT FALLBACK (owner's call, 2026-09-10):
  Where more than one source can answer (prices, financial_metrics, market_cap,
  company_facts), pull from ALL available sources, not just the first that
  works. Reconcile: on a numeric conflict the primary filing / exchange source
  wins (CLAUDE.md directive 3); surface the disagreement rather than hide it (a
  cross-source mismatch is itself a data-quality signal). Only raise when EVERY
  source fails. Single-source data (NSE flows/insider/announcements) has nothing
  to cross-check against and is passed through as-is. This client currently
  implements the single best source per method; the multi-source merge is the
  next build step (PORT_NOTES sec.2a).

Run the shape check:  python reference/india_data_client.py
Run upstream conformance (needs the repo on PYTHONPATH):
  PYTHONPATH=reference/ai-hedge-fund python -c \
    "from hedge_fund.data.protocol import DataClient; \
     from reference.india_data_client import DrishtiDataClient; \
     print(isinstance(DrishtiDataClient(), DataClient))"
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class DataClientError(RuntimeError):
    """Infra failure (auth / rate-limit / network / 5xx). Mirrors upstream
    FDClientError: a backtest must crash on this, not treat it as no-data."""


# ---------------------------------------------------------------------------
# Row models
# ---------------------------------------------------------------------------
# The upstream models are pydantic (hedge_fund/data/models.py). The personas
# only touch a subset (see hedge_fund/features/snapshot.py -> PeriodFundamentals).
# Plain dataclasses here keep the draft dependency-free; swap for
# `from hedge_fund.data.models import Price, FinancialMetrics, ...` when wiring.

@dataclass
class Price:
    open: float
    close: float
    high: float
    low: float
    volume: int
    time: str  # ISO date, "YYYY-MM-DD"


@dataclass
class FinancialMetrics:
    ticker: str
    report_period: str
    period: str
    currency: str | None = "INR"
    filing_date: str | None = None          # point-in-time key; None = unknown (see caveat)
    market_cap: float | None = None
    price_to_earnings_ratio: float | None = None
    price_to_book_ratio: float | None = None
    return_on_equity: float | None = None
    gross_margin: float | None = None
    operating_margin: float | None = None
    net_margin: float | None = None
    debt_to_equity: float | None = None
    current_ratio: float | None = None
    revenue_growth: float | None = None
    earnings_per_share: float | None = None
    book_value_per_share: float | None = None
    free_cash_flow_per_share: float | None = None


@dataclass
class InsiderTrade:
    ticker: str
    name: str
    filing_date: str                        # NSE PIT intimation date
    is_board_director: bool = False
    transaction_date: str | None = None
    transaction_type: str | None = None     # "buy" | "sell" (mapped from acqMode/sign)
    transaction_shares: float | None = None
    shares_owned_before_transaction: float | None = None
    shares_owned_after_transaction: float | None = None
    title: str | None = None                # personCategory (Promoter / KMP / ...)


@dataclass
class CompanyNews:
    ticker: str
    title: str
    source: str
    date: str | None = None
    url: str | None = None


@dataclass
class CompanyFacts:
    ticker: str
    is_active: bool = True
    name: str | None = None
    sector: str | None = None
    industry: str | None = None
    exchange: str | None = None
    market_cap: float | None = None


# ---------------------------------------------------------------------------
# NSE public-API session (mirrors reference/NSE-MCP/src/nse/session.ts)
# ---------------------------------------------------------------------------

_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
       "Chrome/120.0.0.0 Safari/537.36")


class _NSESession:
    """Cookie-bootstrapped requests.Session for nseindia.com.

    NSE's JSON API 401s without browser cookies. First hit the home page +
    live-market page to seed cookies, cache for ~7 min, refresh transparently.
    Identical approach to NSE-MCP's session.ts.
    """

    _TTL = _dt.timedelta(minutes=7)

    def __init__(self) -> None:
        self._session: Any = None
        self._seeded_at: _dt.datetime | None = None

    def _client(self):
        try:
            import requests
        except ModuleNotFoundError as e:  # infra: dependency missing
            raise DataClientError("requests not installed") from e

        fresh = (
            self._session is not None
            and self._seeded_at is not None
            and _dt.datetime.now(_dt.UTC) - self._seeded_at < self._TTL
        )
        if fresh:
            return self._session

        s = requests.Session()
        s.headers.update({"User-Agent": _UA, "Accept": "application/json,text/html"})
        try:
            s.get("https://www.nseindia.com", timeout=10)
            s.get("https://www.nseindia.com/market-data/securities-available-for-trading",
                  timeout=10)
        except Exception as e:
            raise DataClientError(f"NSE session bootstrap failed: {e}") from e
        self._session, self._seeded_at = s, _dt.datetime.now(_dt.UTC)
        return s

    def get_json(self, path: str, params: dict | None = None) -> Any:
        """GET nseindia.com<path>. Raises DataClientError on any infra failure.
        Returns parsed JSON (caller decides whether an empty payload = no-data)."""
        c = self._client()
        url = f"https://www.nseindia.com{path}"
        try:
            r = c.get(url, params=params, timeout=15)
        except Exception as e:
            raise DataClientError(f"GET {path} failed: {e}") from e
        if r.status_code == 401:
            # stale cookies -> reseed once
            self._seeded_at = None
            c = self._client()
            try:
                r = c.get(url, params=params, timeout=15)
            except Exception as e:
                raise DataClientError(f"GET {path} retry failed: {e}") from e
        if r.status_code >= 400:
            raise DataClientError(f"GET {path} -> {r.status_code}: {r.text[:200]}")
        try:
            return r.json()
        except ValueError as e:
            raise DataClientError(f"GET {path} returned non-JSON") from e


# ---------------------------------------------------------------------------
# The client
# ---------------------------------------------------------------------------

def _iso(d: str) -> _dt.date:
    return _dt.date.fromisoformat(d[:10])


def _nse_ddmmyyyy(d: str) -> str:
    y, m, dd = d[:10].split("-")
    return f"{dd}-{m}-{y}"


@dataclass
class DrishtiDataClient:
    """India DataClient. `ticker` is the bare NSE symbol ("RELIANCE", "TCS").
    Pass "SYMBOL.BO" to force BSE for the Yahoo-backed calls."""

    _nse: _NSESession = field(default_factory=_NSESession)

    # -- Yahoo-backed (working) ------------------------------------------

    def _yf(self, ticker: str):
        try:
            import yfinance
        except ModuleNotFoundError as e:
            raise DataClientError("yfinance not installed (pip install yfinance)") from e
        sym = ticker if ("." in ticker) else f"{ticker}.NS"
        return yfinance.Ticker(sym)

    def get_prices(self, ticker: str, start_date: str, end_date: str, **kwargs) -> list[Price]:
        t = self._yf(ticker)
        try:
            # end is exclusive in yfinance; bump a day
            end_excl = (_iso(end_date) + _dt.timedelta(days=1)).isoformat()
            hist = t.history(start=start_date, end=end_excl, interval="1d", auto_adjust=False)
        except Exception as e:
            raise DataClientError(f"yfinance history {ticker}: {e}") from e
        if hist is None or hist.empty:
            return []  # genuinely no bars in range
        out: list[Price] = []
        for idx, row in hist.iterrows():
            out.append(Price(
                open=float(row["Open"]), close=float(row["Close"]),
                high=float(row["High"]), low=float(row["Low"]),
                volume=int(row["Volume"]), time=idx.date().isoformat(),
            ))
        return out

    def get_company_facts(self, ticker: str) -> CompanyFacts | None:
        t = self._yf(ticker)
        try:
            info = t.info or {}
        except Exception as e:
            raise DataClientError(f"yfinance info {ticker}: {e}") from e
        if not info:
            return None
        return CompanyFacts(
            ticker=ticker.split(".")[0],
            name=info.get("longName") or info.get("shortName"),
            sector=info.get("sector"),
            industry=info.get("industry"),
            exchange=info.get("exchange"),
            market_cap=info.get("marketCap"),
        )

    def get_market_cap(self, ticker: str, end_date: str) -> float | None:
        # NOTE: latest-only. In a backtest this is lookahead — snapshot.py
        # deliberately reads market_cap from the point-in-time metrics row
        # instead, so this is only used outside PIT contexts.
        facts = self.get_company_facts(ticker)
        return facts.market_cap if facts else None

    # -- Yahoo + Screener (partial, POINT-IN-TIME CAVEAT) ---------------

    def get_financial_metrics(
        self, ticker: str, end_date: str, period: str = "ttm", limit: int = 10,
    ) -> list[FinancialMetrics]:
        """Per-period ratios, newest first.

        CAVEAT (documented in PORT_NOTES.md): the Yahoo path yields only ~4-8
        recent quarters and carries NO filing_date, so point-in-time filtering
        by `end_date` is approximate (we fall back to report_period + a fixed
        45-day publication lag). Backtests of value personas therefore have
        mild lookahead until the Screener enrichment (real board-approval /
        filing dates) lands. Live use as-of today is fine.
        """
        try:
            import yfinance  # noqa: F401
        except ModuleNotFoundError as e:
            raise DataClientError("yfinance not installed") from e
        t = self._yf(ticker)
        try:
            qf = t.quarterly_financials       # income statement
            qbs = t.quarterly_balance_sheet
            info = t.info or {}
        except Exception as e:
            raise DataClientError(f"yfinance financials {ticker}: {e}") from e
        if qf is None or qf.empty:
            return []

        cutoff = _iso(end_date)
        lag = _dt.timedelta(days=45)  # ~ board-approval to public filing
        rows: list[FinancialMetrics] = []
        for col in qf.columns:                # columns are period-end Timestamps
            rp = col.date()
            if rp + lag > cutoff:             # not yet "filed" as of end_date
                continue
            g = lambda df, k: (float(df.loc[k, col]) if (k in df.index and df.loc[k, col] == df.loc[k, col]) else None)  # noqa: E731
            revenue = g(qf, "Total Revenue")
            net_income = g(qf, "Net Income")
            equity = g(qbs, "Stockholders Equity") or g(qbs, "Total Stockholder Equity")
            debt = g(qbs, "Total Debt")
            rows.append(FinancialMetrics(
                ticker=ticker.split(".")[0],
                report_period=rp.isoformat(),
                period=period,
                filing_date=(rp + lag).isoformat(),   # APPROX — replace w/ Screener
                market_cap=info.get("marketCap"),
                price_to_earnings_ratio=info.get("trailingPE"),
                price_to_book_ratio=info.get("priceToBook"),
                return_on_equity=(net_income / equity if net_income and equity else None),
                net_margin=(net_income / revenue if net_income and revenue else None),
                debt_to_equity=(debt / equity if debt and equity else None),
            ))
            if len(rows) >= limit:
                break
        return rows

    # -- NSE public API (working, thin) --------------------------------

    def get_insider_trades(
        self, ticker: str, end_date: str, start_date: str | None = None, limit: int = 1000,
    ) -> list[InsiderTrade]:
        """SEBI PIT disclosures. NSE endpoint: /api/corporates-pit
        (same as reference/NSE-MCP/src/tools/insiderTrading.ts).
        NSE only serves ~last 12 months; older history is not available here."""
        sym = ticker.split(".")[0].upper()
        frm = _nse_ddmmyyyy(start_date) if start_date else _nse_ddmmyyyy(
            (_iso(end_date) - _dt.timedelta(days=365)).isoformat())
        payload = self._nse.get_json("/api/corporates-pit", {
            "symbol": sym, "from_date": frm, "to_date": _nse_ddmmyyyy(end_date),
            "type": "individual",
        })
        rows = (payload or {}).get("data") or []
        out: list[InsiderTrade] = []
        for r in rows:
            intim = str(r.get("intimDt") or "")[:10]
            if intim and intim > end_date:        # point-in-time guard
                continue
            before = float(r.get("befAcqSharesNo") or 0)
            after = float(r.get("aftAcqSharesNo") or 0)
            out.append(InsiderTrade(
                ticker=sym,
                name=str(r.get("acqName") or "").strip(),
                filing_date=intim,
                title=str(r.get("personCategory") or "").strip(),
                transaction_shares=float(r.get("secAcq") or 0),
                transaction_type=("sell" if after < before else "buy"),
                shares_owned_before_transaction=before,
                shares_owned_after_transaction=after,
                transaction_date=str(r.get("acqtoDt") or "")[:10] or None,
            ))
        return out[:limit]

    def get_news(
        self, ticker: str, end_date: str, start_date: str | None = None, limit: int = 1000,
    ) -> list[CompanyNews]:
        """NSE corporate announcements (Tier-1 filings per config/news-sources.md).
        Endpoint: /api/corporate-announcements?index=equities&symbol=...
        (same as reference/NSE-MCP/src/tools/announcements.ts). This is filings,
        not press — media whitelist RSS is a separate layer, not this method."""
        sym = ticker.split(".")[0].upper()
        payload = self._nse.get_json("/api/corporate-announcements", {
            "index": "equities", "symbol": sym,
        })
        rows = payload or []
        if isinstance(rows, dict):
            rows = rows.get("data") or []
        out: list[CompanyNews] = []
        for r in rows:
            when = str(r.get("an_dt") or r.get("sort_date") or "")[:10]
            if when and when > end_date:
                continue
            if start_date and when and when < start_date:
                continue
            out.append(CompanyNews(
                ticker=sym,
                title=str(r.get("desc") or r.get("attchmntText") or "").strip(),
                source="NSE",
                date=when or None,
                url=r.get("attchmntFile"),
            ))
        return out[:limit]

    # -- Not portable cheaply -----------------------------------------

    def get_earnings(self, ticker: str):
        raise NotImplementedError(
            "get_earnings: no free Indian consensus-EPS feed => no BEAT/MISS. "
            "PEAD persona is out of scope until an estimates source is chosen. "
            "See reference/PORT_NOTES.md.")

    def get_earnings_history(self, ticker: str, limit: int = 12):
        raise NotImplementedError(
            "get_earnings_history: same as get_earnings — needs consensus estimates.")


# ---------------------------------------------------------------------------
# Shape check (no network) — the ponytail one-runnable-check
# ---------------------------------------------------------------------------

def _demo() -> None:
    c = DrishtiDataClient()

    # date helpers
    assert _nse_ddmmyyyy("2026-09-10") == "10-09-2026"
    assert _iso("2026-09-10T00:00:00") == _dt.date(2026, 9, 10)

    # the two unportable methods must RAISE, not return empty
    for fn in (lambda: c.get_earnings("TCS"), lambda: c.get_earnings_history("TCS")):
        try:
            fn()
        except NotImplementedError:
            pass
        else:
            raise AssertionError("expected NotImplementedError")

    # every protocol method is present and callable
    for m in ("get_prices", "get_financial_metrics", "get_news", "get_insider_trades",
              "get_company_facts", "get_earnings", "get_earnings_history", "get_market_cap"):
        assert callable(getattr(c, m)), m

    print("shape check OK")


if __name__ == "__main__":
    _demo()

import YahooFinance from "yahoo-finance2";

const yahooFinance = new YahooFinance();

export interface UsQuoteArgs {
  /** US ticker, e.g. AAPL, MSFT, NVDA (no exchange suffix). Required. */
  ticker: string;
}

export interface UsQuote {
  ticker: string;
  currency: string;
  price: number;
  previousClose: number;
  changePct: number | null;
  fiftyTwoWeekHigh: number | null;
  fiftyTwoWeekLow: number | null;
  marketCap: number | null;
  trailingPE: number | null;
  forwardPE: number | null;
  dividendYield: number | null;
  earningsDate: string | null;
}

export async function getUsQuote(args: UsQuoteArgs): Promise<UsQuote> {
  const ticker = (args.ticker ?? "").trim().toUpperCase();
  if (!/^[A-Z][A-Z0-9.\-]{0,9}$/.test(ticker)) throw new Error(`invalid US ticker: ${JSON.stringify(args.ticker)}`);

  const q: any = await yahooFinance.quote(ticker);
  const price = q.regularMarketPrice ?? 0;
  const prev = q.regularMarketPreviousClose ?? 0;
  return {
    ticker: q.symbol ?? ticker,
    currency: q.currency ?? "USD",
    price,
    previousClose: prev,
    changePct: prev ? Number((((price - prev) / prev) * 100).toFixed(2)) : null,
    fiftyTwoWeekHigh: q.fiftyTwoWeekHigh ?? null,
    fiftyTwoWeekLow: q.fiftyTwoWeekLow ?? null,
    marketCap: q.marketCap ?? null,
    trailingPE: q.trailingPE ?? null,
    forwardPE: q.forwardPE ?? null,
    dividendYield: q.dividendYield ?? null,
    earningsDate: q.earningsTimestamp ? new Date(q.earningsTimestamp).toISOString() : null,
  };
}

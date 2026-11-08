import YahooFinance from "yahoo-finance2";

const yahooFinance = new YahooFinance();

// Layer 1 "macro tide" — the global backdrop Indian equities trade against.
const TICKERS: Record<string, string> = {
  DXY: "DX-Y.NYB", // US Dollar Index
  US_10Y: "^TNX", // US 10-year Treasury yield
  Brent_Crude: "BZ=F",
  India_VIX: "^INDIAVIX",
  USD_INR: "INR=X",
  US_VIX: "^VIX",
  Nifty_50: "^NSEI",
};

export interface Indicator {
  value: number | null;
  changePct: number | null;
}

export interface MacroRegimeResult {
  asOf: string;
  indicators: Record<string, Indicator>;
  macroScore: number; // macro-only, roughly -6..+6; combine with participant-OI score for a full regime read
  signals: string[];
  note: string;
}

export async function getMacroRegime(): Promise<MacroRegimeResult> {
  const symbols = Object.values(TICKERS);
  const quotes = await yahooFinance.quote(symbols);
  const bySymbol = new Map((Array.isArray(quotes) ? quotes : [quotes]).map((q: any) => [q.symbol, q]));

  const indicators: Record<string, Indicator> = {};
  for (const [name, sym] of Object.entries(TICKERS)) {
    const q: any = bySymbol.get(sym);
    const price = q?.regularMarketPrice ?? null;
    const prev = q?.regularMarketPreviousClose ?? null;
    indicators[name] = {
      value: price,
      changePct: price != null && prev ? Number((((price - prev) / prev) * 100).toFixed(2)) : null,
    };
  }

  const { score, signals } = scoreMacro(indicators);

  return {
    asOf: new Date().toISOString(),
    indicators,
    macroScore: score,
    signals,
    note:
      "Macro-only regime read. Thresholds are calibration-sensitive heuristics (medium confidence), " +
      "not precise signals — review them periodically. Combine with get_participant_oi (derivatives " +
      "smart-money score) for the full regime picture.",
  };
}

function scoreMacro(ind: Record<string, Indicator>): { score: number; signals: string[] } {
  let score = 0;
  const signals: string[] = [];
  const v = (k: string) => ind[k]?.value ?? null;
  const chg = (k: string) => ind[k]?.changePct ?? null;

  const dxy = v("DXY");
  if (dxy != null) {
    if (dxy > 104) { score -= 1; signals.push(`Bearish: DXY (dollar index) elevated at ${dxy} (>104)`); }
    else if (dxy < 100) { score += 1; signals.push(`Bullish: DXY weak at ${dxy} (<100)`); }
  }
  const y = v("US_10Y");
  if (y != null) {
    if (y > 4.5) { score -= 1; signals.push(`Bearish: US 10Y yield high at ${y}% (>4.5)`); }
    else if (y < 3.8) { score += 1; signals.push(`Bullish: US 10Y yield low at ${y}% (<3.8)`); }
  }
  const brent = v("Brent_Crude");
  if (brent != null) {
    if (brent > 90) { score -= 1; signals.push(`Bearish: Brent crude elevated at $${brent} (>90)`); }
    else if (brent < 75) { score += 1; signals.push(`Bullish: Brent crude soft at $${brent} (<75)`); }
  }
  const ivix = v("India_VIX");
  if (ivix != null) {
    if (ivix > 20) { score -= 1; signals.push(`Bearish: India VIX (volatility index) elevated at ${ivix} (>20)`); }
    else if (ivix < 14) { score += 1; signals.push(`Bullish: India VIX complacent at ${ivix} (<14)`); }
  }
  // USD/INR: use direction, not a stale absolute level (the rupee's absolute band drifts over time).
  const inrChg = chg("USD_INR");
  if (inrChg != null) {
    if (inrChg > 0.15) { score -= 1; signals.push(`Bearish: rupee weakening (USD/INR +${inrChg}%)`); }
    else if (inrChg < -0.15) { score += 1; signals.push(`Bullish: rupee strengthening (USD/INR ${inrChg}%)`); }
  }
  // US VIX premium over India VIX = global risk being priced ahead of India.
  const usvix = v("US_VIX");
  if (usvix != null && ivix != null && usvix > ivix + 2) {
    score -= 1; signals.push("Bearish: US VIX premium over India VIX — global risk not yet priced in India");
  }
  if (signals.length === 0) signals.push("Neutral: no macro extreme triggered");
  return { score, signals };
}

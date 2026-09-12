import { resolveCik, companyFacts } from "../us/edgar.js";

export interface UsFundamentalsArgs {
  /** US ticker, e.g. AAPL, MSFT, NVDA. Required. */
  ticker: string;
  /** How many fiscal years back. Default 4. */
  years?: number;
}

// Concept fallbacks — different filers tag the same line differently.
const CONCEPTS: Record<string, string[]> = {
  revenue: ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet"],
  netIncome: ["NetIncomeLoss"],
  operatingIncome: ["OperatingIncomeLoss"],
  assets: ["Assets"],
  liabilities: ["Liabilities"],
  equity: ["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
  operatingCashFlow: ["NetCashProvidedByUsedInOperatingActivities"],
};

export interface UsFundamentalsResult {
  ticker: string;
  entity: string | null;
  currency: string;
  annual: Record<string, Record<string, number>>; // fiscalYear -> metric -> value
  note: string;
  error: string | null;
}

/** Latest annual (10-K, full-year) value per fiscal year for a concept. */
function annualByFy(facts: any, names: string[]): Record<number, number> {
  const gaap = facts.facts?.["us-gaap"] ?? {};
  const out: Record<number, number> = {};
  for (const name of names) {
    const usd = gaap[name]?.units?.USD;
    if (!Array.isArray(usd)) continue;
    for (const row of usd) {
      if (row.form === "10-K" && row.fp === "FY" && typeof row.fy === "number" && typeof row.val === "number") {
        out[row.fy] = row.val; // later entries overwrite = latest restated value wins
      }
    }
    if (Object.keys(out).length) break; // first concept that resolves wins
  }
  return out;
}

export async function getUsFundamentals(args: UsFundamentalsArgs): Promise<UsFundamentalsResult> {
  const ticker = (args.ticker ?? "").trim().toUpperCase();
  if (!ticker) throw new Error("ticker is required");
  const years = args.years ?? 4;

  const resolved = await resolveCik(ticker);
  if (!resolved) return { ticker, entity: null, currency: "USD", annual: {}, note: "", error: `No SEC CIK for "${ticker}" (not a US filer, or wrong ticker).` };

  const facts = await companyFacts(resolved.cik);
  if (!facts) return { ticker, entity: resolved.title, currency: "USD", annual: {}, note: "", error: "SEC company facts unavailable (fetch failed)." };

  const perMetric: Record<string, Record<number, number>> = {};
  const allFy = new Set<number>();
  for (const [metric, names] of Object.entries(CONCEPTS)) {
    perMetric[metric] = annualByFy(facts, names);
    Object.keys(perMetric[metric]).forEach((fy) => allFy.add(Number(fy)));
  }
  const fys = [...allFy].sort((a, b) => b - a).slice(0, years).sort((a, b) => a - b);

  const annual: Record<string, Record<string, number>> = {};
  for (const fy of fys) {
    annual[String(fy)] = {};
    for (const metric of Object.keys(CONCEPTS)) {
      const v = perMetric[metric][fy];
      if (v !== undefined) annual[String(fy)][metric] = v;
    }
  }

  return {
    ticker,
    entity: facts.entityName ?? resolved.title,
    currency: "USD",
    annual,
    note: "Restated annual (10-K, full-year) figures from SEC EDGAR XBRL. Values in USD. Derive margins/ROE from these; a missing metric means that concept was not tagged, not zero.",
    error: null,
  };
}

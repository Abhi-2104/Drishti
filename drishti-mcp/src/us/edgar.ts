// SEC EDGAR — free US company facts + filings. No API key; SEC requires a descriptive
// User-Agent with contact info (we use a generic tool contact, never the user's email).
import { cacheGet, cacheSet } from "../cache.js";

const UA = process.env.EDGAR_UA ?? "Drishti Research (research-tool contact@drishti.local)";

async function edgarJson<T>(url: string, ttlMs: number): Promise<T | null> {
  const hit = cacheGet<T>(url);
  if (hit !== undefined) return hit;
  const res = await fetch(url, {
    headers: { "User-Agent": UA, Accept: "application/json" },
    signal: AbortSignal.timeout(20000),
  });
  if (!res.ok) return null;
  const data = (await res.json()) as T;
  cacheSet(url, data, ttlMs);
  return data;
}

interface TickerRow { cik_str: number; ticker: string; title: string }

/** Resolve a US ticker to its 10-digit zero-padded CIK. */
export async function resolveCik(ticker: string): Promise<{ cik: string; title: string } | null> {
  const map = await edgarJson<Record<string, TickerRow>>(
    "https://www.sec.gov/files/company_tickers.json",
    24 * 60 * 60 * 1000,
  );
  if (!map) return null;
  const t = ticker.trim().toUpperCase();
  const row = Object.values(map).find((r) => r.ticker === t);
  if (!row) return null;
  return { cik: String(row.cik_str).padStart(10, "0"), title: row.title };
}

export interface CompanyFacts {
  entityName: string;
  facts: { "us-gaap"?: Record<string, { units: Record<string, Array<Record<string, unknown>>> }> };
}

export async function companyFacts(cik: string): Promise<CompanyFacts | null> {
  return edgarJson<CompanyFacts>(
    `https://data.sec.gov/api/xbrl/companyfacts/CIK${cik}.json`,
    12 * 60 * 60 * 1000,
  );
}

export interface Submissions {
  name: string;
  filings: { recent: Record<string, unknown[]> };
}

export async function submissions(cik: string): Promise<Submissions | null> {
  return edgarJson<Submissions>(
    `https://data.sec.gov/submissions/CIK${cik}.json`,
    6 * 60 * 60 * 1000,
  );
}

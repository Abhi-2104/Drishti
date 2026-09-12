import { resolveCik, submissions } from "../us/edgar.js";

export interface UsFilingsArgs {
  /** US ticker, e.g. AAPL. Required. */
  ticker: string;
  /** Filter to these form types (e.g. ["10-K","10-Q","8-K","4"]). Omit for the common set. */
  forms?: string[];
  /** Max filings. Default 25. */
  limit?: number;
}

export interface UsFiling {
  form: string;
  filingDate: string;
  reportDate: string | null;
  description: string;
  url: string;
  isInsider: boolean; // Form 3/4/5
}

export interface UsFilingsResult {
  ticker: string;
  entity: string | null;
  count: number;
  filings: UsFiling[];
  note: string;
  error: string | null;
}

const DEFAULT_FORMS = ["10-K", "10-Q", "8-K", "4", "SC 13D", "SC 13G"];

export async function getUsFilings(args: UsFilingsArgs): Promise<UsFilingsResult> {
  const ticker = (args.ticker ?? "").trim().toUpperCase();
  if (!ticker) throw new Error("ticker is required");
  const forms = (args.forms && args.forms.length ? args.forms : DEFAULT_FORMS).map((f) => f.toUpperCase());
  const limit = args.limit ?? 25;

  const resolved = await resolveCik(ticker);
  if (!resolved) return { ticker, entity: null, count: 0, filings: [], note: "", error: `No SEC CIK for "${ticker}".` };

  const sub = await submissions(resolved.cik);
  if (!sub) return { ticker, entity: resolved.title, count: 0, filings: [], note: "", error: "SEC submissions unavailable (fetch failed)." };

  const r = sub.filings.recent as Record<string, any[]>;
  const n = r.form?.length ?? 0;
  const cikNum = String(Number(resolved.cik)); // un-padded for the archive path
  const out: UsFiling[] = [];
  for (let i = 0; i < n && out.length < limit; i++) {
    const form = String(r.form[i]).toUpperCase();
    if (!forms.includes(form)) continue;
    const accn = String(r.accessionNumber[i]).replace(/-/g, "");
    const doc = r.primaryDocument[i];
    out.push({
      form,
      filingDate: r.filingDate[i],
      reportDate: r.reportDate?.[i] || null,
      description: r.primaryDocDescription?.[i] || form,
      url: `https://www.sec.gov/Archives/edgar/data/${cikNum}/${accn}/${doc}`,
      isInsider: ["3", "4", "5"].includes(form),
    });
  }

  return {
    ticker,
    entity: sub.name ?? resolved.title,
    count: out.length,
    filings: out,
    note: "Recent SEC filings. 10-K annual / 10-Q quarterly reports; 8-K material events; Form 4 = insider transactions; SC 13D/G = large-stake disclosures. Fetch a document URL + Read it for the primary-source detail.",
    error: null,
  };
}

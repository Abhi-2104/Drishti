import { fetchNSE } from "../nse/fetch.js";

export interface IpoArgs {
  /** "current" (open now), "upcoming", or "all". Default: all. */
  status?: "current" | "upcoming" | "all";
  /** Optional filter: company name or symbol substring (case-insensitive). */
  query?: string;
}

interface RawRow {
  [k: string]: unknown;
}

export interface IpoRow {
  symbol: string | null;
  company: string | null;
  isin: string | null;
  series: string | null;
  priceBand: string | null;
  issueSize: string | null;
  lotSize: string | null;
  open: string | null;
  close: string | null;
  status: string | null;
  exchange: string | null;
  raw: RawRow; // full source row, so nothing is silently dropped
}

function pick(r: RawRow, ...keys: string[]): string | null {
  for (const k of keys) {
    const v = r[k];
    if (v !== undefined && v !== null && String(v).trim() !== "") return String(v).trim();
  }
  return null;
}

function normalize(r: RawRow): IpoRow {
  const band =
    pick(r, "priceBand", "price_band", "issuePrice", "price") ??
    ((pick(r, "minPrice", "lowPrice") && pick(r, "maxPrice", "highPrice"))
      ? `${pick(r, "minPrice", "lowPrice")} - ${pick(r, "maxPrice", "highPrice")}`
      : null);
  return {
    symbol: pick(r, "symbol"),
    company: pick(r, "companyName", "company", "name", "issuerName"),
    isin: pick(r, "isin", "isinNumber"),
    series: pick(r, "series"),
    priceBand: band,
    issueSize: pick(r, "issueSize", "issue_size"),
    lotSize: pick(r, "lotSize", "lot_size", "marketLot"),
    open: pick(r, "issueStartDate", "biddingStartDate", "issueOpenDate", "open"),
    close: pick(r, "issueEndDate", "biddingEndDate", "issueCloseDate", "close"),
    status: pick(r, "status"),
    exchange: pick(r, "isBse") === "1" ? "BSE/NSE" : "NSE",
    raw: r,
  };
}

function asRows(resp: unknown): RawRow[] {
  if (Array.isArray(resp)) return resp as RawRow[];
  if (resp && typeof resp === "object") {
    const d = (resp as Record<string, unknown>).data;
    if (Array.isArray(d)) return d as RawRow[];
  }
  return [];
}

async function safe(path: string): Promise<{ rows: RawRow[]; error: string | null }> {
  try {
    return { rows: asRows(await fetchNSE(path, { ttlMs: 10 * 60 * 1000 })), error: null };
  } catch (e) {
    // Failure is a value, not a silent empty list (a blocked NSE feed must be visible).
    return { rows: [], error: e instanceof Error ? e.message : String(e) };
  }
}

export interface IpoResult {
  current: IpoRow[];
  upcoming: IpoRow[];
  errors: Record<string, string>; // which feed failed, if any
  note: string;
  source: string;
}

export async function getIpos(args: IpoArgs = {}): Promise<IpoResult> {
  const status = args.status ?? "all";
  const errors: Record<string, string> = {};
  let current: IpoRow[] = [];
  let upcoming: IpoRow[] = [];

  if (status === "current" || status === "all") {
    const r = await safe("/api/ipo-current-issue");
    if (r.error) errors["current"] = r.error;
    current = r.rows.map(normalize);
  }
  if (status === "upcoming" || status === "all") {
    const r = await safe("/api/all-upcoming-issues?category=ipo");
    if (r.error) errors["upcoming"] = r.error;
    upcoming = r.rows.map(normalize);
  }

  const q = (args.query ?? "").trim().toLowerCase();
  if (q) {
    const m = (x: IpoRow) =>
      (x.symbol ?? "").toLowerCase().includes(q) || (x.company ?? "").toLowerCase().includes(q);
    current = current.filter(m);
    upcoming = upcoming.filter(m);
  }

  return {
    current,
    upcoming,
    errors,
    note:
      Object.keys(errors).length > 0
        ? "A feed failed (see errors) — treat as UNKNOWN, not 'no IPOs'. NSE feed is egress-sensitive."
        : "Mainboard IPOs from NSE. For the prospectus (DRHP/RHP), fetch the offer document (SEBI/exchange) and read it — that is the primary doc for /ipo analysis.",
    source: "NSE public issues API (ipo-current-issue, all-upcoming-issues)",
  };
}

import { fetchScreener } from "../screener/fetch.js";

export interface ScreenerArgs {
  /** NSE symbol (TITAN), BSE code (511658), or company name. Required. */
  symbol: string;
}

interface DocLink {
  label: string;
  url: string;
}

export interface ScreenerResult {
  query: string;
  resolved: { name: string; url: string } | null;
  ratios: Record<string, string>;
  profitLoss: string;
  quarters: string;
  balanceSheet: string;
  cashFlow: string;
  ratiosSection: string;
  shareholding: string;
  documents: { concalls: DocLink[]; annualReports: DocLink[] };
  note: string;
  source: string;
}

// --- HTML helpers (tag-strip + section-slice only; no table parsing) -------

function clean(html: string): string {
  return html
    .replace(/<script[\s\S]*?<\/script>/gi, " ")
    .replace(/<style[\s\S]*?<\/style>/gi, " ")
    .replace(/<[^>]+>/g, " ")
    .replace(/&nbsp;/g, " ")
    .replace(/&amp;/g, "&")
    .replace(/&#8377;|&rupee;/gi, "₹")
    .replace(/&[a-z]+;/gi, " ")
    .replace(/[ \t]+/g, " ")
    .replace(/ *\n */g, "\n")
    .replace(/\n{2,}/g, "\n")
    .trim();
}

/** Slice raw HTML of a <section id="..."> up to the next <section>. */
function sliceSection(html: string, id: string): string {
  const start = html.indexOf(`<section id="${id}"`);
  if (start === -1) return "";
  const rest = html.slice(start);
  const next = rest.indexOf("<section ", 10);
  return next === -1 ? rest : rest.slice(0, next);
}

function sectionText(html: string, id: string, cap = 6000): string {
  return clean(sliceSection(html, id)).slice(0, cap);
}

/** Parse the #top-ratios <li> list into name -> value. */
function parseTopRatios(html: string): Record<string, string> {
  const start = html.indexOf('id="top-ratios"');
  if (start === -1) return {};
  const block = html.slice(start, start + html.slice(start).indexOf("</ul>") + 5);
  const out: Record<string, string> = {};
  for (const li of block.matchAll(/<li[^>]*>([\s\S]*?)<\/li>/gi)) {
    const name = clean((li[1].match(/class="name">([\s\S]*?)<\/span>/i)?.[1] ?? ""));
    if (!name) continue;
    const value = clean(li[1]).replace(name, "").replace(/\s+/g, " ").trim();
    if (value) out[name] = value;
  }
  return out;
}

/**
 * BSE serves filings via a redirect page (AnnPdfOpen.aspx?Pname=<file>.pdf).
 * Rewrite to the direct PDF so it can be downloaded + Read without following
 * the 302. Non-BSE links (researchbytes etc.) pass through unchanged.
 */
function directPdfUrl(url: string): string {
  const m = url.match(/AnnPdfOpen\.aspx\?Pname=([^&]+)/i);
  return m ? `https://www.bseindia.com/xml-data/corpfiling/AttachHis/${m[1]}` : url;
}

/** Pull concall + annual-report links out of the #documents section. */
function parseDocuments(html: string): { concalls: DocLink[]; annualReports: DocLink[] } {
  const docs = sliceSection(html, "documents");
  const concalls: DocLink[] = [];
  const annualReports: DocLink[] = [];
  for (const a of docs.matchAll(/<a[^>]+href="([^"]+)"[^>]*>([\s\S]*?)<\/a>/gi)) {
    const url = a[1];
    const label = clean(a[2]);
    if (!label && !url) continue;
    if (/concall|transcript|earnings call/i.test(label + url)) {
      concalls.push({ label: label || "Concall", url: directPdfUrl(url) });
    } else if (/annual report|AnnualReport/i.test(label + url)) {
      annualReports.push({ label: label || "Annual Report", url: directPdfUrl(url) });
    }
  }
  return { concalls: concalls.slice(0, 8), annualReports: annualReports.slice(0, 6) };
}

export async function getScreener(args: ScreenerArgs): Promise<ScreenerResult> {
  const query = (args.symbol ?? "").trim();
  if (!query) throw new Error("symbol is required");

  // 1. Resolve slug via Screener's search API — handles NSE symbol, BSE code, name.
  //    This is the fallback that beats the raw-slug 404 problem (e.g. NETTLINX).
  const searchRaw = await fetchScreener(
    `/api/company/search/?q=${encodeURIComponent(query)}`,
    { accept: "application/json", ttlMs: 24 * 60 * 60 * 1000 },
  );
  if (!searchRaw) throw new Error(`Screener: search returned nothing for "${query}"`);

  let hits: Array<{ name: string; url: string }>;
  try {
    hits = JSON.parse(searchRaw);
  } catch {
    throw new Error(`Screener: non-JSON search response for "${query}"`);
  }
  if (!hits.length) {
    throw new Error(`Screener: no company found for "${query}" (not listed, or wrong name/code)`);
  }

  // Prefer an exact ticker match in the URL (/company/TITAN/...), else first hit.
  const up = query.toUpperCase();
  const resolved =
    hits.find((h) => new RegExp(`/company/${up}/`, "i").test(h.url)) ?? hits[0];

  // 2. Fetch the resolved company page (already includes /consolidated/ when it exists).
  const page = await fetchScreener(resolved.url);
  if (!page) throw new Error(`Screener: page 404 for resolved url ${resolved.url}`);

  const note =
    hits.length > 1
      ? `Resolved "${query}" -> ${resolved.name}. Other matches: ${hits
          .filter((h) => h !== resolved)
          .slice(0, 3)
          .map((h) => h.name)
          .join(", ")}`
      : `Resolved "${query}" -> ${resolved.name}`;

  return {
    query,
    resolved: { name: resolved.name, url: resolved.url },
    ratios: parseTopRatios(page),
    profitLoss: sectionText(page, "profit-loss"),
    quarters: sectionText(page, "quarters"),
    balanceSheet: sectionText(page, "balance-sheet"),
    cashFlow: sectionText(page, "cash-flow"),
    ratiosSection: sectionText(page, "ratios"),
    shareholding: sectionText(page, "shareholding"),
    documents: parseDocuments(page),
    note,
    source: `Screener.in ${resolved.url}`,
  };
}

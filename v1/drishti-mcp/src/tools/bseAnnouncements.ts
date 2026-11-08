import { fetchBse } from "../bse/fetch.js";

export interface BseAnnouncementsArgs {
  /** NSE symbol, company name, or 6-digit BSE scrip code. Required. */
  symbol: string;
  /** Look back N days. Default: 90. */
  daysBack?: number;
  /** Max rows. Default: 40. */
  limit?: number;
}

export interface BseAnnouncement {
  date: string;
  category: string;
  subCategory: string;
  headline: string;
  critical: boolean;
  pdf: string | null;
}

export interface BseAnnouncementsResult {
  symbol: string;
  scripCode: string;
  count: number;
  announcements: BseAnnouncement[];
  note: string;
  source: string;
}

function ymd(d: Date): string {
  return d.toISOString().slice(0, 10).replace(/-/g, "");
}

function strip(s: string): string {
  return (s ?? "")
    .replace(/<[^>]+>/g, " ")
    .replace(/&amp;/g, "&")
    .replace(/&#\d+;|&[a-z]+;/gi, " ")
    .replace(/\s+/g, " ")
    .trim();
}

/** Resolve an NSE symbol / name / code to a 6-digit BSE scrip code. */
async function resolveScrip(query: string): Promise<string> {
  if (/^\d{6}$/.test(query)) return query;
  const html = await fetchBse(
    `/Msource/1D/getQouteSearch.aspx?Type=EQ&text=${encodeURIComponent(query)}&flag=site`,
    { accept: "text/html,*/*", ttlMs: 24 * 60 * 60 * 1000 },
  );
  const m = html?.match(/\/(\d{6})\//);
  if (!m) throw new Error(`BSE: no scrip code found for "${query}" (not BSE-listed, or wrong name)`);
  return m[1];
}

export async function getBseAnnouncements(args: BseAnnouncementsArgs): Promise<BseAnnouncementsResult> {
  const query = (args.symbol ?? "").trim();
  if (!query) throw new Error("symbol is required");
  const daysBack = args.daysBack ?? 90;
  const limit = args.limit ?? 40;

  const scripCode = await resolveScrip(query);
  const to = new Date();
  const from = new Date(Date.now() - daysBack * 24 * 60 * 60 * 1000);

  // AnnSubCategoryGetData is the live endpoint; AnnGetData is dead (always empty).
  const raw = await fetchBse(
    `/BseIndiaAPI/api/AnnSubCategoryGetData/w?pageno=1&strCat=-1&strPrevDate=${ymd(from)}` +
      `&strScrip=${scripCode}&strSearch=P&strToDate=${ymd(to)}&strType=C&subcategory=-1`,
    { ttlMs: 5 * 60 * 1000 },
  );

  let rows: Array<Record<string, unknown>> = [];
  if (raw && raw.trim().startsWith("{")) {
    try {
      rows = (JSON.parse(raw).Table as Array<Record<string, unknown>>) ?? [];
    } catch {
      /* fall through to empty */
    }
  }

  const announcements: BseAnnouncement[] = rows.slice(0, limit).map((r) => {
    const att = (r.ATTACHMENTNAME as string) || "";
    return {
      date: (r.NEWS_DT as string) || (r.DissemDT as string) || "",
      category: strip((r.CATEGORYNAME as string) || ""),
      subCategory: strip((r.SUBCATNAME as string) || ""),
      headline: strip((r.HEADLINE as string) || (r.NEWSSUB as string) || ""),
      critical: String(r.CRITICALNEWS ?? "0") === "1",
      pdf: att ? `https://www.bseindia.com/xml-data/corpfiling/AttachHis/${att}` : null,
    };
  });

  return {
    symbol: query,
    scripCode,
    count: announcements.length,
    announcements,
    note:
      announcements.length === 0
        ? `No BSE filings for scrip ${scripCode} in the last ${daysBack} days.`
        : `Filter insider/pledge signal on category/subCategory (e.g. "Insider Trading", "SAST", "Pledge"). Fetch pdf via direct URL + Read it.`,
    source: `BSE India AnnSubCategoryGetData, scrip ${scripCode}`,
  };
}

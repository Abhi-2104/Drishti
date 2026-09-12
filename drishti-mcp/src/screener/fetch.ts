import { fetchHttp, type HttpFetchOptions, type SourceConfig } from "../http.js";

const BASE = "https://www.screener.in";

// Screener.in — Indian company fundamentals (NSE + BSE). Fundamentals change
// slowly, so cache hard (6h). Set NSE_MCP_UA to override the browser UA.
const CONFIG: SourceConfig = {
  name: "Screener.in",
  userAgent:
    process.env.NSE_MCP_UA ??
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
  defaultTtlMs: 6 * 60 * 60 * 1000,
  defaultAccept: "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
  minIntervalMs: 500,
  timeoutMs: 30_000,
  defaultNullStatuses: [404],
};

/** Fetch a Screener path (page or /api). Returns null on 404. */
export async function fetchScreener(
  path: string,
  options: HttpFetchOptions = {},
): Promise<string | null> {
  return fetchHttp(`${BASE}${path}`, CONFIG, options);
}

import { fetchHttp, type HttpFetchOptions, type SourceConfig } from "../http.js";

// api.bseindia.com rejects requests without a bseindia.com Origin/Referer.
const CONFIG: SourceConfig = {
  name: "BSE India",
  userAgent:
    process.env.NSE_MCP_UA ??
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
  defaultTtlMs: 5 * 60 * 1000,
  defaultAccept: "application/json, text/plain, */*",
  minIntervalMs: 350,
  timeoutMs: 30_000,
  defaultNullStatuses: [],
};

/** Fetch a path under api.bseindia.com with the required BSE headers. */
export async function fetchBse(path: string, options: HttpFetchOptions = {}): Promise<string | null> {
  return fetchHttp(`https://api.bseindia.com${path}`, CONFIG, {
    ...options,
    headers: {
      Origin: "https://www.bseindia.com",
      Referer: "https://www.bseindia.com/",
      ...(options.headers ?? {}),
    },
  });
}

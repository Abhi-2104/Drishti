import { cacheGet, cacheSet } from "./cache.js";

export interface HttpFetchOptions {
  /** Skip the cache for this call. Default: cache enabled. */
  cache?: boolean;
  /** Override the source's default cache TTL. */
  ttlMs?: number;
  /** Override the source's default Accept header. */
  accept?: string;
  /**
   * HTTP statuses that resolve to `null` instead of throwing.
   * Used e.g. when 404 means "not found" rather than "error".
   */
  nullStatuses?: number[];
  /** Extra headers merged after the source defaults. */
  headers?: Record<string, string>;
}

export interface SourceConfig {
  name: string;
  userAgent: string;
  defaultTtlMs: number;
  defaultAccept: string;
  minIntervalMs?: number;
  timeoutMs?: number;
  defaultNullStatuses?: number[];
}

const DEFAULT_TIMEOUT_MS = 30_000;

class TransientError extends Error {}

const nextSlotByName = new Map<string, number>();

async function paceSlot(name: string, minIntervalMs: number): Promise<void> {
  if (minIntervalMs <= 0) return;
  const now = Date.now();
  const next = nextSlotByName.get(name) ?? 0;
  const jitter = Math.random() * 250; // spread requests so cadence isn't machine-regular
  const wait = Math.max(0, next - now) + jitter;
  nextSlotByName.set(name, Math.max(now, next) + minIntervalMs);
  if (wait > 0) await new Promise((r) => setTimeout(r, wait));
}

async function doFetch(
  url: string,
  config: SourceConfig,
  options: HttpFetchOptions,
  asBinary: boolean,
): Promise<string | ArrayBuffer | null> {
  await paceSlot(config.name, config.minIntervalMs ?? 0);
  const accept = options.accept ?? config.defaultAccept;
  const timeoutMs = config.timeoutMs ?? DEFAULT_TIMEOUT_MS;

  let res: Response;
  try {
    res = await fetch(url, {
      headers: {
        "User-Agent": config.userAgent,
        Accept: accept,
        ...(options.headers ?? {}),
      },
      signal: AbortSignal.timeout(timeoutMs),
    });
  } catch (err) {
    if (err instanceof Error && (err.name === "TimeoutError" || err.name === "AbortError")) {
      throw new Error(`${config.name} timed out after ${timeoutMs}ms for ${url}`);
    }
    throw err;
  }

  if (res.status >= 500) {
    throw new TransientError(`${config.name} returned ${res.status} for ${url}`);
  }

  const nullStatuses = options.nullStatuses ?? config.defaultNullStatuses ?? [];
  if (nullStatuses.includes(res.status)) return null;

  if (!res.ok) {
    throw new Error(`${config.name} returned ${res.status} for ${url}`);
  }

  return asBinary ? await res.arrayBuffer() : await res.text();
}

async function fetchWithRetry(
  url: string,
  config: SourceConfig,
  options: HttpFetchOptions,
  asBinary: boolean,
): Promise<string | ArrayBuffer | null> {
  try {
    return await doFetch(url, config, options, asBinary);
  } catch (err) {
    if (err instanceof TransientError) {
      return await doFetch(url, config, options, asBinary);
    }
    throw err;
  }
}

export async function fetchHttp(
  url: string,
  config: SourceConfig,
  options: HttpFetchOptions = {},
): Promise<string | null> {
  const useCache = options.cache !== false;
  const ttl = options.ttlMs ?? config.defaultTtlMs;

  if (useCache) {
    const hit = cacheGet<string>(url);
    if (hit !== undefined) return hit;
  }

  const text = (await fetchWithRetry(url, config, options, false)) as string | null;
  if (text === null) return null;

  if (useCache) cacheSet(url, text, ttl);
  return text;
}

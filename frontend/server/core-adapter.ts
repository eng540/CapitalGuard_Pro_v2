const HEALTH_TIMEOUT_MS = 10_000;

type CoreConfig = { baseUrl: string; apiKey: string };
export type CoreHealth = { status: "ok"; baseUrl: string };

export function getCoreConfig(env = process.env): CoreConfig {
  const rawUrl = env.CAPITALGUARD_CORE_BASE_URL?.trim();
  const apiKey = env.CAPITALGUARD_CORE_API_KEY?.trim();
  if (!rawUrl || !apiKey) throw new Error("CAPITALGUARD_CORE_NOT_CONFIGURED");
  const url = new URL(rawUrl);
  if (url.protocol !== "https:") throw new Error("CAPITALGUARD_CORE_URL_MUST_USE_HTTPS");
  return { baseUrl: url.toString().replace(/\/$/, ""), apiKey };
}

export async function probeCoreHealth(fetchImpl: typeof fetch = fetch, env = process.env): Promise<CoreHealth> {
  const config = getCoreConfig(env);
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), HEALTH_TIMEOUT_MS);
  try {
    const response = await fetchImpl(`${config.baseUrl}/health`, {
      headers: { Authorization: `Bearer ${config.apiKey}`, Accept: "application/json" },
      signal: controller.signal,
    });
    if (!response.ok) throw new Error(`CAPITALGUARD_CORE_HEALTH_${response.status}`);
    const payload = await response.json() as { status?: string };
    if (payload.status !== "ok") throw new Error("CAPITALGUARD_CORE_UNHEALTHY");
    return { status: "ok", baseUrl: config.baseUrl };
  } finally {
    clearTimeout(timer);
  }
}

export async function coreReadOnlyFetch(path: string, init: RequestInit = {}, fetchImpl: typeof fetch = fetch, env = process.env) {
  const config = getCoreConfig(env);
  if (!path.startsWith("/api/")) throw new Error("CAPITALGUARD_CORE_READ_PATH_REQUIRED");
  const response = await fetchImpl(`${config.baseUrl}${path}`, {
    ...init,
    headers: { ...init.headers, Authorization: `Bearer ${config.apiKey}`, Accept: "application/json" },
  });
  if (!response.ok) throw new Error(`CAPITALGUARD_CORE_API_${response.status}`);
  return response.json() as Promise<unknown>;
}

function query(path: string, params: Record<string, string>) {
  const search = new URLSearchParams(params);
  return `${path}?${search.toString()}`;
}

export async function coreGetPrice(symbol: string, fetchImpl: typeof fetch = fetch, env = process.env) {
  return coreReadOnlyFetch(query("/api/webapp/price", { symbol }), {}, fetchImpl, env);
}

export async function coreGetSignal(recId: number, fetchImpl: typeof fetch = fetch, env = process.env) {
  return coreReadOnlyFetch(`/api/webapp/signal/${recId}`, {}, fetchImpl, env);
}

export async function coreGetTmaPortfolio(initData: string, fetchImpl: typeof fetch = fetch, env = process.env) {
  if (!initData.trim()) throw new Error("CAPITALGUARD_TMA_INITDATA_REQUIRED");
  return coreReadOnlyFetch(query("/api/webapp/portfolio", { initData }), {}, fetchImpl, env);
}

import { describe, expect, it } from "vitest";
import { coreGetPrice, coreGetTraderReadModel, coreVerifyTelegramInitData, getCoreConfig, probeCoreHealth } from "./core-adapter";

describe("CapitalGuard Core adapter", () => {
  it("rejects a missing or insecure Core configuration", () => {
    expect(() => getCoreConfig({})).toThrow("CAPITALGUARD_CORE_NOT_CONFIGURED");
    expect(() => getCoreConfig({ CAPITALGUARD_CORE_BASE_URL: "http://core.local", CAPITALGUARD_CORE_API_KEY: "safe-key" })).toThrow("CAPITALGUARD_CORE_URL_MUST_USE_HTTPS");
  });

  it("validates the configured Core service through a lightweight authenticated health request", async () => {
    const health = await probeCoreHealth();
    expect(health.status).toBe("ok");
    expect(health.baseUrl.startsWith("https://")).toBe(true);
  }, 15_000);

  it("proxies only a documented read request and keeps the service key server-side", async () => {
    let requestUrl = "";
    let authorization = "";
    const fakeFetch = async (input: string | URL | Request, init?: RequestInit) => {
      requestUrl = String(input);
      authorization = String((init?.headers as Record<string, string>).Authorization);
      return new Response(JSON.stringify({ symbol: "BTCUSDT", price: 70000 }), { status: 200 });
    };
    const payload = await coreGetPrice("BTCUSDT", fakeFetch as typeof fetch, { CAPITALGUARD_CORE_BASE_URL: "https://core.example", CAPITALGUARD_CORE_API_KEY: "private-service-key" });
    expect(requestUrl).toBe("https://core.example/api/webapp/price?symbol=BTCUSDT");
    expect(authorization).toBe("Bearer private-service-key");
    expect(payload).toMatchObject({ symbol: "BTCUSDT" });
  });

  it("accepts Telegram data only when the Core verifier confirms it", async () => {
    const rejectedFetch = async () => new Response(JSON.stringify({ ok: false, error: "invalid initData" }), { status: 200 });
    await expect(coreVerifyTelegramInitData("auth_date=1&user=%7B%7D", rejectedFetch as typeof fetch, {
      CAPITALGUARD_CORE_BASE_URL: "https://core.example",
      CAPITALGUARD_CORE_API_KEY: "private-service-key",
    })).rejects.toThrow("CAPITALGUARD_TMA_INITDATA_INVALID");

    const acceptedFetch = async () => new Response(JSON.stringify({ ok: true, portfolio: [] }), { status: 200 });
    await expect(coreVerifyTelegramInitData("auth_date=1&user=%7B%7D", acceptedFetch as typeof fetch, {
      CAPITALGUARD_CORE_BASE_URL: "https://core.example",
      CAPITALGUARD_CORE_API_KEY: "private-service-key",
    })).resolves.toMatchObject({ ok: true });
  });

  it("requests a trader read model only through the server-side Core adapter", async () => {
    let requestUrl = "";
    let authorization = "";
    const fakeFetch = async (input: string | URL | Request, init?: RequestInit) => {
      requestUrl = String(input);
      authorization = String((init?.headers as Record<string, string>).Authorization);
      return new Response(JSON.stringify({
        ok: true,
        schema_version: "2026-08-20.1",
        as_of: "2026-08-20T00:00:00Z",
        user: { telegram_id: 123456, role: "TRADER" },
        portfolio: { open_position_count: 0, positions: [] },
        performance: {},
        funnel: {},
      }), { status: 200 });
    };
    const result = await coreGetTraderReadModel(123456, fakeFetch as typeof fetch, {
      CAPITALGUARD_CORE_BASE_URL: "https://core.example",
      CAPITALGUARD_CORE_API_KEY: "private-service-key",
    });
    expect(requestUrl).toBe("https://core.example/api/webapp/read-models/trader/123456");
    expect(authorization).toBe("Bearer private-service-key");
    expect(result.portfolio.open_position_count).toBe(0);
    await expect(coreGetTraderReadModel(0, fakeFetch as typeof fetch)).rejects.toThrow("CAPITALGUARD_TMA_TELEGRAM_ID_REQUIRED");
  });
});

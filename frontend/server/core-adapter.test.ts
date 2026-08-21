import { describe, expect, it } from "vitest";
import { coreGetAnalysts, coreGetOperationsFeed, coreGetPrice, coreGetR5Readiness, coreGetTraderHistorical, coreGetTraderReadModel, coreGetTraderRecommendationDetail, coreGetTraderRecommendations, coreReviewHistoricalBatch, coreVerifyTelegramInitData, getCoreConfig, probeCoreHealth } from "./core-adapter";

describe("CapitalGuard Core adapter", () => {
  it("rejects a missing or insecure Core configuration", () => {
    expect(() => getCoreConfig({})).toThrow("CAPITALGUARD_CORE_NOT_CONFIGURED");
    expect(() => getCoreConfig({ CAPITALGUARD_CORE_BASE_URL: "http://core.local", CAPITALGUARD_CORE_API_KEY: "safe-key" })).toThrow("CAPITALGUARD_CORE_URL_MUST_USE_HTTPS");
  });

  it("validates the configured Core service through a lightweight authenticated health request", async () => {
    let authorization = "";
    const fakeFetch = async (_input: string | URL | Request, init?: RequestInit) => {
      authorization = String((init?.headers as Record<string, string>).Authorization);
      return new Response(JSON.stringify({ status: "ok" }), { status: 200 });
    };
    const health = await probeCoreHealth(fakeFetch as typeof fetch, {
      CAPITALGUARD_CORE_BASE_URL: "https://core.example",
      CAPITALGUARD_CORE_API_KEY: "private-service-key",
    });
    expect(health.status).toBe("ok");
    expect(health.baseUrl).toBe("https://core.example");
    expect(authorization).toBe("Bearer private-service-key");
  });

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

  it("sends owner review commands server-to-server with an idempotency key", async () => {
    let requestUrl = "";
    let requestBody = "";
    const fakeFetch = async (input: string | URL | Request, init?: RequestInit) => {
      requestUrl = String(input);
      requestBody = String(init?.body);
      return new Response(JSON.stringify({ ok: true, batch_id: 9, status: "VALIDATED" }), { status: 200 });
    };
    await coreReviewHistoricalBatch({ actorTelegramId: 123456, batchId: 9, approved: true, note: "Evidence reviewed", idempotencyKey: "command-key-123456" }, fakeFetch as typeof fetch, {
      CAPITALGUARD_CORE_BASE_URL: "https://core.example",
      CAPITALGUARD_CORE_API_KEY: "private-service-key",
    });
    expect(requestUrl).toBe("https://core.example/api/webapp/owner/review-batches");
    expect(JSON.parse(requestBody)).toMatchObject({ actor_telegram_id: 123456, batch_id: 9, approved: true, idempotency_key: "command-key-123456" });
  });

  it("retrieves operations telemetry only through the server-side Core adapter", async () => {
    let requestUrl = "";
    const fakeFetch = async (input: string | URL | Request) => {
      requestUrl = String(input);
      return new Response(JSON.stringify({ ok: true, events: [], summary: { critical: 0, warning: 0, total: 0 } }), { status: 200 });
    };
    const result = await coreGetOperationsFeed(123456, fakeFetch as typeof fetch, { CAPITALGUARD_CORE_BASE_URL: "https://core.example", CAPITALGUARD_CORE_API_KEY: "private-service-key" });
    expect(requestUrl).toBe("https://core.example/api/webapp/owner/operations-feed?actor_telegram_id=123456");
    expect(result.summary.total).toBe(0);
  });

  it("keeps trader and analyst read models behind the Core service adapter", async () => {
    const urls: string[] = [];
    const fakeFetch = async (input: string | URL | Request) => {
      urls.push(String(input));
      return new Response(JSON.stringify({ ok: true, schema_version: "2026-08-21.2", as_of: "2026-08-20T00:00:00Z", items: [] }), { status: 200 });
    };
    const env = { CAPITALGUARD_CORE_BASE_URL: "https://core.example", CAPITALGUARD_CORE_API_KEY: "private-service-key" };
    await coreGetTraderRecommendations(123456, fakeFetch as typeof fetch, env);
    await coreGetTraderHistorical(123456, fakeFetch as typeof fetch, env);
    await coreGetAnalysts(fakeFetch as typeof fetch, env);
    expect(urls).toEqual([
      "https://core.example/api/webapp/read-models/trader/123456/recommendations",
      "https://core.example/api/webapp/read-models/trader/123456/historical",
      "https://core.example/api/webapp/read-models/analysts",
    ]);
  });

  it("retrieves one owned recommendation only through an encoded public reference", async () => {
    let requestUrl = "";
    const fakeFetch = async (input: string | URL | Request) => {
      requestUrl = String(input);
      return new Response(JSON.stringify({ ok: true, schema_version: "2026-08-21.2", as_of: "2026-08-21T00:00:00Z", item: { id: 1, entity_type: "USER_TRADE", public_ref: "USR-000012/T-0003", display_ref: "USR-000012/T-0003", asset: "BTCUSDT", side: "LONG", market: "Futures", entry: 1, stop_loss: 1, targets: [], status: "WATCHLIST", source_type: "TRACKED_RECOMMENDATION", source: null, created_at: null, activated_at: null, closed_at: null, timeline: [] } }), { status: 200 });
    };
    const env = { CAPITALGUARD_CORE_BASE_URL: "https://core.example", CAPITALGUARD_CORE_API_KEY: "private-service-key" };
    const result = await coreGetTraderRecommendationDetail(123456, "USR-000012/T-0003", fakeFetch as typeof fetch, env);
    expect(requestUrl).toBe("https://core.example/api/webapp/read-models/trader/123456/recommendations/USR-000012%2FT-0003");
    expect(result.item.public_ref).toBe("USR-000012/T-0003");
  });

  it("reports R5 as a server-controlled noncommercial hold", async () => {
    const fakeFetch = async () => new Response(JSON.stringify({ ok: true, status: "HOLD", reasons: ["RESTORE_DRILL_DEFERRED"], commercial_enabled: false, copy_trading_enabled: false, execution_controls: { auto_trade_enabled: false, trade_live_enabled: false }, observation: { started_at: null, required_hours: 168, elapsed_hours: 0, remaining_hours: 168, complete: false }, snapshot: { outbox_backlog: 0, owner_review_backlog: 0, replay_backlog: 0 }, as_of: "2026-08-21T00:00:00Z" }), { status: 200 });
    const result = await coreGetR5Readiness(123456, fakeFetch as typeof fetch, { CAPITALGUARD_CORE_BASE_URL: "https://core.example", CAPITALGUARD_CORE_API_KEY: "private-service-key" });
    expect(result.status).toBe("HOLD");
    expect(result.commercial_enabled).toBe(false);
    expect(result.copy_trading_enabled).toBe(false);
    expect(result.execution_controls).toEqual({ auto_trade_enabled: false, trade_live_enabled: false });
    expect(result.observation).toMatchObject({ required_hours: 168, complete: false });
  });
});

import { afterEach, describe, expect, it, vi } from "vitest";
import { telegramIdFromWebSession } from "./capitalguard";
import { appRouter } from "./routers";
import type { TrpcContext } from "./_core/context";

function coreContext(): TrpcContext {
  return {
    user: { id: 101, openId: "core-route-test", email: "test@example.com", name: "Core Route Test", loginMethod: "manus", role: "admin", createdAt: new Date(), updatedAt: new Date(), lastSignedIn: new Date() },
    req: { protocol: "https", headers: {} } as TrpcContext["req"],
    res: { clearCookie: () => undefined } as TrpcContext["res"],
  };
}

describe("CapitalGuard Core tRPC routes", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("proxies Core health only through the authenticated server route", async () => {
    vi.stubGlobal("fetch", async () => new Response(JSON.stringify({ status: "ok" }), { status: 200 }));
    const caller = appRouter.createCaller(coreContext());
    await expect(caller.capitalguard.core.health()).resolves.toMatchObject({ status: "ok" });
  });

  it("rejects malformed symbols before any request is sent to Core", async () => {
    const caller = appRouter.createCaller(coreContext());
    await expect(caller.capitalguard.core.price({ symbol: "BTC/USDT" })).rejects.toMatchObject({ code: "BAD_REQUEST" });
  });

  it("derives the Core identity only from a signed Telegram Web session", () => {
    expect(telegramIdFromWebSession("telegram:123456")).toBe(123456);
    expect(() => telegramIdFromWebSession("core-route-test")).toThrow("CAPITALGUARD_TMA_SESSION_REQUIRED");
  });

  it("derives the UserTrade close actor from the Telegram session and forwards no client actor field", async () => {
    let requestUrl = "";
    let requestBody = "";
    vi.stubGlobal("fetch", async (input: string | URL | Request, init?: RequestInit) => {
      requestUrl = String(input);
      requestBody = String(init?.body);
      return new Response(JSON.stringify({ ok: true, entity_type: "USER_TRADE", public_ref: "USR-000012/T-0003", status: "CLOSED", close_price: 70123.45, replayed: false }), { status: 200 });
    });
    const caller = appRouter.createCaller({ ...coreContext(), user: { ...coreContext().user!, openId: "telegram:123456" } });
    await expect(caller.capitalguard.trader.closeUserTrade({ publicRef: "USR-000012/T-0003", idempotencyKey: "close-command-key-0001" })).resolves.toMatchObject({ status: "CLOSED" });
    expect(requestUrl).toContain("/read-models/trader/123456/recommendations/USR-000012%2FT-0003/commands/close");
    expect(JSON.parse(requestBody)).toEqual({ actor_telegram_id: 123456, idempotency_key: "close-command-key-0001" });
  });
});

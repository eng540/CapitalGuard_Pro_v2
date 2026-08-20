import { describe, expect, it } from "vitest";
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
  it("proxies Core health only through the authenticated server route", async () => {
    const caller = appRouter.createCaller(coreContext());
    await expect(caller.capitalguard.core.health()).resolves.toMatchObject({ status: "ok" });
  }, 15_000);

  it("rejects malformed symbols before any request is sent to Core", async () => {
    const caller = appRouter.createCaller(coreContext());
    await expect(caller.capitalguard.core.price({ symbol: "BTC/USDT" })).rejects.toMatchObject({ code: "BAD_REQUEST" });
  });

  it("derives the Core identity only from a signed Telegram Web session", () => {
    expect(telegramIdFromWebSession("telegram:123456")).toBe(123456);
    expect(() => telegramIdFromWebSession("core-route-test")).toThrow("CAPITALGUARD_TMA_SESSION_REQUIRED");
  });
});

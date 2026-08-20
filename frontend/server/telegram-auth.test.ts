import { describe, expect, it } from "vitest";
import { authenticateTelegramInitData, parseFreshTelegramInitData } from "./telegram-auth";

const NOW_MS = 1_780_000_000_000;

function initData(overrides: Record<string, string> = {}) {
  const params = new URLSearchParams({
    user: JSON.stringify({ id: 123456, first_name: "Capital", last_name: "Guard", username: "capitalguard" }),
    auth_date: String(Math.floor(NOW_MS / 1000) - 30),
    hash: "telegram-signature-validated-by-core",
    ...overrides,
  });
  return params.toString();
}

describe("Telegram-first authentication", () => {
  it("creates a Web identity only after Core confirms a fresh Telegram payload", async () => {
    const identity = await authenticateTelegramInitData(initData(), async () => ({ ok: true }), NOW_MS);
    expect(identity).toEqual({
      openId: "telegram:123456",
      name: "Capital Guard",
      loginMethod: "telegram_mini_app",
    });
  });

  it("rejects a signature rejected by Core before creating an identity", async () => {
    await expect(
      authenticateTelegramInitData(initData(), async () => {
        throw new Error("CAPITALGUARD_TMA_INITDATA_INVALID");
      }, NOW_MS),
    ).rejects.toThrow("CAPITALGUARD_TMA_INITDATA_INVALID");
  });

  it("rejects expired, future-dated, and malformed Telegram data", () => {
    expect(() => parseFreshTelegramInitData(initData({ auth_date: String(Math.floor(NOW_MS / 1000) - 301) }), NOW_MS)).toThrow("TELEGRAM_AUTH_EXPIRED");
    expect(() => parseFreshTelegramInitData(initData({ auth_date: String(Math.floor(NOW_MS / 1000) + 61) }), NOW_MS)).toThrow("TELEGRAM_AUTH_INVALID");
    expect(() => parseFreshTelegramInitData(initData({ user: "not-json" }), NOW_MS)).toThrow("TELEGRAM_AUTH_INVALID");
  });
});

import { describe, expect, it } from "vitest";
import { getTelegramWebApp } from "../client/src/lib/tma";

describe("Telegram Mini App bridge", () => {
  it("is safe when CapitalGuard opens as a standalone website", () => {
    expect(getTelegramWebApp()).toBeUndefined();
  });
});

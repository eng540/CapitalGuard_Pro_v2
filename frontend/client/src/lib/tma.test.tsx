import { describe, expect, it } from "vitest";
import { applyTelegramViewport, getTelegramWebApp } from "./tma";

describe("getTelegramWebApp", () => {
  it("is safe when the platform runs as a standalone website", () => {
    expect(getTelegramWebApp()).toBeUndefined();
  });
});

describe("applyTelegramViewport", () => {
  it("maps optional safe-area and theme data without requiring Telegram-only APIs", () => {
    applyTelegramViewport({ colorScheme: "dark", contentSafeAreaInset: { bottom: 24 }, themeParams: { bg_color: "#101820" } });
    expect(document.documentElement.style.getPropertyValue("--tma-safe-bottom")).toBe("24px");
    expect(document.documentElement.dataset.tmaTheme).toBe("dark");
  });
});

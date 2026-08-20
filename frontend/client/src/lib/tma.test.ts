import { describe, expect, it } from "vitest";
import { getTelegramWebApp } from "./tma";

describe("getTelegramWebApp", () => {
  it("is safe when the platform runs as a standalone website", () => {
    expect(getTelegramWebApp()).toBeUndefined();
  });
});

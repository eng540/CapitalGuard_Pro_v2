import { describe, expect, it } from "vitest";
import { analystValidationMessage } from "./analyst-validation-message";

describe("analystValidationMessage", () => {
  it("maps raw entry validation without exposing the payload", () => {
    const result = analystValidationMessage('[{"origin":"number","code":"too_small","path":["entry"]}]');
    expect(result.field).toBe("entry");
    expect(result.message).toContain("Limit");
    expect(result.message).not.toContain("origin");
    expect(result.message).not.toContain("path");
  });

  it("maps service failures to a safe retry message", () => {
    const result = analystValidationMessage("CAPITALGUARD_CORE_TIMEOUT");
    expect(result.field).toBe("service");
    expect(result.message).toContain("لم تُنشأ توصية");
  });
});

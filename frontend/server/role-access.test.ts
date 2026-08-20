import { describe, expect, it } from "vitest";
import { canAccessCapitalGuardRole } from "./_core/trpc";

describe("CapitalGuard role boundaries", () => {
  it("allows a trader into trader-only data while blocking analyst-only data", () => {
    expect(canAccessCapitalGuardRole("trader", ["trader"])).toBe(true);
    expect(canAccessCapitalGuardRole("trader", ["analyst"])).toBe(false);
  });

  it("allows an analyst into analyst data but not trader-only data", () => {
    expect(canAccessCapitalGuardRole("analyst", ["analyst"])).toBe(true);
    expect(canAccessCapitalGuardRole("analyst", ["trader"])).toBe(false);
  });

  it("allows an administrator through every explicit CapitalGuard boundary", () => {
    expect(canAccessCapitalGuardRole("admin", ["trader"])).toBe(true);
    expect(canAccessCapitalGuardRole("admin", ["analyst"])).toBe(true);
    expect(canAccessCapitalGuardRole("admin", ["admin"])).toBe(true);
  });
});

import { describe, expect, it } from "vitest";
import { buildAnalystComparison, calculateRiskPlan } from "./capitalguard";

describe("calculateRiskPlan", () => {
  it("calculates a bounded long position from capital and stop distance", () => {
    const result = calculateRiskPlan({ capital: 10_000, riskPercent: 1, entry: 70_000, stop: 69_500, side: "long" });
    expect(result).toEqual({ valid: true, reason: "RISK_PLAN_READY", riskAmount: 100, quantity: 0.2, notional: 14000 });
  });

  it("rejects a stop that invalidates short-side risk direction", () => {
    const result = calculateRiskPlan({ capital: 10_000, riskPercent: 1, entry: 70_000, stop: 69_500, side: "short" });
    expect(result.valid).toBe(false);
    expect(result.reason).toBe("STOP_DIRECTION_INVALID");
  });
});

describe("buildAnalystComparison", () => {
  it("ranks by PnL while surfacing sample confidence", () => {
    const comparison = buildAnalystComparison([
      { analystCode: "AN-2", winRate: 72, totalPnlPct: 12, maxDrawdownPct: -8, sampleSize: 18 },
      { analystCode: "AN-1", winRate: 63, totalPnlPct: 19, maxDrawdownPct: -6, sampleSize: 74 },
    ]);
    expect(comparison.leader?.analystCode).toBe("AN-1");
    expect(comparison.confidence).toBe("SUFFICIENT_SAMPLE");
  });
});

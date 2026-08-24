import { describe, expect, it } from "vitest";
import { buildAnalystComparison, calculateRiskPlan, compareSelectedAnalysts } from "./capitalguard";
import type { CoreAnalystReadModel } from "./core-adapter";

describe("calculateRiskPlan", () => {
  it("calculates a bounded long position from capital and stop distance", () => {
    const result = calculateRiskPlan({ capital: 10_000, riskPercent: 1, entry: 70_000, stop: 69_500, side: "long" });
    expect(result).toMatchObject({ valid: true, reason: "RISK_PLAN_READY", riskAmount: 100, quantity: 0.2, notional: 14000, leverage: 1, marginRequired: 14000 });
  });

  it("uses an explicit risk amount when supplied", () => {
    const result = calculateRiskPlan({ capital: 10_000, riskPercent: 1, riskAmount: 50, entry: 70_000, stop: 69_500, side: "long" });
    expect(result.riskAmount).toBe(50);
    expect(result.quantity).toBe(0.1);
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

  it("compares only requested Core analysts and rejects an unknown analyst", () => {
    const analyst = (code: string, totalPnlPct: number): CoreAnalystReadModel => ({
      analyst_code: code,
      public_ref: null,
      public_name: code,
      sample_size: 40,
      win_rate_pct: 65,
      total_pnl_pct: totalPnlPct,
      max_drawdown_pct: 4,
      active_recommendations: 1,
      risk_exposure_pct: 2,
      eligible_for_ranking: true,
      freshness_days: 1,
    });
    const rows = [analyst("AN-1", 8), analyst("AN-2", 15), analyst("AN-3", 100)];

    const comparison = compareSelectedAnalysts(rows, ["AN-1", "AN-2"]);

    expect(comparison.rows.map(row => row.analystCode)).toEqual(["AN-2", "AN-1"]);
    expect(comparison.leader?.analystCode).toBe("AN-2");
    expect(() => compareSelectedAnalysts(rows, ["AN-1", "AN-9"])).toThrow("ANALYST_NOT_FOUND");
  });
});

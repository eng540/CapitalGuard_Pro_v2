import { describe, expect, it } from "vitest";
import { validateSmartAnalysis } from "./smart-analysis";

describe("validateSmartAnalysis", () => {
  it("keeps Smart Dropzone constrained to an explanatory extraction contract", () => {
    const result = validateSmartAnalysis({
      classification: "INITIAL_SIGNAL",
      asset: "BTCUSDT",
      side: "LONG",
      entry: 70000,
      stopLoss: 69500,
      targets: [71000],
      confidence: 0.82,
      temporalHint: "LIVE_REVIEW",
      explanation: "Entry and stop were extracted.",
      safetyNotice: "Review before any manual action.",
    });
    expect(result.asset).toBe("BTCUSDT");
    expect(result.temporalHint).toBe("LIVE_REVIEW");
  });

  it("rejects an unbounded confidence claim", () => {
    expect(() => validateSmartAnalysis({
      classification: "INITIAL_SIGNAL", asset: "BTCUSDT", side: "LONG", entry: 70000, stopLoss: 69500,
      targets: [], confidence: 2, temporalHint: "LIVE_REVIEW", explanation: "x", safetyNotice: "x",
    })).toThrow();
  });
});

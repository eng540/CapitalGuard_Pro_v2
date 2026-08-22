import { describe, expect, it } from "vitest";
import { getAnalystTradeFlow } from "./analyst-trade-flow";

describe("analyst trade flow", () => {
  it("restricts Spot to Long", () => {
    expect(getAnalystTradeFlow("Spot", "LIMIT").allowedSides).toEqual(["LONG"]);
  });

  it("allows both Futures directions", () => {
    expect(getAnalystTradeFlow("Futures", "LIMIT").allowedSides).toEqual(["LONG", "SHORT"]);
  });

  it("uses Core live price for Market but demands an entry for Limit and Stop Market", () => {
    expect(getAnalystTradeFlow("Futures", "MARKET")).toMatchObject({ manualEntryRequired: false, entryMode: "CORE_LIVE_PRICE" });
    expect(getAnalystTradeFlow("Futures", "LIMIT")).toMatchObject({ manualEntryRequired: true, entryMode: "LIMIT_PRICE", entryLabel: "سعر الدخول" });
    expect(getAnalystTradeFlow("Futures", "STOP_MARKET")).toMatchObject({ manualEntryRequired: true, entryMode: "STOP_TRIGGER", entryLabel: "سعر التفعيل" });
  });
});

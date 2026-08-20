import { describe, expect, it } from "vitest";
import * as webSchema from "../drizzle/schema";

describe("A-PG Web data boundary", () => {
  it("contains only Web-owned persistence tables and no financial Core tables", () => {
    expect(Object.keys(webSchema)).toEqual(expect.arrayContaining(["users", "webPreferences", "webSavedComparisons", "webNotificationPreferences", "webAuditEvents"]));
    expect(Object.keys(webSchema)).not.toEqual(expect.arrayContaining(["recommendations", "trades", "portfolios", "historicalBatches", "temporalDecisions"]));
  });
});

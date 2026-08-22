import { describe, expect, it } from "vitest";
import { normalizeFinancialNumber, normalizeSymbol } from "./financial-input";

describe("financial input normalization", () => {
  it("normalizes Arabic and Eastern Arabic digits", () => {
    expect(normalizeFinancialNumber("١٢٦٨")) .toBe(1268);
    expect(normalizeFinancialNumber("۱۲۶۸")) .toBe(1268);
  });

  it("normalizes Arabic punctuation and market suffixes", () => {
    expect(normalizeFinancialNumber("٧٧،٥٠٠")) .toBe(77500);
    expect(normalizeFinancialNumber("77K")) .toBe(77000);
    expect(normalizeFinancialNumber("1.5M")) .toBe(1500000);
  });

  it("rejects malformed prices and normalizes symbols", () => {
    expect(Number.isNaN(normalizeFinancialNumber("seventy-seven"))).toBe(true);
    expect(normalizeSymbol(" #btc/usdt ")).toBe("BTCUSDT");
  });
});

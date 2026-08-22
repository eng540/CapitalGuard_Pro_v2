import { describe, expect, it } from "vitest";
import { userTradeEventCopy } from "./user-trade-event-copy";

describe("userTradeEventCopy", () => {
  it("labels manual lifecycle actions in Arabic", () => {
    expect(userTradeEventCopy("MANUAL_PARTIAL_CLOSE")).toMatchObject({ label: "إغلاق جزئي", source: "يدوي" });
    expect(userTradeEventCopy("MANUAL_STOP_MOVED_TO_BREAKEVEN")).toMatchObject({ label: "نقل الوقف إلى التعادل", source: "يدوي" });
    expect(userTradeEventCopy("MANUAL_PENDING_ENTRY_UPDATED")).toMatchObject({ label: "تعديل سعر الدخول", source: "يدوي" });
    expect(userTradeEventCopy("PENDING_CANCELLED")).toMatchObject({ label: "إلغاء سجل معلّق", source: "يدوي" });
  });

  it("labels automatic targets and retains an explicit fallback", () => {
    expect(userTradeEventCopy("TP2")).toMatchObject({ label: "تحقق TP2", source: "آلي" });
    expect(userTradeEventCopy("UNKNOWN_EVENT")).toMatchObject({ source: "نظام" });
  });
});

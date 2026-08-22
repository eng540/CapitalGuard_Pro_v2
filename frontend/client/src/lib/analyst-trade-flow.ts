export type AnalystMarket = "Spot" | "Futures";
export type AnalystSide = "LONG" | "SHORT";
export type AnalystOrderType = "MARKET" | "LIMIT" | "STOP_MARKET";

export function getAnalystTradeFlow(market: AnalystMarket, orderType: AnalystOrderType) {
  const isMarket = orderType === "MARKET";
  const allowedSides: AnalystSide[] = market === "Spot" ? ["LONG"] : ["LONG", "SHORT"];
  return {
    allowedSides,
    manualEntryRequired: !isMarket,
    entryLabel: orderType === "STOP_MARKET" ? "سعر التفعيل" : "سعر الدخول",
    entryMode: isMarket ? "CORE_LIVE_PRICE" : orderType === "STOP_MARKET" ? "STOP_TRIGGER" : "LIMIT_PRICE",
  };
}

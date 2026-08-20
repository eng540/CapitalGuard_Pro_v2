export const previewPortfolio = {
  totalEquity: 24860.42,
  availableBalance: 17190.18,
  realizedPnl: 1240.61,
  unrealizedPnl: 184.32,
  currency: "USDT",
};

export const previewTrades = [
  { id: "TR-000184", asset: "BTCUSDT", side: "long", status: "active", entry: "68,954.00", pnl: "+1.84%", source: "AN-000001/R-000012" },
  { id: "TR-000181", asset: "SOLUSDT", side: "long", status: "partial", entry: "143.60", pnl: "+4.12%", source: "Manual Log" },
  { id: "TR-000176", asset: "ETHUSDT", side: "short", status: "closed", entry: "3,482.00", pnl: "+0.92%", source: "CH-000002" },
];

export const previewRecommendations = [
  { ref: "AN-000001/R-000012", asset: "BTCUSDT", side: "long", status: "active", entry: "69,158.00", stop: "69,000.00", targets: "70,000 · 72,000", source: "Crypto Radar" },
  { ref: "AN-000003/R-000077", asset: "SOLUSDT", side: "long", status: "pending", entry: "90.00", stop: "88.00", targets: "91.00 · 95.00", source: "CryptoTerraNet" },
  { ref: "AN-000002/R-000041", asset: "BTCUSDT", side: "short", status: "closed", entry: "68,655.60", stop: "69,000.00", targets: "68,500 · 68,400", source: "Crypto Radar" },
];

export const previewAnalysts = [
  { code: "AN-000001", name: "Crypto Radar", channel: "CH-000001", trust: "verified", winRate: "63.8%", pnl: "+18.42%", drawdown: "-6.11%", sample: 76 },
  { code: "AN-000002", name: "CryptoTerraNet", channel: "CH-000002", trust: "canonical", winRate: "58.2%", pnl: "+12.74%", drawdown: "-8.33%", sample: 52 },
  { code: "AN-000003", name: "BTC-ADNAN", channel: "SH-000004", trust: "unclaimed", winRate: "—", pnl: "Pending", drawdown: "—", sample: 0 },
];

export const previewBatches = [
  { ref: "HB-000015", source: "Crypto Radar", status: "replay_pending", accepted: 1, rejected: 0, temporal: "LIVE_STALE", outcome: "CONSISTENT", gate: "REPLAY_PENDING" },
  { ref: "HB-000013", source: "Crypto Radar", status: "review_required", accepted: 1, rejected: 0, temporal: "CLOSED_EVENT", outcome: "MISMATCH", gate: "OWNER_REVIEW_REQUIRED" },
  { ref: "HB-000010", source: "Crypto - BTC-ADNAN", status: "staged", accepted: 1, rejected: 0, temporal: "HISTORICAL_RECONSTRUCTION", outcome: "NOT_PARSED", gate: "PARSER_PENDING" },
];

export const pnlSeries = [
  { day: "السبت", pnl: 102 },
  { day: "الأحد", pnl: -42 },
  { day: "الإثنين", pnl: 185 },
  { day: "الثلاثاء", pnl: 238 },
  { day: "الأربعاء", pnl: 150 },
  { day: "الخميس", pnl: 318 },
  { day: "الجمعة", pnl: 227 },
];

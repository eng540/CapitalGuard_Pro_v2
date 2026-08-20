import {
  decimal,
  index,
  int,
  json,
  mysqlEnum,
  mysqlTable,
  text,
  timestamp,
  uniqueIndex,
  varchar,
} from "drizzle-orm/mysql-core";

export const userRoles = ["user", "trader", "analyst", "admin"] as const;
export const marketSides = ["long", "short"] as const;
export const tradeStatuses = ["pending", "active", "partial", "closed", "cancelled"] as const;
export const batchStatuses = ["staged", "review_required", "validated", "evidence_ingested", "replay_pending", "replayed", "rejected"] as const;
export const sourceTrusts = ["canonical", "unclaimed", "claimed", "verified"] as const;
export const historicalOwnerKinds = ["trader_follow", "analyst", "channel"] as const;

export const users = mysqlTable("users", {
  id: int("id").autoincrement().primaryKey(),
  openId: varchar("openId", { length: 64 }).notNull().unique(),
  name: text("name"),
  email: varchar("email", { length: 320 }),
  loginMethod: varchar("loginMethod", { length: 64 }),
  role: mysqlEnum("role", userRoles).default("trader").notNull(),
  createdAt: timestamp("createdAt").defaultNow().notNull(),
  updatedAt: timestamp("updatedAt").defaultNow().onUpdateNow().notNull(),
  lastSignedIn: timestamp("lastSignedIn").defaultNow().notNull(),
});

export const portfolios = mysqlTable("portfolios", {
  id: int("id").autoincrement().primaryKey(),
  userId: int("userId").notNull().unique(),
  currency: varchar("currency", { length: 8 }).notNull().default("USDT"),
  totalEquity: decimal("totalEquity", { precision: 20, scale: 8 }).notNull().default("0"),
  availableBalance: decimal("availableBalance", { precision: 20, scale: 8 }).notNull().default("0"),
  realizedPnl: decimal("realizedPnl", { precision: 20, scale: 8 }).notNull().default("0"),
  unrealizedPnl: decimal("unrealizedPnl", { precision: 20, scale: 8 }).notNull().default("0"),
  updatedAt: timestamp("updatedAt").defaultNow().onUpdateNow().notNull(),
});

export const channels = mysqlTable(
  "channels",
  {
    id: int("id").autoincrement().primaryKey(),
    channelCode: varchar("channelCode", { length: 32 }).notNull(),
    displayName: varchar("displayName", { length: 128 }).notNull(),
    telegramChannelId: varchar("telegramChannelId", { length: 64 }),
    trust: mysqlEnum("trust", sourceTrusts).notNull().default("unclaimed"),
    ownerId: int("ownerId"),
    createdAt: timestamp("createdAt").defaultNow().notNull(),
    updatedAt: timestamp("updatedAt").defaultNow().onUpdateNow().notNull(),
  },
  table => [uniqueIndex("channels_code_unique").on(table.channelCode)]
);

export const analystProfiles = mysqlTable(
  "analystProfiles",
  {
    id: int("id").autoincrement().primaryKey(),
    userId: int("userId").notNull().unique(),
    analystCode: varchar("analystCode", { length: 32 }).notNull(),
    headline: varchar("headline", { length: 160 }),
    winRate: decimal("winRate", { precision: 7, scale: 4 }).notNull().default("0"),
    totalPnlPct: decimal("totalPnlPct", { precision: 12, scale: 4 }).notNull().default("0"),
    maxDrawdownPct: decimal("maxDrawdownPct", { precision: 12, scale: 4 }).notNull().default("0"),
    sampleSize: int("sampleSize").notNull().default(0),
    verifiedAt: timestamp("verifiedAt"),
    updatedAt: timestamp("updatedAt").defaultNow().onUpdateNow().notNull(),
  },
  table => [uniqueIndex("analyst_profiles_code_unique").on(table.analystCode)]
);

export const recommendations = mysqlTable(
  "recommendations",
  {
    id: int("id").autoincrement().primaryKey(),
    publicRef: varchar("publicRef", { length: 64 }).notNull(),
    analystId: int("analystId"),
    channelId: int("channelId"),
    asset: varchar("asset", { length: 32 }).notNull(),
    side: mysqlEnum("side", marketSides).notNull(),
    status: mysqlEnum("status", tradeStatuses).notNull().default("pending"),
    entry: decimal("entry", { precision: 24, scale: 10 }),
    stopLoss: decimal("stopLoss", { precision: 24, scale: 10 }),
    targets: json("targets"),
    finalPnlPct: decimal("finalPnlPct", { precision: 12, scale: 4 }),
    temporalDecision: varchar("temporalDecision", { length: 64 }),
    createdAt: timestamp("createdAt").defaultNow().notNull(),
    activatedAt: timestamp("activatedAt"),
    closedAt: timestamp("closedAt"),
  },
  table => [uniqueIndex("recommendations_public_ref_unique").on(table.publicRef), index("recommendations_analyst_index").on(table.analystId)]
);

export const trades = mysqlTable(
  "trades",
  {
    id: int("id").autoincrement().primaryKey(),
    publicRef: varchar("publicRef", { length: 64 }).notNull(),
    userId: int("userId").notNull(),
    recommendationId: int("recommendationId"),
    asset: varchar("asset", { length: 32 }).notNull(),
    side: mysqlEnum("side", marketSides).notNull(),
    status: mysqlEnum("status", tradeStatuses).notNull().default("pending"),
    sourceType: varchar("sourceType", { length: 24 }).notNull().default("manual"),
    entry: decimal("entry", { precision: 24, scale: 10 }),
    stopLoss: decimal("stopLoss", { precision: 24, scale: 10 }),
    size: decimal("size", { precision: 24, scale: 10 }),
    realizedPnl: decimal("realizedPnl", { precision: 20, scale: 8 }).notNull().default("0"),
    createdAt: timestamp("createdAt").defaultNow().notNull(),
    closedAt: timestamp("closedAt"),
  },
  table => [uniqueIndex("trades_public_ref_unique").on(table.publicRef), index("trades_user_status_index").on(table.userId, table.status)]
);

export const historicalBatches = mysqlTable(
  "historicalBatches",
  {
    id: int("id").autoincrement().primaryKey(),
    publicRef: varchar("publicRef", { length: 64 }).notNull(),
    requestedByUserId: int("requestedByUserId").notNull(),
    channelId: int("channelId"),
    status: mysqlEnum("status", batchStatuses).notNull().default("staged"),
    acceptedRecords: int("acceptedRecords").notNull().default(0),
    rejectedRecords: int("rejectedRecords").notNull().default(0),
    temporalMode: varchar("temporalMode", { length: 64 }),
    financialOutcome: varchar("financialOutcome", { length: 64 }),
    replayGate: varchar("replayGate", { length: 64 }),
    ownerReview: varchar("ownerReview", { length: 64 }),
    createdAt: timestamp("createdAt").defaultNow().notNull(),
    reviewedAt: timestamp("reviewedAt"),
  },
  table => [uniqueIndex("historical_batches_public_ref_unique").on(table.publicRef), index("historical_batches_status_index").on(table.status)]
);

export const temporalDecisions = mysqlTable(
  "temporalDecisions",
  {
    id: int("id").autoincrement().primaryKey(),
    batchId: int("batchId"),
    sourceRef: varchar("sourceRef", { length: 128 }).notNull(),
    mode: varchar("mode", { length: 64 }).notNull(),
    route: varchar("route", { length: 64 }).notNull(),
    reasons: json("reasons"),
    ageSeconds: int("ageSeconds"),
    marketAsOf: timestamp("marketAsOf"),
    createdAt: timestamp("createdAt").defaultNow().notNull(),
  },
  table => [index("temporal_decisions_batch_index").on(table.batchId)]
);

export const historicalWallets = mysqlTable(
  "historicalWallets",
  {
    id: int("id").autoincrement().primaryKey(),
    ownerId: int("ownerId"),
    channelId: int("channelId"),
    ownerKind: mysqlEnum("ownerKind", historicalOwnerKinds).notNull(),
    publicRef: varchar("publicRef", { length: 64 }).notNull(),
    totalSignals: int("totalSignals").notNull().default(0),
    replayedSignals: int("replayedSignals").notNull().default(0),
    verifiedPnlPct: decimal("verifiedPnlPct", { precision: 12, scale: 4 }).notNull().default("0"),
    maxDrawdownPct: decimal("maxDrawdownPct", { precision: 12, scale: 4 }).notNull().default("0"),
    updatedAt: timestamp("updatedAt").defaultNow().onUpdateNow().notNull(),
  },
  table => [uniqueIndex("historical_wallets_public_ref_unique").on(table.publicRef), index("historical_wallets_owner_index").on(table.ownerId, table.ownerKind)]
);

export type User = typeof users.$inferSelect;
export type InsertUser = typeof users.$inferInsert;

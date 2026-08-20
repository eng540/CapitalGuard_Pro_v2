import { and, desc, eq, inArray } from "drizzle-orm";
import { z } from "zod";
import {
  analystProfiles,
  channels,
  historicalBatches,
  historicalWallets,
  portfolios,
  recommendations,
  trades,
  users,
} from "../drizzle/schema";
import { getDb } from "./db";
import { adminProcedure, analystProcedure, protectedProcedure, router, traderProcedure } from "./_core/trpc";
import { analyzeForwardText, smartAnalysisInput } from "./smart-analysis";
import { coreGetPrice, coreGetSignal, coreGetTmaPortfolio, probeCoreHealth } from "./core-adapter";

const riskInput = z.object({
  capital: z.number().positive(),
  riskPercent: z.number().positive().max(10),
  entry: z.number().positive(),
  stop: z.number().positive(),
  side: z.enum(["long", "short"]),
});

export function calculateRiskPlan(input: z.infer<typeof riskInput>) {
  const riskAmount = input.capital * (input.riskPercent / 100);
  const priceRisk = Math.abs(input.entry - input.stop);
  const validDirection = input.side === "long" ? input.stop < input.entry : input.stop > input.entry;
  if (!validDirection || priceRisk === 0) {
    return { valid: false, reason: "STOP_DIRECTION_INVALID", riskAmount: 0, quantity: 0, notional: 0 };
  }
  const quantity = riskAmount / priceRisk;
  return {
    valid: true,
    reason: "RISK_PLAN_READY",
    riskAmount: Number(riskAmount.toFixed(2)),
    quantity: Number(quantity.toFixed(6)),
    notional: Number((quantity * input.entry).toFixed(2)),
  };
}

export type AnalystComparisonRow = { analystCode: string; winRate: number; totalPnlPct: number; maxDrawdownPct: number; sampleSize: number };
export function buildAnalystComparison(rows: AnalystComparisonRow[]) {
  const ranked = [...rows].sort((a, b) => b.totalPnlPct - a.totalPnlPct || b.winRate - a.winRate);
  const leader = ranked[0] ?? null;
  return { leader, rows: ranked, confidence: leader && leader.sampleSize >= 30 ? "SUFFICIENT_SAMPLE" : "LOW_SAMPLE" };
}

async function workspaceSnapshot(userId: number, role: string) {
  const db = await getDb();
  if (!db) return { connection: "pending", portfolio: null, trades: [], recommendations: [], analyst: null, historical: [] };
  const [portfolio] = await db.select().from(portfolios).where(eq(portfolios.userId, userId)).limit(1);
  const recentTrades = await db.select().from(trades).where(eq(trades.userId, userId)).orderBy(desc(trades.createdAt)).limit(8);
  const recentRecommendations = await db.select().from(recommendations).orderBy(desc(recommendations.createdAt)).limit(8);
  const [analyst] = role === "analyst" || role === "admin" ? await db.select().from(analystProfiles).where(eq(analystProfiles.userId, userId)).limit(1) : [];
  const batches = role === "admin"
    ? await db.select().from(historicalBatches).orderBy(desc(historicalBatches.createdAt)).limit(8)
    : await db.select().from(historicalBatches).where(eq(historicalBatches.requestedByUserId, userId)).orderBy(desc(historicalBatches.createdAt)).limit(8);
  return { connection: "ready", portfolio: portfolio ?? null, trades: recentTrades, recommendations: recentRecommendations, analyst: analyst ?? null, historical: batches };
}

export const capitalguardRouter = router({
  workspace: protectedProcedure.query(async ({ ctx }) => workspaceSnapshot(ctx.user.id, ctx.user.role)),
  recommendations: protectedProcedure.query(async () => {
    const db = await getDb();
    return db ? db.select().from(recommendations).orderBy(desc(recommendations.createdAt)).limit(40) : [];
  }),
  discoverAnalysts: protectedProcedure.query(async () => {
    const db = await getDb();
    if (!db) return [];
    return db.select({ profile: analystProfiles, user: users }).from(analystProfiles).leftJoin(users, eq(analystProfiles.userId, users.id)).orderBy(desc(analystProfiles.winRate)).limit(40);
  }),
  compareAnalysts: protectedProcedure.input(z.object({ codes: z.array(z.string().min(1)).min(2).max(3) })).query(async ({ input }) => {
    const db = await getDb();
    if (!db) return { leader: null, rows: [], confidence: "DATA_PENDING" };
    const profiles = await db.select().from(analystProfiles).where(inArray(analystProfiles.analystCode, input.codes));
    return buildAnalystComparison(profiles.map(profile => ({ analystCode: profile.analystCode, winRate: Number(profile.winRate), totalPnlPct: Number(profile.totalPnlPct), maxDrawdownPct: Number(profile.maxDrawdownPct), sampleSize: profile.sampleSize })));
  }),
  historicalBatches: protectedProcedure.query(async ({ ctx }) => {
    const db = await getDb();
    if (!db) return [];
    return ctx.user.role === "admin"
      ? db.select().from(historicalBatches).orderBy(desc(historicalBatches.createdAt)).limit(40)
      : db.select().from(historicalBatches).where(eq(historicalBatches.requestedByUserId, ctx.user.id)).orderBy(desc(historicalBatches.createdAt)).limit(40);
  }),
  historicalWallet: protectedProcedure.query(async ({ ctx }) => {
    const db = await getDb();
    if (!db) return [];
    return db.select().from(historicalWallets).where(eq(historicalWallets.ownerId, ctx.user.id)).orderBy(desc(historicalWallets.updatedAt));
  }),
  smartAnalyze: protectedProcedure.input(smartAnalysisInput).mutation(async ({ input }) => analyzeForwardText(input.text)),
  core: router({
    health: protectedProcedure.query(() => probeCoreHealth()),
    price: protectedProcedure.input(z.object({ symbol: z.string().trim().min(3).max(24).regex(/^[A-Z0-9]+$/) })).query(({ input }) => coreGetPrice(input.symbol)),
    signal: protectedProcedure.input(z.object({ recId: z.number().int().positive() })).query(({ input }) => coreGetSignal(input.recId)),
    tmaPortfolio: protectedProcedure.input(z.object({ initData: z.string().trim().min(20).max(10_000) })).query(({ input }) => coreGetTmaPortfolio(input.initData)),
  }),
  riskPlan: protectedProcedure.input(riskInput).mutation(({ input }) => calculateRiskPlan(input)),
  trader: router({
    portfolio: traderProcedure.query(async ({ ctx }) => workspaceSnapshot(ctx.user.id, ctx.user.role)),
  }),
  analyst: router({
    dashboard: analystProcedure.query(async ({ ctx }) => {
      const db = await getDb();
      if (!db) return { profile: null, recommendations: [] };
      const [profile] = await db.select().from(analystProfiles).where(eq(analystProfiles.userId, ctx.user.id)).limit(1);
      const analystRecommendations = profile ? await db.select().from(recommendations).where(eq(recommendations.analystId, profile.id)).orderBy(desc(recommendations.createdAt)).limit(40) : [];
      return { profile: profile ?? null, recommendations: analystRecommendations };
    }),
  }),
  admin: router({
    overview: adminProcedure.query(async () => {
      const db = await getDb();
      if (!db) return { connection: "pending", users: 0, channels: 0, pendingReviews: 0 };
      const [allUsers, allChannels, pendingReviews] = await Promise.all([
        db.select().from(users),
        db.select().from(channels),
        db.select().from(historicalBatches).where(and(eq(historicalBatches.status, "review_required"))),
      ]);
      return { connection: "ready", users: allUsers.length, channels: allChannels.length, pendingReviews: pendingReviews.length };
    }),
  }),
});

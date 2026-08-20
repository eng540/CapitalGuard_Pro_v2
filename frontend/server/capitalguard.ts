import { z } from "zod";
import { randomUUID } from "node:crypto";
import { getWebUserCount } from "./db";
import { adminProcedure, analystProcedure, protectedProcedure, router, traderProcedure } from "./_core/trpc";
import { analyzeForwardText, smartAnalysisInput } from "./smart-analysis";
import { coreGetPrice, coreGetSignal, coreGetTmaPortfolio, coreGetTraderReadModel, coreIngestHistoricalEvidence, coreListOwnerReviewBatches, coreReviewHistoricalBatch, probeCoreHealth } from "./core-adapter";

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
  if (!validDirection || priceRisk === 0) return { valid: false, reason: "STOP_DIRECTION_INVALID", riskAmount: 0, quantity: 0, notional: 0 };
  const quantity = riskAmount / priceRisk;
  return { valid: true, reason: "RISK_PLAN_READY", riskAmount: Number(riskAmount.toFixed(2)), quantity: Number(quantity.toFixed(6)), notional: Number((quantity * input.entry).toFixed(2)) };
}

export type AnalystComparisonRow = { analystCode: string; winRate: number; totalPnlPct: number; maxDrawdownPct: number; sampleSize: number };
export function buildAnalystComparison(rows: AnalystComparisonRow[]) {
  const ranked = [...rows].sort((a, b) => b.totalPnlPct - a.totalPnlPct || b.winRate - a.winRate);
  const leader = ranked[0] ?? null;
  return { leader, rows: ranked, confidence: leader && leader.sampleSize >= 30 ? "SUFFICIENT_SAMPLE" : "LOW_SAMPLE" };
}

/**
 * These procedures deliberately return no local financial records. Core API is the
 * only source of truth for financial data and will progressively supply read models.
 */
function coreOwnedSnapshot() {
  return { connection: "core_api_required", portfolio: null, trades: [], recommendations: [], analyst: null, historical: [] };
}

export function telegramIdFromWebSession(openId: string): number {
  const match = /^telegram:(\d+)$/.exec(openId);
  const telegramId = match ? Number(match[1]) : Number.NaN;
  if (!Number.isSafeInteger(telegramId) || telegramId <= 0) throw new Error("CAPITALGUARD_TMA_SESSION_REQUIRED");
  return telegramId;
}

export const capitalguardRouter = router({
  workspace: protectedProcedure.query(() => coreOwnedSnapshot()),
  recommendations: protectedProcedure.query(() => []),
  discoverAnalysts: protectedProcedure.query(() => []),
  compareAnalysts: protectedProcedure.input(z.object({ codes: z.array(z.string().min(1)).min(2).max(3) })).query(() => ({ leader: null, rows: [], confidence: "CORE_DATA_PENDING" })),
  historicalBatches: protectedProcedure.query(() => []),
  historicalWallet: protectedProcedure.query(() => []),
  smartAnalyze: protectedProcedure.input(smartAnalysisInput).mutation(async ({ input }) => analyzeForwardText(input.text)),
  core: router({
    health: protectedProcedure.query(() => probeCoreHealth()),
    price: protectedProcedure.input(z.object({ symbol: z.string().trim().min(3).max(24).regex(/^[A-Z0-9]+$/) })).query(({ input }) => coreGetPrice(input.symbol)),
    signal: protectedProcedure.input(z.object({ recId: z.number().int().positive() })).query(({ input }) => coreGetSignal(input.recId)),
    tmaPortfolio: protectedProcedure.input(z.object({ initData: z.string().trim().min(20).max(10_000) })).query(({ input }) => coreGetTmaPortfolio(input.initData)),
    traderSnapshot: protectedProcedure.query(({ ctx }) => coreGetTraderReadModel(telegramIdFromWebSession(ctx.user.openId))),
  }),
  riskPlan: protectedProcedure.input(riskInput).mutation(({ input }) => calculateRiskPlan(input)),
  trader: router({ portfolio: traderProcedure.query(() => coreOwnedSnapshot()) }),
  analyst: router({ dashboard: analystProcedure.query(() => ({ profile: null, recommendations: [] })) }),
  admin: router({
    overview: adminProcedure.query(async () => ({ connection: "web_db", users: await getWebUserCount(), channels: 0, pendingReviews: 0 })),
    historicalReviewBatches: adminProcedure.query(({ ctx }) => coreListOwnerReviewBatches(telegramIdFromWebSession(ctx.user.openId))),
    reviewHistoricalBatch: adminProcedure.input(z.object({ batchId: z.number().int().positive(), approved: z.boolean(), note: z.string().trim().max(1_000).optional() })).mutation(({ ctx, input }) => coreReviewHistoricalBatch({
      actorTelegramId: telegramIdFromWebSession(ctx.user.openId),
      batchId: input.batchId,
      approved: input.approved,
      note: input.note,
      idempotencyKey: randomUUID(),
    })),
    ingestHistoricalEvidence: adminProcedure.input(z.object({ batchId: z.number().int().positive() })).mutation(({ ctx, input }) => coreIngestHistoricalEvidence({
      actorTelegramId: telegramIdFromWebSession(ctx.user.openId),
      batchId: input.batchId,
      idempotencyKey: randomUUID(),
    })),
  }),
});

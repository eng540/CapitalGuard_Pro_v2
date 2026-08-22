import { z } from "zod";
import { randomUUID } from "node:crypto";
import { getWebUserCount } from "./db";
import { adminProcedure, analystProcedure, protectedProcedure, router, traderProcedure } from "./_core/trpc";
import { analyzeForwardText, smartAnalysisInput } from "./smart-analysis";
import { coreCancelPendingUserTrade, coreCloseUserTrade, coreConfirmAnalystRecommendation, coreGetAnalystAssets, coreGetAnalystPublicationChannels, coreGetAnalystRecommendationPublication, coreGetAnalysts, coreGetOperationsFeed, coreGetPrice, coreGetR5Readiness, coreGetSignal, coreGetTraderHistorical, coreGetTraderReadModel, coreGetTraderRecommendationDetail, coreGetTraderRecommendations, coreIngestHistoricalEvidence, coreListOwnerReviewBatches, coreMoveUserTradeStopToBreakeven, corePartialCloseUserTrade, corePreviewAnalystRecommendation, coreReviewHistoricalBatch, coreUpdatePendingUserTradeEntry, probeCoreHealth } from "./core-adapter";

const riskInput = z.object({
  capital: z.number().positive(),
  riskPercent: z.number().positive().max(10),
  entry: z.number().positive(),
  stop: z.number().positive(),
  side: z.enum(["long", "short"]),
});

const analystRecommendationInput = z.object({
  asset: z.string().trim().min(3).max(24).regex(/^[A-Z0-9]+$/),
  side: z.enum(["LONG", "SHORT"]),
  market: z.string().trim().min(2).max(32),
  orderType: z.enum(["LIMIT", "MARKET", "STOP_MARKET"]),
  entry: z.number().nonnegative(),
  stopLoss: z.number().positive(),
  targetsRaw: z.string().trim().min(1).max(1_000),
  notes: z.string().trim().max(2_000).optional(),
  leverage: z.string().trim().max(16).optional(),
  channelIds: z.array(z.number().int()).max(20),
}).superRefine((value, ctx) => {
  if (value.orderType !== "MARKET" && value.entry <= 0) {
    ctx.addIssue({ code: "custom", path: ["entry"], message: "Entry price must be greater than zero for Limit or Stop Market" });
  }
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

async function adminOverview() {
  let connection: "ready" | "degraded" = "degraded";
  try {
    await probeCoreHealth();
    connection = "ready";
  } catch (error) {
    console.warn("[CapitalGuard Admin] Core health unavailable", error instanceof Error ? error.message : "unknown");
  }
  return { connection, users: await getWebUserCount(), channels: 0, pendingReviews: 0 };
}

export function telegramIdFromWebSession(openId: string): number {
  const match = /^telegram:(\d+)$/.exec(openId);
  const telegramId = match ? Number(match[1]) : Number.NaN;
  if (!Number.isSafeInteger(telegramId) || telegramId <= 0) throw new Error("CAPITALGUARD_TMA_SESSION_REQUIRED");
  return telegramId;
}

export const capitalguardRouter = router({
  workspace: protectedProcedure.query(() => coreOwnedSnapshot()),
  recommendations: protectedProcedure.query(({ ctx }) => coreGetTraderRecommendations(telegramIdFromWebSession(ctx.user.openId))),
  recommendationDetail: protectedProcedure.input(z.object({ publicRef: z.string().trim().min(1).max(80) })).query(({ ctx, input }) => coreGetTraderRecommendationDetail(telegramIdFromWebSession(ctx.user.openId), input.publicRef)),
  discoverAnalysts: protectedProcedure.query(() => coreGetAnalysts()),
  compareAnalysts: protectedProcedure.input(z.object({ codes: z.array(z.string().min(1)).min(2).max(3) })).query(() => ({ leader: null, rows: [], confidence: "CORE_DATA_PENDING" })),
  historicalBatches: protectedProcedure.query(({ ctx }) => coreGetTraderHistorical(telegramIdFromWebSession(ctx.user.openId))),
  historicalWallet: protectedProcedure.query(({ ctx }) => coreGetTraderHistorical(telegramIdFromWebSession(ctx.user.openId))),
  smartAnalyze: protectedProcedure.input(smartAnalysisInput).mutation(async ({ input }) => analyzeForwardText(input.text)),
  core: router({
    health: protectedProcedure.query(() => probeCoreHealth()),
    price: protectedProcedure.input(z.object({ symbol: z.string().trim().min(3).max(24).regex(/^[A-Z0-9]+$/) })).query(({ input }) => coreGetPrice(input.symbol)),
    signal: protectedProcedure.input(z.object({ recId: z.number().int().positive() })).query(({ input }) => coreGetSignal(input.recId)),
    traderSnapshot: protectedProcedure.query(({ ctx }) => coreGetTraderReadModel(telegramIdFromWebSession(ctx.user.openId))),
  }),
  riskPlan: protectedProcedure.input(riskInput).mutation(({ input }) => calculateRiskPlan(input)),
  trader: router({
    portfolio: traderProcedure.query(() => coreOwnedSnapshot()),
    closeUserTrade: traderProcedure.input(z.object({ publicRef: z.string().trim().min(1).max(80), idempotencyKey: z.string().trim().min(16).max(128) })).mutation(({ ctx, input }) => coreCloseUserTrade({
      actorTelegramId: telegramIdFromWebSession(ctx.user.openId),
      publicRef: input.publicRef,
      idempotencyKey: input.idempotencyKey,
    })),
    partialCloseUserTrade: traderProcedure.input(z.object({ publicRef: z.string().trim().min(1).max(80), closePercent: z.number().finite().positive().max(100), idempotencyKey: z.string().trim().min(16).max(128) })).mutation(({ ctx, input }) => corePartialCloseUserTrade({
      actorTelegramId: telegramIdFromWebSession(ctx.user.openId),
      publicRef: input.publicRef,
      closePercent: input.closePercent,
      idempotencyKey: input.idempotencyKey,
    })),
    moveUserTradeStopToBreakeven: traderProcedure.input(z.object({ publicRef: z.string().trim().min(1).max(80), idempotencyKey: z.string().trim().min(16).max(128) })).mutation(({ ctx, input }) => coreMoveUserTradeStopToBreakeven({
      actorTelegramId: telegramIdFromWebSession(ctx.user.openId),
      publicRef: input.publicRef,
      idempotencyKey: input.idempotencyKey,
    })),
    updatePendingUserTradeEntry: traderProcedure.input(z.object({ publicRef: z.string().trim().min(1).max(80), entry: z.number().finite().positive(), idempotencyKey: z.string().trim().min(16).max(128) })).mutation(({ ctx, input }) => coreUpdatePendingUserTradeEntry({
      actorTelegramId: telegramIdFromWebSession(ctx.user.openId), publicRef: input.publicRef, entry: input.entry, idempotencyKey: input.idempotencyKey,
    })),
    cancelPendingUserTrade: traderProcedure.input(z.object({ publicRef: z.string().trim().min(1).max(80), idempotencyKey: z.string().trim().min(16).max(128) })).mutation(({ ctx, input }) => coreCancelPendingUserTrade({
      actorTelegramId: telegramIdFromWebSession(ctx.user.openId),
      publicRef: input.publicRef,
      idempotencyKey: input.idempotencyKey,
    })),
  }),
  analyst: router({
    dashboard: analystProcedure.query(() => ({ profile: null, recommendations: [] })),
    assets: analystProcedure.input(z.object({ market: z.enum(["Spot", "Futures"]) })).query(({ input }) => coreGetAnalystAssets(input.market)),
    publicationChannels: analystProcedure.query(({ ctx }) => coreGetAnalystPublicationChannels(telegramIdFromWebSession(ctx.user.openId))),
    recommendationPublication: analystProcedure.input(z.object({ publicRef: z.string().trim().min(1).max(80) })).query(({ ctx, input }) => coreGetAnalystRecommendationPublication(telegramIdFromWebSession(ctx.user.openId), input.publicRef)),
    previewRecommendation: analystProcedure.input(analystRecommendationInput).mutation(({ ctx, input }) => corePreviewAnalystRecommendation({ ...input, actorTelegramId: telegramIdFromWebSession(ctx.user.openId) })),
    confirmRecommendation: analystProcedure.input(analystRecommendationInput.safeExtend({ idempotencyKey: z.string().trim().min(16).max(128).optional() })).mutation(({ ctx, input }) => coreConfirmAnalystRecommendation({ ...input, actorTelegramId: telegramIdFromWebSession(ctx.user.openId), idempotencyKey: input.idempotencyKey ?? randomUUID() })),
  }),
  admin: router({
    overview: adminProcedure.query(adminOverview),
    historicalReviewBatches: adminProcedure.query(async ({ ctx }) => {
      try {
        return await coreListOwnerReviewBatches(telegramIdFromWebSession(ctx.user.openId));
      } catch (error) {
        console.warn("[CapitalGuard Admin] Owner review queue unavailable", error instanceof Error ? error.message : "unknown");
        throw new Error("CAPITALGUARD_OWNER_REVIEW_QUEUE_UNAVAILABLE");
      }
    }),
    operationsFeed: adminProcedure.query(async ({ ctx }) => {
      try {
        return await coreGetOperationsFeed(telegramIdFromWebSession(ctx.user.openId));
      } catch (error) {
        console.warn("[CapitalGuard Admin] Operations feed unavailable", error instanceof Error ? error.message : "unknown");
        throw new Error("CAPITALGUARD_OPERATIONS_FEED_UNAVAILABLE");
      }
    }),
    r5Readiness: adminProcedure.query(async ({ ctx }) => {
      try {
        return await coreGetR5Readiness(telegramIdFromWebSession(ctx.user.openId));
      } catch (error) {
        console.warn("[CapitalGuard Admin] R5 readiness unavailable", error instanceof Error ? error.message : "unknown");
        throw new Error("CAPITALGUARD_R5_READINESS_UNAVAILABLE");
      }
    }),
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

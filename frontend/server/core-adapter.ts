const HEALTH_TIMEOUT_MS = 10_000;
const CORE_REQUEST_TIMEOUT_MS = 15_000;

type CoreConfig = { baseUrl: string; apiKey: string };
export type CoreHealth = { status: "ok"; baseUrl: string };
export type CoreTraderReadModel = {
  ok: true;
  schema_version: string;
  as_of: string;
  user: { telegram_id: number; role: string };
  portfolio: {
    open_position_count: number;
    positions: Array<{
      id: number;
      asset: string;
      side: string;
      market: string;
      entry: number;
      stop_loss: number;
      open_size_percent?: number;
      live_price: number | null;
      pnl_live_pct: number;
      status: string;
      source_type: string;
      targets: Array<{ price: number; percent: number; hit: boolean }>;
      protection?: { mode: string; active: boolean; trailing_value: number | null; break_even_after_profit_pct: number | null };
      created_at: string | null;
    }>;
  };
  performance: Record<string, unknown>;
  funnel: Record<string, unknown>;
};
export type CoreOwnerReviewBatch = {
  id: number;
  ref: string;
  status: string;
  source_kind: string;
  total_records: number;
  accepted_records: number;
  rejected_records: number;
  created_at: string | null;
  owner_review: { approved?: boolean; note?: string; reviewed_at?: string } | null;
  replay_ready: boolean;
  replay_signal_count: number;
  replay_block_reason: "HISTORICAL_REPLAY_NOT_READY" | null;
};
export type CoreOperationsEvent = { id: string; category: "PUBLICATION" | "LIFECYCLE" | "AUDIT"; code: string; severity: "info" | "warning" | "critical"; record_ref: string; occurred_at: string };
export type CoreOperationsFeed = { events: CoreOperationsEvent[]; summary: { critical: number; warning: number; total: number } };
export type CoreTraderRecommendation = {
  id: number;
  entity_type: "USER_TRADE";
  public_ref: string;
  display_ref: string;
  asset: string;
  side: string;
  market: string;
  entry: number;
  stop_loss: number;
  open_size_percent: number;
  targets: unknown[];
  status: string;
  source_type: string;
  source: { entity_type: "RECOMMENDATION"; public_ref: string | null; analyst_id: number | null } | null;
  created_at: string | null;
  activated_at: string | null;
  closed_at: string | null;
  timeline: Array<{ event_type: string; event_timestamp: string }>;
  protection?: { mode: string; active: boolean; trailing_value: number | null; break_even_after_profit_pct: number | null };
};
export type CoreHistoricalRecord = { public_ref: string; asset: string | null; side: string | null; status: string; trust_tier: string; eligible_for_ranking: boolean; decision_timestamp: string | null };
export type CoreAnalystReadModel = { analyst_code: string | null; public_ref: string | null; public_name: string; sample_size: number; win_rate_pct: number; total_pnl_pct: number; profit_factor?: number | null; profit_factor_infinite?: boolean; max_drawdown_pct: number; active_recommendations: number; risk_exposure_pct: number; eligible_for_ranking: boolean; freshness_days: number | null; signal_health?: { avg_minutes_to_first_target: number | null; target_observation_count: number; reversed_before_entry_count: number; most_profitable_pairs: Array<{ asset: string; pnl_pct: number }> } };
export type CoreR5Readiness = { status: "HOLD"; reasons: string[]; commercial_enabled: false; copy_trading_enabled: false; execution_controls: { auto_trade_enabled: boolean; trade_live_enabled: boolean }; observation: { started_at: string | null; required_hours: number; elapsed_hours: number; remaining_hours: number; complete: boolean }; snapshot: { outbox_backlog: number; owner_review_backlog: number; replay_backlog: number }; as_of: string };
export type CoreHistoricalTrustQuality = { status: "HOLD" | "EVIDENCE_READY"; quality: { analyst_id: number | null; channel_id: number | null; total_signals: number; verified_signals: number; rank_eligible_signals: number; excluded_signals: number; unfilled_signals: number; verified_replay_events: number; market_evidence_artifacts: number; replay_coverage_percent: number; reviewed_attributions: number; pending_attributions: number; confidence_weighted_sample: number }; commercial_enabled: false };
export type CoreHistoricalTrustReadiness = { status: "HOLD" | "READY_FOR_OWNER_RELEASE"; reasons: string[]; public_ranking_enabled: false; commercial_enabled: false; snapshot: { sample_size: number; replay_coverage_percent: number; reviewed_attributions: number; pending_attributions: number } };
export type CoreHistoricalBinanceReplayInput = { actorTelegramId: number; signalId: number; start: string; end: string; interval: "1m" | "5m" | "15m" | "1h"; limit: number; idempotencyKey: string };
export type CoreHistoricalBinanceReplayConfirmation = { ok: true; signal_id: number; event_count: number; replayed: boolean; commercial_enabled: false };
export type CoreHistoricalBatchBinanceReplayInput = { actorTelegramId: number; batchId: number; idempotencyKey: string };
export type CoreHistoricalBatchBinanceReplayConfirmation = { ok: true; batch_id: number; signal_ids: number[]; event_count: number; window: string; replayed: boolean; commercial_enabled: false };
export type CoreCommandError = Error & { code?: string; status?: number };
export type CoreHistoricalIntakeItem = {
  itemKey?: string;
  rawText?: string | null;
  sourceChatId?: number;
  sourceMessageId?: number;
  sourceMessageRevision?: number;
  sourceMessageTimestamp?: string;
  sourceReplyToMessageId?: number;
  sourceUri?: string;
  sourceOriginType?: string;
  relatedItemKey?: string;
  media?: Record<string, unknown>;
};
export type CoreHistoricalIntakeBatch = {
  id: number;
  ref: string;
  status: string;
  source_kind: string;
  total_records: number;
  accepted_records: number;
  rejected_records: number;
  created_at: string | null;
  metadata: Record<string, unknown>;
  items: Array<{
    id: number;
    order: number | null;
    item_key: string | null;
    status: string;
    semantic_status: string;
    parse_status: string | null;
    source_verification: string;
    source_chat_id: number | null;
    source_message_id: number | null;
    source_timestamp: string | null;
    raw_text: string | null;
    content_hash: string;
    missing_fields: string[];
    conflicting_fields: string[];
    canonical: Record<string, unknown>;
    rejection_reason: string | null;
    metadata: Record<string, unknown>;
  }>;
};
export type CoreHistoricalIntakeResponse = { ok: true; batch: CoreHistoricalIntakeBatch };
export type CoreHistoricalIntakeListResponse = { ok: true; batches: CoreHistoricalIntakeBatch[] };
export type CoreHistoricalIntakeReportResponse = { ok: true; report: { batch_id: number; batch_ref: string; status: string; source_kind: string; counts: Record<string, number>; readiness: Record<string, boolean>; signals: Array<Record<string, unknown>>; next_action: string } };
export type CoreAnalystRecommendationInput = { actorTelegramId: number; asset: string; side: "LONG" | "SHORT"; market: string; orderType: "LIMIT" | "MARKET" | "STOP_MARKET"; entry: number; stopLoss: number; targetsRaw: string; notes?: string; leverage?: string; channelIds: number[] };
export type CoreAnalystRecommendationPreview = { schema_version: number; mode: "PREVIEW"; asset: string; side: "LONG" | "SHORT"; market: string; order_type: "LIMIT" | "MARKET" | "STOP_MARKET"; entry: string; stop_loss: string; targets: Array<{ price: string; close_percent: number }>; live_price: string | null; publication: { state: "NOT_QUEUED"; eligible_channel_count: number } };
export type CoreAnalystRecommendationConfirmation = { ok: true; entity_type: "RECOMMENDATION"; public_ref: string; publication: { state: "SAVED" | "QUEUED"; queued_delivery_count: number }; replayed: boolean };
export type CoreAnalystPublicationChannel = { id: number; title: string; username: string | null };
export type CoreAnalystPublicationStatus = {
  schema_version: string;
  public_ref: string;
  publication: {
    state: "SAVED" | "QUEUED" | "PUBLISHING" | "DELIVERED" | "RETRYING" | "FAILED";
    delivery_count: number;
    delivered_count: number;
    retrying_count: number;
    failed_count: number;
    channels: Array<{ channel_id: number; channel_title: string; state: "QUEUED" | "PUBLISHING" | "DELIVERED" | "RETRYING" | "FAILED"; attempts: number; next_attempt_at: string | null; sent_at: string | null; failure_code: "RETRY_SCHEDULED" | "DELIVERY_FAILED" | null }>;
  };
};
export type CoreAnalystAsset = { symbol: string; venue: string; provider_symbol: string; market: string };
export type CoreUserTradeCloseInput = { actorTelegramId: number; publicRef: string; idempotencyKey: string };
export type CoreUserTradeCloseConfirmation = { ok: true; entity_type: "USER_TRADE"; public_ref: string; status: string; close_price: number; replayed: boolean };
export type CoreUserTradeCancelConfirmation = { ok: true; entity_type: "USER_TRADE"; public_ref: string; status: "CANCELLED"; close_price: null; pnl_percentage: null; replayed: boolean };
export type CoreUserTradePartialCloseInput = CoreUserTradeCloseInput & { closePercent: number };
export type CoreUserTradePartialCloseConfirmation = { ok: true; entity_type: "USER_TRADE"; public_ref: string; status: "ACTIVATED"; closed_percent: number; remaining_open_size_percent: number; partial_close_price: number; replayed: boolean };
export type CoreUserTradeBreakevenConfirmation = { ok: true; entity_type: "USER_TRADE"; public_ref: string; status: "ACTIVATED"; stop_loss: number; replayed: boolean };
export type CorePendingUserTradeEntryInput = CoreUserTradeCloseInput & { entry: number };
export type CorePendingUserTradeEntryConfirmation = { ok: true; entity_type: "USER_TRADE"; public_ref: string; status: "WATCHLIST" | "PENDING_ACTIVATION"; entry: number; replayed: boolean };

export function getCoreConfig(env = process.env): CoreConfig {
  const rawUrl = env.CAPITALGUARD_CORE_BASE_URL?.trim();
  const apiKey = env.CAPITALGUARD_CORE_API_KEY?.trim();
  if (!rawUrl || !apiKey) throw new Error("CAPITALGUARD_CORE_NOT_CONFIGURED");
  const url = new URL(rawUrl);
  if (url.protocol !== "https:") throw new Error("CAPITALGUARD_CORE_URL_MUST_USE_HTTPS");
  return { baseUrl: url.toString().replace(/\/$/, ""), apiKey };
}

function getRequestTimeoutMs(env = process.env): number {
  const configured = Number(env.CAPITALGUARD_CORE_TIMEOUT_MS);
  return Number.isFinite(configured) && configured >= 100 && configured <= 30_000 ? configured : CORE_REQUEST_TIMEOUT_MS;
}

export async function probeCoreHealth(fetchImpl: typeof fetch = fetch, env = process.env): Promise<CoreHealth> {
  const config = getCoreConfig(env);
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), HEALTH_TIMEOUT_MS);
  try {
    const response = await fetchImpl(`${config.baseUrl}/health`, {
      headers: { Authorization: `Bearer ${config.apiKey}`, Accept: "application/json" },
      signal: controller.signal,
    });
    if (!response.ok) throw new Error(`CAPITALGUARD_CORE_HEALTH_${response.status}`);
    const payload = await response.json() as { status?: string };
    if (payload.status !== "ok") throw new Error("CAPITALGUARD_CORE_UNHEALTHY");
    return { status: "ok", baseUrl: config.baseUrl };
  } catch (error) {
    if (controller.signal.aborted) throw new Error("CAPITALGUARD_CORE_HEALTH_TIMEOUT");
    if (error instanceof Error && error.message.startsWith("CAPITALGUARD_CORE_")) throw error;
    throw new Error("CAPITALGUARD_CORE_HEALTH_UNAVAILABLE");
  } finally {
    clearTimeout(timer);
  }
}

export async function coreReadOnlyFetch(path: string, init: RequestInit = {}, fetchImpl: typeof fetch = fetch, env = process.env) {
  const config = getCoreConfig(env);
  if (!path.startsWith("/api/")) throw new Error("CAPITALGUARD_CORE_READ_PATH_REQUIRED");
  const controller = new AbortController();
  const callerSignal = init.signal;
  const forwardCallerAbort = () => controller.abort();
  if (callerSignal?.aborted) controller.abort();
  else callerSignal?.addEventListener("abort", forwardCallerAbort, { once: true });
  const timer = setTimeout(() => controller.abort(), getRequestTimeoutMs(env));
  try {
    const response = await fetchImpl(`${config.baseUrl}${path}`, {
      ...init,
      signal: controller.signal,
      headers: { ...init.headers, Authorization: `Bearer ${config.apiKey}`, Accept: "application/json" },
    });
    if (!response.ok) {
      let errorCode: string | undefined;
      try {
        const errorPayload = await response.clone().json() as { detail?: unknown; code?: unknown };
        const detail = errorPayload.detail;
        if (detail && typeof detail === "object" && typeof (detail as { code?: unknown }).code === "string") {
          errorCode = (detail as { code: string }).code;
        } else if (typeof errorPayload.code === "string") {
          errorCode = errorPayload.code;
        } else if (typeof detail === "string") {
          errorCode = detail;
        }
      } catch {
        // Preserve the stable HTTP error when Core has no JSON detail.
      }
      const error = new Error(`CAPITALGUARD_CORE_API_${response.status}${errorCode ? `:${errorCode}` : ""}`) as CoreCommandError;
      error.code = errorCode;
      error.status = response.status;
      throw error;
    }
    return response.json() as Promise<unknown>;
  } catch (error) {
    if (controller.signal.aborted) throw new Error("CAPITALGUARD_CORE_TIMEOUT");
    if (error instanceof Error && error.message.startsWith("CAPITALGUARD_CORE_API_")) throw error;
    throw new Error("CAPITALGUARD_CORE_UNAVAILABLE");
  } finally {
    clearTimeout(timer);
    callerSignal?.removeEventListener("abort", forwardCallerAbort);
  }
}

function query(path: string, params: Record<string, string>) {
  const search = new URLSearchParams(params);
  return `${path}?${search.toString()}`;
}

export async function coreGetPrice(symbol: string, fetchImpl: typeof fetch = fetch, env = process.env) {
  return coreReadOnlyFetch(query("/api/webapp/price", { symbol }), {}, fetchImpl, env);
}

export async function coreGetSignal(recId: number, fetchImpl: typeof fetch = fetch, env = process.env) {
  return coreReadOnlyFetch(`/api/webapp/signal/${recId}`, {}, fetchImpl, env);
}

export async function coreGetTraderReadModel(telegramId: number, fetchImpl: typeof fetch = fetch, env = process.env): Promise<CoreTraderReadModel> {
  if (!Number.isSafeInteger(telegramId) || telegramId <= 0) throw new Error("CAPITALGUARD_TMA_TELEGRAM_ID_REQUIRED");
  const payload = await coreReadOnlyFetch(`/api/webapp/read-models/trader/${telegramId}`, {}, fetchImpl, env);
  if (!payload || typeof payload !== "object" || (payload as { ok?: unknown }).ok !== true) {
    throw new Error("CAPITALGUARD_CORE_READ_MODEL_INVALID");
  }
  return payload as CoreTraderReadModel;
}

async function coreCommand(path: string, payload: Record<string, unknown>, fetchImpl: typeof fetch = fetch, env = process.env) {
  const result = await coreReadOnlyFetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  }, fetchImpl, env);
  if (!result || typeof result !== "object" || (result as { ok?: unknown }).ok !== true) throw new Error("CAPITALGUARD_CORE_COMMAND_INVALID");
  return result as Record<string, unknown>;
}

function analystRecommendationPayload(input: CoreAnalystRecommendationInput) {
  if (!Number.isSafeInteger(input.actorTelegramId) || input.actorTelegramId <= 0) throw new Error("CAPITALGUARD_TMA_TELEGRAM_ID_REQUIRED");
  return { actor_telegram_id: input.actorTelegramId, asset: input.asset, side: input.side, market: input.market, order_type: input.orderType, entry: input.entry, stop_loss: input.stopLoss, targets_raw: input.targetsRaw, notes: input.notes, leverage: input.leverage ?? "20", channel_ids: input.channelIds };
}

export async function corePreviewAnalystRecommendation(input: CoreAnalystRecommendationInput, fetchImpl: typeof fetch = fetch, env = process.env): Promise<CoreAnalystRecommendationPreview> {
  const result = await coreCommand("/api/webapp/recommendations/preview", analystRecommendationPayload(input), fetchImpl, env);
  const preview = result.preview;
  if (!preview || typeof preview !== "object" || (preview as { mode?: unknown }).mode !== "PREVIEW") throw new Error("CAPITALGUARD_CORE_ANALYST_PREVIEW_INVALID");
  return preview as CoreAnalystRecommendationPreview;
}

export async function coreGetAnalystPublicationChannels(actorTelegramId: number, fetchImpl: typeof fetch = fetch, env = process.env): Promise<CoreAnalystPublicationChannel[]> {
  if (!Number.isSafeInteger(actorTelegramId) || actorTelegramId <= 0) throw new Error("CAPITALGUARD_TMA_TELEGRAM_ID_REQUIRED");
  const result = await coreReadOnlyFetch(query("/api/webapp/recommendations/channels", { actor_telegram_id: String(actorTelegramId) }), {}, fetchImpl, env);
  if (!result || typeof result !== "object" || (result as { ok?: unknown }).ok !== true || !Array.isArray((result as { items?: unknown }).items)) throw new Error("CAPITALGUARD_CORE_ANALYST_CHANNELS_INVALID");
  return (result as { items: CoreAnalystPublicationChannel[] }).items;
}

export async function coreGetAnalystRecommendationPublication(actorTelegramId: number, publicRef: string, fetchImpl: typeof fetch = fetch, env = process.env): Promise<CoreAnalystPublicationStatus> {
  if (!Number.isSafeInteger(actorTelegramId) || actorTelegramId <= 0) throw new Error("CAPITALGUARD_TMA_TELEGRAM_ID_REQUIRED");
  const normalizedRef = publicRef.trim();
  if (!normalizedRef || normalizedRef.length > 80) throw new Error("CAPITALGUARD_PUBLIC_REF_REQUIRED");
  const result = await coreReadOnlyFetch(query(`/api/webapp/recommendations/${encodeURIComponent(normalizedRef)}/publication`, { actor_telegram_id: String(actorTelegramId) }), {}, fetchImpl, env);
  if (!result || typeof result !== "object" || (result as { ok?: unknown }).ok !== true || (result as { public_ref?: unknown }).public_ref !== normalizedRef || !(result as { publication?: unknown }).publication) {
    throw new Error("CAPITALGUARD_CORE_ANALYST_PUBLICATION_INVALID");
  }
  return result as CoreAnalystPublicationStatus;
}

export async function coreGetAnalystAssets(market: "Spot" | "Futures", fetchImpl: typeof fetch = fetch, env = process.env): Promise<CoreAnalystAsset[]> {
  const result = await coreReadOnlyFetch(query("/api/webapp/recommendations/assets", { market }), {}, fetchImpl, env);
  if (!result || typeof result !== "object" || (result as { ok?: unknown }).ok !== true || !Array.isArray((result as { items?: unknown }).items)) throw new Error("CAPITALGUARD_CORE_ANALYST_ASSETS_INVALID");
  return (result as { items: CoreAnalystAsset[] }).items;
}

export async function coreConfirmAnalystRecommendation(input: CoreAnalystRecommendationInput & { idempotencyKey: string }, fetchImpl: typeof fetch = fetch, env = process.env): Promise<CoreAnalystRecommendationConfirmation> {
  const key = input.idempotencyKey.trim();
  if (key.length < 16 || key.length > 128) throw new Error("CAPITALGUARD_IDEMPOTENCY_KEY_INVALID");
  const result = await coreCommand("/api/webapp/recommendations/confirm", { ...analystRecommendationPayload(input), idempotency_key: key }, fetchImpl, env);
  if (result.entity_type !== "RECOMMENDATION" || typeof result.public_ref !== "string" || !result.publication) throw new Error("CAPITALGUARD_CORE_ANALYST_CONFIRM_INVALID");
  return result as unknown as CoreAnalystRecommendationConfirmation;
}

export async function coreListOwnerReviewBatches(actorTelegramId: number, fetchImpl: typeof fetch = fetch, env = process.env): Promise<CoreOwnerReviewBatch[]> {
  if (!Number.isSafeInteger(actorTelegramId) || actorTelegramId <= 0) throw new Error("CAPITALGUARD_TMA_TELEGRAM_ID_REQUIRED");
  const result = await coreReadOnlyFetch(query("/api/webapp/owner/review-batches", { actor_telegram_id: String(actorTelegramId) }), {}, fetchImpl, env);
  if (!result || typeof result !== "object" || (result as { ok?: unknown }).ok !== true || !Array.isArray((result as { batches?: unknown }).batches)) {
    throw new Error("CAPITALGUARD_CORE_OWNER_BATCHES_INVALID");
  }
  return (result as { batches: CoreOwnerReviewBatch[] }).batches;
}

export async function coreGetOperationsFeed(actorTelegramId: number, fetchImpl: typeof fetch = fetch, env = process.env): Promise<CoreOperationsFeed> {
  if (!Number.isSafeInteger(actorTelegramId) || actorTelegramId <= 0) throw new Error("CAPITALGUARD_TMA_TELEGRAM_ID_REQUIRED");
  const result = await coreReadOnlyFetch(query("/api/webapp/owner/operations-feed", { actor_telegram_id: String(actorTelegramId) }), {}, fetchImpl, env);
  if (!result || typeof result !== "object" || (result as { ok?: unknown }).ok !== true || !Array.isArray((result as { events?: unknown }).events)) throw new Error("CAPITALGUARD_CORE_OPERATIONS_FEED_INVALID");
  return result as CoreOperationsFeed;
}

export async function coreGetTraderRecommendations(telegramId: number, fetchImpl: typeof fetch = fetch, env = process.env): Promise<{ schema_version: string; as_of: string; items: CoreTraderRecommendation[] }> {
  if (!Number.isSafeInteger(telegramId) || telegramId <= 0) throw new Error("CAPITALGUARD_TMA_TELEGRAM_ID_REQUIRED");
  const result = await coreReadOnlyFetch(`/api/webapp/read-models/trader/${telegramId}/recommendations`, {}, fetchImpl, env);
  if (!result || typeof result !== "object" || (result as { ok?: unknown }).ok !== true || typeof (result as { schema_version?: unknown }).schema_version !== "string" || !Array.isArray((result as { items?: unknown }).items)) throw new Error("CAPITALGUARD_CORE_RECOMMENDATIONS_INVALID");
  return result as { schema_version: string; as_of: string; items: CoreTraderRecommendation[] };
}

export async function coreGetTraderRecommendationDetail(telegramId: number, publicRef: string, fetchImpl: typeof fetch = fetch, env = process.env): Promise<{ schema_version: string; as_of: string; item: CoreTraderRecommendation }> {
  if (!Number.isSafeInteger(telegramId) || telegramId <= 0) throw new Error("CAPITALGUARD_TMA_TELEGRAM_ID_REQUIRED");
  const normalizedRef = publicRef.trim();
  if (!normalizedRef || normalizedRef.length > 80) throw new Error("CAPITALGUARD_PUBLIC_REF_REQUIRED");
  const result = await coreReadOnlyFetch(`/api/webapp/read-models/trader/${telegramId}/recommendations/${encodeURIComponent(normalizedRef)}`, {}, fetchImpl, env);
  if (!result || typeof result !== "object" || (result as { ok?: unknown }).ok !== true || typeof (result as { schema_version?: unknown }).schema_version !== "string" || !(result as { item?: unknown }).item) throw new Error("CAPITALGUARD_CORE_RECOMMENDATION_DETAIL_INVALID");
  return result as { schema_version: string; as_of: string; item: CoreTraderRecommendation };
}

export async function coreCloseUserTrade(input: CoreUserTradeCloseInput, fetchImpl: typeof fetch = fetch, env = process.env): Promise<CoreUserTradeCloseConfirmation> {
  if (!Number.isSafeInteger(input.actorTelegramId) || input.actorTelegramId <= 0) throw new Error("CAPITALGUARD_TMA_TELEGRAM_ID_REQUIRED");
  const publicRef = input.publicRef.trim();
  const idempotencyKey = input.idempotencyKey.trim();
  if (!publicRef || publicRef.length > 80) throw new Error("CAPITALGUARD_PUBLIC_REF_REQUIRED");
  if (idempotencyKey.length < 16 || idempotencyKey.length > 128) throw new Error("CAPITALGUARD_IDEMPOTENCY_KEY_INVALID");
  const path = `/api/webapp/read-models/trader/${input.actorTelegramId}/recommendations/${encodeURIComponent(publicRef)}/commands/close`;
  const result = await coreCommand(path, { actor_telegram_id: input.actorTelegramId, idempotency_key: idempotencyKey }, fetchImpl, env);
  if (result.entity_type !== "USER_TRADE" || result.public_ref !== publicRef || typeof result.close_price !== "number") {
    throw new Error("CAPITALGUARD_CORE_USER_TRADE_CLOSE_INVALID");
  }
  return result as unknown as CoreUserTradeCloseConfirmation;
}

export async function coreCancelPendingUserTrade(input: CoreUserTradeCloseInput, fetchImpl: typeof fetch = fetch, env = process.env): Promise<CoreUserTradeCancelConfirmation> {
  if (!Number.isSafeInteger(input.actorTelegramId) || input.actorTelegramId <= 0) throw new Error("CAPITALGUARD_TMA_TELEGRAM_ID_REQUIRED");
  const publicRef = input.publicRef.trim();
  const idempotencyKey = input.idempotencyKey.trim();
  if (!publicRef || publicRef.length > 80) throw new Error("CAPITALGUARD_PUBLIC_REF_REQUIRED");
  if (idempotencyKey.length < 16 || idempotencyKey.length > 128) throw new Error("CAPITALGUARD_IDEMPOTENCY_KEY_INVALID");
  const path = `/api/webapp/read-models/trader/${input.actorTelegramId}/recommendations/${encodeURIComponent(publicRef)}/commands/cancel`;
  const result = await coreCommand(path, { actor_telegram_id: input.actorTelegramId, idempotency_key: idempotencyKey }, fetchImpl, env);
  if (result.entity_type !== "USER_TRADE" || result.public_ref !== publicRef || result.status !== "CANCELLED" || result.close_price !== null || result.pnl_percentage !== null) {
    throw new Error("CAPITALGUARD_CORE_USER_TRADE_CANCEL_INVALID");
  }
  return result as unknown as CoreUserTradeCancelConfirmation;
}

export async function corePartialCloseUserTrade(input: CoreUserTradePartialCloseInput, fetchImpl: typeof fetch = fetch, env = process.env): Promise<CoreUserTradePartialCloseConfirmation> {
  if (!Number.isSafeInteger(input.actorTelegramId) || input.actorTelegramId <= 0) throw new Error("CAPITALGUARD_TMA_TELEGRAM_ID_REQUIRED");
  const publicRef = input.publicRef.trim();
  const idempotencyKey = input.idempotencyKey.trim();
  if (!publicRef || publicRef.length > 80) throw new Error("CAPITALGUARD_PUBLIC_REF_REQUIRED");
  if (idempotencyKey.length < 16 || idempotencyKey.length > 128) throw new Error("CAPITALGUARD_IDEMPOTENCY_KEY_INVALID");
  if (!Number.isFinite(input.closePercent) || input.closePercent <= 0 || input.closePercent > 100) throw new Error("CAPITALGUARD_PARTIAL_CLOSE_PERCENT_INVALID");
  const path = `/api/webapp/read-models/trader/${input.actorTelegramId}/recommendations/${encodeURIComponent(publicRef)}/commands/partial-close`;
  const result = await coreCommand(path, { actor_telegram_id: input.actorTelegramId, close_percent: input.closePercent, idempotency_key: idempotencyKey }, fetchImpl, env);
  if (result.entity_type !== "USER_TRADE" || result.public_ref !== publicRef || result.status !== "ACTIVATED" || typeof result.closed_percent !== "number" || typeof result.remaining_open_size_percent !== "number" || typeof result.partial_close_price !== "number") {
    throw new Error("CAPITALGUARD_CORE_USER_TRADE_PARTIAL_CLOSE_INVALID");
  }
  return result as unknown as CoreUserTradePartialCloseConfirmation;
}

export async function coreMoveUserTradeStopToBreakeven(input: CoreUserTradeCloseInput, fetchImpl: typeof fetch = fetch, env = process.env): Promise<CoreUserTradeBreakevenConfirmation> {
  if (!Number.isSafeInteger(input.actorTelegramId) || input.actorTelegramId <= 0) throw new Error("CAPITALGUARD_TMA_TELEGRAM_ID_REQUIRED");
  const publicRef = input.publicRef.trim();
  const idempotencyKey = input.idempotencyKey.trim();
  if (!publicRef || publicRef.length > 80) throw new Error("CAPITALGUARD_PUBLIC_REF_REQUIRED");
  if (idempotencyKey.length < 16 || idempotencyKey.length > 128) throw new Error("CAPITALGUARD_IDEMPOTENCY_KEY_INVALID");
  const path = `/api/webapp/read-models/trader/${input.actorTelegramId}/recommendations/${encodeURIComponent(publicRef)}/commands/move-stop-to-breakeven`;
  const result = await coreCommand(path, { actor_telegram_id: input.actorTelegramId, idempotency_key: idempotencyKey }, fetchImpl, env);
  if (result.entity_type !== "USER_TRADE" || result.public_ref !== publicRef || result.status !== "ACTIVATED" || typeof result.stop_loss !== "number") {
    throw new Error("CAPITALGUARD_CORE_USER_TRADE_BREAKEVEN_INVALID");
  }
  return result as unknown as CoreUserTradeBreakevenConfirmation;
}

export async function coreUpdatePendingUserTradeEntry(input: CorePendingUserTradeEntryInput, fetchImpl: typeof fetch = fetch, env = process.env): Promise<CorePendingUserTradeEntryConfirmation> {
  if (!Number.isSafeInteger(input.actorTelegramId) || input.actorTelegramId <= 0) throw new Error("CAPITALGUARD_TMA_TELEGRAM_ID_REQUIRED");
  const publicRef = input.publicRef.trim(); const idempotencyKey = input.idempotencyKey.trim();
  if (!publicRef || publicRef.length > 80) throw new Error("CAPITALGUARD_PUBLIC_REF_REQUIRED");
  if (idempotencyKey.length < 16 || idempotencyKey.length > 128) throw new Error("CAPITALGUARD_IDEMPOTENCY_KEY_INVALID");
  if (!Number.isFinite(input.entry) || input.entry <= 0) throw new Error("CAPITALGUARD_PENDING_ENTRY_INVALID");
  const path = `/api/webapp/read-models/trader/${input.actorTelegramId}/recommendations/${encodeURIComponent(publicRef)}/commands/update-entry`;
  const result = await coreCommand(path, { actor_telegram_id: input.actorTelegramId, entry: input.entry, idempotency_key: idempotencyKey }, fetchImpl, env);
  if (result.entity_type !== "USER_TRADE" || result.public_ref !== publicRef || (result.status !== "WATCHLIST" && result.status !== "PENDING_ACTIVATION") || typeof result.entry !== "number") throw new Error("CAPITALGUARD_CORE_PENDING_ENTRY_INVALID");
  return result as unknown as CorePendingUserTradeEntryConfirmation;
}

export async function coreCreateHistoricalIntake(input: { actorTelegramId: number; sourceKind: "TELEGRAM_EXPORT" | "MANUAL_ADMIN_IMPORT"; inputMode: "PASTE" | "UPLOAD" | "TELEGRAM_EXPORT"; items: CoreHistoricalIntakeItem[]; isPartial: boolean; batchLabel?: string }, fetchImpl: typeof fetch = fetch, env = process.env): Promise<CoreHistoricalIntakeResponse> {
  if (!Number.isSafeInteger(input.actorTelegramId) || input.actorTelegramId <= 0) throw new Error("CAPITALGUARD_TMA_TELEGRAM_ID_REQUIRED");
  if (!Array.isArray(input.items) || input.items.length < 1 || input.items.length > 5000) throw new Error("CAPITALGUARD_HISTORICAL_ITEMS_INVALID");
  const items = input.items.map(item => ({
    item_key: item.itemKey,
    raw_text: item.rawText,
    source_chat_id: item.sourceChatId,
    source_message_id: item.sourceMessageId,
    source_message_revision: item.sourceMessageRevision ?? 0,
    source_message_timestamp: item.sourceMessageTimestamp,
    source_reply_to_message_id: item.sourceReplyToMessageId,
    source_uri: item.sourceUri,
    source_origin_type: item.sourceOriginType,
    related_item_key: item.relatedItemKey,
    media: item.media,
  }));
  const result = await coreCommand("/api/webapp/historical/intake", {
    actor_telegram_id: input.actorTelegramId,
    source_kind: input.sourceKind,
    input_mode: input.inputMode,
    items,
    is_partial: input.isPartial,
    batch_label: input.batchLabel,
  }, fetchImpl, env);
  if (!result.batch || typeof result.batch !== "object" || !Array.isArray((result.batch as { items?: unknown }).items)) throw new Error("CAPITALGUARD_HISTORICAL_INTAKE_INVALID");
  return result as unknown as CoreHistoricalIntakeResponse;
}

export async function coreListHistoricalIntake(actorTelegramId: number, fetchImpl: typeof fetch = fetch, env = process.env): Promise<CoreHistoricalIntakeListResponse> {
  if (!Number.isSafeInteger(actorTelegramId) || actorTelegramId <= 0) throw new Error("CAPITALGUARD_TMA_TELEGRAM_ID_REQUIRED");
  const result = await coreReadOnlyFetch(query("/api/webapp/historical/intake", { actor_telegram_id: String(actorTelegramId), limit: "25" }), {}, fetchImpl, env);
  if (!result || typeof result !== "object" || (result as { ok?: unknown }).ok !== true || !Array.isArray((result as { batches?: unknown }).batches)) throw new Error("CAPITALGUARD_HISTORICAL_INTAKE_LIST_INVALID");
  return result as CoreHistoricalIntakeListResponse;
}

export async function coreGetHistoricalIntakeReport(batchId: number, actorTelegramId: number, fetchImpl: typeof fetch = fetch, env = process.env): Promise<CoreHistoricalIntakeReportResponse> {
  if (!Number.isSafeInteger(batchId) || batchId <= 0 || !Number.isSafeInteger(actorTelegramId) || actorTelegramId <= 0) throw new Error("CAPITALGUARD_HISTORICAL_INTAKE_INPUT_INVALID");
  const result = await coreReadOnlyFetch(query(`/api/webapp/historical/intake/${batchId}/report`, { actor_telegram_id: String(actorTelegramId) }), {}, fetchImpl, env);
  if (!result || typeof result !== "object" || (result as { ok?: unknown }).ok !== true || !(result as { report?: unknown }).report) throw new Error("CAPITALGUARD_HISTORICAL_REPORT_INVALID");
  return result as CoreHistoricalIntakeReportResponse;
}

export async function coreGetHistoricalIntake(batchId: number, actorTelegramId: number, fetchImpl: typeof fetch = fetch, env = process.env): Promise<CoreHistoricalIntakeResponse> {
  if (!Number.isSafeInteger(batchId) || batchId <= 0 || !Number.isSafeInteger(actorTelegramId) || actorTelegramId <= 0) throw new Error("CAPITALGUARD_HISTORICAL_INTAKE_INPUT_INVALID");
  const result = await coreReadOnlyFetch(query(`/api/webapp/historical/intake/${batchId}`, { actor_telegram_id: String(actorTelegramId) }), {}, fetchImpl, env);
  if (!result || typeof result !== "object" || (result as { ok?: unknown }).ok !== true || !(result as { batch?: unknown }).batch) throw new Error("CAPITALGUARD_HISTORICAL_INTAKE_INVALID");
  return result as CoreHistoricalIntakeResponse;
}

export async function coreGetTraderHistorical(telegramId: number, fetchImpl: typeof fetch = fetch, env = process.env): Promise<{ as_of: string; items: CoreHistoricalRecord[] }> {
  if (!Number.isSafeInteger(telegramId) || telegramId <= 0) throw new Error("CAPITALGUARD_TMA_TELEGRAM_ID_REQUIRED");
  const result = await coreReadOnlyFetch(`/api/webapp/read-models/trader/${telegramId}/historical`, {}, fetchImpl, env);
  if (!result || typeof result !== "object" || (result as { ok?: unknown }).ok !== true || !Array.isArray((result as { items?: unknown }).items)) throw new Error("CAPITALGUARD_CORE_HISTORICAL_INVALID");
  return result as { as_of: string; items: CoreHistoricalRecord[] };
}

export type CoreSignalDiscoveryItem = { public_ref: string | null; analyst_code: string | null; analyst_name: string | null; asset: string; side: string; status: string; pnl_pct: number | null; created_at: string | null; closed_at: string | null };

export async function coreGetSignalDiscovery(input: { asset?: string; windowDays: number; minPnlPct?: number }, fetchImpl: typeof fetch = fetch, env = process.env): Promise<{ as_of: string; window_days: number; items: CoreSignalDiscoveryItem[] }> {
  const params: Record<string, string> = { window_days: String(input.windowDays) };
  if (input.asset?.trim()) params.asset = input.asset.trim().toUpperCase();
  if (input.minPnlPct !== undefined) params.min_pnl_pct = String(input.minPnlPct);
  const result = await coreReadOnlyFetch(query("/api/webapp/read-models/signals", params), {}, fetchImpl, env);
  if (!result || typeof result !== "object" || (result as { ok?: unknown }).ok !== true || !Array.isArray((result as { items?: unknown }).items)) throw new Error("CAPITALGUARD_CORE_SIGNAL_DISCOVERY_INVALID");
  return result as { as_of: string; window_days: number; items: CoreSignalDiscoveryItem[] };
}

export async function coreGetAnalysts(fetchImpl: typeof fetch = fetch, env = process.env): Promise<{ as_of: string; items: CoreAnalystReadModel[] }> {
  const result = await coreReadOnlyFetch("/api/webapp/read-models/analysts", {}, fetchImpl, env);
  if (!result || typeof result !== "object" || (result as { ok?: unknown }).ok !== true || !Array.isArray((result as { items?: unknown }).items)) throw new Error("CAPITALGUARD_CORE_ANALYSTS_INVALID");
  return result as { as_of: string; items: CoreAnalystReadModel[] };
}

export type CoreAnalystDashboard = {
  ok: true;
  schema_version: string;
  as_of: string;
  profile: { analyst_code: string | null; public_ref: string | null; public_name: string; bio: string | null; specialty_market: string | null; strategy_style: string | null };
  health: { sample_size: number; win_rate_pct: number; total_pnl_pct: number; profit_factor: number | null; profit_factor_infinite: boolean; max_drawdown_pct: number; active_recommendations: number; risk_exposure_pct: number; freshness_days: number | null; eligible_for_ranking: boolean; minimum_sample_size: number; signal_health: { avg_minutes_to_first_target: number | null; target_observation_count: number; reversed_before_entry_count: number; most_profitable_pairs: Array<{ asset: string; pnl_pct: number }> } };
};

export async function coreGetAnalystDashboard(actorTelegramId: number, fetchImpl: typeof fetch = fetch, env = process.env): Promise<CoreAnalystDashboard> {
  if (!Number.isSafeInteger(actorTelegramId) || actorTelegramId <= 0) throw new Error("CAPITALGUARD_TMA_TELEGRAM_ID_REQUIRED");
  const result = await coreReadOnlyFetch(`/api/webapp/read-models/analyst/${actorTelegramId}/dashboard`, {}, fetchImpl, env);
  if (!result || typeof result !== "object" || (result as { ok?: unknown }).ok !== true || !(result as { profile?: unknown }).profile || !(result as { health?: unknown }).health) throw new Error("CAPITALGUARD_CORE_ANALYST_DASHBOARD_INVALID");
  return result as CoreAnalystDashboard;
}

export async function coreGetR5Readiness(actorTelegramId: number, fetchImpl: typeof fetch = fetch, env = process.env): Promise<CoreR5Readiness> {
  if (!Number.isSafeInteger(actorTelegramId) || actorTelegramId <= 0) throw new Error("CAPITALGUARD_TMA_TELEGRAM_ID_REQUIRED");
  const result = await coreReadOnlyFetch(query("/api/webapp/owner/r5-readiness", { actor_telegram_id: String(actorTelegramId) }), {}, fetchImpl, env);
  if (!result || typeof result !== "object" || (result as { ok?: unknown }).ok !== true || (result as { status?: unknown }).status !== "HOLD") throw new Error("CAPITALGUARD_CORE_R5_READINESS_INVALID");
  return result as CoreR5Readiness;
}

export async function coreGetHistoricalTrustQuality(actorTelegramId: number, fetchImpl: typeof fetch = fetch, env = process.env): Promise<CoreHistoricalTrustQuality> {
  if (!Number.isSafeInteger(actorTelegramId) || actorTelegramId <= 0) throw new Error("CAPITALGUARD_TMA_TELEGRAM_ID_REQUIRED");
  const result = await coreReadOnlyFetch(query("/api/webapp/owner/historical-quality", { actor_telegram_id: String(actorTelegramId) }), {}, fetchImpl, env);
  if (!result || typeof result !== "object" || (result as { ok?: unknown }).ok !== true || !["HOLD", "EVIDENCE_READY"].includes(String((result as { status?: unknown }).status))) throw new Error("CAPITALGUARD_CORE_HISTORICAL_QUALITY_INVALID");
  return result as CoreHistoricalTrustQuality;
}

export async function coreGetHistoricalTrustReadiness(actorTelegramId: number, fetchImpl: typeof fetch = fetch, env = process.env): Promise<CoreHistoricalTrustReadiness> {
  if (!Number.isSafeInteger(actorTelegramId) || actorTelegramId <= 0) throw new Error("CAPITALGUARD_TMA_TELEGRAM_ID_REQUIRED");
  const result = await coreReadOnlyFetch(query("/api/webapp/owner/historical-trust-readiness", { actor_telegram_id: String(actorTelegramId) }), {}, fetchImpl, env);
  if (!result || typeof result !== "object" || (result as { ok?: unknown }).ok !== true || !["HOLD", "READY_FOR_OWNER_RELEASE"].includes(String((result as { status?: unknown }).status))) throw new Error("CAPITALGUARD_CORE_HISTORICAL_TRUST_READINESS_INVALID");
  return result as CoreHistoricalTrustReadiness;
}

export async function coreReviewHistoricalBatch(input: { actorTelegramId: number; batchId: number; approved: boolean; note?: string; idempotencyKey: string }, fetchImpl: typeof fetch = fetch, env = process.env) {
  return coreCommand("/api/webapp/owner/review-batches", {
    actor_telegram_id: input.actorTelegramId,
    batch_id: input.batchId,
    approved: input.approved,
    note: input.note,
    idempotency_key: input.idempotencyKey,
  }, fetchImpl, env);
}

export async function coreIngestHistoricalEvidence(input: { actorTelegramId: number; batchId: number; idempotencyKey: string }, fetchImpl: typeof fetch = fetch, env = process.env) {
  return coreCommand(`/api/webapp/owner/review-batches/${input.batchId}/ingest-evidence`, {
    actor_telegram_id: input.actorTelegramId,
    batch_id: input.batchId,
    idempotency_key: input.idempotencyKey,
  }, fetchImpl, env);
}

export async function coreReplayHistoricalSignalFromBinance(input: CoreHistoricalBinanceReplayInput, fetchImpl: typeof fetch = fetch, env = process.env): Promise<CoreHistoricalBinanceReplayConfirmation> {
  if (!Number.isSafeInteger(input.actorTelegramId) || input.actorTelegramId <= 0 || !Number.isSafeInteger(input.signalId) || input.signalId <= 0) throw new Error("CAPITALGUARD_OWNER_REPLAY_INPUT_INVALID");
  if (!Number.isSafeInteger(input.limit) || input.limit < 1 || input.limit > 1500 || input.idempotencyKey.trim().length < 16) throw new Error("CAPITALGUARD_OWNER_REPLAY_INPUT_INVALID");
  const result = await coreCommand(`/api/webapp/owner/historical-signals/${input.signalId}/replay-binance`, { actor_telegram_id: input.actorTelegramId, signal_id: input.signalId, start: input.start, end: input.end, interval: input.interval, limit: input.limit, idempotency_key: input.idempotencyKey }, fetchImpl, env);
  if (result.ok !== true || result.signal_id !== input.signalId || typeof result.event_count !== "number" || result.commercial_enabled !== false) throw new Error("CAPITALGUARD_OWNER_REPLAY_INVALID");
  return result as CoreHistoricalBinanceReplayConfirmation;
}

export async function coreReplayReviewedBatchFromBinance(input: CoreHistoricalBatchBinanceReplayInput, fetchImpl: typeof fetch = fetch, env = process.env): Promise<CoreHistoricalBatchBinanceReplayConfirmation> {
  if (!Number.isSafeInteger(input.actorTelegramId) || input.actorTelegramId <= 0 || !Number.isSafeInteger(input.batchId) || input.batchId <= 0 || input.idempotencyKey.trim().length < 16) throw new Error("CAPITALGUARD_OWNER_BATCH_REPLAY_INPUT_INVALID");
  const result = await coreCommand(`/api/webapp/owner/review-batches/${input.batchId}/replay-binance`, { actor_telegram_id: input.actorTelegramId, batch_id: input.batchId, idempotency_key: input.idempotencyKey }, fetchImpl, env);
  if (result.ok !== true || result.batch_id !== input.batchId || !Array.isArray(result.signal_ids) || typeof result.event_count !== "number" || result.commercial_enabled !== false) throw new Error("CAPITALGUARD_OWNER_BATCH_REPLAY_INVALID");
  return result as CoreHistoricalBatchBinanceReplayConfirmation;
}

/**
 * Core owns the Telegram bot secret and validates the initData HMAC. Web never
 * receives or stores a bot token; it accepts identity data only after this
 * server-to-server check succeeds.
 */
export async function coreVerifyTelegramInitData(initData: string, fetchImpl: typeof fetch = fetch, env = process.env) {
  const normalized = initData.trim();
  if (!normalized || normalized.length > 10_000) throw new Error("CAPITALGUARD_TMA_INITDATA_INVALID");
  let payload: Record<string, unknown>;
  try {
    payload = await coreCommand("/api/webapp/telegram/verify", { init_data: normalized }, fetchImpl, env);
  } catch (error) {
    if (error instanceof Error && error.message === "CAPITALGUARD_CORE_COMMAND_INVALID") {
      throw new Error("CAPITALGUARD_TMA_INITDATA_INVALID");
    }
    throw error;
  }
  if (typeof payload.telegram_id !== "number" || !Number.isSafeInteger(payload.telegram_id) || payload.telegram_id <= 0) {
    throw new Error("CAPITALGUARD_TMA_INITDATA_INVALID");
  }
  return payload as { ok: true; telegram_id: number };
}

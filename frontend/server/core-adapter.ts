const HEALTH_TIMEOUT_MS = 10_000;
const CORE_REQUEST_TIMEOUT_MS = 8_000;

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
      live_price: number | null;
      pnl_live_pct: number;
      status: string;
      source_type: string;
      targets: Array<{ price: number; percent: number; hit: boolean }>;
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
};
export type CoreHistoricalRecord = { public_ref: string; asset: string | null; side: string | null; status: string; trust_tier: string; eligible_for_ranking: boolean; decision_timestamp: string | null };
export type CoreAnalystReadModel = { analyst_code: string | null; public_ref: string | null; public_name: string; sample_size: number; win_rate_pct: number; total_pnl_pct: number; max_drawdown_pct: number; active_recommendations: number; risk_exposure_pct: number; eligible_for_ranking: boolean; freshness_days: number | null };
export type CoreR5Readiness = { status: "HOLD"; reasons: string[]; commercial_enabled: false; copy_trading_enabled: false; execution_controls: { auto_trade_enabled: boolean; trade_live_enabled: boolean }; observation: { started_at: string | null; required_hours: number; elapsed_hours: number; remaining_hours: number; complete: boolean }; snapshot: { outbox_backlog: number; owner_review_backlog: number; replay_backlog: number }; as_of: string };
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
    if (!response.ok) throw new Error(`CAPITALGUARD_CORE_API_${response.status}`);
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

export async function coreGetTraderHistorical(telegramId: number, fetchImpl: typeof fetch = fetch, env = process.env): Promise<{ as_of: string; items: CoreHistoricalRecord[] }> {
  if (!Number.isSafeInteger(telegramId) || telegramId <= 0) throw new Error("CAPITALGUARD_TMA_TELEGRAM_ID_REQUIRED");
  const result = await coreReadOnlyFetch(`/api/webapp/read-models/trader/${telegramId}/historical`, {}, fetchImpl, env);
  if (!result || typeof result !== "object" || (result as { ok?: unknown }).ok !== true || !Array.isArray((result as { items?: unknown }).items)) throw new Error("CAPITALGUARD_CORE_HISTORICAL_INVALID");
  return result as { as_of: string; items: CoreHistoricalRecord[] };
}

export async function coreGetAnalysts(fetchImpl: typeof fetch = fetch, env = process.env): Promise<{ as_of: string; items: CoreAnalystReadModel[] }> {
  const result = await coreReadOnlyFetch("/api/webapp/read-models/analysts", {}, fetchImpl, env);
  if (!result || typeof result !== "object" || (result as { ok?: unknown }).ok !== true || !Array.isArray((result as { items?: unknown }).items)) throw new Error("CAPITALGUARD_CORE_ANALYSTS_INVALID");
  return result as { as_of: string; items: CoreAnalystReadModel[] };
}

export async function coreGetR5Readiness(actorTelegramId: number, fetchImpl: typeof fetch = fetch, env = process.env): Promise<CoreR5Readiness> {
  if (!Number.isSafeInteger(actorTelegramId) || actorTelegramId <= 0) throw new Error("CAPITALGUARD_TMA_TELEGRAM_ID_REQUIRED");
  const result = await coreReadOnlyFetch(query("/api/webapp/owner/r5-readiness", { actor_telegram_id: String(actorTelegramId) }), {}, fetchImpl, env);
  if (!result || typeof result !== "object" || (result as { ok?: unknown }).ok !== true || (result as { status?: unknown }).status !== "HOLD") throw new Error("CAPITALGUARD_CORE_R5_READINESS_INVALID");
  return result as CoreR5Readiness;
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

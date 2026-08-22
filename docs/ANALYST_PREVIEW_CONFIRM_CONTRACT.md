# Analyst Recommendation Preview & Confirm Contract

## Scope

This Core-owned contract supports an analyst-facing review flow for recommendations. It is a non-commercial capability: it does not enable billing, copy trading, automated trading, or live trade execution.

| Step | Endpoint | Persistence | Responsibility |
|---|---|---:|---|
| Preview | `POST /api/webapp/recommendations/preview` | No | Authenticate the Telegram Mini App actor, obtain Core pricing for MARKET orders, validate financial geometry, and validate active channel ownership. |
| Confirm | `POST /api/webapp/recommendations/confirm` | Yes | Create exactly one Core recommendation, enqueue only Core Publication Outbox deliveries, and retain the completed response for idempotent replay. |

## Required Input Boundary

The request contains the existing typed `WebAppSignal` fields. Confirm additionally requires an `idempotency_key` of 16–128 characters. Core, not React or the Web adapter, is authoritative for analyst authorization, active-channel ownership, financial validation, effective MARKET entry, recommendation identity, and publication state.

## Preview Response

On success Preview returns `ok: true` and a `preview` object with `schema_version`, `mode: PREVIEW`, normalized financial values, optional `live_price`, and publication state `NOT_QUEUED`. Preview never writes `Recommendation`, `PublicationDelivery`, or `WebCommandAudit`.

## Confirm Response and Replay

Confirm returns `ok`, `entity_type: RECOMMENDATION`, `public_ref`, `publication`, and `replayed`. It records the response in `WebCommandAudit`. Reuse of the same idempotency key with the same canonical payload returns the saved response; reuse with a different payload is rejected. Confirm uses `CreationService.create_and_publish_recommendation_async`, so selected valid channels are represented only as queued Core Outbox deliveries.

## Safety Invariants

The legacy `POST /api/webapp/create` endpoint remains unchanged for compatibility while clients migrate. The contract must not invoke `background_publish_and_index`, direct Telegram publication, or frontend-side financial calculations. `BILLING_ENABLED`, `COPY_TRADING_ENABLED`, `AUTO_TRADE_ENABLED`, and `TRADE_LIVE_ENABLED` remain fail-closed.

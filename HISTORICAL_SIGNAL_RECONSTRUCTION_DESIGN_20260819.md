# Historical Signal Reconstruction — Architectural Design

## Decision

Historical channel recommendations will be stored in a separate, non-actionable historical domain. They must not be inserted directly as live `Recommendation` rows or counted as verified analyst performance merely because a message can be parsed.

The historical domain contains four layers:

| Layer | Entity | Purpose |
|---|---|---|
| Evidence | `historical_signal_evidence` | Raw message/export/admin file and immutable hash, with acquisition method and source timestamps |
| Signal | `historical_signals` | Normalized recommendation fields parsed from evidence, linked to channel and optionally verified analyst |
| Timeline | `historical_signal_events` | Created/activated/TP/SL/update/closed events with event time, price, evidence, and confidence |
| Attribution | `historical_signal_attributions` | Analyst/channel ownership and user/admin follow relationships, each with proof and confidence |

## Source-of-truth and trust tiers

| Tier | Meaning | Public analyst ranking |
|---|---|---|
| `VERIFIED_LIVE` | Captured by the bot at the time of the channel post | Eligible |
| `VERIFIED_HISTORY` | Retrieved from an authorized Telegram user history or official export with message ID/date | Eligible after market replay checks |
| `RECONSTRUCTED` | Parsed historical message plus point-in-time market evidence | Eligible only with explicit weighted policy |
| `MANUAL_ATTESTED` | Entered or confirmed by an admin without independent source | Never eligible by default |
| `UNVERIFIED` | Missing timestamp, source, ownership, or market evidence | Excluded |

## Temporal replay rules

Every reconstructed event has `event_timestamp`, `market_as_of`, `data_source`, and `fetched_at`. A market observation can support an event only when it is point-in-time data at or before the event decision time. Current prices or later candles cannot be used to prove a past TP/SL event. Missing or conflicting market data produces `UNVERIFIED`, not an inferred result.

The system will distinguish `MESSAGE_TIME`, `EVENT_TIME`, `MARKET_AS_OF`, and `INGESTED_AT`; these must never be collapsed into one `created_at` field.

## Analyst and channel attribution

A historical signal is attributed to an analyst only when the channel is linked to a known owner through the current `Channel`/`ChannelCatalog` relationship or a separately recorded verified ownership proof. If ownership is unknown, the signal remains in a channel-level historical wallet and does not inflate the analyst profile.

If a trader or administrator follows a historical signal, the relationship is recorded separately from the historical signal itself. A user follow may create a non-actionable historical tracking record and may be displayed in the user's history, but it does not rewrite the original channel timeline.

## Deduplication

The immutable deduplication key is derived from `(source_channel_identity, telegram_message_id, message_revision)` when available. For exports without a message ID, use a normalized-content hash plus source timestamp and channel identity. A second import must be idempotent and must not create a second signal or duplicate timeline events.

## Product behavior

Historical signals are read-only by default. They can be inspected, filtered by channel/analyst/date/asset/status/trust tier, and linked to a trader's historical tracking record. They cannot trigger live alerts, price streaming, Outbox publication, or automatic execution. Any promotion to an operational recommendation requires an explicit new live recommendation action and must not reuse historical event timestamps as current state.

## Rollout

1. Build evidence, signal, event, and attribution tables with immutable hashes and trust fields.
2. Add an admin-only dry-run import manifest and validation report; do not ingest arbitrary history directly into production.
3. Implement deterministic parser/replay contracts with missing-data and future-leak tests.
4. Add channel/analyst/user wallet queries with confidence separation.
5. Add a feature flag and keep historical results out of public ranking until Gate approval.

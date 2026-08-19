# ID Architecture Audit — CapitalGuard Pro v2

## Current-state finding

The current implementation uses global database primary keys per table. `recommendations.id` is globally unique within the recommendations table, `user_trades.id` is globally unique within user_trades, `channels.id` is globally unique within channels, and `watched_channels.id` is unique for a user/channel watch row. The Telegram channel numeric ID is separately unique as a Telegram platform identifier.

The current Telegram `record_id` is presentation metadata that aliases the underlying row ID; it is not a per-analyst, per-trader, or per-channel sequence. `source_type`, `analyst_id`, `user_id`, `source_recommendation_id`, `channel_id`, and `watched_channel_id` already provide relational dimensions for filtering.

## Important distinction

`Channel.id` currently represents an analyst-owned channel configuration, while `WatchedChannel.id` represents a user-specific watch relation. Neither is a canonical identity for a Telegram channel across all users. A future channel catalog keyed by `telegram_channel_id` is therefore recommended before implementing channel-local numbering.

## Existing publication identity

Publication Outbox delivery identity is already scoped by recommendation, Telegram channel, operation, and event key. This should remain the delivery/idempotency identity. Human-facing scoped IDs should not replace the canonical delivery key.

## Core design implication

Do not replace the existing global primary keys with local numbers. Add a layered identity model: immutable internal primary key, opaque/global public reference, and human-readable scoped sequence references for analyst, trader, source channel, and publication channel. Scoped sequences must be allocated transactionally and never by `MAX(id)+1`.

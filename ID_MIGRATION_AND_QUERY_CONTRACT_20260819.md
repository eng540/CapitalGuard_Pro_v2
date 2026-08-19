# ID Migration and Query Contract

## Migration principles

The migration must be additive and backward-compatible. Existing integer primary keys and foreign keys remain unchanged. New public and scoped fields are introduced, backfilled, indexed, and only then adopted by the UI and API. The old row ID remains available internally and can be accepted temporarily in admin/debug lookups.

## Proposed schema additions

| Table | New fields or relation | Constraint |
|---|---|---|
| `users` | `public_ref`, `user_code` | Unique on each; code format `USR-######` |
| `analyst_profiles` | `analyst_code` | Unique; format `AN-######` |
| `recommendations` | `public_ref`, `analyst_sequence` | `UNIQUE(public_ref)`, `UNIQUE(analyst_id, analyst_sequence)` |
| `user_trades` | `public_ref`, `trader_sequence` | `UNIQUE(public_ref)`, `UNIQUE(user_id, trader_sequence)` |
| `channel_catalog` | `id`, `public_ref`, `channel_code`, `telegram_channel_id` | Unique Telegram ID, public ref, and channel code |
| `recommendation_channel_refs` | `recommendation_id`, `channel_id`, `channel_sequence` | `UNIQUE(channel_id, channel_sequence)` and one row per recommendation/channel |
| `scoped_id_counters` | `scope_type`, `scope_id`, `next_value` | Primary key `(scope_type, scope_id)` |

The relation `recommendation_channel_refs` is necessary because one recommendation can be published to multiple channels. A single `channel_sequence` column on `recommendations` cannot represent that fact correctly.

## Backfill order

First create and populate `users.user_code`, `analyst_profiles.analyst_code`, and `channel_catalog` from existing users, analyst profiles, and distinct Telegram channel IDs. Next backfill `recommendations.public_ref` and `analyst_sequence` ordered by `(analyst_id, created_at, id)`. Then backfill `user_trades.public_ref` and `trader_sequence` ordered by `(user_id, created_at, id)`. Finally create channel-reference rows from existing `Recommendation.channel_id` and publication-delivery/channel relationships where the channel mapping is reliable.

Backfill must be idempotent and should write an audit report with counts, duplicate checks, null checks, and unmapped channel rows. No existing recommendation or trade may receive a different public reference after the first successful backfill.

## Query and filter contract

Every list endpoint or Telegram view should support the same semantic filters, even if the UI exposes only a subset:

| Filter | Meaning |
|---|---|
| `entity_type` | `recommendation`, `user_trade`, `channel`, or `publication` |
| `owner_type` and `owner_id` | analyst, trader, or channel scope |
| `source_type` | analyst recommendation, direct input, or tracked recommendation |
| `channel_ref` | canonical channel code or public reference |
| `status` | pending, active, watchlist, or closed |
| `created_from`, `created_to` | UTC time range |
| `public_ref` | exact global lookup |
| `scoped_sequence` | lookup only together with its scope |

A scoped number must never be accepted without its scope. `R-000123` alone is invalid; `AN-0042/R-000123` or `CH-0021/R-000087` is valid. This prevents collisions and support mistakes.

## Stable ordering and pagination

Lists should order by `created_at DESC, id DESC` or use a cursor containing both values. Sorting by scoped sequence alone is insufficient after imports or historical backfills. Pagination must apply filters before limiting results, and the same filter contract must be used for Hub, history, exports, and admin search.

## Authorization contract

A trader may read their own `user_trades` and the public metadata of recommendations they are permitted to follow. An analyst may read their own recommendations, their owned channel references, and aggregate public performance views permitted by product policy. An administrator may search across scopes. A public channel view must not reveal private trader IDs, Telegram user IDs, or internal database keys.

## Compatibility aliases

During the transition, responses may include both `legacy_id` and the new identity object:

```json
{
  "legacy_id": 4812,
  "public_ref": "REC-01JQ...",
  "scope": {"type": "analyst", "code": "AN-0042", "sequence": 123},
  "display_ref": "AN-0042/R-000123"
}
```

The UI should migrate to `display_ref` and `public_ref`; internal callbacks may continue accepting legacy integer IDs until all active keyboards and messages have expired.

## Acceptance tests

The implementation is not complete until tests prove that two analysts can both have `R-000001`, two traders can both have `T-000001`, and two channels can both have their own `R-000001` without collisions. Additional tests must cover concurrent allocation, rollback gaps, duplicate Telegram channel ingestion, multi-channel publication, authorization boundaries, exports, callback lookup, and legacy-ID compatibility.

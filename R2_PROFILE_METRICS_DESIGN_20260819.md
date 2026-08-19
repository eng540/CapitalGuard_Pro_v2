# R2 Profile and Metrics Design

## Decision

R2 will extend the existing `AnalystProfile` and `AnalystStats` models additively. It will not create a parallel analyst directory or duplicate recommendation performance logic.

## Profile contract

`AnalystProfile` will support `public_name`, `bio`, `specialty_market`, `strategy_style`, and `profile_updated_at`. Only the analyst owner may edit these fields. Public discovery will expose the fields only when `is_public=true`.

## Measurement contract

All analyst performance queries will accept an optional UTC window using `created_at` as the cohort timestamp and `closed_at` as the outcome timestamp. A result will include `window_days`, `as_of`, `data_freshness_at`, and `freshness_days`. Ranking requires the minimum sample size and excludes shadow, invalidated, and non-closed outcomes.

## Exposure contract

The current active-count exposure proxy remains available for backward compatibility but is no longer the primary risk metric. The new risk exposure is the sum of active recommendation risk percentages, calculated as `abs(entry-stop_loss)/entry*100` per active recommendation. It is explicitly labeled as an approximate percentage-risk exposure and will not be presented as capital allocation.

## Comparison contract

The existing channel comparison service remains the single source of truth. It will add optional `market`, `asset`, `created_from`, `created_to`, and `window_days` filters and include comparison scope metadata. The Telegram command will expose these filters conservatively without claiming investment advice.

## Safety and rollout

All schema changes are additive with backfill-safe defaults. The first implementation will provide analyst self-editing and read-only discovery metrics. No payment, subscription entitlement, copy trading, or automatic execution is included.

# R2 Identity Implementation Notes

**Branch:** `feature/id-architecture-r2-20260819`  
**Scope:** Layered identity foundation, canonical channel catalog, per-channel recommendation references, and shared search filters.

## Implemented

The implementation preserves all existing integer primary keys and legacy callback identifiers. It adds nullable, backfillable public references to users, recommendations, and user trades; user and analyst display codes; analyst-local recommendation sequences; trader-local trade sequences; and a transactional counter table.

A canonical `channel_catalog` now represents one Telegram channel across analyst ownership rows and user watch rows. Existing `Channel` and `WatchedChannel` records retain their Telegram IDs and receive a nullable catalog link. A recommendation-to-channel relation allocates a local recommendation sequence per catalog channel, which correctly models a recommendation published to multiple channels.

The publication outbox continues to use its existing recommendation/channel/operation/event idempotency key. On enqueue it now ensures a channel catalog row and a stable recommendation-channel reference without changing delivery semantics.

The shared `IdentityQueryService` supports entity type, owner, scope code, source type, channel code, status, asset, side, time range, public reference, scoped sequence, stable ordering, and bounded limits. It is designed for reuse by Hub, History, exports, and future admin search.

## Migration sequence

| Revision | Purpose |
|---|---|
| `20251205_add_layered_identity` | Add user/recommendation/trade identity fields, counters, and deterministic backfill |
| `20251206_add_channel_catalog` | Add canonical channel table and link existing channels/watch rows |
| `20251207_add_recommendation_channel_refs` | Add per-channel recommendation references and historical backfill |

The migrations are additive. Existing IDs remain valid, and nullable fields allow a controlled rollout. Production migration execution must be observed on Railway before changing all external links to public references.

## Quality evidence

Local evidence after implementation: **95 tests passed, 1 skipped**, `compileall` passed, migration files compile, critical Flake8 selection passed, Bandit high-severity scan passed, and `git diff --check` passed. Existing pytest warnings and the one pre-existing skipped parser test remain unchanged.

A full Alembic upgrade was attempted against SQLite with `PYTHONPATH=src`; it was blocked in the repository's pre-existing baseline migration because SQLite rejects its `DEFAULT now()` DDL. This is a baseline portability limitation, not an error in the new migration files. Railway production uses PostgreSQL, and the new migrations use portable SQL for their own DDL/backfill paths.

## Release guardrails

No payment, subscription entitlement, copy trading, or automatic execution behavior is introduced. Before merge, CI must run the existing test, critical lint, Bandit, pip-audit, compile, and Alembic-head checks. After merge, Railway health, metrics, migration completion, outbox queue, and controlled Telegram role tests must be verified.

# ID Hierarchy and Scoped Identity Design

## Executive recommendation

The idea is logically sound, but local numbering must complement—not replace—the current global database identifiers. The recommended model has four layers: an immutable internal primary key, an opaque global public reference, a stable human-readable owner code, and a scoped sequence for the relevant owner or channel.

## Identity layers

| Layer | Example | Purpose | Publicly shown |
|---|---|---|---|
| Internal row key | `recommendations.id = 4812` | Joins, foreign keys, database performance | No |
| Global public reference | `REC-01JQ...` | Unambiguous cross-system lookup, deep links, audit logs | Yes |
| Owner/entity code | `AN-0042`, `USR-0138`, `CH-0021` | Stable display and filtering of analyst, user, or channel | Yes |
| Scoped sequence | `AN-0042/R-000123`, `USR-0138/T-000044` | Human-friendly numbering within an analyst or user scope | Yes |

The global public reference should be an opaque UUIDv7/ULID-like identifier generated once at creation. It should not expose Telegram IDs, database row counts, or authorization information. The existing integer primary keys remain internal and continue to support foreign keys.

## Recommended scopes

### Analyst recommendations

Each recommendation receives a global `public_ref` and an `analyst_sequence` allocated within the analyst scope. The uniqueness rule is `UNIQUE(analyst_id, analyst_sequence)`. The display form is `AN-0042/R-000123`, while the global form is `REC-<opaque-ref>`.

This satisfies the requested behavior: Analyst 1 can have recommendations 1, 2, 3, and Analyst 2 can also have recommendations 1, 2, 3. The numbers are not globally unique by themselves; the analyst code makes them unambiguous.

### Trader records

Each `UserTrade` receives a global `public_ref` and a `trader_sequence` allocated within the trader scope. The uniqueness rule is `UNIQUE(user_id, trader_sequence)`. Direct logs and tracked signals use the same trader sequence space so the trader has one coherent portfolio numbering, while `source_type` distinguishes `DIRECT_INPUT` from `TRACKED_RECOMMENDATION`.

The display form is `USR-0138/T-000044` plus a source badge. A tracked trade additionally shows its origin recommendation and channel references.

### Channels

A canonical channel identity should be separated from channel ownership and watch relationships. The current `Channel` row represents an analyst-owned configuration, while `WatchedChannel` represents a user-specific watch relation; neither should become the universal identity of a Telegram channel.

Introduce a canonical channel catalog keyed by the unique Telegram `telegram_channel_id`, with a stable `CH-xxxx` code. Analyst-owned configuration rows and user watch rows should reference the canonical channel. This avoids creating a different channel identity for each watcher.

### Channel-local recommendation numbering

A recommendation may be published to multiple channels. Therefore, a single `channel_sequence` column on `recommendations` is incorrect: one recommendation would need several numbers. Create a semantic relation such as `recommendation_channel_refs` with one row per `(recommendation, canonical_channel)` and allocate `channel_sequence` within that channel. The uniqueness rule is `UNIQUE(channel_id, channel_sequence)`.

A recommendation published to two channels may consequently display, for example, `CH-0021/R-000087` and `CH-0034/R-000112`. The existing Publication Outbox remains the delivery/idempotency ledger and should not be overloaded with human-facing sequence semantics.

## Global ID strategy

A single central numeric counter is not recommended as the primary user-facing identifier. It is predictable, leaks volume, and creates unnecessary contention. The practical global identity is an opaque reference with a type prefix, such as `REC-01JQ...`, `TRD-01JQ...`, or `CH-01JQ...`; the opaque portion is unique and stable.

If the product later requires a hard database-enforced identity across every entity type, add a `global_objects` registry with a one-to-one row per public entity. This is an optional R4/R5 capability, not a prerequisite for Alpha or R2.

## Allocation rules

Scoped sequences must be allocated transactionally. Use a counter table keyed by `(scope_type, scope_id)` and lock the counter row with `SELECT ... FOR UPDATE`, or use a dedicated counter mechanism. Never calculate a new number with `MAX(sequence) + 1`. Numbers must never be reused after deletion, and gaps caused by rollback or failed publication are acceptable because auditability is more important than visual continuity.

## Display examples

| Object | Recommended compact card identity |
|---|---|
| Analyst recommendation | `🧠 Analyst • REC-01JQ... • AN-0042/R-000123` |
| Same recommendation in Channel 21 | `Channel CH-0021/R-000087` |
| Direct trader log | `📝 Trader Log • TRD-01JR... • USR-0138/T-000044` |
| Tracked trader signal | `📡 Tracked • TRD-01JS... • USR-0138/T-000045 • source REC-01JQ...` |

The UI should show the compact scoped identity first and place the full global reference behind copy/details actions. This keeps cards readable while retaining exact auditability.

## Why this is better than local IDs alone

Local IDs alone are ambiguous outside their scope, can collide in support messages, and are unsafe as API identifiers. Global references alone are unhelpful to humans. Combining both gives the system strong joins and auditability while preserving fast human navigation and role-aware filtering.

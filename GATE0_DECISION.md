# Gate 0 Decision

## Decision

**Decision: NO-GO for Alpha, production, payments, and Copy Trading.**  
**Decision: CONDITIONAL GO for continued local/Staging preparation and documentation.**

## Basis

The branch has a green local regression baseline: `61 passed, 1 skipped`, successful `compileall`, `pip-audit`, and Bandit High, plus one Alembic head and passing Dedup/API integration checks. However, the operational evidence required by Phase 0 is not complete because the execution environment does not provide Docker, PostgreSQL/Redis clients, or configured Telegram/AI/Staging credentials.

The missing evidence includes empty-database PostgreSQL migration, upgrade of an existing anonymized dataset, backup/restore with measured RTO/RPO, complete Redis/Telegram/AI startup, post-deployment smoke, and controlled WebSocket degraded-mode testing. A SQLite migration attempt is not accepted as a substitute because the historical baseline is PostgreSQL-oriented and fails on `now()`/JSONB portability.

## Exit criteria to change the decision

| Criterion | Required evidence | Current state |
|---|---|---|
| Clean PostgreSQL migration | Empty DB `alembic upgrade head`, one head, schema check | NOT VERIFIED |
| Existing-data migration | Before/after counts, FK/status reconciliation | NOT VERIFIED |
| Recovery | Backup + restore timestamps, RTO/RPO, checksum/row checks | NOT VERIFIED |
| Full startup | Redis/Telegram/AI startup logs and `/health=200` | NOT VERIFIED |
| E2E | Forward→Parse→Review→Confirm→Watchlist→Activate→Alert→Close | PARTIAL/local only |
| Security | Secrets/RBAC/webhook/PII checklist with negative tests | PARTIAL |
| Smoke | Staging health, auth, webhook, portfolio, metrics | NOT VERIFIED |
| Regression | Full pytest, security, compile, migration checks | VERIFIED locally |

## Allowed work under Conditional GO

The team may complete documentation, test harnesses, migration portability analysis, deterministic unit/integration tests, observability definitions, and staging automation. The team must not expose the product to real users, activate payment collection, enable public analyst marketplace, or execute real trades.

## Required owner action

Provide a PostgreSQL/Redis/Telegram sandbox or staging environment and credentials through the approved secret channel. After the evidence run, reopen this decision and choose `GO R1 Development`, `GO Alpha`, or remain `NO-GO`.

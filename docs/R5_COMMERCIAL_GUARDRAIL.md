# CapitalGuard R5 Commercial Guardrail

## Current release position

The product is operating as a controlled, non-commercial system. `BILLING_ENABLED=false` and `COPY_TRADING_ENABLED=false` are explicit Core defaults, no payment provider is connected, and Copy Trading remains absent from the runtime and user interfaces. A successful engineering deployment, a green CI run, or an R4 operational checkpoint does not change these restrictions.

## R5 decision contract

`ReleaseStabilityGateService` is deliberately conservative. It returns `HOLD` when tests fail, the publication outbox is not drained, a historical-to-live leak is detected, financial conflicts are not reviewed, or historical replay is still pending. It returns `PASS` only for a clean evidence snapshot; even then, `commercial_enabled` and `copy_trading_enabled` are always `false`.

| Gate evidence | Required before a separate commercial authorization |
|---|---|
| Quality | CI, Core tests, Web tests, and production build pass for the release candidate. |
| Data isolation | No historical evidence, replay result, or review command creates a live recommendation, `UserTrade`, or publication delivery. |
| Operations | Health checks, event feed, owner audit trail, migration evidence, and deployment logs are retained. |
| Recovery | PostgreSQL backup and restore drill has an owner-approved RTO/RPO record. |
| Retention | A defined stability observation window has no unresolved critical delivery or reconciliation incidents. |
| Commercial review | Legal, pricing, payment-provider, privacy, and support runbooks are approved in a dedicated decision. |

## Prohibited until separately authorized

No automatic execution, trade copying, broker credential collection, customer charging, payment capture, subscription renewal, or provider webhook may be enabled under this gate. Any future commercial work starts from a new approved decision, not by toggling an unreviewed environment variable.

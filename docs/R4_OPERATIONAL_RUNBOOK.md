# CapitalGuard R4 Operational Runbook

## Purpose and release scope

This runbook governs the paired Railway services: **Core** owns financial, lifecycle, historical-evidence, and publication data; **Web** owns sessions, preferences, and presentation-only state. The production system currently operates as a controlled, non-commercial release. `BILLING_ENABLED=false` remains mandatory, and there is no Copy Trading or automatic execution path.

## Health and service ownership

| Service | Health endpoint | Expected response | Owner data boundary |
|---|---|---|---|
| Core | `https://capitalguardprov2-production-b4ea.up.railway.app/health` | `{"status":"ok"}` | Recommendations, trades, lifecycle, historical evidence, outbox, owner commands. |
| Web | `https://capitalguardprov2-production-8208.up.railway.app/health` | `{"status":"ok","service":"capitalguard-web"}` | Telegram sessions, Web preferences, saved comparisons, Web audit events. |

The Web Admin page must show **Ready** before an owner treats an empty Owner Review queue as a real empty queue. An `Error` state must not be replaced with preview records.

## Operational objectives

| Signal | Initial operating target | Breach response |
|---|---|---|
| Core and Web health | Both healthy in scheduled manual checks and after each deployment. | Stop operational changes; inspect Railway deploy logs and roll back the affected service if the previous revision is known-good. |
| Core read path | No unresolved `401`, `403`, or `5xx` from the server-to-server adapter during owner verification. | Recheck `API_KEY` in Core, `CAPITALGUARD_CORE_API_KEY` in Web, and `CAPITALGUARD_OWNER_TELEGRAM_ID`; never place these in `VITE_` variables. |
| Publication outbox | No sustained `PENDING`, `PROCESSING`, `RETRY`, or `FAILED` backlog. | Review the operations feed, delivery error, channel configuration, and retry policy before manual retry. |
| Historical integrity | No historical record becomes a live recommendation, `UserTrade`, or publication delivery. | Treat as severity 1; suspend ingestion, preserve audit evidence, and investigate before any restart. |
| Owner commands | Every review or evidence action has an idempotency key and a Core audit record. | Do not repeat manually until the existing command audit record has been checked. |

## Severity and response

| Severity | Examples | First action | Recovery evidence |
|---|---|---|---|
| S1 | Live/historical boundary violation, credential disclosure, unintended trade execution. | Disable impacted command path, rotate the affected secret, preserve logs and audit IDs. | Owner incident record, corrected deployment, regression test. |
| S2 | Core unavailable, migration failure, sustained publication failure. | Halt deployments, inspect logs, roll back to last known-good revision. | Health response restored and affected queue reconciled. |
| S3 | Empty Admin feed, stale UI, non-critical channel retry. | Confirm Ready/Error state, inspect safe server log code, correct configuration or deploy. | UI and event-feed verification screenshot. |

## Deployment and rollback

Before production deployment, CI, build, and migration checks must be green. Deploy `main` only. After deployment, confirm `db:migrate`/Alembic output, both health endpoints, Telegram Mini App login, and the Admin Ready state. If a release fails, use Railway deployment history to redeploy the last known-good revision; do not edit historical Alembic migrations to resolve a production issue.

## Backup and restore drill

The owner must perform this drill against a **separate restore target**, never by overwriting either production database.

1. Record a timestamp, source service, and expected RPO/RTO target.
2. Create a PostgreSQL backup for Core and a separate PostgreSQL backup for Web.
3. Restore each into a non-production PostgreSQL service.
4. Run Core Alembic and Web Drizzle migrations on the restore targets.
5. Verify Core health, Web health, Telegram auth configuration, and that historical records remain isolated.
6. Record duration, checksum or row-count sanity evidence, operator, issues, and corrective actions in the owner incident log.

## R4/R5 evidence checklist

| Evidence | Status required before R5 commercial review |
|---|---|
| Core and Web health verification after the current release | Complete for the current operational slice. |
| Telegram-first session and owner RBAC verification | Complete for the current operational slice. |
| Owner Review/Evidence audit and empty/error state verification | Complete for the current operational slice. |
| Operations-feed route and protected unauthenticated probe | Complete for the current operational slice. |
| Core and Web PostgreSQL backup-and-restore drill | Pending owner-operated evidence. |
| Defined observation window with no unresolved S1/S2 incident | Pending elapsed-time evidence. |
| Formal commercial, legal, privacy, support, and pricing approval | Pending separate product decision. |

Until every pending row is evidenced and approved, R5 is **HOLD**. A technical `PASS` in the release-gate service is not commercial authorization.

## Current test-environment decision

The owner has deferred the PostgreSQL Restore Drill while the Railway deployment is treated as a test environment. This does not block feature development, Telegram authentication, read-only Core views, historical review, or the Owner Review workflow. It does block a transition of R5 out of **HOLD** and blocks any future commercial enablement. When resumed, the drill must use separate Core and Web restore targets and record the measured RTO/RPO evidence described above.

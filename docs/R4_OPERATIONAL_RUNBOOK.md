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

### Latest masked evidence

The owner completed a separate Core and Web logical Restore Drill on **21 August 2026**. Both restores, Core Alembic verification, Web Drizzle verification, and masked table-integrity checks passed. The observed RTO was approximately **45 seconds** and the source snapshot age (RPO) was **under five minutes**. The temporary targets were dropped after verification, while production Core and Web health remained `ok`. See [`RESTORE_DRILL_EVIDENCE_20260821.md`](./RESTORE_DRILL_EVIDENCE_20260821.md).

## Secret rotation evidence

The owner completed a masked rotation of the Core API service key, Web JWT secret, and Web PostgreSQL credentials on **22 August 2026**. The revoked credential received HTTP `401`, the current credential received HTTP `200`, and both Core and Web health endpoints remained `ok`. No secret values, database URLs, or identifiers are recorded. See [`SECRET_ROTATION_EVIDENCE_20260822.md`](./SECRET_ROTATION_EVIDENCE_20260822.md).

## R4/R5 evidence checklist

| Evidence | Status required before R5 commercial review |
|---|---|
| Core and Web health verification after the current release | Complete for the current operational slice. |
| Telegram-first session and owner RBAC verification | Complete for the current operational slice. |
| Owner Review/Evidence audit and empty/error state verification | Complete for the current operational slice. |
| Operations-feed route and protected unauthenticated probe | Complete for the current operational slice. |
| Core and Web PostgreSQL backup-and-restore drill | Complete — masked separate-target drill passed on 21 August 2026; measured RTO ≈45s and RPO <5m. |
| Defined observation window with no unresolved S1/S2 incident | Pending elapsed-time evidence. |
| Formal commercial, legal, privacy, support, and pricing approval | Pending separate product decision. |

Until every pending row is evidenced and approved, R5 is **HOLD**. A technical `PASS` in the release-gate service is not commercial authorization.

## Current operational decision

The PostgreSQL Restore Drill is complete for the current Core/Web schemas. This closes the backup-and-restore evidence item but does not make the release commercial. Feature development, Telegram authentication, read-only Core views, historical review, and Owner Review remain non-commercial, while every other unresolved R4/R5 evidence item still governs its own gate.

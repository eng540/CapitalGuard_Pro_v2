# CapitalGuard Restore Drill Evidence — 21 August 2026

## Scope and safety boundary

This owner-operated drill restored the **Core PostgreSQL** and **Web PostgreSQL** datasets separately into temporary, non-production PostgreSQL targets. It did not overwrite either production database, change production variables, enable billing, enable Copy Trading, or permit automatic/live trade execution.

The evidence is intentionally masked: it excludes connection strings, credentials, backup files, customer rows, message bodies, and personally identifiable information.

## Result

| Control | Core PostgreSQL | Web PostgreSQL |
|---|---|---|
| Logical restore to a separate target | PASS | PASS |
| Schema/migration verification | PASS — Alembic revision and schema | PASS — Drizzle schema and tables |
| Integrity verification | PASS — `recommendations`, `user_trades`, `users`, `alembic_version` | PASS — 5/5 tables: `web_users`, `web_preferences`, `web_audit_events`, `web_saved_comparisons`, `web_notification_preferences` |
| Temporary drill-target cleanup | PASS — dropped and fully purged | PASS — dropped and fully purged |

## Recovery measurements

| Metric | Observed result | Interpretation |
|---|---|---|
| Measured RTO | approximately 45 seconds | Time to restore and validate the drill target; this is an observed drill measurement, not a permanent service-level guarantee. |
| Measured RPO | less than 5 minutes | The drill used a near-real-time logical snapshot. |
| Production Core after drill | `ok` | Public `/health` probe verified after owner completion. |
| Production Web after drill | `ok` | Public `/health` probe verified after owner completion. |
| Core API v1 after drill | `ok`, `commercial_mode=noncommercial` | Confirms the operational state remained non-commercial. |

## Acceptance statement

The backup-and-restore component of G0/R4 is **PASS** for the recorded 21 August 2026 drill. The result proves a separate logical restore path for the present Core and Web schemas and supports the retained RTO/RPO measurements above.

It does **not** close the remaining G0, R1, R2, R3-C, R4, or R5-C requirements. In particular, it is not commercial authorization, does not establish a payment workflow, and does not authorize Copy Trading or live execution.

## Follow-up cadence

Repeat a masked drill after material schema or backup-policy changes and before any commercial-release decision. Store only masked evidence in the repository; retain encrypted dump files and connection details outside Git according to the operational retention policy.


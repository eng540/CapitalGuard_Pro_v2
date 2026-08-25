# Urgent Runtime Repair and Forward UX Study

## Scope

This branch contains operational hotfixes only. No Telegram UX messages, handlers, navigation, or product flow were changed. The forward-review UX work is documented separately as a study and is intentionally not implemented in this branch.

## Applied operational fixes

1. Added `20260825_repair_historical_market_evidence_sequence` after the current Alembic head. On PostgreSQL it aligns `historical_market_evidence.id` to `MAX(id) + 1` using the table's owned sequence. The operation is forward-only and preserves all rows; non-PostgreSQL dialects are no-ops.
2. Added an isolated regression test for the sequence migration.
3. Propagated `error_code` from image parsing through `ParsingManager`, allowing provider rate-limit and provider-unavailable responses to be classified consistently.
4. Added regression tests proving image provider failures produce HTTP 503 with `Retry-After` rather than HTTP 200 with a hidden failure.
5. Set `httpx` and `httpcore` loggers to WARNING in AI service startup so INFO request logs cannot emit full Telegram file URLs. Application telemetry remains responsible for redaction.
6. Set Core API `runtime_status` to `ready` when startup completes, fixing the observed `startup_complete=true` / `runtime_status=starting` inconsistency.

## Verification

- Full Python suite: 379 passed, 1 skipped, 17 warnings.
- Focused hotfix suite: 23 passed.
- Python compilation: passed.
- Alembic heads: one connected head after the new migration.
- Frontend suite: 87 passed in 25 files.
- `git diff --check`: passed.

## Known deployment action

The migration must run in the production deployment through `alembic upgrade head`. The Telegram Bot Token exposed in previous logs must be revoked and replaced separately; this branch does not contain or modify any secret.

## UX study boundary

The forward flow remains unchanged in this branch. The study recommends a single user-facing decision card backed by existing historical and live services, while keeping temporal classification, owner review, replay gates, provenance, and audit metadata internal. No duplicate handler, service, database, or parallel business logic is introduced by the study.

# Gate 0 Decision

## Decision

**Decision: CONDITIONAL GO to merge Railway deployment/CI hardening PR 179 into `main`.**
**Decision: NO-GO for Alpha, public launch, payments, and Copy Trading.**

## Evidence now available

The owner supplied Railway logs showing PostgreSQL migration execution, migration completion, Supervisor/API startup, application startup completion, Uvicorn listening on `0.0.0.0:8080`, repeated Telegram webhook HTTP 200 traffic, and valid WebApp traffic. External smoke against the public Railway URL also passed: `/health` HTTP 200 with `{"status":"ok"}`, `/metrics` HTTP 200, invalid Telegram initData rejected, and complete TradingView payload without `X-TV-Secret` rejected with HTTP 401. PR 179 CI passed with pytest, critical lint, Bandit High, pip-audit, compileall, and Alembic heads.

## What the log does not prove

The supplied log does not expose migration head, backup timestamp, restore result, RTO, RPO, or a controlled existing-data migration reconciliation. It also contains repeated PTB conversation warnings and expected authentication errors logged at error level for requests without a hash. These are not blockers for merging deployment hardening, but they are follow-up items for R1/operations.

## Criteria

| Criterion | Evidence | State |
|---|---|---|
| PostgreSQL migration command runs | Railway log lines 2–5, `PostgresqlImpl` | VERIFIED for observed deployment |
| Application startup | Railway log lines 9–23 | VERIFIED |
| HTTP health/metrics | External smoke | VERIFIED |
| Invalid Telegram auth rejected | External smoke/log lines 83–85 | VERIFIED |
| TradingView missing secret rejected | External smoke/log lines 89–90 | VERIFIED |
| CI regression/security gates | PR 179 run `32199765567` | VERIFIED |
| Existing-data migration reconciliation | No counts/FK report | NOT VERIFIED |
| Backup/restore and RTO/RPO | No restore evidence | NOT VERIFIED |
| Full E2E Forward→Close on live dependencies | Not proven by supplied log | PARTIAL |
| PTB conversation warnings | Present in lines 12–21 | FOLLOW-UP |

## Merge rule

PR 179 may be marked ready and merged because it contains Railway configuration/CI hardening, smoke automation, documentation, and the already-tested Gate 0 implementation, and the observed Railway deployment has successfully executed PostgreSQL migrations and started the application. The merge must not be interpreted as Alpha or production approval.

## Post-merge restrictions

Do not activate payment collection, public analyst marketplace, or Copy Trading. Before Alpha, complete backup/restore with measured RTO/RPO, existing-data migration reconciliation, a full E2E test on Railway dependencies, and a warning/auth-log cleanup pass.

## Next approved implementation

After merge and a successful post-merge Railway smoke, begin R1 in this order: `/log` using the shared Parser contract, review/edit/confirm state handling, state transition events, Activated-only reports, and funnel metrics. Each feature requires its own PR, tests, and rollback note.

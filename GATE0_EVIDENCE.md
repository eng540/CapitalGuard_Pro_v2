# Gate 0 Evidence

## Baseline

| Item | Value |
|---|---|
| Base | `origin/main` at `c5b1be0d247b6f7e02b427e86950a16148721015` |
| Branch | `implementation/gate-0-r1-foundation-20260819` |
| Commit before docs | `317f74c6ede9543ea90097081fcf1b715c0b60fb` |

## Local evidence

| Command | Result |
|---|---|
| `pytest -q` | 61 passed, 1 skipped, 45 warnings |
| `pytest -q tests/test_dedup_ledger.py tests/test_integration_flow.py` | 6 passed, 1 warning |
| `python3 -m compileall -q src ai_service` | PASS |
| `bandit -q -r src ai_service --severity-level high` | PASS; no High findings |
| `pip-audit -r requirements.txt` | PASS |
| `PYTHONPATH=src alembic heads` | PASS; one head `20251201_add_dedup_ledger` |
| `git diff --check` | PASS before implementation commit |
| API smoke tests | 4 passed |
| Dedup unit tests | 3 passed |

## External/staging evidence

The sandbox has no Docker, PostgreSQL client, Redis client, or configured staging credentials. The following environment keys were absent during this run: `DATABASE_URL`, `REDIS_URL`, `TELEGRAM_BOT_TOKEN`, `AI_SERVICE_URL`, `TV_WEBHOOK_SECRET`, `JWT_SECRET`, and `API_KEY`.

Therefore the following evidence is **NOT VERIFIED** in this run:

1. `alembic upgrade head` on an empty PostgreSQL database.
2. Migration against an existing anonymized PostgreSQL dataset.
3. Backup and restore drill with measured RTO/RPO.
4. Full startup with Redis, Telegram, and AI service.
5. Post-deployment smoke against a Staging URL.
6. Binance reconnect/degraded mode under a controlled external feed.

A local SQLite `alembic upgrade head` attempt was also blocked by the historical baseline migration using PostgreSQL-oriented `now()` defaults and JSONB definitions. This is recorded as a portability limitation; no historical migration was rewritten in this branch.

## Interpretation

Local code quality and regression checks are green for the current branch. Operational Gate 0 is not green because the external recovery, migration, startup, and deployment evidence is unavailable. The correct release state is `NO-GO` for Alpha/production and `CONDITIONAL GO` only for continued local/staging preparation.

## Required next evidence

The owner must provide a PostgreSQL/Redis/Telegram sandbox or staging environment. The next run must attach command output, commit SHA, migration head, health responses, restore timestamps, RTO/RPO, and E2E traces before changing the decision to `GO R1 Development` or `GO Alpha`.

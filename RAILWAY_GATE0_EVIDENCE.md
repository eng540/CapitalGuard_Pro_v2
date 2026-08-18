# Railway Gate 0 Evidence

## Branch

| Field | Value |
|---|---|
| Base | `c90fc88660304db752dbab6d5d5ce45adaf66f39` |
| Branch | `implementation/railway-gate0-hardening-20260819` |
| Target | Railway Docker deployment |

## Implemented checks

| Check | Result |
|---|---|
| Railway config TOML parse | PASS |
| CI workflow YAML parse | PASS |
| Railway smoke script Bash syntax | PASS |
| `pytest -q` | 61 passed, 1 skipped, 45 warnings |
| `compileall` | PASS |
| Bandit High | PASS |
| pip-audit | PASS |
| Alembic heads | PASS; one head `20251201_add_dedup_ledger` |
| Dynamic `PORT` in `__main__` | IMPLEMENTED |
| Railway `/health` config | IMPLEMENTED in `railway.toml` |
| Manual Railway smoke workflow | IMPLEMENTED |

## Not externally verified

No Railway connector, Railway public URL, deployment ID, or staging secrets are available in this session. Therefore this run did not execute `scripts/railway_smoke.sh` against the running service, did not read Railway logs, and did not perform PostgreSQL fresh/existing migration or backup/restore against the real Railway database.

## Required Railway run

After the owner supplies or confirms the non-secret public service URL, run:

```bash
bash scripts/railway_smoke.sh https://<railway-domain>
```

Then record the Railway deployment ID, commit SHA, `/health` response, `/metrics` response, invalid Telegram initData rejection, migration head, startup logs, and rollback target. Keep secrets in Railway Variables/Secrets and do not place them in GitHub Actions inputs or repository files.

## Current decision

`CONDITIONAL GO` for merging Railway configuration and CI hardening into the implementation branch. `NO-GO` for merging into `main`, Alpha, public launch, payment activation, or Copy Trading until the external Railway smoke and database/recovery evidence are attached.

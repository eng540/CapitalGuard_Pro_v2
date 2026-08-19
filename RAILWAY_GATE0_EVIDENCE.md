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
| GitHub Actions CI on PR 179 | PASS; run `32199424084` |
| Critical lint (`E9,F63,F7,F82`) | PASS |
| External `/health` | PASS; HTTP 200 with `{"status":"ok"}` |
| External `/metrics` | PASS; HTTP 200 Prometheus output |
| External invalid Telegram initData | PASS; rejected with HTTP 200 payload `ok=false` and 403 auth detail |
| External TradingView missing secret | PASS; HTTP 401 `Invalid TradingView secret` |

## External smoke evidence

Target URL: `https://capitalguardprov2-production-b4ea.up.railway.app/`
Run date: 2026-08-19
The smoke script passed with `health=200 metrics=200 invalid_initData=rejected`. Detailed checks returned `GET /health` with `{"status":"ok"}`, Prometheus output from `/metrics`, an invalid Telegram initData response with `ok=false`, and a complete invalid TradingView payload without `X-TV-Secret` returning HTTP 401.

## Not externally verified

This public URL does not expose a deployment ID, migration head, Railway logs, PostgreSQL access, or backup/restore evidence. Therefore this run did not perform PostgreSQL fresh/existing migration verification or backup/restore against the Railway database.

## Required Railway run

After the owner supplies or confirms the non-secret public service URL, run:

```bash
bash scripts/railway_smoke.sh https://<railway-domain>
```

Then record the Railway deployment ID, commit SHA, `/health` response, `/metrics` response, invalid Telegram initData rejection, migration head, startup logs, and rollback target. Keep secrets in Railway Variables/Secrets and do not place them in GitHub Actions inputs or repository files.

## Current decision

`GO` for the external HTTP smoke and CI/config hardening evidence. `CONDITIONAL GO` for merging PR 179 into `main` after the owner reviews the deployment impact. `NO-GO` remains for Alpha, public launch, payment activation, and Copy Trading until PostgreSQL migration, backup/restore, and RTO/RPO evidence are attached.

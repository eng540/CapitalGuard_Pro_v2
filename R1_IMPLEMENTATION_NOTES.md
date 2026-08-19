# R1 Trader Core Implementation

## Scope delivered

This branch implements the first R1 trader-core slice without changing the existing recommendation lifecycle.

### Direct input

`/log` accepts the shared quick-command and editor formats already used by `/rec`. It parses Arabic digits and numeric suffixes through the existing parser contract, shows a review card, and requires explicit confirmation before persistence.

### Trade lifecycle

A confirmed direct input creates a trader-owned `UserTrade` in `WATCHLIST` status. The record is marked with `source_type=DIRECT_INPUT`, carries the original text, participates in the existing DedupLedger window, and creates a `UserTradeEvent` named `LOGGED_DIRECT_INPUT`.

### Metrics and reports

The WebApp now exposes authenticated `/api/webapp/performance` and `/api/webapp/funnel` endpoints. Performance uses the existing Activated Portfolio Only policy. Funnel metrics count total logged trades, direct input, forward input, activated trades, closed activated trades, and conversion rates.

## Validation

- Full pytest: 66 passed, 1 skipped.
- R1 targeted tests: 8 passed.
- Critical lint: passed.
- Bandit High: passed; 0 High issues.
- pip-audit: passed.
- compileall: passed.
- Alembic heads: one head, `20251202_add_user_trade_source_type`.
- `git diff --check`: passed.

## Deployment note

The branch includes a PostgreSQL migration adding `user_trades.source_type`. It must be applied through the existing entrypoint on Railway and verified in deployment logs before enabling the direct-input feature for users. The manual Railway workflow on `main` can be used when GitHub Auto-Deploy is unavailable, provided the owner adds the repository secret `RAILWAY_TOKEN`.

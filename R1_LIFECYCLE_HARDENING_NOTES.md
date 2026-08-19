# R1 Lifecycle Hardening

## Scope

This change closes the R1 lifecycle gap identified during Telegram verification. Direct `/log` trades created in `WATCHLIST` can now transition to `ACTIVATED` through the same lifecycle entry point as pending trades. Final SL and TP closures persist `pnl_percentage`, and PriceStreamer includes `WATCHLIST` trades during startup symbol loading so a restart does not leave direct-input trades unmonitored until a later reconciliation.

## Evidence

The Railway log showed successful PostgreSQL migrations, application startup, Telegram webhook delivery, and direct `/log` persistence. The Telegram screenshots confirmed `WATCHLIST`, `DIRECT_INPUT`, duplicate prevention, and a prior SL close. The missing evidence was reliable coverage for the complete lifecycle and restart behavior.

## Validation

- Full pytest: 71 passed, 1 skipped.
- Critical lint: passed.
- Bandit High: passed.
- pip-audit: passed.
- compileall: passed.
- Alembic heads: passed with one head.
- diff check: passed.

## Alpha conditions

Alpha remains conditional on one real Railway smoke scenario proving `WATCHLIST -> ACTIVATED -> CLOSED`, with correct PnL, followed by 24–48 hours of operational observation. Payment, public marketplace, and Copy Trading remain out of scope for R1.

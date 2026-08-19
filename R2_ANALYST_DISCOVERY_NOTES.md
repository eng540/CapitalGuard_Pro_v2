# R2 Analyst Discovery Notes

**Branch:** `feature/r2-analyst-discovery-20260819`  
**Scope:** Read-only analyst discovery and sample-aware profile metrics.

## Implemented

`AnalystDiscoveryService` reads public analyst profiles and non-shadow recommendations. It calculates closed-sample size, Win Rate, cumulative PnL percentage, maximum drawdown proxy, active recommendation count, active asset exposure proxy, public identity, and ranking eligibility.

The default minimum ranking sample is five closed recommendations with valid entry and exit prices. Analysts below that sample are still discoverable when explicitly requested by the service, but they are marked as ineligible for ranking. This prevents a single successful recommendation from presenting as reliable performance.

A new `/find_analysts` command exposes the read-only discovery view. It displays analyst code, sample size, Win Rate, PnL, drawdown, active exposure proxy, and eligibility. `/commands` now lists this capability in the common command directory.

## R2 guardrails

The implementation does not create payment entitlements, follow subscriptions, copy trades, or automatic execution. It does not rank analysts by Win Rate alone. A future comparison screen must preserve the same sample-size and drawdown guardrails and must state the measurement window and data source.

## Quality evidence

After the R2 slice: **97 tests passed, 1 skipped**, compileall passed, touched-module critical Flake8 passed, Bandit high-severity scan passed, and git diff check passed. Existing parser warnings and the pre-existing skipped parser test remain unchanged.

## Next R2 slice

Add analyst profile editing with authorization, explicit measurement windows, exposure based on open notional/risk rather than a count proxy, and comparison filters for channel and market. Only after those are tested should the read-only discovery view be exposed to the controlled Alpha cohort.

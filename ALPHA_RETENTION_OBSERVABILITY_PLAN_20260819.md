# Alpha Retention and Observability Plan

## Purpose

This plan defines the controlled Alpha observation period after R2. It is not a commercial launch and does not enable payments or copy trading.

## Cohorts and observation

The operator should invite 20–50 users in small cohorts, beginning with an internal analyst/trader pair and then expanding only after the first cohort completes the lifecycle smoke test. The observation window is 24–48 hours for operational stability and seven days for early retention.

## Metrics

| Metric | Definition | Pause threshold |
|---|---|---|
| Lifecycle notification completeness | Identified CREATE/UPDATE/REPLY/CLOSE notifications delivered ÷ queued operations | Any unexplained missing notification or repeated failure |
| Outbox backlog | Current PENDING/PROCESSING/RETRY records | Persistent non-zero queue or any FAILED item without triage |
| Duplicate event rate | Duplicate lifecycle events ÷ total lifecycle events | Any duplicate that changes user-visible state |
| Tracking integrity | Tracked UserTrades with source recommendation and source type | Any tracked record without resolvable source identity |
| State convergence | UserTrade and source recommendation agree after terminal source event | Any unexplained divergence |
| Retention D1/D7 | Users active on day 1/day 7 ÷ invited users | Used as a decision signal; never used alone to hide reliability failures |
| Support incidents | User-reported failures grouped by severity | Any critical data-loss, unauthorized access, or silent close |

## Operational procedure

The operator records the deployment commit, UTC observation start, cohort size, health status, metrics snapshot, lifecycle smoke-test IDs, and all incidents. A missing notification is investigated through the Outbox record and worker logs before using Refresh as a workaround.

## Expansion rule

The Alpha cohort may expand only if health is available, the queue is clear, no critical incident is open, tracked/source identity is intact, and the lifecycle smoke test has passed. Payment and commercial entitlements remain disabled throughout this plan.

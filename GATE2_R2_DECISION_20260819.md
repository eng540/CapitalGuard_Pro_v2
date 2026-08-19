# Gate R2 Decision and Alpha Readiness

## Decision

R2 engineering delivery is complete through analyst profiles, discovery, channel comparison, time windows, freshness metadata, and approximate risk exposure. The code has passed CI and Railway health/Outbox verification. Gate R2 is therefore **conditionally ready for controlled Alpha**, pending a bounded Telegram acceptance run.

## Confirmed evidence

| Area | Evidence |
|---|---|
| R2 code | PR #193 Analyst Discovery, PR #194 channel comparison, and PR #196 profile/metrics merged into `main`. |
| Quality | Full suite at the last R2 release: `108 passed, 1 skipped`; compile, critical lint, and Bandit passed. |
| Production | Railway health returned `{"status":"ok"}` after PR #196. |
| Delivery | Outbox queue was `0.0`; recorded CREATE/REPLY/UPDATE operations were SENT or explicitly SKIPPED by idempotency. |
| Scope safety | No payment, entitlement charging, copy trading, or automatic execution was introduced. |

## Conditional manual acceptance

The following must be exercised with a real analyst account before declaring Gate R2 fully closed:

1. Run `/analyst_profile` without arguments and confirm the current profile is rendered.
2. Update `name`, `bio`, `market`, `style`, and `public=yes`, then rerun the command and confirm persistence.
3. Run `/find_analysts days=30` from a trader account and confirm profile fields, sample-size warning, freshness, and risk exposure are rendered.
4. Run `/compare_analyst AN-000001 days=30 market=Futures asset=BTCUSDT` and confirm that the scope is shown and channel results remain isolated.
5. Verify that a non-analyst receives a permission denial for `/analyst_profile`.

Until this run is recorded, R2 is classified as **conditionally accepted**, not commercially certified.

## Alpha control limits

Alpha should remain limited to 20–50 invited users, with no payment collection and no copy trading. The operator should monitor Outbox queue size, RETRY/FAILED deliveries, duplicate lifecycle events, Telegram command errors, daily active users, seven-day retention, and support incidents. Any unexplained state divergence, missing identified notification, or persistent queue backlog pauses expansion.

## Exit criteria for Gate R2

Gate R2 can be marked fully closed when the manual acceptance table is complete, the 24–48 hour observation window has no unexplained lifecycle divergence, and retention/support metrics are recorded. After that point, R3 design may proceed while commercial activation remains separately gated.

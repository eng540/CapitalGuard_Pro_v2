# CapitalGuard R5 Readiness Evaluation — 2026-08-21

## Decision

**Current decision: HOLD — non-commercial test operation continues.** The system is permitted to continue feature development and controlled operational testing. It is not authorized to enable billing, payments, subscription charging, broker credentials, automatic execution, or Copy Trading.

## Evidence completed

| Evidence area | Result | Verification |
|---|---|---|
| Core quality gate | Passed | `199 passed`, `1 skipped` in the full Core suite. |
| Web quality gate | Passed | TypeScript check, `33 passed` Vitest tests, and production build passed. |
| Core/Web data boundary | Implemented | Financial records remain Core-owned; Web uses authenticated server-to-server read models only. |
| Health and deployment | Passed | Web and Core health routes were verified after Railway deployments. |
| Telegram-first | Passed | Mini App session and owner RBAC were verified in the live test workflow. |
| Owner Review and Evidence | Passed | Core command audit, idempotency, Owner RBAC, and a truthful empty queue/error state were deployed. |
| Operations feed | Passed | Owner-only lifecycle, publication, and command audit feed is available without local financial replication. |
| Commercial defaults | Passed | `BILLING_ENABLED=false` and `COPY_TRADING_ENABLED=false` are explicit Core defaults and have executable tests. |
| R5 status visibility | Passed | The owner-only R5 readiness endpoint and Admin panel expose backlog counts while keeping status `HOLD`. |

## Mandatory HOLD reasons

| Reason | State | What changes it |
|---|---|---|
| `RESTORE_DRILL_DEFERRED` | Deferred by the owner while Railway is a test environment. | Run separate Core and Web PostgreSQL restore drills and record RTO/RPO. |
| `NO_R5_OBSERVATION_WINDOW` | Not yet elapsed. | Define and complete a retained stability window with no unresolved S1/S2 incident. |
| Commercial authorization | Not requested or approved. | A dedicated legal, pricing, privacy, support, and payment-provider decision. |

## Non-negotiable controls

> A successful CI run, deployment, or technical gate never turns on commerce. The R5 endpoint always returns `HOLD`, `commercial_enabled=false`, and `copy_trading_enabled=false` while the mandatory evidence is incomplete.

The restore-drill deferral does not permit bypassing the guardrail. It only allows the team to continue in test mode. The separate R4 runbook remains the authoritative procedure for resuming the drill.

## Related changes

The current technical evidence includes the A-PG isolation [PR #222](https://github.com/eng540/CapitalGuard_Pro_v2/pull/222), Telegram-first authentication [PR #223](https://github.com/eng540/CapitalGuard_Pro_v2/pull/223), Mini App bridge repair [PR #224](https://github.com/eng540/CapitalGuard_Pro_v2/pull/224), health check [PR #225](https://github.com/eng540/CapitalGuard_Pro_v2/pull/225), Core Read Models [PR #226](https://github.com/eng540/CapitalGuard_Pro_v2/pull/226) and [PR #232](https://github.com/eng540/CapitalGuard_Pro_v2/pull/232), Owner Review [PR #227](https://github.com/eng540/CapitalGuard_Pro_v2/pull/227), operations feed [PR #229](https://github.com/eng540/CapitalGuard_Pro_v2/pull/229), R5 guardrail [PR #230](https://github.com/eng540/CapitalGuard_Pro_v2/pull/230), R4 runbook [PR #231](https://github.com/eng540/CapitalGuard_Pro_v2/pull/231), deferred-restore decision [PR #233](https://github.com/eng540/CapitalGuard_Pro_v2/pull/233), commercial default locks [PR #234](https://github.com/eng540/CapitalGuard_Pro_v2/pull/234), and R5 Admin readiness [PR #235](https://github.com/eng540/CapitalGuard_Pro_v2/pull/235).

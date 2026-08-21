# Test Matrix

**المبدأ:** الاختبارات تعطي `BUILD_DONE`؛ أما smoke/UAT/recovery/load فتنتج دليل البوابة. يحفظ كل artifact الأمر والالتزام والبيئة والوقت والنتيجة.

| ID | المجال | دليل الاختبار الحالي | الدليل المفتوح |
|---|---|---|---|
| T-01 | Parser وArabic normalization | parser unit/integration | UAT samples |
| T-02 | `/log` direct input | `tests/test_log_handler.py` | Telegram UAT |
| T-03 | Lifecycle/PnL/history | R1 trade/lifecycle tests | reference dataset reconciliation |
| T-04 | Dedup/outbox/idempotency | dedup/publication tests | repeated-live-flow UAT |
| T-05 | alerts/TP/SL/reconnect | lifecycle/notifications coverage | p95/fault-injection/live evidence |
| T-06 | R2 discovery/comparison | R2 targeted tests | analyst acceptance and observation |
| T-07 | Historical/temporal/replay | historical E2E/release tests | real approved batch + OHLCV |
| T-08 | Web/TMA/RBAC/read boundary | 33 Vitest/TypeScript/build | role UAT/degraded dependency evidence |
| T-09 | Owner commands/Ops/R5 guardrails | command/R5 tests | restore and secret rotation evidence |
| T-10 | PostgreSQL migration | CI/migrations | fresh + existing reconciliation + restore |
| T-11 | Platform multi-tenant/API v1 | not started | scope decision then contract/security/load tests |
| T-12 | payments | hold | provider sandbox suite after decision |
| T-13 | Copy Trading | hold | C0–C5 security/reconciliation/kill-switch suite after decision |

## Gate sets

`G0`: full Core suite, security/static, fresh+existing migrations, restore, Redis/Telegram startup, E2E.
`R1/AV`: reconciliation, UAT, p95, funnel/retention.
`R2/H`: analyst acceptance, observation, real historical batch.
`R4`: tenancy/API/load/SLO/canary.
`R3-C/R5-C`: only after separate Owner Decision.

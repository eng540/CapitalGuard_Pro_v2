# Requirement Traceability Matrix

**Baseline:** `origin/main@f51cecd2641a4896df6766454bbf5a9a7d71e1a4` (PR #241).
**حالة التتبع:** لا تعني `BUILD_DONE` إغلاق القبول التشغيلي؛ المرجع المكمل هو `docs/GATE_SCORECARDS.md`.

| ID | Requirement | التنفيذ/المسار | دليل الكود والاختبار | دليل القبول | الحالة |
|---|---|---|---|---|---|
| TR-001 | Forward + Parse + Review | Core Telegram parsing | parser/forwarding tests | UAT حي ما زال مطلوباً | BUILD_DONE / EVIDENCE_OPEN |
| TR-002 | `/log` direct input | R1 | PR #181؛ `tests/test_log_handler.py` | UAT ownership/review | BUILD_DONE / EVIDENCE_OPEN |
| TR-003 | Watchlist/Activated/Close/PnL | R1 lifecycle | PRs #181, #184–#186 | reference reconciliation ≥99% | BUILD_DONE / EVIDENCE_OPEN |
| TR-004 | Dedup وidempotency | R1/Publication | PRs #188–#190؛ dedup tests | duplicate-free UAT | BUILD_DONE / EVIDENCE_OPEN |
| TR-005 | Alerts، TP/SL، إخطار معرّف | R1/R2 | PR #195 وOutbox | latency/reconnect UAT | BUILD_DONE جزئياً / EVIDENCE_OPEN |
| TR-006 | Funnel وActivated-only reporting | R1 | PR #181 وCore read models | metrics contract + dataset | BUILD_DONE جزئياً / EVIDENCE_OPEN |
| TR-007 | Analyst profile/discovery/comparison | R2 | PRs #192–#197 | analyst acceptance + 24–48h observation | BUILD_DONE / EVIDENCE_OPEN |
| TR-008 | Historical evidence/replay/attribution | H1–H8 | PRs #199–#218؛ historical E2E tests | real batch + OHLCV + review/replay | BUILD_DONE / EVIDENCE_OPEN |
| TR-009 | Temporal routing | H1–H8 | PRs #210–#212؛ temporal tests | real intake sample | BUILD_DONE / EVIDENCE_OPEN |
| TR-010 | Web trader/analyst/admin platform | R4 slice | PRs #219–#232؛ Web tests | tenant/API/load/SLO/canary | BUILD_DONE / EVIDENCE_OPEN |
| TR-011 | Telegram-first auth + RBAC | R4 slice | PR #223؛ auth/RBAC tests | credential rotation evidence | BUILD_DONE / EVIDENCE_OPEN |
| TR-012 | Owner Review/Evidence/Ops feed | R4 slice | PRs #227–#229 | UAT review evidence | BUILD_DONE / EVIDENCE_OPEN |
| TR-013 | Entitlements for Alpha | R3-C foundation | PR #198 | product/retention decision | BUILD_DONE / HOLD |
| TR-014 | Payment/subscription | R3-C | لا provider/webhook/refund path | legal + provider sandbox decision | NOT_STARTED / HOLD |
| TR-015 | Versioned public API/rate limits | R4 | internal read models only | API v1/OpenAPI/scopes/rate limits | NOT_STARTED / OPEN |
| TR-016 | Tenant isolation/export/delete | R4 | A-PG service boundary only | tenant schema/tests/data-rights evidence | NOT_STARTED / OPEN |
| TR-017 | Recovery/migration reconciliation | G0/R4 | migrations and health verified | fresh/existing DB + restore/RTO/RPO | EVIDENCE_OPEN |
| TR-018 | Secret/PII operational proof | G0/R4 | fail-closed code | masked rotation/rejection/retention evidence | EVIDENCE_OPEN |
| TR-019 | Copy Trading Sandbox | R5-C | fail-closed guardrails only | C0–C5 sandbox/reconciliation/kill switch/legal | NOT_STARTED / HOLD |

## قاعدة القبول

لكل `PASS` تشغيلي يلزم رابط PR/commit، اختبار، بيئة، وقت، نتيجة، ومالك قرار. لا تُسوَّق أو تُفعل المتطلبات ذات `EVIDENCE_OPEN` أو `HOLD` كميزات مكتملة.

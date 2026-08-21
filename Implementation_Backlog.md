# CapitalGuard Engineering Implementation Backlog

**الحاكم:** `docs/MASTER_EXECUTION_BASELINE_20260821.md`
**الحالة:** Sprint G-1 مغلق توثيقياً؛ لا يفتح نطاقاً منتجياً أو تجارياً.

## 1. القاعدة التنفيذية

كل قصة تملك Requirement ID، مالكاً، معيار قبول، اختباراً، دليلاً، وخطة rollback. تستخدم الحالات `BUILD_DONE` و`EVIDENCE_OPEN` و`GATE_CLOSED` و`HOLD` ولا يُستبدل أي منها بعبارة «مكتمل» عامة.

## 2. سجل الحزم

| Epic | النطاق | حالة البناء | الحاجز التالي |
|---|---|---|---|
| G0-E1 | migrations, recovery, secrets, E2E | BUILD_DONE جزئياً | restore/RTO/RPO، fresh/existing reconciliation، masked rotation evidence |
| R1-E1 | input/review/ownership/lifecycle | BUILD_DONE | UAT وreference-report reconciliation |
| R1-E2 | alerts/outbox/report/funnel | BUILD_DONE جزئياً | p95/reconnect/duplicate and funnel evidence |
| AV-E1 | Alpha/value proof | NOT_STARTED | cohort، D7، activation، TTFV، incident/support log |
| R2-E1 | profiles/discovery/comparison | BUILD_DONE | analyst acceptance + observation |
| H-E1 | import/evidence/replay/temporal | BUILD_DONE | real batch + OHLCV + claim/review/replay |
| R3C-E1 | Alpha grants | BUILD_DONE | remains HOLD pending commercial decision |
| R3C-E2 | payment/provider | NOT_STARTED | legal/pricing/support + sandbox scope |
| R4-E1 | Web/A-PG/TMA/read models/review ops | BUILD_DONE | tenant/API/load/SLO/canary evidence |
| R5C-E1 | Copy Trading Sandbox | NOT_STARTED | cannot start before R4/R3-C decisions |

## 3. الأعمال المفتوحة بالترتيب

### P0 — أدلة البوابة لا الميزات

| ID | العمل | التبعية | القبول |
|---|---|---|---|
| G0-01 | Restore Drill مستقل لـ Core وWeb | بيئات restore منفصلة | RTO/RPO، migrations، health، نتائج مقنّعة |
| G0-02 | existing-data migration reconciliation | snapshot مجهول الهوية | counts/FKs/statuss قبل/بعد |
| G0-03 | دليل تدوير الأسرار | صلاحيات Railway | القديم مرفوض، لا secret في doc/log، OAuth placeholder غائب |
| G0-04 | E2E live controlled Forward→Close | Redis/Telegram/market | trace واحد قابل لإعادة المراجعة |

### P1 — قبول المنتج

| ID | العمل | التبعية | القبول |
|---|---|---|---|
| AV-01 | بروتوكول UAT داخلي للمتداول | G0-04 أو قرار risk صريح | حالات input/review/activate/alert/close/report موثقة |
| AV-02 | dataset reconciliation | metrics contract | ≥99% وWatchlist excluded |
| AV-03 | Alpha scorecard | AV-01 | cohort/activation/D7/TTFV/support facts، لا ادعاءات تسويقية |
| R2-01 | analyst acceptance | AV-01 | profile/discovery/comparison/RBAC على حساب حقيقي |
| H-01 | historical real acceptance | مصدر مصرح + OHLCV | claim/review/evidence/replay بلا تسرب حي |

### P2 — إغلاق R4 بعد الأدلة

tenant model، API v1/OpenAPI/scopes/rate limits، export/delete، load/SLO/error budget/canary لا تبدأ إلا بقرار scope مستقل بعد نتائج G0 وUAT.

## 4. المحظورات

الدفع وCopy Trading وAuto/Live Trade محظورة أثناء HOLD. لا تبدأ Provider أو broker أو مفاتيح تنفيذ أو بيانات إنتاج ضمن هذا الـ Backlog دون Owner Decision جديد.

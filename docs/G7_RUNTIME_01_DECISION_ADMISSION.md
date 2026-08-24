# G7-RUNTIME-01 — Decision Boundary and Command Admission Enforcement

## النطاق

يطبق هذا PR enforcement على Web command المحدد `POST /api/webapp/recommendations/confirm` فقط. لا يعمم التغيير على كل Web وTelegram callers.

## المسار المفروض

```text
signed Telegram initData أو Core service authorization
→ analyst identity + active check
→ canonical normalization/validation
→ ANALYSIS_TO_RECOMMENDATION decision
→ OperationalAdmissionService
→ CreationService
→ WebCommandAudit
```

## الضمانات

- `OperationalDecisionService.prepare()` يقبل `ANALYTICAL_RESULT` فقط.
- recommendation target لا ينتج إلا عبر `prepare_recommendation()` مع actor وcommand identity.
- admission يرفض التحليل العام أو أي target غير recommendation.
- Web confirmation يرفض actor غير الموجود أو غير النشط أو غير المصنف ANALYST قبل CreationService.
- `WebCommandAudit` وrequest hash وidempotency key يعيدان نفس النتيجة عند إعادة command المطابق ويرفضان إعادة استخدام المفتاح مع payload مختلف.
- Decision fingerprint وtrace id يحفظان داخل response المسجل في WebCommandAudit.
- لا يسمح admission بتفعيل execution.
- G5/G6 Replay وRanking وTrust وRisk وTrading لم تُعدّل.

## المسار المؤجل

يبقى `POST /api/webapp/create` خارج هذا PR لأنه caller مختلف يحتاج triage وتكاملًا مستقلًا. كما تبقى Telegram full runtime integration وdistributed restart/concurrency وproduction telemetry خارج النطاق.

## حدود المعاملة

يستمر `session_scope()` الحالي في امتلاك transaction للـWeb command. لا تنفذ خدمات G7 commit أو rollback على transaction المملوكة للـcaller. ويجب أن تبقى أي network I/O خارج transaction الطويلة.

## الاختبارات

يشمل التحقق حالات target semantics وunauthenticated/unauthorized/inactive actor وnew authorized command وduplicate same payload وduplicate different payload وG5/G6 regression وRanking/Trust isolation.

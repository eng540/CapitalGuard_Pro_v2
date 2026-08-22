# Secret Rotation Evidence — 22 August 2026

## Scope

نفذ المالك تدويراً لـ Core API service key وJWT الخاص بالويب وبيانات وصول PostgreSQL Web. لا تتضمن هذه الوثيقة قيماً أو معرفات أو روابط قواعد بيانات.

| الاختبار | النتيجة المقنّعة | الحالة |
|---|---|---|
| الاعتماد السابق الملغى | HTTP 401 Unauthorized | PASS |
| الاعتماد الحالي | HTTP 200 OK | PASS |
| Core health | `ok` | PASS |
| Web health | `ok` | PASS |

## قرار الدليل

يُغلق دليل التدوير التشغيلي. لا يغير هذا القرار أقفال `BILLING_ENABLED` أو `COPY_TRADING_ENABLED` أو `AUTO_TRADE_ENABLED` أو `TRADE_LIVE_ENABLED`، ولا يغلق بقية بنود G0/R4.

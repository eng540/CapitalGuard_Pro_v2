# Architecture Implementation Map

## 1. الحاكمية المعمارية

CapitalGuard يبقى Modular Monolith في Core Python مع AI service مستقلة. Core PostgreSQL هي المصدر الوحيد للحقيقة المالية والتاريخية التشغيلية؛ Redis للـ cache/persistence المؤقتة؛ Telegram/FastAPI واجهات تشغيل.

Web SaaS طبقة منفصلة: React/Express/tRPC مع PostgreSQL Web مستقلة للجلسات والتفضيلات وتدقيق الويب فقط. تقرأ Core خادمياً بعقد read-model ولا تحتفظ بتوصيات أو صفقات أو PnL أو Evidence مالية.

## 2. خريطة التنفيذ الحالية

| النطاق | المكونات | الحالة |
|---|---|---|
| Trader lifecycle | forwarding, `/log`, review, ownership, events, PnL, history | BUILD_DONE / EVIDENCE_OPEN |
| Publication | outbox, idempotency, lifecycle notifications | BUILD_DONE / EVIDENCE_OPEN |
| Trust | identity, analyst profile, discovery, comparison | BUILD_DONE / EVIDENCE_OPEN |
| Historical | import, evidence, parser, replay, temporal, review | BUILD_DONE / EVIDENCE_OPEN |
| Web control plane | A-PG, TMA auth, read models, owner commands, operations feed | BUILD_DONE / EVIDENCE_OPEN |
| Commerce | Alpha grants only | HOLD |
| Execution | fail-closed direct-trade controls | HOLD / NOT_STARTED as Copy Trading |

## 3. حدود لا يجوز كسرها

لا يكتب Web مباشرة في Core DB. لا يتسرب التاريخ إلى Recommendation/UserTrade/Outbox/PriceStreamer. لا تنشأ ميكروسيرفس أو event broker قبل دليل حمل. ولا تعد `AutoTradeService` أو R5 guardrails Copy Trading.

## 4. مخاطر يتعين إثباتها

Recovery، migrations existing-data، alert reconnect/latency، metrics reconciliation، secret rotation، tenant isolation وAPI v1/load/canary. المرجع التشغيلي هو `docs/GATE_SCORECARDS.md`.

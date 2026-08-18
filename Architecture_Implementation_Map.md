# Architecture Implementation Map

## 1. المبدأ

المعمارية المستهدفة هي **Modular Monolith** منظم، مع خدمة AI منفصلة، PostgreSQL كمصدر الحقيقة، Redis للـ persistence/cache والمهام المؤقتة، وFastAPI/Telegram كواجهات. لا توجد حاجة حالية إلى Microservices جديدة.

## 2. خريطة الطبقات

| الطبقة | المسارات الحالية | المسؤولية | الحالة |
|---|---|---|---|
| Interfaces | `src/capitalguard/interfaces/telegram/*` | أوامر Telegram، Forwarding، Review، Confirm | PARTIAL/VERIFIED |
| Interfaces | `src/capitalguard/interfaces/api/main.py` | startup، readiness، webhook، routers | VERIFIED جزئيًا |
| Interfaces | `interfaces/api/routers/webapp.py` | price/channels/create/portfolio/action/signal | VERIFIED للمسارات الحالية |
| Application | `parsing_service.py` | parsing، attempts، idempotency hint | VERIFIED |
| Application | `creation_service.py` | shadow recommendation وUserTrade forwarding | PARTIAL/VERIFIED |
| Application | `lifecycle_service.py` | activate/close/update/PnL | PARTIAL مع إصلاح PnL |
| Application | `dedup_service.py` | fingerprint وwindow ledger | VERIFIED في الفرع الحالي |
| Application | `alert_service.py` | triggers وإرسال التنبيهات | PARTIAL؛ يحتاج external integration test |
| Application | `performance_service.py` | PnL/stats/reporting | PARTIAL؛ يحتاج reference reconciliation |
| Infrastructure | `db/models/*` | ORM models وmetadata | VERIFIED مع Dedup migration |
| Infrastructure | `db/repository.py` | repositories وquery boundaries | PARTIAL؛ يحتاج ownership review |
| Infrastructure | `sched/price_streamer.py` | streaming price updates | PARTIAL؛ يحتاج reconnect/load test |
| Infrastructure | `market/ws_client.py` | Binance WebSocket | PARTIAL؛ يحتاج operational test |
| Infrastructure | `db/backup_service.py` | backup loop | PARTIAL؛ يحتاج restore evidence |
| External | `ai_service/*` | AI parsing API | PARTIAL؛ يحتاج timeout/degraded mode test |

## 3. تدفقات التنفيذ الحالية والمستهدفة

### 3.1 Forwarding إلى UserTrade

`forward_parsing_handler.py` يستقبل الرسالة، ويستدعي Parser/AI عند الحاجة، ثم يعرض Review. بعد Confirm يمرر payload إلى `TradeService.create_trade_from_forwarding_async`، الذي يفوض إلى `CreationService.create_trade_from_forwarding_async`. تقوم الخدمة بالتحقق، استدعاء `DedupLedgerService.check_and_record`، إنشاء `WatchedChannel` عند وجود قناة، إنشاء `UserTrade`، تسجيل fingerprint، ثم جدولة trigger عند توفر AlertService.

### 3.2 Recommendation إلى القناة

`WebApp /api/webapp/create` أو مسار Telegram يستدعي `CreationService.create_and_publish_recommendation_async`. تحفظ التوصية كـ shadow وتعيد `{queued: true, success: [], failed: []}`، ثم يعمل `background_publish_and_index` للنشر والفهرسة. لا يجب اعتبار `queued` نجاح النشر الخارجي النهائي.

### 3.3 Monitoring إلى Close

`PriceStreamer` يحدّث أسعار الرموز، و`AlertService` يبني triggers ويستدعي lifecycle. عند الإغلاق، `LifecycleService.close_user_trade_async` يفرض ownership، يحدد `CLOSED`، يحفظ `close_price` و`pnl_percentage` ويسجل `UserTradeEvent`. يلزم اختبار عدم تكرار close/alert.

## 4. خريطة البيانات

| الكيان | الجدول/النموذج | المصدر | الملاحظات |
|---|---|---|---|
| User | `users` / `models/auth.py` | Telegram identity | يحتاج RBAC/PII review |
| Recommendation | `recommendations` | Analyst/WebApp/Telegram | shadow ثم publish |
| UserTrade | `user_trades` | Forward/Activate | الطبقات المالية تبدأ Activated |
| UserTradeEvent | `user_trade_events` | lifecycle/alerts | audit trail للمتداول |
| RecommendationEvent | `recommendation_events` | creation/lifecycle | audit trail للتوصية |
| WatchedChannel | `watched_channels` | Forward | channel context per user |
| DedupLedger | `dedup_ledger` | Forward | unique user/channel/fingerprint/window |
| ParsingAttempt | `parsing_attempts` | ParsingService | raw content وparser path |
| Subscription | `subscriptions` | موجود ORM فقط | لا يساوي payment system |

## 5. حدود التغيير

لا يتم تعديل طبقة التخزين أو إدخال event broker جديد إلا إذا أثبت الحمل حاجة لذلك. لا يتم فصل AlertService أو ParsingService إلى خدمات مستقلة جديدة. التغيير المسموح في R1 هو إضافة contracts واختبارات ومؤشرات وتوثيق فوق الهيكل الحالي.

## 6. نقاط الخطر المعمارية

أهم المخاطر الحالية هي lifecycle event loops، الاعتماد على خدمات خارجية أثناء startup، ازدواج حالات ORM/domain، migrations القديمة غير المحمولة على SQLite، وخلط shadow/queued مع published. كل PR يجب أن يحدد أي خطر من هذه المخاطر يعالجه.

## 7. الاختبارات المعمارية

يجب تغطية كل تدفق بثلاث طبقات: Unit للعقد المحلي، Integration مع SQLite/PostgreSQL fixture، وE2E على Staging مع Redis/Telegram sandbox. لا تعتبر اختبارات TestClient بدون lifespan بديلًا عن startup smoke.

# Runbook

## الحالة التشغيلية

هذا النظام يشغّل FastAPI وTelegram وAlertService وPriceStreamer داخل عملية API واحدة. لا توجد عملية watcher مستقلة؛ هدف `make watcher` يشرح ذلك فقط، ويجب تشغيل `make api` أو `make dev` لبدء مراقبة الأسعار.

## التشغيل المحلي

أنشئ البيئة وثبّت التبعيات، ثم انسخ `.env.example` إلى `.env` واملأ القيم السرية. يجب ضبط `REDIS_URL` و`TELEGRAM_BOT_TOKEN` و`API_KEY` و`JWT_SECRET` بقيم عشوائية مناسبة، كما يجب ضبط `TV_WEBHOOK_SECRET` قبل تفعيل استقبال TradingView. نفّذ `make migrate` ثم `make dev` للتطوير أو `make api` لتشغيل الخادم على جميع الواجهات. لا حاجة لتشغيل `make bot` بالتوازي مع API؛ عملية API تهيئ Telegram عند اكتمال startup.

## متطلبات الإقلاع

يرفض التطبيق بدء التشغيل عند غياب Redis أو فشل الاتصال به أو استخدام JWT secret افتراضي/قصير أو خوارزمية JWT غير مسموحة. كما أن readiness لا تصبح سليمة إلا بعد تهيئة Telegram والخدمات وAlertService. لذلك يجب استخدام `GET /health` كـ readiness probe، والتعامل مع HTTP 503 على أنه عدم جاهزية، لا كفشل HTTP عابر.

## API المنشورة حاليًا

| المسار | الغرض | الحماية |
|---|---|---|
| `GET /` | معلومات الإصدار | عامة |
| `GET /health` | readiness بعد اكتمال startup | عامة، تعيد 503 قبل الجاهزية |
| `POST /webhook/telegram` | استقبال تحديثات Telegram | يجب حمايتها على مستوى الشبكة/Telegram webhook |
| `POST /webhook/tradingview` | استقبال إشارات TradingView | `X-TV-Secret` إلزامي، وتعيد 503 إذا لم يُضبط السر |
| `GET /metrics` | Prometheus metrics | يجب تقييدها على مستوى الشبكة في الإنتاج |
| `GET /dash` | لوحة الإشارات | WebApp/جلسة |
| `GET /new` | إنشاء توصية | WebApp/جلسة |
| `GET /portfolio` | محفظة المستخدم | WebApp/جلسة |
| `/api/webapp/*` | عمليات WebApp | تحقق Telegram وملكية السجلات |
| `/auth/*` | محلية اختيارية | معطلة افتراضيًا حتى اكتمال مخطط الاعتماد المحلي |

المسارات القديمة مثل `/recommendations` و`/report` ليست موصولة في التطبيق الحالي، ولذلك لا ينبغي استخدامها في smoke tests أو وثائق تكامل خارجية قبل تنفيذها صراحة.

## Webhook TradingView

أرسل `X-TV-Secret` بقيمة مطابقة تمامًا لـ `TV_WEBHOOK_SECRET`. لا يقبل التطبيق webhook عند غياب السر، ويجب عدم نشر المسار على الإنترنت العام دون rate limiting وreplay protection وقيود شبكة إضافية. يجب إنشاء مستخدم TradingView محلل في قاعدة البيانات وربطه بالمعرف الإداري قبل استخدام المسار.

## Redis Persistence

تستخدم persistence مساحة مفاتيح `ptb:v2:*` وترميز JSON مقيدًا بالأنواع بدل فك تسلسل `pickle`. بيانات مساحة `ptb:*` القديمة لا تُقرأ تلقائيًا؛ يجب اعتبارها بيانات legacy ونقلها عبر إجراء مراجَع إن كانت مطلوبة.

## DedupLedger

يسجل النظام بصمة حتمية لكل إشارة Forward لكل مستخدم وقناة ضمن نافذة افتراضية مقدارها خمس دقائق. التكرار داخل النافذة يعاد كمرفوض مع `duplicate=true` ولا ينشئ `UserTrade` ثانيًا. يجب تشغيل migration `20251201_add_dedup_ledger` قبل تفعيل مسار Forwarding في بيئة جديدة، ومراجعة سجلات `dedup_ledger` عند التحقيق في تكرار الإشارات. لا ينبغي حذف السجل أثناء التنظيف التشغيلي؛ فهو جزء من أثر التدقيق.

## المراقبة والإيقاف

راقب readiness، سجلات فشل Redis وTelegram وAlertService، reconnects الخاصة بـ Binance، أخطاء مهام النسخ الاحتياطي، ونسبة رفض Dedup. عند الإيقاف، يلغي التطبيق المهام الخلفية ثم يوقف AlertService وTelegram. لا تعتبر عملية الإقلاع ناجحة إلا بعد ظهور رسالة startup complete.

## الاختبارات قبل الدمج

شغّل `make test` أو `pytest -q`، ثم `python3 -m compileall -q src ai_service`، و`flake8 src`، و`bandit -r src ai_service -q --severity-level high`، و`pip-audit -r requirements.txt`. شغّل `PYTHONPATH=src alembic heads` للتحقق من وجود رأس واحد، واختبر `alembic upgrade head` على PostgreSQL قبل النشر. قد تعرض Bandit تحذيرات منخفضة قديمة؛ لا تُعتبر بوابة الدمج مغلقة إلا عند عدم وجود High، مع بقاء التقرير الكامل محفوظًا للمراجعة. يجب ألا تُخفى نتائج الفحوص باستخدام `|| true`، ويجب إصلاح أخطاء الجمع والفشل قبل الدمج.

## Metrics

`GET /metrics` يعرض صيغة Prometheus. في الإنتاج يجب عزله خلف شبكة المراقبة أو مصادقة reverse proxy، وعدم تعريضه مباشرة للعامة.

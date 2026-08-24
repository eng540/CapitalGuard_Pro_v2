# سجل تنفيذ ميزات CapitalGuard Pro v2

## النطاق

نُفذت التوسعات فوق الكود الموجود في فرع `feat/ox-alpha-openrouter-integration` المبني على `main` عند `0e121f13`. لم تُنشأ خدمة موازية ولم تُكرر طبقات Core أو AlertService أو StrategyEngine أو Historical Intake. بقيت عقود Telegram وCore وWeb مستقرة، وأضيفت الحقول والواجهات فقط حيث كان ذلك لازمًا لتفعيل الميزة فعليًا.

## خريطة الميزات والتنفيذ

| الميزة | التنفيذ الفعلي | مصدر الحقيقة |
|---|---|---|
| التسجيل السريع `/log` وForward والصور | موجودة مسبقًا في Telegram handlers وAI Parsing؛ لم يُنشأ مسار مكرر. تم الحفاظ عليها. | Telegram handlers وAI Parsing Service |
| Auto Break-Even وTrailing | أضيفت حقول الحماية إلى `Recommendation` و`UserTrade` عبر migration واحدة. صار `StrategyEngine` يقبل `ACTIVE` و`ACTIVATED`، ويُدخل UserTrade في الفهرس نفسه. يتم توريث إعداد الحماية عند إنشاء UserTrade من توصية. | StrategyEngine وAlertService وCore models |
| التنبيهات الفورية | بقي Telegram push الحالي هو قناة التنبيه الفوري التشغيلية. واجهة الويب تعيد قراءة Core دوريًا مع حالات فشل صريحة. لم يُكشف service key للمتصفح ولم تُضف WebSocket غير مؤمنة أو بنية Pub/Sub مكررة. | AlertService وTelegram notifications وCore read models |
| Position Sizing | أضيف مبلغ مخاطرة مباشر، مع إبقاء نسبة المخاطرة كـ fallback. أضيفت الرافعة والهامش التقريبي والتحذير عند الرافعة العالية. | Risk Studio و`calculateRiskPlan` |
| Smart Dropzone | أضيف drag/drop متعدد، قراءة TXT/JSON/CSV، روابط provenance، اسم batch، وإرسال الدفعة عبر `historicalIntake` الموجود. التحليل المفرد يعيد استخدام `smartAnalyze` الحالي. | Historical parser وHistorical Intake API |
| Audited Analyst Dossier وLeaderboard | أضيف Profit Factor ومرشحات الاسم والأهلية والترتيب، وواجهة dossier عامة read-only. | AnalystDiscoveryService وCore analyst read model |
| Signal Discovery | أضيف Core read model عام مفلتر حسب الأصل والفترة وأقل PnL، وواجهة `/signals` مربوطة عبر adapter وtRPC. | Core `/read-models/signals` |
| Risk Heatmap | أضيفت heatmap التعرض إلى Risk Studio من مراكز Core الحية، مع إظهار الحماية ونسبة الحجم المفتوح. | Core trader read model وRisk Studio |
| Analyst Workspace | أضيفت بطاقة Signal Health فوق Workspace، وتشمل العينة، Win Rate، PnL، Profit Factor، Drawdown، Exposure، متوسط الوصول إلى TP1، وعدد الانعكاسات قبل الدخول. | Analyst Dashboard read model |
| Publication Outbox | لم يُنشأ outbox ثانٍ؛ بقي `PublicationOutboxService` الحالي مصدر النشر والتحديثات الآلية. تم ربط Dashboard بالقراءة الحالية فقط. | PublicationOutboxService |
| Public Track Record | أضيف dossier عام لا يعرض بيانات خاصة أو أوامر تنفيذ، ويستخدم نفس leaderboard read model. | Core public analyst read model |
| Signal Health Analytics | أضيفت إحصاءات مشتقة من توصيات وأحداث Core: متوسط زمن الوصول لأول هدف، عدد الانعكاسات قبل الدخول، والأزواج الأكثر ربحية. | Recommendation events وPerformance data |

## قاعدة البيانات

أُضيفت migration:

`alembic/versions/20260824_add_usertrade_profit_stop_fields.py`

وتضيف حقول الحماية إلى `user_trades` وحقول Break-Even إلى `recommendations` و`user_trades`. `alembic heads` يعرض revision واحدة متصلة:

`20260824_add_usertrade_profit_stop_fields (head)`

## الاختبارات والتحقق

| الفحص | النتيجة |
|---|---:|
| كامل اختبارات Python | 347 ناجحًا، 1 متجاوز، 17 تحذيرًا غير مانع |
| كامل اختبارات frontend | 87 ناجحًا في 25 ملف اختبار |
| TypeScript `tsc --noEmit` | ناجح |
| Production build | ناجح |
| Python compilation | ناجح |
| `git diff --check` | ناجح |
| اختبارات Ox Alpha contract المحلية | ناجحة باستخدام mocks فقط |

الاختبار المتجاوز موجود مسبقًا في `tests/test_parsing.py` لأنه يتطلب `session_scope/UOW` لمسار تصحيح async، ولم يُتجاوز بسبب التغييرات الحالية.

## الحدود التشغيلية المقصودة

لم تُرسل أي طلبات حقيقية إلى OpenRouter أو Binance أثناء الاختبارات. لا توجد أسرار داخل المستودع. لا تنفذ واجهات الويب أي أمر سوقي تلقائيًا، وتبقى أوامر Core محمية بملكية المستخدم وidempotency والحالة.

لم تُضف قناة WebSocket جديدة إلى الويب؛ السبب أمني ومعماري: Core service key لا يجوز أن يصل إلى المتصفح، كما أن بيئة الويب الحالية لا تملك broker مشتركًا آمنًا للبث. التنبيه الفوري التشغيلي الموجود هو Telegram عبر `AlertService`، بينما تعرض الواجهة آخر قراءة Core مع polling محدود. إضافة WebSocket فعلية تتطلب قناة مصادقة قصيرة العمر أو broker مشتركًا معتمدًا قبل تفعيلها إنتاجيًا.

## حالة Git

التعديلات محلية على فرع التنفيذ فقط. لم يُنشأ commit أو push أو PR أو merge في هذه المرحلة. يجب مراجعة `git diff` ثم إنشاء PR مستقل بعد موافقة المالك على نطاق الميزات والم migration.

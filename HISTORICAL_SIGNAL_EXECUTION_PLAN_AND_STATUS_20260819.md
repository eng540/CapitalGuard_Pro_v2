# الخطة التنفيذية التفصيلية لميزة Historical Signal Reconstruction

## 1. الهدف والنطاق

الهدف هو بناء طبقة تاريخية موثوقة لتجميع توصيات القنوات القديمة، حفظ مصدرها الأصلي، إعادة بناء دورة حياتها زمنيًا، ربطها بالقناة والمحلل والمتداول، ثم إدخال السجلات المؤهلة فقط في مقاييس السمعة. الميزة لا تعيد كتابة الماضي داخل دورة التوصيات الحية، ولا تحول السجل التاريخي تلقائيًا إلى Recommendation قابلة للنشر أو التفعيل أو التنفيذ.

القرار الأساسي هو فصل المجال التاريخي عن المجال التشغيلي الحالي. التوصية التاريخية كيان تحليلي غير قابل للتنفيذ، بينما Recommendation وUserTrade وPublication Outbox تبقى مخصصة للحاضر والتشغيل الفعلي.

> القاعدة الحاكمة: لا نثبت حدثًا تاريخيًا ولا نحسب أداءً تاريخيًا إلا بوجود مصدر، وقت، علاقة ملكية، ودليل سوقي متاح عند أو قبل وقت الحدث. عند غياب الدليل نسجل `UNVERIFIED` بدل التخمين.

## 2. المعمارية العامة

### 2.1 طبقات النظام

| الطبقة | المكونات | المسؤولية |
|---|---|---|
| Source Acquisition | Telegram Bot API، Telegram API/TDLib عبر حساب مصرح، Telegram Export، ملف إداري | جلب أو استقبال المصدر الخام مع إثبات طريقة الحصول عليه |
| Evidence | `HistoricalImportBatch`، `HistoricalSignalEvidence` | حفظ manifest، النص الخام، timestamp، message ID، hash، الملكية، ودرجة الثقة |
| Normalization | Parser مشترك، قواعد Asset/Side/Entry/SL/TP، تطبيع الوقت والرموز | تحويل الرسالة الخام إلى حقول منظمة دون حذف المصدر الأصلي |
| Historical Domain | `HistoricalSignal`، `HistoricalSignalEvent`، `HistoricalSignalAttribution` | تخزين التوصية التاريخية وخطها الزمني وإسنادها للمحلل والقناة والمتداول |
| Market Replay | OHLCV/Tick adapter، نقطة زمنية، مصدر السوق، سياسة censor gap | فحص ما إذا كان TP/SL/التفعيل حدث فعلاً وفق بيانات متاحة حينها |
| Trust & Reputation | درجات المصدر، الثقة، أهلية الترتيب، مؤشرات historical_verified | فصل الأداء الموثق عن اليدوي وغير المؤكد وعدم تضخيم AnalystStats الحالية |
| Query & UX | Historical Wallet، Channel History، Analyst History، Admin Review | البحث والتصفية والمراجعة والعرض بدون تفعيل أو نشر |
| Observability | Prometheus، Outbox metrics، import metrics، audit logs، batch reports | كشف عدد المقبول والمرفوض والمتكرر وغير المؤكد وفشل replay |

### 2.2 مسار البيانات المستهدف

```text
مصدر تاريخي
   ↓
Import Manifest / Batch DRY_RUN
   ↓ validation + ownership + timestamp checks
Batch VALIDATED
   ↓
Immutable Evidence + SHA-256 content hash
   ↓
Historical Signal Parser
   ↓
HistoricalSignal PARSED
   ↓
Market Replay at historical timestamp
   ↓
HistoricalSignalEvent VERIFIED / UNVERIFIED
   ↓
Analyst / Channel / Trader Attribution
   ↓
Historical Reputation Summary
   ↓
Public ranking only after Gate approval
```

## 3. نموذج البيانات المعتمد

### 3.1 دفعة الاستيراد

الجدول `historical_import_batches` يمثل عملية استيراد كاملة. يحتوي على `batch_ref`، القناة، مصدر البيانات، المستخدم الذي طلب العملية، hash للـ manifest، عدد السجلات الكلي، المقبول، المرفوض، والحالة.

الحالات المعتمدة هي `DRY_RUN` ثم `VALIDATED` ثم `IMPORTED` أو `REJECTED`. لا يسمح النظام بإدخال Evidence مرتبطة بدفعة قبل وصولها إلى `VALIDATED`.

### 3.2 Evidence

الجدول `historical_signal_evidence` هو سجل الدليل الخام. يحتوي على:

| الحقل | الغرض |
|---|---|
| `source_kind` | `LIVE_BOT_UPDATE` أو `AUTHORIZED_USER_HISTORY` أو `TELEGRAM_EXPORT` أو `MANUAL_ADMIN_IMPORT` |
| `telegram_channel_id` | هوية القناة الخارجية |
| `telegram_message_id` | المعرف الأصلي للرسالة إن توفر |
| `message_revision` | تمييز النسخة المعدلة من الرسالة |
| `message_timestamp` | وقت الرسالة الأصلي |
| `raw_text` | النص الخام كما وصل |
| `content_hash` | SHA-256 للنص المطبع |
| `dedup_key` | مفتاح منع التكرار |
| `ownership_proof_type/ref` | دليل ملكية أو علاقة القناة بالمحلل |
| `evidence_confidence` | درجة الثقة في مصدر الدليل |
| `batch_id` | دفعة الاستيراد |

إذا توفر channel ID وmessage ID يستخدم النظام المفتاح `telegram:<channel>:<message>:r<revision>`. إذا لم يتوفر message ID يستخدم hash المحتوى مع القناة والوقت.

### 3.3 Historical Signal

الجدول `historical_signals` يحتوي النسخة المنظمة من التوصية القديمة: الأصل، الاتجاه، الدخول، الوقف، الأهداف، السوق، وقت القرار، القناة، المحلل، و`public_ref` مستقل مثل `HIST-...`.

يحتوي أيضًا على:

| الحقل | القيم |
|---|---|
| `status` | `IMPORTED`, `PARSED`, `REPLAYED`, `UNVERIFIED` |
| `trust_tier` | `VERIFIED_LIVE`, `VERIFIED_HISTORY`, `RECONSTRUCTED`, `MANUAL_ATTESTED`, `UNVERIFIED` |
| `confidence_score` | قيمة بين 0 و1 |
| `eligible_for_ranking` | false افتراضيًا، ولا يصبح true إلا بعد بوابات الثقة |

### 3.4 Timeline Events

الجدول `historical_signal_events` يسجل `CREATED`, `ACTIVATED`, `TP1`, `TP2`, `SL`, `UPDATE`, `CLOSED` وغيرها. كل حدث يحتفظ بـ `event_timestamp` منفصل عن `market_as_of` و`created_at`، مع `data_source`, `price`, `replay_status`, `event_confidence`, ومرجع evidence.

### 3.5 Attribution

الجدول `historical_signal_attributions` يفصل الإسناد عن التوصية:

| النوع | المعنى |
|---|---|
| `ANALYST` | إسناد التوصية لمحلل |
| `CHANNEL` | إسنادها لقناة |
| `TRADER_FOLLOW` | متداول سجل أنه تابع التوصية تاريخيًا |
| `ADMIN` | إسناد أو مراجعة إدارية |

متابعة المتداول التاريخية لا تنشئ `UserTrade` حية، ولا تغير الحالة الحية، ولا ترسل تنبيهًا، ولا تدخل PriceStreamer.

## 4. مصادر البيانات والقيود

### 4.1 Telegram

المصدر الحي الحالي يستمر عبر Bot API والتحديثات. أما التاريخ القديم فلا ينبغي افتراض أن Bot Token وحده يستطيع جلبه. وثائق Telegram الرسمية تفصل بين Bot API وTelegram API/TDLib، وتوضح أن `messages.getHistory` مخصص لحسابات المستخدمين، بينما Bot API يعالج التحديثات الواردة [1] [2] [3].

لذلك يعتمد التنفيذ على أربع طرق قابلة للتدقيق:

| الطريقة | الحالة | الثقة الافتراضية |
|---|---|---:|
| Bot update محفوظ وقت الحدث | أقوى مصدر تشغيلي | 1.0000 |
| Authorized user history | يتطلب حساب مستخدم مصرحًا وانضمامًا للقناة | 0.9500 |
| Telegram Export | ملف يقدمه المالك أو المستخدم المصرح | 0.9000 |
| Manual Admin Import | مفيد للمراجعة، غير كافٍ للترتيب العام | 0.4000 |

لا يتم تنفيذ MTProto أو حفظ session/phone credentials في هذه الشريحة؛ لأن ذلك يحتاج قرار وصول صريح، إدارة أسرار، rate limits، ومسار تشغيل مستمر منفصل.

### 4.2 بيانات السوق

يجب أن يحدد Market Replay مصدرًا تاريخيًا يدعم نقطة زمنية مناسبة للأصل والفترة. لا يستخدم السعر الحالي لإثبات حدث قديم، ولا تستخدم شمعة لاحقة لوقت القرار لإثبات TP أو SL سابق. إذا لم تتوفر بيانات عند وقت الحدث، يبقى الحدث `UNVERIFIED`.

قبل أي backtest أو replay عام يجب تطبيق censor gap يمنع استخدام معلومات لاحقة لنقطة القرار. كما يجب حفظ اسم مصدر السوق، الفاصل الزمني، timezone، precision، وقت الجلب، ومعرف الأداة.

## 5. درجات الثقة والسمعة

الأهلية الأولية المقترحة للترتيب العام هي:

```text
analyst_id موجود
AND trust_tier في VERIFIED_LIVE / VERIFIED_HISTORY / RECONSTRUCTED
AND confidence_score >= 0.8000
AND يوجد حدث واحد على الأقل replay_status=VERIFIED
```

السجلات `MANUAL_ATTESTED` و`UNVERIFIED` تظهر في التاريخ والمراجعة، لكنها لا تدخل ترتيب المحلل العام. كما أن `HistoricalReputationSummary` منفصل عن `AnalystStats` الحالية حتى لا تختلط النتائج التاريخية بالأداء الحي.

المؤشرات المقترحة:

| المؤشر | الوصف |
|---|---|
| `historical_total_signals` | كل السجلات التاريخية المرتبطة بالمحلل أو القناة |
| `historical_verified_signals` | السجلات ذات مصدر موثق |
| `historical_rank_eligible_signals` | السجلات التي اجتازت كل شروط الثقة |
| `historical_excluded_signals` | غير المؤكد واليدوي والمرفوض |
| `verified_replay_events` | أحداث TP/SL/close المثبتة سوقيًا |
| `confidence_weighted_sample` | حجم عينة موزون بالثقة، منفصل عن العدد الخام |

## 6. الأدوات والتقنيات

| المجال | التقنية أو الأداة | الاستخدام |
|---|---|---|
| Backend | Python 3.12، FastAPI، SQLAlchemy | الخدمات والعقود وطبقة API |
| Telegram | `python-telegram-bot` للحي، Telegram API/TDLib مستقبلًا للتاريخ المصرح | استقبال الحاضر أو استيراد التاريخ بعد اعتماد الوصول |
| Database | PostgreSQL على Railway/Supabase، SQLite للاختبارات | الجداول والفهارس والقيود |
| Migrations | Alembic | migration `20251212_add_historical_signal_reconstruction` |
| Data validation | Pydantic/خدمات تحقق مخصصة | فحص manifest والوقت والمصدر |
| Hashing | SHA-256 | dedup وإثبات عدم تغير النص الخام |
| Market replay | Adapter abstraction لمصدر OHLCV/Tick | لا يثبت حدثًا دون نقطة زمنية صحيحة |
| Testing | pytest، fixtures الحالية، compileall | اختبارات الوحدة والتكامل ومنع التسرب الزمني |
| Quality | Flake8 الحرج، Bandit، pip-audit عبر CI | أمن وصحة الكود |
| Runtime | Docker، Supervisord، Railway | تشغيل worker والـ API وAlembic |
| Observability | Prometheus، logs، Outbox metrics | متابعة الاستيراد وإعادة التشغيل والأخطاء |
| Version control | GitHub، GitHub Actions، PR gates | فرع مستقل، CI، دمج محمي |

## 7. ما تم تنفيذه فعليًا

| المرحلة | الحالة |
|---|---|
| تدقيق مصادر Telegram والقيود | مكتمل، مع حفظ المصادر الرسمية |
| قرار فصل التاريخ عن الحاضر | مكتمل ومُوثق |
| Import Batch وDRY_RUN/VALIDATED | منفذ |
| Evidence مع hash وmessage ID وsource kind | منفذ |
| Historical Signal وtrust tier | منفذ |
| Timeline Events والقيود الزمنية | منفذ |
| منع market_as_of اللاحق للحدث | منفذ ومختبر |
| منع VERIFIED بلا سعر/مصدر/وقت سوق | منفذ ومختبر |
| Dedup للرسائل والـ events | منفذ ومختبر |
| Attribution للمحلل والقناة والمتداول | منفذ |
| Historical Trader Follow غير الحي | منفذ ومختبر |
| Historical Wallet Query | منفذ |
| Historical Reputation Summary منفصل | منفذ ومختبر |
| Migration | منفذة، revision `20251212_add_historical_signal_reconstruction` |
| PR/CI | PR #199 مدمج بعد نجاح push وpull_request |
| Railway | health 200، Outbox queue=0 |
| تشغيل مصدر Telegram تاريخي فعلي | غير منفذ |
| Market Replay adapter فعلي | غير منفذ |
| Parser عام لملفات Telegram | غير منفذ |
| واجهة Admin لاستيراد manifest | غير منفذة |

## 8. الاختبارات المنفذة

تم اجتياز:

```text
118 passed, 1 skipped
```

وتشمل الاختبارات حالات duplicate evidence، duplicate events، decision/event chronology، future market leakage، verified replay requirements، dry-run batch gate، historical trader follow، وثقة السجلات اليدوية. كما نجحت compileall وFlake8 الحرج وBandit وAlembic heads وgit diff check.

## 9. ما تبقى وخطة التنفيذ التالية

### المرحلة A — Controlled Import Adapter

يتم بناء manifest schema ثابت لملفات Telegram Export أو مصدر مصرح، مع batch preview يعرض عدد الرسائل، الرسائل غير القابلة للتحليل، duplicate، timestamps الناقصة، والقنوات غير المعروفة. لا يتم الإدخال النهائي قبل موافقة إدارية صريحة.

### المرحلة B — Telegram Authorized History

يتم اختيار مسار واحد فقط: Telegram Export يقدمه مالك القناة، أو حساب مستخدم مصرح يدير وصول MTProto/TDLib. يجب حفظ الأسرار خارج قاعدة البيانات، استخدام rate limits، تسجيل session owner، وتوثيق القناة التي تم الوصول إليها. لا يستخدم هذا المسار Bot Token وحده.

### المرحلة C — Historical Parser

يُعاد استخدام ParsingService الحالي قدر الإمكان، مع نسخة parser لا تنشئ Recommendation. كل نتيجة تحمل parse status، parse confidence، الحقول المستخرجة، النص الأصلي، وأخطاء التحليل. الرسائل ذات Entry/SL/TP الناقصة تبقى `PARSED_PARTIAL` أو `UNVERIFIED`.

### المرحلة D — Market Replay

يتم بناء interface مثل:

```python
get_point_in_time(asset, market, timestamp, interval) -> MarketObservation
```

ويجب على adapter إعادة السعر، الوقت الفعلي للملاحظة، مصدر البيانات، precision، وحالة coverage. لا يسمح replay بإثبات TP/SL إذا تجاوزت الملاحظة وقت الحدث أو كانت بعد censor gap.

### المرحلة E — Ownership and Analyst Link

يتم إنشاء workflow لإثبات أن القناة مملوكة أو مرتبطة بالمحلل، مع حالات `PROPOSED`, `VERIFIED`, `REJECTED`. لا يذهب التاريخ إلى ملف المحلل العام إلا بعد تحقق الملكية أو وجود Subscription/Channel relation موثقة.

### المرحلة F — Admin Review and Wallet UX

إضافة لوحة إدارية تعرض دفعات التاريخ، evidence، parse errors، trust tier، timeline، وقبول/رفض الإسناد. إضافة Historical Channel Wallet وHistorical Analyst History مع شارات واضحة: `Verified History`, `Reconstructed`, `Manual`, `Unverified`.

### المرحلة G — Reputation Gate

بعد توافر replay موثق، تتم إضافة historical metrics المنفصلة إلى `/find_analysts` و`/compare_analyst` مع عرض العدد الخام والعدد المؤهل ودرجة الثقة. لا يتم دمج التاريخ في Win Rate التجاري العام قبل Gate مستقل.

### المرحلة H — Observation and Rollback

تشغيل migration وadapter خلف feature flag، مراقبة import counts وrejects وreplay failures، حفظ rollback plan، وعدم لمس live Recommendation أو Outbox. أي خلل في التاريخ يجب ألا يؤثر على دورة التوصيات الحية.

## 10. المخاطر وضوابطها

| الخطر | الضابط |
|---|---|
| اختلاق TP/SL من السعر الحالي | market_as_of <= event_timestamp + source coverage check |
| خلط تاريخ محلل مجهول بمحلل معروف | attribution proof وحالة VERIFIED منفصلة |
| تكرار الاستيراد | dedup key وcontent hash وbatch manifest hash |
| تسرب زمني في التقييم | censor gap وحفظ as-of timestamps |
| تحويل التاريخ إلى تنفيذ حي | جداول منفصلة، لا Recommendation FK، لا Outbox، لا PriceStreamer |
| استيراد قناة غير مملوكة | ownership proof وAdmin review |
| تغيير المصدر بعد الإدخال | raw text hash وimmutable evidence semantics |
| كشف بيانات حساسة | عدم حفظ Telegram sessions في DB، تخزين secrets خارج التطبيق |
| ضغط Railway | dry-run، batching، rate limits، worker محدود، ومراقبة metrics |

## 11. القرار الحالي

تم اعتماد ودمج **Historical Foundation Gate** فقط. لا يجوز اعتبار تاريخ المحلل مثبتًا تجاريًا بعد. المرحلة التالية هي Controlled Historical Import ثم Market Replay حقيقي بمصدر موثوق. بعد اجتياز ذلك فقط يمكن إصدار قرار إدخال التاريخ في السمعة العامة.

## References

[1]: https://core.telegram.org/method/messages.getHistory "Telegram API: messages.getHistory"
[2]: https://core.telegram.org/bots/api "Telegram Bot API"
[3]: https://core.telegram.org/api "Telegram APIs: Bot API, Telegram API, and TDLib"

## تحديث Controlled Historical Import

بعد اعتماد الخطة، نُفذت الشريحة التالية على فرع مستقل وتشمل `HistoricalManifestService` للتحقق الجاف وحساب manifest hash، و`HistoricalImportService` لتسجيل الدفعات كـ `VALIDATED` أو `REJECTED` قبل الإدخال، و`TelegramExportAdapter` لقراءة Telegram Export دون اتصال حي.

كما نُفذ `HistoricalParserService` غير التشغيلي باستخدام قواعد التطبيع المشتركة دون استدعاء CreationService، ونُفذ `HistoricalMarketReplayService` لإعادة بناء ACTIVATED وTP وSL من observations ذات timestamp ومصدر وسعر، مع رفض observation بعد `replay_end` ورفض أي استخدام لبيانات مستقبلية.

تمت إضافة حقول المراجعة الإدارية إلى attribution: reviewer ووقت المراجعة والملاحظة، مع حالات `VERIFIED` و`REJECTED`. ولم يتم إضافة Telegram user-account credentials أو تشغيل MTProto/TDLib؛ ما زال adapter الحالي يعتمد على ملف Export مصرح به، وهذا مقصود لحماية الأسرار والتحكم في نطاق الوصول.

نتيجة الاختبارات الموسعة لهذه الشريحة: `129 passed, 1 skipped`، مع نجاح compileall وFlake8 الحرج وBandit وAlembic head وgit diff check. ما زال الإدخال الفعلي من قناة تاريخية حقيقية وmarket provider حقيقي اختبار قبول تشغيليًا متبقيًا، ولا تدخل البيانات التاريخية في live alerts أو Outbox أو PriceStreamer.

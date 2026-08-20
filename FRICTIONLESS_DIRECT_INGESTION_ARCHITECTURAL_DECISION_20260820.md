# قرار معماري: Frictionless Direct Historical Ingestion

**التاريخ:** 2026-08-20  
**المشروع:** CapitalGuard Pro v2  
**الحالة:** قرار معماري مقترح للاعتماد قبل التنفيذ

## الملخص التنفيذي

رؤية المالك صحيحة في جوهرها: **لا ينبغي أن يضطر المستخدم إلى معرفة channel code أو تذكر أوامر start وfinish حتى يرسل رسالة تاريخية إلى النظام**. هذه الأوامر يجب أن تصبح أدوات متقدمة للإدارة والاسترداد، لا جزءًا من المسار الطبيعي للمستخدم.

لكن رد المراجع يحتاج إلى تجريد إضافي. عبارة «أي إعادة توجيه = تاريخ مقبول» و«إلغاء كل بوابات الحماية» غير مناسبة ماليًا. القرار الصحيح هو:

> **أي رسالة Telegram معاد توجيهها من قناة تصبح Historical Candidate تلقائيًا، ويمكن تحليلها وعرض نتيجتها بشكل خاص، لكن لا تصبح Evidence موثقة أو تدخل السمعة العامة أو تنشئ توصية حية إلا بعد اجتياز طبقات التحقق المناسبة.**

بهذا القرار تنتقل البساطة إلى الواجهة، بينما تبقى الحماية في الخلفية. لا نطلب من المستخدم كود قناة في المسار العادي، ولا نرفض القناة الجديدة لمجرد أنها غير مسجلة، ولا نخلط التدقيق التاريخي مع التداول الحي. وفي الوقت نفسه لا ننشئ قناة موثقة تلقائيًا، ولا نمنح قناة مجهولة حق التأثير في ترتيب المحللين.

## تقييم رؤية المالك ورد المراجع

| العنصر | التقييم | القرار |
|---|---|---|
| إعادة توجيه أي رسالة من القناة دون `/start` | صحيح UX-wise | يعتمد كمسار افتراضي |
| التعرف التلقائي على القناة | صحيح | `find-or-create` لقناة ظل غير موثقة |
| تحديث القناة إن كانت موجودة | صحيح مع قيد | تحديث metadata فقط، لا تغيير الملكية أو حالة التوثيق |
| اعتبار forward تاريخيًا | صحيح كتصنيف معالجة | يسمى `Historical Candidate` وليس Evidence موثقة تلقائيًا |
| إلغاء `REJECTED_CHANNEL` | صحيح في مرحلة intake | يستبدل بـ `UNCLAIMED` أو `UNVERIFIED` عند القناة الجديدة |
| إلغاء كل session commands | صحيح للمستخدم العادي | تبقى جلسة داخلية مخفية مع debounce، وتبقى الأوامر للإدارة والاسترداد |
| إدخال القناة في الترتيب فورًا | غير مقبول | لا يدخل الترتيب العام إلا بعد Claim وVerification |
| إعادة السوق تلقائيًا | صحيح وظيفيًا | مهمة غير متزامنة، ولا تعرض نتيجة نهائية عند غياب OHLCV |
| الرد خلال ثانية دائمًا | مبالغ فيه | نعرض receipt سريعًا ثم نرسل النتيجة عند اكتمال replay |
| إزالة كل الحماية من أول خطوة | غير مقبول | نزيل الاحتكاك، لا نزيل التحقق والعزل والحدود التشغيلية |

## ما الخطأ في التصميم الحالي؟

التصميم الحالي وضع **هوية القناة** و**صلاحية النقل** و**صلاحية السمعة** في بوابة واحدة. لذلك أصبح المستخدم مطالبًا بأن يختار قناة مسجلة مسبقًا، ثم يبدأ batch، ثم يعيد التوجيه، ثم ينهيه. وقد ظهر ذلك عمليًا في `REJECTED_CHANNEL` رغم أن المستخدم كان يحمل مصدرًا صحيحًا، ثم ظهر dedup عالمي أعاد receipt من دفعة قديمة إلى دفعة جديدة [1] [2].

المشكلة ليست في وجود سجلات أو dry-run أو مراجعة مالك؛ هذه عناصر لازمة لمسار مالي تاريخي. المشكلة هي أن هذه العناصر ظهرت للمستخدم كخطوات إلزامية قبل أن يحصل حتى على نتيجة أولية. يجب فصل أربع طبقات كانت متداخلة:

1. **استقبال الرسالة:** هل وصلت من Telegram وتحمل origin حقيقيًا؟
2. **اكتشاف المصدر:** ما القناة التي جاءت منها؟ وهل هي مسجلة أم جديدة؟
3. **التدقيق:** هل يمكن تحليل النص وربطه ببيانات السوق؟
4. **السمعة والاعتماد:** هل يحق للنتيجة التأثير في ترتيب عام أو ملف محلل موثق؟

## النموذج المعماري المعتمد

```text
Telegram forward in private chat
        │
        ▼
Direct Historical Router
        │
        ├── genuine channel origin? ── no ──► UNVERIFIED / no attribution
        │
        ▼
Find-or-create source channel
        │
        ├── canonical ChannelCatalog exists ─► attach canonical identity
        └── otherwise ───────────────────────► create/reuse Shadow Channel
        │
        ▼
Hidden Auto Batch + Debounce Buffer
        │
        ▼
Immutable Forward Receipt
        │
        ▼
Historical Parser + Financial Consistency Check
        │
        ▼
Async Market Replay using source message timestamp
        │
        ├── private preview: allowed for eligible active users
        ├── unverified historical profile: allowed with explicit label
        └── public reputation / leaderboard: blocked until Claim + Verification
```

### القاعدة الأساسية للرسائل

في المحادثة الخاصة مع البوت، تكون الرسالة المعاد توجيهها من قناة **Historical Candidate** تلقائيًا. لا تدخل تلقائيًا إلى live parser ولا إلى `Recommendation` أو `UserTrade`. أما التوصية الحية فتدخل عبر `/log` أو `/newrec` أو عبر channel-post موثوق مخصص لمسار النشر، وليس عبر forward خاص غير مصنف.

هذا الفصل أبسط للمستخدم وأكثر اتساقًا معماريًا: **forward خاص = تاريخ، log/newrec = تداول حي**. ولا يعود النظام محتاجًا إلى تخمين نية المستخدم من نفس الرسالة.

## اكتشاف القناة دون تلويث السجل الرسمي

لا ينبغي أن ينشئ النظام صفًا موثقًا في `ChannelCatalog` لكل قناة يراها أول مرة. البديل هو كيان مستقل باسم `HistoricalShadowChannel` أو `DiscoveredChannel`، يحمل حالة `UNCLAIMED`.

| الحقل | الغرض |
|---|---|
| `telegram_channel_id` | الهوية التقنية القادمة من Telegram |
| `title` و`username` | metadata قابلة للتحديث، وليست إثبات ملكية |
| `first_seen_at` و`last_seen_at` | سجل الاكتشاف |
| `discovered_by_user_id` | من طلب التدقيق أول مرة |
| `sample_count` | عدد الرسائل المرصودة |
| `claim_status` | `UNCLAIMED`, `CLAIMED`, `VERIFIED`, `REVOKED` |
| `canonical_channel_catalog_id` | يملأ بعد الربط أو المطالبة |

تعمل خوارزمية `find-or-create` بهذا الترتيب: تبحث أولًا عن `ChannelCatalog` canonical بالـ Telegram ID، ثم تبحث عن Shadow Channel، ثم تنشئ shadow جديدة إذا لم تجد أيًا منهما. إذا كانت القناة موجودة، يحدث النظام `last_seen_at` وmetadata فقط. لا يتم تغيير المحلل المالك، ولا channel code، ولا public reputation بسبب forward واحد.

## ما الذي يعنيه «Historical»؟

إعادة التوجيه تعطي النظام نقطة زمنية للمصدر، لكنها لا تجعل البيانات صحيحة ماليًا تلقائيًا. لذلك نستخدم حالات واضحة:

| الحالة | المعنى |
|---|---|
| `RECEIVED` | وصلت رسالة forward إلى البوت |
| `CANDIDATE` | يوجد origin قناة يمكن ربطه تقنيًا |
| `UNVERIFIED` | المصدر مخفي أو الملكية غير مثبتة |
| `PARSED_PARTIAL` | تم استخراج جزء من البيانات فقط |
| `REPLAY_PENDING` | ينتظر شموع السوق أو عامل replay |
| `REPLAYED_PRIVATE` | تم حساب نتيجة خاصة للمستخدم |
| `VERIFIED_HISTORICAL` | اعتمدت الملكية وسلامة المصدر والسياسة |
| `PUBLIC_REPUTATION_ELIGIBLE` | يسمح فقط بعد Claim وVerification |

المحتوى الذي لا يحمل `forward_origin` حقيقيًا لا ينسب تلقائيًا إلى قناة. يمكن قبوله كـ `MANUAL_ATTESTED` في preview خاص، لكن لا يدخل attribution الموثق.

## الدفعات والجلسات: إخفاء التعقيد لا حذفه

المستخدم لا يحتاج إلى start وfinish. لكن النظام يحتاج داخليًا إلى batch حتى يحافظ على atomicity وdedup وdebounce والتقارير. لذلك يكون التدفق:

```text
أول forward → إنشاء AutoBatch مخفي
forward إضافي خلال 2–3 ثوانٍ → يضاف إلى نفس AutoBatch
سكون 2–3 ثوانٍ → إغلاق الالتقاط وتشغيل preview/replay
```

يبقى `historical_forward_start`, `historical_forward_finish`, و`historical_forward_cancel` متاحًا للمشرفين وللاختبار والاسترداد، لكنه لا يظهر كمتطلب للمسار الطبيعي. يجب أن يكون للـ AutoBatch حدود داخلية: عدد رسائل، حجم نص، مدة، rate limit، وانتهاء تلقائي. هذه ليست عوائق UX؛ إنها حماية تشغيلية من spam وDoS.

## نتيجة المستخدم والتدقيق السوقي

لا نعد المستخدم بنتيجة نهائية خلال ثانية؛ نرسل أولًا receipt بسيطًا:

```text
📥 تم استلام الرسالة التاريخية
القناة: CoinTellx
الحالة: Historical Candidate
المصدر: UNCLAIMED / أو Channel Code
جاري تحليل النص وإعادة تشغيل السوق...
```

ثم يرسل النظام بطاقة النتيجة عند اكتمال العمل:

```text
📜 Historical Preview
Asset: BTCUSDT
Source time: 2026-08-20 22:00 UTC
Replay: 1m OHLCV
Result: +0.16%
Confidence: UNVERIFIED
Public reputation: Not eligible
```

إذا لم تتوفر شموع السوق، تكون النتيجة `REPLAY_PENDING` أو `MARKET_DATA_UNAVAILABLE`، لا نتيجة تقديرية. وإذا كانت الرسالة معدلة، تحفظ `edit_date` ويظهر تحذير integrity عندما يكون التعديل لاحقًا لتحرك السعر.

## السمعة والملكية

الرد المرفق محق في نقل بوابة الملكية إلى طبقة السمعة، لكن يجب تحديدها بدقة. هناك فرق بين:

> **السماح للمستخدم برؤية تدقيق خاص** و**السماح للنظام بإدخال النتيجة في ترتيب عام**.

الأول يمكن أن يكون متاحًا لأي مستخدم نشط ضمن quotas. الثاني يتطلب، على الأقل، مصدر قناة قابلًا للإسناد، مراجعة التلاعب والتعديلات، تغطية سوقية كافية، وعدم وجود تعارض ملكية. وإذا ادعى محلل ملكية القناة، نحتاج Claim workflow يضيف حسابًا مصرحًا، دون نقل كل التاريخ السابق تلقائيًا إلى السمعة إلا بعد سياسة قبول واضحة.

## قرار القبول

### نعتمد من رؤية المالك

نعتمد المسار الافتراضي بنقرة واحدة: المستخدم يعيد توجيه أي رسالة قناة إلى الخاص، والنظام يكتشف المصدر ويبدأ historical preview دون طلب كود أو أمر. نعتمد Shadow Channel للقنوات الجديدة، ونبقي نتائج القنوات غير المطالب بها خاصة وموسومة بوضوح. نعتمد debounce داخليًا، ونعتمد الفصل الصريح بين forward التاريخي و`/log` أو `/newrec` الحي.

### لا نعتمد حرفيًا

لا نعتمد إنشاء `ChannelCatalog` موثق تلقائيًا، ولا إزالة origin validation، ولا إدخال القناة في leaderboard فورًا، ولا ضمان نتيجة خلال ثانية مهما كان توفر السوق، ولا حذف كل الحدود الداخلية. كما لا نعتمد عبارة «احتمالية أخطاء المقارنة صفر»؛ التطبيع يقلل خطأ النوع، لكن ما زالت هناك حالات origin مخفي، تعديل، duplicate، نقص OHLCV، ورسائل غير قابلة للتحليل.

## نطاق MVP المقترح

| المرحلة | المخرجات | بوابة القبول |
|---|---|---|
| M1 Direct Router | forward خاص يذهب للتاريخ تلقائيًا، وlive parser لا يلتقطه | لا يظهر `Analyzing forwarded message` ولا ينشئ live entities |
| M2 Shadow Discovery | find-or-create مع حالة `UNCLAIMED` وتحديث metadata | قناة جديدة لا تسبب رفضًا، ولا تدخل canonical reputation |
| M3 AutoBatch | debounce 2–3 ثوانٍ مع limits وidempotency | رسالة واحدة و10 رسائل تنتج batch واضحًا دون فقد أو تكرار |
| M4 Replay Preview | Parser + market replay غير متزامن | النتيجة تحمل source time وconfidence وdata availability |
| M5 Reputation Gate | Claim وVerification منفصلان عن preview | لا يظهر unclaimed source في leaderboard العام |
| M6 Admin Controls | status, cancel, review, quota, abuse controls | يمكن إيقاف/مراجعة أي batch دون لمس live Outbox |

## الخلاصة التنفيذية

رؤيتك ليست خاطئة؛ الخطأ كان في جعل حماية النظام تظهر كتعقيد للمستخدم. وفي المقابل، رد المراجع أصاب اتجاه UX لكنه بالغ في تبسيط القيود عندما اقترح فتح الإدخال وإزالة الحماية بالكامل.

**القرار المعتمد:** نعيد بناء الواجهة حول Frictionless Direct Ingestion، مع بقاء المعالجة التاريخية مرشحة وغير موثقة في البداية، وقناة الظل هي طبقة الاكتشاف، وAutoBatch هو التعقيد الداخلي المخفي، وClaim/Verification هما بوابة السمعة العامة. بهذا نصل إلى تجربة بسيطة فعلًا دون تحويل أي forward عشوائي إلى حقيقة مالية أو توصية حية أو سجل محلل موثق.

## مراجع داخلية

[1]: HISTORICAL_FORWARDING_INTAKE_DESIGN_20260819.md "التصميم الحالي لمسار Historical Forwarding"
[2]: HISTORICAL_FORWARDING_FALSE_REJECTION_DIAGNOSIS_20260820.md "تشخيص الرفض الكاذب وglobal dedup"
[3]: HISTORICAL_FORWARDING_REAL_TEST_DIAGNOSIS_20260820.md "تشخيص اختبار Telegram الأول"

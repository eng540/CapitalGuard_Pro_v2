# Historical Signal Reconstruction — Owner Input and Acceptance Checklist

## 1. ما تم تنفيذه دون حاجة إلى تدخل المالك

تم بناء طبقة البيانات التاريخية، دفعات الاستيراد، dry-run manifest، Telegram Export adapter، parser غير تشغيلي، Market Replay مع منع التسرب الزمني، إسناد المحلل والقناة والمتداول، مراجعة الملكية، Historical Wallet، وفصل المقاييس التاريخية عن الأداء الحي. تم دمج الشريحة الحالية في `main` عبر PR #200، ونجحت الاختبارات وCI وRailway.

هذه الطبقات لا تحتاج إلى Telegram session أو API secret من المالك في الوقت الحالي، لأنها تعمل على عقد ملفات Export ومصادر سوق مجردة وخلف بوابات أمان.

## 2. ما يحتاجه النظام من المالك

### 2.1 مصدر التاريخ

يجب توفير أحد الخيارين التاليين، ولا يجب إرسال كلمات مرور أو session strings داخل المحادثة:

| الخيار | المطلوب من المالك | النتيجة |
|---|---|---|
| Telegram Export | ملف JSON/ZIP رسمي للقناة أو مجموعة القنوات القديمة | المسار الأبسط والأكثر أمانًا للبدء |
| Authorized History Connector | قرار صريح باستخدام حساب مستخدم مصرح عبر MTProto/TDLib | يحتاج تصميم أسرار وتشغيل ومراجعة وصول قبل التنفيذ |

يفضل البدء بملف Telegram Export لعينة صغيرة من قناة واحدة، وليس كامل أرشيف النظام. يجب أن تكون القناة مملوكة للمالك أو أن يملك المالك إذنًا واضحًا من صاحبها.

### 2.2 تعريف القناة والملكية

لكل قناة تاريخية يجب تزويد النظام بالحد الأدنى التالي:

```text
telegram_channel_id: -100...
channel_title: اسم القناة
channel_username: @channel إن وجد
owner_type: ANALYST / SYSTEM / UNKNOWN
analyst_user_id أو analyst_code: إن كان معروفًا
ownership_proof: رابط أو تأكيد إداري أو مستند ملكية
historical_period_start: YYYY-MM-DD
historical_period_end: YYYY-MM-DD
```

إذا كانت ملكية القناة غير مؤكدة، يمكن استيرادها في `Channel Historical Wallet`، لكن لا تُنسب إلى ملف محلل ولا تدخل السمعة العامة.

### 2.3 مصدر بيانات السوق

يجب تحديد مصدر يسمح ببيانات تاريخية مناسبة للأصل والسوق والفاصل الزمني. المطلوب ليس السعر الحالي، بل بيانات عند أو قبل كل حدث تاريخي.

| الحقل | مثال |
|---|---|
| provider | Binance / مزود آخر معتمد |
| market | Spot / Futures |
| symbols | BTCUSDT، ETHUSDT |
| intervals | 1m أو 5m أو 1h بحسب الدقة المطلوبة |
| timezone | UTC |
| retention period | المدة التي يمكن للمصدر تغطيتها |
| credentials | تُضاف عبر Railway secrets فقط، لا تُرسل في الرسائل |

إذا لم يتوفر مصدر يغطي فترة التوصيات، يبقى الحدث `UNVERIFIED` ولا يتم احتساب Win Rate أو PnL تاريخي له.

## 3. ما يفعله المالك عمليًا الآن

أول خطوة مطلوبة هي تجهيز **عينة قبول صغيرة**:

| المطلوب | النطاق المقترح |
|---|---|
| عدد القنوات | قناة واحدة في البداية |
| عدد الرسائل | 20 إلى 100 رسالة |
| الفترة | أسبوع أو شهر معلوم |
| النوع | رسائل توصيات واضحة، مع بعض التحديثات والإغلاقات إن وجدت |
| المصدر | Telegram Export رسمي |
| الملكية | تأكيد مالك القناة أو المحلل |
| السوق | تحديد Spot/Futures ومصدر البيانات |

بعد وصول الملف والبيانات الوصفية، ينفذ النظام بالترتيب التالي:

```text
استلام الملف
→ فحص hash وmanifest
→ DRY_RUN
→ تقرير المقبول/المرفوض/المكرر
→ موافقة المالك
→ VALIDATED batch
→ Parsing
→ Ownership review
→ Market Replay
→ Timeline report
→ قرار Historical Gate
```

## 4. ما لا يجب إرساله

لا ترسل كلمات مرور Telegram، أرقام التحقق، session strings، ملفات cookies، مفاتيح API داخل النص، أو بيانات حسابات شخصية. إذا احتاج النظام مصدرًا خاصًا، يتم إنشاء secret في Railway أو استخدام ملف مرفوع للمشروع حسب مسار الاعتماد المعتمد.

## 5. معايير قبول العينة

لا تعتبر العينة ناجحة إلا إذا تحققت الشروط التالية:

| المعيار | شرط النجاح |
|---|---|
| المصدر | كل رسالة لها مصدر وطابع زمني واضح |
| التكرار | إعادة الاستيراد لا تنشئ سجلات ثانية |
| parser | تظهر نسبة parsed وpartial وunparsed مع حفظ النص الخام |
| الملكية | القناة منسوبة أو موسومة UNKNOWN بوضوح |
| الزمن | لا يوجد event قبل القرار أو market_as_of بعد الحدث |
| السوق | كل حدث VERIFIED له مصدر وسعر ووقت سوق |
| المحفظة | متابعة المتداول التاريخية لا تنشئ UserTrade حيًا |
| السمعة | غير المؤكد واليدوي لا يدخل الترتيب العام |
| التشغيل | Live Recommendation وOutbox وPriceStreamer لا تتأثر |

## 6. قرارات يجب أن يعتمدها المالك

يحتاج النظام قرارًا واضحًا بشأن ما إذا كان التاريخ سيظهر للعامة كـ `Verified History` أو سيبقى داخليًا حتى اكتمال مراجعة أوسع. كما يجب تحديد الحد الأدنى للعينة، فترة السماح، وسياسة التعامل مع الرسائل المعدلة أو المحذوفة.

الافتراض الافتراضي الآمن هو إبقاء التاريخ داخليًا، وإظهار شارة الثقة، وعدم دمجه في ترتيب المحلل حتى وجود مصدر سوق وملكية موثقين.

## 7. الخطوة التالية بعد تسليم العينة

بعد استلام عينة Export وبيانات القناة، يتم تنفيذ dry-run محليًا، ثم تقرير قبول، ثم PR صغير منفصل إذا احتاج parser أو adapter إلى تعديل. لا يتم نشر أي تاريخ فعلي إلى Railway قبل موافقة المالك على نتيجة dry-run.

> لا تحتاج إلى تشغيل أوامر Git أو Railway. المطلوب منك هو توفير العينة المصرح بها، تعريف القناة والمحلل، تحديد مصدر السوق، واعتماد تقرير dry-run. بقية التحقق والترحيل والاختبارات والدمج يتولاها مسار التنفيذ الهندسي.

## 8. Authorized user-account dry-run

The owner may run the first read-only test from a controlled workstation after installing the optional connector requirements. Do not place the phone number, login code, 2FA password, API hash, or session contents in Git or chat.

```bash
pip install -r requirements-history-connector.txt
export TELEGRAM_HISTORY_API_ID='provided-by-my.telegram.org'
export TELEGRAM_HISTORY_API_HASH='provided-by-my.telegram.org'
export TELEGRAM_HISTORY_SESSION_PATH='/secure/path/capitalguard-history-reader.session'
export HISTORY_READER_ACCOUNT_ALIAS='owner-history-reader'
python scripts/telegram_history_dry_run.py --channel-id '-1001234567890' --max-pages 2 --page-size 100
```

The first authorization may request the phone, Telegram login code, and 2FA password interactively in the controlled terminal. The command prints counts, issues, manifest hash, and checkpoint only; it does not persist evidence and does not create live recommendations. After reviewing the report, the owner provides the report and confirms that the account is authorized to read the channel. The session file remains local and is never uploaded as an ordinary attachment.

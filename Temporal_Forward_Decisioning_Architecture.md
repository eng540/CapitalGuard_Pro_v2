# Temporal Forward Decisioning Architecture

## Executive decision

الفجوة التي ظهرت في Forward Intake حقيقية. القرار لا ينبغي أن يعتمد على كون المستخدم أرسل أمرًا معينًا أو على ترتيب Telegram handlers فقط. **زمن الرسالة، زمن إعادة التوجيه، زمن التعديل، زمن الحدث، السعر عند كل زمن، وتسلسل الرسائل** هي التي تحدد طبيعة السجل وحالته.

إعادة توجيه رسالة اليوم لا تجعلها توصية اليوم. وبالمقابل، رسالة منشورة قبل دقيقة قد تكون صالحة لمسار live إذا كان سعرها ما يزال داخل نطاق صلاحية الدخول ولم تظهر لها نتيجة نهائية. لذلك نحتاج محرك قرار زمني سعري واحدًا قبل اختيار live أو historical.

## المشكلة في النموذج الحالي

النموذج الحالي يلتقط الرسالة في أحد مسارين ثم يحاول تفسيرها. هذا يعكس القرار: الاستقبال يسبق فهم الزمن والسعر. والنتيجة أن الرسالة نفسها قد تبدو live أو historical فقط بسبب handler أو الأمر المستخدم، رغم أن الوقائع الزمنية والسعرية تقول شيئًا مختلفًا.

هناك أربعة أزمنة يجب عدم دمجها:

| الزمن | المعنى |
|---|---|
| `source_time` | وقت نشر الرسالة في القناة كما يثبته Telegram Forward Origin |
| `event_time` | وقت الحدث المالي المذكور أو المستنتج، مثل تعديل الوقف أو تحقق الهدف |
| `received_time` | وقت وصول الـ Forward إلى البوت |
| `ingested_time` | وقت حفظه داخل النظام |

ويجب إضافة زمن خامس عند وجود تعديل:

```text
edit_time = وقت آخر تعديل معروف للرسالة الأصلية
```

لا يجوز استخدام `received_time` بدل `source_time` لحساب الأداء التاريخي، ولا استخدام `now` لتفسير سعر كان يجب قياسه عند `source_time`.

## Temporal Decision Engine

يقترح النظام خدمة موحدة:

```text
TemporalDecisionService.decide(
    source_time,
    event_time,
    received_time,
    edit_time,
    source_origin,
    parsed_payload,
    market_snapshots,
    related_timeline,
)
```

تنتج الخدمة قرارًا قابلًا للتدقيق لا مجرد Boolean:

```text
mode
confidence
reason_codes
price_validity
age_seconds
market_as_of
timeline_relation
replay_readiness
requires_review
```

## الحالات الأساسية

| القرار | متى يستخدم | النتيجة |
|---|---|---|
| `LIVE_ELIGIBLE` | رسالة حديثة، origin موثوق، لا نتيجة نهائية، والسعر ما زال داخل envelope الصلاحية | Live Review فقط؛ لا إنشاء تلقائي قبل التأكيد |
| `LIVE_STALE` | الرسالة حديثة نسبيًا لكن السعر خرج من envelope أو تجاوزت freshness | لا Recommendation حية؛ تُحفظ كـ stale candidate أو historical candidate |
| `HISTORICAL_RECONSTRUCTION` | المصدر قديم، أو توجد نتيجة نهائية، أو السعر الحالي لم يعد يمثل لحظة القرار | Evidence ثم Parser ثم Market Replay عند توفر OHLCV |
| `UPDATE_EVENT` | الرسالة رد أو تحديث مرتبط بتوصية أصلية أو signal timeline قائم | تُضاف كحدث append-only ولا تنشئ توصية جديدة |
| `CLOSED_EVENT` | الرسالة تتضمن إغلاقًا أو نتيجة نهائية | تُربط بالتوصية/الإشارة الأصلية، أو تُنشئ Historical Event غير مستقل إذا لم يوجد parent |
| `EDITED_AFTER_MARKET` | `edit_time` متأخر عن حدث سوقي أو نتيجة مسجلة | Evidence جديدة revision، مع flag للتلاعب أو إعادة الكتابة |
| `UNVERIFIED_TIME` | لا يوجد source origin حقيقي أو الزمن متناقض | لا Attribution موثوق ولا ranking |
| `DUPLICATE` | نفس المصدر والرسالة والإصدار محفوظة مسبقًا | لا أثر جديد، مع receipt idempotent |

## Price Validity Envelope

لا يكفي السؤال: «كم عمر الرسالة؟». يجب السؤال: «هل ما زال سعر الرسالة صالحًا للتنفيذ أو الحكم؟».

لذلك يحسب النظام:

```text
risk_budget_pct = abs(entry - stop_loss) / entry
entry_drift_pct = abs(current_price - entry) / entry
age_seconds = received_time - source_time
```

ثم يستخرج:

```text
freshness_score
price_distance_score
market_data_quality_score
price_validity_score
```

ويستخدم envelope قابلًا للتهيئة حسب السوق ونوع الأمر، وليس رقمًا عالميًا ثابتًا. في حالة LIMIT تكون المقارنة مع نطاق الدخول، وفي حالة MARKET تكون المقارنة مع السعر وقت المصدر مع حد انزلاق، وفي حالة رسالة UPDATE تكون المقارنة مع آخر حالة معروفة.

قاعدة مهمة:

> السعر الحالي يستخدم فقط لتحديد صلاحية المسار الحي. أما الحكم التاريخي فيستخدم السعر التاريخي عند `market_as_of=source_time` أو `event_time`.

إذا لم تتوفر OHLCV تاريخية، لا يختلق النظام نتيجة؛ يحفظ الحالة `REPLAY_PENDING`.

## Signal Timeline

كل رسالة معاد توجيهها تصبح عقدة في Timeline، لا Recommendation مستقلة بالضرورة:

```text
INITIAL_SIGNAL
  ├── AMENDMENT
  ├── ENTRY_UPDATE
  ├── STOP_UPDATE
  ├── TARGET_UPDATE
  ├── PARTIAL_EXIT
  ├── TARGET_HIT
  └── CLOSE
```

الربط يعتمد بالترتيب على:

```text
source_chat_id
reply_to_message_id
asset + side
time proximity
entry/stop/target fingerprints
semantic event type
```

ويجب حفظ كل إصدار كما وصل. التعديل لا يستبدل الرسالة القديمة، بل ينشئ `revision+1` مع مقارنة واضحة:

```text
before → after
changed_fields
edit_time
market_state_before_edit
```

## Unified Forward Intake

المعمارية المستهدفة هي:

```text
Telegram Forward
      ↓
ForwardCaptureAdapter
      ↓
TemporalNormalizer
      ↓
Parser + Event Classifier
      ↓
TemporalDecisionService
      ↓
SignalTimelineResolver
      ├── LIVE_REVIEW
      ├── HISTORICAL_EVIDENCE
      ├── UPDATE_EVENT
      ├── CLOSED_EVENT
      └── UNVERIFIED_QUARANTINE
```

بهذا تصبح live وhistorical **مخرجين لقرار واحد**، وليسا مدخلين متنافسين. ولا يحق لأي handler أن يقرر وحده أن الرسالة live أو historical.

## UX المقترح

يجب أن تعرض بطاقة الاستقبال سبب القرار، لا النتيجة فقط:

```text
📥 Forward captured
Source: CryptoTerraNet
Source time: 2026-08-20 04:36 UTC
Received: 2026-08-20 04:36 UTC
Age: 18 seconds
Market as-of: pending
Price validity: 0.91
Decision: LIVE_REVIEW
Reason: FRESH_SOURCE_WITHIN_ENTRY_ENVELOPE
```

وعند رسالة قديمة:

```text
📜 Historical candidate
Source time: 2026-08-19 21:10 UTC
Received: 2026-08-20 04:40 UTC
Age: 7h 30m
Decision: HISTORICAL_RECONSTRUCTION
Replay: PENDING_MARKET_DATA
Reason: SOURCE_AGE_AND_TERMINAL_UPDATE
```

وعند التحديث:

```text
🔄 Timeline update
Parent: HIST-...
Event: STOP_MOVED_TO_ENTRY
Event time: ...
Price as-of: ...
Revision: 2
```

## الميزات الإبداعية والعملية المساندة

### 1. Temporal Explainability Card

كل قرار يحتوي على reason codes قابلة للعرض والتدقيق، حتى يعرف المستخدم لماذا اعتبر النظام الرسالة تاريخية أو غير صالحة للـ live.

### 2. Time-Travel Market Snapshot

عرض السعر، السبريد، وحالة السوق عند زمن الرسالة لا عند زمن المشاهدة الحالية، مع علامة `DATA_SOURCE` ودرجة تغطية السوق.

### 3. Counterfactual Live Check

عند تصنيف رسالة تاريخية، يعرض النظام: «لو وصلت الرسالة في وقتها، هل كانت صالحة؟» مع الفرق بين سعر المصدر وسعر السوق التاريخي.

### 4. Timeline Reconciliation

خدمة تجمع الرسائل الأولية والتحديثات والإغلاقات في سلسلة واحدة، وتكشف التناقضات مثل إغلاق قبل تفعيل أو TP قبل Entry.

### 5. Revision and Tamper Chain

كل تعديل يضيف revision immutable، ويقارن حالة السوق قبل وبعد التعديل. لا يتم حذف النسخة السابقة.

### 6. Replay Readiness Score

درجة من 0 إلى 1 مبنية على اكتمال source time، OHLCV coverage، parse completeness، وtimeline continuity. لا تسمح الدرجة وحدها بالـ ranking لكنها تحدد أولوية المراجعة.

### 7. Stale Signal Watcher

مراقب يكتشف توصيات وصلت متأخرة، ويمنع تفعيلها الحي إذا تجاوزت صلاحية السعر، مع اقتراح تحويلها إلى Historical Reconstruction.

### 8. Conflict Resolver

إذا قالت الرسالة `TP1 HIT` لكن OHLCV لا يغطي الحدث، يحتفظ النظام بالادعاء كـ `SOURCE_ASSERTED` ونتيجة السوق كـ `MARKET_UNVERIFIED` بدل دمجهما أو اختيار أحدهما بصمت.

## مراحل التنفيذ

| المرحلة | المخرج |
|---|---|
| T1 | Value objects: `TemporalContext`, `PriceValidity`, `TemporalDecision` |
| T2 | TemporalNormalizer يفصل source/event/received/ingested/edit times |
| T3 | ForwardIntakeRouter واحد بدل قرار موزع بين handlers |
| T4 | SignalTimelineResolver وربط reply/amendment/close |
| T5 | PriceValidityService مع market snapshot وOHLCV coverage |
| T6 | UX explanation cards وreason codes وreplay readiness |
| T7 | اختبارات الزمن، السعر، الإصدارات، التناقضات، والعزل الحي/التاريخي |
| T8 | تشغيل تدريجي خلف feature flag ثم Gate Owner Acceptance |

## القرار النهائي

يُعتمد الزمن والسعر وتسلسل الأحداث كطبقة القرار الأولى. لا يتم تصنيف الرسالة live أو historical بسبب أمر المستخدم أو ترتيب handler وحده. أمر المستخدم يمكن أن يعبّر عن النية، لكنه لا يتغلب على حقائق الزمن والسوق:

```text
intent + temporal facts + price validity + timeline state
                         ↓
                 auditable decision
```

الخطوة البرمجية التالية يجب أن تكون تنفيذ `TemporalDecisionService` و`ForwardIntakeRouter` خلف feature flag، ثم ترحيل Direct Router القديم والـ live parser لاستخدامهما. لا نضيف مسارًا ثالثًا، ولا نحذف المسار القديم؛ نوحد نقطة القرار ونبقي مخرجات live وhistorical منفصلة.

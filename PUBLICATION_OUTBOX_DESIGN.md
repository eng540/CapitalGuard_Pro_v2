# Recommendation Publication Outbox

## الهدف

تحويل نشر توصيات المحللين إلى عملية دائمة وقابلة للاستئناف، بحيث لا يعتمد نجاح النشر على بقاء مهمة `asyncio.create_task` حية داخل عملية Telegram، ولا يؤدي تكرار worker أو إعادة النشر إلى إرسال الرسالة نفسها مرتين.

## الكيان

يُنشأ سجل `PublicationDelivery` لكل زوج `(recommendation_id, telegram_channel_id)` ولكل نوع عملية نشر. المفتاح الفريد يمنع أكثر من سجل تسليم أولي لنفس التوصية والقناة.

| الحقل | الغرض |
|---|---|
| `recommendation_id` | التوصية المطلوب نشرها |
| `telegram_channel_id` | القناة المستهدفة |
| `operation` | `CREATE`, `UPDATE`, `REPLY`, `CLOSE` |
| `status` | `PENDING`, `PROCESSING`, `SENT`, `RETRY`, `FAILED` |
| `attempts` | عدد المحاولات |
| `next_attempt_at` | موعد المحاولة التالية |
| `telegram_message_id` | معرف الرسالة بعد النشر |
| `last_error` | آخر خطأ قابل للتشخيص |
| `idempotency_key` | مفتاح فريد دائم للعملية |
| `created_at/updated_at/sent_at` | التدقيق الزمني |

## قواعد الحالة

```text
PENDING → PROCESSING → SENT
                    ↘ RETRY → PROCESSING
                    ↘ FAILED بعد استنفاد المحاولات
```

لا تُعتبر التوصية `PUBLISHED` إلا بعد نجاح سجلات القنوات المطلوبة أو تسجيل `PARTIAL_FAILURE` صريح. فشل Telegram لا يتراجع عن حفظ التوصية، لكنه يبقى مرئيًا في PublicationDelivery وقابلًا لإعادة المحاولة.

## قواعد idempotency

يكون المفتاح الأولي للنشر:

```text
recommendation:{recommendation_id}:channel:{telegram_channel_id}:operation:CREATE
```

أما التحديثات والردود فتستخدم مفتاحًا يضم `event_id`، وبذلك لا يعاد إرسال نفس الحدث عند إعادة تشغيل worker.

## المعالجة

يُنشأ Outbox داخل نفس معاملة حفظ التوصية والقنوات المستهدفة. يقوم worker دوري أو مهمة تشغيلية بانتقاء السجلات `PENDING/RETRY` التي حان وقتها باستخدام lock مناسب، يحولها إلى `PROCESSING`، يرسلها، ثم يحفظ النتيجة في نفس سجل التسليم. عند فشل الشبكة يُستخدم backoff محدود، وعند استنفاد المحاولات تتحول الحالة إلى `FAILED` ويصل تنبيه إداري.

## نطاق الدفعة الحالية

هذه الدفعة تنفذ النموذج والهجرة وقيد idempotency وسجل المحاولة، وتبقي المسار الحالي متوافقًا. ربط worker الدائم مع كل عمليات UPDATE/REPLY/CLOSE يأتي في نفس المسار بعد تثبيت عقد البيانات والاختبارات.

## معايير القبول

1. لا يمكن إنشاء سجلين `CREATE` لنفس التوصية والقناة.
2. يمكن إعادة تشغيل worker بعد توقفه دون فقدان السجل.
3. يتم حفظ `telegram_message_id` فقط بعد نجاح الإرسال.
4. يتغير `attempts` و`last_error` عند الفشل.
5. توجد اختبارات للحفظ، القيد الفريد، الانتقال، وإعادة المحاولة.

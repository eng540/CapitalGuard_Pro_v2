# G7-CON-02 — Decision Traceability Contract

## الغرض

يضيف هذا PR عقدًا نقيًا لتتبع handoff من canonical input إلى decision preparation. الهدف هو منع قرار مجهول المصدر، وتثبيت `source_ref` و`correlation_id` و`causation_id` و`input_hash` و`trace_id` دون إدخال persistence أو event bus جديد.

## القاعدة

لا تكون نتيجة القرار قابلة للاستهلاك التشغيلي إلا إذا أمكن إرجاعها إلى مصدر semantic معروف. إذا غاب `source_ref` يفشل handoff بدل إنتاج قرار غير قابل للمراجعة.

## الحقول

| الحقل | الوظيفة |
|---|---|
| `source_ref` | مرجع الرسالة/المراجعة/الدليل الذي أنتج canonical input |
| `correlation_id` | ربط القرار بسياق command أو batch أو workflow |
| `causation_id` | تحديد الحدث أو الأمر الذي سبب القرار |
| `input_hash` | بصمة canonical input وevidence |
| `trace_id` | هوية مستقرة للسلسلة الحالية |
| `contract_version` | نسخة عقد التتبع |

## الحدود

العقد لا يكتب إلى قاعدة البيانات، ولا ينفذ network I/O، ولا ينشئ Recommendation أو UserTrade أو Execution State. لا يسمح بتجاوز canonical/domain validation، ولا يغيّر G5 أو G6 أو Lifecycle أو Alert أو Trust.

## إعادة الاستخدام

يعيد هذا PR استخدام `OperationalDecisionService` من PR-G7-CON-01 وعقود الملكية والمعاملات والأحداث السابقة. أي persistence أو propagation فعلي للـtrace يحتاج PR لاحقًا يحدد المالك وحدود المعاملة.

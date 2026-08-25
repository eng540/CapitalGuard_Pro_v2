# Historical Attribution Schema Fix

## Production symptom

ظهر في سجل الإنتاج خطأ PostgreSQL من نوع `UndefinedColumn` أثناء استدعاء `GET /api/webapp/owner/historical-trust-readiness`: النموذج `HistoricalSignalAttribution` يقرأ العمود `reviewed_at`، بينما قاعدة البيانات التشغيلية لا تحتويه. نفس الإصدار من النموذج يحتوي أيضًا على `review_note`، لذلك يعالجه الإصلاح معًا.

## Root cause

الـ migration الأصلية لإنشاء `historical_signal_attributions` تحتوي `reviewed_at` و`review_note`، لكن قاعدة legacy في الإنتاج وصلت إلى revision لاحقة مع مخطط ناقص. migration الإصلاح السابقة عالجت `reviewed_by_user_id` فقط، ولم تكن idempotent repair مكتملة لحالة review state.

## Fix

أضيفت migration `20260825_repair_historical_attribution_review_state` بعد head الحالية. تفحص الأعمدة الموجودة ثم تضيف فقط الأعمدة الناقصة:

| Column | Type | Nullable | Data impact |
|---|---|---:|---|
| `reviewed_at` | `TIMESTAMP WITH TIME ZONE` | نعم | لا يوجد؛ القيم القديمة تبقى NULL |
| `review_note` | `TEXT` | نعم | لا يوجد؛ القيم القديمة تبقى NULL |

لا تحذف migration بيانات، ولا تغيّر السجلات الحالية، ولا تنشئ جدولًا أو مسارًا موازيًا. سيطبقها entrypoint الحالي تلقائيًا عند النشر عبر `alembic upgrade head`.

## Verification

- migration upgrade/downgrade harness: ناجح.
- اختبار regression للمشكلة: **1 ناجح**.
- اختبارات `HistoricalSignalService`: **9 ناجحة**.
- اختبارات `HistoricalSignalMaterializationService`: **5 ناجحة**.
- Alembic single-head: ناجح، والـ head الجديدة هي `20260825_repair_historical_attribution_review_state`.
- Python compilation: ناجح.

## Deployment note

بعد دمج ونشر الإصلاح، يجب انتظار نجاح migration قبل استقبال traffic، ثم فحص `/health` واستدعاء endpoint `historical-trust-readiness` بحساب owner مصادق. لا ينبغي تنفيذ `ALTER TABLE` يدويًا إذا كان entrypoint يعمل بصورة صحيحة؛ migration idempotent هي مصدر التغيير الوحيد.

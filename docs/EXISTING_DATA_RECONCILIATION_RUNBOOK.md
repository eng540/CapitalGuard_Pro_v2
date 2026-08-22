# Existing-data Reconciliation Runbook

هذا الدليل يعدّ verifier **read-only ومقنّعاً** لاختبار نسخة PostgreSQL معزولة ومستعادة من snapshot مقنّع. لا يستخدم Railway production ولا يطلب أو يسجل كلمات مرور أو URLs أو صفوفاً شخصية أو مالية.

## الضوابط

| الضابط | التطبيق |
|---|---|
| البيئة | قاعدة منفصلة من snapshot مقنّع فقط |
| هدف الاتصال | `RECONCILIATION_EXPECTED_DB` يطابق `current_database()` قبل أي قراءة |
| الكتابة | المعاملة تبدأ بـ`SET TRANSACTION READ ONLY` |
| المخرجات | revision، counts مجمعة، status buckets، وعدد FK غير الموثقة فقط |
| المحظور | URLs، أسرار، معرفات مستخدمين، نصوص توصيات، أو أي صفوف مالية |

## التشغيل

بعد الاستعادة إلى قاعدة معزولة وتسميتها صراحةً، يضبط المالك محلياً متغيرات البيئة في جلسة آمنة ثم يشغّل:

```bash
RECONCILIATION_EXPECTED_DB=<isolated_database_name> \
PYTHONPATH=src python scripts/reconcile_existing_data.py
```

سجل المالك النتيجة JSON بعد التأكد أنها لا تحتوي إلا على counts وstatus buckets وrevision. يرفق timestamp وcommit SHA واسم بيئة وصفي غير حساس (مثل `staging-restored`) ولا يرفق رابط الاتصال.

> نجاح verifier في CI يثبت قابلية تشغيله على PostgreSQL معزول فقط. إغلاق بوابة Existing-data reconciliation يتطلب لاحقاً نتيجة snapshot مقنّع حقيقية يراجعها المالك؛ لا يعتبر هذا الـartifact دليلاً بديلاً عنها.

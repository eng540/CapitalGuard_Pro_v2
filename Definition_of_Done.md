# Definition of Done

## 1. قاعدة عامة

لا تعتبر المهمة مكتملة لأن الكود يعمل محليًا فقط. الإغلاق يتطلب تحققًا من الكود والاختبارات والبيانات والأمن والتشغيل والتوثيق والدليل.

> **Done = Code + Tests + Validation + Security + Documentation + Migration + Regression + PR + Evidence**

## 2. مستوى Task

| البند | شرط الإغلاق |
|---|---|
| Scope | المهمة مرتبطة بـ Requirement/Story ولا تحتوي توسعًا غير مصرح |
| Code | التغيير محدود، قابل للقراءة، ولا يكرر abstraction غير ضروري |
| Error handling | الحالات الصحيحة والخاطئة واضحة ولا يوجد silent failure غير موثق |
| Tests | Unit أو Integration مناسب، مع negative case واحد على الأقل |
| Data | أي schema/query موثق ومختبر على DB مناسبة |
| Security | مراجعة ownership/secrets/input عند انطباقها |
| Observability | log/metric مفيد دون PII غير ضروري |
| Docs | تحديث RUNBOOK/API/contract عند تغير السلوك |
| Evidence | سجل اختبار أو screenshot/log قابل للإرفاق |

## 3. مستوى Story

يجب أن تنجح جميع Tasks التابعة، ويجب أن يوجد Integration test يثبت الرحلة الكاملة. يجب أن تكون Acceptance Criteria قابلة للتحقق آليًا، وأن تتم مراجعة حالات الفشل والتراجع.

## 4. مستوى PR

| البند | Required |
|---|---|
| Branch | فرع مستقل من آخر `main` أو branch معتمد |
| Review | مراجعة تقنية واحدة على الأقل، وأمنية عند الحساسية |
| Tests | `pytest -q` وtests المتخصصة ناجحة |
| Static | `compileall` وBandit High وpip-audit |
| Migration | `heads` واحد وupgrade/rollback evidence عند تغيير schema |
| Diff | `git diff --check` وغييرات خارج النطاق مرفوضة |
| Rollback | وصف كيف يمكن التراجع أو تعطيل Feature Flag |
| Release note | أثر التغيير على التشغيل والبيانات واضح |

## 5. مستوى Gate

لا تغلق Gate إلا عند نجاح كل P0 وعدم وجود evidence مفقود. يسمح بـ `CONDITIONAL GO` فقط إذا كانت المخاطر غير المانعة مصنفة بمالك وموعد. لا يسمح بـ `GO Alpha` مع فشل Recovery أو E2E أو ownership.

## 6. قواعد خاصة

عند تغيّر State Machine يجب إعادة تشغيل reports وE2E. عند تغيّر secrets أو webhook يجب تشغيل Security tests. عند تغيّر migration يجب اختبار قاعدة فارغة ونسخة بيانات. عند تغيّر alert logic يجب اختبار duplication وreconnect وclose idempotency.

# Definition of Done

## 1. القاعدة

> **Task Done ≠ Gate Closed.**

`Task Done` يتطلب code/tests/docs/security/rollback. أما `Gate Closed` فيتطلب أيضاً evidence خارجي ومالك قرار. ويجب أن تسجل الوثائق الحالة بصيغة `BUILD_DONE` أو `EVIDENCE_OPEN` أو `GATE_CLOSED` أو `HOLD`.

## 2. تعريف الإنجاز للمهمة وPR

| المجال | الشرط |
|---|---|
| Traceability | Requirement ID وbacklog/story متصلان بالـ PR. |
| Scope | لا تغيير خارج النطاق؛ لا feature تجاري ضمن PR حوكمة. |
| Tests | tests مناسبة وتشمل negative case؛ وتشغل suite المتأثرة. |
| Data | migration/rollback/fresh-existing evidence عند انطباقها. |
| Security | ownership/input/secrets/PII reviewed عند الحساسية. |
| Metrics | أثر العقد أو event أو KPI موثق. |
| Operations | log/metric/runbook/degraded behavior عند الخدمات الحية. |
| Evidence | command، environment، time، result، artifact أو سبب عدم التطبيق. |

## 3. تعريف إغلاق البوابة

كل P0 يجب أن يكون PASS، ولا يوجد evidence مفقود، ويوجد Owner Decision مكتوب. لا يسمح `CONDITIONAL GO` بـ Alpha أو تجارة عند فشل recovery أو E2E أو ownership أو financial reconciliation.

## 4. قواعد خاصة

أي تغيير State Machine يعيد tests للتقارير وE2E. تغيير metrics يعيد reference reconciliation. تغيير secret أو auth يعيد negative tests ويضيف rotation evidence. تغيير migration يعيد fresh/existing/restore evidence.

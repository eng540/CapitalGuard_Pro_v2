# G7 Decision-to-Operational-State Boundary — PR-G7-OWN-04

## الغرض

يثبت هذا العقد شروط انتقال الحقيقة الدلالية المقبولة إلى نتيجة تحليل أو Recommendation أو UserTrade أو Execution State. لا ينفذ انتقالًا فعليًا ولا ينشئ كيانًا أو معاملة؛ بل يحدد boundary قابلًا للاختبار قبل أي PR سلوكي لاحق.

## القاعدة

```text
Canonical Accepted
→ Domain Validation
→ Application Decision Use Case
→ Creation/Lifecycle Boundary
→ Explicit Execution Gate
```

لا يجوز لـAI output أو Web payload أن يصبح مدخلًا تشغيليًا مباشرًا. الحالات `CANONICAL_INCOMPLETE` و`CANONICAL_AMBIGUOUS` و`AI_CANDIDATE` لا تتجاوز boundary؛ تُرفض أو تُعزل أو تعاد للمراجعة.

## الانتقالات

| الانتقال | المالك | الشرط |
|---|---|---|
| Canonical → Analysis | Application decision use case | canonical validation وprovenance |
| Analysis → Recommendation | `CreationService` عبر use case | domain validation وpolicy |
| Recommendation → UserTrade | `LifecycleService` عبر typed command | authorization وvalid state transition |
| UserTrade → Execution | `AutoTradeService` عبر execution command | risk وcredentials وAUTO_TRADE_ENABLED وTRADE_LIVE_ENABLED |

## متطلبات مشتركة

كل انتقال يملك audit requirement وidempotency requirement وfailure policy. لا يملك التحليل أو read model حق تعديل Recommendation أو UserTrade، ولا يملك execution أي صلاحية لتجاوز risk أو live gates.

## نطاق PR

يشمل هذا PR العقد والاختبارات والتوثيق فقط. لا يغير CreationService أو LifecycleService أو AutoTradeService أو API أو G5/G6 أو state machine الحالية. أي تطبيق سلوكي للعقد يجب أن يأتي في PR مستقل يثبت أثره على جميع callers والاختبارات.

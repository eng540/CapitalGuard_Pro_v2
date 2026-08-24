# G7-CON-03 — Operational Admission Policy

## الغرض

يثبت هذا PR خطوة البناء التالية بعد عقود الملكية والتتبع: لا تنتقل نتيجة decision إلى Recommendation إلا عبر admission صريح يحمل actor وcommand identity وdecision fingerprint وtrace id.

## ما يفعله العقد

يقبل `OperationalDecision` الناتج من canonical accepted input، ويتحقق من target والهوية، ثم يعيد `RecommendationAdmission`. هذا القبول لا ينشئ Recommendation ولا يغير Lifecycle ولا يرسل Notification ولا يستدعي Exchange.

## الحالات

| الحالة | المعنى |
|---|---|
| `READY_FOR_EXPLICIT_COMMAND` | البيانات مستوفية، لكن لا يزال يلزم تنفيذ command منفصل |
| `REQUIRES_REVIEW` | يجب أن يمر القرار بمراجعة قبل command |

## ضمانات

- لا يقبل input غير canonical accepted.
- لا يقبل decision target خاصًا بالتنفيذ.
- يشترط `actor_ref` و`command_id`.
- يحمل `decision_fingerprint` و`trace_id` إلى command boundary.
- يرفض payload يحاول تفعيل `execution_allowed`.
- لا يملك persistence أو transaction أو network I/O.

## ما يبقى خارج هذا PR

لا يربط هذا PR admission فعليًا بـ`CreationService` أو `WebCommandService`، ولا يغير Recommendation أو UserTrade أو Risk أو Trading أو Ranking أو Trust. الربط السلوكي يحتاج PR مستقلًا يحدد authorization وtransaction owner وcommand idempotency وlifecycle effects.

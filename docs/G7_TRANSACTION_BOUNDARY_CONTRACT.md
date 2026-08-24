# G7 Transaction and Unit-of-Work Ownership Contract — PR-G7-OWN-02

## الغرض

يحدد هذا العقد مالك حدود المعاملة لكل عملية تشغيلية حساسة في النظام. لا يغير هذا PR سلوك الخدمات الحالية ولا ينقل commit/rollback فعليًا؛ بل يضيف سياسة نقيّة قابلة للاختبار تمهيدًا لامتدادات سلوكية منفصلة ومراجعة مستقلة.

## القاعدة الأساسية

> Application Use Case / Unit of Work owns the transaction boundary.

لا يجوز لـDomain/Application Service تنفيذ `commit()` أو `rollback()` عشوائيًا على transaction يملكها caller. عند الحاجة إلى atomicity فرعية أو عزل سباق uniqueness، يستخدم المسار Savepoint صريحًا. لا يجوز استخدام `session.rollback()` العام كآلية معالجة عامة داخل transaction خارج ملكية الخدمة.

## الحدود المعتمدة

| العملية | المالك | النطاق | الشبكة داخل المعاملة |
|---|---|---|---:|
| `WEB_COMMAND` | Application Use Case | Short Command | لا |
| `HISTORICAL_MATERIALIZATION` | Application Use Case | Aggregate Operation | لا |
| `REPLAY_RUN` | Application Use Case | Bounded Batch Item | لا |
| `OUTBOX_DELIVERY` | Worker Use Case | Delivery State Transition | لا |
| `ALERT_ACTION` | Worker Use Case | Action Application | لا |
| `LIVE_EXECUTION` | Application Use Case | Aggregate Operation | لا |

## Replay Run مقابل Database Transaction

Replay Run وحدة تشغيل وتتبّع تاريخية، وليست معاملة قاعدة بيانات واحدة. يجب أن تكون جلب البيانات الخارجية خارج transaction، وأن تكون commit boundaries محدودة بحيث لا يؤدي فشل signal أو evidence واحدة إلى محو transaction يملكها caller أو إلى تعليق معاملة طويلة أثناء provider/network I/O.

## Failure and retry

كل عملية تحدد commit boundary وrollback boundary وsavepoint policy وretry boundary وfailure isolation وrecovery semantics. يجب أن تكون حالات الفشل قابلة للمراجعة، وألا يتحول فشل provider أو delivery إلى نجاح صامت.

## نطاق PR

يشمل هذا PR contract وtests وdocumentation فقط. لا يغير G5/G6 Replay أو migrations أو ORM models أو runtime wiring، ولا يعيد بناء Unit of Work الحالي. أي تغيير سلوكي يحتاج PR مستقلًا يثبت أثره على callers والاختبارات.

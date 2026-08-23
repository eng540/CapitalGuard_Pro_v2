# تقرير تنفيذ G5 — Historical Signal Materialization

**الحالة:** `READY_FOR_REVIEW`  
**النطاق:** تجسيد مسودات G4 المقبولة فقط إلى `HistoricalSignal` تاريخية موثقة، مع بقاء Replay وMarket Evidence وRanking وTrust والتداول الحي خارج هذا العمل.

## النتيجة التنفيذية

أصبح المسار `materialize(draft_id)` آمناً عند الطلبين المتزامنين للـDraft المقبولة نفسها. تستخدم العمليتان قيد الهوية الموجود على `HistoricalSignalMaterialization.draft_id` كحقيقة idempotency، وتحافظان على قيد `HistoricalSignal.public_ref` بدلاً من تخفيفه. عند حدوث `IntegrityError` في flush للإشارة أو للجسر، تُغلق المعاملة المتعارضة ثم تُستعاد المادة القائمة للـDraft نفسها؛ وإذا لم توجد مادة قائمة، يُعاد الخطأ بدلاً من إخفائه.

| بند التحقق | الدليل النهائي |
|---|---|
| اسم اختبار التزامن | `verify_g5_postgres_concurrency.py` |
| مسار الاختبار | `scripts/verify_g5_postgres_concurrency.py` |
| قاعدة البيانات | خدمة PostgreSQL الموجودة في وظيفة `fresh-postgres-migration` داخل CI |
| الجلسات والمعاملات | جلستان SQLAlchemy مستقلتان، ومعاملتان مستقلتان، وحاجز `threading.Barrier(2)` لبدء السباق في الوقت نفسه |
| المدخل المتزامن | `accepted_draft_id` واحدة في العمليتين |
| النتيجة | `HistoricalSignal` واحدة و`HistoricalSignalMaterialization` واحدة، وتعيد العمليتان `signal.id` نفسها |
| قيود البيانات | بقيت UNIQUE وFK وreferential integrity فعالة؛ لم تُحذف أو تُضعف |
| CI | جميع فحوص PR #335 خضراء، بما فيها `fresh-postgres-migration` التي تشغّل harness الحقيقي |
| فحص المنصة | `core_health=ok` و`web_health=ok` و`v1_status=noncommercial` |

## الإصلاح المحدود

كان السباق يصل إلى `session.flush()` الخاص بـ`HistoricalSignal` قبل نطاق الاسترداد السابق، ولذلك يظهر تعارض `public_ref` كاستثناء غير معالج. اقتصر الإصلاح على توسيع نفس معالجة `IntegrityError` الموجودة لتغطي flush الإشارة وflush الجسر، ثم الاستعلام عن جسر الـDraft نفسه بعد rollback. لم يُغيّر معنى `public_ref` الحتمي، ولم يُدخل UUID عشوائياً أو retry loop أو آلية تزامن موازية.

## حدود لا تزال سارية

> **G5 لا يشغّل Replay تلقائياً، ولا ينشئ Market Evidence أو Ranking أو Trust أو Recommendation/UserTrade حي.**

كما أن materialization لأحداث lifecycle تظل مرتبطة بـparent materialization موثق وتعيد استخدام الإشارة الأصلية، ولا تنشئ إشارة تاريخية مستقلة من حدث منفصل.

## قرار المرحلة

G5 **جاهز للمراجعة** وفق نطاقه المعتمد، بما في ذلك إثبات التزامن الحقيقي على PostgreSQL. لا يبدأ أي Gate لاحق ضمن هذا التقرير.

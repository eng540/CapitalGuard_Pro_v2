# G5 Historical Signal Materialization — Codebase Reconnaissance

**الحالة:** قرار تصميم قبل التنفيذ الوظيفي.  
**النطاق:** G5 فقط؛ لا Replay أو Market Evidence أو Ranking أو Trust أو توصية/تداول حي.

## القرار التنفيذي

> المسار الرسمي الجديد الوحيد هو: `G1 Canonical Message → G2 Interpretation → G3 Candidates → G4 ACCEPTED Draft → G5 Materialization → HistoricalSignal`.

لا يجوز لـG5 استخراج أو استكمال قيم مالية صامتاً. وإذا غابت قابلية التتبع أو الزمن أو القبول، فالنتيجة هي `MATERIALIZATION_BLOCKED` بسبب ظاهر قابل للتشخيص.

| المكوّن القائم | المسؤولية الفعلية | القرار | أثر G5 |
|---|---|---|---|
| `HistoricalSignal` | السجل التاريخي الموحد، evidence، timestamp، events وattribution | **REUSE + EXTEND** | يعاد استخدامه؛ يضاف رابط فريد إلى G4 Draft مقبولة لضمان idempotency على مستوى قاعدة البيانات. |
| `HistoricalSignalEvidence` | raw source immutable وملكية المصدر ووقت الرسالة | **REUSE** | يبقى مصدر الدليل ولا تنشأ طبقة Evidence موازية. |
| `HistoricalCanonicalMessage` / `HistoricalMessageRevision` | هوية المصدر والمراجعة والزمن وreceipt/evidence provenance | **REUSE** | تستخدم كمرجع provenance وtemporal integrity، لا ينسخ النص عبر طبقات G5. |
| `HistoricalContentInterpretation` | تفسير G2 المثبت على revision | **REUSE** | يدخل في chain المسودة فقط؛ لا يعاد تفسيره في G5. |
| `HistoricalFinancialCandidate` | مرشحات G3 القابلة للمراجعة | **REUSE** | لا يقبل G5 إلا المرشحات المشار إليها من مسودة G4 المقبولة. |
| `HistoricalRecommendationDraft` | G4 Draft + review/override + evidence chain | **REUSE** | هو بوابة القبول الوحيدة للمادة التاريخية. |
| `HistoricalSignalService.create_signal` | writer legacy مباشر من evidence/parser | **DEPRECATE / BLOCK** لمسار forward/replay | لا يستدعى من Evidence Ingestion أو Replay بعد G5؛ يحتفظ به مؤقتاً فقط للتوافق الموثق إلى أن ترحّل الاختبارات القديمة. |
| `HistoricalEvidenceIngestionService._ensure_replayable_signal` و`ensure_replayable_signals` | ينشئان HistoricalSignal مباشرةً بعد parser | **BLOCK + MIGRATE** | يوقف الإنشاء المباشر ويُستبدل بالربط G1–G4؛ لا يصبح batch replayable لمجرد parse. |
| Replay/Binance/OHLCV | Market evidence ونتيجة السوق | **DO_NOT_TOUCH** | لا يستدعيه G5 ولا يكتب artifacts أو events سوقية. |
| Ranking/Trust/Recommendation/UserTrade/Outbox | طبقات أداء أو كيان حي | **DO_NOT_TOUCH** | لا تتغير ولا تستقبل أي ناتج مباشر من G5. |

## قيود G5 الإلزامية

| الشرط | تحقق G5 |
|---|---|
| بوابة القبول | `HistoricalRecommendationDraft.status == ACCEPTED` فقط. |
| الحد الأدنى للمصدر | revision مرتبطة بـcanonical message وevidence، وsource timestamp قابل للإثبات. |
| سلامة الزمن | `signal_timestamp` لا يسبق source timestamp؛ lifecycle اللاحق يمثل Signal/Event تاريخياً مستقلاً ولا يعدّل الأصل. |
| Provenance | signal → accepted draft → candidate IDs → interpretation → revision → canonical message → evidence/raw source. |
| التكرار والتزامن | `UNIQUE(draft_id)` على رابط materialization، مع معالجة `IntegrityError` لإرجاع السجل نفسه. |
| الحجب | draft غير مقبولة، evidence/revision/timestamp غائب، candidate chain ناقصة أو متعارضة، أو lifecycle بلا draft أصلية مقبولة = `MATERIALIZATION_BLOCKED`. |
| العزل | لا `HistoricalMarketEvidence`، لا `HistoricalSignalEvent` سوقي، لا ranking، لا trust، لا `Recommendation`، لا `UserTrade`، لا outbox. |

## المسارات الموروثة التي يجب ترحيلها

المسار السابق `Evidence Ingestion → Parser → HistoricalSignal → Replay` يخالف قاعدة G5 لأنه ينشئ السجل من evidence قبل G4 Review. سيُحجب هذا الإنشاء في نفس PR G5؛ ولا يُعاد تشغيل batch تلقائياً. الدفعات القديمة لا تُرحّل بصمت: تُظهر حالة **MATERIALIZATION_BLOCKED_LEGACY_PATH** إلى أن تمر عبر workflow قانوني يثبت G1–G4.

## نطاق التنفيذ التالي

1. إضافة جدول رابط `HistoricalSignalMaterialization` يحمل `draft_id` الفريد وسبب الحجب ووقت materialization والمراجع المصدرية من دون إنشاء HistoricalSignal موازية.
2. إضافة خدمة `HistoricalSignalMaterializationService.materialize(draft_id)` تعيد HistoricalSignal القائمة أو تنشئ واحدة ذرّياً من Draft مقبولة فقط.
3. تعديل ingestion/replay لتتوقف عن `create_signal` المباشر، ثم نقل الاختبارات القديمة إلى G5 workflow أو اختبار حجب migration الصريح.
4. إضافة اختبارات قبول/حجب/idempotency/concurrency/temporal integrity والعزل لجميع أنواع lifecycle المعتمدة.

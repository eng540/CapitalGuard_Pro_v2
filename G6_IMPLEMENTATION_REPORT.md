# G6 IMPLEMENTATION REPORT

## 1. Executive Summary

تم تنفيذ امتدادات G6 الدنيا على فرع `feature/g6-historical-replay-integration` اعتمادًا على `main` كمصدر الحقيقة، وعلى وثيقتي `G6_ARCHITECTURE_EXECUTION_SPEC.md` و`G6_REUSE_AND_GAP_REPORT.md` المقدمتين سابقًا كمرجع Contract.

تم التحقق أولًا من شرط G5 على `main` عند `8220f2ca8449d6ea763052e6f54412fc625af31c`. اختبارات G5 الأساسية نجحت، ولم يظهر blocker مباشر يستلزم تعديل G5؛ لذلك لم يُعدّل G5 ولم تُفتح مسارات G4/G5.

النتيجة الحالية هي **G6 implementation baseline جاهز للمراجعة، وليس تصريحًا ببدء G7**. يستهلك الأمر الجديد `HistoricalSignal` مرتبطة فعليًا بـ`HistoricalSignalMaterialization`، وينشئ هوية `HistoricalReplayRun` قابلة للتتبع، ويربط Evidence وReplay Events بها، ويمنع Ranking refresh داخل مسار G6، ويصنف تعارض OHLCV كـ`AMBIGUOUS`، ويسجل data-as-of غير المثبتة كـ`UNVERIFIABLE`.

## 2. Exact Scope Implemented

يشمل التنفيذ:

| المجال | الحالة |
|---|---|
| HistoricalSignal | `REUSE`; لا ينشئ G6 Signal جديدة |
| HistoricalSignalMaterialization | `REUSE`; G6 يرفض Signal بلا bridge G5 مطابق |
| HistoricalMarketReplayService | `REUSE + EXTEND` |
| HistoricalMarketEvidence | `REUSE + EXTEND` عبر `replay_run_id` وmetadata |
| HistoricalSignalEvent | `EXTEND` عبر `replay_run_id` وخيار عزل Ranking |
| HistoricalReplayGateService | `EXTEND` عبر `assess_replay()` الذي لا يمنح Reputation eligibility |
| BinanceHistoricalOhlcvProvider | `REUSE` |
| BinanceClient | `REUSE`; لم يُعد تصميمه |
| Web G6 command | `IMPLEMENT` كأمر owner-only مشتق من Signal identity |
| ReplayRun identity | `IMPLEMENT` كامتداد minimal مطلوب لأن `replay_run_ref` وحده لم يكن كافيًا |
| Ranking / Trust / Reputation | `DO_NOT_TOUCH`؛ لا تعديل للمعايير أو الحالات |
| Trading / UserTrade / Parser / AI | `DO_NOT_TOUCH` |

## 3. Existing Architecture Reused

المسار الرسمي المنفذ هو:

```text
G5 HistoricalSignalMaterialization
        ↓
HistoricalSignal
        ↓
Owner-only G6 replay command
        ↓
ReplayRun
        ↓
BinanceHistoricalOhlcvProvider / BinanceClient
        ↓
HistoricalMarketReplayService
        ↓
HistoricalSignalEvent + HistoricalMarketEvidence
```

G6 لا يعيد materialization، ولا ينشئ Signal جديدة، ولا يستخدم `HistoricalSignalService.create_signal()` كاختصار للمسار الرسمي.

## 4. REUSE / EXTEND / IMPLEMENT Decisions

### REUSE

تمت إعادة استخدام نماذج `HistoricalSignal` و`HistoricalSignalMaterialization`، ومحرك Replay الموجود، ومزود Binance والعميل، وآليات owner authorization وWebCommandAudit الحالية.

### EXTEND

تم تمديد `HistoricalMarketEvidence` و`HistoricalSignalEvent` بربط اختياري إلى ReplayRun. وتم تمديد `HistoricalSignalService.record_event()` بخيار `refresh_ranking` مع بقاء السلوك legacy الافتراضي كما هو. وتم تمديد Gate بواجهة replay-only منفصلة دلاليًا عن Reputation.

### IMPLEMENT

تم إنشاء نموذج `HistoricalReplayRun`، وmigration واحدة minimal، وأمر Web G6 owner-only، واختبارات Contract مباشرة لـG5 precondition وReplayRun وEvidence linkage وidempotency وOHLCV ambiguity وRanking isolation.

### DEPRECATE / MIGRATE LATER

يبقى المسار legacy `HistoricalSignalService.create_signal()` موجودًا دون حذف أو إعادة تصميم. كما يبقى أمر Web batch القديم كما هو خارج مسار G6 الجديد، ولا يُستخدم لإثبات G6 E2E الرسمي.

## 5. Files Modified

```text
src/capitalguard/application/services/historical_market_replay_service.py
src/capitalguard/application/services/historical_signal_service.py
src/capitalguard/application/services/historical_replay_gate_service.py
src/capitalguard/application/services/web_command_service.py
src/capitalguard/interfaces/api/routers/webapp.py
src/capitalguard/infrastructure/db/models/historical_signal.py
src/capitalguard/infrastructure/db/models/__init__.py
```

## 6. Files Created

```text
src/capitalguard/infrastructure/db/models/historical_replay_run.py
alembic/versions/20260823_add_historical_replay_runs.py
tests/test_g6_historical_replay_integration.py
G6_IMPLEMENTATION_REPORT.md
```

## 7. Database Changes

تمت إضافة جدول `historical_replay_runs` بالحقول الخاصة بالهوية، والإشارة إلى Signal وG5 materialization، وrequest fingerprint، وReplay version، وpolicy version، والحالة، والنافذة، وprovider metadata، وfetched timestamp، وdata-as-of status، وambiguity، وquality، والنتيجة، والفشل، وأزمنة التشغيل.

تمت إضافة `replay_run_id` اختياري إلى `historical_market_evidence` و`historical_signal_events` مع Foreign Keys وindexes. الترحيل يستخدم Alembic batch mode لتغطية SQLite التحقّقية، كما اجتاز اختبار upgrade/downgrade على schema قديم في قاعدة مؤقتة.

لا توجد قاعدة بيانات Replay موازية ولا Market Evidence database موازية.

## 8. Replay Run Contract

كل G6 run يملك:

```text
run_ref
signal_id
materialization_id
request_fingerprint
replay_version = G6-R1
policy_version = G6-OHLCV-UTC-1
status
window_start / window_end
interval / limit_count
provider / endpoint / provider_metadata
data_source
fetched_at
data_as_of_status
ambiguity_status
quality_status
result_json / failure_reason
started_at / completed_at / failed_at
```

إعادة الطلب بنفس Signal وG5 materialization والنافذة والنسخة تعيد استخدام ReplayRun المكتمل بدل إنشاء تشغيل مكرر أو استدعاء provider مرة أخرى.

## 9. Temporal Model

يشتق أمر Web G6 النافذة من `HistoricalSignal.decision_timestamp`، ولا يسمح للعميل بإرسال asset أو entry أو SL أو TP أو decision timestamp أو window حر. النافذة الحالية هي `SOURCE_TIMESTAMP_PLUS_24H`، interval `1m`، وحد provider قدره `1500` candle.

يتم رفض timestamp غير timezone-aware، ورفض candle بعد replay end، وتُستبعد candle قبل decision timestamp من event processing. وقد تسجل تلك candle داخل artifact كبيانات سياقية؛ لذلك يجب تفسير artifact coverage، لا event timeline فقط.

Binance الحالية لا تثبت data availability التاريخية بمعنى data-as-of مستقل؛ لذلك يسجل G6:

```text
data_as_of_status = UNVERIFIABLE
status = COMPLETED_UNVERIFIABLE
quality_status = UNVERIFIABLE
```

ولا يحول `fetched_at` إلى `data_as_of`.

## 10. Window Semantics

النافذة مشتقة من Source Truth وليس من Web payload. حدودها timezone-aware، ونهايتها شاملة في provider/model الحالي وفق timestamps المقبولة، مع interval محدد وحد أقصى للشموع. لا يسمح الأمر الجديد بتغيير هذه السياسة من الواجهة.

## 11. Lifecycle Model

يسجل ReplayRun `source_lifecycle` من materializations المرتبطة بنفس Signal، مرتبًا حسب `source_timestamp` ثم ID، ويحافظ على:

```text
materialization_id
 draft_id
 materialization_kind
 draft_kind
 revision_id
 source_timestamp
 related_materialization_id
```

لا تنشأ HistoricalSignal جديدة لتحديثات lifecycle. وتبقى نتيجة G6 Evidence/Replay facts، لا Outcome نهائيًا.

## 12. Ambiguity Model

في G6، إذا احتوت candle واحدة على TP وSL في الوقت نفسه، فلا يتم اختيار ترتيب لمس غير مثبت. ينشئ Replay event واحدًا:

```text
event_type = AMBIGUOUS
replay_status = AMBIGUOUS
quality_status = UNVERIFIABLE
```

وتسجل السياسة القديمة في metadata بصيغة `PESSIMISTIC_SL_FIRST_INFERRED` للدلالة على أنها سياسة محافظة وليست حقيقة سوقية. أما المسار legacy المباشر فيحافظ على سلوكه السابق ولا يُستخدم لإثبات G6.

## 13. Market Evidence Model

يعاد استخدام `HistoricalMarketEvidence`. في G6 يرتبط Evidence بـReplayRun عبر `replay_run_id`، ويحمل `replay_run_ref` نفسه، ويخزن artifact hash وprovider وendpoint وinterval والنافذة وعدد الشموع وmetadata تتضمن Replay version وfetched time وdata-as-of status وambiguity وquality.

Evidence لا تعدل HistoricalSignal، ولا تعني تلقائيًا Profit أو Win أو Loss أو Ranking أو Trust.

## 14. Provider Provenance

يستخدم G6 `BinanceHistoricalOhlcvProvider` و`BinanceClient` الموجودين. يسجل provider والendpoint وdata source وfetched_at وmetadata. لم يتم اختراع provider أو عميل جديد. لأن provider لا يثبت provider algorithm version مستقلًا، يسجل الحقل `provider_version = UNVERIFIED` بدل التخمين.

## 15. Transaction Boundary

الـcaller/UoW يملك transaction boundary. خدمات G6 لا تنفذ `commit()` أو `rollback()` على transaction الخاصة بالcaller. تمت إحاطة إنشاء ReplayRun وEvidence وEvent بـSavepoint عند الحاجة لمعالجة uniqueness races دون rollback شامل.

ReplayRun ليس database transaction. وفشل provider يعبر عن نفسه في Run status وfailure reason، مع بقاء القرار النهائي للـUoW. لا يوجد `session.rollback()` داخل G6 لمعالجة IntegrityError.

## 16. Idempotency

تستخدم ReplayRun request fingerprint مع uniqueness، وتستخدم event dedup keys تتضمن ReplayRun في مسار G6، بينما يبقى artifact key deterministic. كما يستمر WebCommandAudit في حماية idempotency key وrequest fingerprint على مستوى الأمر.

## 17. Concurrency

تم تجهيز uniqueness وSavepoint لسباق إنشاء ReplayRun وEvidence وEvents. الاختبارات المحلية تثبت السلوك على SQLite، أما PostgreSQL الحقيقي فيبقى شرط CI/PR لأن بيئة التنفيذ المحلية لا تحتوي PostgreSQL أو Docker/Podman.

## 18. Authorization

أضيف endpoint:

```text
POST /api/webapp/owner/historical-signals/{signal_id}/g6-replay
```

وهو owner-only، ويتطلب Core service authorization وactor identity وidempotency key. يرسل Web فقط Signal identity؛ ويشتق Core نافذة Replay وبقية source parameters.

## 19. Observability

يُعاد في response:

```text
signal_id
materialization_id
replay_run_id
replay_run_ref
status
event_count
replay_version
ambiguity_status
quality_status
failure_reason
commercial_enabled = false
```

كما يسجل WebCommandAudit الأمر، ويسجل ReplayRun metadata والنتيجة وأرقام Evidence وEvents.

## 20. Failure Isolation

لا يعدل G6 HistoricalSignal أو G5 source truth. فشل provider ينتج Run `FAILED` مع سبب، وفشل temporal validation أو missing materialization يرفض التشغيل. لا يتم تسجيل provider failure كنجاح في G6 Web audit.

## 21. Ranking / Trust Isolation

يستدعي G6 `record_event(..., refresh_ranking=False)`، لذلك لا ينفذ `refresh_ranking_eligibility()` من مسار G6. لم تتغير معايير Ranking أو Trust أو Reputation. وأضيفت `assess_replay()` إلى Gate بحيث تكون `reputation_eligible=False` حتى عند `REPLAY_READY`.

## 22. Tests

أضيفت اختبارات G6 لـ:

```text
G5 materialization precondition
ReplayRun identity
Evidence linkage
Replay event linkage
idempotent replay reuse
provider call reuse
OHLCV TP+SL ambiguity
G6 replay-only gate isolation
source lifecycle capture
```

## 23. PostgreSQL Verification

لا يوجد PostgreSQL محلي في بيئة التنفيذ الحالية، ولذلك لم أعدّ SQLite دليلًا كافيًا على concurrency PostgreSQL. تم تجهيز الاختبارات والقيود، ويجب أن يثبت CI/PR الحقيقي:

```text
PostgreSQL migration
PostgreSQL uniqueness
PostgreSQL concurrent ReplayRun creation
PostgreSQL event/evidence Savepoint behavior
```

## 24. Real E2E

المسار الواقعي الذي يثبته التنفيذ يبدأ من `HistoricalSignal` الناتجة من G5 materialization، وليس من `create_signal()` legacy. اختبارات G6 تستخدم fixture G5 الفعلية الموجودة في اختبارات materialization، ثم تمرر Signal إلى Replay service وتثبت Evidence وEvents وRun.

E2E الإنتاجي مع Binance الحقيقي لا يُنفذ تلقائيًا داخل الاختبارات لتجنب الاعتماد على شبكة خارجية وبيانات متغيرة؛ provider boundary يبقى قابلًا للاستبدال في الاختبار فقط.

## 25. Multi-Lifecycle E2E

تم حفظ مصدر lifecycle من G5 materializations المرتبطة بالـSignal داخل ReplayRun result. لم يُنشأ Outcome أو Performance من lifecycle، ولم يُختلق Market Event من مصدر الرسالة. دعم market replay التفصيلي لكل update type يظل محدودًا بقدرات `HistoricalSignal` وmaterialization الحالية.

## 26. CI Results

الفحوصات المحلية المكافئة الحالية:

```text
flake8 critical errors: PASS
bandit high severity: PASS
compileall: PASS
alembic heads: PASS
pytest: 288 passed, 1 skipped
```

اختبار migration upgrade/downgrade على schema قديم SQLite: PASS. أما PostgreSQL وGitHub Actions وPR فتبقى نتائجها مرتبطة بالتشغيل بعد رفع الفرع.

## 27. Technical Debt

تظل data-as-of semantics غير مثبتة من Binance provider، ولذلك الحالة `COMPLETED_UNVERIFIABLE` مقصودة. كما أن `HistoricalReplayGateService.assess()` القديم يحتفظ بواجهة compatibility التي تجمع replay وreputation، بينما `assess_replay()` هو المسار المعزول المخصص لـG6.

يبقى المسار batch القديم خارج أمر G6 الجديد، ويحتاج قرار migration مستقل إذا أريد جعله واجهة G6 الرسمية. لا يستخدم G6 هذا المسار لإثبات E2E الرسمي.

## 28. Deferred Items

تم تأجيل ما يلي:

- إعادة تصميم G5 أو تعديل G5 transaction semantics العامة.
- حذف `HistoricalSignalService.create_signal()`.
- إعادة تصميم Ranking أو Trust أو Reputation.
- Outcome finalization وPerformance.
- Trading وLive Trading.
- AI/LLM evidence.
- Provider historical availability proof الكامل.
- PostgreSQL concurrency evidence إلى CI/PR.
- تحويل batch legacy إلى G6 official path.
- أي G7.

## 29. Not Implemented

لم يتم تنفيذ Ranking أو Trust أو Reputation أو Performance أو Trading أو AI أو Parser أو G1 أو G2 أو G3 أو G4 أو إعادة بناء G5 أو Replay Engine أو Market Evidence model موازٍ.

## 30. Commit / PR Numbers

تُملأ هذه الخانة بعد commit وفتح PR على GitHub. لا يُعتبر هذا التقرير تصريحًا ببدء G7.

---

## Final Status

```text
G5_PRECONDITION: PASS — NO G5 BLOCKER FOUND
G6_IMPLEMENTATION: READY FOR REVIEW
G6_PRODUCTION_READY: NO — POSTGRESQL/CI/PR REVIEW REQUIRED
RANKING_CHANGED: NO
TRUST_CHANGED: NO
TRADING_ENABLED: NO
G7_STARTED: NO
```

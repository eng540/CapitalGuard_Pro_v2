# CapitalGuard Engineering Implementation Backlog

**Baseline:** `main` بعد الدمج، `c5b1be0d247b6f7e02b427e86950a16148721015`  
**Current implementation branch:** `implementation/gate-0-r1-foundation-20260819`  
**Current delivery:** `317f74c6ede9543ea90097081fcf1b715c0b60fb`  
**Status vocabulary:** `VERIFIED`، `PARTIAL`، `EXISTS — REQUIRES FIX`، `NOT FOUND`.

## 1. طريقة استخدام الـ Backlog

كل مهمة هنا تمر بالتسلسل التالي: **Phase → Epic → Feature → Story → Task → Implementation → Test → Acceptance → PR**. لا تُنقل المهمة إلى `Done` بمجرد أن يعمل الكود محليًا؛ يجب أن يوجد اختبار ودليل قبول ومراجعة أمنية أو migration عند الحاجة.

الأولوية `P0` تمنع الإطلاق أو قد تسبب فقدان بيانات أو تسربًا أو سلوكًا ماليًا خاطئًا. الأولوية `P1` مطلوبة لإغلاق المرحلة الحالية أو إطلاق R1. الأولوية `P2` تحسين أو توسعة لا تمنع Gate الحالية.

## 2. خط الأساس الحالي

| المجال | الحالة | الدليل الحالي |
|---|---|---|
| Telegram وSmart Forwarding | `VERIFIED جزئيًا` | `src/capitalguard/interfaces/telegram/forward_parsing_handler.py` |
| Parser | `VERIFIED` للسيناريوهات المختبرة | `src/capitalguard/application/services/parsing_service.py` و`tests/test_parsing.py` |
| Watchlist/Activated | `VERIFIED جزئيًا` | `UserTradeStatus` في `domain/entities.py` وORM |
| Dedup الأولي | `VERIFIED` | `DedupLedgerService`، migration، واختبارات التكامل |
| تنبيهات السعر | `PARTIAL` | `AlertService` و`PriceStreamer`؛ يحتاج اختبار تشغيل خارجي |
| التقارير | `PARTIAL` | `PerformanceService`؛ يحتاج عينة مرجعية وActivated-only reconciliation |
| API الحالية | `VERIFIED للمسارات المنشورة` | `/health` و`/api/webapp/*` و`/metrics` |
| `/log` | `NOT FOUND` | لا يوجد عقد إدخال يدوي موحد منشور |
| سوق المحللين | `NOT FOUND/PARTIAL` | لا يوجد `/find_analysts` مكتمل |
| الدفع | `NOT FOUND` | الاشتراكات ORM فقط دون payment ledger/provider |
| Copy Trading | `NOT READY` | `AutoTradeService` لا يساوي Copy Trading آمنًا |
| الاختبارات | `VERIFIED محليًا` | `61 passed, 1 skipped` في آخر Gate 0 |
| التشغيل الخارجي | `NOT VERIFIED` | PostgreSQL/Redis/Telegram/Restore لم تُثبت في Staging |

## 3. Phase 0 — Foundation & Stabilization

### Epic F0-E1 — نطاق R1 وحالة الصفقة

**F0-E1-S1 — State Machine.**  المهمة التقنية: اعتماد الحالات `WATCHLIST → PENDING_ACTIVATION → ACTIVATED → CLOSED` وتوثيق الانتقالات المسموحة ومصدر كل انتقال. الملفات: `src/capitalguard/domain/entities.py`، `src/capitalguard/infrastructure/db/models/recommendation.py`، `src/capitalguard/application/services/creation_service.py`، `lifecycle_service.py`. الاختبارات: Unit وIntegration وRegression. معيار القبول: كل انتقال غير مسموح يرفض برسالة مستقرة، و`Watchlist` لا يدخل PnL.

**F0-E1-S2 — Transition Audit.**  المهمة: التأكد من تسجيل user/time/reason/source لكل انتقال في `UserTradeEvent` أو كيان مكافئ. الاختبار: Database integration. معيار القبول: يمكن إعادة بناء تاريخ الصفقة من الأحداث دون التخمين.

### Epic F0-E2 — Test Baseline

**F0-E2-S1 — Test Collection.**  الحالة الحالية: `VERIFIED` بعد إضافة `pythonpath = ["src"]`. المهمة: إزالة أي فشل جمع، وتسجيل كل حالة skipped وسببها. الاختبار: `pytest -q`. معيار القبول: لا أخطاء جمع، وكل skipped مبرر.

**F0-E2-S2 — Failure Classification.**  المهمة: تصنيف الفشل إلى P0/P1/P2 في سجل مستقل. الدليل: `pytest` report وissue references. معيار القبول: لا يوجد فشل غير مصنف عند Gate review.

**F0-E2-S3 — Lint Baseline.**  الحالة: `EXISTS — REQUIRES FIX` بسبب مخالفات قديمة كثيرة. المهمة: إنشاء baseline ثم تنظيف الملفات المعدلة حديثًا دون ادعاء أن كل الشجرة خضراء. الاختبار: `flake8 src` وlint changed files. معيار القبول المرحلي: لا مخالفات جديدة في PR، وخطة إغلاق المخالفات القديمة.

### Epic F0-E3 — E2E Forward-to-Close

**F0-E3-S1 — Forward Input.**  الملفات: `forward_parsing_handler.py`، `parsing_service.py`. الاختبارات: Unit Parser وTelegram handler integration. القبول: نص صالح ينتج payload موحدًا مع asset/side/entry/SL/targets.

**F0-E3-S2 — Review and Confirm.**  الملفات: `forward_parsing_handler.py` وTelegram callbacks. الاختبار: E2E mocked Telegram. القبول: لا يتم الحفظ قبل تأكيد المستخدم، والتعديل ينعكس على payload النهائي.

**F0-E3-S3 — Alert and Close.**  الملفات: `alert_service.py`، `lifecycle_service.py`، `price_streamer.py`. الاختبار: Integration مع fake price feed. القبول: تنبيه واحد، إغلاق idempotent، PnL محفوظ، وعدم إعادة تنبيه بعد الإغلاق.

### Epic F0-E4 — Database and Recovery

**F0-E4-S1 — Fresh Migration.**  الحالة: `PARTIAL`. المهمة: تشغيل `alembic upgrade head` على PostgreSQL فارغة. القبول: جميع migrations تمر من baseline إلى `20251201_add_dedup_ledger`.

**F0-E4-S2 — Existing Data Migration.**  المهمة: نسخ anonymized من Staging وترقية نسخة بيانات. الاختبار: migration + reconciliation. القبول: counts وforeign keys وstatus values متطابقة قبل/بعد.

**F0-E4-S3 — Backup/Restore.**  الملفات: `backup_service.py` وRUNBOOK. الاختبار: Restore إلى بيئة منفصلة. القبول: RPO وRTO مقاسان، والبيانات الأساسية قابلة للقراءة.

### Epic F0-E5 — Security and Operations

**F0-E5-S1 — Secrets Review.**  الملفات: `.env.example`، `config.py`، Docker/CI. القبول: لا default secrets، ولا secret في Git أو logs.

**F0-E5-S2 — RBAC and Ownership.**  الملفات: `deps.py`، `auth.py`، `webapp.py`. الاختبار: unauthorized/cross-owner. القبول: مرفوض بأمان دون كشف وجود السجل.

**F0-E5-S3 — Webhook Security.**  الملفات: `tradingview.py` و`main.py`. الاختبار: missing/wrong/replay/rate-limit. القبول: secret إلزامي وتوقيع/نافذة replay واضحة.

**F0-E5-S4 — PII Review.**  المهمة: حصر telegram IDs/usernames وlogs وbackup retention. القبول: سياسة احتفاظ ووصول موثقة.

**F0-E5-S5 — Health and Smoke.**  الاختبار: deployment smoke. القبول: `/health` يعيد 503 قبل readiness و200 بعدها، وSmoke يغطي root/auth/webhook/portfolio.

## 4. Phase 1 — Trader R1

### Epic R1-E1 — Unified Input

**R1-E1-S1 `/log`.** الحالة: `NOT FOUND`. المهمة: إنشاء command/handler يستقبل direct text ويستدعي نفس Parser contract المستخدم في Forwarding. الاختبارات: Unit/API/Telegram integration. القبول: لا يوجد مسار parsing ثانٍ بحسابات مختلفة.

**R1-E1-S2 — Validation Contract.** المهمة: منع side/SL/target غير المنطقي، وتوحيد رسائل الخطأ. القبول: LONG وSHORT valid/invalid cases مغطاة.

### Epic R1-E2 — Review and Portfolio

**R1-E2-S1 — Review Card.** المهمة: بطاقة واحدة تعرض asset/side/entry/SL/TP/status ومصدر الإشارة. القبول: تعديل الحقول قبل Confirm.

**R1-E2-S2 — Watchlist/Activated Lists.** المهمة: قوائم الكل/القناة/Watchlist/Activated/History. القبول: الأعداد والتقارير تستخدم الحالة الصحيحة.

**R1-E2-S3 — Activation Ownership.** المهمة: التحقق من ملكية UserTrade عند Activate/Close/Update. القبول: لا يستطيع مستخدم تعديل سجل مستخدم آخر.

### Epic R1-E3 — Alerts and Reporting

**R1-E3-S1 — Trigger Registration.** المهمة: ربط الصفقة المفعلة فقط بالمراقبة عند الحاجة. القبول: Watchlist لا تُعامل كصفقة مالية.

**R1-E3-S2 — Activated-only Report.** المهمة: تقرير 7/30/90 يومًا يحسب PnL/Win Rate/PF/Holding من Activated/Closed فقط. الاختبار: reference dataset. القبول: تطابق ≥99%.

**R1-E3-S3 — Funnel Events.** المهمة: تسجيل forward/parse/edit/confirm/activate/close/return. القبول: funnel قابل للاستعلام ولا يسجل الحدث مرتين.

## 5. Rules of Execution

لا يبدأ Epic جديد قبل تحقق dependencies وGate المرحلة السابقة. كل Story يجب أن تكون قابلة للإنجاز في PR واحد أو مجموعة صغيرة مترابطة. كل PR يرفق test result وmigration evidence وsecurity impact وrollback note عند الحاجة.

## 6. الحالة الحالية والقرار

تم إنجاز جزء من F0 وDedup الأولي، لكن F0 التشغيلي لم يُغلق بعد بسبب PostgreSQL Staging وBackup/Restore وRedis/Telegram startup وRTO/RPO. لذلك R1 Backlog جاهز للتنفيذ، لكن لا يبدأ `/log` قبل قرار Gate 0 رسمي.

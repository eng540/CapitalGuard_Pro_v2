# System Change Record

## Change Set

**Date:** 2026-08-24
**Repository:** `eng540/CapitalGuard_Pro_v2`
**Branch:** `fix/analyst-comparison-runtime`
**Base:** `main` at merge commit `123e621c` (PR #348)
**Scope:** تشغيل آمن، مقارنة المحللين، اختبارات regression، وتوثيق حدود Core/Web.

## Records

| File | Change Type | Reason | Impact |
|---|---|---|---|
| `src/capitalguard/interfaces/api/main.py` | Operational bug fix | حلقة `MarketDataService._auto_refresh_loop()` كانت تُنشأ دون تسجيلها في `app.state.background_tasks`، بينما shutdown يلغي وينتظر المهام المسجلة فقط. | أصبحت مهمة التحديث المسماة `market-data-auto-refresh` مُدارة ضمن دورة حياة التطبيق وتُلغى وتُنتظر عند shutdown، مع توحيد التسجيل أيضًا لمهمة النسخ الاحتياطي. |
| `tests/test_runtime_background_tasks.py` | New regression test | لاختبار عقد تسجيل المهام وإزالتها بعد الإلغاء. | يمنع عودة فجوة دورة الحياة التي تترك مهامًا خلفية طويلة العمر غير مُدارة. |
| `frontend/server/capitalguard.ts` | Feature implementation and merge resolution | مسار `compareAnalysts` كان يعيد stub ثابتًا (`CORE_DATA_PENDING`) بدل مقارنة بيانات Core، وتعارض الملف مع إضافة PR #348. | أصبح المسار يجلب قائمة المحللين من Core، يتحقق من الرموز المختارة، يرفض المحلل غير الموجود والتكرار، ويرتب PnL ثم Win Rate ويعيد confidence ووقت القراءة، مع الحفاظ على Historical Intake APIs من PR #348. |
| `frontend/server/capitalguard.test.ts` | Regression tests | تغطية اختيار المحللين من نموذج Core ومعالجة unknown analyst. | يثبت أن المقارنة تستخدم المحللين المطلوبين فقط وتفشل بوضوح عند مرجع غير موجود. |
| `frontend/client/src/pages/Analysts.tsx` | UI integration | الشاشة كانت تحسب العرض محليًا من نتائج الاكتشاف ولا تستدعي مسار المقارنة الفعلي. | الشاشة تستدعي `compareAnalysts` بعد اختيار محللين، وتعرض loading/error والصدارة والثقة ووقت قراءة Core. |
| `frontend/client/src/pages/Analysts.test.tsx` | UI regression tests | mock الصفحة لم يكن يعرف مسار المقارنة ولم يكن يغطي عرض النتيجة الحية. | يغطي حالات empty/loading/error وظهور المقارنة بعد اختيار محللين. |
| `frontend/server/core-adapter.test.ts` | Test correctness fix | اختبار unavailable كان يمرر دالة fetch في موضع `RequestInit`، لذلك لم يختبر حالة unavailable الفعلية. | أصبح الاختبار يستدعي التوقيع الصحيح ويثبت رسالة unavailable المتوقعة. |
| `frontend/server/core-router.test.ts` | Test isolation fix | كانت اختبارات أوامر Core تفشل عند تشغيل المجموعة كاملة بسبب غياب إعدادات Core الاختبارية وتسابق تعديل `process.env`. | أضيفت إعدادات Core وهمية ثابتة خاصة بالاختبار، وأُبقي تنظيف global fetch فقط، فأصبحت الاختبارات مستقرة بالتوازي. |
| `frontend/README.md` | Documentation correction | وصف Web العام بأنه read-only لم يعد يطابق وجود Command APIs منفصلة للتأكيد والأوامر والتدقيق. | يوضح الفصل بين Read Model غير الكاتبة وCommand APIs ذات التأثيرات الجانبية وضوابطها. |
| `SYSTEM_CHANGE_RECORD.md` | Change history | حفظ سجل منظم لكل تعديل حسب التوجيه المرفق. | يسهّل تدقيق سبب كل تغيير وأثره بعد الدمج. |

## Validation Record

| Validation | Result |
|---|---|
| Python full suite | `337 passed, 1 skipped, 17 warnings` |
| Frontend full Vitest suite | `25 files passed, 84 tests passed` |
| TypeScript check | Passed with exit status `0` |
| Frontend production build | Passed with exit status `0` |
| `git diff --check` before commit | Passed after this record was normalized |
| PR #348 reachability | Merge commit `123e621c` is the base of this branch |

## Known Non-blocking Warnings

The validation output still reports existing deprecation/build warnings: FastAPI `on_event` lifecycle decorators, Python `datetime.utcnow()` usage in existing domain code, missing optional Vite analytics environment variables, and a large frontend bundle warning. These warnings did not fail the test suite or production build and were outside the approved comparison/lifecycle scope.

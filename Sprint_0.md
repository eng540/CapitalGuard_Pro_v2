# Sprint 0 — Foundation & Stabilization

## Sprint Goal

بنهاية Sprint 0 يجب أن يكون مسار الصفقة قابلًا للاختبار والتشغيل في بيئة Staging، وأن تكون الحالات والبيانات والنسخ الاحتياطي والأسرار والصحة التشغيلية مفهومة وقابلة للتحقق. لا يهدف Sprint 0 إلى إضافة `/log` أو الدفع؛ هدفه إزالة المخاطر التي تجعل أي ميزة لاحقة غير موثوقة.

**المدة المقترحة:** 10 أيام عمل.  
**المخرجات:** Gate evidence، state machine، PostgreSQL migration proof، restore drill، E2E، security review، smoke report، وقرار GO/NO-GO.

## P0 — مانع الإطلاق

| ID | المهمة | السبب | الملفات/البيانات | Dependency | Effort | Test | Acceptance / Evidence |
|---|---|---|---|---|---:|---|---|
| S0-P0-01 | تشغيل migrations على PostgreSQL فارغة | منع فشل الإقلاع على schema جديدة | `alembic/versions/*` | لا شيء | 1d | Database | `alembic upgrade head` ناجح وschema inspection محفوظ |
| S0-P0-02 | تشغيل migration على نسخة بيانات anonymized | منع فقدان بيانات موجودة | DB + Alembic | S0-P0-01 | 1d | Migration/Regression | counts/FK/status قبل وبعد متطابقة |
| S0-P0-03 | Backup/Restore drill | إثبات قابلية الاستعادة | `backup_service.py` | PostgreSQL | 1d | Recovery | RTO/RPO مقاسان وrestore report |
| S0-P0-04 | E2E Forward→Parse→Review→Confirm→Alert→Close | منع إطلاق دورة مالية مكسورة | Telegram handlers/services/DB | state + DB | 2d | E2E | سيناريو صالح وسيناريو خطأ، alert واحد، close idempotent، PnL صحيح |
| S0-P0-05 | Secrets/RBAC/Webhook/PII review | منع تسريب أو تجاوز صلاحية | `config.py`, `auth.py`, `deps.py`, `tradingview.py` | baseline | 1d | Security | checklist موقعة، no default secrets، unauthorized tests |
| S0-P0-06 | Redis/Telegram startup smoke | readiness وحدها لا تثبت startup الكامل | `main.py`, `boot.py`, compose | external sandbox | 1d | Smoke | `/health` 503 قبل الجاهزية و200 بعدها، logs بلا crash |

## P1 — مطلوب لإغلاق الأساس

| ID | المهمة | السبب | Effort | Acceptance |
|---|---|---|---:|---|
| S0-P1-01 | توثيق State Machine والانتقالات | منع خلط Watchlist/Activated/PnL | 0.5d | وثيقة + tests لكل transition |
| S0-P1-02 | تثبيت Dedup window/config | منع duplicate signals | 0.5d | config موثق، unit/integration ناجح |
| S0-P1-03 | تصنيف skipped/warnings/lint | معرفة الدين التقني الحقيقي | 0.5d | سجل تصنيف وروابط issues |
| S0-P1-04 | تحديث CI ليشغل الاختبارات والأمن | منع regression بعد الدمج | 0.5d | CI green أو failure واضح مانع |
| S0-P1-05 | Metrics للـ funnel والأخطاء | قياس قبل Alpha | 1d | counters/timers موثقة ويمكن قراءتها |
| S0-P1-06 | Runbook وRollback note | تقليل MTTR | 0.5d | إجراء rollback وتجربة على Staging |

## P2 — تحسينات غير مانعة

| ID | المهمة | Effort | Acceptance |
|---|---|---:|---|
| S0-P2-01 | تنظيف deprecation warnings | 1d | warnings الحرجة مصنفة أو مغلقة |
| S0-P2-02 | تنظيف Flake8 القديم | 2–3d | خفض المخالفات دون تغيير سلوك |
| S0-P2-03 | إضافة dashboards محلية | 1d | لوحة صحة لا تعرض PII |

## Definition of Sprint Done

لا يغلق Sprint 0 إلا إذا أُرفق PR بالمخرجات التالية: نتائج الاختبارات، تقرير migration، تقرير restore، E2E evidence، security checklist، smoke logs، وقرار GO/NO-GO. نتيجة `61 passed` المحلية وحدها لا تكفي.

## قرار نهاية Sprint

إذا فشل أي بند P0، تكون النتيجة `NO-GO` ويُفتح corrective sprint. إذا نجحت P0 وبقي P1 موثقًا بخطة زمنية، يمكن اعتماد `CONDITIONAL GO` إلى R1 development دون Alpha. لا يوجد `GO Alpha` قبل نجاح Staging وRecovery وE2E الخارجي.

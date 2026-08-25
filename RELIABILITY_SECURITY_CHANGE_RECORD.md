# Reliability & Security Hardening — Change Record

## Branch and scope

تم تنفيذ التعديلات على الفرع `hardening/reliability-security-p0` المبني من `main` عند `21715eb2`. النطاق محصور في تقوية lifecycle وreadiness وidempotency وjob lifecycle وcorrelation tracking وCI migration checks. لم تتم إضافة service موازية ولم تتغير عقود Core المالية.

## Changes

| Area | Change | Safety boundary |
|---|---|---|
| Liveness | إضافة `/live` يعكس حياة العملية دون الاعتماد على Redis أو Telegram أو Binance. | لا يكشف بيانات أو أسرارًا. |
| Readiness | إضافة `/ready` يعكس startup completion، توفر Telegram/services، ومهام background المتدهورة. | يعيد 503 عند عدم الجاهزية أو فشل مهمة. |
| Runtime state | إضافة `starting`, `stopped`, `stopping` مع reset واضح عند startup/shutdown. | `/health` الحالي محفوظ للتوافق. |
| Background tasks | callback موحد يرصد task exceptions ويسجل اسم المهمة كـ degraded بدل إسقاط الاستثناء بصمت. | الإيقاف يلغي وينتظر المهام الموجودة. |
| HTTP correlation | قبول `X-Request-ID` عند مطابقته للنمط الآمن، أو إنشاء UUID جديد، وإعادته في response header. | لا يتم قبول whitespace أو قيم ضخمة أو رموز غير آمنة. |
| Command idempotency | توحيد التحقق من idempotency keys بطول 8–160 وبنمط آمن. | إعادة استخدام المفتاح مع payload مختلف مرفوض. |
| Command audit | حفظ `correlation_id` و`request_hash` في metadata الداخلية لسجل `WebCommandAudit`. | لا تُعاد metadata الداخلية إلى العميل عند replay. |
| Job lifecycle | إضافة `JobExecution` و`JobState` لتوحيد الانتقالات وحدود retry وexponential backoff. | لا توجد persistence جديدة؛ النماذج الحالية تبقى مصدر التخزين. |
| Security regression | تغطية access-token round trip لضمان عمل JWT path فعليًا. | أسرار الاختبارات وهمية فقط. |
| Migration CI | إضافة `verify_alembic_single_head.py` وتشغيله في CI. | يفشل CI عند تعدد migration heads. |

## Verification

- كامل اختبارات Python: **355 passed, 1 skipped, 17 warnings**.
- اختبارات hardening المركزة: **62 passed**.
- `python -m compileall`: ناجح.
- Alembic single-head verifier: ناجح، head واحدة هي `20260824_add_usertrade_profit_stop_fields`.
- كامل اختبارات frontend وTypeScript وproduction build من خط الأساس السابق ما زالت ناجحة، وتُعاد في CI للـ PR.
- `git diff --check`: ناجح.

## Known non-blocking warnings

توجد تحذيرات deprecation سابقة تخص FastAPI `on_event` و`datetime.utcnow` وStarlette/httpx، ولا تمنع التشغيل. تحويل FastAPI إلى lifespan API هو تحسين لاحق مستقل حتى لا تختلط migration التشغيلية مع تغيير دورة حياة واسع.

## Explicitly not included

لم يتم فصل العمليات إلى containers مستقلة في هذه الدفعة، ولم تُضف WebSocket أو broker جديد، ولم يُكشف أي Core service key للمتصفح. هذه تغييرات بنيوية تحتاج قرار deployment واختبارات load منفصلة.

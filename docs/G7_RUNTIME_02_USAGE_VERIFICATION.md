# G7-RUNTIME-02 — Usage Verification and Legacy Disposition

## حالة التحقق

تمت مراجعة `main` عند `fcb0a66ecfbb59a78687a7249bacac3b7704affd`. لم يظهر داخل المستودع Frontend consumer أو Mini App أو static page يستدعي `POST /api/webapp/create`. كما تصف `frontend/CORE_API_DISCOVERY_20260820.md` هذا route بأنه غير مفعّل في Alpha.

لم تتوفر access logs أو reverse-proxy logs أو telemetry داخل checkout تثبت آخر استخدام، لذلك التصنيف الصحيح هو `usage unknown`، وليس `unused`.

## القرار

لا يُحذف `/api/webapp/create` الآن لأن consumer خارجي محتمل لا يمكن نفيه من source tree. ولا يُترك كتنفيذ مستقل؛ يتحول إلى Compatibility Adapter رقيق يستدعي نفس `WebCommandService.confirm_analyst_recommendation()` المستخدم من المسار canonical.

يكون المسار الظاهر للمستخدم هو:

```text
Web UI → /recommendations/preview → /recommendations/confirm
```

أما `/create` فيبقى مؤقتًا للتوافق فقط، مع `Deprecation: true` و`Link` يشير إلى `/recommendations/confirm`.

## المسار canonical الموحد

```text
actor resolution
→ canonical payload conversion
→ OperationalDecisionService
→ OperationalAdmissionService
→ WebCommandAudit + idempotency
→ CreationService
→ existing publication outbox
```

لا يملك الـadapter القديم parsing ماليًا أو authorization أو persistence أو publication مختلفًا. وهو لا يستدعي `CreationService` مباشرةً.

## idempotency

إذا أرسل العميل القديم `idempotency_key`، يُستخدم المفتاح نفسه. وإذا لم يرسله، يُشتق مفتاح ثابت من actor والـcanonical payload مع ترتيب مستقر للقنوات. هذا يحافظ على التوافق ويمنع التكرار المطابق من إنشاء Recommendation أو Outbox أو Audit إضافي.

## المراقبة والإيقاف

لم تُضف telemetry جديدة في هذا PR. لذلك تبقى مرحلة observation التشغيلية مطلوبة بعد النشر عبر access logs أو counter خالٍ من payload الحساس. بعد فترة مراقبة موثقة، يقرر النظام إما إبقاء adapter لفترة توافق، أو إعلان deprecation نهائي ثم إيقافه في PR منفصل.

إذا ظهر consumer خارجي لا يمكن توافقه بأمان، أو تطلب التوحيد تعديل Telegram أو Historical Forwarding أو G5/G6 أو Ranking/Trust أو Trading، يجب إصدار `G7 BLOCKED — WEB UNIFICATION GAP` بدل توسيع هذا PR.

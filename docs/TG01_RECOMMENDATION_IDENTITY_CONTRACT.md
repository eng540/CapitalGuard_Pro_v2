# TG-01 — عقد هوية توصيات Core وملكية قراءة المتداول

**الحالة:** `BUILD_DONE` — بانتظار قبول UAT لقراءة WebApp.  
**النطاق:** عقد قراءة Core فقط. لا ينفذ هذا التغيير أوامر تداول أو إنشاء توصيات أو دفع أو Copy Trading.

## الغرض

يحافظ CapitalGuard على Core كمصدر الحقيقة الوحيد لـ`Recommendation` و`UserTrade`. تضيف TG-01 عقداً واضحاً يمنع واجهة Web من استخدام المعرف الرقمي الداخلي كهوية عرض أو كعامل صلاحية، ويميز النسخة المتتبعة للمتداول عن التوصية المصدر.

> `Tracked Signal` يصف أصل الـUserTrade عندما يشير إلى توصية مصدر؛ وهو ليس بديلاً عن حالة `WATCHLIST` أو `ACTIVE` أو `CLOSED`.

## عقد القائمة

المسار الحالي: `GET /api/webapp/read-models/trader/{telegram_id}/recommendations`.

هذا مسار server-to-server حصراً. يحمل Web خدمة Core key، ويشتق `telegram_id` من جلسة Telegram الموقعة؛ لا يرسل المتصفح المفتاح ولا يعتمد Core على قيمة متصفح غير موثقة كهوية.

| الحقل | الدلالة |
|---|---|
| `schema_version` | إصدار العقد؛ TG-01 يعلن `2026-08-21.2`. |
| `as_of` | وقت تصوير Read Model في Core. |
| `entity_type` | ثابت `USER_TRADE` في هذا feed. |
| `public_ref` / `display_ref` | المعرّف العام المستقر الذي يستخدمه العرض والمسار الجديد. |
| `source` | مرجع المصدر عند وجوده: `RECOMMENDATION` و`public_ref` و`analyst_id`. |
| `id` | حقل توافق مؤقت للواجهة القديمة فقط؛ لا يستخدم في URL أو الأمر أو فحص الملكية الجديد. |

## عقد التفاصيل والملكية

المسار: `GET /api/webapp/read-models/trader/{telegram_id}/recommendations/{public_ref:path}`.

استخدم converter من نوع `path` لأن مراجع CapitalGuard الهرمية قد تتضمن `/`، مثل `USR-000012/T-0003`. يبحث Core دائماً بالشرطين `UserTrade.user_id` و`UserTrade.public_ref`. عند غياب صف مملوك يعيد `404` سواء كان المرجع غير موجود أو يخص مستخدماً آخر، لمنع تسريب وجود كيان خارجي.

## حدود صريحة

لا تقبل TG-01 `rec_id` أو `trade_id` رقميين لمسار التفاصيل الجديد، ولا تعدل `/action` القديم. معالجة الأوامر تُنفذ في TG-04 بعد dispatcher موحد وconfirmation وidempotency. ويبقى Web PostgreSQL خالياً من النسخ المالية.

## معايير القبول

ينجح TG-01 عندما يجتاز: اختبار Core service key، اختبار serializer للهوية/المصدر، اختبار SQL يثبت حصر lookup بمالك الصف والـpublic ref، والانحدار الكامل لـCore. ويبقى UAT لجلسة Telegram الحية دليلاً مطلوباً قبل وصف العقد بأنه مقبول تشغيلياً.

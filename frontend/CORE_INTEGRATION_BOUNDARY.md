# CapitalGuard Web ↔ Core Integration Boundary

## الهدف

تعمل منصة الويب كطبقة **عرض، صلاحيات، وتحليل مساعد** فوق CapitalGuard Core. لا تعيد الواجهة تنفيذ محرك التداول أو تدقيق التاريخ أو دورة Outbox داخل React أو tRPC.

## مسار الربط المعتمد

تُنشأ طبقة Adapter خادمية مستقبلًا بين tRPC وواجهة Core مُصدّقة. تستدعي الواجهة إجراءات tRPC فقط، بينما يستدعي Adapter API خادم Core عبر حساب خدمة محدود الصلاحية. لا يتصل المتصفح بخادم Core أو قاعدة PostgreSQL الخاصة به مباشرة.

| المجال | مالك الحقيقة | دور منصة الويب |
|---|---|---|
| Recommendation وUserTrade الحية | CapitalGuard Core | عرض، مراجعة، وتتبع فقط |
| Temporal Decision وForward Receipt | CapitalGuard Core | عرض القرار وسبب التصنيف وسجل التدقيق |
| Historical Evidence وMarket Replay | CapitalGuard Core | Owner Review وواجهة Replay Gate |
| المحفظة وPnL | CapitalGuard Core | قراءة مصفاة حسب مالك الحساب ودور المستخدم |
| حساب المخاطر وWhat-If | منصة الويب | أداة قرار محلية لا تنفذ أوامر |
| Smart Dropzone | منصة الويب | استخراج JSON مفسر فقط؛ لا ينشئ كيانًا حيًا |

## ثوابت الأمان

لا يسمح لأي إجراء ويب بإنشاء Recommendation أو UserTrade أو Publication Outbox تلقائيًا. لا ينقل الـ Adapter أسرار Telegram أو مفاتيح البورصات إلى المتصفح. تخضع إجراءات Owner Review وEvidence Ingestion للصلاحية الإدارية وتُسجل في Core عند تفعيل الربط، وليس في حالة واجهة محلية صامتة.

## خطة التفعيل المرحلي

تبدأ المرحلة التالية بواجهة قراءة مصدّقة من Core تشمل توصيات ومراكز ودفعات تاريخية. ثم تضاف mutations الخاصة بالمراجعة عبر عمليات idempotent تحمل `actor_id` و`request_id`. ويبقى الدفع وCopy Trading معطلين حتى عبور Gate الاستقرار والاحتفاظ التشغيلي.

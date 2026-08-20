# CapitalGuard Core API Discovery

تم التحقق من واجهة OpenAPI العامة لخادم Core. الإصدار المعلن هو `27.2-webapp`، وتتوفر مسارات WebApp التالية:

| المسار | الغرض المتوقع | نوع الربط في Alpha |
|---|---|---|
| `GET /api/webapp/price` | سعر أصل لحظي | قراءة فقط |
| `GET /api/webapp/channels` | قنوات المحللين | قراءة فقط |
| `GET /api/webapp/portfolio` | محفظة المستخدم | قراءة فقط بعد ربط هوية المستخدم |
| `GET /api/webapp/performance` | أداء المستخدم | قراءة فقط بعد ربط هوية المستخدم |
| `GET /api/webapp/funnel` | بيانات funnel | قراءة فقط بعد ربط هوية المستخدم |
| `GET /api/webapp/signal/{rec_id}` | تفاصيل توصية | قراءة فقط |
| `POST /api/webapp/create` | إنشاء تداول | غير مفعّل في Alpha |
| `POST /api/webapp/action` | إجراء على تداول | غير مفعّل في Alpha |

سيقتصر Adapter الحالي على health وعمليات القراءة الصريحة. لا تُفعّل عمليات create/action أو أي عملية تداول من منصة الويب خلال Alpha.

المحفظة والأداء وfunnel تتطلب `initData` صالحة من Telegram Mini App وفق عقد Core، لذا لا تُعرض في الموقع المستقل إلا بعد ربط هوية Telegram. أما health والسعر وتفاصيل الإشارة فهي مسارات قراءة منفصلة يمكن للـ Adapter استدعاؤها خادميًا.

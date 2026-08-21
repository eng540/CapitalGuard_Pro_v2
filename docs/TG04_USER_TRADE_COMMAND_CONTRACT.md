# TG-04 — عقد أوامر UserTrade

**الحالة:** `BUILD_DONE` — لا توجد أزرار Web أو UAT حي بعد.  
**الهدف:** استبدال مسار الإجراءات الرقمي الملتبس بعقد دائم يفصل `UserTrade` عن `Recommendation`.

## قواعد العقد

المسار القديم `POST /api/webapp/action` متقاعد ويرد `410 Gone`. لا يقبل عقد TG-04 معرفاً رقمياً من المتصفح. مسار الإغلاق الجديد يحدد المتداول والـ`public_ref` في URI، ويطلب Core service key، ويتحقق أن `actor_telegram_id` يساوي نطاق المتداول قبل فتح جلسة أو استدعاء Lifecycle.

تنفذ الخدمة lookup مقيداً بـ`user_id + public_ref` مع row lock، وتستمد سعر الخروج من `price_service` في Core ولا تقبل سعراً من client. ترفض الحالة `CLOSED` والسعر غير الموثوق. يخزن `WebCommandAudit` بصمة تضم نوع الأمر والفاعل والكيان والهدف والحمولة؛ تكرار المفتاح بالطلب نفسه يعيد النتيجة، وطلب مختلف بالمفتاح نفسه يرفض.

## النطاق الحالي

الأمر الوحيد هو `CLOSE` لـ`USER_TRADE`. لا يدعم هذا الطلب partial close أو SL أو entry أو breakeven أو Recommendation actions. تلك أوامر مستقلة تحتاج policy/state matrix وUAT منفصلين؛ لا يعاد فتح endpoint القديم لتجنب دين تقني أو خلط الكيان.

## قبول قبل Web actions

اجتازت Core `211 passed, 1 skipped`. يلزم UAT لحساب متداول حقيقي على صفقة اختبار غير تجارية: close مرة واحدة، retry بالمفتاح نفسه، محاولة reference غير مملوك، وسعر Core غير متاح. عندها فقط يمكن ربط زر Confirm صريح في Web.

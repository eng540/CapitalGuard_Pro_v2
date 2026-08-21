# TG-02 — تحصين Telegram Webhook

**الحالة:** `BUILD_DONE` — يتطلب ضبط متغير Railway قبل دمجه في الإنتاج.  
**غير مشمول:** الدفع، Copy Trading، التنفيذ الحي، أو أي تغيير في منطق الصفقات.

## الضوابط المطبقة

عند تعيين `TELEGRAM_WEBHOOK_URL`، يرفض Core البدء إن لم يكن `TELEGRAM_WEBHOOK_SECRET` مضبوطاً. يُمرر هذا السر إلى `set_webhook`، ثم يتطلب المسار `/webhook/telegram` الرأس `X-Telegram-Bot-Api-Secret-Token` بالقيمة نفسها قبل فك Update أو تمريره إلى PTB.

تقتصر التحديثات المطلوبة على `message` و`callback_query` و`channel_post` و`edited_channel_post`. هذا يعكس handlers المسجلة حالياً ويقلل السطح المستلم من Telegram. لا تضف نوع تحديث جديداً إلا مع handler واختبار وتحديث هذا allow-list.

## إعداد Railway المطلوب قبل الدمج

أنشئ قيمة عشوائية جديدة من أحرف Telegram المسموح بها (`A-Z`, `a-z`, `0-9`, `_`, `-`) بطول 32–128 حرفاً، ثم أضفها في خدمة **Core فقط** باسم `TELEGRAM_WEBHOOK_SECRET`. لا تضعها في خدمة Web أو المستودع أو المحادثة. يجب أن تكون `TELEGRAM_WEBHOOK_URL` هي URL HTTPS لنقطة `/webhook/telegram` الفعلية.

بعد النشر، نفذ `getWebhookInfo` من بيئة تشغيل آمنة أو راقب السجل؛ يجب عدم وجود `last_error_message`، ويجب أن ترفض تجربة دون الرأس بـ403. لا تختبر بإرسال توكن أو سر عبر رسالة.

## معايير القبول

تغطي الاختبارات: رفض الرأس الغائب، رفض الرأس الخاطئ، وقبول الرأس المطابق قبل معالجة PTB. يظل منع التكرار الكامل لـ`update_id` عملاً لاحقاً في TG-02b لأن تخزين dedup durable يحتاج قرار نموذج البيانات وإجراء migration منفصل.

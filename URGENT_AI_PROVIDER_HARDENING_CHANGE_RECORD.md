# سجل الإصلاحات العاجلة لخدمة AI Parsing

## المشكلة

أظهر سجل الإنتاج أن `ai-service` تصل إلى OpenRouter لكن نموذج `stealth/ox-alpha` يعيد `429 Too Many Requests`. كما ظهر رابط Telegram File API كاملًا في السجلات، ويتضمن Bot Token ضمن المسار.

## الإصلاحات

1. إضافة redaction مركزي يزيل Telegram Bot Token وBearer values من النصوص والروابط المسجلة.
2. إيقاف تسجيل رابط الصورة في `ai_service/main.py`، وتسجيل مصدر عام فقط بدل الرابط الكامل.
3. إضافة retry محدود بحد أقصى ثلاث محاولات مع exponential backoff capped، ومعالجة timeouts وأخطاء الشبكة وحالات 408/409/425/429/5xx.
4. تصنيف `429` كـ `provider_rate_limited` وتمريره عبر `ParsingManager`، مع إعادة HTTP 503 و`Retry-After: 5` من `/ai/parse` و`/ai/parse_image`.
5. إضافة `LLM_FALLBACK_MODELS` اختياري، يرسل `models` إلى OpenRouter بترتيب الأولوية. لا يُفعل تلقائيًا دون إعداد صريح.
6. إضافة اختبارات محلية للـ redaction والـ bounded retry وتصنيف 429 وقائمة النماذج الاحتياطية.

## الحدود الأمنية

لم يتم تدوير Telegram Bot Token أو OpenRouter key لأن قيمة جديدة لم تُقدّم. يجب إلغاء Bot Token المكشوف وتحديثه من BotFather، ثم وضع المفتاح الجديد في Railway Secrets فقط.

## التحقق

- اختبارات AI Parsing المستهدفة: ناجحة.
- اختبارات المسار التاريخي والصورة: ناجحة.
- كامل اختبارات Python: ناجح مع اختبار متجاوز مسبقًا.
- Python compilation و`git diff --check`: ناجحان.
- لم تُستخدم أسرار حقيقية في الاختبارات.

## النشر

هذا السجل والتعديلات لا تُطبق على الإنتاج إلا بعد دمج PR ونشره. بعد النشر يمكن ضبط `LLM_FALLBACK_MODELS` على نموذج يدعم text+image مثل `google/gemini-3.1-flash-lite` إذا كان ذلك مقبولًا من ناحية التكلفة والخصوصية.

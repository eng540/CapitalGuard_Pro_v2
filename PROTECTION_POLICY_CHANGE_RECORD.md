# ProtectionPolicy — Change Record

## النطاق

تم تنفيذ التغيير على الفرع `feat/unify-protection-policy` المبني على `main` عند `1d6eada7`. الهدف هو توحيد التحقق من Break-Even وTrailing داخل domain فوق `StrategyEngine` الحالي، دون إنشاء محرك أو استراتيجية ثانية.

## التنفيذ

| المكوّن | التغيير |
|---|---|
| `domain/protection_policy.py` | إضافة Value Object موحد يقرأ السياسة ويطبع القيم إلى Decimal ويتحقق من mode وside وentry وstop وtrailing وbreak-even. |
| `StrategyEngine` | يطبق ProtectionPolicy قبل التقييم؛ السياسة غير الصالحة تُرفض بأمان مع log warning ولا تسقط عامل التقييم. Action contract لم يتغير. |
| `LifecycleService` | يمرر سياسة الحماية المرشحة إلى نفس validator قبل حفظ إعدادات exit strategy أو إعادة بناء التنبيهات. |
| الاختبارات | تغطية LONG/SHORT، Trailing، Break-Even، القيم الصفرية، الاتجاه الخاطئ، والسياسة غير الصالحة داخل المحرك. |

## قواعد التحقق

السياسة النشطة تحتاج entry وstop صالحين واتجاهًا صحيحًا: وقف LONG أسفل الدخول ووقف SHORT أعلى الدخول. Trailing يحتاج قيمة موجبة. Break-Even يحتاج threshold موجبًا وbuffer غير سالب. Fixed يحتاج profit stop موجبًا. السياسة غير النشطة أو `NONE` لا تمنع السجلات القديمة من القراءة.

## التحقق

- كامل اختبارات Python: **363 ناجحًا، 1 متجاوز، 17 تحذيرًا غير مانع**.
- اختبارات Policy وStrategyEngine وLifecycle: **43 ناجحًا**.
- فحص Python compilation: ناجح.
- فحص Alembic single-head: ناجح، head واحدة هي `20260824_add_usertrade_profit_stop_fields`.
- كامل اختبارات frontend: **87 ناجحًا ضمن 25 ملفًا**.
- TypeScript `tsc --noEmit`: ناجح.
- Production build: ناجح.
- `git diff --check`: ناجح.

## التحذيرات

التحذيرات الحالية سابقة أو غير مانعة، وتشمل deprecation في FastAPI `on_event` و`datetime.utcnow` وStarlette/httpx. لا يغير هذا PR العقود الخارجية أو schema قاعدة البيانات.

## سياسة الدمج

لم يتم دمج هذا الفرع تلقائيًا. بعد مراجعة PR وفحوصات CI يمكن دمجه إلى `main` بصورة مستقلة.

# UserTrade Advanced UAT Matrix

| السيناريو | النتيجة الحاكمة | دليل الاختبار |
|---|---|---|
| إعادة إرسال المفتاح نفسه | لا إغلاق ثانٍ ولا قراءة سعر ثانية؛ يعاد نفس payload | `test_web_user_trade_command.py` |
| مفتاح مستخدم لهدف آخر | يرفض قبل أي تغيير | `test_web_user_trade_command.py` |
| public ref لمستخدم آخر | لا يكشف الكيان ولا يقرأ السعر | `test_web_user_trade_command.py` |
| سجل مغلق أو سعر مفقود | يرفض بلا تعديل للدورة | `test_web_user_trade_command.py` |
| فشل تسليم Outbox مؤقتاً | يسجل retry أو failure مع خطأ؛ لا يكرر الأثر المالي | `test_publication_outbox.py` |
| تعافي Outbox | delivery واحد فقط ينتقل إلى SENT ويحفظ رسالة واحدة | `test_publication_outbox.py` |
| عقد Payload | نجاح الإغلاق يعيد public ref وstatus وreplayed بصورة ثابتة | `test_web_user_trade_command.py` |

لا يدخل هذا الاختبار أوامر سوق أو أموالاً أو Copy Trading. أي زر Web يظل محظوراً إلى أن تمر هذه المصفوفة وUAT المستخدم المتقدم.

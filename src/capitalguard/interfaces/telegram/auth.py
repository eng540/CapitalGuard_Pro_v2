# --- START OF FILE: src/capitalguard/interfaces/telegram/auth.py ---
import logging
from telegram import Update
from telegram.ext.filters import BaseFilter

from capitalguard.infrastructure.db.repository import RecommendationRepository

log = logging.getLogger(__name__)


class _DatabaseAuthFilter(BaseFilter):
    """
    DB-only authentication filter.

    السلوك:
      - عند أول تفاعل لأي مستخدم، يتم إنشاء سجل له في جدول User (إن لم يكن موجودًا).
      - بعدها يعتبر مسموحًا له استخدام الأوامر/المع_handlers.
      - لا يوجد اعتماد على قوائم ثابتة من .env.

    ملاحظات:
      - إن أردت لاحقًا فرض سياسة سماح/منع من قاعدة البيانات نفسها (مثلاً حقل is_active أو role)،
        أضف التحقق هنا بعد استرجاع المستخدم.
    """

    def __init__(self) -> None:
        super().__init__(name="DB_Auth_Filter")
        self.repo = RecommendationRepository()

    def filter(self, update: Update) -> bool:  # type: ignore[override]
        # حماية من التحديثات التي لا تحمل مستخدمًا (مثلاً بعض أنواع الـ callbacks النادرة)
        if not update or not getattr(update, "effective_user", None):
            log.debug("DB_Auth_Filter: No effective_user on update; rejecting.")
            return False

        tg_user = update.effective_user
        tg_id = tg_user.id

        try:
            # يضمن وجود المستخدم (إنشاء عند أول مرة)
            self.repo.find_or_create_user(tg_id)
            return True
        except Exception as e:
            # في حال فشل الوصول لقاعدة البيانات، ارفض الطلب وسجل الخطأ
            log.error("DB_Auth_Filter: DB error while ensuring user %s: %s", tg_id, e, exc_info=True)
            return False


# أنشئ مثيلًا عامًا يُستخدم في تعريف الـ handlers
ALLOWED_USER_FILTER = _DatabaseAuthFilter()
# --- END OF FILE ---
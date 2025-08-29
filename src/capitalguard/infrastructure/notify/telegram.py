# --- START OF FILE: src/capitalguard/infrastructure/notify/telegram.py ---
from __future__ import annotations
from typing import Optional, Tuple

from telegram import Bot
from telegram.constants import ParseMode

from capitalguard.config import settings
from capitalguard.domain.entities import Recommendation
from capitalguard.interfaces.telegram.ui_texts import build_trade_card_text
from capitalguard.interfaces.telegram.keyboards import channel_card_keyboard


class TelegramNotifier:
    """
    الناشر/المحرّر لبطاقات التوصيات في قناة تيليجرام.
    يقرأ إعدادات البوت داخليًا من settings:
      - TELEGRAM_BOT_TOKEN
      - TELEGRAM_CHAT_ID  (✅ الاسم الصحيح المتاح في بيئتك)
    """

    def __init__(self) -> None:
        self.token: Optional[str] = settings.TELEGRAM_BOT_TOKEN
        # ✅ استخدام TELEGRAM_CHAT_ID بدلاً من TELEGRAM_CHANNEL_ID
        self.channel_id: Optional[int] = (
            int(settings.TELEGRAM_CHAT_ID) if getattr(settings, "TELEGRAM_CHAT_ID", None) else None
        )
        self.bot: Optional[Bot] = Bot(self.token) if self.token else None

    # ---------- رسائل عامة ----------
    def send_message(self, text: str, chat_id: Optional[int | str] = None) -> None:
        """إرسال رسالة نصية عادية (تشخيص/إشعار)."""
        if not self.bot:
            return
        self.bot.send_message(
            chat_id=chat_id or self.channel_id,
            text=text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )

    # ---------- بطاقات التوصيات ----------
    def post_recommendation_card(self, rec: Recommendation) -> Optional[Tuple[int, int]]:
        """
        ينشر بطاقة توصية إلى القناة ويعيد (channel_id, message_id).
        يُستخدم عند إنشاء توصية جديدة.
        """
        if not self.bot or not self.channel_id:
            return None

        text = build_trade_card_text(rec)
        msg = self.bot.send_message(
            chat_id=self.channel_id,
            text=text,
            parse_mode=ParseMode.HTML,
            reply_markup=channel_card_keyboard(rec.id, is_open=(rec.status.upper() == "OPEN")),
            disable_web_page_preview=True,
        )
        return (self.channel_id, msg.message_id)

    def edit_recommendation_card(self, rec: Recommendation) -> bool:
        """
        يحرّر البطاقة في القناة عند تعديل SL/TP أو عند الإغلاق.
        إذا تعذّر التحرير (رسالة قديمة جدًا)، كحل أخير يعيد نشر بطاقة جديدة.
        """
        if not self.bot or not rec.channel_id or not rec.message_id:
            return False

        text = build_trade_card_text(rec)
        try:
            self.bot.edit_message_text(
                chat_id=rec.channel_id,
                message_id=rec.message_id,
                text=text,
                parse_mode=ParseMode.HTML,
                reply_markup=channel_card_keyboard(rec.id, is_open=(rec.status.upper() == "OPEN")),
                disable_web_page_preview=True,
            )
            return True
        except Exception:
            # في حال كانت الرسالة غير قابلة للتحرير (مقيدة بزمن أو غيره) ننشر رسالة جديدة
            posted = self.post_recommendation_card(rec)
            return bool(posted)
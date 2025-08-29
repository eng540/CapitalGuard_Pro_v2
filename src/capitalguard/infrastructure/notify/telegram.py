# --- START OF FILE: src/capitalguard/infrastructure/notify/telegram.py ---
from __future__ import annotations
from typing import Optional, Tuple, List

from telegram import Bot
from telegram.constants import ParseMode

from capitalguard.config import settings
from capitalguard.domain.entities import Recommendation
from capitalguard.interfaces.telegram.ui_texts import build_trade_card_text
from capitalguard.interfaces.telegram.keyboards import channel_card_keyboard

class TelegramNotifier:
    """
    مسؤول النشر/التحرير في قناة تيليجرام.
    يقرأ الإعدادات داخليًا (token/chat_id)، لذا إنشاؤه لا يحتاج معاملات.
    """
    def __init__(self) -> None:
        self.token = settings.TELEGRAM_BOT_TOKEN
        self.channel_id = int(settings.TELEGRAM_CHANNEL_ID) if settings.TELEGRAM_CHANNEL_ID else None
        self.bot = Bot(self.token) if self.token else None

    def send_message(self, text: str, chat_id: Optional[int | str] = None) -> None:
        if not self.bot:
            return
        self.bot.send_message(chat_id=chat_id or self.channel_id, text=text, parse_mode=ParseMode.HTML)

    def post_recommendation_card(self, rec: Recommendation) -> Optional[Tuple[int, int]]:
        """ينشر بطاقة توصية إلى القناة ويعيد (channel_id, message_id)."""
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
        """يحرّر البطاقة في القناة عند أي تعديل (SL/TP/Close)."""
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
            # في حال كانت الرسالة القديمة قديمة جدًا/غير قابلة للتحرير، كحل أخير ننشر جديدة (لن نصل هنا غالبًا)
            self.post_recommendation_card(rec)
            return False
# --- END OF FILE ---
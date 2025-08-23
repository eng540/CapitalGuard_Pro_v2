# -*- coding: utf-8 -*-
from __future__ import annotations
import os
import requests
from capitalguard.domain.ports import NotifierPort  # تأكد أن الواجهة موجودة لديك

class TelegramNotifier(NotifierPort):
    """
    مرسِّل إشعارات إلى تيليجرام عبر HTTP.
    يوفر publish(text, chat_id=None).
    يتم استخدامه من الخدمات (TradeService) لنشر الرسائل في قناة الإعلانات.
    """
    def __init__(self):
        self.token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.default_chat = os.getenv("TELEGRAM_CHANNEL_ID")  # قناة/مجموعة الإعلانات
        self.base = f"https://api.telegram.org/bot{self.token}"

    def publish(self, text: str, chat_id: int | None = None) -> None:
        if not self.token:
            return
        chat = chat_id or (int(self.default_chat) if self.default_chat else None)
        if not chat:
            return
        try:
            requests.post(
                f"{self.base}/sendMessage",
                json={"chat_id": chat, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True},
                timeout=10,
            )
        except Exception:
            # لا نكسر الخدمة عند فشل الإرسال
            pass
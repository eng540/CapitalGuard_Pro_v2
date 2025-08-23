# -*- coding: utf-8 -*-
from __future__ import annotations
import os, requests
from capitalguard.domain.ports import NotifierPort

class TelegramNotifier(NotifierPort):
    def __init__(self):
        self.token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.default_chat = os.getenv("TELEGRAM_CHANNEL_ID")  # قناة/جروب النشر
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
                json={"chat_id": chat, "text": text, "parse_mode": "HTML"},
                timeout=10,
            )
        except Exception:
            pass
#--- START OF FILE: src/capitalguard/infrastructure/notify/telegram.py ---
import logging
from typing import Optional, Any, Dict
import httpx
from capitalguard.config import settings

class TelegramNotifier:
    def __init__(self, token: Optional[str] = None):
        self.token = (token or settings.TELEGRAM_BOT_TOKEN or "").strip()
        if not self.token:
            logging.warning("TelegramNotifier: TELEGRAM_BOT_TOKEN is missing.")

    def _send(self, method: str, payload: Dict[str, Any]) -> bool:
        if not self.token: return False
        url = f"https://api.telegram.org/bot{self.token}/{method}"
        try:
            with httpx.Client(timeout=10.0) as client:
                resp = client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
            if not data.get("ok"):
                logging.error("Telegram API Error: %s", data.get("description"))
                return False
            return True
        except httpx.HTTPStatusError as e:
            logging.error("Telegram HTTP Error: %s - %s", e.response.status_code, e.response.text)
        except Exception as e:
            logging.exception("Telegram sending exception: %s", e)
        return False

    def send_message(self, text: str, chat_id: Optional[str | int] = None, parse_mode: str = "HTML") -> None:
        """
        Sends a message. If chat_id is not provided, it defaults to settings.TELEGRAM_CHAT_ID.
        """
        # ✅ استخدام chat_id المستلم أو العودة إلى الإعدادات الافتراضية
        target_chat_id = chat_id or settings.TELEGRAM_CHAT_ID
        if not target_chat_id:
            logging.warning("TelegramNotifier: No chat_id provided and TELEGRAM_CHAT_ID is not set.")
            return

        payload = {
            "chat_id": str(target_chat_id),
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
        }
        self._send("sendMessage", payload)
#--- END OF FILE ---
import logging
from typing import Optional, Any, Dict

import httpx

from capitalguard.config import settings


class TelegramNotifier:
    """
    بسيط وموثوق: يرسل رسائل إلى تيليجرام عبر HTTP API مباشرة.
    يسجّل الأخطاء بوضوح ولا يكسر التطبيق عند الفشل.
    """

    def __init__(self, token: Optional[str] = None):
        self.token = (token or settings.TELEGRAM_BOT_TOKEN or "").strip()
        if not self.token:
            logging.warning("TelegramNotifier: TELEGRAM_BOT_TOKEN is missing. Notifier is disabled.")

    def _send(self, method: str, payload: Dict[str, Any]) -> bool:
        if not self.token:
            logging.error("TelegramNotifier._send: no bot token set.")
            return False

        url = f"https://api.telegram.org/bot{self.token}/{method}"
        try:
            with httpx.Client(timeout=10.0) as client:
                resp = client.post(url, json=payload)
            if resp.status_code != 200:
                logging.error("TelegramNotifier: HTTP %s -> %s", resp.status_code, resp.text)
                return False
            data = resp.json()
            if not data.get("ok"):
                logging.error("TelegramNotifier: API not ok -> %s", data)
                return False
            logging.info("TelegramNotifier: sent %s successfully.", method)
            return True
        except Exception as e:
            logging.exception("TelegramNotifier: exception while sending: %s", e)
            return False

    def send_message(self, chat_id: int | str, text: str, parse_mode: str = "HTML") -> bool:
        if chat_id is None or str(chat_id).strip() == "":
            logging.warning("TelegramNotifier.send_message: empty chat_id, skip send.")
            return False
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
        }
        return self._send("sendMessage", payload)
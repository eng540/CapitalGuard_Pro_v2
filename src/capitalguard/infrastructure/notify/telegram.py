import requests
from capitalguard.config import settings

class TelegramNotifier:
    def __init__(self, token: str | None = None, chat_id: str | None = None):
        self.token = token or settings.TELEGRAM_BOT_TOKEN
        self.chat_id = chat_id or settings.TELEGRAM_CHAT_ID
        self.disabled = not (self.token and self.chat_id)

    def publish(self, text: str) -> None:
        if self.disabled: return
        try:
            url = f"https://api.telegram.org/bot{self.token}/sendMessage"
            requests.post(url, json={"chat_id": self.chat_id, "text": text, "parse_mode": "HTML"}, timeout=10)
        except Exception:
            pass

# --- START OF FILE: src/capitalguard/infrastructure/notify/telegram.py ---
from __future__ import annotations
from typing import Optional, Tuple, Dict, Any
import logging
import requests

from capitalguard.config import settings
from capitalguard.domain.entities import Recommendation
from capitalguard.interfaces.telegram.ui_texts import build_trade_card_text

log = logging.getLogger(__name__)

class TelegramNotifier:
    """
    للنشر/التحرير في القناة فقط (بدون أزرار).
    يعتمد على TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID
    """
    def __init__(self) -> None:
        self.bot_token: Optional[str] = settings.TELEGRAM_BOT_TOKEN
        self.channel_id: Optional[int] = (
            int(settings.TELEGRAM_CHAT_ID) if getattr(settings, "TELEGRAM_CHAT_ID", None) else None
        )
        self.api_base = f"https://api.telegram.org/bot{self.bot_token}" if self.bot_token else None

    # ------------------------
    def _post(self, method: str, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not self.api_base:
            log.warning("TelegramNotifier disabled — missing TELEGRAM_BOT_TOKEN")
            return None
        try:
            r = requests.post(f"{self.api_base}/{method}", json=payload, timeout=15)
            if r.status_code != 200:
                log.error("Telegram API %s failed: %s", method, r.text)
                return None
            data = r.json()
            if not data.get("ok"):
                log.error("Telegram API %s not ok: %s", method, data)
                return None
            return data.get("result")
        except Exception:
            log.exception("Telegram API call error (%s)", method)
            return None

    # ------------------------
    def send_message(self, text: str, chat_id: Optional[int | str] = None) -> Optional[int]:
        target = chat_id or self.channel_id
        if not target:
            return None
        res = self._post("sendMessage", {"chat_id": target, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True})
        return int(res["message_id"]) if res and "message_id" in res else None

    def post_recommendation_card(self, rec: Recommendation) -> Optional[Tuple[int, int]]:
        """ينشر البطاقة في القناة ويعيد (channel_id, message_id)."""
        if not self.channel_id:
            log.warning("No TELEGRAM_CHAT_ID — skipping publish")
            return None
        text = build_trade_card_text(rec)
        res = self._post(
            "sendMessage",
            {"chat_id": self.channel_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True},
        )
        if not res:
            return None
        return (int(res["chat"]["id"]), int(res["message_id"]))

    def edit_recommendation_card(self, rec: Recommendation) -> bool:
        """تحرير البطاقة بعد التعديل/الإغلاق؛ إن فشل التحريك، يمكن إعادة النشر من طبقة أعلى."""
        if not rec.channel_id or not rec.message_id:
            return False
        text = build_trade_card_text(rec)
        res = self._post(
            "editMessageText",
            {"chat_id": rec.channel_id, "message_id": rec.message_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True},
        )
        return bool(res)
# --- END OF FILE ---
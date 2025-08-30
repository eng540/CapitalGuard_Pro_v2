# --- START OF FILE: src/capitalguard/infrastructure/notify/telegram.py ---
from __future__ import annotations
from typing import Optional, Tuple, Dict, Any
import logging, requests
from capitalguard.config import settings
from capitalguard.domain.entities import Recommendation
from capitalguard.interfaces.telegram.ui_texts import build_trade_card_text

log = logging.getLogger(__name__)

class TelegramNotifier:
    BASE = "https://api.telegram.org/bot{token}/{method}"
    settings = settings  # متاح داخليًا

    def _post(self, method: str, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not settings.TELEGRAM_BOT_TOKEN:
            return None
        try:
            url = self.BASE.format(token=settings.TELEGRAM_BOT_TOKEN, method=method)
            resp = requests.post(url, json=payload, timeout=8)
            if not resp.ok:
                log.error("Telegram API %s failed: %s", method, resp.text[:200])
                return None
            return resp.json().get("result")
        except Exception:
            return None

    def _notify_all(self, method: str, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        res = self._post(method, payload)
        if getattr(settings, "SECONDARY_CHAT_ID", None):
            mirror_payload = dict(payload)
            mirror_payload["chat_id"] = int(settings.SECONDARY_CHAT_ID)
            self._post(method, mirror_payload)
        return res

    def publish_recommendation_card(self, rec: Recommendation) -> Optional[Tuple[int, int]]:
        if not settings.TELEGRAM_CHAT_ID:
            return None
        text = build_trade_card_text(rec)
        res = self._notify_all("sendMessage", {
            "chat_id": int(settings.TELEGRAM_CHAT_ID),
            "text": text, "parse_mode": "HTML",
            "disable_web_page_preview": True,
        })
        if not res:
            return None
        return int(res["chat"]["id"]), int(res["message_id"])

    def edit_recommendation_card(self, rec: Recommendation) -> bool:
        if not (rec.channel_id and rec.message_id):
            return False
        text = build_trade_card_text(rec)
        res = self._notify_all("editMessageText", {
            "chat_id": rec.channel_id,
            "message_id": rec.message_id,
            "text": text, "parse_mode": "HTML",
            "disable_web_page_preview": True,
        })
        return bool(res)

    def publish_or_update(self, rec: Recommendation) -> tuple[bool, Optional[Tuple[int,int]]]:
        if rec.channel_id and rec.message_id:
            if self.edit_recommendation_card(rec):
                return True, (rec.channel_id, rec.message_id)
        text = build_trade_card_text(rec) + "\n<i>(Updated)</i>"
        res = self._notify_all("sendMessage", {
            "chat_id": int(settings.TELEGRAM_CHAT_ID),
            "text": text, "parse_mode": "HTML",
            "disable_web_page_preview": True,
        })
        if not res:
            return False, None
        return True, (int(res["chat"]["id"]), int(res["message_id"]))
# --- END OF FILE ---
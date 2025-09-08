# --- START OF FILE: src/capitalguard/infrastructure/notify/telegram.py ---
import logging
from typing import Optional, Tuple, Dict, Any
import httpx
from telegram import InlineKeyboardMarkup
from capitalguard.domain.entities import Recommendation
from capitalguard.interfaces.telegram.ui_texts import build_trade_card_text
from capitalguard.config import settings

log = logging.getLogger(__name__)

class TelegramNotifier:
    """
    Phase 9:
    - لا قناة افتراضية للنشر.
    - النشر يتم فقط عبر post_to_channel(channel_id, ...).
    - edit_recommendation_card يعمل فقط إذا كانت الرسالة منشورة ولها channel_id/message_id.
    """
    def __init__(self):
        self.bot_token = settings.TELEGRAM_BOT_TOKEN
        self.api_base = f"https://api.telegram.org/bot{self.bot_token}" if self.bot_token else None

    def _post(self, method: str, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not self.api_base:
            log.warning("TelegramNotifier not configured (no BOT token). Skip '%s'.", method)
            return None
        try:
            with httpx.Client(timeout=15) as client:
                r = client.post(f"{self.api_base}/{method}", json=payload)
                r.raise_for_status()
                data = r.json()
                if not data.get("ok"):
                    log.error("Telegram API error on %s: %s", method, data.get("description"))
                    return None
                return data.get("result")
        except httpx.HTTPStatusError as e:
            log.error("HTTP error on %s: %s | body=%s", method, e, getattr(e.response, "text", "<no-body>"))
            return None
        except Exception:
            log.exception("Telegram API call failed: %s", method)
            return None

    # ------ Public helpers ------
    def post_to_channel(self, channel_id: int, rec: Recommendation, keyboard: Optional[InlineKeyboardMarkup] = None) -> Optional[Tuple[int, int]]:
        text = build_trade_card_text(rec)
        payload = {
            "chat_id": channel_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        }
        if keyboard:
            payload["reply_markup"] = keyboard.to_dict()
        result = self._post("sendMessage", payload)
        if result and "message_id" in result and "chat" in result:
            try:
                return (int(result["chat"]["id"]), int(result["message_id"]))
            except Exception:
                pass
        return None

    def send_private_message(self, chat_id: int, rec: Recommendation, keyboard: Optional[InlineKeyboardMarkup] = None, text_header: str = ""):
        card_text = build_trade_card_text(rec)
        final_text = f"{text_header}\n\n{card_text}" if text_header else card_text
        payload = {
            "chat_id": chat_id,
            "text": final_text.strip(),
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        }
        if keyboard:
            payload["reply_markup"] = keyboard.to_dict()
        self._post("sendMessage", payload)

    def edit_recommendation_card(self, rec: Recommendation, keyboard: Optional[InlineKeyboardMarkup] = None) -> bool:
        if not rec.channel_id or not rec.message_id:
            return False
        text = build_trade_card_text(rec)
        payload = {
            "chat_id": rec.channel_id,
            "message_id": rec.message_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        }
        if keyboard:
            payload["reply_markup"] = keyboard.to_dict()
        result = self._post("editMessageText", payload)
        return bool(result)
# --- END OF FILE: src/capitalguard/infrastructure/notify/telegram.py ---
# --- START OF FINAL MODIFIED FILE (V6): src/capitalguard/infrastructure/notify/telegram.py ---
import logging
from typing import Optional, Tuple, Dict, Any

import httpx
from telegram import InlineKeyboardMarkup

from capitalguard.config import settings
from capitalguard.domain.entities import Recommendation
from capitalguard.interfaces.telegram.ui_texts import build_trade_card_text

log = logging.getLogger(__name__)

class TelegramNotifier:
    def __init__(self) -> None:
        self.bot_token: Optional[str] = settings.TELEGRAM_BOT_TOKEN
        self.api_base: Optional[str] = (f"https://api.telegram.org/bot{self.bot_token}" if self.bot_token else None)

    def _post(self, method: str, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not self.api_base:
            log.warning("TelegramNotifier is not configured (no BOT token). Skipping '%s'.", method)
            return None
        try:
            with httpx.Client(timeout=15) as client:
                resp = client.post(f"{self.api_base}/{method}", json=payload)
                resp.raise_for_status()
                data = resp.json()
            if not data.get("ok"):
                log.error("Telegram API error on %s: %s", method, data.get("description", "unknown"))
                return None
            return data.get("result")
        except Exception:
            log.exception("Telegram API call '%s' failed", method)
            return None

    def _send_text(self, chat_id: int, text: str, **kwargs) -> Optional[Tuple[int, int]]:
        payload: Dict[str, Any] = {"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True, **kwargs}
        if 'keyboard' in kwargs and kwargs['keyboard']:
            payload["reply_markup"] = kwargs['keyboard'].to_dict()
        
        result = self._post("sendMessage", payload)
        if result and "message_id" in result and "chat" in result:
            try: return (int(result["chat"]["id"]), int(result["message_id"]))
            except (ValueError, TypeError): pass
        return None

    def _edit_text(self, chat_id: int, message_id: int, text: str, keyboard: Optional[InlineKeyboardMarkup] = None) -> bool:
        payload: Dict[str, Any] = {"chat_id": chat_id, "message_id": message_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
        if keyboard: payload["reply_markup"] = keyboard.to_dict()
        return bool(self._post("editMessageText", payload))

    def post_to_channel(self, channel_id: int, rec: Recommendation, keyboard: Optional[InlineKeyboardMarkup] = None) -> Optional[Tuple[int, int]]:
        text = build_trade_card_text(rec)
        return self._send_text(chat_id=channel_id, text=text, keyboard=keyboard)

    def post_notification_reply(self, chat_id: int, message_id: int, text: str) -> Optional[Tuple[int, int]]:
        return self._send_text(chat_id=chat_id, text=text, reply_to_message_id=message_id, allow_sending_without_reply=True)

    def send_private_message(self, chat_id: int, rec: Recommendation, keyboard: Optional[InlineKeyboardMarkup] = None, text_header: str = ""):
        card_text = build_trade_card_text(rec)
        final_text = f"{text_header}\n\n{card_text}".strip() if text_header else card_text
        self._send_text(chat_id=chat_id, text=final_text, keyboard=keyboard)

    def send_private_text(self, chat_id: int, text: str):
        """Sends a simple text message to a private chat."""
        self._send_text(chat_id=chat_id, text=text)

    def edit_recommendation_card_by_ids(self, channel_id: int, message_id: int, rec: Recommendation, keyboard: Optional[InlineKeyboardMarkup] = None) -> bool:
        new_text = build_trade_card_text(rec)
        return self._edit_text(chat_id=channel_id, message_id=message_id, text=new_text, keyboard=keyboard)
# --- END OF FINAL MODIFIED FILE (V6) ---
# --- START OF FINAL, CORRECTED AND ROBUST FILE (V7): src/capitalguard/infrastructure/notify/telegram.py ---
import logging
from typing import Optional, Tuple, Dict, Any

import httpx
from telegram import InlineKeyboardMarkup, Bot
from telegram.ext import Application

from capitalguard.config import settings
from capitalguard.domain.entities import Recommendation
from capitalguard.interfaces.telegram.keyboards import public_channel_keyboard
from capitalguard.interfaces.telegram.ui_texts import build_trade_card_text

log = logging.getLogger(__name__)

class TelegramNotifier:
    """
    Handles all outbound communication to the Telegram Bot API.

    ✅ FIX: This class is now stateful. It holds a reference to the bot application
    to access context-specific information like the bot's username.
    """

    def __init__(self) -> None:
        self.bot_token: Optional[str] = settings.TELEGRAM_BOT_TOKEN
        self.api_base: Optional[str] = (f"https://api.telegram.org/bot{self.bot_token}" if self.bot_token else None)
        self.ptb_app: Optional[Application] = None
        self._bot_username: Optional[str] = None

    def set_ptb_app(self, ptb_app: Application):
        """
        Injects the running PTB application instance into the notifier.
        This is called once at startup.
        """
        self.ptb_app = ptb_app
    
    @property
    def bot_username(self) -> Optional[str]:
        """Lazily fetches and caches the bot's username."""
        if self._bot_username:
            return self._bot_username
        if self.ptb_app and self.ptb_app.bot:
            self._bot_username = self.ptb_app.bot.username
            return self._bot_username
        return None

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
                log.error("Telegram API error on %s: %s (payload=%s)", method, data.get("description", "unknown"), payload)
                return None
            return data.get("result")
        except httpx.HTTPStatusError as e:
            body = e.response.text if getattr(e, "response", None) is not None else "<no-body>"
            log.error("Telegram API HTTP error on %s: %s | body=%s", method, e, body)
            return None
        except Exception:
            log.exception("Telegram API call '%s' failed with exception", method)
            return None

    def _send_text(self, chat_id: int, text: str, keyboard: Optional[InlineKeyboardMarkup] = None, **kwargs) -> Optional[Tuple[int, int]]:
        payload: Dict[str, Any] = {"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True, **kwargs}
        if keyboard:
            payload["reply_markup"] = keyboard.to_dict()
        
        result = self._post("sendMessage", payload)
        if result and "message_id" in result and "chat" in result:
            try:
                return (int(result["chat"]["id"]), int(result["message_id"]))
            except (ValueError, TypeError):
                pass
        return None

    def _edit_text(self, chat_id: int, message_id: int, text: str, keyboard: Optional[InlineKeyboardMarkup] = None) -> bool:
        payload: Dict[str, Any] = {"chat_id": chat_id, "message_id": message_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
        if keyboard:
            payload["reply_markup"] = keyboard.to_dict()
        return bool(self._post("editMessageText", payload))

    def post_to_channel(self, channel_id: int, rec: Recommendation) -> Optional[Tuple[int, int]]:
        text = build_trade_card_text(rec)
        # ✅ FIX: Pass the bot_username to the keyboard builder.
        keyboard = public_channel_keyboard(rec.id, self.bot_username)
        return self._send_text(chat_id=channel_id, text=text, keyboard=keyboard)

    def post_notification_reply(self, chat_id: int, message_id: int, text: str) -> Optional[Tuple[int, int]]:
        return self._send_text(chat_id=chat_id, text=text, reply_to_message_id=message_id, allow_sending_without_reply=True)

    def send_private_text(self, chat_id: int, text: str):
        """Sends a simple text message to a private chat, used for quick alerts."""
        self._send_text(chat_id=chat_id, text=text)

    def edit_recommendation_card_by_ids(self, channel_id: int, message_id: int, rec: Recommendation) -> bool:
        """Edits a previously posted recommendation card in a channel using explicit IDs."""
        new_text = build_trade_card_text(rec)
        # ✅ FIX: Pass the bot_username to the keyboard builder.
        keyboard = public_channel_keyboard(rec.id, self.bot_username) if rec.status != RecommendationStatus.CLOSED else None
        return self._edit_text(
            chat_id=channel_id,
            message_id=message_id,
            text=new_text,
            keyboard=keyboard,
        )
# --- END OF FINAL MODIFIED FILE (V7) ---
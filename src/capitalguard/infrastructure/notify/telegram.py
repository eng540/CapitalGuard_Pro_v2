# --- START OF COMPLETE MODIFIED FILE: src/capitalguard/infrastructure/notify/telegram.py ---
import logging
from typing import Optional, Tuple, Dict, Any

import httpx
from telegram import InlineKeyboardMarkup

from capitalguard.config import settings
from capitalguard.domain.entities import Recommendation
from capitalguard.interfaces.telegram.ui_texts import build_trade_card_text

log = logging.getLogger(__name__)


class TelegramNotifier:
    """
    Handles all outbound communication to the Telegram Bot API.
    - Explicitly targets channels, no default channel concept.
    - Robustly handles API errors and returns consistent types.
    """

    def __init__(self) -> None:
        self.bot_token: Optional[str] = settings.TELEGRAM_BOT_TOKEN
        self.api_base: Optional[str] = (
            f"https://api.telegram.org/bot{self.bot_token}" if self.bot_token else None
        )

    # -------------------------------
    # Low-level HTTP helper
    # -------------------------------
    def _post(self, method: str, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Internal helper to make POST requests to the Telegram API.
        Returns the 'result' dict on success, or None on failure. Does not raise exceptions.
        """
        if not self.api_base:
            log.warning("TelegramNotifier is not configured (no BOT token). Skipping '%s'.", method)
            return None
        try:
            with httpx.Client(timeout=15) as client:
                resp = client.post(f"{self.api_base}/{method}", json=payload)
                resp.raise_for_status()
                data = resp.json()
            if not data.get("ok"):
                desc = data.get("description", "unknown error")
                log.error("Telegram API error on %s: %s (payload=%s)", method, desc, payload)
                return None
            return data.get("result")
        except httpx.HTTPStatusError as e:
            body = e.response.text if getattr(e, "response", None) is not None else "<no-body>"
            log.error("Telegram API HTTP error on %s: %s | body=%s", method, e, body)
            return None
        except Exception:
            log.exception("Telegram API call '%s' failed with exception", method)
            return None

    # -------------------------------
    # Core send/edit helpers
    # -------------------------------
    def _send_text(
        self,
        chat_id: int,
        text: str,
        keyboard: Optional[InlineKeyboardMarkup] = None,
        **kwargs,
    ) -> Optional[Tuple[int, int]]:
        """Internal helper to send a text message."""
        payload: Dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
            **kwargs,
        }
        if keyboard:
            payload["reply_markup"] = keyboard.to_dict()

        result = self._post("sendMessage", payload)
        if result and "message_id" in result and "chat" in result:
            try:
                return (int(result["chat"]["id"]), int(result["message_id"]))
            except (ValueError, TypeError):
                pass
        return None

    def _edit_text(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        keyboard: Optional[InlineKeyboardMarkup] = None,
    ) -> bool:
        """Internal helper to edit an existing text message."""
        payload: Dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if keyboard:
            payload["reply_markup"] = keyboard.to_dict()

        result = self._post("editMessageText", payload)
        return bool(result)

    # -------------------------------
    # Public API for Services
    # -------------------------------
    def post_to_channel(
        self,
        channel_id: int,
        rec: Recommendation,
        keyboard: Optional[InlineKeyboardMarkup] = None,
    ) -> Optional[Tuple[int, int]]:
        """
        Posts a new recommendation card to a specific channel.
        """
        text = build_trade_card_text(rec)
        return self._send_text(chat_id=channel_id, text=text, keyboard=keyboard)

    def post_notification_reply(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        **kwargs,
    ) -> Optional[Tuple[int, int]]:
        """
        Posts a new message as a reply to an existing one, creating a threaded notification.
        """
        return self._send_text(
            chat_id=chat_id,
            text=text,
            reply_to_message_id=message_id,
            allow_sending_without_reply=True,
            **kwargs
        )

    def send_private_message(
        self,
        chat_id: int,
        rec: Recommendation,
        keyboard: Optional[InlineKeyboardMarkup] = None,
        text_header: str = "",
    ) -> None:
        """
        Sends a private message with a recommendation card to a user (analyst).
        """
        card_text = build_trade_card_text(rec)
        final_text = f"{text_header}\n\n{card_text}".strip() if text_header else card_text
        self._send_text(chat_id=chat_id, text=final_text, keyboard=keyboard)

    def edit_recommendation_card(
        self,
        rec: Recommendation,
        keyboard: Optional[InlineKeyboardMarkup] = None,
    ) -> bool:
        """
        Edits a previously posted recommendation card in a channel.
        """
        if not rec.channel_id or not rec.message_id:
            return False
        new_text = build_trade_card_text(rec)
        return self._edit_text(
            chat_id=int(rec.channel_id),
            message_id=int(rec.message_id),
            text=new_text,
            keyboard=keyboard,
        )

    def send_admin_alert(self, text: str) -> None:
        """
        Logs an admin alert. No default channel sending.
        """
        log.info("ADMIN ALERT (logged only): %s", text)
# --- END OF COMPLETE MODIFIED FILE ---
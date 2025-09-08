# --- START OF FILE: src/capitalguard/infrastructure/notify/telegram.py ---
import logging
from typing import Optional, Tuple, Dict, Any
import httpx
from telegram import InlineKeyboardMarkup
from capitalguard.config import settings
from capitalguard.domain.entities import Recommendation
from capitalguard.interfaces.telegram.ui_texts import build_trade_card_text

log = logging.getLogger(__name__)

class TelegramNotifier:
    def __init__(self):
        self.bot_token = settings.TELEGRAM_BOT_TOKEN
        # قناة افتراضية (سلوك قديم/اختياري)
        self.channel_id = settings.TELEGRAM_CHAT_ID
        self.api_base = f"https://api.telegram.org/bot{self.bot_token}" if self.bot_token else None

    # -------- Low-level HTTP helper (never raises) --------
    def _post(self, method: str, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not self.api_base:
            log.warning("TelegramNotifier is not configured. Skipping API call.")
            return None
        try:
            with httpx.Client(timeout=15) as client:
                r = client.post(f"{self.api_base}/{method}", json=payload)
                r.raise_for_status()
                data = r.json()
                if not data.get("ok"):
                    log.error("Telegram API Error (%s): %s", method, data.get("description"))
                    return None
                return data.get("result")
        except httpx.HTTPStatusError as e:
            log.error("Telegram API HTTP Error: %s - Response: %s", e, getattr(e, "response", None).text if getattr(e, "response", None) else "")
            return None
        except Exception as e:
            log.exception("Telegram API call '%s' failed", method)
            return None

    # -------- NEW: Public posting to arbitrary channel (required for multi-channel broadcast) --------
    def post_to_channel(
        self,
        channel_id: int,
        rec: Recommendation,
        keyboard: Optional[InlineKeyboardMarkup] = None,
    ) -> Optional[Tuple[int, int]]:
        """
        Posts a recommendation card to a given channel.
        Returns (chat_id, message_id) on success, or None on failure.
        MUST NOT raise exceptions (service layer loops over multiple channels).
        """
        try:
            text = build_trade_card_text(rec)
            payload: Dict[str, Any] = {
                "chat_id": channel_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            }
            if keyboard:
                payload["reply_markup"] = keyboard.to_dict()

            result = self._post("sendMessage", payload)
            if result and "message_id" in result:
                return (int(result["chat"]["id"]), int(result["message_id"]))
            return None
        except Exception as e:
            log.error("post_to_channel failed for channel %s: %s", channel_id, e, exc_info=True)
            return None

    # -------- Legacy: post to default configured channel (kept for backward compatibility) --------
    def post_recommendation_card(
        self,
        rec: Recommendation,
        keyboard: Optional[InlineKeyboardMarkup] = None
    ) -> Optional[Tuple[int, int]]:
        if not self.channel_id:
            log.warning("Cannot post card: TELEGRAM_CHAT_ID is not set.")
            return None
        return self.post_to_channel(self.channel_id, rec, keyboard)

    # -------- Private DM with the analyst (kept) --------
    def send_private_message(
        self,
        chat_id: int,
        rec: Recommendation,
        keyboard: Optional[InlineKeyboardMarkup] = None,
        text_header: str = ""
    ):
        card_text = build_trade_card_text(rec)
        final_text = f"{text_header}\n\n{card_text}" if text_header else card_text
        payload = {
            "chat_id": chat_id,
            "text": final_text.strip(),
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if keyboard:
            payload["reply_markup"] = keyboard.to_dict()
        self._post("sendMessage", payload)

    # -------- Edit an existing message (channel or private), if ids stored on rec --------
    def edit_recommendation_card(
        self,
        rec: Recommendation,
        keyboard: Optional[InlineKeyboardMarkup] = None
    ) -> bool:
        if not getattr(rec, "channel_id", None) or not getattr(rec, "message_id", None):
            return False
        text = build_trade_card_text(rec)
        payload = {
            "chat_id": rec.channel_id,
            "message_id": rec.message_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if keyboard:
            payload["reply_markup"] = keyboard.to_dict()
        result = self._post("editMessageText", payload)
        return bool(result)

    # -------- Admin alert to default channel (optional) --------
    def send_admin_alert(self, text: str) -> None:
        if self.channel_id:
            self._post("sendMessage", {"chat_id": self.channel_id, "text": f"🔔 ADMIN ALERT 🔔\n{text}"})
# --- END OF FILE: src/capitalguard/infrastructure/notify/telegram.py ---
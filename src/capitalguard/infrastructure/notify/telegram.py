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
    """
    Telegram Notifier (phase 9.x, no default channel)

    - ❌ لا قناة افتراضية: يمنع أي نشر عبر TELEGRAM_CHAT_ID أو ما شابه.
    - ✅ post_to_channel(channel_id, ...) للنشر الصريح إلى قنوات محددة فقط.
    - إرسال خاص للمستخدم، وتحرير بطاقة منشورة سابقًا عند توفر channel_id/message_id.
    - تعامل متين مع أخطاء Telegram API وإرجاع أنواع ثابتة.
    """

    def __init__(self) -> None:
        self.bot_token: Optional[str] = settings.TELEGRAM_BOT_TOKEN
        # لا نستخدم أي قناة افتراضية مطلقًا
        self.api_base: Optional[str] = (
            f"https://api.telegram.org/bot{self.bot_token}" if self.bot_token else None
        )

    # -------------------------------
    # Low-level HTTP helper (never raises)
    # -------------------------------
    def _post(self, method: str, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Calls Telegram Bot API.
        Returns the 'result' dict on success, or None on failure.
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
        parse_mode: str = "HTML",
        disable_web_page_preview: bool = True,
    ) -> Optional[Tuple[int, int]]:
        payload: Dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": disable_web_page_preview,
        }
        if keyboard:
            payload["reply_markup"] = keyboard.to_dict()

        result = self._post("sendMessage", payload)
        if result and "message_id" in result and "chat" in result:
            try:
                return (int(result["chat"]["id"]), int(result["message_id"]))
            except Exception:
                # Fallback in edge cases where ids aren't ints
                pass
        return None

    def _edit_text(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        keyboard: Optional[InlineKeyboardMarkup] = None,
        parse_mode: str = "HTML",
        disable_web_page_preview: bool = True,
    ) -> bool:
        payload: Dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": disable_web_page_preview,
        }
        if keyboard:
            payload["reply_markup"] = keyboard.to_dict()

        result = self._post("editMessageText", payload)
        return bool(result)

    # -------------------------------
    # Public API
    # -------------------------------

    # ✅ --- NEW FUNCTION: post_notification_reply ---
    def post_notification_reply(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        allow_sending_without_reply: bool = True,
        disable_notification: bool = False,
    ) -> Optional[Tuple[int, int]]:
        """
        Sends a new message as a reply to an existing one.
        This creates a threaded notification for a specific event.

        Args:
            chat_id: The channel/chat to send the message to.
            message_id: The ID of the original recommendation card to reply to.
            text: The content of the notification message.
            allow_sending_without_reply: If true, sends the message even if the reply fails.
            disable_notification: If true, sends the message silently.

        Returns:
            A tuple of (chat_id, new_message_id) on success, otherwise None.
        """
        payload: Dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "reply_to_message_id": message_id,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
            "disable_notification": disable_notification,
            "allow_sending_without_reply": allow_sending_without_reply,
        }
        result = self._post("sendMessage", payload)
        if result and "message_id" in result and "chat" in result:
            try:
                return (int(result["chat"]["id"]), int(result["message_id"]))
            except Exception:
                pass
        return None
    # --- END OF NEW FUNCTION ---

    def post_to_channel(
        self,
        channel_id: int,
        rec: Recommendation,
        keyboard: Optional[InlineKeyboardMarkup] = None,
    ) -> Optional[Tuple[int, int]]:
        """
        Post a trade card to a specific Telegram channel by id.
        Returns (channel_id, message_id) on success, None otherwise.
        MUST NOT raise exceptions (service layer loops over multiple channels).
        """
        text = build_trade_card_text(rec)
        return self._send_text(chat_id=channel_id, text=text, keyboard=keyboard)

    def post_recommendation_card(
        self,
        rec: Recommendation,
        keyboard: Optional[InlineKeyboardMarkup] = None,
    ) -> Optional[Tuple[int, int]]:
        """
        Deprecated (9.x): كان يستخدم قناة افتراضية عبر TELEGRAM_CHAT_ID.
        لم يعد مسموحًا بالنشر الافتراضي. تعيد None دائمًا مع تحذير في السجل.
        """
        log.warning("post_recommendation_card is deprecated and disabled (no default channel allowed).")
        return None

    def send_private_message(
        self,
        chat_id: int,
        rec: Recommendation,
        keyboard: Optional[InlineKeyboardMarkup] = None,
        text_header: str = "",
    ) -> None:
        """
        Sends a private message containing the trade card to a specific chat (user).
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
        Edits a previously posted card (public channel message).
        Requires rec.channel_id and rec.message_id to be set.
        """
        if not getattr(rec, "channel_id", None) or not getattr(rec, "message_id", None):
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
        9.x: لا إرسال لقناة افتراضية. نسجل فقط لإشراف النظام.
        """
        log.info("ADMIN ALERT (logged only, no default channel): %s", text)
# --- END OF FILE: src/capitalguard/infrastructure/notify/telegram.py ---
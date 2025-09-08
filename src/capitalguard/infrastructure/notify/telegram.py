#--- START OF FILE: src/capitalguard/infrastructure/notify/telegram.py ---

import logging from typing import Optional, Tuple, Dict, Any import httpx from telegram import InlineKeyboardMarkup

from capitalguard.config import settings from capitalguard.domain.entities import Recommendation from capitalguard.interfaces.telegram.ui_texts import build_trade_card_text

log = logging.getLogger(name)

class TelegramNotifier: """ Telegram Notifier (enhanced for phase 9.0): - Preserves legacy behavior via TELEGRAM_CHAT_ID (default channel). - Adds post_to_channel(channel_id, ...) to broadcast to any linked channel. - Robust error handling and consistent return types. """

def __init__(self):
    self.bot_token: Optional[str] = settings.TELEGRAM_BOT_TOKEN
    # Default channel for legacy single-channel deployments (optional)
    self.channel_id: Optional[int] = None
    try:
        self.channel_id = int(settings.TELEGRAM_CHAT_ID) if settings.TELEGRAM_CHAT_ID else None
    except Exception:
        # Keep None if unparsable, but don't crash
        self.channel_id = None

    self.api_base: Optional[str] = (
        f"https://api.telegram.org/bot{self.bot_token}" if self.bot_token else None
    )

# -------------------------------
# Low-level HTTP helper
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
        body = e.response.text if e.response is not None else "<no-body>"
        log.error("Telegram API HTTP error on %s: %s | body=%s", method, e, body)
        return None
    except Exception as e:
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
def post_to_channel(
    self,
    channel_id: int,
    rec: Recommendation,
    keyboard: Optional[InlineKeyboardMarkup] = None,
) -> Optional[Tuple[int, int]]:
    """
    New (9.0): Post a trade card to any Telegram channel by id.
    Returns (channel_id, message_id) on success, None otherwise.
    """
    text = build_trade_card_text(rec)
    return self._send_text(chat_id=channel_id, text=text, keyboard=keyboard)

def post_recommendation_card(
    self,
    rec: Recommendation,
    keyboard: Optional[InlineKeyboardMarkup] = None,
) -> Optional[Tuple[int, int]]:
    """
    Legacy/Default: Post to the default TELEGRAM_CHAT_ID if configured.
    Keeps backward compatibility for single-channel setups.
    """
    if not self.channel_id:
        log.warning("Cannot post card: TELEGRAM_CHAT_ID is not set.")
        return None
    text = build_trade_card_text(rec)
    return self._send_text(chat_id=self.channel_id, text=text, keyboard=keyboard)

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
    Sends an admin alert to the default channel if available.
    Non-fatal if default channel is not configured.
    """
    if not self.channel_id:
        log.info("Admin alert skipped (no TELEGRAM_CHAT_ID). Message was: %s", text)
        return
    self._send_text(chat_id=self.channel_id, text=f"🔔 ADMIN ALERT 🔔\n{text}", keyboard=None, parse_mode="HTML")

#--- END OF FILE ---
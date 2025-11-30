# --- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/infrastructure/notify/telegram.py ---
# File: src/capitalguard/infrastructure/notify/telegram.py
# Version: v8.0.0-COMPATIBLE (Sync with Lifecycle)
# ✅ THE FIX: Updated signatures to accept 'bot_username' and prevent crashes.

import logging
import asyncio
from typing import Optional, Union, Tuple, Dict, Any
from telegram import Bot, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import TelegramError, RetryAfter, TimedOut, NetworkError
from capitalguard.config import settings
from capitalguard.interfaces.telegram.ui_texts import build_trade_card_text
from capitalguard.interfaces.telegram.keyboards import public_channel_keyboard

log = logging.getLogger(__name__)

class TelegramNotifier:
    def __init__(self, bot_token: str):
        self.bot_token = bot_token
        self.bot = Bot(token=bot_token)
        self._bot_username: Optional[str] = None
        
        # Initialize bot info immediately if possible
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(self._init_bot_info())
            else:
                loop.run_until_complete(self._init_bot_info())
        except Exception as e:
            log.warning(f"Could not init bot info on startup: {e}")

    async def _init_bot_info(self):
        try:
            me = await self.bot.get_me()
            self._bot_username = me.username
            log.info(f"TelegramNotifier initialized for @{self._bot_username}")
        except Exception as e:
            log.error(f"Failed to get bot info: {e}")

    @property
    def bot_username(self) -> str:
        return self._bot_username or "CapitalGuardBot"

    async def _send_text(self, chat_id: Union[int, str], text: str, keyboard: Optional[InlineKeyboardMarkup] = None) -> Optional[Tuple[int, int]]:
        try:
            msg = await self.bot.send_message(
                chat_id=chat_id,
                text=text,
                reply_markup=keyboard,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True
            )
            return (msg.chat.id, msg.message_id)
        except RetryAfter as e:
            log.warning(f"Flood limit exceeded. Sleep {e.retry_after}s")
            await asyncio.sleep(e.retry_after)
            return await self._send_text(chat_id, text, keyboard)
        except Exception as e:
            log.error(f"Failed to send message to {chat_id}: {e}")
            return None

    async def _edit_text(self, chat_id: Union[int, str], message_id: int, text: str, keyboard: Optional[InlineKeyboardMarkup] = None) -> bool:
        try:
            await self.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=text,
                reply_markup=keyboard,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True
            )
            return True
        except Exception as e:
            if "message is not modified" in str(e): return True
            log.error(f"Failed to edit message {chat_id}/{message_id}: {e}")
            return False

    # --- Public API ---

    async def send_admin_alert(self, text: str):
        if settings.TELEGRAM_ADMIN_CHAT_ID:
            await self._send_text(settings.TELEGRAM_ADMIN_CHAT_ID, f"🚨 <b>SYSTEM ALERT</b>\n{text}")

    async def send_private_text(self, chat_id: int, text: str):
        await self._send_text(chat_id, text)

    async def post_to_channel(self, channel_id: Union[int, str], rec: Any, keyboard: Optional[InlineKeyboardMarkup] = None) -> Optional[Tuple[int, int]]:
        # Generate text dynamically
        text = build_trade_card_text(rec, self.bot_username, is_initial_publish=True)
        if keyboard is None:
            keyboard = public_channel_keyboard(rec.id, self.bot_username)
        return await self._send_text(channel_id, text, keyboard)

    async def edit_recommendation_card_by_ids(self, channel_id: Union[int, str], message_id: int, rec: Any, bot_username: str = None) -> bool:
        """
        ✅ FIXED: Accepts 'bot_username' to match LifecycleService call signature.
        """
        # Use passed username or fallback to self
        uname = bot_username or self.bot_username
        
        text = build_trade_card_text(rec, uname, is_initial_publish=False)
        # Import dynamically to avoid circular deps if needed, or rely on top-level
        from capitalguard.domain.entities import RecommendationStatus
        
        keyboard = None
        if rec.status != RecommendationStatus.CLOSED:
            keyboard = public_channel_keyboard(rec.id, uname)
            
        return await self._edit_text(channel_id, message_id, text, keyboard)

    async def post_notification_reply(self, chat_id: int, message_id: int, text: str):
        try:
            await self.bot.send_message(
                chat_id=chat_id,
                text=text,
                reply_to_message_id=message_id,
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            log.error(f"Failed to reply to {chat_id}/{message_id}: {e}")

# --- END OF FILE ---
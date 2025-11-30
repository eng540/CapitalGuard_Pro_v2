# --- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/infrastructure/notify/telegram.py ---
# File: src/capitalguard/infrastructure/notify/telegram.py
# Version: v9.0.0-FINAL-COMPATIBLE (Sync with Lifecycle & UI Texts)
# ✅ THE FIX: Updated signatures to accept 'bot_username' and prevent crashes.
# ✅ COMPATIBLE: Fully compatible with lifecycle_service.py and ui_texts.py

import logging
import asyncio
from typing import Optional, Union, Tuple, Dict, Any
from telegram import Bot, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.error import RetryAfter

from capitalguard.config import settings
from capitalguard.domain.entities import Recommendation, RecommendationStatus
from capitalguard.interfaces.telegram.ui_texts import build_trade_card_text
from capitalguard.interfaces.telegram.keyboards import public_channel_keyboard

log = logging.getLogger(__name__)

class TelegramNotifier:
    """
    Handles all outbound communication to the Telegram Bot API.
    Compatible with lifecycle_service.py and ui_texts.py
    """

    def __init__(self, bot_token: str = None):
        self.bot_token = bot_token or settings.TELEGRAM_BOT_TOKEN
        if not self.bot_token:
            raise ValueError("Telegram bot token is required")
            
        self.bot = Bot(token=self.bot_token)
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
        """Initialize bot username asynchronously"""
        try:
            me = await self.bot.get_me()
            self._bot_username = me.username
            log.info(f"TelegramNotifier initialized for @{self._bot_username}")
        except Exception as e:
            log.error(f"Failed to get bot info: {e}")

    @property
    def bot_username(self) -> str:
        """Get bot username with fallback"""
        return self._bot_username or "CapitalGuardBot"

    async def _send_text(self, chat_id: Union[int, str], text: str, 
                        keyboard: Optional[InlineKeyboardMarkup] = None,
                        **kwargs) -> Optional[Tuple[int, int]]:
        """
        Send text message with proper error handling and retry logic
        """
        try:
            msg = await self.bot.send_message(
                chat_id=chat_id,
                text=text,
                reply_markup=keyboard,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
                **kwargs
            )
            return (msg.chat.id, msg.message_id)
        except RetryAfter as e:
            log.warning(f"Flood limit exceeded. Sleep {e.retry_after}s")
            await asyncio.sleep(e.retry_after)
            return await self._send_text(chat_id, text, keyboard, **kwargs)
        except Exception as e:
            log.error(f"Failed to send message to {chat_id}: {e}")
            return None

    async def _edit_text(self, chat_id: Union[int, str], message_id: int, 
                        text: str, keyboard: Optional[InlineKeyboardMarkup] = None) -> bool:
        """
        Edit existing message with proper error handling
        """
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
            # Message not modified is not an error
            if "message is not modified" in str(e).lower():
                return True
            log.error(f"Failed to edit message {chat_id}/{message_id}: {e}")
            return False

    # --- Public API - Compatible with LifecycleService ---

    async def send_admin_alert(self, text: str):
        """Send alert to admin channel"""
        if settings.TELEGRAM_ADMIN_CHAT_ID:
            await self._send_text(settings.TELEGRAM_ADMIN_CHAT_ID, f"🚨 <b>SYSTEM ALERT</b>\n{text}")

    async def send_private_text(self, chat_id: int, text: str):
        """Send private message to user"""
        await self._send_text(chat_id, text)

    async def post_to_channel(self, channel_id: Union[int, str], rec: Recommendation, 
                            keyboard: Optional[InlineKeyboardMarkup] = None) -> Optional[Tuple[int, int]]:
        """
        Post recommendation to channel
        Compatible with lifecycle_service.py calls
        """
        # Generate text with dynamic bot username
        text = build_trade_card_text(rec, self.bot_username, is_initial_publish=True)
        
        # Use provided keyboard or create default one
        if keyboard is None:
            keyboard = public_channel_keyboard(rec.id, self.bot_username)
            
        return await self._send_text(channel_id, text, keyboard)

    async def edit_recommendation_card_by_ids(self, channel_id: Union[int, str], 
                                            message_id: int, 
                                            rec: Recommendation, 
                                            bot_username: str = None) -> bool:
        """
        ✅ FIXED: Accepts 'bot_username' parameter to match LifecycleService call signature
        Compatible with lifecycle_service.py notify_card_update method
        """
        # Use passed username or fallback to instance username
        username_to_use = bot_username or self.bot_username
        
        # Generate updated card text
        text = build_trade_card_text(rec, username_to_use, is_initial_publish=False)
        
        # Create keyboard only for active recommendations
        keyboard = None
        if rec.status != RecommendationStatus.CLOSED:
            keyboard = public_channel_keyboard(rec.id, username_to_use)
            
        return await self._edit_text(channel_id, message_id, text, keyboard)

    async def post_notification_reply(self, chat_id: int, message_id: int, text: str):
        """
        Post reply to existing message
        Compatible with lifecycle_service.py notify_reply method
        """
        try:
            await self.bot.send_message(
                chat_id=chat_id,
                text=text,
                reply_to_message_id=message_id,
                parse_mode=ParseMode.HTML,
                allow_sending_without_reply=True
            )
        except Exception as e:
            log.error(f"Failed to reply to {chat_id}/{message_id}: {e}")

# --- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/infrastructure/notify/telegram.py ---
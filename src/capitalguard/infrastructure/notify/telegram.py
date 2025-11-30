# --- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/infrastructure/notify/telegram.py ---
# File: src/capitalguard/infrastructure/notify/telegram.py
# Version: v11.0.0-ULTIMATE (High Performance & Stability)
# ✅ THE FIX: 
#    1. Connection Pooling: Uses a persistent connection pool to prevent 'ConnectTimeout'.
#    2. Compatibility: Fully supports 'bot_username' and 'set_ptb_app' to stop crashes.
#    3. Resilience: Smart retry logic for network fluctuations.

import logging
import asyncio
from typing import Optional, Union, Tuple, Dict, Any
from telegram import Bot, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.error import RetryAfter, TimedOut, NetworkError
from telegram.request import HTTPXRequest

from capitalguard.config import settings
from capitalguard.domain.entities import Recommendation, RecommendationStatus
from capitalguard.interfaces.telegram.ui_texts import build_trade_card_text
from capitalguard.interfaces.telegram.keyboards import public_channel_keyboard

log = logging.getLogger(__name__)

class TelegramNotifier:
    """
    Ultimate Telegram Notifier - High Performance Engine.
    Prevents system asphyxiation by reusing connections.
    """

    def __init__(self, bot_token: str = None):
        self.bot_token = bot_token or settings.TELEGRAM_BOT_TOKEN
        if not self.bot_token:
            raise ValueError("Telegram bot token is required")

        # ✅ CRITICAL FIX: Connection Pooling to stop Timeouts
        request = HTTPXRequest(
            connection_pool_size=20,  # Handle up to 20 concurrent requests
            read_timeout=10.0,
            write_timeout=10.0,
            connect_timeout=5.0
        )
        self.bot = Bot(token=self.bot_token, request=request)
        self._bot_username: Optional[str] = None
        self.ptb_app = None  # Fixes AttributeError in boot.py

        # Async Initialization
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(self._init_bot_info())
            else:
                loop.run_until_complete(self._init_bot_info())
        except Exception as e:
            log.warning(f"Async init deferred: {e}")

    async def _init_bot_info(self):
        try:
            me = await self.bot.get_me()
            self._bot_username = me.username
            log.info(f"TelegramNotifier ready: @{self._bot_username}")
        except Exception as e:
            log.error(f"Failed to get bot info: {e}")

    # ✅ CRITICAL FIX: Required by boot.py
    def set_ptb_app(self, ptb_app: Any):
        self.ptb_app = ptb_app
        if hasattr(ptb_app, 'bot') and ptb_app.bot:
             self._bot_username = ptb_app.bot.username

    @property
    def bot_username(self) -> str:
        if self._bot_username: return self._bot_username
        if self.ptb_app and hasattr(self.ptb_app, 'bot'): return self.ptb_app.bot.username
        return "CapitalGuardBot"

    async def _send_text(self, chat_id: Union[int, str], text: str, 
                        keyboard: Optional[InlineKeyboardMarkup] = None,
                        retries: int = 3) -> Optional[Tuple[int, int]]:
        """Robust send with retries"""
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
            log.warning(f"Flood limit. Sleeping {e.retry_after}s")
            await asyncio.sleep(e.retry_after)
            return await self._send_text(chat_id, text, keyboard, retries)
        except (TimedOut, NetworkError) as e:
            if retries > 0:
                await asyncio.sleep(1)
                return await self._send_text(chat_id, text, keyboard, retries - 1)
            log.error(f"Network failed for {chat_id}: {e}")
            return None
        except Exception as e:
            log.error(f"Send failed for {chat_id}: {e}")
            return None

    async def _edit_text(self, chat_id: Union[int, str], message_id: int, 
                        text: str, keyboard: Optional[InlineKeyboardMarkup] = None) -> bool:
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
            if "message is not modified" in str(e).lower(): return True
            log.warning(f"Edit failed {chat_id}/{message_id}: {e}")
            return False

    # --- Public API ---

    async def send_admin_alert(self, text: str):
        if settings.TELEGRAM_ADMIN_CHAT_ID:
            # Fire and forget to avoid blocking main loop
            asyncio.create_task(self._send_text(settings.TELEGRAM_ADMIN_CHAT_ID, f"🚨 <b>SYSTEM ALERT</b>\n{text}"))

    async def send_private_text(self, chat_id: int, text: str):
        await self._send_text(chat_id, text)

    async def post_to_channel(self, channel_id: Union[int, str], rec: Recommendation, 
                            keyboard: Optional[InlineKeyboardMarkup] = None) -> Optional[Tuple[int, int]]:
        text = build_trade_card_text(rec, self.bot_username, is_initial_publish=True)
        if keyboard is None:
            keyboard = public_channel_keyboard(rec.id, self.bot_username)
        return await self._send_text(channel_id, text, keyboard)

    async def edit_recommendation_card_by_ids(self, channel_id: Union[int, str], 
                                            message_id: int, 
                                            rec: Recommendation, 
                                            bot_username: str = None) -> bool:
        """
        ✅ CRITICAL FIX: Accepts 'bot_username' to match LifecycleService.
        """
        uname = bot_username or self.bot_username
        text = build_trade_card_text(rec, uname, is_initial_publish=False)
        
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
                parse_mode=ParseMode.HTML,
                allow_sending_without_reply=True
            )
        except Exception as e:
            log.error(f"Reply failed {chat_id}/{message_id}: {e}")

# --- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE ---
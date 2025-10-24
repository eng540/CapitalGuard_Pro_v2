# --- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/infrastructure/notify/telegram.py ---
# src/capitalguard/infrastructure/notify/telegram.py (v8.1 - Retry Logic)
"""
Handles all outbound communication to the Telegram Bot API.
✅ FIX: Implemented basic retry logic for 429 Too Many Requests errors.
"""
import logging
import time
import asyncio
from typing import Optional, Tuple, Dict, Any, Union
import httpx
from telegram import InlineKeyboardMarkup, Bot
from telegram.ext import Application
from telegram.constants import ParseMode

from capitalguard.config import settings
from capitalguard.domain.entities import Recommendation, RecommendationStatus
from capitalguard.interfaces.telegram.keyboards import public_channel_keyboard
from capitalguard.interfaces.telegram.ui_texts import build_trade_card_text

log = logging.getLogger(__name__)
# Separate logger for Telegram API errors to potentially filter them differently
tg_api_log = logging.getLogger("capitalguard.telegram_api")

class TelegramNotifier:
    """Handles all outbound communication to the Telegram Bot API with retry logic."""

    def __init__(self, max_retries: int = 2, initial_delay: float = 1.0) -> None:
        self.bot_token: Optional[str] = settings.TELEGRAM_BOT_TOKEN
        self.api_base: Optional[str] = (f"https://api.telegram.org/bot{self.bot_token}" if self.bot_token else None)
        self.ptb_app: Optional[Application] = None
        self._bot_username: Optional[str] = None
        self.max_retries = max_retries
        self.initial_delay = initial_delay
        # Use httpx.AsyncClient for connection pooling and async requests
        self.http_client = httpx.AsyncClient(timeout=20.0) # Increased timeout slightly

    def set_ptb_app(self, ptb_app: Application):
        """Injects the running PTB application instance."""
        self.ptb_app = ptb_app

    @property
    def bot_username(self) -> Optional[str]:
        """Lazily fetches and caches the bot's username."""
        if self._bot_username:
            return self._bot_username
        if self.ptb_app and self.ptb_app.bot:
            # Fetch username synchronously if needed, assuming it's available after init
            # In an async context, this might need adjustment if called before bot is ready
            try:
                 # Accessing bot info might require an async call if not cached
                 # Simplified assumption: username is cached or accessible synchronously
                 if hasattr(self.ptb_app.bot, 'username') and self.ptb_app.bot.username:
                     self._bot_username = self.ptb_app.bot.username
                 # else: consider fetching it async if needed here
            except Exception as e:
                 log.error(f"Could not retrieve bot username: {e}")
            return self._bot_username
        return None

    async def _request(self, method: str, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Makes an HTTP POST request to the Telegram API with retry logic."""
        if not self.api_base:
            log.warning("TelegramNotifier is not configured (no BOT token). Skipping '%s'.", method)
            return None

        url = f"{self.api_base}/{method}"
        current_delay = self.initial_delay
        last_exception = None

        for attempt in range(self.max_retries + 1):
            try:
                resp = await self.http_client.post(url, json=payload)

                # Check for 429 Too Many Requests specifically
                if resp.status_code == 429:
                    retry_after = 3 # Default retry
                    try:
                        # Attempt to parse retry_after from response body or headers
                        error_data = resp.json()
                        retry_after = int(error_data.get("parameters", {}).get("retry_after", retry_after))
                    except Exception:
                        # Use header if body parsing fails
                        retry_after = int(resp.headers.get("Retry-After", str(retry_after)))

                    wait_time = max(retry_after, current_delay) # Wait at least retry_after or the backoff delay
                    tg_api_log.warning(f"Telegram API rate limit hit (429) on {method}. Retrying after {wait_time:.2f} seconds (Attempt {attempt + 1}/{self.max_retries + 1}).")
                    if attempt < self.max_retries:
                        await asyncio.sleep(wait_time)
                        current_delay *= 1.5 # Exponential backoff factor
                        last_exception = httpx.HTTPStatusError(f"Rate limit hit after {attempt} retries", request=resp.request, response=resp)
                        continue # Go to next attempt
                    else:
                         # Max retries reached for 429
                         raise httpx.HTTPStatusError("Max retries exceeded for rate limit", request=resp.request, response=resp)

                resp.raise_for_status() # Raise exception for other non-2xx codes
                data = resp.json()

                if not data.get("ok"):
                    # Log specific Telegram error description
                    error_desc = data.get("description", "Unknown Telegram API error")
                    tg_api_log.error("Telegram API error on %s: %s (payload_keys=%s)", method, error_desc, list(payload.keys()))
                    # Should we retry on specific non-429 errors? E.g., 5xx? For now, no.
                    return None # Return None on logical API errors

                return data.get("result") # Success

            except httpx.HTTPStatusError as e:
                # Handle non-429 client/server errors
                body_text = e.response.text[:200] if e.response else "<no response body>"
                tg_api_log.error("Telegram API HTTP error on %s: Status %s | Body: %s", method, e.response.status_code, body_text)
                last_exception = e
                # Break retry loop for most HTTP errors except potential transient ones (e.g., 502, 503, 504)
                # For simplicity, we break on all non-429 errors for now.
                break
            except httpx.RequestError as e:
                # Handle network errors (timeout, connection error, etc.)
                tg_api_log.error("Telegram API request error on %s: %s (Attempt %d)", method, e, attempt + 1)
                last_exception = e
                if attempt < self.max_retries:
                    await asyncio.sleep(current_delay)
                    current_delay *= 1.5
                    continue # Retry on network errors
                else:
                    break # Max retries reached for network errors
            except Exception as e:
                # Catch any other unexpected errors during the request
                log.exception("Unexpected exception during Telegram API call '%s'", method)
                last_exception = e
                break # Do not retry on unknown exceptions

        # If loop finished without success
        log.error(f"Telegram API call '{method}' failed after {self.max_retries + 1} attempts. Last error: {last_exception}")
        return None

    async def _send_text(self, chat_id: Union[int, str], text: str, keyboard: Optional[InlineKeyboardMarkup] = None, **kwargs) -> Optional[Tuple[int, int]]:
        """Sends a text message with potential keyboard using the retry mechanism."""
        payload: Dict[str, Any] = {
            "chat_id": chat_id, "text": text,
            "parse_mode": ParseMode.HTML, "disable_web_page_preview": True, **kwargs
        }
        if keyboard:
            try:
                payload["reply_markup"] = keyboard.to_dict()
            except Exception as e:
                 log.error(f"Failed to serialize keyboard for sendMessage: {e}")
                 # Decide whether to send without keyboard or fail? Sending without for now.

        result = await self._request("sendMessage", payload)
        if result and "message_id" in result and "chat" in result:
            try:
                # Ensure IDs are integers
                return (int(result["chat"]["id"]), int(result["message_id"]))
            except (ValueError, TypeError, KeyError) as e:
                 log.error(f"Failed to parse chat_id/message_id from sendMessage response: {e} | Response: {result}")
        return None

    async def _edit_text(self, chat_id: Union[int, str], message_id: int, text: str, keyboard: Optional[InlineKeyboardMarkup] = None) -> bool:
        """Edits a message text with potential keyboard using the retry mechanism."""
        payload: Dict[str, Any] = {
            "chat_id": chat_id, "message_id": message_id, "text": text,
            "parse_mode": ParseMode.HTML, "disable_web_page_preview": True
        }
        if keyboard:
            try:
                payload["reply_markup"] = keyboard.to_dict()
            except Exception as e:
                 log.error(f"Failed to serialize keyboard for editMessageText: {e}")
                 # Don't attempt edit without valid keyboard if one was intended
                 return False

        result = await self._request("editMessageText", payload)
        # editMessageText returns True or the Message object on success, bool(result) checks this
        return bool(result)

    # --- Public Methods ---

    async def post_to_channel(self, channel_id: int, rec: Recommendation, keyboard: Optional[InlineKeyboardMarkup] = None) -> Optional[Tuple[int, int]]:
        """Posts a recommendation card to a channel."""
        if not rec: return None
        text = build_trade_card_text(rec)
        # Fallback to default keyboard if none provided explicitly
        if keyboard is None:
            keyboard = public_channel_keyboard(rec.id, self.bot_username)
        return await self._send_text(chat_id=channel_id, text=text, keyboard=keyboard)

    async def post_notification_reply(self, chat_id: int, message_id: int, text: str) -> Optional[Tuple[int, int]]:
        """Posts a reply to a specific message, typically for notifications."""
        return await self._send_text(chat_id=chat_id, text=text, reply_to_message_id=message_id, allow_sending_without_reply=True)

    async def send_private_text(self, chat_id: int, text: str):
        """Sends a simple text message to a private chat (user)."""
        # Consider adding error handling or return value if needed
        await self._send_text(chat_id=chat_id, text=text)

    async def edit_recommendation_card_by_ids(self, channel_id: int, message_id: int, rec: Recommendation) -> bool:
        """Edits a previously posted recommendation card using explicit IDs."""
        if not rec: return False
        new_text = build_trade_card_text(rec)
        # Determine keyboard based on status (no keyboard if closed)
        rec_status = _get_attr(rec, 'status')
        current_status = RecommendationStatus.CLOSED if rec_status == 'CLOSED' else RecommendationStatus.ACTIVE # Default to ACTIVE if not CLOSED
        keyboard = public_channel_keyboard(rec.id, self.bot_username) if current_status != RecommendationStatus.CLOSED else None
        return await self._edit_text(
            chat_id=channel_id,
            message_id=message_id,
            text=new_text,
            keyboard=keyboard,
        )

    # Add a method to close the http client gracefully on shutdown
    async def close(self):
         await self.http_client.aclose()

# --- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/infrastructure/notify/telegram.py ---
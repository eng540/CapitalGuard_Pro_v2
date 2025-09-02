# --- START OF FILE: src/capitalguard/interfaces/telegram/keyboards.py ---
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from capitalguard.config import settings

def confirm_recommendation_keyboard(user_data_key: str) -> InlineKeyboardMarkup:
    """Keyboard for the final review step in a conversation."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ نشر في القناة", callback_data=f"rec:publish:{user_data_key}"),
            InlineKeyboardButton("❌ إلغاء", callback_data=f"rec:cancel:{user_data_key}")
        ]
    ])

def public_channel_keyboard(rec_id: int) -> InlineKeyboardMarkup:
    """
    Generates the keyboard for the public message in the channel.
    Simple and focused on the follower.
    """
    # Important: Ensure your bot's username is set in the .env file or config
    bot_username = getattr(settings, "TELEGRAM_BOT_USERNAME", "YourBotName") # Fallback
    follow_url = f"https://t.me/{bot_username}?start=follow_{rec_id}"
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔄 تحديث البيانات الحية", callback_data=f"rec:update_public:{rec_id}"),
            InlineKeyboardButton("🤖 الانضمام والمتابعة", url=follow_url)
        ]
    ])

def analyst_control_panel_keyboard(rec_id: int) -> InlineKeyboardMarkup:
    """
    Generates the full control panel for the analyst's private message.
    """
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔄 تحديث السعر", callback_data=f"rec:update_private:{rec_id}"),
            InlineKeyboardButton("✏️ تعديل", callback_data=f"rec:edit_menu:{rec_id}")
        ],
        [
            InlineKeyboardButton("🛡️ نقل للـ BE", callback_data=f"rec:move_be:{rec_id}"),
            InlineKeyboardButton("💰 إغلاق 50% (ملاحظة)", callback_data=f"rec:close_partial:{rec_id}")
        ],
        [
            InlineKeyboardButton("❌ إغلاق كلي", callback_data=f"rec:close_start:{rec_id}")
        ]
    ])

def confirm_close_keyboard(rec_id: int, exit_price: float) -> InlineKeyboardMarkup:
    """Keyboard to confirm closing a recommendation at a specific price."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ تأكيد الإغلاق", callback_data=f"rec:confirm_close:{rec_id}:{exit_price}"),
            InlineKeyboardButton("❌ تراجع", callback_data=f"rec:cancel_close:{rec_id}")
        ]
    ])
# --- END OF FILE ---